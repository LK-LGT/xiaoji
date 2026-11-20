"""周期调度后台线程（P1-2）。

与 ``notification_scheduler`` 分工：
- ``notification_scheduler``：投递已有的通知发件箱 + 既有 N3/N4 提醒。
- 本模块：**推进考核周期状态**（自动开启任务）+ 投递周期倒计时/逾期提醒。

多实例部署时靠数据库分布式锁（``job_lock_service``）保证同一时刻只有一个
实例执行；进程内锁只用于避免同进程重复并发。
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

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()

#: 停止时等待线程退出的上限（秒）。不无限等：当前轮次可能在写库。
_STOP_JOIN_TIMEOUT_SECONDS = 5.0

_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_result": None,
    "consecutive_failures": 0,
    "run_count": 0,
    "next_run_at": None,
}


def run_once() -> dict[str, Any]:
    """执行一轮周期调度（自动开启 + 提醒投递）。"""
    from . import period_scheduler

    db = get_sessionmaker()()
    try:
        with _lock:
            cycle = period_scheduler.run_cycle(db)
            reminders = period_scheduler.deliver_reminders(db)
        db.commit()
        return {**cycle, **reminders}
    except Exception:
        db.rollback()
        logger.exception("周期调度轮次失败")
        raise
    finally:
        db.close()


def _loop(interval_seconds: int) -> None:
    while not _stop.wait(interval_seconds):
        try:
            result = run_once()
            _update_state(result=result)
        except Exception:  # pragma: no cover - 兜底，调度线程不能死
            _update_state(failed=True)


def _update_state(*, result: dict[str, Any] | None = None, failed: bool = False) -> None:
    now = _now()
    _state["last_run_at"] = now
    if failed:
        _state["last_result"] = "error"
        _state["consecutive_failures"] = int(_state.get("consecutive_failures") or 0) + 1
    else:
        _state["last_result"] = "ok"
        _state["consecutive_failures"] = 0
        _state["run_count"] = int(_state.get("run_count") or 0) + 1
    _state["next_run_at"] = now + timedelta(
        seconds=max(60, settings.period_scheduler_interval_seconds)
    )


def get_scheduler_status() -> dict[str, Any]:
    """返回周期调度运行态（供运维排查）。"""
    running = bool(_thread and _thread.is_alive())
    next_run = _state.get("next_run_at")
    return {
        "enabled": bool(settings.period_scheduler_enabled),
        "running": running,
        "intervalSeconds": max(60, settings.period_scheduler_interval_seconds),
        "started": bool(_state.get("started")),
        "lastRunAt": _state["last_run_at"].isoformat() if _state.get("last_run_at") else None,
        "lastResult": _state.get("last_result"),
        "consecutiveFailures": int(_state.get("consecutive_failures") or 0),
        "nextRunAt": next_run.isoformat() if next_run else None,
        "runCount": int(_state.get("run_count") or 0),
    }


def start() -> None:
    """启动周期调度线程；已启动或未启用时不重复启动。"""
    global _thread
    if not settings.period_scheduler_enabled:
        logger.info("考核周期调度未启用")
        _state["started"] = False
        return
    if _thread and _thread.is_alive():
        return
    interval = max(60, settings.period_scheduler_interval_seconds)
    _stop.clear()
    _state["started"] = True
    _state["next_run_at"] = _now() + timedelta(seconds=interval)
    _thread = threading.Thread(
        target=_loop, args=(interval,), name="period-scheduler", daemon=True
    )
    _thread.start()
    logger.info("考核周期调度已启动, 间隔秒数=%s", interval)


def stop() -> None:
    """停止周期调度线程（幂等，有界等待）。"""
    _stop.set()
    _state["started"] = False
    thread = _thread
    if thread is None or not thread.is_alive():
        return
    thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "周期调度线程未在 %.1f 秒内退出（可能正在执行一轮），将由 daemon 机制回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )
