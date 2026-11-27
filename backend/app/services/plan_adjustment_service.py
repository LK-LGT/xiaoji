"""🔁 **计划调整服务**（★ 2026-11-26 新增）。

⚠️ **为什么需要这个模块**
计划调整这条链路原先**三段全是假的**：

- ``POST /v1/employee/plan-adjustments`` → 返回 ``{"id": 0, "adjustmentNo": ""}``
  （员工点了「申请调整」，什么都没发生）；
- ``GET  /v1/leader/plan-adjustments``    → 恒返回 ``[]``
  （领导端永远看不到任何申请）；
- ``POST /v1/leader/plan-adjustments/{id}/process`` → 只回一个字符串
  （领导点了「同意暂停」，**状态没变**）。

⇒ 前端「计划调整」页是完整可用的 UI，**但整条业务链是断的**。

⚠️ **单号口径**
``ADJ-YYYYMM-0001`` —— 月份 + 4 位顺序号。
⚠️ 顺序号用**独立序列表**（``plan_adjustment_seq``）递增，
不用 ``count()`` ⇒ 并发写入不会撞号。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.config_models import PlanAdjustmentNoRow, PlanAdjustmentRow

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _next_adjustment_no(db: Session, when: datetime | None = None) -> str:
    """生成下一个调整单号（``ADJ-YYYYMM-0001``）。"""
    moment = when or _now()
    period = moment.strftime("%Y%m")
    row = db.execute(
        select(PlanAdjustmentNoRow).where(PlanAdjustmentNoRow.period == period)
    ).scalar_one_or_none()
    if row is None:
        row = PlanAdjustmentNoRow(period=period, last_value=0)
        db.add(row)
        db.flush()
    row.last_value = int(row.last_value or 0) + 1
    db.flush()
    return f"ADJ-{period}-{row.last_value:04d}"


def serialize_adjustment(
    row: PlanAdjustmentRow,
    *,
    employee_name: str = "",
    operator_name: str = "",
) -> dict[str, Any]:
    """计划调整 ⇒ 前端 ``PlanAdjustmentItem`` 契约。"""
    return {
        "id": row.id,
        "adjustmentNo": row.adjustment_no or "",
        "originalPlanType": row.original_plan_type or "",
        "originalPlanId": row.original_plan_id or "",
        "originalPlanNo": row.original_plan_no or "",
        "originalWorkContent": row.original_work_content or "",
        "newPlanType": row.new_plan_type or "",
        "newPlanId": row.new_plan_id or "",
        "newPlanNo": row.new_plan_no or "",
        "ownerId": row.owner_id or "",
        "employeeName": employee_name,
        "adjustmentType": row.adjustment_type or "",
        "reason": row.reason or "",
        "impactText": row.impact_text or "",
        "operationComment": row.operation_comment or "",
        "status": row.status,
        "keepEvidenceChain": bool(row.keep_evidence_chain),
        "operatorName": operator_name,
        "operatedAt": row.operated_at.isoformat() if row.operated_at else None,
    }


def create_adjustment(
    db: Session,
    payload: dict[str, Any],
    *,
    owner_id: str,
) -> dict[str, Any]:
    """员工发起计划调整（真落库）。

    ⚠️ **原计划必须真实存在**
    否则会攒出一堆「指向不存在计划的调整申请」，
    领导处理时根本无从下手 ⇒ 这里校验并在找不到时抛 ``LookupError``。
    """
    plan_type = str(payload.get("planType") or "MONTH").strip().upper()
    plan_id = str(payload.get("planId") or "").strip()
    adjustment_type = str(payload.get("adjustmentType") or "PAUSE").strip().upper()
    reason = str(payload.get("reason") or "").strip()

    if not plan_id:
        raise ValueError("请选择要调整的计划")
    if adjustment_type not in {"PAUSE", "CANCEL"}:
        raise ValueError(f"未知调整类型：{adjustment_type}")
    if not reason:
        raise ValueError("请填写调整原因")

    plan_no, work_content, plan_owner_id = _resolve_plan(db, plan_type, plan_id)
    # ⚠️ 只能调整**自己的**计划 —— 否则员工可以借他人计划发起暂停/撤销。
    if str(plan_owner_id or "") != str(owner_id):
        raise ValueError("只能调整自己的计划")

    row = PlanAdjustmentRow(
        adjustment_no="",
        original_plan_type=plan_type,
        original_plan_id=plan_id,
        original_plan_no=plan_no,
        original_work_content=work_content,
        owner_id=owner_id,
        adjustment_type=adjustment_type,
        reason=reason,
        impact_text=str(payload.get("impactText") or "").strip(),
        status="PENDING",
        keep_evidence_chain=True,
    )
    db.add(row)
    db.flush()
    # ⚠️ 单号在 flush 之后生成 —— 序列表递增需要独立事务位置。
    row.adjustment_no = _next_adjustment_no(db)
    db.commit()
    db.refresh(row)
    return serialize_adjustment(row)


def _resolve_plan(db: Session, plan_type: str, plan_id: str) -> tuple[str, str, str]:
    """取原计划的编号、工作内容与归属人。找不到抛 ``LookupError``。"""
    from ..domain.models import DayPlan, MonthPlan, WeekPlan

    if plan_type == "MONTH":
        plan = db.get(MonthPlan, plan_id)
        if plan is None:
            raise LookupError("月计划不存在")
        items = sorted(plan.items, key=lambda item: item.sort_no)
        content = items[0].task_name if items else plan.title
        return plan.id, content, str(plan.owner_id or "")
    if plan_type == "WEEK":
        plan = db.get(WeekPlan, plan_id)
        if plan is None:
            raise LookupError("周计划不存在")
        items = sorted(plan.items, key=lambda item: item.sort_no)
        content = items[0].content if items else plan.title
        return plan.id, content, str(plan.owner_id or "")
    plan = db.get(DayPlan, plan_id)
    if plan is None:
        raise LookupError("日计划不存在")
    return plan.id, plan.content, str(plan.owner_id or "")


def list_adjustments(
    db: Session,
    *,
    owner_ids: list[str] | None = None,
    scope_org_id: str | None = None,
    status: str | None = None,
    period_month: str | None = None,
) -> list[dict[str, Any]]:
    """计划调整列表（真实数据）。"""
    from ..domain.models import Employee

    stmt = select(PlanAdjustmentRow)
    if owner_ids is not None:
        stmt = stmt.where(PlanAdjustmentRow.owner_id.in_(owner_ids or ["__none__"]))
    if status:
        stmt = stmt.where(PlanAdjustmentRow.status == status)
    if period_month:
        # ⚠️ 按创建月份过滤（``created_at`` 在 SQLite 里是字符串 ⇒ 用前缀匹配）。
        stmt = stmt.where(PlanAdjustmentRow.created_at.like(f"{period_month}%"))

    rows = db.execute(stmt.order_by(PlanAdjustmentRow.created_at.desc())).scalars().all()

    if scope_org_id:
        # ⚠️ 部门范围过滤 —— 先取该部门的员工，再按人过滤。
        member_ids = [
            row[0]
            for row in db.execute(
                select(Employee.id).where(Employee.department_id == scope_org_id)
            ).all()
        ]
        rows = [r for r in rows if r.owner_id in member_ids]

    employees = {
        row[0]: row[1]
        for row in db.execute(
            select(Employee.id, Employee.name).where(
                Employee.id.in_([r.owner_id for r in rows] or ["__none__"])
            )
        ).all()
    }
    operators = {
        row[0]: row[1]
        for row in db.execute(
            select(Employee.id, Employee.name).where(
                Employee.id.in_([r.operator_id for r in rows if r.operator_id] or ["__none__"])
            )
        ).all()
    }
    return [
        serialize_adjustment(
            r,
            employee_name=employees.get(r.owner_id, ""),
            operator_name=operators.get(r.operator_id or "", ""),
        )
        for r in rows
    ]


def get_adjustment(db: Session, adjustment_id: str) -> dict[str, Any] | None:
    """计划调整详情（真实数据）。"""
    from ..domain.models import Employee

    row = db.get(PlanAdjustmentRow, adjustment_id)
    if row is None:
        return None
    employee = db.get(Employee, row.owner_id)
    operator = db.get(Employee, row.operator_id) if row.operator_id else None
    return serialize_adjustment(
        row,
        employee_name=employee.name if employee else "",
        operator_name=operator.name if operator else "",
    )


def process_adjustment(
    db: Session,
    adjustment_id: str,
    payload: dict[str, Any],
    *,
    operator_id: str,
) -> dict[str, Any] | None:
    """领导处理计划调整（真落库）。

    ⚠️ **状态机**
    只有 ``PENDING`` 能被处理 ⇒ 已处理的单子不会被重复处理。
    ``PAUSE`` ⇒ 状态 ``PAUSED``；``CANCEL`` ⇒ 状态 ``CANCELED``。
    """
    row = db.get(PlanAdjustmentRow, adjustment_id)
    if row is None:
        return None
    if row.status != "PENDING":
        raise ValueError(f"该调整申请已处理（当前状态：{row.status}）")

    action = str(payload.get("action") or "").strip().upper()
    if action not in {"PAUSE", "CANCEL"}:
        raise ValueError(f"未知处理动作：{action}")

    comment = str(payload.get("comment") or "").strip()
    if not comment:
        raise ValueError("请填写处理说明")

    row.status = "PAUSED" if action == "PAUSE" else "CANCELED"
    row.operation_comment = comment
    row.operator_id = operator_id
    row.operated_at = _now()
    # ⚠️ 「保留证据链」是**显式选择**，前端传 ``keepEvidenceChain: true``。
    row.keep_evidence_chain = bool(payload.get("keepEvidenceChain", True))

    # ⚠️ 处理后**真的把原计划置为对应状态** ——
    # 否则「已暂停」只是标签，原计划照跑。
    _apply_to_original_plan(db, row)

    db.commit()
    db.refresh(row)
    return row


def _apply_to_original_plan(db: Session, row: PlanAdjustmentRow) -> None:
    """把调整结果应用到原计划。"""
    from ..domain.models import DayPlan, MonthPlan, WeekPlan

    if row.original_plan_type == "MONTH":
        plan = db.get(MonthPlan, row.original_plan_id)
        if plan is not None:
            plan.status = "paused" if row.status == "PAUSED" else "canceled"
    elif row.original_plan_type == "WEEK":
        plan = db.get(WeekPlan, row.original_plan_id)
        if plan is not None:
            plan.status = "PAUSED" if row.status == "PAUSED" else "CANCELED"
    else:
        plan = db.get(DayPlan, row.original_plan_id)
        if plan is not None:
            plan.status = "paused" if row.status == "PAUSED" else "canceled"


__all__ = [
    "create_adjustment",
    "get_adjustment",
    "list_adjustments",
    "process_adjustment",
    "serialize_adjustment",
]
