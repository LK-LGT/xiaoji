"""问答入口：短时凭据、只读预取、异步编排和确定性降级。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import AiTrace, AssessmentTask, Employee
from ..services import agent_tool_service, privacy, qa_permission
from ..services.agent_tool_token import issue_agent_tool_token
from ..services.review_service import AgentClient

router = APIRouter(prefix="/v1/qa", tags=["qa"])


class QAAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    task_id: str = Field(min_length=1, max_length=64)
    period: str | None = Field(default=None, max_length=10)


class AgentQAResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=16000)
    citations: list[str] = Field(default_factory=list, max_length=200)
    sources: list[str] = Field(default_factory=list, max_length=200)
    degraded: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    agent_status: dict[str, str] = Field(default_factory=dict)


@router.post("/ask")
async def ask(
    req: QAAskRequest, current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    started = time.perf_counter()
    scope = qa_permission.resolve_scope(db, current_user, req.task_id, req.period)
    task = db.get(AssessmentTask, req.task_id)
    structured: dict[str, Any] = {}
    for tool in scope.allowed_tools:
        try:
            if tool == "get_metric_values":
                value = agent_tool_service.load_metric_values(db, employee_id=current_user.id, task=task)
            elif tool == "get_evidence":
                # 不扩大可见性：预取默认使用普通员工视角。
                value = agent_tool_service.load_evidence(db, employee_id=current_user.id, task=task, usage=None)
            else:
                value = agent_tool_service.load_department_stats(db, department_id=current_user.department_id, task_id=task.id)
            structured[tool] = privacy.redact_obj(value)
        except Exception:
            structured[tool] = {"available": False}
    employee_id = current_user.id
    trace_id = str(uuid4())
    payload = {"question": privacy.redact_text(req.question),
               "context": {"period": scope.period, "scope": scope.scope, "task_id": req.task_id},
               "allowed_tools": list(scope.allowed_tools), "trace_id": trace_id}
    # 结束读事务，等待 L3 时不持有数据库连接或 SQLite 事务。
    db.rollback()
    response = None
    try:
        payload["tool_token"] = issue_agent_tool_token(
            employee_id=employee_id, task_id=req.task_id, allow=scope.allowed_tools, scope=scope.scope,
        )
        response = AgentQAResponse.model_validate(await AgentClient().qa(payload))
        if not response.answer.strip() or set(response.citations) - set(response.sources):
            raise ValueError("无效问答结果")
    except Exception:
        response = None
    degraded = response is None or response.degraded
    answer = response.answer if response else "AI 问答暂不可用，下方为系统查询数据，也可到汇总报表查看明细。"
    # 数据完整度由 L2 已成功预取的数据源比例给出，不接受模型自报。
    available = sum(v.get("available") is not False for v in structured.values())
    output = {
        "answer": answer, "citations": response.citations if response else [],
        "sources": response.sources if response else [], "data": structured,
        "degraded": degraded, "follow_up_questions": [],
        "tool_calls": response.tool_calls if response else [], "trace_id": trace_id,
        "data_quality": {"completeness": available / len(scope.allowed_tools),
                         "as_of": datetime.now(UTC).isoformat(), "degraded": degraded,
                         "degrade_reason": "工具或智能体不可用" if degraded else None,
                         "note": "完整度表示可用数据源比例，不表示指标填报完整率"},
    }
    try:
        with db.begin_nested():
            db.add(AiTrace(id=trace_id, agent="qa", status="degraded" if degraded else "ok",
                           latency_ms=int((time.perf_counter() - started) * 1000),
                           output_json={"degraded": degraded, "tool_count": len(output["tool_calls"])},
                           error="QA_DEGRADED" if degraded else None))
            db.flush()
    except Exception:
        pass
    return output
