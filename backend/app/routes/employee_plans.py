"""员工计划闭环 API 路由（月计划 / 日计划）。

对应前端：
- `src/api/employee.ts` 的 `/employee/month-plans/*`、`/employee/day-plans/*`

实现说明：
- 领域逻辑全部在 `services/planning.py`（含状态机、乐观锁、序列化）。
  本模块只做「HTTP ↔ 服务层」的转接：解析入参、调用服务、把领域异常映射成
  合适的 HTTP 状态码。
- 状态机不在路由里散落 `plan.status = ...`，一律走
  `planning.submit_* / withdraw_*` 等服务函数。
- 数据范围：以当前登录用户的 `owner_id` 为边界，跨人访问直接 404
  （不泄露「该 id 存在但不属于你」这一信息）。

前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import planning
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee", tags=["employee-plans"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


def _translate(exc: Exception) -> HTTPException:
    """把领域异常映射成 HTTP 状态码。

    - `PlanNotFound` → 404
    - `PlanStateError` → 409（当前状态不允许该操作）
    - `PlanVersionConflict` → 409（乐观锁冲突）
    - `ValueError` → 400（入参非法）
    """
    if isinstance(exc, planning.PlanNotFound):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, planning.PlanVersionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, planning.PlanStateError):
        return HTTPException(status_code=409, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="计划操作失败")


def _run(fn, *args, **kwargs):
    """执行服务层调用，把领域异常转成 HTTPException。"""
    try:
        return fn(*args, **kwargs)
    except (planning.PlanNotFound, planning.PlanStateError, planning.PlanVersionConflict, ValueError) as exc:
        raise _translate(exc) from exc


# ===========================================================================
# 月计划 —— /employee/month-plans/*
#
# 响应结构：EmployeeMonthPlanDetailResp / EmployeeMonthPlanTemplatePreviewResp /
#           EmployeeMonthPlanSubmitResp
# ===========================================================================


@router.get("/month-plans/{plan_id}")
async def get_employee_month_plan(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取员工月计划详情。

    响应结构：EmployeeMonthPlanDetailResp
    """
    plan = _run(planning.get_month_plan, db, current_user.id, plan_id)
    items = _run(planning.list_month_plan_items, db, plan.id)
    dept_name = _run(planning.department_name_for, db, current_user)
    return _ok(planning.serialize_month_plan_detail(plan, items, current_user, dept_name))


@router.get("/month-plans/{plan_id}/template-preview")
async def get_employee_month_plan_template_preview(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取月计划模板预览。

    响应结构：EmployeeMonthPlanTemplatePreviewResp
    """
    preview = _run(planning.month_plan_template_preview, db, current_user.id, plan_id)
    return _ok(preview)


@router.get("/month-plans/{plan_id}/template-download")
async def download_employee_month_plan_template(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载月计划模板（Excel）。

    响应结构：二进制文件流
    """
    # 模板产物尚未上线：先校验计划归属，无产物时返回 404，避免前端拿到损坏文件。
    _run(planning.get_month_plan, db, current_user.id, plan_id)
    raise HTTPException(status_code=404, detail="模板文件尚未生成")


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
    payload = data.model_dump()
    plan = _run(planning.upsert_month_plan_draft, db, current_user.id, None, payload)
    items = _run(planning.list_month_plan_items, db, plan.id)
    dept_name = _run(planning.department_name_for, db, current_user)
    return _ok(planning.serialize_month_plan_detail(plan, items, current_user, dept_name))


@router.post("/month-plans/{plan_id}/draft")
async def save_employee_month_plan_draft(
    plan_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """保存月计划草稿。

    请求体：SaveMonthPlanDraftReq
    响应结构：EmployeeMonthPlanDetailResp
    """
    payload = data.model_dump()
    plan = _run(planning.upsert_month_plan_draft, db, current_user.id, plan_id, payload)
    items = _run(planning.list_month_plan_items, db, plan.id)
    dept_name = _run(planning.department_name_for, db, current_user)
    return _ok(planning.serialize_month_plan_detail(plan, items, current_user, dept_name))


@router.post("/month-plans/{plan_id}/draft/clear")
async def clear_employee_month_plan_draft(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """清空月计划草稿内容（保留计划本体）。

    响应结构：EmployeeMonthPlanDraftClearResp
    （= EmployeeMonthPlanDetailResp 但 id 为 null）
    """
    plan = _run(planning.clear_month_plan_draft, db, current_user.id, plan_id)
    dept_name = _run(planning.department_name_for, db, current_user)
    detail = planning.serialize_month_plan_detail(plan, [], current_user, dept_name)
    detail["id"] = None
    return _ok(detail)


@router.post("/month-plans/{plan_id}/submit")
async def submit_employee_month_plan(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交月计划（草稿 → 已提交）。

    响应结构：EmployeeMonthPlanSubmitResp
    """
    plan = _run(planning.submit_month_plan, db, current_user.id, plan_id)
    return _ok(
        {
            "id": plan.id,
            "status": plan.status,
            "submittedAt": planning._iso(plan.submitted_at),
        }
    )


@router.post("/month-plans/{plan_id}/withdraw")
async def withdraw_employee_month_plan(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """撤回月计划（已提交 → 草稿）。

    响应结构：EmployeeMonthPlanSubmitResp
    """
    plan = _run(planning.withdraw_month_plan, db, current_user.id, plan_id)
    return _ok(
        {
            "id": plan.id,
            "status": plan.status,
            "submittedAt": planning._iso(plan.submitted_at),
        }
    )


@router.delete("/month-plans/{plan_id}/items/{item_id}")
async def delete_employee_month_plan_item(
    plan_id: str,
    item_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """删除月计划条目。

    响应结构：void（前端按 void 处理，此处返回空信封）
    """
    _run(planning.delete_month_plan_item, db, current_user.id, plan_id, item_id)
    return _ok(None)


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
    """获取某一天的日计划详情（当天无计划时返回空壳 + 可选项）。

    查询参数：date
    响应结构：EmployeeDayPlanDetailResp
    """
    try:
        plan_date = planning.parse_date(date, "date")
    except ValueError as exc:
        raise _translate(exc) from exc

    plan = _run(planning.get_day_plan_by_date, db, current_user.id, plan_date)
    options = _run(planning.day_plan_week_item_options, db, current_user.id, plan_date)
    dept_name = _run(planning.department_name_for, db, current_user)
    return _ok(planning.serialize_day_plan_detail(plan, plan_date, dept_name, options, None))


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
    payload = data.model_dump()
    plan = _run(planning.upsert_day_plan, db, current_user.id, payload)
    options = _run(planning.day_plan_week_item_options, db, current_user.id, plan.plan_date)
    dept_name = _run(planning.department_name_for, db, current_user)
    return _ok(planning.serialize_day_plan_detail(plan, plan.plan_date, dept_name, options, None))


@router.post("/day-plans/submit")
async def submit_employee_day_plan(
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交日计划（草稿 → 已提交）。

    请求体：SubmitDayPlanReq
    响应结构：EmployeeDayPlanSubmitResp
    """
    payload = data.model_dump()
    plan = _run(planning.submit_day_plan, db, current_user.id, payload)
    return _ok(
        {
            "id": plan.id,
            "status": plan.status,
            "submittedAt": planning._iso(plan.submitted_at) or "",
        }
    )


@router.post("/day-plans/{plan_id}/withdraw")
async def withdraw_employee_day_plan(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """撤回日计划（已提交 → 草稿）。

    响应结构：EmployeeDayPlanSubmitResp
    """
    plan = _run(planning.withdraw_day_plan, db, current_user.id, plan_id)
    return _ok(
        {
            "id": plan.id,
            "status": plan.status,
            "submittedAt": planning._iso(plan.submitted_at) or "",
        }
    )


@router.delete("/day-plans/{plan_id}")
async def delete_employee_day_plan_draft(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """删除日计划草稿。

    响应结构：void（前端按 void 处理，此处返回空信封）
    """
    _run(planning.delete_day_plan, db, current_user.id, plan_id)
    return _ok(None)
