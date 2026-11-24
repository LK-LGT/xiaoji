"""事务内写发件箱，事务外发送；失败可查、可重推。"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.models import AssessmentRecord, AssessmentTask, Employee, Score
from ..domain.notification_models import Notification
from .notification_templates import SCENES, render

logger = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def enqueue(
    db: Session, *, employee_id: str, scene: str, period: str,
    object_id: str = "", object_type: str = "", label: str = "",
    event_key: str = "", due_at: datetime | None = None,
) -> Notification | None:
    """同一业务事件只写一次；提醒用日期作为 event_key，形成每日频控。

    ⚠️ 命中去重时返回**已存在的行**（不是 None）。需要区分「新建」与
       「已存在」的调用方请用 :func:`enqueue_new`。
    """
    employee = db.get(Employee, employee_id)
    if employee is None or employee.employment_status != "active" or employee.account_status != "enabled":
        return None
    if scene in {"N3", "N4"} and not event_key:
        event_key = now().date().isoformat()
    key = hashlib.sha256(f"{employee_id}|{scene}|{period}|{object_id}|{event_key}".encode()).hexdigest()
    existing = db.query(Notification).filter_by(dedup_key=key).first()
    if existing:
        return existing
    title, content, message_type, route = render(scene, label)
    if scene == "N5" and object_type == "week_plan":
        route = "/employee/week-plans"
    elif scene == "N4":
        route = "/performance/manager-reviews"
    if object_type == 'assessment_record':
        record = db.get(AssessmentRecord, object_id)
        task = db.get(AssessmentTask, record.task_id) if record else None
        if task and task.snapshot_id:
            from ..domain.models import ConfigSnapshot
            snapshot = db.get(ConfigSnapshot, task.snapshot_id)
            if snapshot and (snapshot.form_template_json or {}).get('scheme_version_id'):
                route = '/performance/scheme-filling'
    # 保存通知失败不污染业务事务；唯一键同时兜住并发触发。
    try:
        with db.begin_nested():
            row = Notification(
                employee_id=employee_id, scene=scene, period=period, dedup_key=key,
                title=title, content=content, message_type=message_type,
                object_id=object_id, object_type=object_type, route=route, due_at=due_at,
            )
            db.add(row)
            db.flush()
        return row
    except Exception:
        logger.warning("通知写入失败：scene=%s object=%s", scene, object_id, exc_info=True)
        return None


def enqueue_safe(db: Session, **kwargs: Any) -> Notification | None:
    try:
        return enqueue(db, **kwargs)
    except Exception:
        logger.warning("通知旁路失败：scene=%s", kwargs.get("scene"), exc_info=True)
        return None


def enqueue_new(db: Session, **kwargs: Any) -> Notification | None:
    """仅当**新建**通知时返回行；命中去重（已存在）时返回 ``None``。

    与 :func:`enqueue` 的区别：``enqueue`` 命中去重时返回已存在的行，
    调用方无法区分「这条是我刚写的」还是「之前就有了」。
    每日频控类调用（如周期调度提醒）需要这个区分，
    否则重复投递会被误计为「已发送」，导致频控失效。
    """
    employee_id = kwargs.get("employee_id")
    scene = kwargs.get("scene")
    period = kwargs.get("period", "")
    object_id = kwargs.get("object_id", "")
    event_key = kwargs.get("event_key", "")
    if scene in {"N3", "N4"} and not event_key:
        event_key = now().date().isoformat()
    key = hashlib.sha256(
        f"{employee_id}|{scene}|{period}|{object_id}|{event_key}".encode()
    ).hexdigest()
    if db.query(Notification).filter_by(dedup_key=key).first():
        return None
    return enqueue_safe(db, **kwargs)


def record_event(db: Session, record: AssessmentRecord, scene: str, *, event_key: str = "") -> None:
    task = db.get(AssessmentTask, record.task_id)
    employee = db.get(Employee, record.employee_id)
    if not task or not employee:
        return
    if scene == "N8" and (
        record.status != "confirmed" or not record.confirmed_rating
        or db.query(Score).filter_by(record_id=record.id).first() is None
    ):
        return
    recipients = [employee.id]
    if scene == "N4":
        recipients = [employee.manager_id] if employee.manager_id else []
    elif scene == "N8" and employee.manager_id:
        recipients.append(employee.manager_id)
    for recipient in recipients:
        enqueue_safe(
            db, employee_id=recipient, scene=scene, period=task.id,
            object_id=record.id, object_type="assessment_record",
            label=task.name, event_key=event_key,
            due_at=task.self_review_deadline if scene == "N3" else None,
        )


def delivery_scope() -> tuple[str, str, str] | None:
    employee_id = settings.notification_canary_employee_id.strip()
    scene = settings.notification_canary_scene.strip()
    outbox_id = settings.notification_canary_outbox_id.strip()
    if settings.notification_enabled and employee_id and scene in SCENES and outbox_id:
        return employee_id, scene, outbox_id
    return None


def _send_rating_attachment(
    db: Session,
    sender: Any,
    receive_id: str,
    receive_type: str,
    record: AssessmentRecord,
) -> None:
    """生成并发送绩效评定表附件（FS-6）。

    ⚠️ **两步**：先上传拿 ``file_key``，再发 file 消息。
    ⚠️ **附件格式**由 ``settings.feishu_rating_attachment_type`` 决定
    （默认 ``xlsx``；PDF 需要 ``reportlab`` 依赖，缺失时自动降级为 Excel）。
    """
    from .feishu_rating_export import build_rating_attachment

    export = build_rating_attachment(
        db,
        record,
        file_type=settings.feishu_rating_attachment_type,
    )
    file_key = sender.upload_file(
        export.filename,
        export.content,
        file_type=export.file_type,
    )
    sender.send_file_message(
        receive_id,
        file_key,
        receive_id_type=receive_type,
        title=export.filename,
    )
    logger.info(
        "绩效评定表附件已发送：record=%s file=%s type=%s",
        record.id,
        export.filename,
        export.file_type,
    )


def dispatch_pending(db: Session, *, client: Any = None, limit: int = 50) -> int:
    """仅处理获准员工和场景的已提交发件箱。"""
    scope = delivery_scope()
    if scope is None:
        return 0
    from .feishu_approval_sync import employee_feishu_receive_id
    from .feishu_client import FeishuClient

    employee_id, scene, outbox_id = scope
    scope_filter = (
        Notification.employee_id == employee_id,
        Notification.scene == scene,
        Notification.id == outbox_id,
    )
    # 进程退出后结果不明的发送交人工重推，避免自动重复发给员工。
    db.execute(update(Notification).where(
        *scope_filter,
        Notification.status == "sending",
        Notification.claimed_at < now() - timedelta(minutes=30),
    ).values(status="failed", last_error="delivery_outcome_unknown"))
    db.commit()
    ids = [r.id for r in db.query(Notification).filter(*scope_filter).filter_by(status="pending")
           .limit(limit).all()]
    sent = 0
    for notification_id in ids:
        # 抢占用条件更新：只有把 pending 改成 sending 的那个实例才继续，
        # 多实例同时投递时不会重复发送同一条。
        claimed = db.execute(update(Notification).where(
            *scope_filter,
            Notification.id == notification_id,
            Notification.status == "pending",
        ).values(status="sending", attempts=Notification.attempts + 1, claimed_at=now()))
        db.commit()
        if claimed.rowcount != 1:
            continue
        row = db.get(Notification, notification_id)
        employee = db.get(Employee, row.employee_id)
        receive_id, receive_type = employee_feishu_receive_id(employee) if employee else ("", "open_id")
        own_client = None
        # ⚠️ N8（绩效等级已发布）也要拿记录 —— FS-6 要用它生成评定表附件。
        record = (
            db.get(AssessmentRecord, row.object_id)
            if row.scene in {"N3", "N4", "N8"}
            else None
        )
        obsolete = (row.scene == "N3" and (not record or record.status not in {"not_started", "self_review_open"})) or (
            row.scene == "N4" and (not record or record.status != "self_review_submitted")
        )
        if obsolete:
            row.status, row.skip_reason = "skipped", "task_completed"
            row.read_at = row.read_at or now()
        elif not employee or employee.employment_status != "active" or employee.account_status != "enabled":
            row.status, row.skip_reason = "skipped", "inactive_employee"
        elif not receive_id:
            row.status, row.skip_reason = "skipped", "missing_identity"
        elif client is None and (not settings.feishu_app_id or not settings.feishu_app_secret):
            row.status, row.skip_reason = "skipped", "not_configured"
        else:
            content = f"【{row.title}】\n{row.content}"
            db.commit()
            try:
                sender = client
                if sender is None:
                    own_client = FeishuClient(
                        app_id=settings.feishu_app_id, app_secret=settings.feishu_app_secret,
                        tenant_id=settings.feishu_tenant_id,
                    )
                    sender = own_client
                sender.send_text_message(receive_id, content, receive_id_type=receive_type)
                # ⚠️ **附件是独立的第二条消息**（FS-6）
                #
                # 飞书的消息类型**互斥** —— 卡片（interactive）不能带 file_key，
                # 文件（file）不能带正文。
                # ⇒ 要「一句话 + 一个附件」就**必须发两条消息**。
                #
                # ⚠️ **附件失败不推翻整条通知** —— 文字已经送到了，
                #    附件是「锦上添花」。附件挂了就记 warning，主消息仍算 sent。
                if row.scene == "N8" and record is not None:
                    try:
                        _send_rating_attachment(
                            db, sender, receive_id, receive_type, record
                        )
                    except Exception as exc:  # noqa: BLE001 - 附件失败不影响主消息
                        logger.warning(
                            "绩效评定表附件发送失败（不影响主消息）：record=%s err=%s",
                            record.id,
                            exc,
                        )
                row.status, row.sent_at, row.last_error = "sent", now(), None
                sent += 1
            except Exception as exc:
                # 不保存异常正文，防止上游异常带出凭据或请求内容。
                row.status, row.last_error = "failed", type(exc).__name__
            finally:
                if own_client:
                    own_client.close()
        db.commit()
    return sent


def serialize(row: Notification) -> dict:
    return {
        "id": row.id, "messageType": row.message_type, "sceneCode": row.scene,
        "title": row.title, "content": row.content,
        "status": "READ" if row.read_at else "UNREAD",
        "objectType": row.object_type, "objectId": row.object_id,
        "route": row.route, "menuPath": row.route,
        "createdAt": row.created_at.isoformat(),
        "readAt": row.read_at.isoformat() if row.read_at else None,
        "dueAt": row.due_at.isoformat() if row.due_at else None,
        "overdue": bool(row.due_at and row.due_at < now()),
    }
