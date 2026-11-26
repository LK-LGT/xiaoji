"""定时同步调度与失败告警的测试。

覆盖三块：

1. **调度器**：启动即执行、间隔生效、跳过已有任务、连续失败计数；
2. **告警**：落库、去重抑制、失败/部分失败/恢复、积压阈值；
3. **旁路安全**：告警自身出错**绝不能**影响同步主流程。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.domain.models import (
    Base,
    OrganizationSyncAlert,
    OrganizationSyncBatch,
    OrganizationSyncItemError,
)
from backend.app.services import feishu_scheduler
from backend.app.services.sync_alert_service import (
    ALERT_BATCH_FAILED,
    ALERT_BATCH_PARTIAL_FAILED,
    ALERT_ITEM_ERROR_BACKLOG,
    ALERT_SYNC_RECOVERED,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_OPEN,
    STATUS_RESOLVED,
    alert_batch_outcome,
    alert_error_backlog,
    alert_scheduler_failures,
    create_alert,
    resolve_alert,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """每个用例前后重置调度器运行态，避免相互污染。"""
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


# ─────────────────────────────────────────────────────────────
# 调度器
# ─────────────────────────────────────────────────────────────


def test_scheduler_runs_immediately_on_start(monkeypatch):
    """启动调度器后应立即跑一次，而不是等一个完整周期。

    回归点：以前 ``_loop`` 先 ``wait(interval)`` 再跑，
    服务重启/扩缩容后最长会有一个周期的组织架构陈旧窗口。
    """
    calls: list[int] = []

    def fake_run() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", fake_run)
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_interval_seconds", 3600)

    feishu_scheduler.start_scheduler()
    try:
        # 首轮在后台线程里跑，等它落地。
        for _ in range(100):
            if calls:
                break
            import time

            time.sleep(0.02)
        assert calls, "启动后应立即执行一次同步"
    finally:
        feishu_scheduler.stop_scheduler()


def test_scheduler_status_reports_runtime(monkeypatch):
    """调度器状态应暴露运行态字段。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)
    monkeypatch.setattr(settings, "feishu_sync_interval_seconds", 900)

    status = feishu_scheduler.get_scheduler_status()
    assert status["enabled"] is True
    assert status["intervalSeconds"] == 900
    assert "running" in status
    assert "nextRunAt" in status
    assert "consecutiveFailures" in status
    assert status["consecutiveFailures"] == 0


def test_scheduler_skips_when_sync_already_running(monkeypatch):
    """已有同步在运行时，本轮应跳过而不是排队。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)

    calls: list[int] = []

    def fake_run() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", fake_run)

    feishu_scheduler._run_once(3600)
    assert feishu_scheduler._get_state()["run_count"] == 1
    assert feishu_scheduler._get_state()["consecutive_failures"] == 0


def test_scheduler_counts_consecutive_failures(monkeypatch):
    """连续失败应累加计数，成功后清零。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", True)

    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: False)
    feishu_scheduler._run_once(3600)
    feishu_scheduler._run_once(3600)
    assert feishu_scheduler._get_state()["consecutive_failures"] == 2

    monkeypatch.setattr(feishu_scheduler, "run_scheduled_sync", lambda: True)
    feishu_scheduler._run_once(3600)
    assert feishu_scheduler._get_state()["consecutive_failures"] == 0


def test_scheduler_does_not_start_when_disabled(monkeypatch):
    """未启用时不得启动调度线程。"""
    monkeypatch.setattr(settings, "feishu_sync_enabled", False)
    feishu_scheduler.start_scheduler()
    status = feishu_scheduler.get_scheduler_status()
    assert status["enabled"] is False
    assert status["running"] is False


# ─────────────────────────────────────────────────────────────
# 告警
# ─────────────────────────────────────────────────────────────


def _make_batch(db, status: str = "failed", **kwargs) -> OrganizationSyncBatch:
    batch = OrganizationSyncBatch(
        batch_no=kwargs.pop("batch_no", "ORG-TEST-001"),
        status=status,
        mode="full",
        trigger_type="manual",
        **kwargs,
    )
    db.add(batch)
    db.commit()
    return batch


def test_batch_failure_creates_error_alert(db):
    """批次失败应产生 error 级告警。"""
    batch = _make_batch(db, "failed", error_message="飞书接口 500", error_category="retryable")

    alert = alert_batch_outcome(db, batch)

    assert alert is not None
    assert alert.alert_type == ALERT_BATCH_FAILED
    assert alert.severity == SEVERITY_ERROR
    assert alert.status == STATUS_OPEN
    assert batch.batch_no in alert.title
    assert "飞书接口 500" in alert.message


def test_batch_partial_failure_creates_warning_alert(db):
    """部分失败应产生 warning 级告警。"""
    batch = _make_batch(db, "partial_failed", employees_failed=3)

    alert = alert_batch_outcome(db, batch)

    assert alert is not None
    assert alert.alert_type == ALERT_BATCH_PARTIAL_FAILED
    assert alert.severity == SEVERITY_WARNING


def test_successful_batch_resolves_previous_alert(db):
    """恢复正常时应把此前的失败告警标记为 resolved。"""
    batch = _make_batch(db, "failed", error_message="boom")
    failed_alert = alert_batch_outcome(db, batch)
    assert failed_alert is not None

    batch.status = "ok"
    db.commit()
    alert_batch_outcome(db, batch)

    db.expire_all()
    resolved = db.get(OrganizationSyncAlert, failed_alert.id)
    assert resolved.status == STATUS_RESOLVED
    assert resolved.resolved_at is not None

    recovery = db.execute(
        select(OrganizationSyncAlert).where(
            OrganizationSyncAlert.alert_type == ALERT_SYNC_RECOVERED
        )
    ).scalars().first()
    assert recovery is not None
    assert recovery.severity == SEVERITY_INFO


def test_alert_is_deduplicated_in_suppression_window(db, monkeypatch):
    """同一 dedup_key 在抑制窗口内只产生一条告警。"""
    monkeypatch.setattr(settings, "feishu_sync_alert_suppress_seconds", 1800)

    first = create_alert(
        db,
        alert_type=ALERT_BATCH_FAILED,
        severity=SEVERITY_ERROR,
        title="第一次",
        message="m",
        dedup_key="same-key",
    )
    second = create_alert(
        db,
        alert_type=ALERT_BATCH_FAILED,
        severity=SEVERITY_ERROR,
        title="第二次",
        message="m",
        dedup_key="same-key",
    )

    assert first is not None
    assert second is None, "抑制窗口内不应重复告警"

    count = db.query(func.count(OrganizationSyncAlert.id)).scalar()
    assert count == 1


def test_alert_allowed_again_after_suppression_window(db, monkeypatch):
    """抑制窗口过后应能再次告警。"""
    monkeypatch.setattr(settings, "feishu_sync_alert_suppress_seconds", 1800)

    create_alert(
        db,
        alert_type=ALERT_BATCH_FAILED,
        severity=SEVERITY_ERROR,
        title="第一次",
        message="m",
        dedup_key="same-key",
    )

    # 把窗口推到过去 —— 模拟时间流逝。
    recent = db.execute(
        select(OrganizationSyncAlert).where(OrganizationSyncAlert.dedup_key == "same-key")
    ).scalars().first()
    recent.suppressed_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()

    again = create_alert(
        db,
        alert_type=ALERT_BATCH_FAILED,
        severity=SEVERITY_ERROR,
        title="第二次",
        message="m",
        dedup_key="same-key",
    )
    assert again is not None


def test_error_backlog_alert_respects_threshold(db, monkeypatch):
    """待处理失败记录超阈值才告警；回落后自动恢复。"""
    monkeypatch.setattr(settings, "feishu_sync_alert_error_backlog_threshold", 3)

    assert alert_error_backlog(db) is None, "未达阈值不应告警"

    batch = _make_batch(db, "failed", error_message="boom")
    for index in range(3):
        db.add(
            OrganizationSyncItemError(
                batch_id=batch.id,
                resource_type="employee",
                resource_id=f"u{index}",
                message="boom",
                status="pending",
            )
        )
    db.commit()

    alert = alert_error_backlog(db)
    assert alert is not None
    assert alert.alert_type == ALERT_ITEM_ERROR_BACKLOG
    assert alert.severity == SEVERITY_WARNING

    # 全部解决后应补恢复告警。
    for row in db.query(OrganizationSyncItemError).all():
        row.status = "resolved"
    db.commit()

    assert alert_error_backlog(db) is None
    recovery = db.execute(
        select(OrganizationSyncAlert).where(
            OrganizationSyncAlert.alert_type == ALERT_SYNC_RECOVERED
        )
    ).scalars().first()
    assert recovery is not None


def test_scheduler_consecutive_failures_alert(db, monkeypatch):
    """连续失败达阈值才告警。"""
    monkeypatch.setattr(settings, "feishu_sync_alert_consecutive_failures", 3)

    assert alert_scheduler_failures(db, consecutive_failures=2) is None
    alert = alert_scheduler_failures(db, consecutive_failures=3, last_error="连接超时")
    assert alert is not None
    assert alert.severity == SEVERITY_ERROR
    assert "连接超时" in alert.message


def test_resolve_alert_without_existing_returns_none(db):
    """没有对应告警时恢复操作应安全返回 None。"""
    assert resolve_alert(db, dedup_key="nonexistent", title="t", message="m") is None


# ─────────────────────────────────────────────────────────────
# 旁路安全
# ─────────────────────────────────────────────────────────────


def test_alert_failure_does_not_break_sync(monkeypatch):
    """告警自身抛异常时，同步任务仍应正常完成。

    回归点：告警是旁路能力，它出错绝不能把成功的同步变成失败。
    """
    from backend.app.services import feishu_sync_tasks

    db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db)
    session = sessionmaker(bind=db)()
    try:
        batch = _make_batch(session, "failed", error_message="boom")

        # 让告警模块整体失效。
        import backend.app.services.sync_alert_service as alert_mod

        def boom(*_args, **_kwargs):
            raise RuntimeError("告警服务挂了")

        monkeypatch.setattr(alert_mod, "alert_batch_outcome", boom)

        # 不抛异常即通过 —— 同步流程不受影响。
        feishu_sync_tasks._raise_alerts_for_batch(session, batch.id)
    finally:
        session.close()
        db.dispose()


def test_raise_alerts_swallows_exceptions(monkeypatch):
    """_raise_alerts_for_batch 必须吞掉内部所有异常。"""
    from backend.app.services import feishu_sync_tasks

    class ExplodingSession:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("db 挂了")

    # 不抛异常即通过。
    feishu_sync_tasks._raise_alerts_for_batch(ExplodingSession(), "whatever")
