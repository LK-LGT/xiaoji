"""飞书端「每日填报提醒」推送调度器（2026-11-23）。

⚠️ **推送时机**
--------------
每天**固定时刻**推一次（默认 17:30，下班前）。
⚠️ **一天只推一次** —— 用「推送记录表」的日期唯一约束保证，
不是靠内存状态（多实例会重复推）。

⚠️ **推给谁**
------------
1. **剪辑本人** —— 一张「表格 + 表单」卡，直接在飞书里填完提交。
2. **主管** —— 一张**只读**汇总卡（「谁还没填」）。

⚠️ **主管卡不给表单**
--------------------
主管能替下属填 = 「A 的产出记到 B 头上」重新变成可能。
⇒ 主管卡只有表格，填报必须由员工本人提交。

⚠️ **板块隔离**
--------------
推送对象必须**所在部门开通了「剪辑工作量」板块** ——
⇒ 仓储部的人**绝不会**收到剪辑填报提醒。
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any

from ..core.config import settings
from ..core.db import get_sessionmaker
from ..domain.models import _now

logger = logging.getLogger(__name__)

#: 分布式锁名 —— 与其它同步任务互不阻塞。
JOB_NAME = "feishu_daily_metric_push"

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()

_STOP_JOIN_TIMEOUT_SECONDS = 5.0

#: 轮询间隔（秒）—— 每分钟检查一次「到点没到」。
_TICK_SECONDS = 60

_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": "",
    "consecutive_failures": 0,
    "run_count": 0,
    "skip_count": 0,
}


def _set_state(**kwargs: Any) -> None:
    _state.update(kwargs)


def get_scheduler_status() -> dict[str, Any]:
    """返回调度器运行态（camelCase）。"""
    running = bool(_thread and _thread.is_alive())
    return {
        "enabled": bool(settings.feishu_daily_push_enabled),
        "running": running,
        "pushTime": settings.feishu_daily_push_time,
        "started": bool(_state.get("started")),
        "lastRunAt": _state["last_run_at"].isoformat() if _state.get("last_run_at") else None,
        "lastFinishedAt": (
            _state["last_finished_at"].isoformat() if _state.get("last_finished_at") else None
        ),
        "lastResult": _state.get("last_result"),
        "lastError": _state.get("last_error") or "",
        "consecutiveFailures": int(_state.get("consecutive_failures") or 0),
        "runCount": int(_state.get("run_count") or 0),
        "skipCount": int(_state.get("skip_count") or 0),
    }


def _parse_push_time() -> tuple[int, int]:
    """解析配置的推送时刻 → ``(时, 分)``。

    ⚠️ **配错了不能让调度器死掉** —— 退回默认 17:30。
    """
    raw = (settings.feishu_daily_push_time or "").strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        return (17, 30)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return (17, 30)
    return (hour, minute)


def _is_push_window(now: datetime) -> bool:
    """当前时刻是否该推。

    ⚠️ **不是「到点那一刻」而是「到点后的 10 分钟窗口」**
    ⇒ 服务重启晚了、或者那一分钟调度线程刚好卡住，
    都不会漏推当天的提醒。
    """
    hour, minute = _parse_push_time()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target <= now < target + timedelta(minutes=10)


def run_daily_push(day: str = "") -> dict[str, Any]:
    """执行一轮推送。

    Args:
        day: 推送哪一天（``YYYY-MM-DD``）。留空 = 今天。

    Returns:
        推送摘要 ``{"day":..., "editors":..., "leaders":..., "skipped":...}``。
    """
    from .feishu_daily_push import push_daily_metrics

    db = get_sessionmaker()()
    try:
        target = day or date.today().isoformat()
        result = push_daily_metrics(db, target)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        logger.exception("每日填报推送异常")
        _set_state(last_result="error", last_error=str(exc)[:500])
        raise
    finally:
        db.close()


def _loop() -> None:
    while not _stop_event.wait(_TICK_SECONDS):
        try:
            with _lock:
                now = _now()
                if not _is_push_window(now):
                    continue
                from .feishu_daily_push import already_pushed_today

                db = get_sessionmaker()()
                try:
                    if already_pushed_today(db, now.date().isoformat()):
                        _set_state(
                            last_result="skipped_already_pushed",
                            skip_count=int(_state.get("skip_count") or 0) + 1,
                        )
                        continue
                    result = run_daily_push(now.date().isoformat())
                finally:
                    db.close()
                _set_state(
                    last_run_at=now,
                    last_finished_at=_now(),
                    last_result="pushed",
                    last_error="",
                    run_count=int(_state.get("run_count") or 0) + 1,
                    consecutive_failures=0,
                )
                logger.info("每日填报推送完成: %s", result)
        except Exception:  # pragma: no cover - 兜底，调度线程不能死
            logger.exception("每日填报推送轮次异常")
            _set_state(
                last_result="error",
                consecutive_failures=int(_state.get("consecutive_failures") or 0) + 1,
            )


def start() -> None:
    """启动调度线程；已启动时不重复启动。"""
    global _thread
    if not settings.feishu_daily_push_enabled:
        logger.info("每日填报推送未启用")
        _set_state(started=False)
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _set_state(started=True)
    _thread = threading.Thread(
        target=_loop, name="feishu-daily-metric-push", daemon=True
    )
    _thread.start()
    logger.info(
        "每日填报推送已启动, 推送时刻=%s", settings.feishu_daily_push_time
    )


def stop() -> None:
    """停止调度线程（幂等，有界等待）。"""
    _stop_event.set()
    _set_state(started=False)
    thread = _thread
    if thread is None or not thread.is_alive():
        return
    thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "每日填报推送线程未在 %.1f 秒内退出，将由 daemon 机制回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )


__all__ = [
    "JOB_NAME",
    "get_scheduler_status",
    "run_daily_push",
    "start",
    "stop",
]
