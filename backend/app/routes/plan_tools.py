"""L3 → L2 只读工具网关 —— **计划助手专用**（Step 6）。

## 为什么与 ``agent_tools.py`` 分开

现有 ``/v1/agent-tools/*`` 的 3 个工具**全部绑死在考核任务上**：

- 令牌里的 ``task`` 必须是真实存在的 ``AssessmentTask``；
- ``period`` 必须与任务周期一致，否则 400。

计划助手场景**没有考核任务** —— 员工写 11 月月计划时，
11 月的考核任务可能压根不存在。所以这里新建一套
**与考核任务解耦**的网关：

| 端点 | 工具名 | 能看到 |
|---|---|---|
| ``POST /v1/plan-tools/get_my_plan_history`` | ``get_my_plan_history`` | **只有我自己**的历史计划 |
| ``POST /v1/plan-tools/get_department_plan_history`` | ``get_department_plan_history`` | **同部门**历史计划（脱敏） |

## 鉴权

复用 ``agent_tool_token`` 的同一套机制（同一密钥、同一 ``aud``、同一签名格式）。
令牌里用**哨兵值** ``plan`` 代替 ``task_id`` —— 计划工具不查考核任务。

⚠️ **绝不能**因为哨兵而放松别的地方的校验：
``/v1/agent-tools/*`` 那边照样要求真实任务。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.models import AiToolCall, Employee
from ..services import plan_tool_service
from ..services.agent_tool_token import (
    AgentToolTokenError,
    AgentToolTokenPayload,
    assert_tool_allowed,
    verify_agent_tool_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/plan-tools", tags=["plan-tools"])

#: 计划工具的令牌哨兵任务 ID —— 计划助手没有考核任务
PLAN_TASK_SENTINEL = "plan"

#: 允许出现在计划工具令牌里的工具名（白名单）
PLAN_TOOL_NAMES = ("get_my_plan_history", "get_department_plan_history")


@dataclass(frozen=True)
class PlanToolContext:
    employee_id: str
    allow: frozenset[str]
    scope: str
    payload: AgentToolTokenPayload
    trace_id: str | None = None


def plan_tool_ctx(
    authorization: str | None = Header(default=None),
    x_agent_trace_id: str | None = Header(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]*$"),
    db: Session = Depends(get_db),
) -> PlanToolContext:
    """解析并校验计划工具令牌。"""
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
    return PlanToolContext(
        employee_id=payload.sub,
        allow=frozenset(payload.allow),
        scope=payload.scope,
        payload=payload,
        trace_id=x_agent_trace_id or None,
    )


class MyPlanHistoryRequest(BaseModel):
    """我的历史计划查询参数。"""

    model_config = {"extra": "forbid"}

    months: int = Field(default=6, ge=1, le=24, description="回看多少个月")
    plan_type: str = Field(default="month", description="month / week / day")


class DepartmentPlanHistoryRequest(BaseModel):
    """部门历史计划查询参数。"""

    model_config = {"extra": "forbid"}

    months: int = Field(default=6, ge=1, le=24, description="回看多少个月")
    limit: int = Field(default=40, ge=1, le=200, description="最多返回多少条目")


def _write_audit(
    db: Session, *, ctx: PlanToolContext, tool: str, args: dict[str, Any],
    ok: bool, latency_ms: int, error: str | None = None,
) -> None:
    """写工具调用审计 —— 与考核工具网关同一套结构。"""
    try:
        with db.begin_nested():
            db.add(AiToolCall(
                trace_id=ctx.trace_id, agent="plan", tool=tool,
                employee_id=ctx.employee_id, task_id=PLAN_TASK_SENTINEL,
                args_hash=hashlib.sha256(json.dumps(
                    args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest(),
                args_keys=sorted(args), ok=ok, latency_ms=latency_ms,
                error=error,
            ))
            db.flush()
    except Exception:
        logger.warning("计划工具审计写入失败", exc_info=True)


def _run_plan_tool(
    tool: str, req: Any, ctx: PlanToolContext, db: Session
) -> dict[str, Any]:
    """执行计划只读工具 —— 统一审计与错误处理。"""
    started = time.perf_counter()
    args = req.model_dump()
    error: str | None = None
    status = 200
    data: dict[str, Any] = {}
    try:
        assert_tool_allowed(ctx.payload, tool)
        if tool == "get_my_plan_history":
            data = plan_tool_service.load_my_plan_history(
                db,
                employee_id=ctx.employee_id,
                months=req.months,
                plan_type=req.plan_type,
            )
        elif tool == "get_department_plan_history":
            # ⚠️ 部门级工具要求 scope=department —— 与考核工具同一把锁
            if ctx.scope != "department":
                raise HTTPException(403, "DEPARTMENT_SCOPE_REQUIRED")
            data = plan_tool_service.load_department_plan_history(
                db,
                employee_id=ctx.employee_id,
                months=req.months,
                limit=req.limit,
            )
        else:
            raise HTTPException(404, "UNKNOWN_TOOL")
    except AgentToolTokenError as exc:
        status, error = exc.status_code, "TOOL_FORBIDDEN"
    except HTTPException as exc:
        status, error = exc.status_code, str(exc.detail)
    except Exception:
        db.rollback()
        error = "TOOL_EXECUTION_FAILED"
        status = 500
        logger.warning("计划工具执行失败: tool=%s", tool, exc_info=True)
    _write_audit(
        db, ctx=ctx, tool=tool, args=args, ok=error is None,
        latency_ms=int((time.perf_counter() - started) * 1000), error=error,
    )
    if error:
        return JSONResponse(
            status_code=status,
            content={"code": status, "message": error, "data": {}},
        )
    return {"code": 0, "message": "", "data": data}


@router.post("/get_my_plan_history")
def get_my_plan_history(
    req: MyPlanHistoryRequest,
    ctx: PlanToolContext = Depends(plan_tool_ctx),
    db: Session = Depends(get_db),
):
    """读取**我自己**的历史计划。

    ⚠️ 越权不可能：查询范围硬编码为令牌里的 ``sub``，
       请求体里**没有任何字段能指定别人**。
    """
    return _run_plan_tool("get_my_plan_history", req, ctx, db)


@router.post("/get_department_plan_history")
def get_department_plan_history(
    req: DepartmentPlanHistoryRequest,
    ctx: PlanToolContext = Depends(plan_tool_ctx),
    db: Session = Depends(get_db),
):
    """读取**同部门**的历史计划（脱敏后）。

    ⚠️ 需要 ``scope=department`` 的令牌 —— 只读令牌调不动。
    """
    return _run_plan_tool("get_department_plan_history", req, ctx, db)
