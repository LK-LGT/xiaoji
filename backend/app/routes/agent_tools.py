"""L3 → L2 只读工具网关，身份和任务仅来自短时令牌。"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.models import AiToolCall, AssessmentRecord, AssessmentTask, Employee
from ..schemas import (
    DepartmentStatsRequest,
    EvidenceQueryRequest,
    MetricValuesRequest,
    PeerScoreRequest,
    ScoringBreakdownRequest,
)
from ..services import access_control, agent_tool_service, peer_score_service, privacy
from ..services.agent_tool_token import (
    AgentToolTokenError,
    AgentToolTokenPayload,
    assert_tool_allowed,
    verify_agent_tool_token,
)
from ..services.period import normalize_period

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/agent-tools", tags=["agent-tools"])


@dataclass(frozen=True)
class AgentToolContext:
    employee_id: str
    task_id: str
    allow: frozenset[str]
    scope: str
    payload: AgentToolTokenPayload
    trace_id: str | None = None


def agent_tool_ctx(
    authorization: str | None = Header(default=None),
    x_agent_trace_id: str | None = Header(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]*$"),
    db: Session = Depends(get_db),
) -> AgentToolContext:
    parts = (authorization or "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="工具令牌无效")
    try:
        payload = verify_agent_tool_token(parts[1])
    except AgentToolTokenError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    employee = db.get(Employee, payload.sub)
    if employee is None or employee.employment_status != "active" or employee.account_status != "enabled":
        raise HTTPException(status_code=403, detail="工具令牌无效")
    return AgentToolContext(
        employee_id=payload.sub, task_id=payload.task,
        allow=frozenset(payload.allow), scope=payload.scope, payload=payload, trace_id=x_agent_trace_id or None,
    )


def write_tool_audit(
    db: Session, *, ctx: AgentToolContext, tool: str, args: dict[str, Any],
    ok: bool, latency_ms: int, error: str | None = None,
    agent: str = "qa", trace_id: str | None = None,
) -> None:
    # 保存点隔离审计 INSERT 失败，避免污染外层事务；只记录错误码，不存异常原文。
    try:
        with db.begin_nested():
            db.add(AiToolCall(
                trace_id=trace_id or ctx.trace_id, agent=agent, tool=tool,
                employee_id=ctx.employee_id, task_id=ctx.task_id,
                args_hash=hashlib.sha256(json.dumps(
                    args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest(),
                args_keys=sorted(args), ok=ok, latency_ms=latency_ms,
                error=error,
            ))
            db.flush()
    except Exception:
        logger.warning("工具调用审计写入失败")


@router.post("/ping")
def ping(
    ctx: AgentToolContext = Depends(agent_tool_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        assert_tool_allowed(ctx.payload, "ping")
    except AgentToolTokenError as exc:
        write_tool_audit(
            db, ctx=ctx, tool="ping", args={}, ok=False,
            latency_ms=0, error="TOOL_FORBIDDEN",
        )
        # HTTPException 会触发 get_db 回滚，先提交这次已认证的拒绝审计。
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    write_tool_audit(db, ctx=ctx, tool="ping", args={}, ok=True, latency_ms=0)
    return {"code": 0, "message": "", "data": {"ok": True}}


def _run_business_tool(tool: str, req: Any, ctx: AgentToolContext, db: Session) -> dict[str, Any]:
    started = time.perf_counter()
    args = req.model_dump()
    error = None
    status = 200
    data: dict[str, Any] = {}
    try:
        assert_tool_allowed(ctx.payload, tool)
        employee = db.get(Employee, ctx.employee_id)
        context = access_control.build_access_context(db, employee)
        task = db.get(AssessmentTask, ctx.task_id)
        if task is None:
            raise HTTPException(404, "TASK_NOT_FOUND")
        period = normalize_period(task.period)
        requested_period = args.get("period")
        if period is None or (requested_period is not None and normalize_period(requested_period) != period):
            raise HTTPException(400, "PERIOD_MISMATCH")
        if tool == "get_department_stats":
            department = employee.department_id
            if task.department_id is not None and task.department_id != department:
                raise HTTPException(403, "TASK_SCOPE_FORBIDDEN")
            if ctx.scope != "department" or not department or not (
                context.has_permission(access_control.PermissionAction.VIEW_ALL_SCORES)
                or department in context.managed_department_ids
            ):
                raise HTTPException(403, "DEPARTMENT_FORBIDDEN")
            data = agent_tool_service.load_department_stats(db, department_id=department, task_id=task.id)
        else:
            # ⚠️ P0-4b：目标人来自**令牌**（``subj``），不是请求体。
            #
            #   令牌里的 ``subj`` 是**后端授权后**写入的（见
            #   ``qa_authorization.authorize_qa_subject``），且被签名保护 ——
            #   改了签名就失效。
            #
            #   ⚠️ **绝不接受请求体里的 employee_id** ——
            #   即使模型被诱导传入别人的 id，也必须被忽略。
            #   这是"后端硬权限"在工具层的物理实现。
            target_employee_id = ctx.payload.subj or employee.id
            target = db.get(Employee, target_employee_id)
            if target is None:
                raise HTTPException(404, "SUBJECT_NOT_FOUND")
            # ⚠️ peer 目标必须**在职** —— 离职员工的绩效不通过问答开放。
            if target.employment_status != "active" or target.account_status != "enabled":
                raise HTTPException(403, "SUBJECT_INACTIVE")
            # ★ 区分「查自己」与「查他人」。
            #
            # ⚠️ 判定依据是**目标人是不是提问者本人**，不是「令牌有没有 subj」——
            #    自查询的令牌也可能带 subj（= 本人 id），
            #    若按「有没有 subj」分支，自查询会被误判成 peer 而遭拒。
            is_self = target.id == employee.id
            if is_self:
                if not context.can_view_score_of(target.id):
                    raise HTTPException(403, "EMPLOYEE_FORBIDDEN")
            else:
                # ★ 查他人 ⇒ 必须走 peer 闸门（三条件判定）。
                #   上级 / 跨部门 / 跨级 一律 403。
                if not access_control.is_peer_viewable(db, context, target):
                    raise HTTPException(403, "PEER_FORBIDDEN")
            record = db.query(AssessmentRecord).filter_by(task_id=task.id, employee_id=target.id).first()
            if record is None:
                raise HTTPException(404, "RECORD_NOT_FOUND")
            # ★ peer 令牌**只能**调 ``get_peer_score``。
            #
            # ⚠️ 为什么必须在这里再挡一层（而不是只靠令牌的 allow 白名单）：
            #   令牌签发方若误把 ``get_evidence`` 写进 allow，
            #   证据原文就会经由 peer 场景泄露出去 ——
            #   评语/证据/自评正是 peer 场景**必须裁掉**的字段。
            #   ⇒ 查他人 + 非 peer 工具 ⇒ 一律拒绝。
            if not is_self and tool != "get_peer_score":
                raise HTTPException(403, "PEER_TOOL_FORBIDDEN")
            if tool == "get_metric_values":
                data = agent_tool_service.load_metric_values(db, employee_id=target.id, task=task)
            elif tool == "get_scoring_breakdown":
                data = agent_tool_service.load_scoring_breakdown(db, employee_id=target.id, task=task)
            elif tool == "get_peer_score":
                # ⚠️ peer 专用工具：**字段白名单**裁剪在
                #   ``peer_score_service`` 内完成，不依赖调用点分支。
                data = peer_score_service.load_peer_score(db, employee_id=target.id, task=task)
            else:
                data = agent_tool_service.load_evidence(
                    db, employee_id=target.id, task=task, usage=req.usage,
                    roles=context.roles,
                )
        data = privacy.redact_obj({"task_id": task.id, "period": period, **data})
    except AgentToolTokenError as exc:
        status, error = exc.status_code, "TOOL_FORBIDDEN"
    except HTTPException as exc:
        status, error = exc.status_code, str(exc.detail)
    except Exception:
        # 查询失败也先恢复会话，确保失败审计可以写入。
        db.rollback()
        error = "TOOL_EXECUTION_FAILED"
        status = 500
    write_tool_audit(
        db, ctx=ctx, tool=tool, args=args, ok=error is None,
        latency_ms=int((time.perf_counter() - started) * 1000), error=error,
    )
    # 拒绝响应不抛 HTTPException，确保 get_db 能提交失败审计。
    if error:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=status, content={"code": status, "message": error, "data": {}})
    return {"code": 0, "message": "", "data": data}


@router.post("/get_metric_values")
def get_metric_values(
    req: MetricValuesRequest, ctx: AgentToolContext = Depends(agent_tool_ctx), db: Session = Depends(get_db),
):
    return _run_business_tool("get_metric_values", req, ctx, db)


@router.post("/get_scoring_breakdown")
def get_scoring_breakdown(
    req: ScoringBreakdownRequest, ctx: AgentToolContext = Depends(agent_tool_ctx), db: Session = Depends(get_db),
):
    """评分构成明细 —— 回答「这分数是怎么算出来的」。

    ⚠️ 权限与 ``get_metric_values`` **同级**（都要求能看本人成绩），
    但**内容不同**：这里多给了权重、等级分、贡献分与算式。
    """
    return _run_business_tool("get_scoring_breakdown", req, ctx, db)


@router.post("/get_peer_score")
def get_peer_score(
    req: PeerScoreRequest, ctx: AgentToolContext = Depends(agent_tool_ctx), db: Session = Depends(get_db),
):
    """★ 同部门平级同事的绩效摘要（**只读 + 字段裁剪**）。

    ⚠️ **目标人来自令牌，不是请求体** ——
       令牌里的 ``subj`` 是后端授权后写入的并被签名保护。
       请求体里**没有** employee_id 参数（``extra="forbid"``）。

    ⚠️ **字段裁剪在服务端完成** ——
       只给总分、等级、指标构成与算式；
       **不给**评语正文、证据原文、自评内容、申诉记录。
    """
    return _run_business_tool("get_peer_score", req, ctx, db)


@router.post("/get_evidence")
def get_evidence(
    req: EvidenceQueryRequest, ctx: AgentToolContext = Depends(agent_tool_ctx), db: Session = Depends(get_db),
):
    return _run_business_tool("get_evidence", req, ctx, db)


@router.post("/get_department_stats")
def get_department_stats(
    req: DepartmentStatsRequest, ctx: AgentToolContext = Depends(agent_tool_ctx), db: Session = Depends(get_db),
):
    return _run_business_tool("get_department_stats", req, ctx, db)
