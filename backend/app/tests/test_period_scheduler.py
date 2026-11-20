"""P1-2 acceptance: 周期调度 —— 自动开启 / 倒计时 / 逾期告警。"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    _now,
)
from backend.app.domain.notification_models import Notification
from backend.app.services import period_scheduler as sched


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Department(id="D", name="业务", leader_id="L"))
    s.add(Department(id="X", name="其他", leader_id="XL"))
    s.flush()
    s.add(Employee(id="L", name="L", department_id="D", position="主管"))
    s.add(Employee(id="E1", name="E1", department_id="D", position="销售"))
    s.add(Employee(id="E2", name="E2", department_id="D", position="销售"))
    s.add(Employee(id="X1", name="X1", department_id="X", position="销售"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def _task(db, task_id="T1", status="draft", self_days=7, manager_days=14):
    now = _now()
    task = AssessmentTask(
        id=task_id,
        name="2026年11月考核",
        period="202611",
        department_id="D",
        position="销售",
        status=status,
        self_review_deadline=now + timedelta(days=self_days),
        manager_review_deadline=now + timedelta(days=manager_days),
    )
    db.add(task)
    db.commit()
    return task


def test_start_due_tasks_creates_records_idempotently(db):
    """自动开启：为范围内员工建记录，重复执行不重复建。"""
    task = _task(db)
    started = sched.start_due_tasks(db)
    assert started == ["T1"]
    assert task.status == "in_progress"
    records = db.scalars(
        select(AssessmentRecord).where(AssessmentRecord.task_id == "T1")
    ).all()
    assert len(records) == 2
    # 幂等：再跑一次不新增
    sched.start_due_tasks(db)
    records = db.scalars(
        select(AssessmentRecord).where(AssessmentRecord.task_id == "T1")
    ).all()
    assert len(records) == 2


def test_draft_task_without_deadline_is_not_auto_started(db):
    """未配置截止时间的草稿任务不自动开启（可能是人工准备中）。"""
    db.add(AssessmentTask(id="T2", name="手工草稿", period="202612", status="draft"))
    db.commit()
    assert sched.start_due_tasks(db) == []
    assert db.get(AssessmentTask, "T2").status == "draft"


def test_countdown_reminder_within_threshold(db):
    """倒计时：距截止 3 天内且未完成的记录进入提醒计划。"""
    task = _task(db, self_days=2)
    sched.start_due_tasks(db)
    plan = sched.reminder_plan(db, task)
    assert len(plan["countdown"]) == 2
    assert plan["overdue"] == []


def test_no_countdown_when_far_from_deadline(db):
    """距截止较远时不提醒。"""
    task = _task(db, self_days=30)
    sched.start_due_tasks(db)
    plan = sched.reminder_plan(db, task)
    assert plan["countdown"] == []
    assert plan["overdue"] == []


def test_overdue_detection(db):
    """逾期：超过截止时间仍未完成 → 进入逾期清单。"""
    task = _task(db, self_days=-2)
    sched.start_due_tasks(db)
    plan = sched.reminder_plan(db, task)
    assert len(plan["overdue"]) == 2
    # 向下取整的自然日口径：截止设为 now-2 天，实际略超 2 天 → 报 3 天
    assert plan["overdue"][0]["overdue_days"] == 3
    assert plan["countdown"] == []


def test_confirmed_record_excluded_from_reminders(db):
    """已确认等级的记录不再提醒（其余未完成的仍提醒）。"""
    task = _task(db, self_days=1)
    sched.start_due_tasks(db)
    record = db.scalar(
        select(AssessmentRecord).where(AssessmentRecord.task_id == "T1")
    )
    record.confirmed_rating = "B"
    db.commit()
    plan = sched.reminder_plan(db, task)
    assert len(plan["countdown"]) == 1
    assert plan["countdown"][0]["employee_id"] != record.employee_id
    assert plan["completed"] == 1


def test_deliver_reminders_is_daily_deduplicated(db):
    """每日频控：同一天重复投递不重复写通知。"""
    _task(db, self_days=1)
    sched.start_due_tasks(db)
    first = sched.deliver_reminders(db)
    assert first["sent"] == 2
    second = sched.deliver_reminders(db)
    # 第二次全部命中去重
    assert second["sent"] == 0
    assert db.query(Notification).count() == 2


def test_deliver_overdue_uses_overdue_scene(db):
    """逾期走 N14 场景。"""
    _task(db, self_days=-1)
    sched.start_due_tasks(db)
    sched.deliver_reminders(db)
    scenes = {n.scene for n in db.query(Notification).all()}
    assert scenes == {"N14"}


def test_run_cycle_reports_totals(db):
    """跑一轮调度返回汇总。"""
    _task(db, self_days=1)
    result = sched.run_cycle(db)
    assert result["started_tasks"] == ["T1"]
    assert result["countdown_total"] == 2
    assert result["overdue_total"] == 0
