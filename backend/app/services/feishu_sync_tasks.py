"""飞书组织架构同步任务编排。

职责：
1. **异步执行**：把同步放到后台线程执行，HTTP 请求立即返回批次 ID，
   避免大组织下请求超时、网关断连。
2. **统一互斥**：人工触发与定时触发共用同一把分布式锁
   （``feishu_organization_sync``），保证同一时刻只有一个同步在跑。
3. **幂等**：相同幂等键的重复触发直接复用已有批次，不再新建。

设计取舍：
- 任务在**进程内线程**执行，进程重启会中断任务。
  为此 `OrganizationSyncService.recover_stale_batches()` 会在每次调度
  和每次人工触发前把超时的 ``running`` 批次标记为失败，避免批次永久悬挂。
- 真正跨进程、跨重启的任务队列属于后续演进方向；当前阶段用
  「异步 + 分布式锁 + 超时自愈」满足生产可用性要求。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.models import OrganizationSyncBatch
from .feishu_client import FeishuClient
from .job_lock_service import (
    acquire_job_lock,
    current_holder_id,
    release_job_lock,
)
from .organization_sync import OrganizationSyncService

logger = logging.getLogger(__name__)

# 与定时任务共用同一把锁。
JOB_NAME = "feishu_organization_sync"

# 锁 TTL（秒）。必须大于同步任务的最坏耗时，否则慢任务会被别的实例抢走。
LOCK_TTL_SECONDS = 3600

# 后台任务线程池（简化为一个守护线程 + 队列）。
_task_lock = threading.Lock()
_running_tasks: set[str] = set()


class SyncAlreadyRunningError(Exception):
    """已有同步任务在运行。"""

    def __init__(self, batch_id: str | None = None, message: str | None = None) -> None:
        super().__init__(message or "已有组织架构同步任务在运行，请稍后再试")
        self.batch_id = batch_id


class SyncNotConfiguredError(Exception):
    """飞书应用未配置。"""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "飞书应用配置未完成")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_idempotency_key(
    *,
    mode: str,
    trigger_type: str,
    actor_id: str | None,
) -> str:
    """构造幂等键。

    人工触发按「操作人 + 模式 + 5 分钟窗口」聚合，避免管理员连点
    造成重复同步；定时触发按「调度窗口」聚合，天然幂等。
    """
    if trigger_type == "scheduled":
        window = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")[:-1] + "0"
        return f"scheduled:{window}"
    window = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")[:-1] + "0"
    return f"manual:{actor_id or 'anonymous'}:{mode}:{window}"


def _find_batch_by_idempotency_key(
    db: Session, idempotency_key: str
) -> OrganizationSyncBatch | None:
    return db.execute(
        select(OrganizationSyncBatch).where(
            OrganizationSyncBatch.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()


def _find_running_batch(db: Session) -> OrganizationSyncBatch | None:
    """查找当前运行中的批次（跨实例可见）。"""
    return db.execute(
        select(OrganizationSyncBatch)
        .where(OrganizationSyncBatch.status == "running")
        .order_by(OrganizationSyncBatch.started_at.desc())
    ).scalars().first()


def create_batch(
    db: Session,
    *,
    mode: str,
    trigger_type: str,
    actor_id: str | None,
    idempotency_key: str | None = None,
) -> tuple[OrganizationSyncBatch, bool]:
    """创建同步批次，返回 ``(批次, 是否新建)``。

    若幂等键已存在则复用已有批次；若已有批次处于 ``running``
    则抛出 :class:`SyncAlreadyRunningError`。
    """
    key = idempotency_key or build_idempotency_key(
        mode=mode, trigger_type=trigger_type, actor_id=actor_id
    )

    existing = _find_batch_by_idempotency_key(db, key)
    if existing is not None:
        return existing, False

    running = _find_running_batch(db)
    if running is not None:
        raise SyncAlreadyRunningError(batch_id=running.id)

    batch = OrganizationSyncBatch(
        batch_no=f"ORG-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:10]}",
        status="pending",
        mode=mode,
        trigger_type=trigger_type,
        triggered_by=actor_id,
        idempotency_key=key,
        phase="pending",
        phase_detail="等待执行",
    )
    db.add(batch)
    try:
        db.commit()
    except IntegrityError:
        # 并发下另一个请求插入了相同幂等键的批次。
        db.rollback()
        existing = _find_batch_by_idempotency_key(db, key)
        if existing is not None:
            return existing, False
        raise
    db.refresh(batch)
    return batch, True


def _execute_batch(batch_id: str, *, mode: str, trigger_type: str, actor_id: str | None) -> None:
    """在后台线程中执行同步批次。"""
    from ..core.db import get_sessionmaker

    db = get_sessionmaker()()
    holder_id = current_holder_id()
    locked = False
    try:
        if not acquire_job_lock(db, JOB_NAME, holder_id, ttl_seconds=LOCK_TTL_SECONDS):
            logger.warning("同步任务未获得分布式锁，跳过执行")
            _mark_batch_failed(db, batch_id, "未获得同步锁，可能有其它实例正在执行")
            return
        locked = True

        batch = db.get(OrganizationSyncBatch, batch_id)
        if batch is None:
            logger.warning("同步批次不存在，跳过执行: batch_id=%s", batch_id)
            return
        if batch.status not in ("pending", "running"):
            logger.info("同步批次已处于终态，跳过执行: batch_id=%s status=%s", batch_id, batch.status)
            return

        batch.status = "running"
        batch.phase = "running"
        batch.phase_detail = "正在执行同步"
        batch.started_at = _now()
        db.commit()

        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
        try:
            service = OrganizationSyncService(
                db,
                client,
                mode=mode,
                triggered_by=actor_id,
                trigger_type=trigger_type,
            )
            service.recover_stale_batches()
            result = service.sync_all()
            logger.info(
                "飞书组织同步完成, batch_id=%s status=%s",
                result.get("batch_id"),
                result.get("status"),
            )
        finally:
            client.close()
    except Exception as exc:
        safe_message = OrganizationSyncService._safe_error(exc)
        logger.error("飞书组织同步任务异常: %s", safe_message)
        _mark_batch_failed(db, batch_id, safe_message)
    finally:
        if locked:
            release_job_lock(db, JOB_NAME, holder_id)
        db.close()
        with _task_lock:
            _running_tasks.discard(batch_id)


def _mark_batch_failed(db: Session, batch_id: str, message: str) -> None:
    """把批次标记为失败。"""
    try:
        batch = db.get(OrganizationSyncBatch, batch_id)
        if batch is None:
            return
        if batch.status in ("ok", "partial_failed", "failed"):
            return
        batch.status = "failed"
        batch.error_message = message[:1000]
        batch.phase = "finished"
        batch.phase_detail = None
        batch.finished_at = _now()
        db.commit()
    except Exception:  # pragma: no cover - 兜底
        db.rollback()


def start_async_sync(
    db: Session,
    *,
    mode: str = "full",
    trigger_type: str = "manual",
    actor_id: str | None = None,
) -> tuple[OrganizationSyncBatch, bool]:
    """创建批次并在后台线程执行。

    Returns:
        ``(批次, 是否新建)``。若命中幂等键则复用已有批次且不重复执行。

    Raises:
        SyncNotConfiguredError: 飞书应用未配置。
        SyncAlreadyRunningError: 已有批次在运行。
    """
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise SyncNotConfiguredError()

    batch, created = create_batch(
        db, mode=mode, trigger_type=trigger_type, actor_id=actor_id
    )
    if not created:
        return batch, False

    with _task_lock:
        _running_tasks.add(batch.id)

    thread = threading.Thread(
        target=_execute_batch,
        args=(batch.id,),
        kwargs={
            "mode": mode,
            "trigger_type": trigger_type,
            "actor_id": actor_id,
        },
        name=f"feishu-org-sync-{batch.id[:8]}",
        daemon=True,
    )
    thread.start()
    return batch, True


def get_sync_status(db: Session) -> dict[str, Any]:
    """构造同步链路状态，供前端展示。

    包含：配置状态、定时开关、上次成功时间、当前批次、游标、
    下次计划时间与最近失败摘要。
    """
    configured = bool(settings.feishu_app_id and settings.feishu_app_secret)

    last_success = db.execute(
        select(OrganizationSyncBatch)
        .where(OrganizationSyncBatch.status.in_(("ok", "partial_failed")))
        .order_by(OrganizationSyncBatch.finished_at.desc())
    ).scalars().first()

    last_batch = db.execute(
        select(OrganizationSyncBatch)
        .order_by(OrganizationSyncBatch.started_at.desc())
    ).scalars().first()

    running = _find_running_batch(db)

    from ..domain.models import OrganizationSyncCursor

    cursors = db.execute(select(OrganizationSyncCursor)).scalars().all()

    interval = max(60, settings.feishu_sync_interval_seconds)
    next_run = None
    if settings.feishu_sync_enabled:
        base = last_batch.finished_at if last_batch and last_batch.finished_at else _now()
        next_run = (base + timedelta(seconds=interval)).isoformat()

    return {
        "configured": configured,
        "appIdConfigured": bool(settings.feishu_app_id),
        "appSecretConfigured": bool(settings.feishu_app_secret),
        "tenantIdConfigured": bool(settings.feishu_tenant_id),
        "scheduledEnabled": settings.feishu_sync_enabled,
        "scheduleIntervalSeconds": interval,
        "nextScheduledAt": next_run,
        "running": running is not None,
        "runningBatchId": running.id if running else None,
        "lastSuccessAt": last_success.finished_at.isoformat() if last_success and last_success.finished_at else None,
        "lastBatchId": last_batch.id if last_batch else None,
        "lastBatchStatus": last_batch.status if last_batch else None,
        "lastErrorMessage": last_batch.error_message if last_batch else None,
        "lastErrorCategory": last_batch.error_category if last_batch else None,
        "cursors": [
            {
                "resourceType": cursor.resource_type,
                "checkpointValue": cursor.checkpoint_value,
                "cursorValue": cursor.cursor_value,
                "pageToken": cursor.page_token,
                "snapshotComplete": cursor.snapshot_complete,
                "lastBatchId": cursor.last_batch_id,
                "updatedAt": cursor.updated_at.isoformat() if cursor.updated_at else None,
            }
            for cursor in cursors
        ],
    }
