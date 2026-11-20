"""飞书方案自动同步后的**自动发起考核**。

为什么单独一个模块
----------------
「方案发布」与「发起考核」是**两件事**：

- 方案发布 = 规则生效（改版后新考核用新规则）
- 发起考核 = 这个周期真的开始跑了（生成任务、记录、通知员工）

把它们混在一起会让「改了方案」意外地**立刻给所有人开考核**。
所以这里做成独立的一步，由配置 ``feishu_scheme_sync_auto_start_task``
显式开启。

⚠️ **安全边界**
- **幂等**：同一周期 + 同一批员工**绝不重复发起**。
  判定依据是「该周期已有考核任务且已覆盖这些员工」。
- **只对已发布且已生效的方案发起**：草稿方案不发起。
- **一次失败不影响其它部门**：异常记进明细，不外抛。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.models import AssessmentRecord, AssessmentTask, Employee
from ..domain.scheme_models import SchemeVersion

logger = logging.getLogger(__name__)


def period_key(period_type: str, day: date) -> str:
    """按周期类型算周期标识（紧凑格式，与系统其它地方一致）。"""
    if period_type == "quarterly":
        quarter = (day.month - 1) // 3 + 1
        return f"{day.year}Q{quarter}"
    if period_type == "annual":
        return str(day.year)
    return f"{day.year}{day.month:02d}"


def period_bounds(period_type: str, day: date) -> tuple[date, date]:
    """算周期起止日期。"""
    if period_type == "quarterly":
        quarter = (day.month - 1) // 3 + 1
        start_month = (quarter - 1) * 3 + 1
        start = date(day.year, start_month, 1)
        end = date(day.year, start_month + 2, 1) + timedelta(days=31)
        return start, date(end.year, end.month, 1) - timedelta(days=1)
    if period_type == "annual":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    start = date(day.year, day.month, 1)
    end = date(day.year, day.month, 1) + timedelta(days=31)
    return start, date(end.year, end.month, 1) - timedelta(days=1)


def already_started(db: Session, period: str, employee_ids: set[str]) -> bool:
    """该周期是否已经为这些员工发起过考核。"""
    if not employee_ids:
        return False
    rows = db.execute(
        select(AssessmentTask, AssessmentRecord)
        .join(AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id)
        .where(AssessmentTask.period == period)
    ).all()
    covered = {record.employee_id for _task, record in rows}
    return bool(employee_ids & covered)


def start_assessment_for_scheme(
    db: Session,
    version: SchemeVersion,
    *,
    actor_id: str | None,
    period_type: str,
) -> dict[str, Any]:
    """为某个已发布方案自动发起考核。

    Returns:
        ``{"started": bool, "reason": str, "task_id": str | None, "count": int}``
    """
    binding = version.payload.get("binding") or {}
    department_id = binding.get("department_id")
    position = binding.get("position") or ""
    if not department_id:
        return {"started": False, "reason": "方案缺少适用部门", "task_id": None, "count": 0}

    today = datetime.now().date()
    period = period_key(period_type, today)

    # ⚠️ **只在方案已生效的周期发起** —— 未来才生效的方案现在发起是错的。
    effective_from = binding.get("effective_from")
    effective_to = binding.get("effective_to")
    period_start, period_end = period_bounds(period_type, today)
    if effective_from and effective_to:
        try:
            start_bound = date.fromisoformat(effective_from)
            end_bound = date.fromisoformat(effective_to)
        except ValueError:
            return {
                "started": False,
                "reason": "方案生效期格式非法",
                "task_id": None,
                "count": 0,
            }
        # 周期必须落在方案的生效期内
        if period_start < start_bound or period_end > end_bound:
            return {
                "started": False,
                "reason": (
                    f"当前周期 {period} 不在方案生效期内"
                    f"（{effective_from} ~ {effective_to}）"
                ),
                "task_id": None,
                "count": 0,
            }

    # ── 取范围内在职员工 ──
    stmt = select(Employee).where(
        Employee.department_id == department_id,
        Employee.employment_status == "active",
        Employee.account_status == "enabled",
    )
    if position:
        stmt = stmt.where(Employee.position == position)
    people = list(db.scalars(stmt).all())
    if not people:
        return {
            "started": False,
            "reason": "部门内没有符合条件的在职员工",
            "task_id": None,
            "count": 0,
        }

    # ── 幂等：同周期同员工不重复发起 ──
    if already_started(db, period, {e.id for e in people}):
        return {
            "started": False,
            "reason": f"周期 {period} 已发起过考核，未重复发起",
            "task_id": None,
            "count": 0,
        }

    # ── 截止时间 ──
    self_deadline = datetime.combine(period_end, datetime.min.time()) + timedelta(
        days=settings.feishu_scheme_sync_self_review_days
    )
    manager_deadline = datetime.combine(period_end, datetime.min.time()) + timedelta(
        days=settings.feishu_scheme_sync_manager_review_days
    )
    if manager_deadline <= self_deadline:
        manager_deadline = self_deadline + timedelta(days=1)

    from ..services import scheme_service
    from ..services.notification_service import record_event

    task = AssessmentTask(
        id=f"FS_{period}_{department_id}"[:64],
        name=f"{today.year}年{department_id}绩效考核",
        period=period,
        status="created",
        owner_id=actor_id or "",
        self_review_deadline=self_deadline,
        manager_review_deadline=manager_deadline,
    )
    db.add(task)
    db.flush()
    scheme_service.freeze_version(db, task, version, people)
    for employee in people:
        record = AssessmentRecord(
            task_id=task.id, employee_id=employee.id, status="not_started"
        )
        db.add(record)
        db.flush()
        record_event(db, record, "N9")
    db.flush()
    logger.info(
        "自动发起考核完成: task=%s period=%s 人数=%s", task.id, period, len(people)
    )
    return {
        "started": True,
        "reason": "",
        "task_id": task.id,
        "count": len(people),
    }


__all__ = [
    "already_started",
    "period_bounds",
    "period_key",
    "start_assessment_for_scheme",
]
