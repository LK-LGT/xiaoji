"""飞书组织架构定时同步任务。

多实例部署时通过数据库分布式锁（app.services.job_lock_service）保证同一时刻
只有一个实例执行同步。进程内锁仅用于避免同一进程重复并发。

人工触发与定时触发共用同一把锁与同一套执行逻辑
（见 `app.services.feishu_sync_tasks`），避免出现两套行为。

调度策略
--------
- **启动即执行一次**：避免「服务刚起来要等一个周期才同步」的空窗；
  首次执行放在后台线程里，不阻塞应用启动。
- **固定间隔**：每轮间隔 ``feishu_sync_interval_seconds``。
  上一次同步**尚未结束**时直接跳过本轮（而不是排队），
  避免任务堆积 —— 与人工触发共用同一把分布式锁。
- **可观测**：``get_scheduler_status()`` 暴露运行态、下次执行时间、
  连续失败次数，供前端与运维查看。
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

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

# 停止调度器时等待线程退出的上限（秒）。
# 不无限等：当前轮次可能正在同步，强制中断会打断批次落库。
_STOP_JOIN_TIMEOUT_SECONDS = 5.0

# ─────────────────────────────────────────────────────────────
# 调度运行态（进程内，仅用于可观测性，不参与业务判定）
# ─────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_finished_at": None,
    "last_result": None,
    "consecutive_failures": 0,
    "next_run_at": None,
    "run_count": 0,
    "skip_count": 0,
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


def _get_state() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def get_scheduler_status() -> dict[str, Any]:
    """返回定时调度器的运行态（供状态接口与运维排查）。

    字段全部是 camelCase，与其它飞书接口契约一致。
    """
    current = _get_state()
    running = bool(_scheduler_thread and _scheduler_thread.is_alive())
    next_run = current.get("next_run_at")
    return {
        "enabled": bool(settings.feishu_sync_enabled),
        "running": running,
        "intervalSeconds": max(60, settings.feishu_sync_interval_seconds),
        "started": bool(current.get("started")),
        "lastRunAt": current.get("last_run_at").isoformat() if current.get("last_run_at") else None,
        "lastFinishedAt": current.get("last_finished_at").isoformat() if current.get("last_finished_at") else None,
        "lastResult": current.get("last_result"),
        "consecutiveFailures": int(current.get("consecutive_failures") or 0),
        "nextRunAt": next_run.isoformat() if next_run else None,
        "runCount": int(current.get("run_count") or 0),
        "skipCount": int(current.get("skip_count") or 0),
    }


def run_scheduled_sync() -> bool:
    """执行一次定时同步。

    与人工触发走同一条路径：创建批次 → 抢分布式锁 → 后台执行。
    已有同步在运行时直接跳过（而不是排队），避免任务堆积。

    Returns:
        True 表示本轮**成功提交**了一个同步批次；False 表示被跳过或失败。
    """
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("定时同步已跳过：飞书应用配置缺失")
        _set_state(last_result="skipped_not_configured")
        return False

    db = get_sessionmaker()()
    try:
        batch, created = start_async_sync(
            db, mode="incremental", trigger_type="scheduled", actor_id=None
        )
        if not created:
            logger.info("定时同步跳过：已有批次在运行或幂等命中, batch_id=%s", batch.id)
            _set_state(last_result="skipped_already_running")
            return False
        logger.info("定时同步已提交, batch_id=%s batch_no=%s", batch.id, batch.batch_no)
        _set_state(last_result="submitted")
        return True
    except SyncAlreadyRunningError as exc:
        logger.info("定时同步跳过：已有同步在运行, batch_id=%s", exc.batch_id)
        _set_state(last_result="skipped_already_running")
        return False
    except Exception as exc:
        safe_message = str(exc)[:500]
        logger.error("定时同步异常: %s", safe_message)
        _set_state(last_result="error", last_error=safe_message)
        return False
    finally:
        db.close()


def _loop(interval_seconds: int) -> None:
    """调度主循环。

    ⚠️ **首次执行放在启动时**（而不是等一个完整周期）：
    服务重启/扩缩容后，组织架构应尽快收敛到最新，
    否则最长会有一个周期的数据陈旧窗口。
    """
    # 启动即跑一次 —— 放到后台线程，不阻塞应用启动。
    _run_once(interval_seconds, initial=True)

    while not _stop_event.wait(interval_seconds):
        _run_once(interval_seconds)


def _run_once(interval_seconds: int, *, initial: bool = False) -> None:
    """跑一轮定时同步，并更新运行态。"""
    started_at = _now()
    _set_state(last_run_at=started_at)
    try:
        with _lock:
            submitted = run_scheduled_sync()
    except Exception:  # pragma: no cover - 兜底，调度线程不能死
        logger.exception("定时同步轮次异常")
        submitted = False

    finished_at = _now()
    next_run = finished_at + timedelta(seconds=interval_seconds)
    current = _get_state()

    if submitted:
        _set_state(
            last_finished_at=finished_at,
            next_run_at=next_run,
            run_count=int(current.get("run_count") or 0) + 1,
            consecutive_failures=0,
            last_error=None,
        )
    elif current.get("last_result") == "skipped_already_running":
        # 跳过不算失败，但也不清零连续失败计数 —— 连续跳过同样值得关注。
        _set_state(
            last_finished_at=finished_at,
            next_run_at=next_run,
            skip_count=int(current.get("skip_count") or 0) + 1,
        )
    else:
        _set_state(
            last_finished_at=finished_at,
            next_run_at=next_run,
            consecutive_failures=int(current.get("consecutive_failures") or 0) + 1,
        )

    if initial:
        logger.info("飞书定时同步首轮执行完成, 结果=%s", current.get("last_result"))

    _maybe_alert_scheduler_failures()


def _maybe_alert_scheduler_failures() -> None:
    """连续失败达阈值时生成告警（旁路，失败不影响调度）。"""
    try:
        current = _get_state()
        failures = int(current.get("consecutive_failures") or 0)
        if failures <= 0:
            return

        from ..core.db import get_sessionmaker
        from .sync_alert_service import (
            alert_scheduler_failures,
            mark_notification,
            notify_alert,
        )

        db = get_sessionmaker()()
        try:
            alert = alert_scheduler_failures(
                db,
                consecutive_failures=failures,
                last_error=current.get("last_error"),
            )
            if alert is not None:
                success = notify_alert(alert)
                mark_notification(db, alert, success=success)
        finally:
            db.close()
    except Exception:  # pragma: no cover - 告警旁路兜底
        logger.warning("生成调度器失败告警失败（不影响调度）", exc_info=True)


def start_scheduler() -> None:
    """启动定时任务；已启动时不会重复启动。"""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    if not settings.feishu_sync_enabled:
        logger.info("飞书定时同步未启用")
        _set_state(started=False, next_run_at=None)
        return
    interval = max(60, settings.feishu_sync_interval_seconds)
    _stop_event.clear()
    _set_state(started=True, next_run_at=_now() + timedelta(seconds=interval))
    _scheduler_thread = threading.Thread(
        target=_loop,
        args=(interval,),
        name="feishu-organization-sync",
        daemon=True,
    )
    _scheduler_thread.start()
    logger.info("飞书组织架构定时同步已启动, 间隔秒数=%s", interval)


def stop_scheduler() -> None:
    """停止定时任务（幂等，可重复调用）。

    ⚠️ 只置停止事件 + **有界等待**，不强杀：
    当前这一轮同步可能正在执行（拉飞书接口 + 落库，可能几十秒），
    强制中断会打断批次落库，留下「批次停在 running」的脏状态。

    等待超时后直接返回 —— 线程是 daemon，进程退出时会被自动回收。
    """
    _stop_event.set()
    _set_state(started=False, next_run_at=None)

    thread = _scheduler_thread
    if thread is None or not thread.is_alive():
        return
    # 停止事件最长要等一个轮次间隔才会被 `_loop` 的 wait 唤醒，
    # 这里只等一小段时间确认线程确实退出，不无限等。
    thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "飞书定时同步线程未在 %.1f 秒内退出（可能正在执行一轮同步），"
            "将由 daemon 机制在进程退出时回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )


__all__ = [
    "JOB_NAME",
    "get_scheduler_status",
    "run_scheduled_sync",
    "start_scheduler",
    "stop_scheduler",
]
