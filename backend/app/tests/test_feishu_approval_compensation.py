"""飞书审批回流定时补偿（FS-7）的测试。

⚠️ **核心断言**：webhook 漏推时，定时补偿能把计划从「审批中」救回终态。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Employee, MonthPlan
from backend.app.domain.plan_models import PlanFeishuBinding
from backend.app.services.feishu_approval_compensation import (
    TERMINAL_STATUSES,
    compensate_once,
    find_stale_bindings,
)


def _utc_now() -> datetime:
    """当前 UTC 时间（naive）。

    ⚠️ **必须用 UTC，不能用 ``datetime.now()``**
    ⚠️ 项目的 ``_now()`` 是 ``datetime.now(UTC).replace(tzinfo=None)`` ——
    ⚠️ 存的是 **UTC 的 naive 值**。
    ⚠️ 而 ``datetime.now()`` 是**本地时间** —— 本机 UTC+8 时，
    ⚠️ 写入值会比 UTC **超前 8 小时** ⇒ ``updated_at <= cutoff`` 恒为 False
    ⚠️ ⇒ 看起来「一条都查不到」。
    """
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_binding(
    db,
    *,
    plan_id: str = "P1",
    instance_code: str = "inst-1",
    instance_status: str = "PENDING",
    updated_at: datetime | None = None,
    with_plan: bool = True,
) -> PlanFeishuBinding:
    """造一条绑定行。

    ⚠️ ``with_plan=True`` 时**同时**造出对应的员工与月计划 ——
    回流要真的把计划状态改成 APPROVED，没有计划会报「计划不存在」。
    """
    if with_plan:
        owner_id = f"E-{plan_id}"
        db.add(Employee(id=owner_id, name=f"员工{plan_id}"))
        db.add(
            MonthPlan(
                id=plan_id,
                owner_id=owner_id,
                plan_month="2026-11",
                title=f"月计划 {plan_id}",
                status="submitted",
                result_status="not_submitted",
            )
        )
    row = PlanFeishuBinding(
        plan_type="month",
        plan_id=plan_id,
        instance_code=instance_code,
        instance_status=instance_status,
        correlation_key=f"CK-{plan_id}",
        updated_at=updated_at or (_utc_now() - timedelta(hours=2)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class _StubClient:
    """只提供 ``fetch_approval_instance`` 的假客户端。"""

    def __init__(self, status: str = "APPROVED", fail: bool = False) -> None:
        self.status = status
        self.fail = fail
        self.called: list[str] = []

    def fetch_approval_instance(self, instance_id: str) -> dict:
        self.called.append(instance_id)
        if self.fail:
            raise RuntimeError("飞书接口不可用")
        return {"instance_code": instance_id, "status": self.status}


class TestFindStaleBindings:
    def test_stale_pending_binding_is_found(self, db):
        _make_binding(db, updated_at=_utc_now() - timedelta(hours=2))
        found = find_stale_bindings(db, stale_seconds=300)
        assert len(found) == 1

    def test_fresh_binding_is_skipped(self, db):
        """⚠️ 刚更新过的**不该**重复拉 —— 省飞书频控配额。"""
        _make_binding(db, updated_at=_utc_now())
        assert find_stale_bindings(db, stale_seconds=300) == []

    def test_terminal_status_is_skipped(self, db):
        """⚠️ 已终态的**不需要**再补偿。"""
        for status in sorted(TERMINAL_STATUSES):
            _make_binding(
                db,
                plan_id=f"P-{status}",
                instance_code=f"inst-{status}",
                instance_status=status,
                updated_at=_utc_now() - timedelta(hours=2),
            )
        assert find_stale_bindings(db, stale_seconds=300) == []

    def test_binding_without_instance_code_is_skipped(self, db):
        """⚠️ 没有飞书实例就无从拉起。"""
        _make_binding(db, instance_code="", updated_at=_utc_now() - timedelta(hours=2))
        assert find_stale_bindings(db, stale_seconds=300) == []

    def test_limit_caps_result_count(self, db):
        """⚠️ 单轮上限 —— 防止积压时一轮拉爆频控。"""
        for i in range(5):
            _make_binding(
                db,
                plan_id=f"P{i}",
                instance_code=f"inst-{i}",
                updated_at=_utc_now() - timedelta(hours=2),
            )
        assert len(find_stale_bindings(db, stale_seconds=300, limit=2)) == 2

    def test_oldest_updated_is_compensated_first(self, db):
        """⚠️ 越陈旧的越优先 —— 早补早恢复。"""
        now = _utc_now()
        _make_binding(
            db, plan_id="NEW", instance_code="i-new", updated_at=now - timedelta(minutes=10)
        )
        _make_binding(
            db, plan_id="OLD", instance_code="i-old", updated_at=now - timedelta(hours=5)
        )
        found = find_stale_bindings(db, stale_seconds=300)
        assert [b.plan_id for b in found] == ["OLD", "NEW"]


class TestCompensateOnce:
    def test_no_bindings_returns_empty_result(self, db):
        result = compensate_once(db, client=_StubClient())
        assert result.scanned == 0
        assert result.compensated == 0

    def test_stale_binding_is_compensated(self, db):
        """⚠️ **核心场景** —— webhook 漏推，补偿把实例拉回来。"""
        _make_binding(db, updated_at=_utc_now() - timedelta(hours=2))
        client = _StubClient(status="APPROVED")
        result = compensate_once(db, client=client)
        assert result.scanned == 1
        assert result.compensated == 1
        assert result.updated == 1
        assert client.called == ["inst-1"]

    def test_non_terminal_status_counts_as_skipped(self, db):
        """⚠️ 还在审批中 —— 补偿过了，但状态没变。"""
        _make_binding(db, updated_at=_utc_now() - timedelta(hours=2))
        client = _StubClient(status="PENDING")
        result = compensate_once(db, client=client)
        assert result.compensated == 1
        assert result.updated == 0
        assert result.skipped == 1

    def test_one_failure_does_not_block_others(self, db):
        """⚠️ **关键隔离性** —— 一个实例失败不该拖垮整轮。"""
        _make_binding(
            db,
            plan_id="BAD",
            instance_code="inst-bad",
            updated_at=_utc_now() - timedelta(hours=2),
        )
        _make_binding(
            db,
            plan_id="GOOD",
            instance_code="inst-good",
            updated_at=_utc_now() - timedelta(hours=2),
        )

        class _PartiallyFailingClient(_StubClient):
            def fetch_approval_instance(self, instance_id: str) -> dict:
                if instance_id == "inst-bad":
                    raise RuntimeError("飞书接口不可用")
                return super().fetch_approval_instance(instance_id)

        result = compensate_once(db, client=_PartiallyFailingClient())
        assert result.failed == 1
        assert result.compensated == 1
        assert len(result.errors) == 1

    def test_failure_does_not_raise(self, db):
        """⚠️ 补偿失败**不得**抛到调用方 —— 调度线程不能因此死掉。"""
        _make_binding(db, updated_at=_utc_now() - timedelta(hours=2))
        result = compensate_once(db, client=_StubClient(fail=True))
        assert result.failed == 1
        assert result.errors

    def test_fresh_binding_is_not_fetched(self, db):
        """⚠️ 未陈旧的**不该**调飞书接口。"""
        _make_binding(db, updated_at=_utc_now())
        client = _StubClient()
        result = compensate_once(db, client=client)
        assert result.scanned == 0
        assert client.called == []
