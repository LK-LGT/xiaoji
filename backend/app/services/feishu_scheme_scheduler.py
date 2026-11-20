"""飞书绩效方案自动同步 —— 定时调度器。

与 ``feishu_scheduler``（组织架构同步）分工：
    ``feishu_scheduler``        → 同步组织架构（部门 / 员工）
    ``feishu_scheme_scheduler`` → 同步绩效方案文档（版本 / 发布 / 发起考核）

调度策略
--------
- **启动即执行一次**：避免「服务刚起来要等一个周期才同步」的空窗。
  首次执行放在后台线程里，不阻塞应用启动。
- **固定间隔**：每轮间隔 ``feishu_scheme_sync_interval_seconds``。
  上一轮**尚未结束**时直接跳过本轮（不排队），避免任务堆积。
- **分布式锁**：多实例部署时靠 ``job_lock_service`` 保证同一时刻
  只有一个实例执行 —— 与人工触发共用同一把锁。

⚠️ **失败必须可见**
同步失败会写进 ``feishu_scheme_sync_batch``，并（配置了接收人时）
发飞书告警。**绝不静默失败** —— 绩效方案没同步 = 员工按旧方案考核，
这是必须让人知道的事。
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from ..core.config import settings
from ..core.db import get_sessionmaker
from ..domain.models import _now

logger = logging.getLogger(__name__)

#: 分布式锁名。与组织架构同步用不同的锁名 —— 两个任务可以并行。
JOB_NAME = "feishu_scheme_sync"

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()

_STOP_JOIN_TIMEOUT_SECONDS = 5.0

_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": "",
    "consecutive_failures": 0,
    "next_run_at": None,
    "run_count": 0,
    "skip_count": 0,
}


def _set_state(**kwargs: Any) -> None:
    _state.update(kwargs)


def get_scheduler_status() -> dict[str, Any]:
    """返回调度器运行态（camelCase，与其它飞书接口一致）。"""
    running = bool(_thread and _thread.is_alive())
    next_run = _state.get("next_run_at")
    return {
        "enabled": bool(settings.feishu_scheme_sync_enabled),
        "running": running,
        "intervalSeconds": max(60, settings.feishu_scheme_sync_interval_seconds),
        "started": bool(_state.get("started")),
        "lastRunAt": _state["last_run_at"].isoformat() if _state.get("last_run_at") else None,
        "lastFinishedAt": (
            _state["last_finished_at"].isoformat() if _state.get("last_finished_at") else None
        ),
        "lastResult": _state.get("last_result"),
        "lastError": _state.get("last_error") or "",
        "consecutiveFailures": int(_state.get("consecutive_failures") or 0),
        "nextRunAt": next_run.isoformat() if next_run else None,
        "runCount": int(_state.get("run_count") or 0),
        "skipCount": int(_state.get("skip_count") or 0),
    }


def run_scheduled_sync() -> dict[str, Any] | None:
    """执行一轮定时同步。

    Returns:
        同步批次摘要；未执行（跳过 / 未配置）时返回 ``None``。
    """
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("飞书方案同步已跳过：飞书应用配置缺失")
        _set_state(last_result="skipped_not_configured")
        return None

    from .feishu_docs import build_docs_client
    from .feishu_scheme_sync import (
        STATUS_SUCCEEDED,
        FeishuSchemeSyncService,
        notify_sync_result,
    )
    from .job_lock_service import acquire_job_lock, current_holder_id, release_job_lock

    db = get_sessionmaker()()
    holder = current_holder_id()
    acquired = False
    try:
        acquired = acquire_job_lock(db, JOB_NAME, holder, ttl_seconds=1800)
        if not acquired:
            logger.info("飞书方案同步跳过：其他实例正在执行")
            _set_state(last_result="skipped_already_running")
            return None

        docs = build_docs_client()
        try:
            service = FeishuSchemeSyncService(db, docs, trigger_type="scheduled")
            batch = service.run()
            db.commit()
        finally:
            docs.client.close()

        summary = {
            "batchId": batch.id,
            "status": batch.status,
            "sourceCount": batch.source_count,
            "changedCount": batch.changed_count,
            "publishedCount": batch.published_count,
            "failedCount": batch.failed_count,
        }
        _set_state(last_result=batch.status, last_error=batch.error_message or "")
        logger.info("飞书方案同步完成: %s", summary)

        # ⚠️ **异常时通知 HR** —— 全部成功不发，避免刷屏。
        if batch.status != STATUS_SUCCEEDED or batch.failed_count:
            try:
                notify_sync_result(batch)
            except Exception:  # pragma: no cover - 通知旁路
                logger.warning("方案同步通知发送失败（不影响同步结果）", exc_info=True)
        return summary
    except Exception as exc:
        db.rollback()
        logger.exception("飞书方案同步轮次异常")
        _set_state(last_result="error", last_error=str(exc)[:500])
        return None
    finally:
        if acquired:
            release_job_lock(db, JOB_NAME, holder)
        db.close()


def _loop(interval_seconds: int) -> None:
    # 启动即跑一次 —— 放后台线程，不阻塞应用启动。
    _run_once(interval_seconds, initial=True)
    while not _stop_event.wait(interval_seconds):
        _run_once(interval_seconds)


def _run_once(interval_seconds: int, *, initial: bool = False) -> None:
    _set_state(last_run_at=_now())
    try:
        with _lock:
            summary = run_scheduled_sync()
    except Exception:  # pragma: no cover - 兜底，调度线程不能死
        logger.exception("飞书方案同步轮次异常")
        summary = None

    finished = _now()
    next_run = finished + timedelta(seconds=interval_seconds)
    if summary is not None:
        _set_state(
            last_finished_at=finished,
            next_run_at=next_run,
            run_count=int(_state.get("run_count") or 0) + 1,
            consecutive_failures=0,
        )
    elif _state.get("last_result") == "skipped_already_running":
        _set_state(
            last_finished_at=finished,
            next_run_at=next_run,
            skip_count=int(_state.get("skip_count") or 0) + 1,
        )
    else:
        _set_state(
            last_finished_at=finished,
            next_run_at=next_run,
            consecutive_failures=int(_state.get("consecutive_failures") or 0) + 1,
        )

    if initial:
        logger.info("飞书方案同步首轮执行完成, 结果=%s", _state.get("last_result"))


def start() -> None:
    """启动调度线程；已启动或未启用时不重复启动。"""
    global _thread
    if not settings.feishu_scheme_sync_enabled:
        logger.info("飞书方案自动同步未启用")
        _set_state(started=False, next_run_at=None)
        return
    if _thread and _thread.is_alive():
        return
    interval = max(60, settings.feishu_scheme_sync_interval_seconds)
    _stop_event.clear()
    _set_state(started=True, next_run_at=_now() + timedelta(seconds=interval))
    _thread = threading.Thread(
        target=_loop, args=(interval,), name="feishu-scheme-sync", daemon=True
    )
    _thread.start()
    logger.info("飞书方案自动同步已启动, 间隔秒数=%s", interval)


def stop() -> None:
    """停止调度线程（幂等，有界等待）。"""
    _stop_event.set()
    _set_state(started=False, next_run_at=None)
    thread = _thread
    if thread is None or not thread.is_alive():
        return
    thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "飞书方案同步线程未在 %.1f 秒内退出，将由 daemon 机制回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )


__all__ = ["JOB_NAME", "get_scheduler_status", "run_scheduled_sync", "start", "stop"]
