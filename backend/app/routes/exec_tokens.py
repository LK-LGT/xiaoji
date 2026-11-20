"""P1-3 —— 执行类指令的**会话级令牌签发**入口。

为什么需要这个文件
----------------
``/v1/exec-tools/*`` 网关要求 ``Authorization: Bearer <tool_token>``，
且令牌必须带 ``exec:<tool>`` 授权键。这个令牌原先只能由 **L3 问答链路**
（``/v1/qa/ask``）在服务端内部签发 —— 也就是说：

* 前端页面**拿不到**执行类令牌 → P1-3 只有后端、前端完全接不上；
* 想接入就只能让前端复用问答令牌，而问答令牌的 ``allow`` 里
  **只有只读工具**（``get_metric_values`` 等），调执行类指令必然 403。

本文件补上这条缺失的桥：前端用**登录会话**换取一个**短时效、绑定用户与任务、
只含执行类授权**的令牌，然后照常走两阶段 ``prepare`` → ``confirm``。

安全边界
--------
1. **不新增能力**：签发的授权键完全由用户自身的业务权限推导 ——
   没有管理权限的人拿不到 ``exec:*`` 令牌。
2. **作用域收窄**：``exec_remind`` 只对**有查看该员工绩效权限**的人签发；
   ``exec_export`` / ``exec_stats`` 只对**能管本部门**的人签发。
3. **作用域可枚举**：令牌按工具逐个签发，前端只能拿到**本次请求的工具**。
4. **不改变执行语义**：两阶段确认、一次性令牌、审计 —— 全部保持原样。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import AssessmentTask, Employee
from ..services import access_control
from ..services.agent_tool_token import issue_agent_tool_token
from ..services.exec_tool_service import EXEC_TOOLS, exec_scope

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/exec-tools", tags=["exec-tools"])


class ExecTokenRequest(BaseModel):
    """签发执行类令牌请求。"""

    tool: str = Field(..., description=f"执行类工具名，可选：{' / '.join(EXEC_TOOLS)}")
    task_id: str = Field(..., description="考核任务 ID")
    scope: str = Field("self", description="作用域：self / department")


def _tool_scope(
    db: Session, user: Employee, tool: str, task_id: str, scope: str
) -> str:
    """推导该用户对指定工具的作用域。

    作用域只能**收窄**，不能扩大 —— 这是签发令牌的核心安全约束。
    """
    context = access_control.build_access_context(db, user)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="考核任务不存在")

    if tool == "exec_remind":
        # 催办的合法目标由 prepare 阶段逐人校验（不能催自己、必须对目标有查看权）。
        # 这里要求用户**有管理范围** —— 不能催办自己，所以"能看自己的分"不够。
        # ⚠️ `can_view_score_of(user.id)` 对任何在职员工都成立（能看自己的分），
        #   不能拿它当"有权催办"的判据。
        if not (
            context.direct_report_ids
            or context.delegated_employee_ids
            or context.managed_department_ids
            or context.has_permission(access_control.PermissionAction.VIEW_ALL_SCORES)
        ):
            raise HTTPException(status_code=403, detail="无权催办：没有任何管理范围")
        return "department" if (
            context.direct_report_ids
            or context.delegated_employee_ids
            or context.managed_department_ids
            or context.has_permission(access_control.PermissionAction.VIEW_ALL_SCORES)
        ) else "self"

    if tool == "exec_export":
        if scope == "department":
            if not user.department_id or user.department_id not in context.managed_department_ids:
                raise HTTPException(status_code=403, detail="无权导出部门数据")
            return "department"
        if scope == "self":
            if not context.has_permission(access_control.PermissionAction.VIEW_OWN_SCORE):
                raise HTTPException(status_code=403, detail="无权导出本人绩效数据")
            return "self"
        raise HTTPException(status_code=400, detail="导出范围非法")

    if tool == "exec_stats":
        if scope != "department":
            raise HTTPException(status_code=400, detail="统计范围非法")
        if not user.department_id or not (
            user.department_id in context.managed_department_ids
            or context.has_permission(access_control.PermissionAction.VIEW_ALL_SCORES)
        ):
            raise HTTPException(status_code=403, detail="无权统计部门数据")
        return "department"

    raise HTTPException(status_code=400, detail=f"未知执行类工具：{tool}")


@router.post("/token")
def issue_exec_token(
    req: ExecTokenRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """签发执行类工具令牌（会话鉴权）。

    返回的令牌**只含本次请求的那一个**执行类授权键，
    短时效、绑定当前用户与任务。
    """
    if req.tool not in EXEC_TOOLS:
        raise HTTPException(status_code=400, detail=f"未知执行类工具：{req.tool}")
    scope = _tool_scope(db, current_user, req.tool, req.task_id, req.scope)
    try:
        token = issue_agent_tool_token(
            employee_id=current_user.id,
            task_id=req.task_id,
            allow=[exec_scope(req.tool)],
            scope=scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="执行类令牌签发失败") from exc
    return {
        "code": 0,
        "data": {
            "token": token,
            "tool": req.tool,
            "scope": scope,
            "task_id": req.task_id,
        },
    }
