"""通知后台任务：每日频控提醒与发件箱投递。"""

import logging
import threading
from datetime import timedelta

from ..core.config import settings
from ..core.db import get_sessionmaker
from ..domain.models import AssessmentRecord, AssessmentTask
from .notification_service import delivery_scope, dispatch_pending, now, record_event

logger = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None


def run_once() -> None:
    db = get_sessionmaker()()
    try:
        current = now()
        rows = db.query(AssessmentRecord).join(AssessmentTask).filter(
            AssessmentTask.status.notin_(["confirmed", "archived", "draft"]),
        ).all()
        for record in rows:
            task = record.task
            deadline = task.self_review_deadline
            if record.status in {"not_started", "self_review_open"} and deadline:
                if current <= deadline <= current + timedelta(days=3):
                    record_event(db, record, "N3", event_key=current.date().isoformat())
            if record.status == "self_review_submitted":
                record_event(db, record, "N4", event_key=current.date().isoformat())
        from .review_delivery import dispatch as dispatch_review
        from .review_delivery import reminders
        reminders(db)
        dispatch_review(db)
        db.commit()
        dispatch_pending(db)
    except Exception:
        db.rollback()
        logger.exception("通知后台任务失败")
    finally:
        db.close()


def _loop() -> None:
    while not _stop.wait(30):
        run_once()


def start() -> None:
    global _thread
    if not settings.notification_enabled or (_thread and _thread.is_alive()):
        return
    if delivery_scope() is None:
        logger.warning("通知投递未启动：缺少有效的灰度员工、场景或发件箱记录配置")
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="notification-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
