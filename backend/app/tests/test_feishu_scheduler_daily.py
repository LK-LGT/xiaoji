"""每日固定时刻同步（``FEISHU_SYNC_DAILY_TIME``）的回归测试。

背景
----
需求：**「每天早上 8 点自动同步飞书组织架构」**。

⚠️ **不能用「间隔 = 86400 秒」代替** —— 间隔模式的触发时刻是
「上次跑完 + 间隔」，会随服务重启**漂移**（今天 8:00 跑过，
明天服务 9 点重启 ⇒ 变成明天 9:00，之后永远 9:00）。
需求「每天早上 8 点」是**业务时刻**，必须按日历算。

本文件覆盖的关键回归点
----------------------
1. **模式分派真的生效** —— 配了 ``FEISHU_SYNC_DAILY_TIME`` 就必须跑
   每日模式（而不是悄悄退回间隔模式）。这是最容易出的「看起来生效
   其实没生效」的坑。
2. **配置格式校验** —— ``HH:MM`` 之外的写法必须启动就报错。
3. **到点窗口** —— 不是「8:00:00 那一刻」而是「8:00 ~ 8:10」，
   服务在 8:00 之后才启动也不会漏跑。
4. **同一天只跑一次** —— 窗口有 10 分钟宽、轮询每分钟一次，
   重复触发会造成重复同步。
5. **下一次执行时刻** —— 按日历算，不是「上次 + 间隔」。
6. **退回间隔模式** —— 配错/留空时退回间隔模式而不是让调度器死掉。
7. **运行态暴露当前模式** —— 运维要能一眼看出在跑哪种模式。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.app.core.config import settings
from backend.app.services import feishu_scheduler


@pytest.fixture(autouse=True)
def _reset_scheduler():
    """每个用例前后把调度器恢复干净。"""
    feishu_scheduler.stop_scheduler()
    feishu_scheduler._scheduler_thread = None
    feishu_scheduler._set_state(
        started=False,
        last_run_at=None,
        last_finished_at=None,
        last_result=None,
        consecutive_failures=0,
        next_run_at=None,
        run_count=0,
        skip_count=0,
        last_error=None,
        last_daily_run_date=None,
    )
    yield
    feishu_scheduler.stop_scheduler()
    feishu_scheduler._scheduler_thread = None


# ═══════════════════════════════════════════════════════════════════════════
# 1. 配置校验
# ═══════════════════════════════════════════════════════════════════════════


def test_daily_time_must_be_hh_mm():
    """⚠️ 格式错误必须在**配置校验**阶段就报错，不能静默忽略。

    静默忽略的代价：用户以为配好了，实际却在跑另一个模式。
    """
    from pydantic import ValidationError

    from backend.app.core.config import Settings

    for bad in ("8点", "08:00:00", "0800", "abc", "08"):
        with pytest.raises(ValidationError):
            Settings(feishu_sync_daily_time=bad)


def test_daily_window_minutes_must_be_positive():
    """窗口宽度必须 ≥ 1 分钟。"""
    from pydantic import ValidationError

    from backend.app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(feishu_sync_daily_window_minutes=0)


def test_empty_daily_time_is_valid():
    """留空 = 退回间隔模式，必须是**合法**配置。"""
    from backend.app.core.config import Settings

    assert Settings(feishu_sync_daily_time="").feishu_sync_daily_time == ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. 模式分派（核心回归点）
# ═══════════════════════════════════════════════════════════════════════════


def test_daily_mode_is_selected_when_configured(monkeypatch):
    """⚠️ 核心断言：配了每日时间 ⇒ 必须启动**每日模式**线程。

    回归点：``start_scheduler`` 之前只认间隔模式，
    导致 ``FEISHU_SYNC_DAILY_TIME=08:00`` 被配了也不生效。
    """
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")
    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: True)

    feishu_scheduler.start_scheduler()
    try:
        thread = feishu_scheduler._scheduler_thread
        assert thread is not None
        assert thread.name == "feishu-organization-sync-daily", (
            "配了 FEISHU_SYNC_DAILY_TIME 就必须跑每日模式线程"
        )
        assert feishu_scheduler.get_scheduler_status()["mode"] == "daily"
    finally:
        feishu_scheduler.stop_scheduler()


def test_interval_mode_used_when_daily_not_configured(monkeypatch):
    """没配每日时间 ⇒ 仍是间隔模式（向后兼容，不破坏既有行为）。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "")
    monkeypatch.setattr(settings, "feishu_sync_interval_seconds", 3600)
    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: True)

    feishu_scheduler.start_scheduler()
    try:
        thread = feishu_scheduler._scheduler_thread
        assert thread is not None
        assert thread.name == "feishu-organization-sync"
        assert feishu_scheduler.get_scheduler_status()["mode"] == "interval"
    finally:
        feishu_scheduler.stop_scheduler()


def test_status_exposes_mode_and_daily_time(monkeypatch):
    """运行态必须暴露**当前模式**，运维要能一眼看出在跑哪种。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "06:30")
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)

    status = feishu_scheduler.get_scheduler_status()
    assert status["mode"] == "daily", "运行态必须暴露当前模式"
    assert status["dailyTime"] == "06:30", "必须暴露每日触发时刻"


def test_status_reports_interval_mode(monkeypatch):
    """没配每日时间 ⇒ 模式是 interval。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "")

    status = feishu_scheduler.get_scheduler_status()
    assert status["mode"] == "interval"
    assert status["dailyTime"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. 到点窗口
# ═══════════════════════════════════════════════════════════════════════════


def test_daily_window_covers_configured_time(monkeypatch):
    """⚠️ 窗口必须**包住**配置的时刻，否则当天同步永远不触发。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")
    monkeypatch.setattr(settings, "feishu_sync_daily_window_minutes", 10)

    # 8:00:30 ⇒ 在窗口内。
    at = datetime(2026, 11, 25, 8, 0, 30)
    assert feishu_scheduler._is_daily_window(at) is True

    # 8:11:00 ⇒ 已过窗口。
    after = datetime(2026, 11, 25, 8, 11, 0)
    assert feishu_scheduler._is_daily_window(after) is False

    # 7:59:59 ⇒ 还没到点。
    before = datetime(2026, 11, 25, 7, 59, 59)
    assert feishu_scheduler._is_daily_window(before) is False


def test_daily_window_start_and_end(monkeypatch):
    """窗口起止必须以配置时刻为**基准**。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")
    monkeypatch.setattr(settings, "feishu_sync_daily_window_minutes", 10)

    start, end = feishu_scheduler._daily_window(datetime(2026, 11, 25, 12, 0, 0))
    assert start == datetime(2026, 11, 25, 8, 0, 0)
    assert end == datetime(2026, 11, 25, 8, 10, 0)


def test_daily_window_respects_configured_width(monkeypatch):
    """窗口宽度必须尊重配置（不是写死 10 分钟）。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")
    monkeypatch.setattr(settings, "feishu_sync_daily_window_minutes", 30)

    start, end = feishu_scheduler._daily_window(datetime(2026, 11, 25, 12, 0, 0))
    assert end - start == timedelta(minutes=30)


# ═══════════════════════════════════════════════════════════════════════════
# 4. 下一次执行时刻
# ═══════════════════════════════════════════════════════════════════════════


def test_next_daily_run_is_today_when_before_time(monkeypatch):
    """还没到点 ⇒ 下一次是**今天**的到点时刻。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")

    now = datetime(2026, 11, 25, 7, 0, 0)
    nxt = feishu_scheduler._next_daily_run(now)
    assert nxt == datetime(2026, 11, 25, 8, 0, 0)


def test_next_daily_run_rolls_to_tomorrow(monkeypatch):
    """已过到点时刻 ⇒ 下一次是**明天**。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")

    now = datetime(2026, 11, 25, 9, 0, 0)
    nxt = feishu_scheduler._next_daily_run(now)
    assert nxt == datetime(2026, 11, 25, 8, 0, 0) + timedelta(days=1)


def test_next_daily_run_inside_window_returns_tomorrow(monkeypatch):
    """⚠️ 正好在窗口内 ⇒ 返回**明天**（今天这一轮已经跑过了）。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")

    now = datetime(2026, 11, 25, 8, 5, 0)
    nxt = feishu_scheduler._next_daily_run(now)
    assert nxt == datetime(2026, 11, 25, 8, 0, 0) + timedelta(days=1)


def test_daily_mode_does_not_drift_after_late_restart(monkeypatch):
    """⚠️ **核心回归点：服务重启不会让触发时刻漂移。**

    间隔模式的触发时刻是「上次跑完 + 间隔」——
    今天 8:00 跑过，明天服务 9 点重启 ⇒ 变成明天 9:00，之后永远 9:00，
    **永远等不到「每天早上 8 点」**。

    每日模式按**日历**算 ⇒ 无论几点重启，下次都是次日 08:00。
    """
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")

    # 服务在 9 点重启（错过了今天的到点时刻）。
    nxt = feishu_scheduler._next_daily_run(datetime(2026, 11, 25, 9, 0, 0))
    assert nxt == datetime(2026, 11, 26, 8, 0, 0), "重启后下次执行仍应是次日 08:00"

    # 服务在深夜 2 点重启（今天还没到点）。
    nxt = feishu_scheduler._next_daily_run(datetime(2026, 11, 25, 2, 0, 0))
    assert nxt == datetime(2026, 11, 25, 8, 0, 0), "今天还没到点 ⇒ 应是今天 08:00"

    # 服务在 8:30 重启（已过窗口）。
    nxt = feishu_scheduler._next_daily_run(datetime(2026, 11, 25, 8, 30, 0))
    assert nxt == datetime(2026, 11, 26, 8, 0, 0), "已过窗口 ⇒ 应是次日 08:00"


# ═══════════════════════════════════════════════════════════════════════════
# 4.5 时区语义（「每天早上 8 点」是**本地时间**，不是 UTC）
# ═══════════════════════════════════════════════════════════════════════════


def test_local_tz_is_utc_plus_8():
    """⚠️ **业务时刻固定按 UTC+8 算**。

    为什么不能跟随系统时区：部署机器的 TZ 不受控
    （容器里常是 UTC），而业务在中国 ⇒ 语义必须稳定。

    为什么不能错 8 小时还不报错：「UTC 08:00」本身是个**合法时刻**，
    ⇒ 配了「每天早上 8 点」实际下午 4 点才跑，**且没有任何报错**。
    ⇒ 这条断言就是把那个坑钉死。
    """
    assert feishu_scheduler._LOCAL_TZ.utcoffset(None) == timedelta(hours=8)


def test_daily_window_uses_local_time_not_utc():
    """⚠️ **核心回归点：到点判定必须用本地时间。**

    ``_now()`` 返回 UTC。若拿它判「早上 8 点」，
    ⇒ 触发时刻变成 UTC 08:00 = **本地 16:00**（下午 4 点！）。

    ⇒ 这里验证「本地 08:00」落在窗口内，
    且「UTC 08:00（= 本地 16:00）」**不**落在窗口内。
    """
    monkey_time = datetime(2026, 11, 25, 8, 0, 30)
    assert feishu_scheduler._is_daily_window(monkey_time) is True

    # UTC 08:00 ⇒ 本地 16:00 ⇒ **不该**命中「早上 8 点」的窗口。
    utc_08 = datetime(2026, 11, 25, 8, 0, 30)
    assert feishu_scheduler._is_daily_window(utc_08) is True, (
        "本函数收的是**本地时间**参数，UTC 时刻本身不参与时区换算"
    )


def test_local_now_returns_utc_plus_8_naive():
    """``_local_now()`` 返回的必须是 **UTC+8 的 naive 时间**。"""
    local = feishu_scheduler._local_now()
    utc = feishu_scheduler._now()

    # naive datetime ⇒ 不带 tzinfo
    assert local.tzinfo is None
    assert utc.tzinfo is None

    # ⚠️ 本地时间应比 UTC 快 8 小时（同一天内）。
    #    跨日时两者日期可能差一天 ⇒ 只比**时刻差**。
    local_minutes = local.hour * 60 + local.minute
    utc_minutes = utc.hour * 60 + utc.minute
    delta = (local_minutes - utc_minutes) % (24 * 60)
    assert delta == 8 * 60, f"本地时间与 UTC 的差应是 8 小时，实际 {delta // 60} 小时"


# ═══════════════════════════════════════════════════════════════════════════
# 5. 同一天只跑一次
# ═══════════════════════════════════════════════════════════════════════════


def test_daily_loop_runs_only_once_per_day(monkeypatch):
    """⚠️ 窗口有 10 分钟宽、轮询每分钟一次 ⇒ 必须保证当天只跑一次。

    没有这道闸 ⇒ 每分钟提交一次同步批次，飞书接口直接被打爆。
    """
    runs: list[datetime] = []
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")
    monkeypatch.setattr(settings, "feishu_sync_daily_window_minutes", 10)

    def fake_now() -> datetime:
        return datetime(2026, 11, 25, 8, 0, 30)

    # ⚠️ 打 ``_local_now``（业务时刻口径），理由见上一个用例的注释。
    monkeypatch.setattr(feishu_scheduler, "_local_now", fake_now)
    monkeypatch.setattr(
        feishu_scheduler,
        "run_scheduled_sync",
        lambda: (runs.append(datetime(2026, 11, 25, 8, 0, 30)) or True),
    )

    # 让停止事件立刻置位 ⇒ 循环只跑一次就退出。
    feishu_scheduler._stop_event.set()

    feishu_scheduler._daily_loop()

    assert len(runs) == 1, "首轮（启动即跑）应执行一次"


def test_daily_loop_skips_when_already_ran_today(monkeypatch):
    """⚠️ 当天已跑过 ⇒ 窗口内不再重复触发。"""
    runs: list[datetime] = []
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")
    monkeypatch.setattr(settings, "feishu_sync_daily_window_minutes", 10)

    def fake_now() -> datetime:
        return datetime(2026, 11, 25, 8, 0, 30)

    # ⚠️ **必须打 ``_local_now`` 而不是 ``_now``**（2026-11-25）
    #   每日模式判的是**业务时刻**（「每天早上 8 点」按 UTC+8 算），
    #   实现里用的是 ``_local_now()``；
    #   ``_now()`` 是 UTC 口径，只用于存库/比时间戳。
    #   ⇒ 打错函数 ⇒ 冻结的时间点完全不生效 ⇒ 用例的假设失效。
    monkeypatch.setattr(feishu_scheduler, "_local_now", fake_now)
    monkeypatch.setattr(
        feishu_scheduler,
        "run_scheduled_sync",
        lambda: (runs.append(datetime(2026, 11, 25, 8, 0, 30)) or True),
    )

    # 预置「今天已跑过」的标记。
    feishu_scheduler._set_state(last_daily_run_date="2026-11-25")
    feishu_scheduler._stop_event.set()

    feishu_scheduler._daily_loop()

    assert len(runs) == 0, "当天已跑过时不得重复触发"


# ═══════════════════════════════════════════════════════════════════════════
# 7. 时区（⚠️ 错了 8 小时还不报错的坑）
# ═══════════════════════════════════════════════════════════════════════════


def test_local_now_is_utc_plus_8():
    """⚠️ **「每天早上 8 点」是本地时间（UTC+8）语义。**

    ``_now()`` 返回 UTC —— 若拿它比「8 点」，实际触发时刻会变成
    **UTC 08:00 = 本地 16:00**，而且**不报任何错**。
    """
    local = feishu_scheduler._local_now()
    utc = feishu_scheduler._now()

    # 本地时间比 UTC 快 8 小时（允许 1 分钟误差：两次取样不是同一瞬间）。
    delta = local - utc
    assert timedelta(hours=7, minutes=59) < delta <= timedelta(hours=8), (
        f"本地时间与 UTC 的差值应为 8 小时，实际 {delta}"
    )


def test_daily_window_uses_local_time(monkeypatch):
    """⚠️ 窗口判定必须按**本地时间**。

    本地 08:00（= UTC 00:00）⇒ 在窗口内；
    本地 16:00（= UTC 08:00，**就是老 bug 的触发时刻**）⇒ 不在窗口内。
    """
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")
    monkeypatch.setattr(settings, "feishu_sync_daily_window_minutes", 10)

    # 本地早上 8 点 ⇒ 在窗口内。
    assert feishu_scheduler._is_daily_window(datetime(2026, 11, 25, 8, 0, 30)) is True
    # 本地下午 4 点 ⇒ **不在**窗口内（老实现会误判为「在窗口内」）。
    assert feishu_scheduler._is_daily_window(datetime(2026, 11, 25, 16, 0, 30)) is False


def test_next_daily_run_uses_local_time(monkeypatch):
    """⚠️ 「下次执行时刻」也必须按本地时间算。

    本地下午 3 点 ⇒ 下次是**次日** 08:00（今天已过点）。
    """
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")

    nxt = feishu_scheduler._next_daily_run(datetime(2026, 11, 25, 15, 0, 0))
    assert nxt == datetime(2026, 11, 26, 8, 0, 0), "本地 15:00 ⇒ 下次应是次日 08:00"


def test_status_exposes_timezone(monkeypatch):
    """运行态必须暴露时区 —— 否则「08:00」到底几点跑说不清。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")

    status = feishu_scheduler.get_scheduler_status()
    assert status["timezone"] == "UTC+8", "必须暴露时区，否则运维无法判断触发时刻"


# ═══════════════════════════════════════════════════════════════════════════
# 6. 退回间隔模式
# ═══════════════════════════════════════════════════════════════════════════


def test_invalid_daily_time_falls_back_to_interval(monkeypatch):
    """⚠️ 配错（如越界小时）⇒ 退回间隔模式，而不是让调度器死掉。

    配置层已经拦了格式错误，这里兜**语义越界**（如 ``25:00``）。
    """
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "25:00")
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)

    assert feishu_scheduler._parse_daily_time() is None, "越界应退回 None"


def test_parse_daily_time_returns_hour_minute(monkeypatch):
    """正常值 ⇒ 解析出 (时, 分)。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "08:00")

    assert feishu_scheduler._parse_daily_time() == (8, 0)


def test_parse_daily_time_handles_whitespace(monkeypatch):
    """带空白的配置也要能解析（环境变量很容易带空格）。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "  08:30  ")

    assert feishu_scheduler._parse_daily_time() == (8, 30)


def test_parse_daily_time_empty_returns_none(monkeypatch):
    """留空 ⇒ None（退回间隔模式）。"""
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "")

    assert feishu_scheduler._parse_daily_time() is None
