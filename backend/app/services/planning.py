"""计划域服务 —— 月计划 / 周计划 / 日计划的 CRUD 与状态机。

职责边界：
1. **状态机**：三层计划共用一套流转规则，集中在本模块的 `plan_state_machine.py`，
   路由层不允许直接写 `plan.status = ...`。
2. **乐观锁**：提交 / 撤回 / 更新时校验前端回传的 `version_no`，
   不匹配抛 `PlanVersionConflict`（HTTP 409）。
3. **数据范围**：所有读写都以 `owner_id = 当前用户` 为边界，
   跨人访问由路由层复用 `services/access_control.py` 的 `can_access_employee` 判定。

术语：
- **月计划（month_plan）**：一个月的计划容器，条目是待办任务（month_plan_item）。
- **周计划（week_plan）**：一周的计划容器，条目可关联月计划条目。
- **日计划（day_plan）**：某一天的计划容器，条目可关联周计划条目。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..domain.enums import PlanStatus, PlanTaskType
from ..domain.models import (
    DayPlan,
    DayPlanItem,
    Department,
    Employee,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
    WeekPlanItem,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """当前 UTC 时间（naive），与 `domain.models._now` 保持同一口径。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===========================================================================
# 状态机
# ===========================================================================


class PlanStateError(Exception):
    """非法状态流转。"""

    def __init__(self, message: str, current: str, target: str) -> None:
        super().__init__(message)
        self.message = message
        self.current = current
        self.target = target


class PlanVersionConflict(Exception):
    """乐观锁冲突：前端持有的版本号与服务端不一致。"""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"版本冲突：期望 {expected}，实际 {actual}")
        self.expected = expected
        self.actual = actual


class PlanNotFound(Exception):
    """计划不存在，或不属于当前用户。"""

    def __init__(self, message: str = "计划不存在") -> None:
        super().__init__(message)
        self.message = message


# 合法迁移表。键是当前状态，值是可以迁移到的目标状态集合。
#
#   draft ──submit──> submitted
#   submitted ──withdraw──> draft
#   submitted ──approve──> approved
#   submitted ──reject──> rejected
#   approved ──withdraw──> draft
#   rejected ──withdraw──> draft
#
# 「撤回」统一回到 draft —— 员工改完可以再次提交。
_TRANSITIONS: dict[str, frozenset[str]] = {
    PlanStatus.DRAFT.value: frozenset({PlanStatus.SUBMITTED.value}),
    PlanStatus.SUBMITTED.value: frozenset(
        {
            PlanStatus.DRAFT.value,
            PlanStatus.PENDING.value,
            PlanStatus.APPROVED.value,
            PlanStatus.REJECTED.value,
        }
    ),
    PlanStatus.WITHDRAWN.value: frozenset({PlanStatus.SUBMITTED.value}),
    PlanStatus.PENDING.value: frozenset(
        {PlanStatus.APPROVED.value, PlanStatus.REJECTED.value, PlanStatus.DRAFT.value}
    ),
    PlanStatus.APPROVED.value: frozenset({PlanStatus.DRAFT.value}),
    PlanStatus.REJECTED.value: frozenset({PlanStatus.DRAFT.value}),
}

# 员工可编辑的状态。只有草稿 / 已撤回 / 被驳回能被改内容。
EDITABLE_STATES: frozenset[str] = frozenset(
    {PlanStatus.DRAFT.value, PlanStatus.WITHDRAWN.value, PlanStatus.REJECTED.value}
)


def can_transition(current: str, target: str) -> bool:
    """判断状态迁移是否合法。"""
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    """状态迁移合法性断言，非法则抛 `PlanStateError`。"""
    if not can_transition(current, target):
        raise PlanStateError(
            f"不允许的状态流转：{current} → {target}", current=current, target=target
        )


def is_editable(status: str) -> bool:
    """计划当前是否可编辑。"""
    return status in EDITABLE_STATES


# 周计划用大写状态（DRAFT / PENDING / APPROVED / REJECTED），
# 与前端 `weekPlan.ts` 的 `WeekPlanStatus` 对齐。
_WEEK_STATUS_ALIASES: dict[str, str] = {
    PlanStatus.DRAFT.value: "DRAFT",
    PlanStatus.SUBMITTED.value: "PENDING",
    PlanStatus.PENDING.value: "PENDING",
    PlanStatus.APPROVED.value: "APPROVED",
    PlanStatus.REJECTED.value: "REJECTED",
    PlanStatus.WITHDRAWN.value: "DRAFT",
}


def to_week_status(status: str) -> str:
    """把内部状态转成前端周计划用的状态字符串。"""
    return _WEEK_STATUS_ALIASES.get(status, "DRAFT")


# ===========================================================================
# 乐观锁
# ===========================================================================


def assert_version(plan: Any, expected: int | None) -> None:
    """校验乐观锁版本号。

    `expected` 为 None 表示前端未带版本号（不做校验），
    但提交 / 撤回这类状态流转必须带，故由调用方决定是否允许 None。
    """
    if expected is None:
        return
    actual = int(getattr(plan, "version_no", 1) or 1)
    if int(expected) != actual:
        raise PlanVersionConflict(expected=int(expected), actual=actual)


def _bump_version(plan: Any) -> int:
    """版本号 +1 并返回新值。"""
    current = int(getattr(plan, "version_no", 1) or 1)
    plan.version_no = current + 1
    return plan.version_no


# ===========================================================================
# 日期工具
# ===========================================================================


def parse_date(value: Any, field_name: str = "date") -> date:
    """解析 `YYYY-MM-DD` 日期字符串。"""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} 格式非法，应为 YYYY-MM-DD：{value}") from exc


def current_month() -> str:
    """当前月份 `YYYY-MM`。"""
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def week_range(anchor: date) -> tuple[date, date]:
    """返回 anchor 所在周的周一至周日。"""
    monday = anchor - timedelta(days=anchor.weekday())
    return monday, monday + timedelta(days=6)


def normalize_month(value: Any) -> str:
    """归一化月份为 `YYYY-MM`。"""
    text = str(value or "").strip()
    if not text:
        return current_month()
    # 兼容 `202611` 紧凑格式
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}-{text[4:]}"
    return text[:7]


# ===========================================================================
# 序列化（响应结构严格对齐前端 TS 接口）
# ===========================================================================


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date_str(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def serialize_month_plan_item(item: MonthPlanItem) -> dict:
    """`EmployeeMonthPlanItem`。"""
    return {
        "id": item.id,
        "taskType": item.task_type,
        "performanceWeight": item.performance_weight,
        "estimatedValue": item.estimated_value,
        "taskName": item.task_name,
        "taskContent": item.task_content,
        "progress": item.progress,
        "deliverable": item.deliverable,
        "quality": item.quality,
        "estimatedStartDate": _date_str(item.estimated_start_date),
        "estimatedEndDate": _date_str(item.estimated_end_date),
        "status": item.status,
        "sortNo": item.sort_no,
        "submittedAt": _iso(item.submitted_at),
        "approvedAt": _iso(item.approved_at),
        "approvalComment": item.approval_comment,
    }


def serialize_week_plan_item(item: WeekPlanItem) -> dict:
    """`WeekPlanItem`（前端 weekPlan.ts）。

    附带 `parent` 字段，对应 `WeekPlanParentOption`。
    """
    parent = None
    if item.month_plan_item_id:
        month_item = getattr(item, "month_item", None)
        if month_item is not None:
            parent = serialize_week_plan_parent(month_item)
    return {
        "id": item.id,
        "monthPlanItemId": item.month_plan_item_id,
        "content": item.content,
        "completionStatus": item.completion_status,
        "deliverable": item.deliverable,
        "quality": item.quality,
        "remark": item.remark,
        "plannedFinishDate": _date_str(item.planned_finish_date),
        "sortNo": item.sort_no,
        "parent": parent,
    }


def serialize_week_plan_parent(month_item: MonthPlanItem) -> dict:
    """`WeekPlanParentOption`。"""
    plan = getattr(month_item, "plan", None)
    owner = getattr(plan, "owner", None) if plan is not None else None
    return {
        "monthPlanItemId": month_item.id,
        "monthPlanId": plan.id if plan is not None else "",
        "monthPlanTitle": plan.title if plan is not None else "",
        "planMonth": plan.plan_month if plan is not None else "",
        "taskType": month_item.task_type,
        "performanceWeight": month_item.performance_weight,
        "taskName": month_item.task_name,
        "estimatedStartDate": _date_str(month_item.estimated_start_date),
        "estimatedEndDate": _date_str(month_item.estimated_end_date),
        "status": month_item.status,
        "existingWeekPlanCount": 0,
        "employeeName": owner.name if owner is not None else "",
    }


def serialize_week_plan_summary(plan: WeekPlan, item_count: int = 0) -> dict:
    """`WeekPlanSummary`。"""
    owner = getattr(plan, "owner", None)
    dept = getattr(owner, "department", None) if owner is not None else None
    return {
        "id": plan.id,
        "title": plan.title,
        "weekStart": _date_str(plan.week_start),
        "weekEnd": _date_str(plan.week_end),
        "status": to_week_status(plan.status),
        "versionNo": plan.version_no,
        "ownerUserId": owner.id if owner is not None else "",
        "employeeName": owner.name if owner is not None else "",
        "deptId": owner.department_id if owner is not None else None,
        "departmentName": dept.name if dept is not None else "",
        "itemCount": item_count,
        "submitAt": _iso(plan.submitted_at),
        "approveAt": _iso(plan.approve_at),
        "approvalComment": plan.approval_comment,
    }


def serialize_month_plan_detail(
    plan: MonthPlan,
    items: Sequence[MonthPlanItem],
    employee: Employee | None,
    department_name: str,
) -> dict:
    """`EmployeeMonthPlanDetailResp`。"""
    return {
        "id": plan.id,
        "planMonth": plan.plan_month,
        "employeeName": employee.name if employee is not None else "",
        "departmentName": department_name,
        "projectName": plan.project_name,
        "positionName": plan.position_name,
        "status": plan.status,
        "resultStatus": plan.result_status,
        "updatedAt": _iso(plan.updated_at),
        "approvalComment": plan.approval_comment,
        "approvedAt": _iso(plan.approved_at),
        "items": [serialize_month_plan_item(i) for i in items],
        "deliverables": [],
        "resultSummary": {
            "submittedCount": 0,
            "confirmedCount": 0,
            "rejectedCount": 0,
            "latestVersion": "",
            "overallCompletionRate": plan.completion_rate,
        },
        "confirmRecords": [],
    }


def serialize_week_plan_detail(
    plan: WeekPlan,
    items: Sequence[WeekPlanItem],
    sibling_plans: Sequence[WeekPlan],
    day_plan_count: int,
) -> dict:
    """`WeekPlanDetail`。"""
    item_count = len(items)
    return {
        "summary": serialize_week_plan_summary(plan, item_count),
        "items": [serialize_week_plan_item(i) for i in items],
        "siblingPlans": [
            serialize_week_plan_summary(p, len(getattr(p, "items", []) or [])) for p in sibling_plans
        ],
        "dayPlanCount": day_plan_count,
    }


def serialize_day_plan_detail(
    plan: DayPlan | None,
    plan_date: date,
    org_name: str,
    week_plan_item_options: Sequence[dict],
    legacy_month_plan_item: dict | None,
) -> dict:
    """`EmployeeDayPlanDetailResp`。

    `plan` 为 None 表示当天还没有计划（新建场景）。
    """
    return {
        "id": plan.id if plan is not None else 0,
        "planDate": plan_date.isoformat(),
        "orgName": org_name,
        "relatedWeekPlanItemIds": [
            item.week_plan_item_id
            for item in (getattr(plan, "items", []) or [])
            if item.week_plan_item_id
        ],
        "content": plan.content if plan is not None else "",
        "remark": plan.remark if plan is not None else "",
        "status": plan.status if plan is not None else PlanStatus.DRAFT.value,
        "reviewStatus": plan.review_status if plan is not None else None,
        "riskLevel": plan.risk_level if plan is not None else None,
        "leaderComment": plan.leader_comment if plan is not None else None,
        "reviewedAt": _iso(plan.reviewed_at) if plan is not None else None,
        "departmentComment": plan.department_comment if plan is not None else None,
        "departmentReviewedAt": _iso(plan.department_reviewed_at) if plan is not None else None,
        "approvalDueAt": _iso(plan.approval_due_at) if plan is not None else None,
        "calendarRule": {
            "date": plan_date.isoformat(),
            "ruleType": "WORKDAY",
            "forceReport": False,
            "explicit": False,
            "missingRequired": False,
        },
        "weekPlanItemOptions": list(week_plan_item_options),
        "legacyMonthPlanItem": legacy_month_plan_item,
    }


# ===========================================================================
# 月计划 CRUD
# ===========================================================================


def get_month_plan(db: Session, owner_id: str, plan_id: str) -> MonthPlan:
    """按 id 取月计划，限定归属人。"""
    plan = db.get(MonthPlan, plan_id)
    if plan is None or plan.owner_id != owner_id:
        raise PlanNotFound("月计划不存在")
    return plan


def list_month_plan_items(db: Session, plan_id: str) -> list[MonthPlanItem]:
    """列出月计划条目（按 sort_no 排序）。"""
    rows = db.execute(
        select(MonthPlanItem)
        .where(MonthPlanItem.plan_id == plan_id)
        .order_by(MonthPlanItem.sort_no, MonthPlanItem.created_at)
    ).scalars().all()
    return list(rows)


def upsert_month_plan_draft(
    db: Session,
    owner_id: str,
    plan_id: str | None,
    payload: dict,
) -> MonthPlan:
    """新建或更新月计划草稿。

    - `plan_id` 为 None → 按 `planMonth` 找本月计划，没有就新建。
    - 已有条目按 `id` 更新，没有 id 的新增，带 id 但不存在的删除。
    """
    plan_month = normalize_month(payload.get("planMonth"))
    raw_items = payload.get("items") or []

    if plan_id:
        plan = get_month_plan(db, owner_id, plan_id)
    else:
        plan = db.execute(
            select(MonthPlan).where(
                MonthPlan.owner_id == owner_id,
                MonthPlan.plan_month == plan_month,
            )
        ).scalar_one_or_none()
        if plan is None:
            plan = MonthPlan(
                owner_id=owner_id,
                plan_month=plan_month,
                title=str(payload.get("title") or ""),
                status=PlanStatus.DRAFT.value,
            )
            db.add(plan)
            db.flush()

    if not is_editable(plan.status):
        raise PlanStateError(
            f"当前状态（{plan.status}）不允许编辑", current=plan.status, target=plan.status
        )

    existing = {item.id: item for item in list_month_plan_items(db, plan.id)}
    keep_ids: set[str] = set()

    for index, raw in enumerate(raw_items):
        item_id = raw.get("id")
        if item_id and item_id in existing:
            item = existing[item_id]
            keep_ids.add(item.id)
        else:
            item = MonthPlanItem(plan_id=plan.id)
            db.add(item)
        item.task_type = str(raw.get("taskType") or PlanTaskType.REGULAR.value)
        item.task_name = str(raw.get("taskName") or "")
        item.task_content = str(raw.get("taskContent") or "")
        item.deliverable = str(raw.get("deliverable") or "")
        item.quality = str(raw.get("quality") or "")
        item.performance_weight = float(raw.get("performanceWeight") or 0)
        item.estimated_value = float(raw.get("estimatedValue") or 0)
        item.estimated_start_date = _optional_date(raw.get("estimatedStartDate"))
        item.estimated_end_date = _optional_date(raw.get("estimatedEndDate"))
        item.sort_no = int(raw.get("sortNo") if raw.get("sortNo") is not None else index)

    # 带 id 但本次没提交的条目 → 删除
    for item_id, item in existing.items():
        if item_id not in keep_ids:
            db.delete(item)

    if payload.get("title"):
        plan.title = str(payload["title"])
    if payload.get("projectName"):
        plan.project_name = str(payload["projectName"])
    if payload.get("positionName"):
        plan.position_name = str(payload["positionName"])

    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def _optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return parse_date(value)


def delete_month_plan_item(db: Session, owner_id: str, plan_id: str, item_id: str) -> None:
    """删除月计划条目。"""
    plan = get_month_plan(db, owner_id, plan_id)
    if not is_editable(plan.status):
        raise PlanStateError(
            f"当前状态（{plan.status}）不允许删除条目", current=plan.status, target=plan.status
        )
    item = db.get(MonthPlanItem, item_id)
    if item is None or item.plan_id != plan.id:
        raise PlanNotFound("月计划条目不存在")
    db.delete(item)
    plan.version_no = _bump_version(plan)
    db.flush()


def clear_month_plan_draft(db: Session, owner_id: str, plan_id: str) -> MonthPlan:
    """清空月计划草稿的条目（保留计划本体）。"""
    plan = get_month_plan(db, owner_id, plan_id)
    if not is_editable(plan.status):
        raise PlanStateError(
            f"当前状态（{plan.status}）不允许清空", current=plan.status, target=plan.status
        )
    db.execute(delete(MonthPlanItem).where(MonthPlanItem.plan_id == plan.id))
    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def submit_month_plan(db: Session, owner_id: str, plan_id: str) -> MonthPlan:
    """提交月计划：draft/withdrawn → submitted。"""
    plan = get_month_plan(db, owner_id, plan_id)
    assert_transition(plan.status, PlanStatus.SUBMITTED.value)
    plan.status = PlanStatus.SUBMITTED.value
    plan.submitted_at = _now()
    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def withdraw_month_plan(db: Session, owner_id: str, plan_id: str) -> MonthPlan:
    """撤回月计划：submitted → draft。"""
    plan = get_month_plan(db, owner_id, plan_id)
    assert_transition(plan.status, PlanStatus.DRAFT.value)
    plan.status = PlanStatus.DRAFT.value
    plan.submitted_at = None
    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def month_plan_template_preview(db: Session, owner_id: str, plan_id: str) -> dict:
    """`EmployeeMonthPlanTemplatePreviewResp`。"""
    plan = get_month_plan(db, owner_id, plan_id)
    items = list_month_plan_items(db, plan.id)
    owner = db.get(Employee, plan.owner_id)
    return {
        "planId": plan.id,
        "planMonth": plan.plan_month,
        "employeeName": owner.name if owner is not None else "",
        "projectName": plan.project_name,
        "positionName": plan.position_name,
        "items": [
            {
                "rowNo": index + 1,
                "taskContent": item.task_content,
                "performanceWeight": item.performance_weight,
                "deliverable": item.deliverable,
                "quality": item.quality,
                "estimatedStartDate": _date_str(item.estimated_start_date),
                "estimatedEndDate": _date_str(item.estimated_end_date),
                "estimatedValue": item.estimated_value,
            }
            for index, item in enumerate(items)
        ],
        "estimatedValueTotal": sum(i.estimated_value for i in items),
    }


# ===========================================================================
# 周计划 CRUD
# ===========================================================================


def get_week_plan(db: Session, owner_id: str, plan_id: str) -> WeekPlan:
    """按 id 取周计划，限定归属人。"""
    plan = db.get(WeekPlan, plan_id)
    if plan is None or plan.owner_id != owner_id:
        raise PlanNotFound("周计划不存在")
    return plan


def list_week_plan_items(db: Session, plan_id: str) -> list[WeekPlanItem]:
    rows = db.execute(
        select(WeekPlanItem)
        .where(WeekPlanItem.plan_id == plan_id)
        .order_by(WeekPlanItem.sort_no, WeekPlanItem.created_at)
    ).scalars().all()
    return list(rows)


def list_week_plans(
    db: Session,
    owner_id: str,
    status: str | None = None,
    week_start: str | None = None,
    dept_id: str | None = None,
) -> list[WeekPlan]:
    """列出「我的」周计划。"""
    stmt = select(WeekPlan).where(WeekPlan.owner_id == owner_id)
    if week_start:
        stmt = stmt.where(WeekPlan.week_start == parse_date(week_start, "weekStart"))
    if dept_id:
        stmt = stmt.join(Employee, WeekPlan.owner_id == Employee.id).where(
            Employee.department_id == dept_id
        )
    rows = db.execute(stmt.order_by(WeekPlan.week_start.desc(), WeekPlan.created_at.desc())).scalars().all()
    if status:
        wanted = status.upper()
        rows = [r for r in rows if to_week_status(r.status) == wanted]
    return list(rows)


def list_week_plan_parent_options(db: Session, owner_id: str) -> list[dict]:
    """可选的父级月计划条目。

    只列出**本人**月计划里、且尚未被任何周计划引用的条目 —— 防止同一月计划条目
    被反复下钻成多份周计划。
    """
    used = set(
        db.execute(select(WeekPlanItem.month_plan_item_id).where(
            WeekPlanItem.month_plan_item_id.isnot(None)
        )).scalars().all()
    )
    rows = db.execute(
        select(MonthPlanItem)
        .join(MonthPlan, MonthPlanItem.plan_id == MonthPlan.id)
        .where(MonthPlan.owner_id == owner_id)
        .order_by(MonthPlan.plan_month.desc(), MonthPlanItem.sort_no)
    ).scalars().all()
    return [serialize_week_plan_parent(item) for item in rows if item.id not in used]


def upsert_week_plan(
    db: Session,
    owner_id: str,
    plan_id: str | None,
    payload: dict,
) -> WeekPlan:
    """新建或更新周计划。"""
    week_start_raw = payload.get("weekStart")
    if not week_start_raw:
        raise ValueError("weekStart 不能为空")
    week_start = parse_date(week_start_raw, "weekStart")
    week_end = week_start + timedelta(days=6)
    raw_items = payload.get("items") or []

    if plan_id:
        plan = get_week_plan(db, owner_id, plan_id)
        if not is_editable(plan.status):
            raise PlanStateError(
                f"当前状态（{plan.status}）不允许编辑", current=plan.status, target=plan.status
            )
    else:
        plan = WeekPlan(
            owner_id=owner_id,
            title=str(payload.get("title") or ""),
            week_start=week_start,
            week_end=week_end,
            status=PlanStatus.DRAFT.value,
        )
        db.add(plan)
        db.flush()

    plan.week_start = week_start
    plan.week_end = week_end
    if payload.get("title"):
        plan.title = str(payload["title"])

    existing = {item.id: item for item in list_week_plan_items(db, plan.id)}
    keep_ids: set[str] = set()
    for index, raw in enumerate(raw_items):
        item_id = raw.get("id")
        if item_id and item_id in existing:
            item = existing[item_id]
            keep_ids.add(item.id)
        else:
            item = WeekPlanItem(plan_id=plan.id)
            db.add(item)
        item.month_plan_item_id = raw.get("monthPlanItemId") or None
        item.content = str(raw.get("content") or "")
        item.deliverable = raw.get("deliverable") or None
        item.quality = raw.get("quality") or None
        item.planned_finish_date = _optional_date(raw.get("plannedFinishDate"))
        item.sort_no = int(raw.get("sortNo") if raw.get("sortNo") is not None else index)

    for item_id, item in existing.items():
        if item_id not in keep_ids:
            db.delete(item)

    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def delete_week_plan(db: Session, owner_id: str, plan_id: str) -> WeekPlan:
    """删除周计划（仅草稿态允许）。"""
    plan = get_week_plan(db, owner_id, plan_id)
    if not is_editable(plan.status):
        raise PlanStateError(
            f"当前状态（{plan.status}）不允许删除", current=plan.status, target=plan.status
        )
    db.delete(plan)
    db.flush()
    return plan


def submit_week_plan(db: Session, owner_id: str, plan_id: str) -> WeekPlan:
    """提交周计划。"""
    plan = get_week_plan(db, owner_id, plan_id)
    assert_transition(plan.status, PlanStatus.SUBMITTED.value)
    plan.status = PlanStatus.SUBMITTED.value
    plan.submitted_at = _now()
    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def withdraw_week_plan(db: Session, owner_id: str, plan_id: str) -> WeekPlan:
    """撤回周计划。"""
    plan = get_week_plan(db, owner_id, plan_id)
    assert_transition(plan.status, PlanStatus.DRAFT.value)
    plan.status = PlanStatus.DRAFT.value
    plan.submitted_at = None
    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def sibling_week_plans(db: Session, owner_id: str, plan: WeekPlan) -> list[WeekPlan]:
    """同周的其他周计划（`WeekPlanDetail.siblingPlans`）。"""
    rows = db.execute(
        select(WeekPlan).where(
            WeekPlan.owner_id == owner_id,
            WeekPlan.week_start == plan.week_start,
            WeekPlan.id != plan.id,
        )
    ).scalars().all()
    return list(rows)


def count_day_plans_for_week(db: Session, owner_id: str, plan: WeekPlan) -> int:
    """该周内已建的日计划数量。"""
    from ..domain.models import DayPlan as _DayPlan

    count = db.execute(
        select(func.count(_DayPlan.id)).where(
            _DayPlan.owner_id == owner_id,
            _DayPlan.plan_date >= plan.week_start,
            _DayPlan.plan_date <= plan.week_end,
        )
    ).scalar_one()
    return int(count or 0)


# ===========================================================================
# 日计划 CRUD
# ===========================================================================


def get_day_plan(db: Session, owner_id: str, plan_id: str) -> DayPlan:
    """按 id 取日计划，限定归属人。"""
    plan = db.get(DayPlan, plan_id)
    if plan is None or plan.owner_id != owner_id:
        raise PlanNotFound("日计划不存在")
    return plan


def get_day_plan_by_date(db: Session, owner_id: str, plan_date: date) -> DayPlan | None:
    """按日期取日计划，不存在返回 None。"""
    return db.execute(
        select(DayPlan).where(DayPlan.owner_id == owner_id, DayPlan.plan_date == plan_date)
    ).scalar_one_or_none()


def upsert_day_plan(
    db: Session,
    owner_id: str,
    payload: dict,
) -> DayPlan:
    """新建或更新日计划草稿。

    - `id` 存在 → 更新该条（必须是本人且当天唯一）。
    - `id` 缺省 → 按 `planDate` 找当天计划，没有就新建。
    """
    plan_date = parse_date(payload.get("planDate"), "planDate")
    raw_items = payload.get("relatedWeekPlanItemIds") or []

    plan_id = payload.get("id")
    if plan_id:
        plan = get_day_plan(db, owner_id, plan_id)
        if not is_editable(plan.status):
            raise PlanStateError(
                f"当前状态（{plan.status}）不允许编辑", current=plan.status, target=plan.status
            )
        plan.plan_date = plan_date
    else:
        plan = get_day_plan_by_date(db, owner_id, plan_date)
        if plan is None:
            plan = DayPlan(
                owner_id=owner_id,
                plan_date=plan_date,
                status=PlanStatus.DRAFT.value,
            )
            db.add(plan)
            db.flush()
        elif not is_editable(plan.status):
            raise PlanStateError(
                f"当前状态（{plan.status}）不允许编辑", current=plan.status, target=plan.status
            )

    plan.content = str(payload.get("content") or "")
    plan.remark = str(payload.get("remark") or "")

    # 重建条目：关联的周计划条目集合以本次提交为准
    db.execute(delete(DayPlanItem).where(DayPlanItem.plan_id == plan.id))
    for index, week_item_id in enumerate(raw_items):
        if not week_item_id:
            continue
        db.add(
            DayPlanItem(
                plan_id=plan.id,
                week_plan_item_id=str(week_item_id),
                content="",
                sort_no=index,
            )
        )

    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def submit_day_plan(db: Session, owner_id: str, payload: dict) -> DayPlan:
    """提交日计划。"""
    plan = upsert_day_plan(db, owner_id, payload)
    assert_transition(plan.status, PlanStatus.SUBMITTED.value)
    plan.status = PlanStatus.SUBMITTED.value
    plan.submitted_at = _now()
    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def withdraw_day_plan(db: Session, owner_id: str, plan_id: str) -> DayPlan:
    """撤回日计划。"""
    plan = get_day_plan(db, owner_id, plan_id)
    assert_transition(plan.status, PlanStatus.DRAFT.value)
    plan.status = PlanStatus.DRAFT.value
    plan.submitted_at = None
    plan.version_no = _bump_version(plan)
    db.flush()
    return plan


def delete_day_plan(db: Session, owner_id: str, plan_id: str) -> None:
    """删除日计划（仅草稿态允许）。"""
    plan = get_day_plan(db, owner_id, plan_id)
    if not is_editable(plan.status):
        raise PlanStateError(
            f"当前状态（{plan.status}）不允许删除", current=plan.status, target=plan.status
        )
    db.delete(plan)
    db.flush()


def day_plan_week_item_options(
    db: Session, owner_id: str, plan_date: date
) -> list[dict]:
    """当天可选的周计划条目（`EmployeeWeekPlanItemOption`）。

    取当天所属周的本人周计划条目。
    """
    week_start, _ = week_range(plan_date)
    plans = db.execute(
        select(WeekPlan).where(
            WeekPlan.owner_id == owner_id,
            WeekPlan.week_start == week_start,
        )
    ).scalars().all()
    options: list[dict] = []
    for plan in plans:
        for item in list_week_plan_items(db, plan.id):
            options.append(
                {
                    "id": item.id,
                    "content": item.content,
                    "deliverable": item.deliverable,
                    "plannedFinishDate": _date_str(item.planned_finish_date),
                    "monthPlanItemId": item.month_plan_item_id,
                    "monthPlanItemName": None,
                }
            )
    return options


def department_name_for(db: Session, employee: Employee | None) -> str:
    """取员工所在部门名。"""
    if employee is None or not employee.department_id:
        return ""
    dept = db.get(Department, employee.department_id)
    return dept.name if dept is not None else ""
