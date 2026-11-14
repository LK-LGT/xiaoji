"""员工计划闭环 API 路由（月计划 / 周计划 / 日计划）。

对应前端：
- `src/api/employee.ts` 的 `/employee/month-plans/*`、`/employee/day-plans/*`
- `src/api/weekPlan.ts` 的 `/employee/week-plans/*`
- `src/api/weekResult.ts` 的 `/employee/week-plans/{id}/items/{id}/results/*`

⚠️ **当前实现状态**：本模块为**契约层实现** —— 路由、HTTP 方法、请求参数、
响应结构全部对齐前端 TypeScript 类型定义，但**尚未接入领域模型**。

后端当前 34 张表中没有任何计划类表（无 `month_plan` / `week_plan` / `day_plan`
及其 item / 版本 / 结果表），因此这里统一返回结构正确但内容为空的响应，
目的是让前端页面不再报 404 / 类型错误，同时把「领域建模 + 真实 CRUD + 状态机」
作为独立排期项。响应体里的字段集合与前端类型**逐字段对齐**，
后续接入真实数据时只需替换 `_empty_*` 构造器，不改接口签名。

前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee", tags=["employee-plans"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ===========================================================================
# 月计划 —— /employee/month-plans/*
#
# 响应结构：EmployeeMonthPlanDetailResp / EmployeeMonthPlanTemplatePreviewResp /
#           EmployeeMonthPlanSubmitResp
# ===========================================================================


@router.get("/month-plans/{plan_id}")
async def get_employee_month_plan(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取员工月计划详情。

    响应结构：EmployeeMonthPlanDetailResp
    """
    return _ok(_empty_month_plan_detail(plan_id))


@router.get("/month-plans/{plan_id}/template-preview")
async def get_employee_month_plan_template_preview(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取月计划模板预览。

    响应结构：EmployeeMonthPlanTemplatePreviewResp
    """
    return _ok(_empty_month_plan_template_preview(plan_id))


@router.get("/month-plans/{plan_id}/template-download")
async def download_employee_month_plan_template(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载月计划模板。

    响应结构：二进制文件流
    """
    # 尚无真实模板产物时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="模板不存在或已过期")


@router.post("/month-plans/draft")
async def create_employee_month_plan_draft(
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """新建月计划草稿。

    请求体：SaveMonthPlanDraftReq
    响应结构：EmployeeMonthPlanDetailResp
    """
    return _ok(_empty_month_plan_detail(0, plan_month=data.get("planMonth")))


@router.post("/month-plans/{plan_id}/draft")
async def save_employee_month_plan_draft(
    plan_id: int,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """保存月计划草稿。

    请求体：SaveMonthPlanDraftReq
    响应结构：EmployeeMonthPlanDetailResp
    """
    return _ok(_empty_month_plan_detail(plan_id))


@router.post("/month-plans/{plan_id}/draft/clear")
async def clear_employee_month_plan_draft(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """清空月计划草稿内容。

    响应结构：EmployeeMonthPlanDraftClearResp
    （= EmployeeMonthPlanDetailResp 但 id 为 null）
    """
    detail = _empty_month_plan_detail(plan_id)
    detail["id"] = None
    return _ok(detail)


@router.post("/month-plans/{plan_id}/submit")
async def submit_employee_month_plan(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交月计划。

    响应结构：EmployeeMonthPlanSubmitResp
    """
    return _ok({"id": plan_id, "status": "submitted"})


@router.post("/month-plans/{plan_id}/withdraw")
async def withdraw_employee_month_plan(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """撤回月计划。

    响应结构：EmployeeMonthPlanSubmitResp
    """
    return _ok({"id": plan_id, "status": "draft"})


@router.delete("/month-plans/{plan_id}/items/{item_id}")
async def delete_employee_month_plan_item(
    plan_id: int,
    item_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """删除月计划条目。

    响应结构：void（前端按 void 处理，此处返回空信封）
    """
    return _ok(None)


# ===========================================================================
# 周计划 —— /employee/week-plans/*
#
# ⚠️ 周计划的 10 个端点实现在 `employee_week_plan.py`（独立 router，前缀
# `/v1/employee/week-plans`），此处不再重复定义，避免同一路径注册两次
# 导致 OpenAPI 出现 Duplicate Operation ID。
# ===========================================================================


# ===========================================================================
# 日计划 —— /employee/day-plans/*
#
# 响应结构：EmployeeDayPlanDetailResp / EmployeeDayPlanSubmitResp
# ===========================================================================


@router.get("/day-plans/detail")
async def get_employee_day_plan_detail(
    date: str = Query(..., description="计划日期 YYYY-MM-DD"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取某一天的日计划详情。

    查询参数：date
    响应结构：EmployeeDayPlanDetailResp
    """
    return _ok(_empty_day_plan_detail(date))


@router.post("/day-plans/draft")
async def save_employee_day_plan_draft(
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """保存日计划草稿。

    请求体：SaveDayPlanDraftReq
    响应结构：EmployeeDayPlanDetailResp
    """
    return _ok(_empty_day_plan_detail(data.get("planDate", "")))


@router.post("/day-plans/submit")
async def submit_employee_day_plan(
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交日计划。

    请求体：SubmitDayPlanReq
    响应结构：EmployeeDayPlanSubmitResp
    """
    return _ok({"id": 0, "status": "submitted", "submittedAt": ""})


@router.post("/day-plans/{plan_id}/withdraw")
async def withdraw_employee_day_plan(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """撤回日计划。

    响应结构：EmployeeDayPlanSubmitResp
    """
    return _ok({"id": plan_id, "status": "draft", "submittedAt": ""})


@router.delete("/day-plans/{plan_id}")
async def delete_employee_day_plan_draft(
    plan_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """删除日计划草稿。

    响应结构：void（前端按 void 处理，此处返回空信封）
    """
    return _ok(None)


# ===========================================================================
# 响应构造辅助
#
# 以下构造器的字段集合与前端 TypeScript 接口**逐字段对齐**，
# 后续接入真实数据时只需替换构造器内容，不改接口签名。
# ===========================================================================


def _empty_month_plan_detail(
    plan_id: int, plan_month: Optional[str] = None
) -> dict:
    """构造 EmployeeMonthPlanDetailResp。"""
    return {
        "id": plan_id,
        "planMonth": plan_month or "",
        "employeeName": "",
        "departmentName": "",
        "status": "draft",
        "resultStatus": "not_submitted",
        "updatedAt": "",
        "items": [],
        "deliverables": [],
        "resultSummary": {
            "submittedCount": 0,
            "confirmedCount": 0,
            "rejectedCount": 0,
            "latestVersion": "",
            "overallCompletionRate": 0,
        },
        "confirmRecords": [],
    }


def _empty_month_plan_template_preview(plan_id: int) -> dict:
    """构造 EmployeeMonthPlanTemplatePreviewResp。"""
    return {
        "planId": plan_id,
        "planMonth": "",
        "items": [],
        "estimatedValueTotal": 0,
    }


def _empty_day_plan_detail(plan_date: str) -> dict:
    """构造 EmployeeDayPlanDetailResp。"""
    return {
        "id": 0,
        "planDate": plan_date,
        "orgName": "",
        "relatedWeekPlanItemIds": [],
        "content": "",
        "remark": "",
        "status": "draft",
        "calendarRule": {
            "date": plan_date,
            "ruleType": "WORKDAY",
            "forceReport": False,
            "explicit": False,
            "missingRequired": False,
        },
        "weekPlanItemOptions": [],
        "legacyMonthPlanItem": None,
    }
