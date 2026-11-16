"""组织架构事件队列消费调度器（R18）。

后台定时扫描事件队列并消费。

⚠️ **与全量同步用不同的锁**
--------------------------------
两者是**不同**的锁：

- ``feishu_organization_sync`` —— 全量/增量同步的锁；
- ``feishu_event_consume`` —— 事件队列消费的锁。

⚠️ **为什么不能共用一把锁**：
共用会导致「事件消费时，全量同步被挡住」或反之 ——
两个任务本该**并行**（事件走快路径，全量走兜底对账），
共用锁会让其中一个一直排队，事件变更迟迟不生效。

⚠️ **事件消费者不需要分布式锁吗？**
需要 —— 多实例部署时每个实例都会跑这个调度器，
不加锁会导致**同一批事件被多个实例重复消费**。
重复消费本身是幂等的（upsert），但会浪费飞书 API 配额
（每次消费都要回查飞书），且可能造成批次记录混乱。
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from ..core.config import settings
from ..core.db import get_sessionmaker
from .feishu_client import FeishuClient
from .job_lock_service import acquire_job_lock, current_holder_id, release_job_lock
from .org_change_consumer import OrgChangeConsumer
from .org_change_queue import queue_stats

logger = logging.getLogger(__name__)

#: 事件消费用的锁名（与全量同步的锁**不同**，见模块说明）。
JOB_NAME = "feishu_event_consume"

#: 锁 TTL（秒）。消费一批事件可能要回查飞书，给足时间。
LOCK_TTL_SECONDS = 600

_consumer_thread: threading.Thread | None = None
_stop_event = threading.Event()

# 停止调度器时等待线程退出的上限（秒）。
_STOP_JOIN_TIMEOUT_SECONDS = 5.0

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_finished_at": None,
    "last_result": None,
    "consecutive_failures": 0,
    "next_run_at": None,
    "run_count": 0,
    "processed_count": 0,
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


def _get_state() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def get_consumer_status() -> dict[str, Any]:
    """事件消费者运行态（运维视图）。"""
    current = _get_state()
    stats: dict[str, int] = {}
    try:
        db = get_sessionmaker()()
        try:
            stats = queue_stats(db)
        finally:
            db.close()
    except Exception:  # pragma: no cover - 状态查询失败不该影响主流程
        logger.warning("读取事件队列统计失败", exc_info=True)

    return {
        "enabled": bool(settings.feishu_event_consume_enabled),
        "running": bool(_consumer_thread and _consumer_thread.is_alive()),
        "started": bool(current.get("started")),
        "intervalSeconds": max(10, settings.feishu_event_consume_interval_seconds),
        "lastRunAt": (
            current["last_run_at"].isoformat() if current.get("last_run_at") else None
        ),
        "lastFinishedAt": (
            current["last_finished_at"].isoformat()
            if current.get("last_finished_at")
            else None
        ),
        "lastResult": current.get("last_result"),
        "consecutiveFailures": int(current.get("consecutive_failures") or 0),
        "nextRunAt": (
            current["next_run_at"].isoformat() if current.get("next_run_at") else None
        ),
        "runCount": int(current.get("run_count") or 0),
        "processedCount": int(current.get("processed_count") or 0),
        "queue": stats,
    }


def consume_once() -> dict[str, int] | None:
    """执行一轮消费。

    Returns:
        消费统计；未获得锁时返回 ``None``（表示本轮跳过）。
    """
    holder_id = current_holder_id()
    db = get_sessionmaker()()
    locked = False
    try:
        if not acquire_job_lock(db, JOB_NAME, holder_id, ttl_seconds=LOCK_TTL_SECONDS):
            # 另一个实例在消费 —— 本轮跳过，不排队。
            logger.debug("未获得事件消费锁，跳过本轮")
            return None
        locked = True

        if not settings.feishu_app_id or not settings.feishu_app_secret:
            logger.warning("飞书应用配置未完成，跳过事件消费")
            return {"processed": 0, "failed": 0, "dead_letter": 0}

        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
        consumer = OrgChangeConsumer(db, client)
        result = consumer.process_pending()
        return result
    finally:
        if locked:
            release_job_lock(db, JOB_NAME, holder_id)
        db.close()


def _run_once(interval: int) -> None:
    """执行一轮并更新运行态。"""
    started_at = _now()
    _set_state(last_run_at=started_at)
    current = _get_state()
    next_run = started_at + timedelta(seconds=interval)
    _set_state(next_run_at=next_run)

    try:
        result = consume_once()
        if result is None:
            _set_state(
                last_finished_at=_now(),
                next_run_at=next_run,
                last_result="skipped_locked",
            )
            return

        processed = int(result.get("processed") or 0)
        failed = int(result.get("failed") or 0)
        _set_state(
            last_finished_at=_now(),
            next_run_at=next_run,
            last_result=result,
            run_count=int(current.get("run_count") or 0) + 1,
            processed_count=int(current.get("processed_count") or 0) + processed,
            consecutive_failures=0 if processed or not failed else int(
                current.get("consecutive_failures") or 0
            )
            + 1,
        )
        if processed:
            logger.info("事件消费完成: 处理 %s 条, 失败 %s 条", processed, failed)
    except Exception as exc:
        logger.exception("事件消费轮次失败: %s", exc)
        _set_state(
            last_finished_at=_now(),
            next_run_at=next_run,
            last_result={"error": str(exc)[:500]},
            consecutive_failures=int(current.get("consecutive_failures") or 0) + 1,
        )


def _loop(interval: int) -> None:
    """调度循环。"""
    # 首轮稍等一下 —— 让应用完成启动，避免与数据库迁移抢锁。
    if _stop_event.wait(5.0):
        return
    while not _stop_event.is_set():
        _run_once(interval)
        if _stop_event.wait(interval):
            break
    logger.info("飞书事件消费调度器已退出")


def start_consumer() -> None:
    """启动事件消费调度器；已启动时不会重复启动。"""
    global _consumer_thread
    if _consumer_thread and _consumer_thread.is_alive():
        return
    if not settings.feishu_event_consume_enabled:
        logger.info("飞书事件消费未启用")
        _set_state(started=False, next_run_at=None)
        return
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.info("飞书应用配置未完成，事件消费调度器不启动")
        _set_state(started=False, next_run_at=None)
        return

    interval = max(10, settings.feishu_event_consume_interval_seconds)
    _stop_event.clear()
    _set_state(started=True, next_run_at=_now() + timedelta(seconds=interval))
    _consumer_thread = threading.Thread(
        target=_loop,
        args=(interval,),
        name="feishu-event-consumer",
        daemon=True,
    )
    _consumer_thread.start()
    logger.info("飞书事件消费调度器已启动, 间隔秒数=%s", interval)


def stop_consumer() -> None:
    """停止事件消费调度器（幂等，可重复调用）。

    ⚠️ 只置停止事件 + **有界等待**，不强杀：
    当前轮次可能正在回查飞书并落库，强制中断会打断写入。
    """
    _stop_event.set()
    _set_state(started=False, next_run_at=None)

    thread = _consumer_thread
    if thread is None or not thread.is_alive():
        return
    thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "飞书事件消费线程未在 %.1f 秒内退出（可能正在消费一批事件），"
            "将由 daemon 机制在进程退出时回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )


__all__ = [
    "JOB_NAME",
    "consume_once",
    "get_consumer_status",
    "start_consumer",
    "stop_consumer",
]
