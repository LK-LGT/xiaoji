"""📊 **领导 / 部门工作台数据聚合**（★ 2026-11-27 新增）。

⚠️ **为什么需要这个模块**
------------------------------------------------------------------
``routes/leader.py`` 的 ``/org-tree``、``/workbench``、``/employees``、
``/employees/{id}/work-items``、月计划详情原先**全是空壳**：
接口返回 ``[]`` / 空对象，前端页面却完整可用
⇒ 主管打开首页看到「当前没有直属员工」，点员工也看不到任何计划/成果。

本模块把这些端点接到**真实业务表**：月计划 / 周计划 / 日计划 / 成果。
路由层只负责算「可见范围」，本模块只负责取数与序列化。

⚠️ **范围必须由调用方传入**
本模块**不做权限判断** —— 传入什么 employee_ids，就只查这些人。
这样领导端（直属下属）与部门端（管理部门）可以复用同一套取数逻辑，
且不会因为「忘了过滤」而越权。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..domain.models import (
    DayPlan,
    Department,
    Employee,
    MonthPlan,
    WeekPlan,
)
from ..domain.result_models import Result


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value.isoformat()


def _month_start(plan_month: str) -> str | None:
    text = str(plan_month or "").strip()
    if len(text) >= 7 and text[4] == "-":
        return f"{text[:7]}-01"
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}-{text[4:]}-01"
    return None


def org_tree(db: Session, department_ids: Iterable[str]) -> list[dict[str, Any]]:
    """返回指定部门集合的组织树（只含集合内节点）。"""
    wanted = {str(item) for item in department_ids if item}
    if not wanted:
        return []
    rows = (
        db.execute(select(Department).where(Department.id.in_(wanted)))
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in rows}
    children: dict[str | None, list[Department]] = {}
    for row in rows:
        parent = row.parent_id if row.parent_id in by_id else None
        children.setdefault(parent, []).append(row)

    def build(node: Department) -> dict[str, Any]:
        return {
            "id": node.id,
            "label": node.name,
            "orgType": "DEPARTMENT",
            "children": [
                build(child)
                for child in sorted(children.get(node.id, []), key=lambda item: item.name)
            ],
        }

    return [build(node) for node in sorted(children.get(None, []), key=lambda item: item.name)]


def _department_names(db: Session, department_ids: Iterable[str]) -> dict[str, str]:
    ids = {str(item) for item in department_ids if item}
    if not ids:
        return {}
    rows = db.execute(
        select(Department.id, Department.name).where(Department.id.in_(ids))
    ).all()
    return {str(dept_id): name for dept_id, name in rows}


def employee_summaries(
    db: Session, employee_ids: Iterable[str]
) -> list[dict[str, Any]]:
    """批量构造 ``LeaderEmployeeSummary``（一次查询，避免 N+1）。"""
    ids = [str(item) for item in employee_ids if item]
    if not ids:
        return []

    employees = (
        db.execute(select(Employee).where(Employee.id.in_(ids)))
        .scalars()
        .all()
    )
    if not employees:
        return []

    counts: dict[str, dict[str, int]] = {
        employee_id: {"month": 0, "week": 0, "day": 0, "result": 0}
        for employee_id in ids
    }
    latest: dict[str, datetime] = {}

    def collect(model, key: str) -> None:
        rows = db.execute(
            select(model.owner_id, func.count(), func.max(model.updated_at))
            .where(model.owner_id.in_(ids))
            .group_by(model.owner_id)
        ).all()
        for owner_id, count, updated_at in rows:
            owner_id = str(owner_id)
            if owner_id not in counts:
                continue
            counts[owner_id][key] = int(count or 0)
            if updated_at is not None:
                current = latest.get(owner_id)
                if current is None or updated_at > current:
                    latest[owner_id] = updated_at

    collect(MonthPlan, "month")
    collect(WeekPlan, "week")
    collect(DayPlan, "day")
    collect(Result, "result")

    dept_names = _department_names(db, {employee.department_id for employee in employees})
    items: list[dict[str, Any]] = []
    for employee in sorted(employees, key=lambda item: (item.name or "", item.id)):
        bucket = counts.get(employee.id, {"month": 0, "week": 0, "day": 0, "result": 0})
        total = sum(bucket.values())
        items.append(
            {
                "ownerId": employee.id,
                "employeeNo": employee.login_account or employee.id,
                "employeeName": employee.name or "",
                "orgId": employee.department_id or "0",
                "orgName": dept_names.get(str(employee.department_id or ""), ""),
                "monthPlanCount": bucket["month"],
                "weekPlanCount": bucket["week"],
                "dayPlanCount": bucket["day"],
                "resultCount": bucket["result"],
                "totalWorkCount": total,
                "latestActivityAt": _iso(latest.get(employee.id)),
            }
        )
    return items


def employee_summary(db: Session, employee_id: str) -> dict[str, Any] | None:
    rows = employee_summaries(db, [employee_id])
    return rows[0] if rows else None


def _result_evidence_map(results: list[Result]) -> dict[tuple[str, str], str]:
    """(plan_type, plan_item_id) → evidenceStatus，取最新一条。"""
    mapping: dict[tuple[str, str], str] = {}
    for result in sorted(results, key=lambda item: item.updated_at or datetime.min):
        if result.plan_item_id:
            mapping[(str(result.plan_type), str(result.plan_item_id))] = (
                result.evidence_status or "NONE"
            )
    return mapping


def _build_rows(db: Session, employee: Employee) -> list[dict[str, Any]]:
    owner_id = employee.id
    rows: list[dict[str, Any]] = []

    month_plans = (
        db.execute(
            select(MonthPlan)
            .options(selectinload(MonthPlan.items))
            .where(MonthPlan.owner_id == owner_id)
        )
        .scalars()
        .all()
    )
    week_plans = (
        db.execute(
            select(WeekPlan)
            .options(selectinload(WeekPlan.items))
            .where(WeekPlan.owner_id == owner_id)
        )
        .scalars()
        .all()
    )
    day_plans = (
        db.execute(
            select(DayPlan)
            .options(selectinload(DayPlan.items))
            .where(DayPlan.owner_id == owner_id)
        )
        .scalars()
        .all()
    )
    results = (
        db.execute(select(Result).where(Result.owner_id == owner_id))
        .scalars()
        .all()
    )

    evidence = _result_evidence_map(results)
    month_titles = {plan.id: plan.title for plan in month_plans}
    week_titles = {plan.id: plan.title for plan in week_plans}
    day_titles = {plan.id: (plan.content or "").splitlines()[0][:80] for plan in day_plans}

    for plan in month_plans:
        base = {
            "workType": "MONTH_PLAN",
            "businessId": plan.id,
            "periodLabel": plan.plan_month or "",
            "startDate": _month_start(plan.plan_month),
            "endDate": _month_start(plan.plan_month),
            "relatedPlanType": "MONTH",
            "relatedPlanId": plan.id,
            "relatedPlanTitle": plan.title or "",
            "leaderComment": plan.approval_comment or "",
            "updatedAt": _iso(plan.updated_at),
        }
        if not plan.items:
            rows.append(
                {
                    **base,
                    "rowId": f"MONTH_PLAN:{plan.id}:PLAN",
                    "title": plan.title or plan.plan_month,
                    "content": "",
                    "deliverable": "",
                    "remark": "",
                    "quality": "",
                    "status": plan.status,
                    "completionRate": plan.completion_rate,
                    "evidenceStatus": "NONE",
                }
            )
            continue
        for item in plan.items:
            rows.append(
                {
                    **base,
                    "rowId": f"MONTH_PLAN:{plan.id}:{item.id}",
                    "itemId": item.id,
                    "taskType": item.task_type,
                    "title": item.task_name or plan.title,
                    "content": item.task_content or "",
                    "deliverable": item.deliverable or "",
                    "remark": item.progress or "",
                    "quality": item.quality or "",
                    "estimatedStartDate": _iso(item.estimated_start_date),
                    "estimatedEndDate": _iso(item.estimated_end_date),
                    # 日期筛选优先用任务自己的计划日期，而不是月容器月初。
                    "startDate": _iso(item.estimated_start_date) or base["startDate"],
                    "endDate": _iso(item.estimated_end_date) or base["endDate"],
                    "performanceWeight": item.performance_weight,
                    "estimatedValue": item.estimated_value,
                    "status": item.status or plan.status,
                    "completionRate": plan.completion_rate,
                    "evidenceStatus": evidence.get(("month", str(item.id)), "NONE"),
                }
            )

    for plan in week_plans:
        base = {
            "workType": "WEEK_PLAN",
            "businessId": plan.id,
            "periodLabel": f"{plan.week_start.isoformat()}~{plan.week_end.isoformat()}",
            "startDate": _iso(plan.week_start),
            "endDate": _iso(plan.week_end),
            "relatedPlanType": "WEEK",
            "relatedPlanId": plan.id,
            "relatedPlanTitle": plan.title or "",
            "leaderComment": plan.approval_comment or "",
            "updatedAt": _iso(plan.updated_at),
        }
        if not plan.items:
            rows.append(
                {
                    **base,
                    "rowId": f"WEEK_PLAN:{plan.id}:PLAN",
                    "title": plan.title or "",
                    "content": "",
                    "deliverable": "",
                    "remark": "",
                    "quality": "",
                    "status": plan.status,
                    "evidenceStatus": "NONE",
                }
            )
            continue
        for item in plan.items:
            rows.append(
                {
                    **base,
                    "rowId": f"WEEK_PLAN:{plan.id}:{item.id}",
                    "itemId": item.id,
                    "title": item.content or plan.title,
                    "content": item.content or "",
                    "deliverable": item.deliverable or "",
                    "remark": item.remark or "",
                    "quality": item.quality or "",
                    "estimatedStartDate": _iso(item.planned_finish_date),
                    "estimatedEndDate": _iso(item.planned_finish_date),
                    "startDate": _iso(item.planned_finish_date) or base["startDate"],
                    "endDate": _iso(item.planned_finish_date) or base["endDate"],
                    "status": item.completion_status or plan.status,
                    "evidenceStatus": evidence.get(("week", str(item.id)), "NONE"),
                }
            )

    for plan in day_plans:
        base = {
            "workType": "DAY_PLAN",
            "businessId": plan.id,
            "periodLabel": plan.plan_date.isoformat(),
            "startDate": _iso(plan.plan_date),
            "endDate": _iso(plan.plan_date),
            "relatedPlanType": "DAY",
            "relatedPlanId": plan.id,
            "relatedPlanTitle": (plan.content or "").splitlines()[0][:80],
            "leaderComment": plan.leader_comment or "",
            "updatedAt": _iso(plan.updated_at),
        }
        if not plan.items:
            rows.append(
                {
                    **base,
                    "rowId": f"DAY_PLAN:{plan.id}:PLAN",
                    "title": (plan.content or "").splitlines()[0][:80],
                    "content": plan.content or "",
                    "deliverable": "",
                    "remark": plan.remark or "",
                    "quality": "",
                    "status": plan.status,
                    "evidenceStatus": "NONE",
                }
            )
            continue
        for item in plan.items:
            rows.append(
                {
                    **base,
                    "rowId": f"DAY_PLAN:{plan.id}:{item.id}",
                    "itemId": item.id,
                    "title": item.content or "",
                    "content": item.content or "",
                    "deliverable": "",
                    "remark": item.remark or "",
                    "quality": "",
                    "status": plan.status,
                    "evidenceStatus": evidence.get(("day", str(item.id)), "NONE"),
                }
            )

    for result in results:
        if result.plan_type == "month":
            related_title = month_titles.get(result.plan_id, "")
        elif result.plan_type == "week":
            related_title = week_titles.get(result.plan_id, "")
        else:
            related_title = day_titles.get(result.plan_id, "")
        rows.append(
            {
                "rowId": f"RESULT:{result.id}",
                "workType": "RESULT",
                "businessId": result.id,
                "title": result.title or "",
                "content": result.content or result.description or "",
                "deliverable": result.deliverable or "",
                "remark": result.remark or "",
                "quality": "",
                "periodLabel": _iso(result.result_date) or "",
                "startDate": _iso(result.result_date),
                "endDate": _iso(result.result_date),
                "status": result.status,
                "completionRate": result.completion_rate,
                "evidenceStatus": result.evidence_status or "NONE",
                "relatedPlanType": result.plan_type,
                "relatedPlanId": result.plan_id,
                "relatedPlanTitle": related_title,
                "leaderComment": result.approval_comment or "",
                "updatedAt": _iso(result.updated_at),
            }
        )

    return rows


def work_items_page(
    db: Session,
    employee: Employee,
    *,
    work_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page_no: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """构造 ``LeaderEmployeeWorkPage``。"""
    rows = _build_rows(db, employee)

    wanted_type = (work_type or "").strip().upper()
    if wanted_type:
        rows = [row for row in rows if row["workType"] == wanted_type]

    if start_date:
        rows = [row for row in rows if (row.get("startDate") or "") >= start_date]
    if end_date:
        rows = [row for row in rows if (row.get("endDate") or row.get("startDate") or "") <= end_date]
    if status:
        needle = status.strip().lower()
        rows = [row for row in rows if str(row.get("status") or "").lower() == needle]
    if keyword:
        needle = keyword.strip().lower()
        fields = ("title", "content", "deliverable", "remark", "periodLabel", "relatedPlanTitle")
        rows = [
            row
            for row in rows
            if any(needle in str(row.get(field) or "").lower() for field in fields)
        ]

    rows.sort(
        key=lambda row: (row.get("updatedAt") or "", row.get("rowId") or ""),
        reverse=True,
    )
    total = len(rows)
    start = max(page_no - 1, 0) * page_size
    page_rows = rows[start : start + page_size]

    summary = employee_summary(db, employee.id) or {
        "ownerId": employee.id,
        "employeeNo": employee.login_account or employee.id,
        "employeeName": employee.name or "",
        "orgId": employee.department_id or "0",
        "orgName": "",
        "monthPlanCount": 0,
        "weekPlanCount": 0,
        "dayPlanCount": 0,
        "resultCount": 0,
        "totalWorkCount": 0,
        "latestActivityAt": None,
    }
    return {
        "employee": summary,
        "items": page_rows,
        "total": total,
        "pageNo": page_no,
        "pageSize": page_size,
    }


def month_plan_detail(
    db: Session, employee_id: str, plan_id: str
) -> dict[str, Any] | None:
    plan = db.get(MonthPlan, str(plan_id))
    if plan is None or str(plan.owner_id) != str(employee_id):
        return None
    return {
        "id": plan.id,
        "title": plan.title or "",
        "planMonth": plan.plan_month or "",
        "status": plan.status,
        "leaderComment": plan.approval_comment or "",
        "updatedAt": _iso(plan.updated_at),
        "tasks": [
            {
                "id": item.id,
                "sortNo": item.sort_no,
                "taskType": item.task_type,
                "taskName": item.task_name,
                "taskContent": item.task_content,
                "deliverable": item.deliverable,
                "quality": item.quality,
                "remark": item.progress or "",
                "estimatedStartDate": _iso(item.estimated_start_date),
                "estimatedEndDate": _iso(item.estimated_end_date),
                "performanceWeight": item.performance_weight,
                "estimatedValue": item.estimated_value,
                "status": item.status,
                "completionRate": plan.completion_rate,
            }
            for item in sorted(plan.items, key=lambda row: row.sort_no)
        ],
    }


def workbench_metrics(db: Session, employee_ids: Iterable[str]) -> list[dict[str, Any]]:
    """构造工作台指标卡（只算真实计数，不编造百分比）。"""
    ids = [str(item) for item in employee_ids if item]
    if not ids:
        return []

    def count(model, *conditions) -> int:
        stmt = select(func.count()).select_from(model).where(model.owner_id.in_(ids))
        for condition in conditions:
            stmt = stmt.where(condition)
        return int(db.execute(stmt).scalar_one() or 0)

    month_plans = count(MonthPlan)
    week_plans = count(WeekPlan)
    day_plans = count(DayPlan)
    results = count(Result)
    pending = (
        count(MonthPlan, MonthPlan.status.in_(["submitted", "SUBMITTED"]))
        + count(WeekPlan, WeekPlan.status.in_(["submitted", "SUBMITTED"]))
        + count(DayPlan, DayPlan.status.in_(["submitted", "SUBMITTED"]))
    )
    return [
        {"code": "teamSize", "label": "直属员工", "value": len(ids), "tone": "primary"},
        {"code": "monthPlans", "label": "月计划", "value": month_plans, "tone": "info"},
        {"code": "weekPlans", "label": "周计划", "value": week_plans, "tone": "info"},
        {"code": "dayPlans", "label": "日计划", "value": day_plans, "tone": "info"},
        {"code": "results", "label": "成果", "value": results, "tone": "success"},
        {"code": "pendingApprovals", "label": "待审批", "value": pending, "tone": "warning"},
    ]


__all__ = [
    "org_tree",
    "employee_summaries",
    "employee_summary",
    "work_items_page",
    "month_plan_detail",
    "workbench_metrics",
]
