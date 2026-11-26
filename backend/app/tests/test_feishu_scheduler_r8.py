"""R8 —— 定时同步「真的能跑起来」的回归测试。

背景
----
盘点项 R8：**定时同步实际是关的** —— ``.env`` 无 ``FEISHU_SYNC_ENABLED``
→ 默认 False → ``start_scheduler()`` 直接 return。
代码写好也不跑，页面还显示「定时同步未开启」。

调度器**内部**逻辑（启动即跑、间隔、跳过、失败计数）已有
``test_feishu_scheduler_alerts.py`` 覆盖。本文件聚焦**外围**，
也就是当初让 R8「跑不起来」的那几处：

1. **配置开关真的能打开** —— ``FEISHU_SYNC_ENABLED=true`` 能被读到；
2. **开关默认关闭** —— 不配就必须是 False（不能默认裸奔）；
3. **配置与线程状态要能区分** —— 「配了但没跑」必须可观测；
4. **shutdown 必须停掉线程** —— 否则热重载/滚动更新会留下重复调度；
5. **停止要幂等** —— 重复调用不得抛异常；
6. **停止不得打断正在跑的同步** —— 不能强杀；
7. **未配置飞书凭证时不得提交批次** —— 不能白跑一轮；
8. **端点契约** —— ``/sync-organization/scheduler`` 返回 camelCase 运行态。

⚠️ 为什么不直接改 ``.env`` 当测试：
``.env`` 是**本地配置**且不入库，测试不能依赖它的内容。
本文件用 ``monkeypatch.setattr(settings, ...)`` 模拟开关的各种取值。
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import Base, Employee
from backend.app.main import app
from backend.app.services import feishu_scheduler


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


@pytest.fixture
def client(api):
    return api[0]


@pytest.fixture
def db(api):
    return api[1]


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
    )
    yield
    feishu_scheduler.stop_scheduler()
    feishu_scheduler._scheduler_thread = None


@pytest.fixture(autouse=True)
def _force_interval_mode(monkeypatch):
    """把调度器固定在**间隔模式**，与本机 ``.env`` 无关。

    ⚠️ **为什么必须隔离**：
    ``FEISHU_SYNC_DAILY_TIME`` 是**部署配置**（本地 ``.env`` 常年是
    ``08:00``）。若不隔离，测试会跟着开发者本机的 ``.env`` 走 ——
    配了每日时刻就静默切到每日模式，间隔模式的用例全部失真，
    而且**本地能过、CI 会红**（CI 的 ``.env`` 没有这一项）。

    ⇒ 测试必须**显式声明**自己测的是哪个模式，不能依赖环境。
    """
    monkeypatch.setattr(settings, "feishu_sync_daily_time", "")
    monkeypatch.setattr(settings, "feishu_sync_daily_window_minutes", 10)


# ═══════════════════════════════════════════════════════════════════════════
# 1. 配置开关
# ═══════════════════════════════════════════════════════════════════════════


def test_sync_is_disabled_by_default():
    """⚠️ 默认必须是**关闭**的。

    反过来想：如果默认是开的，那么没配飞书凭证的环境
    会不停空跑一轮同步 + 刷告警。
    """
    assert settings.model_fields["feishu_sync_enabled"].default is False


def test_sync_enabled_can_be_turned_on(monkeypatch):
    """开关打开后调度器必须真的起来。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_interval_seconds", 3600)
    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: True)

    feishu_scheduler.start_scheduler()
    try:
        status = feishu_scheduler.get_scheduler_status()
        assert status["enabled"] is True
        assert status["running"] is True, "开关打开后线程必须存活"
    finally:
        feishu_scheduler.stop_scheduler()


def test_interval_below_minimum_is_rejected():
    """间隔低于 60 秒必须在**配置校验**阶段就报错。

    回归点：间隔配成 5 秒会直接打爆飞书接口。
    """
    from pydantic import ValidationError

    from backend.app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(feishu_sync_interval_seconds=5)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 「配了但没跑」必须可观测
# ═══════════════════════════════════════════════════════════════════════════


def test_config_enabled_but_thread_not_running_is_distinguishable(monkeypatch):
    """⚠️ 核心断言：「配置开关是 true」与「线程在跑」必须**能区分**。

    这正是 R8 最初的样子：``.env`` 里配了开关，但线程没起来，
    页面只看配置 → 显示「定时同步已开启」→ 实际**一次都没跑**。

    运维要能一眼看出「配了但没跑」，必须靠
    ``enabled``（配置）与 ``running``（线程）两个字段的组合。
    """
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    # 不启动线程 —— 模拟「配置改了但进程没重启」。

    status = feishu_scheduler.get_scheduler_status()

    assert status["enabled"] is True, "配置开关读到了 true"
    assert status["running"] is False, "但线程确实没跑 —— 两者必须能区分"


def test_status_exposes_all_observability_fields(monkeypatch):
    """运行态必须暴露运维需要的全部字段。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_interval_seconds", 900)

    status = feishu_scheduler.get_scheduler_status()

    for field in (
        "enabled",
        "running",
        "intervalSeconds",
        "started",
        "lastRunAt",
        "lastFinishedAt",
        "lastResult",
        "consecutiveFailures",
        "nextRunAt",
        "runCount",
        "skipCount",
    ):
        assert field in status, f"缺少运行态字段：{field}"


# ═══════════════════════════════════════════════════════════════════════════
# 3. 启停
# ═══════════════════════════════════════════════════════════════════════════


def test_shutdown_hook_stops_scheduler_thread(monkeypatch):
    """⚠️ 回归点：``main.py`` 之前**只有 startup、没有 shutdown**。

    后果：开发期热重载、测试里反复建 app、容器滚动更新时，
    旧 app 的 daemon 线程还活着，同时新 app 又起了一个 ——
    变成「两个调度器同时跑」。
    """
    from backend.app import main as main_mod

    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: True)

    feishu_scheduler.start_scheduler()
    assert feishu_scheduler.get_scheduler_status()["running"] is True

    # 触发 app 的 shutdown 钩子 —— 这正是进程退出/重载时会走的路径。
    main_mod._shutdown()

    assert feishu_scheduler.get_scheduler_status()["running"] is False


def test_stop_scheduler_is_idempotent():
    """重复停止不得抛异常（shutdown 与测试清理都会调用它）。"""
    feishu_scheduler.stop_scheduler()
    feishu_scheduler.stop_scheduler()


def test_stop_scheduler_joins_thread(monkeypatch):
    """停止后线程应真的退出（不是只置个事件就返回）。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: True)

    feishu_scheduler.start_scheduler()
    thread = feishu_scheduler._scheduler_thread
    assert thread is not None

    feishu_scheduler.stop_scheduler()

    # 给线程一点时间响应停止事件。
    thread.join(timeout=5)
    assert not thread.is_alive(), "停止后调度线程应已退出"


def test_stop_does_not_kill_running_sync(monkeypatch):
    """⚠️ 停止不得打断正在执行的一轮同步。

    当前轮次可能要拉飞书接口 + 落库几十秒，
    强制中断会留下「批次永远停在 running」的脏状态。
    因此停止只置事件 + **有界等待**，不强杀。
    """
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)

    started = threading.Event()
    release = threading.Event()

    def slow_run() -> bool:
        started.set()
        # 模拟一轮长时间的同步。
        release.wait(timeout=10)
        return True

    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", slow_run)

    feishu_scheduler.start_scheduler()
    assert started.wait(timeout=5), "首轮同步应已启动"

    # 停止 —— 线程还在跑那一轮，不应被强杀。
    feishu_scheduler.stop_scheduler()
    thread = feishu_scheduler._scheduler_thread
    assert thread is not None
    assert thread.is_alive(), "正在执行的同步不应被强杀"

    release.set()
    thread.join(timeout=10)


def test_start_scheduler_does_not_double_start(monkeypatch):
    """重复启动不得产生两个调度线程。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: True)

    feishu_scheduler.start_scheduler()
    first = feishu_scheduler._scheduler_thread
    feishu_scheduler.start_scheduler()

    assert feishu_scheduler._scheduler_thread is first, "不得重复创建线程"
    feishu_scheduler.stop_scheduler()


# ═══════════════════════════════════════════════════════════════════════════
# 4. 未配置凭证时不得白跑
# ═══════════════════════════════════════════════════════════════════════════


def test_scheduled_sync_skipped_when_credentials_missing(monkeypatch):
    """飞书凭证没配时，定时同步不得提交批次（否则白跑一轮 + 刷告警）。"""
    monkeypatch.setattr(settings, "feishu_app_id", "")
    monkeypatch.setattr(settings, "feishu_app_secret", "")

    assert feishu_scheduler.run_scheduled_sync() is False
    assert feishu_scheduler._get_state()["last_result"] == "skipped_not_configured"


# ═══════════════════════════════════════════════════════════════════════════
# 5. 端点契约
# ═══════════════════════════════════════════════════════════════════════════


def test_scheduler_endpoint_contract(client: TestClient, db):
    """``/sync-organization/scheduler`` 必须返回 camelCase 运行态。"""
    from backend.app.domain.models import SystemAdmin
    from backend.app.routes.auth import _generate_token

    db.add(
        Employee(
            id="SCH_ADMIN",
            name="管理员",
            login_account="sch_admin",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.flush()
    # 飞书同步管理权限来自 SystemAdmin 角色。
    db.add(SystemAdmin(employee_id="SCH_ADMIN", active=True))
    db.commit()

    token, _ = _generate_token("SCH_ADMIN", "sch_admin", 1)
    resp = client.get(
        "/v1/feishu/sync-organization/scheduler",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    for field in (
        "enabled",
        "running",
        "intervalSeconds",
        "nextRunAt",
        "consecutiveFailures",
    ):
        assert field in data, f"端点缺少字段：{field}"


def test_scheduler_endpoint_requires_auth(client: TestClient):
    """未登录不得读取调度器状态。"""
    resp = client.get("/v1/feishu/sync-organization/scheduler")
    assert resp.status_code in (401, 403), resp.text
