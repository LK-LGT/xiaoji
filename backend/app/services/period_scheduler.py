"""P1-2 —— 考核周期调度：自动开启 / 截止倒计时 / 逾期告警。

职责边界
--------
本模块**只做编排与提醒**，不做任何评分决策：

- **自动开启**：到期的任务从 ``draft`` 推进到 ``in_progress``，
  并为范围内员工建考核记录。
- **截止倒计时**：在截止前若干天列入提醒计划（每日频控由调用方配合）。
- **逾期告警**：超过截止时间仍未完成 → 列入逾期清单，可升级告警。

所有写操作都**幂等**，提醒由调用方按 ``event_key`` 去重后投递。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AssessmentRecord, AssessmentTask, Employee, _now

logger = logging.getLogger(__name__)

#: 任务状态流转
STATUS_DRAFT = "draft"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"

#: 倒计时提醒提前天数（截止前 N 天开始提醒）
COUNTDOWN_DAYS = 3


def _period_deadlines(task: AssessmentTask) -> tuple[datetime | None, datetime | None]:
    return task.self_review_deadline, task.manager_review_deadline


def _employee_scope(db: Session, task: AssessmentTask) -> list[Employee]:
    """取任务范围内的在职员工。"""
    stmt = select(Employee).where(
        Employee.employment_status == "active",
        Employee.account_status == "enabled",
    )
    if task.department_id:
        stmt = stmt.where(Employee.department_id == task.department_id)
    if task.position:
        stmt = stmt.where(Employee.position == task.position)
    return list(db.execute(stmt).scalars().all())


def ensure_records(db: Session, task: AssessmentTask) -> int:
    """为任务范围内员工补齐考核记录（幂等）。

    Returns:
        新建的记录数。
    """
    existing = {
        row.employee_id
        for row in db.scalars(
            select(AssessmentRecord).where(AssessmentRecord.task_id == task.id)
        ).all()
    }
    created = 0
    for employee in _employee_scope(db, task):
        if employee.id in existing:
            continue
        db.add(
            AssessmentRecord(
                id=f"{task.id}:{employee.id}",
                task_id=task.id,
                employee_id=employee.id,
                status="not_started",
            )
        )
        existing.add(employee.id)
        created += 1
    db.flush()
    return created


def start_due_tasks(db: Session, now: datetime | None = None) -> list[str]:
    """自动开启到期的考核任务。

    条件：状态为 ``draft`` **且**配置了至少一个截止时间。
    未配置截止时间的草稿任务不自动开启 —— 那多半是人工准备中的任务。

    ⚠️ ``now`` 仅用于**签名兼容**（``run_cycle`` 会传入）：
    截止时间的判定走 ``_period_deadlines(task)`` —— 读的是任务自身配置，
    不依赖「当前时刻」，所以这里不用它。

    Returns:
        被开启的任务 ID 列表。
    """
    del now  # 仅签名兼容；截止判定读任务配置，不用当前时刻
    started: list[str] = []
    tasks = db.scalars(
        select(AssessmentTask).where(AssessmentTask.status == STATUS_DRAFT)
    ).all()
    for task in tasks:
        self_deadline, manager_deadline = _period_deadlines(task)
        if self_deadline is None and manager_deadline is None:
            continue
        created = ensure_records(db, task)
        task.status = STATUS_IN_PROGRESS
        started.append(task.id)
        logger.info("考核任务自动开启: task_id=%s, 新建记录=%s", task.id, created)
    if started:
        db.flush()
    return started


def pending_records(db: Session, task: AssessmentTask) -> list[AssessmentRecord]:
    """取任务下尚未完成的记录。"""
    return list(
        db.scalars(
            select(AssessmentRecord).where(
                AssessmentRecord.task_id == task.id,
                AssessmentRecord.status != STATUS_COMPLETED,
            )
        ).all()
    )


def _days_left(deadline: datetime | None, now: datetime) -> int | None:
    """距截止的整天数（负数表示已逾期）。

    ⚠️ **不能用 ``timedelta.days``**：它是向零取整的，
    ``-2.5 天`` 会得到 ``-2``，导致逾期天数少报一天。

    这里用**向下取整**（``//``）：``-2.5`` → ``-3``，
    倒计时与逾期都用同一把尺子，口径一致。
    """
    if deadline is None:
        return None
    delta = deadline - now
    return int(delta.total_seconds() // 86400)


def reminder_plan(
    db: Session, task: AssessmentTask, now: datetime | None = None
) -> dict[str, Any]:
    """计算本轮提醒计划（不写库）。

    Returns:
        ``{"countdown": [...], "overdue": [...], "completed": int}``
        - countdown: 距截止 N 天内且未完成的记录
        - overdue: 已过截止时间且未完成的记录
    """
    moment = now or _now()
    countdown: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    completed = 0
    self_deadline, manager_deadline = _period_deadlines(task)
    for record in pending_records(db, task):
        if record.confirmed_rating:
            completed += 1
            continue
        # 自评阶段看自评截止，主管阶段看主管截止
        deadline = (
            manager_deadline
            if record.status == "self_review_submitted"
            else self_deadline
        )
        left = _days_left(deadline, moment)
        if left is None:
            continue
        if left < 0:
            overdue.append(
                {
                    "record_id": record.id,
                    "employee_id": record.employee_id,
                    "overdue_days": abs(left),
                    "stage": record.status,
                }
            )
        elif left <= COUNTDOWN_DAYS:
            countdown.append(
                {
                    "record_id": record.id,
                    "employee_id": record.employee_id,
                    "days_left": left,
                    "stage": record.status,
                }
            )
    return {
        "task_id": task.id,
        "period": task.period,
        "countdown": countdown,
        "overdue": overdue,
        "completed": completed,
    }


def run_cycle(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """跑一轮周期调度。

    1. 自动开启到期的任务
    2. 为在途任务计算倒计时与逾期提醒计划

    Returns:
        汇总结果（各任务的通知计划）。
    """
    moment = now or _now()
    started = start_due_tasks(db, moment)
    plans: list[dict[str, Any]] = []
    for task in db.scalars(
        select(AssessmentTask).where(AssessmentTask.status == STATUS_IN_PROGRESS)
    ).all():
        plans.append(reminder_plan(db, task, moment))
    return {
        "started_tasks": started,
        "plans": plans,
        "countdown_total": sum(len(p["countdown"]) for p in plans),
        "overdue_total": sum(len(p["overdue"]) for p in plans),
        "ran_at": moment.isoformat(),
    }


def deliver_reminders(
    db: Session, now: datetime | None = None, *, notify: bool = True
) -> dict[str, Any]:
    """投递倒计时与逾期提醒。

    **每日频控**：``event_key`` 取当天日期，同一记录同一天只发一次，
    避免调度器多轮执行把用户刷屏。

    Args:
        notify: 关闭时只计算不投递（供预演与测试）。
    """
    moment = now or _now()
    today = moment.date().isoformat()
    sent = 0
    skipped = 0

    from .notification_service import enqueue_new

    for task in db.scalars(
        select(AssessmentTask).where(AssessmentTask.status == STATUS_IN_PROGRESS)
    ).all():
        plan = reminder_plan(db, task, moment)
        for item in plan["countdown"]:
            record = db.get(AssessmentRecord, item["record_id"])
            if not record:
                continue
            scene = "N4" if item["stage"] == "self_review_submitted" else "N3"
            if notify:
                row = enqueue_new(
                    db,
                    employee_id=record.employee_id,
                    scene=scene,
                    period=task.period,
                    object_id=record.id,
                    object_type="assessment_record",
                    label=task.name,
                    event_key=f"{today}:{record.id}:countdown",
                    due_at=task.self_review_deadline,
                )
                sent += 1 if row is not None else 0
                skipped += 0 if row is not None else 1
            else:
                skipped += 1
        for item in plan["overdue"]:
            record = db.get(AssessmentRecord, item["record_id"])
            if not record:
                continue
            if notify:
                row = enqueue_new(
                    db,
                    employee_id=record.employee_id,
                    scene="N14",
                    period=task.period,
                    object_id=record.id,
                    object_type="assessment_record",
                    label=task.name,
                    event_key=f"{today}:{record.id}:overdue",
                    due_at=task.manager_review_deadline,
                )
                sent += 1 if row is not None else 0
                skipped += 0 if row is not None else 1
            else:
                skipped += 1
    return {"sent": sent, "skipped": skipped, "ran_at": moment.isoformat()}
