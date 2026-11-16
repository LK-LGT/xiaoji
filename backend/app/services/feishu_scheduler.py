"""飞书组织架构定时同步任务。

多实例部署时通过数据库分布式锁（app.services.job_lock_service）保证同一时刻
只有一个实例执行同步。进程内锁仅用于避免同一进程重复并发。

人工触发与定时触发共用同一把锁与同一套执行逻辑
（见 `app.services.feishu_sync_tasks`），避免出现两套行为。
"""

from __future__ import annotations

import logging
import threading

from ..core.config import settings
from ..core.db import get_sessionmaker
from ..services.feishu_sync_tasks import (
    JOB_NAME,
    SyncAlreadyRunningError,
    start_async_sync,
)

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()
_lock = threading.Lock()


def run_scheduled_sync() -> None:
    """执行一次定时同步。

    与人工触发走同一条路径：创建批次 → 抢分布式锁 → 后台执行。
    已有同步在运行时直接跳过（而不是排队），避免任务堆积。
    """
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("定时同步已跳过：飞书应用配置缺失")
        return

    db = get_sessionmaker()()
    try:
        batch, created = start_async_sync(
            db, mode="incremental", trigger_type="scheduled", actor_id=None
        )
        if not created:
            logger.info("定时同步跳过：已有批次在运行或幂等命中, batch_id=%s", batch.id)
    except SyncAlreadyRunningError as exc:
        logger.info("定时同步跳过：已有同步在运行, batch_id=%s", exc.batch_id)
    except Exception as exc:
        safe_message = str(exc)[:500]
        logger.error("定时同步异常: %s", safe_message)
    finally:
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


__all__ = ["JOB_NAME", "run_scheduled_sync", "start_scheduler", "stop_scheduler"]
