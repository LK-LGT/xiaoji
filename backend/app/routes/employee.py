"""员工工作台 API 路由。

对应前端 `src/api/employee.ts` 的 `/employee/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee` 前缀。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import DayPlan, Employee
from ..services import plan_adjustment_service, workday_rule
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee", tags=["employee"], dependencies=[Depends(get_current_user)])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


def _normalize_month(month: str | None) -> str:
    """归一化月份为 `YYYY-MM`；非法或缺省时取当前月。"""
    text = str(month or "").strip()[:7]
    if len(text) == 7:
        try:
            year, mon = int(text[:4]), int(text[5:7])
        except ValueError:
            pass
        else:
            if 1 <= mon <= 12:
                return f"{year:04d}-{mon:02d}"
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _month_bounds(month: str) -> tuple[date, date]:
    """`YYYY-MM` → 该月首尾日期（含首尾）。"""
    year, mon = int(month[:4]), int(month[5:7])
    start = date(year, mon, 1)
    if mon == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, mon + 1, 1) - timedelta(days=1)
    return start, end


def _department_name(employee: Employee) -> str:
    """员工所在部门名称（取不到时返回空串）。"""
    dept = getattr(employee, "department", None)
    if dept is None:
        return ""
    return getattr(dept, "name", "") or ""


# ---------------------------------------------------------------------------
# 导出任务
#
# 响应结构：二进制文件流
# ---------------------------------------------------------------------------


@router.get("/export-tasks/{task_id}/download")
async def download_employee_export_task(
    task_id: str,
    db: Session = Depends(get_db),
) -> Any:
    """下载员工侧导出任务产物。

    响应结构：二进制文件流
    """
    # 尚无真实导出产物时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="导出产物不存在或已过期")


# ---------------------------------------------------------------------------
# 工作台仪表盘
#
# 响应结构：EmployeeDashboardResp
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def get_employee_dashboard(
    month: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取员工工作台仪表盘。

    响应结构：EmployeeDashboardResp

    ⚠️ ``workdayCalendar`` / ``missingRequiredDayPlanCount`` 由
    ``services.workday_rule`` 真实计算：按「工作日规则」表解析当月每一天的
    日历口径，标出「强制填报但没有日计划」的缺报日。
    """
    month_text = _normalize_month(month)
    start, end = _month_bounds(month_text)

    # 当月日历：个人规则 > 全局规则 > 默认推导
    calendar = workday_rule.build_calendar(db, start, end, current_user.id)

    # 已有的日计划日期（用于判断缺报）
    plan_stmt = select(DayPlan.plan_date).where(
        DayPlan.owner_id == current_user.id,
        DayPlan.plan_date >= start,
        DayPlan.plan_date <= end,
    )
    existing_dates = set(db.execute(plan_stmt).scalars().all())

    # 标注「应填未填」与「未排班」
    missing_required_count = 0
    unassigned_count = 0
    day_plan_calendar: list[dict[str, Any]] = []
    for item in calendar:
        day = date.fromisoformat(item["date"])
        has_plan = day in existing_dates
        if item.get("ruleType") == "UNASSIGNED":
            unassigned_count += 1
            item["missingRequired"] = False
        elif item["forceReport"] and not has_plan:
            item["missingRequired"] = True
            missing_required_count += 1
        else:
            item["missingRequired"] = False
        if has_plan:
            day_plan_calendar.append({"date": item["date"], "status": "draft"})

    return _ok(
        {
            "currentMonth": month_text,
            "orgName": _department_name(current_user),
            "monthPlans": [],
            "dayPlanCalendar": day_plan_calendar,
            "workdayCalendar": calendar,
            "summary": {
                "monthPlanCount": 0,
                "submittedResultCount": 0,
                "averageCompletionRate": 0,
                "openAppealCount": 0,
                "missingRequiredDayPlanCount": missing_required_count,
                "unassignedDayCount": unassigned_count,
            },
        }
    )


# ---------------------------------------------------------------------------
# 绩效证据
#
# 响应结构：EmployeePerformanceEvidenceResp
# ---------------------------------------------------------------------------


@router.get("/performance-evidence")
async def get_employee_performance_evidence(
    period_type: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取员工绩效证据列表。

    响应结构：EmployeePerformanceEvidenceResp
    = { periodType, periodStart, periodEnd, items }
    """
    return _ok(
        {
            "periodType": period_type or "",
            "periodStart": "",
            "periodEnd": "",
            "items": [],
        }
    )


@router.post("/performance-evidence/export")
async def export_employee_performance_evidence(
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """导出员工绩效证据。

    响应结构：EmployeeExportResp = { taskId, fileName, checksum, downloadUrl }
    """
    return _ok(
        {
            "taskId": "",
            "fileName": "",
            "checksum": "",
            "downloadUrl": "",
        }
    )


# ---------------------------------------------------------------------------
# 申诉
#
# 响应结构：EmployeeAppealListResp / EmployeeAppealOption[]
# ---------------------------------------------------------------------------


@router.get("/appeals")
async def list_employee_appeals(current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """获取员工申诉列表。

    响应结构：EmployeeAppealListResp = { items }
    """
    from ..domain.plan_models import DisputeCase
    from ..services.review_center import employee_item
    rows = db.scalars(select(DisputeCase).where(DisputeCase.owner_id == current_user.id).order_by(DisputeCase.created_at.desc())).all()
    return _ok({"items": [employee_item(c) for c in rows]})


@router.get("/appeals/options")
async def list_employee_appeal_options(current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """获取可申诉的成果选项。

    响应结构：EmployeeAppealOption[]（裸数组）
    """
    from ..services.review_center import appeal_options
    return _ok(appeal_options(db, current_user))


@router.post("/appeals")
async def create_employee_appeal(data: JsonBody, current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """发起申诉。

    请求体：CreateEmployeeAppealReq = { relatedResultId?, title, reason }
    响应结构：EmployeeAppealItem
    """
    from ..services.review_center import create_case, employee_item
    case = create_case(db, current_user, target=str(data.get("relatedResultId") or ""), title=str(data.get("title") or ""), reason=str(data.get("reason") or ""))
    return _ok(employee_item(case))


@router.get("/appeals/{appeal_id}/package")
async def download_employee_appeal_package(
    appeal_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载员工申诉证据包。

    响应结构：二进制文件流
    """
    from fastapi.responses import Response

    from ..services.review_center import get_case, package
    case = get_case(db, appeal_id)
    if case.owner_id != current_user.id:
        raise HTTPException(403, "仅能下载本人案件证据包")
    return Response(package(db, current_user, case), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="review-{case.id}.zip"', "Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# 计划调整
#
# 响应结构：{ id, adjustmentNo, status }
# ---------------------------------------------------------------------------


@router.post("/plan-adjustments")
async def create_employee_plan_adjustment(
    data: JsonBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """发起计划调整申请。

    请求体：CreateEmployeePlanAdjustmentReq
    响应结构：{ id, adjustmentNo, status }

    ⚠️ **2026-11-26：原实现返回 ``{"id": 0, "adjustmentNo": ""}``**
    —— 员工点了「申请调整」，**库里什么都没发生**，
    领导端也就永远看不到这条申请 ⇒ 整条链路是断的。
    ⇒ 现在**真落库**并返回真实的单号。
    """
    try:
        item = plan_adjustment_service.create_adjustment(
            db, dict(data), owner_id=current_user.id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _ok({
        "id": item["id"],
        "adjustmentNo": item["adjustmentNo"],
        "status": item["status"],
    })
