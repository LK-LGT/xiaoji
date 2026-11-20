"""P1-3 —— 执行类指令网关（催办 / 导出 / 统计）。

两阶段协议
----------
1. ``POST /v1/exec-tools/{tool}/prepare`` → 返回执行计划 + ``confirmToken``
2. ``POST /v1/exec-tools/{tool}/confirm`` → 携带令牌真正执行

**没有确认令牌就没有副作用。** 令牌一次性、短时效（5 分钟）、
绑定工具名与用户，跨工具/跨用户使用都会被拒。

授权隔离：执行类工具的授权键是 ``exec:<tool>``，与只读工具的
``get_metric_values`` 等名字互不重叠 —— 只读令牌无法调用执行类指令。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.models import AiToolCall
from ..services import exec_tool_service as svc
from ..services.agent_tool_token import (
    AgentToolTokenError,
    AgentToolTokenPayload,
    assert_tool_allowed,
    verify_agent_tool_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/exec-tools", tags=["exec-tools"])


class ExecPrepareRequest(BaseModel):
    """执行计划请求。"""

    task_id: str = Field(..., description="考核任务 ID")
    targets: list[str] = Field(default_factory=list, description="催办目标员工 ID 列表")
    message: str = Field("", description="催办附言")
    scope: str = Field("self", description="范围：self / department")


class ExecConfirmRequest(BaseModel):
    """执行确认请求。"""

    task_id: str = Field(..., description="考核任务 ID")
    confirm_token: str = Field(..., description="prepare 阶段返回的一次性确认令牌")


def _token_ctx(
    authorization: str | None = Header(default=None),
    x_agent_trace_id: str | None = Header(default=None),
) -> AgentToolTokenPayload:
    """解析并校验工具令牌（不含业务权限，权限在具体工具内校验）。"""
    parts = (authorization or "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="工具令牌无效")
    try:
        return verify_agent_tool_token(parts[1])
    except AgentToolTokenError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _audit(
    db: Session,
    *,
    payload: AgentToolTokenPayload,
    tool: str,
    args: dict[str, Any],
    ok: bool,
    latency_ms: int,
    error: str | None = None,
    trace_id: str | None = None,
) -> None:
    """写工具调用审计（保存点隔离，失败不污染业务事务）。"""
    try:
        with db.begin_nested():
            db.add(
                AiToolCall(
                    trace_id=trace_id,
                    agent="qa",
                    tool=tool,
                    employee_id=payload.sub,
                    task_id=args.get("task_id") or "",
                    args_hash=hashlib.sha256(
                        json.dumps(
                            args, ensure_ascii=False, sort_keys=True, default=str
                        ).encode()
                    ).hexdigest(),
                    args_keys=sorted(args),
                    ok=ok,
                    latency_ms=latency_ms,
                    error=error,
                )
            )
            db.flush()
    except Exception:
        logger.warning("执行类工具审计写入失败")


def _require_exec_allowed(payload: AgentToolTokenPayload, tool: str) -> None:
    """执行类工具授权：要求令牌里带 ``exec:<tool>``。

    只读令牌（``get_metric_values`` 等）**不能**调用执行类指令。
    """
    try:
        assert_tool_allowed(payload, svc.exec_scope(tool))
    except AgentToolTokenError as exc:
        raise HTTPException(status_code=403, detail="未授权执行该操作") from exc


def _plan_response(plan: svc.ExecPlan) -> dict[str, Any]:
    return {
        "code": 0,
        "message": "",
        "data": {
            "tool": plan.tool,
            "summary": plan.summary,
            "impact": plan.impact,
            "confirmToken": plan.confirm_token,
            "expiresAt": plan.expires_at,
        },
    }


# ---------------------------------------------------------------------------
# 催办
# ---------------------------------------------------------------------------


@router.post("/exec_remind/prepare")
def prepare_remind(
    req: ExecPrepareRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """准备催办指令 —— 只生成计划，不产生副作用。"""
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, "exec_remind")
        plan = svc.prepare_remind(
            db,
            employee_id=payload.sub,
            task_id=req.task_id,
            targets=req.targets,
            message=req.message,
        )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool="exec_remind.prepare",
            args=req.model_dump(), ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool="exec_remind.prepare",
        args=req.model_dump(), ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return _plan_response(plan)


@router.post("/exec_remind/confirm")
def confirm_remind(
    req: ExecConfirmRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """确认并执行催办。"""
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, "exec_remind")
        plan = svc.verify_confirm_token(
            req.confirm_token, tool="exec_remind", employee_id=payload.sub
        )
        result = svc.execute_remind(
            db,
            employee_id=payload.sub,
            task_id=req.task_id,
            targets=plan["params"]["targets"],
            message=plan["params"].get("message", ""),
        )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool="exec_remind.confirm",
            args={"task_id": req.task_id}, ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool="exec_remind.confirm",
        args={"task_id": req.task_id}, ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return {"code": 0, "message": "", "data": result}


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


@router.post("/exec_export/prepare")
def prepare_export(
    req: ExecPrepareRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """准备导出指令。"""
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, "exec_export")
        plan = svc.prepare_export(
            db, employee_id=payload.sub, task_id=req.task_id, scope=req.scope
        )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool="exec_export.prepare",
            args=req.model_dump(), ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool="exec_export.prepare",
        args=req.model_dump(), ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return _plan_response(plan)


@router.post("/exec_export/confirm")
def confirm_export(
    req: ExecConfirmRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """确认并执行导出。"""
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, "exec_export")
        plan = svc.verify_confirm_token(
            req.confirm_token, tool="exec_export", employee_id=payload.sub
        )
        result = svc.execute_export(
            db,
            employee_id=payload.sub,
            task_id=req.task_id,
            scope=plan["params"]["scope"],
        )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool="exec_export.confirm",
            args={"task_id": req.task_id}, ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool="exec_export.confirm",
        args={"task_id": req.task_id}, ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return {"code": 0, "message": "", "data": result}


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


@router.post("/exec_stats/prepare")
def prepare_stats(
    req: ExecPrepareRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """准备统计指令。"""
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, "exec_stats")
        plan = svc.prepare_stats(
            db, employee_id=payload.sub, task_id=req.task_id, scope=req.scope
        )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool="exec_stats.prepare",
            args=req.model_dump(), ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool="exec_stats.prepare",
        args=req.model_dump(), ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return _plan_response(plan)


@router.post("/exec_stats/confirm")
def confirm_stats(
    req: ExecConfirmRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """确认并执行统计。"""
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, "exec_stats")
        plan = svc.verify_confirm_token(
            req.confirm_token, tool="exec_stats", employee_id=payload.sub
        )
        result = svc.execute_stats(
            db,
            employee_id=payload.sub,
            task_id=req.task_id,
            scope=plan["params"]["scope"],
        )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool="exec_stats.confirm",
            args={"task_id": req.task_id}, ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool="exec_stats.confirm",
        args={"task_id": req.task_id}, ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return {"code": 0, "message": "", "data": result}


# ---------------------------------------------------------------------------
# 参数化别名：``/exec-tools/{tool}/prepare`` 与 ``/exec-tools/{tool}/confirm``
# ---------------------------------------------------------------------------
# ⚠️ 为什么要留这两组「同义」路径：
#
#   1. 上面那组 ``/exec_remind/prepare`` 是**按工具逐个硬编码**的 ——
#      每加一个执行类工具就要复制两个端点。
#   2. 前端（``frontend/src/api/p0p1.ts``）用的是**参数化**路径：
#      ``/exec-tools/${tool}/prepare`` —— 新增工具时前端**零改动**。
#
# 两组路径共用**同一份 service 实现**（下面按 tool 分发），
# 所以行为完全一致；带工具名的那组保留，是为了兼容既有调用方与测试。


@router.post("/{tool}/prepare")
def prepare_by_tool(
    tool: str,
    req: ExecPrepareRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """按工具名分发 —— 第一阶段：生成计划，不产生副作用。

    支持的 ``tool``：``exec_remind`` / ``exec_export`` / ``exec_stats``。
    """
    if tool not in svc.EXEC_TOOLS:
        raise HTTPException(status_code=404, detail=f"未知执行类工具: {tool}")
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, tool)
        if tool == "exec_remind":
            plan = svc.prepare_remind(
                db,
                employee_id=payload.sub,
                task_id=req.task_id,
                targets=req.targets or [],
                message=req.message or "",
            )
        elif tool == "exec_export":
            plan = svc.prepare_export(
                db,
                employee_id=payload.sub,
                task_id=req.task_id,
                scope=req.scope or "self",
            )
        else:
            plan = svc.prepare_stats(
                db,
                employee_id=payload.sub,
                task_id=req.task_id,
                scope=req.scope or "department",
            )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool=f"{tool}.prepare",
            args=req.model_dump(), ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool=f"{tool}.prepare",
        args=req.model_dump(), ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return _plan_response(plan)


@router.post("/{tool}/confirm")
def confirm_by_tool(
    tool: str,
    req: ExecConfirmRequest,
    payload: AgentToolTokenPayload = Depends(_token_ctx),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """按工具名分发 —— 第二阶段：携带一次性令牌真正执行。

    ⚠️ 令牌**绑定工具名**：用 ``exec_remind`` 的令牌去确认 ``exec_export``
    会被拒（409）—— 这是防跨工具滥用的边界。
    """
    if tool not in svc.EXEC_TOOLS:
        raise HTTPException(status_code=404, detail=f"未知执行类工具: {tool}")
    started = time.perf_counter()
    try:
        _require_exec_allowed(payload, tool)
        plan = svc.verify_confirm_token(
            req.confirm_token, tool=tool, employee_id=payload.sub
        )
        if tool == "exec_remind":
            result = svc.execute_remind(
                db,
                employee_id=payload.sub,
                task_id=req.task_id,
                targets=plan["params"]["targets"],
                message=plan["params"]["message"],
            )
        elif tool == "exec_export":
            result = svc.execute_export(
                db,
                employee_id=payload.sub,
                task_id=req.task_id,
                scope=plan["params"]["scope"],
            )
        else:
            result = svc.execute_stats(
                db,
                employee_id=payload.sub,
                task_id=req.task_id,
                scope=plan["params"]["scope"],
            )
    except svc.ExecToolError as exc:
        _audit(
            db, payload=payload, tool=f"{tool}.confirm",
            args={"task_id": req.task_id}, ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=exc.message,
        )
        db.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    _audit(
        db, payload=payload, tool=f"{tool}.confirm",
        args={"task_id": req.task_id}, ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.commit()
    return {"code": 0, "message": "", "data": result}
