"""📦 **领导端项目包聚合**（★ 2026-11-27 新增）。

⚠️ **为什么需要这个模块**
------------------------------------------------------------------
``routes/leader_suggestion.py`` 的项目日/周/成果包端点原先全部返回空：
列表 ``[]``、详情空对象、Excel 恒 404，而前端领导工作台有完整的三块页签
⇒ 主管看不到项目组当天/当周的提交进度。

本模块按**部门（项目组）**聚合真实的日计划 / 周计划 / 周成果，
并复用 ``plan_approval._owner_in_scope`` 做可见范围过滤。

⚠️ 只聚合**审批人可见范围内**的成员；范围为空 ⇒ 返回空列表。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domain.models import DayPlan, Department, Employee, WeekPlan
from ..domain.result_models import Result
from . import plan_approval

STATUS_LABELS = {
    "draft": "草稿",
    "submitted": "已提交",
    "pending": "待审批",
    "approved": "已审批",
    "rejected": "已驳回",
    "confirmed": "已确认",
    "not_submitted": "未提交",
    "NOT_SUBMITTED": "未提交",
    "DRAFT": "草稿",
    "PENDING": "待审批",
    "APPROVED": "已审批",
    "REJECTED": "已驳回",
    "SUBMITTED": "已提交",
    "CONFIRMED": "已确认",
}


def _label(status: str | None) -> str:
    return STATUS_LABELS.get(str(status or ""), str(status or ""))


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_date(text: str | None, default: date) -> date:
    if not text:
        return default
    return date.fromisoformat(str(text)[:10])


def _week_bounds(week_start: str | None) -> tuple[date, date, str]:
    if week_start:
        start = _parse_date(week_start, date.today())
    else:
        today = date.today()
        start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6), start.isoformat()


def _department_map(db: Session, ids: Iterable[str]) -> dict[str, str]:
    wanted = {str(item) for item in ids if item}
    if not wanted:
        return {}
    rows = db.execute(select(Department.id, Department.name).where(Department.id.in_(wanted))).all()
    return {str(dept_id): name for dept_id, name in rows}


def _employee_no(employee: Employee) -> str:
    return employee.login_account or employee.id


def _visible_plans(db: Session, plans: list[Any], scope: plan_approval.ApprovalScope) -> list[Any]:
    visible: list[Any] = []
    for plan in plans:
        owner = plan.owner or db.get(Employee, plan.owner_id)
        if owner is not None and plan_approval._owner_in_scope(db, str(plan.owner_id), scope, owner):
            visible.append(plan)
    return visible


def _group_by_department(
    db: Session, plans: list[Any], scope_org_id: str | None = None
) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for plan in plans:
        owner = plan.owner or db.get(Employee, plan.owner_id)
        if owner is None:
            continue
        dept_id = str(owner.department_id or "0")
        if scope_org_id and dept_id != str(scope_org_id):
            continue
        grouped.setdefault(dept_id, []).append(plan)
    return grouped


def _package_status(all_approved: bool, has_members: bool) -> str:
    if not has_members:
        return "EMPTY"
    return "OPEN" if all_approved else "COLLECTING"


# ---------------------------------------------------------------------------
# 日计划包
# ---------------------------------------------------------------------------


def day_packages(
    db: Session,
    scope: plan_approval.ApprovalScope,
    *,
    plan_date: str | None = None,
    scope_org_id: str | None = None,
) -> list[dict[str, Any]]:
    target = _parse_date(plan_date, date.today())
    plans = (
        db.execute(
            select(DayPlan)
            .options(selectinload(DayPlan.items))
            .where(DayPlan.plan_date == target)
            .order_by(DayPlan.updated_at.desc())
        )
        .scalars()
        .all()
    )
    visible = _visible_plans(db, plans, scope)
    grouped = _group_by_department(db, visible, scope_org_id)
    names = _department_map(db, grouped.keys())

    packages: list[dict[str, Any]] = []
    for dept_id, dept_plans in sorted(grouped.items()):
        pending = [plan for plan in dept_plans if str(plan.status or "").lower() != "approved"]
        approved = [plan for plan in dept_plans if str(plan.status or "").lower() == "approved"]
        packages.append(
            {
                "groupId": dept_id,
                "groupName": names.get(dept_id, "未归属部门"),
                "planDate": target.isoformat(),
                "submittedCount": sum(
                    1 for plan in dept_plans if str(plan.status or "").lower() != "draft"
                ),
                "totalMemberCount": len(dept_plans),
                "status": _package_status(not pending, bool(dept_plans)),
                "contentAvailable": bool(dept_plans) and not pending,
                "pendingMembers": [
                    {
                        "ownerId": plan.owner_id,
                        "employeeNo": _employee_no(plan.owner) if plan.owner else plan.owner_id,
                        "employeeName": plan.owner.name if plan.owner else "未知员工",
                        "positionName": plan.owner.position if plan.owner else "",
                        "planStatus": plan.status,
                        "statusLabel": _label(plan.status),
                        "submittedAt": _iso(plan.submitted_at),
                    }
                    for plan in pending
                ],
                "completedAt": _iso(max((plan.reviewed_at for plan in approved if plan.reviewed_at), default=None)),
            }
        )
    return packages


def day_package_detail(
    db: Session,
    scope: plan_approval.ApprovalScope,
    group_id: str,
    *,
    plan_date: str | None = None,
) -> dict[str, Any] | None:
    packages = day_packages(db, scope, plan_date=plan_date, scope_org_id=group_id)
    if not packages:
        return None
    target = _parse_date(plan_date, date.today())
    plans = _visible_plans(
        db,
        db.execute(
            select(DayPlan)
            .options(selectinload(DayPlan.items))
            .where(DayPlan.plan_date == target)
        )
        .scalars()
        .all(),
        scope,
    )
    rows = []
    for plan in plans:
        owner = plan.owner or db.get(Employee, plan.owner_id)
        if owner is None or str(owner.department_id or "0") != str(group_id):
            continue
        content = plan.content or "\n".join(item.content for item in plan.items)
        rows.append(
            {
                "ownerId": plan.owner_id,
                "employeeNo": _employee_no(owner),
                "employeeName": owner.name,
                "workContent": content,
            }
        )
    return {"packageInfo": packages[0], "rows": rows}


# ---------------------------------------------------------------------------
# 周计划包
# ---------------------------------------------------------------------------


def _week_member_status(status: str | None) -> str:
    text = str(status or "").upper()
    return {"SUBMITTED": "PENDING"}.get(text, text or "DRAFT")


def week_packages(
    db: Session,
    scope: plan_approval.ApprovalScope,
    *,
    week_start: str | None = None,
    scope_org_id: str | None = None,
) -> list[dict[str, Any]]:
    start, end, _ = _week_bounds(week_start)
    plans = (
        db.execute(
            select(WeekPlan)
            .options(selectinload(WeekPlan.items))
            .where(WeekPlan.week_start == start)
            .order_by(WeekPlan.updated_at.desc())
        )
        .scalars()
        .all()
    )
    visible = _visible_plans(db, plans, scope)
    grouped = _group_by_department(db, visible, scope_org_id)
    names = _department_map(db, grouped.keys())

    packages: list[dict[str, Any]] = []
    for dept_id, dept_plans in sorted(grouped.items()):
        approved = [plan for plan in dept_plans if str(plan.status or "").upper() == "APPROVED"]
        pending = [plan for plan in dept_plans if str(plan.status or "").upper() != "APPROVED"]
        packages.append(
            {
                "groupId": dept_id,
                "groupName": names.get(dept_id, "未归属部门"),
                "weekStart": start.isoformat(),
                "weekEnd": end.isoformat(),
                "approvedCount": len(approved),
                "totalMemberCount": len(dept_plans),
                "status": _package_status(not pending, bool(dept_plans)),
                "contentAvailable": bool(dept_plans) and not pending,
                "pendingMembers": [
                    {
                        "ownerId": plan.owner_id,
                        "employeeNo": _employee_no(plan.owner) if plan.owner else plan.owner_id,
                        "employeeName": plan.owner.name if plan.owner else "未知员工",
                        "positionName": plan.owner.position if plan.owner else "",
                        "planStatus": _week_member_status(plan.status),
                        "statusLabel": _label(plan.status),
                        "approvedAt": _iso(plan.approve_at),
                    }
                    for plan in pending
                ],
                "completedAt": _iso(max((plan.approve_at for plan in approved if plan.approve_at), default=None)),
            }
        )
    return packages


def week_package_detail(
    db: Session,
    scope: plan_approval.ApprovalScope,
    group_id: str,
    *,
    week_start: str | None = None,
) -> dict[str, Any] | None:
    packages = week_packages(db, scope, week_start=week_start, scope_org_id=group_id)
    if not packages:
        return None
    start, _, _ = _week_bounds(week_start)
    plans = _visible_plans(
        db,
        db.execute(
            select(WeekPlan)
            .options(selectinload(WeekPlan.items))
            .where(WeekPlan.week_start == start)
        )
        .scalars()
        .all(),
        scope,
    )
    rows = []
    for plan in plans:
        owner = plan.owner or db.get(Employee, plan.owner_id)
        if owner is None or str(owner.department_id or "0") != str(group_id):
            continue
        items = plan.items or []
        if not items:
            rows.append(
                {
                    "ownerId": plan.owner_id,
                    "employeeNo": _employee_no(owner),
                    "employeeName": owner.name,
                    "workContent": plan.title or "",
                    "completionStatus": plan.status,
                    "deliverable": "",
                    "quality": "",
                }
            )
            continue
        for item in items:
            rows.append(
                {
                    "ownerId": plan.owner_id,
                    "employeeNo": _employee_no(owner),
                    "employeeName": owner.name,
                    "workContent": item.content,
                    "completionStatus": item.completion_status or plan.status,
                    "deliverable": item.deliverable or "",
                    "quality": item.quality or "",
                }
            )
    return {"packageInfo": packages[0], "rows": rows}


# ---------------------------------------------------------------------------
# 周成果包
# ---------------------------------------------------------------------------


def week_result_packages(
    db: Session,
    scope: plan_approval.ApprovalScope,
    *,
    week_start: str | None = None,
    scope_org_id: str | None = None,
) -> list[dict[str, Any]]:
    start, end, _ = _week_bounds(week_start)
    plans = (
        db.execute(select(WeekPlan).where(WeekPlan.week_start == start))
        .scalars()
        .all()
    )
    visible = _visible_plans(db, plans, scope)
    grouped = _group_by_department(db, visible, scope_org_id)
    names = _department_map(db, grouped.keys())
    plan_ids = [plan.id for plan in visible]
    results = (
        db.execute(
            select(Result).where(
                Result.plan_type == "week",
                Result.plan_id.in_(plan_ids or ["__none__"]),
            )
        )
        .scalars()
        .all()
    )
    by_owner: dict[str, list[Result]] = {}
    for result in results:
        by_owner.setdefault(str(result.owner_id), []).append(result)

    packages: list[dict[str, Any]] = []
    for dept_id, dept_plans in sorted(grouped.items()):
        members = [plan.owner for plan in dept_plans if plan.owner is not None]
        approved = [
            member for member in members
            if by_owner.get(str(member.id)) and all(
                str(r.status).lower() == "confirmed" for r in by_owner[str(member.id)]
            )
        ]
        pending = [member for member in members if member not in approved]
        packages.append(
            {
                "groupId": dept_id,
                "groupName": names.get(dept_id, "未归属部门"),
                "weekStart": start.isoformat(),
                "weekEnd": end.isoformat(),
                "approvedCount": len(approved),
                "totalMemberCount": len(members),
                "status": _package_status(not pending, bool(members)),
                "contentAvailable": bool(members) and not pending,
                "pendingMembers": [
                    {
                        "ownerId": member.id,
                        "employeeNo": _employee_no(member),
                        "employeeName": member.name,
                        "positionName": member.position or "",
                        "resultStatus": (
                            str(by_owner[str(member.id)][0].status).upper()
                            if by_owner.get(str(member.id))
                            else "NOT_SUBMITTED"
                        ),
                        "statusLabel": _label(
                            str(by_owner[str(member.id)][0].status)
                            if by_owner.get(str(member.id))
                            else "NOT_SUBMITTED"
                        ),
                        "approvedAt": _iso(
                            max(
                                (r.approved_at for r in by_owner.get(str(member.id), []) if r.approved_at),
                                default=None,
                            )
                        ),
                    }
                    for member in pending
                ],
                "completedAt": _iso(
                    max(
                        (r.approved_at for r in results if r.approved_at),
                        default=None,
                    )
                ),
            }
        )
    return packages


def week_result_package_detail(
    db: Session,
    scope: plan_approval.ApprovalScope,
    group_id: str,
    *,
    week_start: str | None = None,
) -> dict[str, Any] | None:
    packages = week_result_packages(
        db, scope, week_start=week_start, scope_org_id=group_id
    )
    if not packages:
        return None
    start, _, _ = _week_bounds(week_start)
    plans = _visible_plans(
        db,
        db.execute(select(WeekPlan).where(WeekPlan.week_start == start)).scalars().all(),
        scope,
    )
    plan_ids = [plan.id for plan in plans]
    results = (
        db.execute(
            select(Result).where(
                Result.plan_type == "week",
                Result.plan_id.in_(plan_ids or ["__none__"]),
            )
        )
        .scalars()
        .all()
    )
    rows = []
    for result in results:
        owner = db.get(Employee, result.owner_id)
        if owner is None or str(owner.department_id or "0") != str(group_id):
            continue
        rows.append(
            {
                "ownerId": result.owner_id,
                "employeeNo": _employee_no(owner),
                "employeeName": owner.name,
                "workContent": result.title or result.content,
                "completionStatus": result.completion_status or result.status,
                "deliverable": result.deliverable or "",
                "remark": result.remark or "",
            }
        )
    return {"packageInfo": packages[0], "rows": rows}


# ---------------------------------------------------------------------------
# CSV（前端按 Blob 下载；扩展名用 .csv，Excel 可直接打开）
# ---------------------------------------------------------------------------


def to_csv(rows: list[dict[str, Any]], headers: list[tuple[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([label for _, label in headers])
    for row in rows:
        writer.writerow([row.get(key, "") or "" for key, _ in headers])
    return bytes([239, 187, 191]) + output.getvalue().encode("utf-8")


__all__ = [
    "day_packages",
    "day_package_detail",
    "week_packages",
    "week_package_detail",
    "week_result_packages",
    "week_result_package_detail",
    "to_csv",
]
