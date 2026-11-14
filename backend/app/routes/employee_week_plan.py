"""员工侧周计划 API 路由。

对应前端 `src/api/weekPlan.ts` 与 `src/api/weekResult.ts` 的
`/employee/week-plans/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee/week-plans` 前缀。

分页约定：前端 WeekPlanSummary[] 直接 .map()，故列表端点返回裸数组。

⚠️ **当前实现状态**：本模块为**契约层实现** —— 路由、HTTP 方法、请求参数、
响应结构全部对齐前端 TypeScript 类型定义，但**尚未接入领域模型**。

后端当前 34 张表中没有任何计划类表（无 `month_plan` / `week_plan` / `day_plan`
及其 item / 版本 / 结果表），因此这里统一返回结构正确但内容为空的响应，
目的是让前端页面不再报 404 / 类型错误，同时把「领域建模 + 真实 CRUD + 状态机」
作为独立排期项。

鉴权：全部端点均要求有效 token（`get_current_user`），匿名请求返回 401。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee/week-plans", tags=["employee-week-plan"])


# ---------------------------------------------------------------------------
# 请求模型
#
# 注意：必须用显式 Pydantic 模型，不能用 `payload: dict`。
# 本项目所用 FastAPI 版本不会把裸 dict 注解自动解析为 JSON body，
# 请求体会以字符串形式传入并触发 422（dict_type 校验失败）。
# ---------------------------------------------------------------------------


class WeekPlanSavePayload(BaseModel):
    """周计划保存请求体，对应前端 WeekPlanSavePayload。"""

    model_config = {"extra": "allow"}

    weekStart: str = Field(default="")
    versionNo: Optional[int] = None
    items: list[dict] = Field(default_factory=list)
    recordVersion: bool = False
    versionRequestId: str = ""
    restoredFromVersionId: Optional[int] = None


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 周计划本体
#
# 响应结构：WeekPlanSummary[] / WeekPlanDetail / WeekPlanParentOption[]
# ---------------------------------------------------------------------------


@router.get("")
async def list_employee_week_plans(
    status: Optional[str] = Query(None),
    weekStart: Optional[str] = Query(None),
    deptId: Optional[int] = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取我的周计划列表。

    响应结构：WeekPlanSummary[]（裸数组）
    """
    return _ok([])


@router.get("/parent-options")
async def list_week_plan_parent_options(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周计划可选的父级月计划任务。

    响应结构：WeekPlanParentOption[]（裸数组）
    """
    return _ok([])


@router.get("/{plan_id}")
async def get_employee_week_plan(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周计划详情。

    响应结构：WeekPlanDetail
    """
    return _ok(
        {
            "summary": _empty_week_plan_summary(plan_id),
            "items": [],
            "siblingPlans": [],
            "dayPlanCount": 0,
        }
    )


@router.post("")
async def create_employee_week_plan(
    payload: WeekPlanSavePayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """创建周计划。

    响应结构：WeekPlanDetail
    """
    return _ok(
        {
            "summary": _empty_week_plan_summary(),
            "items": [],
            "siblingPlans": [],
            "dayPlanCount": 0,
        }
    )


@router.put("/{plan_id}")
async def update_employee_week_plan(
    plan_id: int,
    payload: WeekPlanSavePayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """更新周计划。

    响应结构：WeekPlanDetail
    """
    return _ok(
        {
            "summary": _empty_week_plan_summary(plan_id),
            "items": [],
            "siblingPlans": [],
            "dayPlanCount": 0,
        }
    )


@router.delete("/{plan_id}")
async def delete_employee_week_plan(
    plan_id: int,
    versionNo: int = Query(..., description="乐观锁版本号"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """删除周计划。

    响应结构：WeekPlanAction
    """
    return _ok(_empty_week_plan_action(plan_id, "DRAFT", versionNo))


@router.post("/{plan_id}/submit")
async def submit_employee_week_plan(
    plan_id: int,
    versionNo: int = Query(..., description="乐观锁版本号"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交周计划审批。

    响应结构：WeekPlanAction
    """
    return _ok(_empty_week_plan_action(plan_id, "PENDING", versionNo))


@router.post("/{plan_id}/withdraw")
async def withdraw_employee_week_plan(
    plan_id: int,
    versionNo: int = Query(..., description="乐观锁版本号"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """撤回周计划。

    响应结构：WeekPlanAction
    """
    return _ok(_empty_week_plan_action(plan_id, "DRAFT", versionNo))


# ---------------------------------------------------------------------------
# 周成果提交
#
# 响应结构：WeekResultSubmitContext / WeekResultSubmitAction
# ---------------------------------------------------------------------------


@router.get("/{plan_id}/items/{item_id}/results/submit-context")
async def get_week_result_submit_context(
    plan_id: int,
    item_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周成果提交上下文。

    响应结构：WeekResultSubmitContext
    """
    return _ok(
        {
            "weekPlanId": plan_id,
            "weekPlanTitle": "",
            "weekStart": "",
            "weekEnd": "",
            "weekPlanStatus": "DRAFT",
            "task": _empty_week_result_task(plan_id, item_id),
            "latestResult": None,
            "acceptedFileTypes": [],
            "maxFileSizeMb": 0,
        }
    )


@router.post("/{plan_id}/items/{item_id}/results/submit")
async def submit_week_result(
    plan_id: int,
    item_id: int,
    file: Optional[UploadFile] = File(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交周成果。

    响应结构：WeekResultSubmitAction
    """
    return _ok(
        {
            "id": 0,
            "status": "submitted",
            "submittedAt": datetime.utcnow().isoformat(),
            "message": "周结果已提交",
        }
    )


def _empty_week_plan_summary(plan_id: int = 0) -> dict:
    """构造 WeekPlanSummary，字段与 frontend/src/api/weekPlan.ts 保持一致。"""
    return {
        "id": plan_id,
        "title": "",
        "weekStart": "",
        "weekEnd": "",
        "status": "DRAFT",
        "versionNo": 1,
        "ownerUserId": 0,
        "employeeName": "",
        "deptId": None,
        "departmentName": "",
        "itemCount": 0,
        "submitAt": None,
        "approveAt": None,
        "approvalComment": None,
    }


def _empty_week_plan_action(plan_id: int, status: str, version_no: int = 1) -> dict:
    """构造 WeekPlanAction，字段与 frontend/src/api/weekPlan.ts 保持一致。"""
    return {
        "id": plan_id,
        "status": status,
        "versionNo": version_no,
        "message": "",
    }


def _empty_week_result_task(plan_id: int, item_id: int) -> dict:
    """构造 WeekResultTask，字段与 frontend/src/api/weekResult.ts 保持一致。"""
    return {
        "id": item_id,
        "weekPlanId": plan_id,
        "monthPlanItemId": None,
        "content": "",
        "deliverable": None,
        "quality": None,
        "plannedFinishDate": None,
        "sortNo": 0,
    }
