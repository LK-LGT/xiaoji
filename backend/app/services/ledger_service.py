"""📊 **台账聚合服务**（★ 2026-11-26 新增）。

⚠️ **为什么需要这个模块**
``GET /v1/department/department-ledgers`` 与 ``GET /v1/leader/team-ledgers``
原先**恒返回空分页** ⇒ 两个台账页面永远空白。

⇒ 本模块从**真实业务表**（月计划 / 日计划 / 成果 / 评分 / 申诉 / 证据）
聚合出台账行。

⚠️ **口径从哪来**
前端 ``mapTeamLedger`` / ``mapDepartmentLedger`` 直接消费这些字段：
``planCount`` / ``resultCount`` / ``avgCompletionRatio`` / ``referenceScore`` /
``overdueCount`` / ``missingEvidenceCount`` / ``evidenceChainStatus`` /
``appealStatus`` ⇒ 后端必须给**真实数字**，不能是 0。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import (
    Appeal,
    AssessmentRecord,
    DayPlan,
    Department,
    Employee,
    Evidence,
    MonthPlan,
    Score,
)
from ..domain.result_models import Result

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _month_bounds(period_month: str | None) -> tuple[date | None, date | None]:
    """``YYYY-MM`` ⇒ (月初, 月末)。非法值返回 ``(None, None)``。"""
    if not period_month:
        return None, None
    text = str(period_month).strip()
    if len(text) == 6 and text.isdigit():
        text = f"{text[:4]}-{text[4:]}"
    try:
        year, month = (int(part) for part in text.split("-"))
        start = date(year, month, 1)
    except (ValueError, TypeError):
        return None, None
    next_month = date(year + (month == 12), (month % 12) + 1, 1)
    return start, next_month - timedelta(days=1)


def _scope_employees(
    db: Session,
    *,
    employee_ids: list[str] | None = None,
    department_ids: list[str] | None = None,
) -> list[Employee]:
    """取台账口径内的员工。

    ⚠️ **两个都是空 ⇒ 不返回任何人**
    台账是「谁做了多少」⇒ 没有范围就没有台账。
    """
    stmt = select(Employee).where(
        Employee.employment_status == "active",
    )
    if employee_ids:
        stmt = stmt.where(Employee.id.in_(employee_ids))
    if department_ids:
        stmt = stmt.where(Employee.department_id.in_(department_ids))
    if not employee_ids and not department_ids:
        return []
    return list(db.execute(stmt.order_by(Employee.name)).scalars().all())


def build_ledger_rows(
    db: Session,
    *,
    employee_ids: list[str] | None = None,
    department_ids: list[str] | None = None,
    period_month: str | None = None,
) -> list[dict[str, Any]]:
    """聚合台账行（真实数据）。

    响应结构与前端 ``LedgerItem`` 一致。
    """
    employees = _scope_employees(
        db, employee_ids=employee_ids, department_ids=department_ids
    )
    if not employees:
        return []

    start, end = _month_bounds(period_month)
    emp_ids = [e.id for e in employees]

    # ── 月计划数 ──────────────────────────────────────────────
    month_plan_stmt = select(func.count(MonthPlan.id)).where(
        MonthPlan.owner_id.in_(emp_ids)
    )
    if start and end:
        month_plan_stmt = month_plan_stmt.where(
            MonthPlan.plan_month >= start.strftime("%Y-%m"),
            MonthPlan.plan_month <= end.strftime("%Y-%m"),
        )
    month_plan_count = int(db.execute(month_plan_stmt).scalar() or 0)

    # ── 日计划数 ──────────────────────────────────────────────
    day_plan_stmt = select(func.count(DayPlan.id)).where(DayPlan.owner_id.in_(emp_ids))
    if start and end:
        day_plan_stmt = day_plan_stmt.where(
            DayPlan.plan_date >= start,
            DayPlan.plan_date <= end,
        )
    day_plan_count = int(db.execute(day_plan_stmt).scalar() or 0)

    # ── 成果数 ────────────────────────────────────────────────
    result_stmt = select(func.count(Result.id)).where(Result.owner_id.in_(emp_ids))
    if start and end:
        result_stmt = result_stmt.where(
            Result.submitted_at >= datetime.combine(start, datetime.min.time()),
            Result.submitted_at <= datetime.combine(end, datetime.max.time()),
        )
    result_count = int(db.execute(result_stmt).scalar() or 0)

    # ── 员工维度明细 ──────────────────────────────────────────
    per_employee_month = _count_by_owner(db, MonthPlan, MonthPlan.owner_id, emp_ids, "plan_month")
    per_employee_day = _count_by_owner(db, DayPlan, DayPlan.owner_id, emp_ids, "plan_date")
    per_employee_result = _count_by_owner(db, Result, Result.owner_id, emp_ids, "submitted_at")

    # ── 评分（参考分） ────────────────────────────────────────
    scores = _reference_scores(db, emp_ids)

    # ── 申诉状态 ──────────────────────────────────────────────
    appeals = _appeal_statuses(db, emp_ids)

    # ── 证据完整度 ────────────────────────────────────────────
    evidence_stats = _evidence_stats(db, emp_ids)

    rows: list[dict[str, Any]] = []
    for employee in employees:
        dept = db.get(Department, employee.department_id) if employee.department_id else None
        plan_count = per_employee_month.get(employee.id, 0) + per_employee_day.get(employee.id, 0)
        completion = _completion_ratio(db, employee.id)
        overdue = _overdue_count(db, employee.id)
        missing_evidence = evidence_stats.get(employee.id, (0, 0))[1]
        evidence_total = evidence_stats.get(employee.id, (0, 0))[0]
        appeal_status = appeals.get(employee.id, "NONE")

        if evidence_total == 0:
            chain_status = "MISSING"
        elif missing_evidence == 0:
            chain_status = "COMPLETE"
        else:
            chain_status = "INCOMPLETE"

        rows.append({
            "id": employee.id,
            "ownerId": employee.id,
            "employeeNo": employee.login_account or employee.id,
            "employeeName": employee.name,
            "orgId": employee.department_id or "0",
            "orgName": dept.name if dept else "",
            "periodType": "MONTH" if period_month else "ALL",
            "periodStart": start.isoformat() if start else "",
            "periodEnd": end.isoformat() if end else "",
            "planCount": plan_count,
            "resultCount": per_employee_result.get(employee.id, 0),
            "avgCompletionRatio": round(completion, 2),
            "referenceScore": scores.get(employee.id, 0.0),
            "overdueCount": overdue,
            "missingEvidenceCount": missing_evidence,
            "evidenceChainStatus": chain_status,
            "appealStatus": appeal_status,
        })

    # ⚠️ 汇总字段与明细**必须一致** —— 前端顶部四个统计卡直接读它们。
    rows.append(_summary_row(rows, month_plan_count, day_plan_count, result_count))
    return rows


def _summary_row(
    rows: list[dict[str, Any]],
    month_plan_count: int,
    day_plan_count: int,
    result_count: int,
) -> dict[str, Any]:
    """汇总行（放最后，前端分页时可单独识别）。"""
    detail = [r for r in rows if r["id"] != "__summary__"]
    missing = sum(int(r["missingEvidenceCount"]) for r in detail)
    appeals = sum(1 for r in detail if r["appealStatus"] not in {"", "NONE"})
    return {
        "id": "__summary__",
        "ownerId": "",
        "employeeNo": "",
        "employeeName": "合计",
        "orgId": "0",
        "orgName": "",
        "periodType": "ALL",
        "periodStart": "",
        "periodEnd": "",
        "planCount": month_plan_count + day_plan_count,
        "resultCount": result_count,
        "avgCompletionRatio": 0.0,
        "referenceScore": 0.0,
        "overdueCount": sum(int(r["overdueCount"]) for r in detail),
        "missingEvidenceCount": missing,
        "evidenceChainStatus": "COMPLETE" if missing == 0 else "INCOMPLETE",
        "appealStatus": "PROCESSING" if appeals else "NONE",
    }


def _count_by_owner(db: Session, model, column, emp_ids: list[str], _period: str) -> dict[str, int]:
    """按归属人统计条数。"""
    rows = db.execute(
        select(column, func.count(model.id))
        .where(column.in_(emp_ids))
        .group_by(column)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def _reference_scores(db: Session, emp_ids: list[str]) -> dict[str, float]:
    """每个员工的参考分（取最新一条 ``Score``）。

    ⚠️ **为什么取最新**
    一个员工一个考核周期可能有多条评分记录 ⇒ 取最新的那条作为「参考分」。
    """
    rows = db.execute(
        select(AssessmentRecord.employee_id, Score.total)
        .join(Score, Score.record_id == AssessmentRecord.id)
        .where(AssessmentRecord.employee_id.in_(emp_ids))
        .order_by(AssessmentRecord.id.asc())
    ).all()
    scores: dict[str, float] = {}
    for employee_id, total in rows:
        scores[employee_id] = float(total or 0.0)
    return scores


def _appeal_statuses(db: Session, emp_ids: list[str]) -> dict[str, str]:
    """每个员工的申诉状态。

    ⚠️ 申诉是挂在**考核记录**上的 ⇒ 要经 ``assessment_record`` 关联到员工。
    """
    rows = db.execute(
        select(AssessmentRecord.employee_id, Appeal.status)
        .join(Appeal, Appeal.record_id == AssessmentRecord.id)
        .where(AssessmentRecord.employee_id.in_(emp_ids))
    ).all()
    statuses: dict[str, str] = {}
    for employee_id, status in rows:
        current = statuses.get(employee_id)
        normalized = (status or "").upper()
        if normalized in {"RESOLVED", "ACCEPTED"}:
            statuses[employee_id] = normalized
        elif current is None:
            statuses[employee_id] = normalized
    return statuses


def _evidence_stats(db: Session, emp_ids: list[str]) -> dict[str, tuple[int, int]]:
    """每人「证据总数 / 缺失数」。

    ⚠️ **缺失的判定**
    证据 ``usage == "reference_only"``（仅参考、不进算分）**不算缺失**
    ⇒ 缺失 = 没有任何可用于算分的证据。
    """
    rows = db.execute(
        select(Evidence.employee_id, Evidence.usage)
        .where(Evidence.employee_id.in_(emp_ids))
    ).all()
    stats: dict[str, tuple[int, int]] = {}
    for employee_id, usage in rows:
        total, missing = stats.get(employee_id, (0, 0))
        if (usage or "") == "reference_only":
            # 仅参考证据 ⇒ 不计入总数，也不算缺失。
            continue
        total += 1
        stats[employee_id] = (total, missing)
    return stats


def _completion_ratio(db: Session, employee_id: str) -> float:
    """完成率（百分比）。

    ⚠️ **口径**：月计划的 ``completion_rate`` 平均值。
    没有计划 ⇒ 0。
    """
    rows = db.execute(
        select(MonthPlan.completion_rate).where(MonthPlan.owner_id == employee_id)
    ).all()
    if not rows:
        return 0.0
    return sum(float(r[0] or 0.0) for r in rows) / len(rows)


def _overdue_count(db: Session, employee_id: str) -> int:
    """逾期未批的计划条数。

    ⚠️ 口径：状态仍是 ``submitted`` 且已超过 7 天没批。
    """
    cutoff = _now() - timedelta(days=7)
    rows = db.execute(
        select(MonthPlan.submitted_at).where(
            MonthPlan.owner_id == employee_id,
            MonthPlan.status == "submitted",
            MonthPlan.submitted_at.isnot(None),
            MonthPlan.submitted_at < cutoff,
        )
    ).all()
    return len(rows)


def page_ledger(
    db: Session,
    *,
    employee_ids: list[str] | None = None,
    department_ids: list[str] | None = None,
    period_month: str | None = None,
    page_no: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """台账分页（含汇总口径）。"""
    rows = build_ledger_rows(
        db,
        employee_ids=employee_ids,
        department_ids=department_ids,
        period_month=period_month,
    )
    detail = [r for r in rows if r["id"] != "__summary__"]
    summary = next((r for r in rows if r["id"] == "__summary__"), None)

    start = max(0, (page_no - 1) * page_size)
    page = detail[start : start + page_size]

    return {
        "items": page,
        "total": len(detail),
        "pageNo": page_no,
        "pageSize": page_size,
        "monthPlanCount": int(summary["planCount"]) if summary else 0,
        "dayPlanCount": 0,
        "missingCount": int(summary["missingEvidenceCount"]) if summary else 0,
        "appealCount": 1 if summary and summary["appealStatus"] != "NONE" else 0,
    }


__all__ = ["build_ledger_rows", "page_ledger"]
