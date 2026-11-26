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

每日固定时刻模式（2026-11-25）
------------------------------
``feishu_sync_daily_time = "08:00"`` ⇒ **每天早上 8 点**自动同步。

⚠️ **不能用「间隔 = 86400 秒」代替** —— 间隔模式的触发时刻是
「上次跑完 + 间隔」，会随服务重启**漂移**（今天 8:00 跑过，
明天服务 9 点重启 ⇒ 变成明天 9:00，之后永远 9:00）。
需求「每天早上 8 点」是**业务时刻**，必须按日历算。
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta, timezone
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
    # ⚠️ **每日模式下「今天已经跑过了」的标记**（``YYYY-MM-DD``）。
    #    每日窗口有 10 分钟宽、轮询每分钟一次 ⇒ 不记这个会重复触发。
    "last_daily_run_date": None,
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


#: 每日模式的**本地时区**（UTC+8，Asia/Shanghai）。
#:
#: ⚠️⚠️ **为什么必须显式指定时区** —— 这是本模块最容易被忽略、
#: 且**错了 8 小时还不报错**的坑：
#:
#:   ``_now()`` 返回的是 **UTC**（naive）。若直接拿它跟「早上 8 点」比，
#:   触发时刻会变成 **UTC 08:00 = 本地 16:00** —— 用户配了「每天早上 8 点」，
#:   实际却是**下午 4 点**才跑，而且**不报任何错**（时刻本身合法）。
#:
#:   ⇒ 「每天早上 8 点」是**业务/本地时间**语义，必须先转成本地时间再比。
#:
#: 固定用 UTC+8 而不是系统本地时区：部署机器的 TZ 不受控
#: （容器里常是 UTC），而业务在中国 ⇒ 语义必须稳定。
_LOCAL_TZ = timezone(timedelta(hours=8))


def _local_now() -> datetime:
    """返回**本地时间**（UTC+8）的 naive datetime。

    ⚠️ 与 ``_now()``（UTC）区分清楚：
    - ``_now()``   —— 存库 / 比对时间戳用（与全库口径一致，UTC）；
    - ``_local_now()`` —— **业务时刻**用（「每天早上 8 点」按本地时间算）。
    """
    return datetime.now(_LOCAL_TZ).replace(tzinfo=None)


#: 每日模式的轮询间隔（秒）—— 每分钟检查一次「到点没到」。
#:
#: ⚠️ 为什么不是「睡到 8:00 再醒」：
#:    ``Event.wait(长睡眠)`` 一旦进程被挂起/时钟跳变就可能错过，
#:    而且没法优雅停机。轮询式更简单可靠（与每日推送调度器一致）。
_DAILY_TICK_SECONDS = 60


def _parse_daily_time() -> tuple[int, int] | None:
    """解析 ``feishu_sync_daily_time`` → ``(时, 分)``；未配置则返回 ``None``。

    ⚠️ **配错了退回间隔模式**，而不是让调度器死掉。
    （格式错误已在 ``Settings`` 校验层拦掉，这里只兜语义越界。）
    """
    raw = (settings.feishu_sync_daily_time or "").strip()
    if not raw:
        return None
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        logger.warning("FEISHU_SYNC_DAILY_TIME 格式无效, 退回间隔模式: %r", raw)
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        logger.warning("FEISHU_SYNC_DAILY_TIME 越界, 退回间隔模式: %r", raw)
        return None
    return (hour, minute)


def _daily_window(now: datetime) -> tuple[datetime, datetime]:
    """返回今天的「到点窗口」起止时刻。

    ⚠️ **不是「8:00:00 那一刻」而是「8:00 ~ 8:10」** ——
    服务在 8:00 之后才启动、或调度线程那一刻刚好卡住，
    都不会漏跑当天同步。
    """
    hour, minute = _parse_daily_time() or (8, 0)
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    window = max(1, int(settings.feishu_sync_daily_window_minutes or 10))
    return start, start + timedelta(minutes=window)


def _is_daily_window(now: datetime) -> bool:
    """当前时刻是否落在今天的到点窗口内。"""
    start, end = _daily_window(now)
    return start <= now < end


def _next_daily_run(now: datetime) -> datetime:
    """下一个到点时刻（严格晚于 ``now``）。

    ⚠️ 若当前正好在窗口内 ⇒ 返回**明天**的到点时刻，
    否则今天这一轮已经跑过了，再跑就是重复。
    """
    hour, minute = _parse_daily_time() or (8, 0)
    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < today:
        return today
    return today + timedelta(days=1)


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
    daily = _parse_daily_time()
    return {
        "enabled": bool(settings.feishu_sync_enabled),
        "running": running,
        # ⚠️ **显式暴露当前模式** —— 否则运维看到「nextRunAt」无从判断
        # 到底是「每天 8 点」还是「每 N 小时」。配置漂移一眼可见。
        "mode": "daily" if daily is not None else "interval",
        "dailyTime": f"{daily[0]:02d}:{daily[1]:02d}" if daily else None,
        # ⚠️ **显式暴露时区** —— 「08:00」是**本地时间**（UTC+8）还是 UTC？
        #    不说清楚的话，运维看到「08:00」根本判断不了实际几点跑。
        "timezone": "UTC+8",
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
    """调度主循环（**间隔模式**）。

    ⚠️ **首次执行放在启动时**（而不是等一个完整周期）：
    服务重启/扩缩容后，组织架构应尽快收敛到最新，
    否则最长会有一个周期的数据陈旧窗口。
    """
    # 启动即跑一次 —— 放到后台线程，不阻塞应用启动。
    _run_once(interval_seconds, initial=True)

    while not _stop_event.wait(interval_seconds):
        _run_once(interval_seconds)


def _daily_loop() -> None:
    """调度主循环（**每日固定时刻模式**）。

    ⚠️ **与间隔模式的关键差异**：触发时刻按**日历**算，不是「上次 + 间隔」。
    ⇒ 服务什么时候重启都不会改变「每天 8:00」这个业务语义。

    ⚠️ **同一天只跑一次**：用 ``last_daily_run_date`` 记录当天已跑的日期，
    避免轮询命中窗口时重复触发（窗口有 10 分钟宽，轮询每分钟一次）。
    """
    # 启动即跑一次 —— 保持与间隔模式一致的行为（服务起来就收敛一次）。
    # ⚠️ **用本地时间**：判断「今天跑过没有」必须按业务日历（UTC+8），
    #    否则 UTC 跨日时点会算错日期。
    now = _local_now()
    # ⚠️ **「启动即跑一次」必须尊重「当天只跑一次」这道闸** ——
    # 否则服务在 8:05 重启 ⇒ 首轮又跑一次 ⇒ 当天同步了两次。
    # 与间隔模式的差异：间隔模式的「启动即跑」是**收敛**语义（多跑一次无害，
    # 只是提前把数据拉齐）；每日模式的重复同步是**浪费 + 打接口**。
    #
    # ⇒ 只有「今天还没跑过」时才跑首轮；已经跑过就只更新 next_run_at。
    if _get_state().get("last_daily_run_date") != now.date().isoformat():
        _run_once(_DAILY_TICK_SECONDS, initial=True, next_run_at=_next_daily_run(now))

    while not _stop_event.wait(_DAILY_TICK_SECONDS):
        now = _local_now()
        if not _is_daily_window(now):
            continue
        current = _get_state()
        today = now.date().isoformat()
        if current.get("last_daily_run_date") == today:
            # 今天已经跑过了 —— 窗口还没过，但不该重复跑。
            _set_state(next_run_at=_next_daily_run(now))
            continue
        _set_state(last_daily_run_date=today)
        _run_once(_DAILY_TICK_SECONDS, next_run_at=_next_daily_run(now))


def _run_once(
    interval_seconds: int,
    *,
    initial: bool = False,
    next_run_at: datetime | None = None,
) -> None:
    """跑一轮定时同步，并更新运行态。

    Args:
        interval_seconds: 间隔模式下「距上次跑完」的秒数（每日模式下仅用于
            兜底计算下次执行时间）。
        initial: 是否是启动后的首轮（首轮不计入连续失败）。
        next_run_at: **下一次执行的绝对时刻**（每日模式按日历算出来的）。
            传了就用它覆盖「上次跑完 + 间隔」的算法。
    """
    started_at = _now()
    _set_state(last_run_at=started_at)
    # ⚠️ **每日模式下，轮次一开跑就锁上「当天只跑一次」的闸** ——
    # 否则「启动即跑一次」的那轮不会留下痕迹，调度线程重启后
    # 会以为今天还没跑过 ⇒ 同一天跑第二遍。
    # 锁闸时机取「开跑」而非「成功」：失败也要算今天跑过，
    # 否则失败 + 重启 ⇒ 无限重试打爆飞书接口。
    if _parse_daily_time() is not None:
        _set_state(last_daily_run_date=started_at.date().isoformat())
    try:
        with _lock:
            submitted = run_scheduled_sync()
    except Exception:  # pragma: no cover - 兜底，调度线程不能死
        logger.exception("定时同步轮次异常")
        submitted = False

    finished_at = _now()
    next_run = next_run_at or (finished_at + timedelta(seconds=interval_seconds))
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

    # ⚠️ **模式分派：每日固定时刻 vs 间隔** ——
    # 配了 ``FEISHU_SYNC_DAILY_TIME`` 就必须走每日模式，
    # 否则用户会以为「每天 8 点自动同步」生效了，实际跑的是间隔模式
    # （触发时刻会随服务重启漂移）—— 那种「看起来生效其实没生效」的坑
    # 比启动失败难查得多。
    daily = _parse_daily_time()
    if daily is not None:
        hour, minute = daily
        _stop_event.clear()
        _set_state(
            started=True,
            next_run_at=_next_daily_run(_local_now()),
        )
        _scheduler_thread = threading.Thread(
            target=_daily_loop,
            name="feishu-organization-sync-daily",
            daemon=True,
        )
        _scheduler_thread.start()
        logger.info(
            "飞书组织架构定时同步已启动, 模式=每日固定时刻, 每日时间=%02d:%02d, 窗口=%s 分钟",
            hour,
            minute,
            settings.feishu_sync_daily_window_minutes,
        )
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
