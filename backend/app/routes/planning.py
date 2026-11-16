"""计划统计 API 路由。

对应前端 `src/api/planning.ts` 的 `/planning/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/planning` 前缀。

⚠️ 历史坑（R6 / BUG-R6-01）：
`main.py` 里曾有 mock 版 `/planning/month-plans` 等端点（**不带 `/v1` 前缀**），
前端经代理后实际请求 `/v1/planning/month-plans` —— **永远命中不到 mock**，
于是员工月计划/日计划/成果列表页长期 404。
本文件补齐这三个**真实列表端点**，数据来自 `month_plan` / `day_plan` / `result` 表。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import DayPlan, Employee, MonthPlan
from ..domain.result_models import Result
from ..services import plan_execution_metric
from ..services.access_control import build_access_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/planning", tags=["planning"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 计划统计
#
# 响应结构：PlanningStats
# ---------------------------------------------------------------------------


@router.get("/stats")
async def get_planning_stats(
    month: str | None = Query(None, description="统计周期 YYYY-MM，缺省取当前月"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取计划执行统计概览。

    响应结构：PlanningStats

    ⚠️ **A7 起接真实统计**（此前恒返回四个 0）：

    | 字段 | 口径 |
    |---|---|
    | `pendingDayPlans` | 待审（待抽检）日计划数：`status=submitted` 且未记录抽检结论 |
    | `overduePendingDayPlans` | 逾期未审：`approval_due_at` 已过期且未抽检 |
    | `currentMonthResults` | 周期内成果总数（月成果按 `plan_month`，周成果按周起始月 —— **D6**） |
    | `closureRate` | 计划执行闭环率 = 已审批成果数 / 应提交条目数 |

    **数据范围**按访问上下文（ACL）解析：HR / 管理员看全公司，
    部门负责人看管理范围，主管看直属下属，普通员工只看自己。
    """
    context = build_access_context(db, current_user)
    stats = plan_execution_metric.build_planning_stats(db, context, period=month)
    return _ok(stats.as_dict())


# ---------------------------------------------------------------------------
# 列表端点（BUG-R6-01）
#
# 前端 `api/planning.ts` 的三个列表函数经代理后打到 `/v1/planning/*`，
# 后端此前**没有**对应端点 → 员工计划/成果列表页 404。
#
# 数据范围（与 `/stats` 一致）：
# - 普通员工：只看自己
# - 主管：自己 + 直属下属
# - 部门负责人：管理范围（所属部门及下级部门）
# - HR：全公司
# ---------------------------------------------------------------------------


def _scoped_owner_ids(context, db: Session) -> list[str] | None:
    """算出当前可见的员工 ID 集合；`None` 表示「不限制」（HR 看全量）。"""
    if context.has_permission("view_all_scores"):
        return None
    ids = {context.employee_id}
    ids |= set(context.direct_report_ids)
    if "department_leader" in context.roles:
        # 管理范围：部门及其下级部门里的在职员工
        from ..domain.models import EmployeeDepartment  # noqa: F401

        if context.managed_department_ids:
            rows = db.query(Employee.id).filter(
                Employee.department_id.in_(context.managed_department_ids),
                Employee.employment_status == "active",
            ).all()
            ids |= {r[0] for r in rows}
    return sorted(ids)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat()


@router.get("/month-plans")
async def list_month_plans(
    planMonth: str | None = Query(None, description="计划周期 YYYY-MM"),
    ownerId: str | None = Query(None, description="指定员工 ID（需在数据范围内）"),
    status: str | None = Query(None, description="计划状态筛选"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """月计划列表。

    响应结构：`MonthPlan[]`（前端 `planning.ts` 的 `MonthPlan` 接口）。
    """
    context = build_access_context(db, current_user)
    scope = _scoped_owner_ids(context, db)

    query = db.query(MonthPlan)
    if scope is not None:
        query = query.filter(MonthPlan.owner_id.in_(scope))
    if ownerId:
        # 指定员工时必须仍落在数据范围内
        if scope is not None and ownerId not in scope:
            return _ok([])
        query = query.filter(MonthPlan.owner_id == ownerId)
    if planMonth:
        query = query.filter(MonthPlan.plan_month == planMonth)
    if status:
        query = query.filter(MonthPlan.status == status)

    rows = query.order_by(MonthPlan.plan_month.desc(), MonthPlan.created_at.desc()).all()
    return _ok([_month_plan_dto(row) for row in rows])


def _month_plan_dto(row: MonthPlan) -> dict:
    """月计划 → 前端契约。

    前端 `MonthPlan.id` 声明为 `number`，但后端主键是字符串 UUID，
    这里返回字符串（前端只做 key 使用，不参与算术）。
    """
    return {
        "id": row.id,
        "title": row.title,
        "planMonth": row.plan_month,
        "status": (row.status or "").upper(),
        "ownerUserId": row.owner_id,
        "deptId": None,
        "completionRate": row.completion_rate,
        "versionNo": str(row.version_no),
        "submitAt": _iso(row.submitted_at),
        "approverId": None,
        "approveAt": _iso(row.approved_at),
        "approvalComment": row.approval_comment,
        "reviewStatus": None,
        "riskLevel": None,
        "reviewedAt": None,
        "resultStatus": row.result_status,
    }


@router.get("/day-plans")
async def list_day_plans(
    planDate: str | None = Query(None, description="计划日期 YYYY-MM-DD"),
    startDate: str | None = Query(None, description="起始日期（含）"),
    endDate: str | None = Query(None, description="结束日期（含）"),
    ownerId: str | None = Query(None, description="指定员工 ID（需在数据范围内）"),
    status: str | None = Query(None, description="计划状态筛选"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """日计划列表。

    响应结构：`DayPlan[]`（前端 `planning.ts` 的 `DayPlan` 接口）。
    """
    context = build_access_context(db, current_user)
    scope = _scoped_owner_ids(context, db)

    query = db.query(DayPlan)
    if scope is not None:
        query = query.filter(DayPlan.owner_id.in_(scope))
    if ownerId:
        if scope is not None and ownerId not in scope:
            return _ok([])
        query = query.filter(DayPlan.owner_id == ownerId)
    if planDate:
        query = query.filter(DayPlan.plan_date == planDate)
    if startDate:
        query = query.filter(DayPlan.plan_date >= date.fromisoformat(startDate))
    if endDate:
        query = query.filter(DayPlan.plan_date <= date.fromisoformat(endDate))
    if status:
        query = query.filter(DayPlan.status == status)

    rows = query.order_by(DayPlan.plan_date.desc(), DayPlan.created_at.desc()).all()
    return _ok([_day_plan_dto(row) for row in rows])


def _day_plan_dto(row: DayPlan) -> dict:
    return {
        "id": row.id,
        "title": row.content[:80] if row.content else "",
        "planDate": row.plan_date.isoformat() if row.plan_date else None,
        "content": row.content,
        "monthPlanId": None,
        "ownerUserId": row.owner_id,
        "deptId": None,
        "status": (row.status or "").upper(),
        "submitAt": _iso(row.submitted_at),
        "approverId": None,
        "approveAt": None,
        "approvalComment": None,
        "departmentReviewComment": row.department_comment,
        "reviewStatus": row.review_status,
        "riskLevel": row.risk_level,
        "reviewedAt": _iso(row.reviewed_at),
    }


@router.get("/results")
async def list_results(
    planType: str | None = Query(None, description="计划类型：MONTH / WEEK / TEMP"),
    ownerId: str | None = Query(None, description="指定员工 ID（需在数据范围内）"),
    status: str | None = Query(None, description="成果状态筛选"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """成果列表。

    响应结构：`ResultItem[]`（前端 `planning.ts` 的 `ResultItem` 接口）。
    """
    context = build_access_context(db, current_user)
    scope = _scoped_owner_ids(context, db)

    query = db.query(Result)
    if scope is not None:
        query = query.filter(Result.owner_id.in_(scope))
    if ownerId:
        if scope is not None and ownerId not in scope:
            return _ok([])
        query = query.filter(Result.owner_id == ownerId)
    if planType:
        query = query.filter(Result.plan_type == planType.upper())
    if status:
        query = query.filter(Result.status == status.upper())

    rows = query.order_by(Result.created_at.desc()).all()
    return _ok([_result_dto(row) for row in rows])


def _result_dto(row: Result) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "resultDate": row.result_date.isoformat() if row.result_date else None,
        "content": row.content or row.description,
        "planType": (row.plan_type or "MONTH").upper(),
        "planId": row.plan_id,
        "temporary": (row.plan_type or "").upper() == "TEMP",
        "temporaryReason": row.remark if (row.plan_type or "").upper() == "TEMP" else None,
        "ownerUserId": row.owner_id,
        "deptId": None,
        "status": (row.status or "").upper(),
        "submitAt": _iso(row.submitted_at),
        "confirmerId": row.approver_id,
        "confirmAt": _iso(row.approved_at),
        "confirmComment": row.approval_comment,
        "leaderSuggestion": None,
        "completionRate": row.completion_rate,
        "versionNo": str(row.version_no),
        "evidenceStatus": row.evidence_status,
    }
