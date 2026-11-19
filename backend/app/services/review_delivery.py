"""复核事件消费：站内通知、提醒、汇总失效与可见的外部待办。"""
from sqlalchemy import select, update

from ..domain.models import AssessmentRecord, Employee, HrDepartmentConfig, Rollup, _now
from ..domain.notification_models import Notification
from ..domain.plan_models import DisputeCase, DisputeReviewer
from ..domain.review_models import ReviewOutbox
from .review_center import TERMINAL, digest, emit


def notify(db, event, recipient_id, title, content):
    employee = db.get(Employee, recipient_id)
    if not employee or employee.account_status != "enabled":
        return
    key = digest({"review_event": event.event_key, "recipient": recipient_id})
    if db.scalar(select(Notification.id).where(Notification.dedup_key == key)):
        return
    db.add(Notification(employee_id=recipient_id, scene="R1", period="review", dedup_key=key, title=title, content=content, message_type="NOTICE", object_type="review", object_id=event.payload.get("case_id", event.payload.get("record_id", "")), route="/employee/review-center"))


def hr_ids(db):
    departments = db.scalars(select(HrDepartmentConfig.department_id).where(HrDepartmentConfig.active.is_(True))).all()
    return db.scalars(select(Employee.id).where(Employee.department_id.in_(departments), Employee.employment_status == "active", Employee.account_status == "enabled")).all()


def dispatch(db, limit=50):
    rows = db.scalars(select(ReviewOutbox).where(ReviewOutbox.status.in_(["PENDING", "FAILED"]), ReviewOutbox.attempts < 10).order_by(ReviewOutbox.created_at).limit(limit)).all()
    for row in rows:
        event_id = row.id
        try:
            with db.begin_nested():
                claimed = db.execute(update(ReviewOutbox).where(ReviewOutbox.id == row.id, ReviewOutbox.status.in_(["PENDING", "FAILED"]), ReviewOutbox.attempts == row.attempts).values(status="PROCESSING", attempts=row.attempts + 1), execution_options={"synchronize_session": False})
                if claimed.rowcount != 1:
                    continue
                db.refresh(row)
                recipients = set()
                if row.payload.get("employee_id"):
                    recipients.add(row.payload["employee_id"])
                case = db.get(DisputeCase, row.payload.get("case_id")) if row.payload.get("case_id") else None
                if case:
                    recipients.update(hr_ids(db))
                    recipients.update(db.scalars(select(DisputeReviewer.user_id).where(DisputeReviewer.case_id == case.id, DisputeReviewer.recusal_status == "NONE")).all())
                if row.topic == "score.adjusted":
                    rec = db.get(AssessmentRecord, row.payload["record_id"])
                    employee = db.get(Employee, rec.employee_id)
                    # 持久化聚合失效，后续查询必须重新生成；不能保留旧总分。
                    db.query(Rollup).filter(Rollup.period == row.payload["period"], ((Rollup.scope == "company") | ((Rollup.scope == "employee") & (Rollup.scope_id == employee.id)) | ((Rollup.scope == "department") & (Rollup.scope_id == employee.department_id)))).delete(synchronize_session=False)
                    for recipient in recipients:
                        notify(db, row, recipient, "绩效调整已生效", "绩效结果已更新，详情与版本记录可在复核中心查看；奖金结算仍需单独确认。")
                    row.status = "MANUAL_REQUIRED"
                    row.last_error = "绩效结果已更新，汇总缓存已失效；未接入奖金结算接口，请人工核查补差或追偿，不能视为已结算。"
                elif row.topic.startswith("evidence."):
                    row.status = "DELIVERED"
                else:
                    if row.topic == "adjustment.requested":
                        recipients.update(hr_ids(db))
                    for recipient in recipients:
                        notify(db, row, recipient, "复核事项进展", "复核案件或分数调整有新进展，请进入复核与校准中心查看。")
                    row.status = "DELIVERED"
                db.flush()
        except Exception as exc:
            current = db.get(ReviewOutbox, event_id)
            current.status = "FAILED"
            current.attempts += 1
            current.last_error = str(exc)[:500]
    db.flush()


def reminders(db):
    today = _now().date().isoformat()
    rows = db.scalars(select(DisputeCase).where(DisputeCase.deadline_at < _now(), DisputeCase.status.notin_(TERMINAL))).all()
    for case in rows:
        key = f"case:{case.id}:overdue:{today}"
        if not db.scalar(select(ReviewOutbox.id).where(ReviewOutbox.event_key == key)):
            emit(db, key, "case.overdue", {"case_id": case.id, "employee_id": case.owner_id, "action": "ESCALATED_TO_HR"})
