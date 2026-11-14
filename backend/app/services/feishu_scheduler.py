"""飞书组织架构定时同步任务。

多实例部署时通过数据库分布式锁（app.services.job_lock_service）保证同一时刻
只有一个实例执行同步。进程内锁仅用于避免同一进程重复并发。
"""

from __future__ import annotations

import logging
import threading

from ..core.config import settings
from ..core.db import get_sessionmaker
from ..services.feishu_client import FeishuClient
from ..services.job_lock_service import (
    acquire_job_lock,
    current_holder_id,
    release_job_lock,
)
from ..services.organization_sync import OrganizationSyncService

logger = logging.getLogger(__name__)

# 分布式锁中的任务名
JOB_NAME = "feishu_organization_sync"

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()
_lock = threading.Lock()


def run_scheduled_sync() -> None:
    """执行一次定时同步。多实例下由分布式锁保证单实例执行。"""
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("定时同步已跳过：飞书应用配置缺失")
        return

    holder_id = current_holder_id()
    db = get_sessionmaker()()
    locked = False
    try:
        # TTL 需大于同步任务最坏耗时，否则慢任务会被其他实例抢占。
        if not acquire_job_lock(db, JOB_NAME, holder_id):
            logger.info("定时同步跳过：其他实例已持有锁, holder=%s", holder_id)
            return
        locked = True

        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
        try:
            service = OrganizationSyncService(
                db,
                client,
                mode="incremental",
                triggered_by=None,
                trigger_type="scheduled",
            )
            recovered = service.recover_stale_batches()
            if recovered:
                logger.warning("已恢复 %s 个超时同步批次", recovered)
            result = service.sync_all()
            logger.info(
                "定时飞书同步完成, batch_id=%s status=%s",
                result.get("batch_id"),
                result.get("status"),
            )
        finally:
            client.close()
    except Exception as exc:
        # 脱敏后再写日志：原始异常可能包含 App Secret / Access Token。
        safe_message = OrganizationSyncService._safe_error(exc)
        logger.error("定时飞书同步异常: %s", safe_message)
    finally:
        if locked:
            release_job_lock(db, JOB_NAME, holder_id)
        db.close()


def _loop(interval_seconds: int) -> None:
    while not _stop_event.wait(interval_seconds):
        with _lock:
            run_scheduled_sync()


def start_scheduler() -> None:
    """启动定时任务；已启动时不会重复启动。"""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    if not settings.feishu_sync_enabled:
        logger.info("飞书定时同步未启用")
        return
    interval = max(60, settings.feishu_sync_interval_seconds)
    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_loop,
        args=(interval,),
        name="feishu-organization-sync",
        daemon=True,
    )
    _scheduler_thread.start()
    logger.info("飞书组织架构定时同步已启动, 间隔秒数=%s", interval)


def stop_scheduler() -> None:
    _stop_event.set()
