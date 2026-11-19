"""FS-4：事务、发送故障、幂等与消息归属。"""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.app.domain.models import AssessmentRecord, AssessmentTask, Employee, MonthPlan
from backend.app.domain.notification_models import Notification
from backend.app.routes import messages
from backend.app.services import notification_service as service
from backend.app.services.notification_templates import SCENES


@pytest.fixture
def people(db):
    manager = Employee(id="NM", name="主管", employment_status="active", account_status="enabled", feishu_open_id="ou_manager")
    employee = Employee(id="NE", name="员工", manager_id="NM", employment_status="active", account_status="enabled", feishu_open_id="ou_employee")
    db.add_all([manager, employee])
    db.commit()
    return employee, manager


class Client:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def send_text_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail:
            raise RuntimeError("upstream credential must not be logged")


def approve_delivery(monkeypatch, row):
    monkeypatch.setattr(service.settings, "notification_enabled", True)
    monkeypatch.setattr(service.settings, "notification_canary_employee_id", row.employee_id)
    monkeypatch.setattr(service.settings, "notification_canary_scene", row.scene)
    monkeypatch.setattr(service.settings, "notification_canary_outbox_id", row.id)


def test_canary_only_claims_approved_row(db, people, monkeypatch):
    employee, manager = people
    allowed = service.enqueue(db, employee_id=employee.id, scene="N5", period="allowed")
    others = [
        service.enqueue(db, employee_id=manager.id, scene="N5", period="P"),
        service.enqueue(db, employee_id=employee.id, scene="N7", period="P"),
        service.enqueue(db, employee_id=employee.id, scene="N5", period="old"),
    ]
    db.commit()
    client = Client()
    approve_delivery(monkeypatch, allowed)
    monkeypatch.setattr(service.settings, "notification_enabled", False)
    assert service.dispatch_pending(db, client=client) == 0
    monkeypatch.setattr(service.settings, "notification_enabled", True)
    monkeypatch.setattr(service.settings, "notification_canary_outbox_id", "")
    assert service.dispatch_pending(db, client=client) == 0
    monkeypatch.setattr(service.settings, "notification_canary_outbox_id", others[0].id)
    assert service.dispatch_pending(db, client=client) == 0
    approve_delivery(monkeypatch, allowed)
    assert service.dispatch_pending(db, client=client) == 1
    assert service.dispatch_pending(db, client=client) == 0
    assert [call[0][0] for call in client.calls] == [employee.feishu_open_id]
    assert all(row.status == "pending" and row.attempts == 0 for row in others)


def test_matrix_delivery_and_dedup(db, people, monkeypatch):
    employee, _ = people
    db.add(AssessmentTask(id="MATRIX", name="考核", period="202611", status="created"))
    db.flush()
    db.add_all([
        AssessmentRecord(id="N3", task_id="MATRIX", employee_id=employee.id, status="not_started"),
        AssessmentRecord(id="N4", task_id="MATRIX", employee_id="NM", status="self_review_submitted"),
    ])
    db.flush()
    for scene in SCENES:
        service.enqueue(db, employee_id=employee.id, scene=scene, period="P", object_id=scene)
        service.enqueue(db, employee_id=employee.id, scene=scene, period="P", object_id=scene)
    db.commit()
    assert db.query(Notification).count() == len(SCENES)
    client = Client()
    for row in db.query(Notification).all():
        approve_delivery(monkeypatch, row)
        assert service.dispatch_pending(db, client=client) == 1
        assert service.dispatch_pending(db, client=client) == 0
    assert len(client.calls) == len(SCENES)
    assert db.query(Notification).filter_by(status="sent").count() == len(SCENES)


def test_plan_submission_queues_without_network_and_keeps_correlation(db, people):
    from backend.app.services import feishu_approval_sync

    employee, manager = people
    plan = MonthPlan(owner_id=employee.id, plan_month="2026-11", title="计划", status="submitted")
    db.add(plan)
    db.flush()
    assert feishu_approval_sync.bind_and_notify_plan_submitted_safe(
        db, owner_id=employee.id, plan_type="month", plan_id=plan.id, plan_title="十一月计划",
    )
    row = db.query(Notification).one()
    assert row.employee_id == manager.id and row.scene == "N1"
    assert "PP" in row.content and row.status == "pending"


def test_reject_real_plan_emits_notification(db, people):
    from backend.app.services import plan_approval

    employee, manager = people
    plan = MonthPlan(owner_id=employee.id, plan_month="2026-11", title="计划", status="submitted")
    db.add(plan)
    db.flush()
    plan_approval.reject_month_plan(db, manager.id, plan.id, comment="请修改")
    assert db.query(Notification).filter_by(scene="N5", employee_id=employee.id).count() == 1


def test_rollback_does_not_leave_notification(db, people):
    employee, _ = people
    # 显式业务写入确保 SQLite 外层事务已开始。
    employee.name = "修改"
    db.flush()
    service.enqueue(db, employee_id=employee.id, scene="N5", period="P")
    db.rollback()
    assert db.query(Notification).count() == 0


def test_send_failure_preserves_business_and_inbox(db, people, monkeypatch):
    employee, _ = people
    plan = MonthPlan(owner_id=employee.id, plan_month="2026-11", title="计划", status="submitted")
    db.add(plan)
    row = service.enqueue(db, employee_id=employee.id, scene="N5", period="P")
    db.commit()
    approve_delivery(monkeypatch, row)
    assert service.dispatch_pending(db, client=Client(fail=True)) == 0
    assert db.get(MonthPlan, plan.id).status == "submitted"
    assert row.status == "failed"
    assert row.last_error == "RuntimeError"
    assert row.read_at is None


def test_completed_reminder_is_not_sent(db, people, monkeypatch):
    employee, _ = people
    db.add(AssessmentTask(id="DONE", name="考核", period="202611", status="created"))
    db.flush()
    record = AssessmentRecord(task_id="DONE", employee_id=employee.id, status="self_review_submitted")
    db.add(record)
    db.flush()
    row = service.enqueue(db, employee_id=employee.id, scene="N3", period="DONE", object_id=record.id)
    db.commit()
    approve_delivery(monkeypatch, row)
    client = Client()
    assert service.dispatch_pending(db, client=client) == 0
    assert row.skip_reason == "task_completed" and row.read_at is not None
    assert not client.calls


def test_retry_requires_hr_and_cannot_resend_sent(db, people, monkeypatch):
    from types import SimpleNamespace

    employee, manager = people
    row = service.enqueue(db, employee_id=employee.id, scene="N7", period="P")
    row.status = "failed"
    db.commit()
    monkeypatch.setattr(messages, "build_access_context_for_user", lambda user, db: SimpleNamespace(roles={"hr"} if user.id == manager.id else set()))
    with pytest.raises(HTTPException) as error:
        asyncio.run(messages.retry_delivery(row.id, current_user=employee, db=db))
    assert error.value.status_code == 403
    asyncio.run(messages.retry_delivery(row.id, current_user=manager, db=db))
    db.flush()
    assert row.status == "pending"
    row.status = "sent"
    db.commit()
    with pytest.raises(HTTPException) as error:
        asyncio.run(messages.retry_delivery(row.id, current_user=manager, db=db))
    assert error.value.status_code == 409


def test_inactive_recipient_and_missing_identity(db, people, monkeypatch):
    employee, _ = people
    row = service.enqueue(db, employee_id=employee.id, scene="N7", period="P")
    employee.account_status = "disabled"
    db.commit()
    approve_delivery(monkeypatch, row)
    client = Client()
    service.dispatch_pending(db, client=client)
    assert row.status == "skipped" and row.skip_reason == "inactive_employee"
    assert not client.calls
    assert service.enqueue(db, employee_id=employee.id, scene="N9", period="Q") is None


def test_unconfigured_delivery_skips_without_creating_client(db, people, monkeypatch):
    employee, _ = people
    row = service.enqueue(db, employee_id=employee.id, scene="N7", period="P")
    db.commit()

    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "feishu_app_id", "")
    monkeypatch.setattr(settings, "feishu_app_secret", "")

    class ForbiddenClient:
        def __init__(self, **kwargs):
            raise AssertionError("client must not be created without credentials")

    monkeypatch.setattr("backend.app.services.feishu_client.FeishuClient", ForbiddenClient)
    approve_delivery(monkeypatch, row)
    assert service.dispatch_pending(db) == 0
    assert row.status == "skipped"
    assert row.skip_reason == "not_configured"


def test_message_identity_pagination_and_read(db, people):
    employee, manager = people
    mine = service.enqueue(db, employee_id=employee.id, scene="N5", period="P")
    other = service.enqueue(db, employee_id=manager.id, scene="N6", period="P")
    db.commit()
    summary = asyncio.run(messages.get_messages_summary(current_user=employee, db=db))["data"]
    assert summary["unreadCount"] == 1
    assert summary["unreadMessages"][0]["id"] == mine.id
    result = asyncio.run(messages.list_messages(
        pageNo=1, pageSize=1, page=None, page_size=None, messageType=None,
        unreadOnly=True, current_user=employee, db=db,
    ))["data"]
    assert result["total"] == 1 and result["records"][0]["id"] == mine.id
    with pytest.raises(HTTPException) as error:
        asyncio.run(messages.mark_as_read(other.id, current_user=employee, db=db))
    assert error.value.status_code == 404
    asyncio.run(messages.mark_all_as_read(current_user=employee, db=db))
    db.flush()
    assert mine.read_at is not None and other.read_at is None


def test_release_requires_confirmation_and_score(db, people):
    employee, _ = people
    task = AssessmentTask(id="NT", name="考核", period="202611", status="created")
    db.add(task)
    db.flush()
    record = AssessmentRecord(task_id=task.id, employee_id=employee.id, status="manager_review_submitted", confirmed_rating="A")
    db.add(record)
    db.flush()
    service.record_event(db, record, "N8")
    assert db.query(Notification).count() == 0
    record.status = "confirmed"
    service.record_event(db, record, "N8")
    assert db.query(Notification).count() == 0
    from backend.app.domain.models import Score

    db.add(Score(record_id=record.id, total=80, per_metric_json=[], trace_json={}))
    db.flush()
    service.record_event(db, record, "N8")
    assert db.query(Notification).filter_by(scene="N8").count() == 2


def test_deadline_reminder_and_daily_frequency(db, people, monkeypatch):
    from backend.app.services import notification_scheduler

    employee, _ = people
    db.add(AssessmentTask(id="NT", name="考核", period="202611", status="created", self_review_deadline=datetime.utcnow() + timedelta(days=1)))
    db.flush()
    db.add(AssessmentRecord(task_id="NT", employee_id=employee.id, status="not_started"))
    db.commit()
    # 复用测试会话，不关闭；投递另有独立测试。
    monkeypatch.setattr(notification_scheduler, "get_sessionmaker", lambda: lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(notification_scheduler, "dispatch_pending", lambda db: 0)
    notification_scheduler.run_once()
    notification_scheduler.run_once()
    assert db.query(Notification).filter_by(scene="N3").count() == 1
