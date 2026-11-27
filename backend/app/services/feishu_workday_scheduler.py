"""🕘 飞书排班每日同步调度器（2026-11-27）。

⚠️ **默认关闭**（``FEISHU_WORKDAY_SYNC_ENABLED=false``）。
开启后每天固定时刻同步「当月 + 下月」的排班：

- 当月：HR 改动排班后次日生效；
- 下月：提前拉取，未排满时状态 PARTIAL，不会把缺数据判定为“未排班”。

⚠️ **不自建锁表**：同步本身按 (employee_id, work_date) 幂等 upsert，
多实例同时跑只会重复请求飞书，不会写坏数据；如需严格单实例，
可在外层加部署级锁。
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Any

from ..core.config import settings
from ..core.db import get_sessionmaker
from . import feishu_workday_sync

logger = logging.getLogger(__name__)

JOB_NAME = "feishu_workday_sync"

_stop_event = threading.Event()
_thread: threading.Thread | None = None

_STOP_JOIN_TIMEOUT_SECONDS = 5.0
_TICK_SECONDS = 60

_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": "",
    "last_run_date": None,
    "run_count": 0,
    "skip_count": 0,
}


def _set_state(**kwargs: Any) -> None:
    _state.update(kwargs)


def get_scheduler_status() -> dict[str, Any]:
    return {
        "enabled": bool(settings.feishu_workday_sync_enabled),
        "running": bool(_thread and _thread.is_alive()),
        "hour": int(settings.feishu_workday_sync_hour),
        "started": bool(_state.get("started")),
        "lastRunAt": _state["last_run_at"].isoformat() if _state.get("last_run_at") else None,
        "lastFinishedAt": (
            _state["last_finished_at"].isoformat() if _state.get("last_finished_at") else None
        ),
        "lastResult": _state.get("last_result"),
        "lastError": _state.get("last_error") or "",
        "lastRunDate": _state.get("last_run_date"),
        "runCount": int(_state.get("run_count") or 0),
        "skipCount": int(_state.get("skip_count") or 0),
    }


def _next_month(day: date) -> date:
    return date(day.year + (1 if day.month == 12 else 0), 1 if day.month == 12 else day.month + 1, 1)


def run_once(today: date | None = None) -> dict[str, Any]:
    """同步当月 + 下月。失败只记录，不抛给调度线程。"""
    today = today or date.today()
    months = [today.strftime("%Y-%m"), _next_month(today).strftime("%Y-%m")]
    results: list[dict[str, Any]] = []
    session = get_sessionmaker()()
    try:
        for period in months:
            try:
                report = feishu_workday_sync.sync_month(
                    session, period, confirm=True
                )
                results.append(report)
            except Exception as exc:  # noqa: BLE001 - 单月失败不阻断另一月
                logger.exception("飞书排班同步失败：%s", period)
                results.append({"period": period, "status": "FAILED", "error": str(exc)})
    finally:
        session.close()
    return {"today": today.isoformat(), "months": results}


def _should_run(now: datetime) -> bool:
    if _state.get("last_run_date") == now.date().isoformat():
        return False
    return now.hour >= int(settings.feishu_workday_sync_hour)


def _loop() -> None:
    while not _stop_event.is_set():
        now = datetime.now()
        if _should_run(now):
            _set_state(last_run_at=now)
            try:
                result = run_once(now.date())
                _set_state(
                    last_finished_at=datetime.now(),
                    last_result=result,
                    last_error="",
                    last_run_date=now.date().isoformat(),
                    run_count=int(_state.get("run_count") or 0) + 1,
                )
            except Exception as exc:  # noqa: BLE001
                _set_state(
                    last_finished_at=datetime.now(),
                    last_error=str(exc),
                    last_run_date=now.date().isoformat(),
                )
                logger.exception("飞书排班同步调度失败")
        _stop_event.wait(_TICK_SECONDS)


def start() -> None:
    global _thread
    if not settings.feishu_workday_sync_enabled:
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="feishu-workday-sync", daemon=True)
    _thread.start()
    _set_state(started=True)
    logger.info("飞书排班同步调度器已启动（每天 %s 点）", settings.feishu_workday_sync_hour)


def stop() -> None:
    global _thread
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    _thread = None
    _set_state(started=False)
