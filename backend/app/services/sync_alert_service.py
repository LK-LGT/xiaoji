"""飞书组织同步失败告警。

职责
----
1. **落库**：同步失败/调度器异常 → 写 ``organization_sync_alert``，
   便于运维在接口里查询「最近哪里坏了」。
2. **去重**：同一问题在抑制窗口内只产生一条告警，
   避免「定时同步每轮都失败」刷屏。
3. **通知**：可选地把告警发到飞书 IM。没配置接收人就不发。
4. **恢复**：问题解决后补一条 ``sync_recovered`` info 告警，
   并把对应 open 告警标记为 resolved。

与已验收逻辑的边界
------------------
⚠️ 本模块**只读**同步批次与失败记录，**不修改**任何同步业务数据。
同步主流程、游标、重试、对账逻辑一概不动 —— 这里只是「旁路观察者」。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.models import (
    OrganizationSyncAlert,
    OrganizationSyncBatch,
    OrganizationSyncItemError,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# 告警类型
# ─────────────────────────────────────────────────────────────
ALERT_BATCH_FAILED = "batch_failed"
ALERT_BATCH_PARTIAL_FAILED = "batch_partial_failed"
ALERT_ITEM_ERROR_BACKLOG = "item_error_backlog"
ALERT_SCHEDULER_CONSECUTIVE_FAILURES = "scheduler_consecutive_failures"
ALERT_SYNC_RECOVERED = "sync_recovered"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RESOLVED = "resolved"

# 通知投递状态
NOTIFY_PENDING = "pending"
NOTIFY_SENT = "sent"
NOTIFY_FAILED = "failed"
NOTIFY_DISABLED = "disabled"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _suppress_seconds() -> int:
    return max(0, int(settings.feishu_sync_alert_suppress_seconds or 0))


def _is_suppressed(db: Session, dedup_key: str) -> bool:
    """同一 dedup_key 是否仍在抑制窗口内。"""
    now = _now()
    recent = db.execute(
        select(OrganizationSyncAlert)
        .where(
            OrganizationSyncAlert.dedup_key == dedup_key,
            OrganizationSyncAlert.suppressed_until.is_not(None),
            OrganizationSyncAlert.suppressed_until > now,
        )
        .order_by(OrganizationSyncAlert.created_at.desc())
    ).scalars().first()
    return recent is not None


def create_alert(
    db: Session,
    *,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    dedup_key: str,
    batch_id: str | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> OrganizationSyncAlert | None:
    """创建一条告警（带去重）。

    Returns:
        新建的告警；若被抑制窗口挡住则返回 None。
    """
    if _is_suppressed(db, dedup_key):
        logger.info("告警被抑制窗口去重: type=%s key=%s", alert_type, dedup_key)
        return None

    now = _now()
    alert = OrganizationSyncAlert(
        alert_type=alert_type,
        severity=severity,
        status=STATUS_OPEN,
        title=title[:256],
        message=message,
        batch_id=batch_id,
        dedup_key=dedup_key[:160],
        suppressed_until=now + timedelta(seconds=_suppress_seconds()),
        detail_json=detail,
        notified_status=NOTIFY_PENDING,
    )
    db.add(alert)
    if commit:
        db.commit()
        db.refresh(alert)
    else:
        db.flush()
    logger.warning(
        "同步告警已生成: type=%s severity=%s title=%s", alert_type, severity, title
    )
    return alert


def _open_alert_by_key(db: Session, dedup_key: str) -> OrganizationSyncAlert | None:
    return db.execute(
        select(OrganizationSyncAlert)
        .where(
            OrganizationSyncAlert.dedup_key == dedup_key,
            OrganizationSyncAlert.status.in_((STATUS_OPEN, STATUS_ACKNOWLEDGED)),
        )
        .order_by(OrganizationSyncAlert.created_at.desc())
    ).scalars().first()


def resolve_alert(
    db: Session,
    *,
    dedup_key: str,
    title: str,
    message: str,
    commit: bool = True,
) -> OrganizationSyncAlert | None:
    """把对应未解决的告警标记为 resolved，并补一条 info 恢复告警。

    Returns:
        新建的恢复告警；没有需要恢复的告警时返回 None。
    """
    existing = _open_alert_by_key(db, dedup_key)
    if existing is None:
        return None

    now = _now()
    existing.status = STATUS_RESOLVED
    existing.resolved_at = now

    recovery = OrganizationSyncAlert(
        alert_type=ALERT_SYNC_RECOVERED,
        severity=SEVERITY_INFO,
        status=STATUS_RESOLVED,
        title=title[:256],
        message=message,
        batch_id=existing.batch_id,
        dedup_key=f"{dedup_key}:recovered"[:160],
        suppressed_until=now + timedelta(seconds=_suppress_seconds()),
        detail_json={"originAlertId": existing.id},
        notified_status=NOTIFY_PENDING,
        resolved_at=now,
    )
    db.add(recovery)
    if commit:
        db.commit()
        db.refresh(recovery)
    else:
        db.flush()
    logger.info("同步告警已恢复: key=%s", dedup_key)
    return recovery


# ─────────────────────────────────────────────────────────────
# 业务触发点
# ─────────────────────────────────────────────────────────────


def alert_batch_outcome(db: Session, batch: OrganizationSyncBatch) -> OrganizationSyncAlert | None:
    """批次失败/部分失败时告警；恢复正常时补恢复告警。

    只读批次，不修改同步数据。
    """
    if batch.status == "failed":
        return create_alert(
            db,
            alert_type=ALERT_BATCH_FAILED,
            severity=SEVERITY_ERROR,
            title=f"飞书组织同步失败：{batch.batch_no}",
            message=(
                f"批次 {batch.batch_no} 同步失败。\n"
                f"错误信息：{(batch.error_message or '（无）')[:500]}\n"
                f"错误分类：{batch.error_category or 'unknown'}"
            ),
            dedup_key=f"batch_failed:{batch.batch_no}",
            batch_id=batch.id,
            detail={
                "batchNo": batch.batch_no,
                "mode": batch.mode,
                "triggerType": batch.trigger_type,
                "errorCategory": batch.error_category,
            },
        )

    if batch.status == "partial_failed":
        return create_alert(
            db,
            alert_type=ALERT_BATCH_PARTIAL_FAILED,
            severity=SEVERITY_WARNING,
            title=f"飞书组织同步部分失败：{batch.batch_no}",
            message=(
                f"批次 {batch.batch_no} 部分失败。\n"
                f"部门失败 {batch.departments_failed} 条，员工失败 {batch.employees_failed} 条。\n"
                f"失败记录可在同步失败清单中查看。"
            ),
            dedup_key=f"batch_partial_failed:{batch.batch_no}",
            batch_id=batch.id,
            detail={
                "batchNo": batch.batch_no,
                "departmentsFailed": batch.departments_failed,
                "employeesFailed": batch.employees_failed,
            },
        )

    # 成功/部分成功 → 解除此前的失败告警。
    resolve_alert(
        db,
        dedup_key=f"batch_failed:{batch.batch_no}",
        title=f"飞书组织同步已恢复：{batch.batch_no}",
        message=f"批次 {batch.batch_no} 已恢复正常。",
    )
    return None


def alert_error_backlog(db: Session) -> OrganizationSyncAlert | None:
    """待处理失败记录积压超阈值时告警。"""
    threshold = max(1, int(settings.feishu_sync_alert_error_backlog_threshold or 1))
    pending = db.execute(
        select(func.count(OrganizationSyncItemError.id)).where(
            OrganizationSyncItemError.status.in_(("pending", "dead_letter"))
        )
    ).scalar_one()

    if pending < threshold:
        resolve_alert(
            db,
            dedup_key="item_error_backlog",
            title="同步失败记录积压已恢复",
            message=f"待处理失败记录已回落到 {pending} 条，低于阈值 {threshold} 条。",
        )
        return None

    return create_alert(
        db,
        alert_type=ALERT_ITEM_ERROR_BACKLOG,
        severity=SEVERITY_WARNING,
        title=f"飞书同步失败记录积压 {pending} 条",
        message=(
            f"当前有 {pending} 条待处理/死信的同步失败记录（阈值 {threshold} 条）。\n"
            f"请到「飞书同步 → 失败清单」处理，或调用补偿重试接口。"
        ),
        dedup_key="item_error_backlog",
        detail={"pendingCount": pending, "threshold": threshold},
    )


def alert_scheduler_failures(
    db: Session,
    *,
    consecutive_failures: int,
    last_error: str | None = None,
) -> OrganizationSyncAlert | None:
    """定时同步连续失败达到阈值时告警。"""
    threshold = max(1, int(settings.feishu_sync_alert_consecutive_failures or 1))
    if consecutive_failures < threshold:
        return None

    return create_alert(
        db,
        alert_type=ALERT_SCHEDULER_CONSECUTIVE_FAILURES,
        severity=SEVERITY_ERROR,
        title=f"飞书定时同步已连续失败 {consecutive_failures} 轮",
        message=(
            f"定时同步连续 {consecutive_failures} 轮失败（阈值 {threshold} 轮）。\n"
            f"最近错误：{(last_error or '（无）')[:500]}\n"
            f"请检查飞书应用配置、权限与接口可用性。"
        ),
        dedup_key="scheduler_consecutive_failures",
        detail={
            "consecutiveFailures": consecutive_failures,
            "threshold": threshold,
            "lastError": (last_error or "")[:500],
        },
    )


# ─────────────────────────────────────────────────────────────
# 外部通知（飞书 IM）
# ─────────────────────────────────────────────────────────────


def format_alert_message(alert: OrganizationSyncAlert) -> str:
    """把告警渲染成飞书 IM 文本。"""
    icon = {
        SEVERITY_ERROR: "🔴",
        SEVERITY_WARNING: "🟠",
        SEVERITY_INFO: "🟢",
    }.get(alert.severity, "⚪")
    return f"{icon} {alert.title}\n\n{alert.message}"


def notify_alert(alert: OrganizationSyncAlert, *, client: Any = None) -> bool:
    """把告警发到飞书 IM。

    没有配置接收人时返回 False（不报错 —— 告警已落库，可通过接口查询）。
    """
    receiver = (settings.feishu_sync_alert_receiver_id or "").strip()
    if not receiver:
        logger.info("未配置告警接收人，跳过外部通知: alert_id=%s", alert.id)
        return False

    own_client = client is None
    if own_client:
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            logger.warning("飞书应用未配置，无法发送告警通知")
            return False
        from .feishu_client import FeishuClient

        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )

    try:
        client.send_text_message(
            receiver,
            format_alert_message(alert),
            receive_id_type=settings.feishu_sync_alert_receiver_type or "chat_id",
        )
        logger.info("告警通知已发送: alert_id=%s", alert.id)
        return True
    except Exception as exc:
        # 通知失败**不能**影响同步主流程 —— 记日志即可。
        logger.warning("告警通知发送失败: alert_id=%s error=%s", alert.id, exc)
        return False
    finally:
        if own_client:
            try:
                client.close()
            except Exception:
                pass


def mark_notification(
    db: Session,
    alert: OrganizationSyncAlert,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    """记录通知投递结果。"""
    alert.notified_status = NOTIFY_SENT if success else NOTIFY_FAILED
    alert.notified_at = _now()
    alert.notify_error = (error or "")[:1000] if error else None
    try:
        db.commit()
    except Exception:  # pragma: no cover - 兜底
        db.rollback()
        logger.warning("更新告警通知状态失败: alert_id=%s", alert.id)


__all__ = [
    "ALERT_BATCH_FAILED",
    "ALERT_BATCH_PARTIAL_FAILED",
    "ALERT_ITEM_ERROR_BACKLOG",
    "ALERT_SCHEDULER_CONSECUTIVE_FAILURES",
    "ALERT_SYNC_RECOVERED",
    "SEVERITY_ERROR",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "STATUS_ACKNOWLEDGED",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "alert_batch_outcome",
    "alert_error_backlog",
    "alert_scheduler_failures",
    "create_alert",
    "format_alert_message",
    "mark_notification",
    "notify_alert",
    "resolve_alert",
]
