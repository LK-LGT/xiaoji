"""百度网盘绩效校验 —— 定时调度器（BP-1）。

与 ``feishu_scheme_scheduler`` 分工：
    ``feishu_scheme_scheduler`` → 同步飞书绩效方案文档
    ``baidu_pan_scheduler``     → 拉网盘成片文件数，落计数快照

调度策略
--------
- **启动即执行一次**：避免「服务刚起来要等一个周期才同步」的空窗。
- **固定间隔**：默认 6 小时（``baidu_pan_sync_interval_seconds``）。
  百度接口有频控（未审核应用 10 次/小时），**不能调太密**。
- **分布式锁**：用独立的锁名 ``baidu_pan_sync`` ——
  与飞书方案同步可以并行，互不阻塞。

⚠️ **失败必须可见**
取数失败写进 ``baidu_pan_sync_batch``。**绝不静默失败** ——
网盘数不出来 = 主管拿不到核对依据，这是必须让人知道的事。

⚠️ **未授权时静默跳过**
没授权就不跑 —— 这是**预期状态**，不是错误，不该刷告警。
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

#: 分布式锁名 —— 与飞书方案同步用不同的锁名，两个任务可以并行。
JOB_NAME = "baidu_pan_sync"

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
    """返回调度器运行态（camelCase）。"""
    running = bool(_thread and _thread.is_alive())
    next_run = _state.get("next_run_at")
    return {
        "enabled": bool(settings.baidu_pan_enabled),
        "running": running,
        "intervalSeconds": max(3600, settings.baidu_pan_sync_interval_seconds),
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
    """执行一轮定时取数。

    Returns:
        批次摘要；未执行（未授权 / 未配置 / 已在运行）时返回 ``None``。
    """
    if not settings.baidu_pan_app_key or not settings.baidu_pan_app_secret:
        logger.info("百度网盘取数已跳过：凭证未配置")
        _set_state(last_result="skipped_not_configured")
        return None

    from .baidu_pan_client import BaiduPanAuthError
    from .baidu_pan_service import BaiduPanService
    from .job_lock_service import acquire_job_lock, current_holder_id, release_job_lock

    db = get_sessionmaker()()
    holder = current_holder_id()
    acquired = False
    try:
        service = BaiduPanService(db)
        try:
            # ⚠️ **未授权是预期状态**（还没走授权流程）—— 静默跳过，不算失败。
            if not service.is_authorized():
                logger.info("百度网盘取数已跳过：尚未授权")
                _set_state(last_result="skipped_not_authorized")
                return None
        finally:
            service.close()

        acquired = acquire_job_lock(db, JOB_NAME, holder, ttl_seconds=3600)
        if not acquired:
            logger.info("百度网盘取数跳过：其他实例正在执行")
            _set_state(last_result="skipped_already_running")
            return None

        service = BaiduPanService(db)
        try:
            batch = service.run_sync(trigger_type="scheduled")
            db.commit()
        finally:
            service.close()

        summary = {
            "batchId": batch.id,
            "status": batch.status,
            "scannedFiles": batch.scanned_files,
            "countedFiles": batch.counted_files,
            "excludedFiles": batch.excluded_files,
            "errorMessage": batch.error_message,
        }
        _set_state(last_result=batch.status, last_error=batch.error_message or "")
        logger.info("百度网盘取数完成: %s", summary)
        return summary
    except BaiduPanAuthError as exc:
        # ⚠️ **授权失效** —— access_token 刷新不了，需要人工重新授权。
        #    这**必须**让人知道（否则校验数据会一直停更）。
        db.rollback()
        logger.warning("百度网盘授权失效，需重新授权: %s", exc)
        _set_state(last_result="auth_expired", last_error=str(exc)[:500])
        return None
    except Exception as exc:
        db.rollback()
        logger.exception("百度网盘取数轮次异常")
        _set_state(last_result="error", last_error=str(exc)[:500])
        return None
    finally:
        if acquired:
            release_job_lock(db, JOB_NAME, holder)
        db.close()


def _loop(interval_seconds: int) -> None:
    _run_once(interval_seconds, initial=True)
    while not _stop_event.wait(interval_seconds):
        _run_once(interval_seconds)


def _run_once(interval_seconds: int, *, initial: bool = False) -> None:
    _set_state(last_run_at=_now())
    try:
        with _lock:
            summary = run_scheduled_sync()
    except Exception:  # pragma: no cover - 兜底，调度线程不能死
        logger.exception("百度网盘取数轮次异常")
        summary = None

    finished = _now()
    next_run = finished + timedelta(seconds=interval_seconds)
    last_result = _state.get("last_result")
    if summary is not None:
        _set_state(
            last_finished_at=finished,
            next_run_at=next_run,
            run_count=int(_state.get("run_count") or 0) + 1,
            consecutive_failures=0,
        )
    elif last_result in ("skipped_already_running", "skipped_not_authorized",
                         "skipped_not_configured"):
        # ⚠️ **跳过不算失败** —— 未授权是预期状态，不该刷告警。
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
        logger.info("百度网盘取数首轮执行完成, 结果=%s", last_result)


def start() -> None:
    """启动调度线程；已启动或未启用时不重复启动。"""
    global _thread
    if not settings.baidu_pan_enabled:
        logger.info("百度网盘绩效校验未启用")
        _set_state(started=False, next_run_at=None)
        return
    if _thread and _thread.is_alive():
        return
    # ⚠️ **下限 1 小时** —— 百度接口有频控（未审核应用 10 次/小时）。
    interval = max(3600, settings.baidu_pan_sync_interval_seconds)
    _stop_event.clear()
    _set_state(started=True, next_run_at=_now() + timedelta(seconds=interval))
    _thread = threading.Thread(
        target=_loop, args=(interval,), name="baidu-pan-sync", daemon=True
    )
    _thread.start()
    logger.info("百度网盘绩效校验已启动, 间隔秒数=%s", interval)


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
            "百度网盘取数线程未在 %.1f 秒内退出，将由 daemon 机制回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )


__all__ = ["JOB_NAME", "get_scheduler_status", "run_scheduled_sync", "start", "stop"]
