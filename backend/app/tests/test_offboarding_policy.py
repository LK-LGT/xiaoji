"""离职员工状态判定策略测试。

核心保证：**默认行为与重构前完全一致**（``is_unjoin`` 仍按 inactive），
只是把判定规则显式化、可配置化，并带可追溯的 reason。
"""

from __future__ import annotations

import pytest

from backend.app.core.config import settings
from backend.app.services.offboarding_policy import (
    REASON_ACTIVE,
    REASON_EXITED,
    REASON_FROZEN,
    REASON_NO_STATUS,
    REASON_NOT_ACTIVATED,
    REASON_RESIGNED,
    REASON_UNJOINED,
    STATUS_ACTIVE,
    STATUS_EXITED,
    STATUS_INACTIVE,
    OffboardingPolicy,
    should_disable_identity,
)


@pytest.fixture()
def policy() -> OffboardingPolicy:
    return OffboardingPolicy()


# ─────────────────────────────────────────────────────────────
# 默认口径（与重构前一致）
# ─────────────────────────────────────────────────────────────


def test_no_status_is_active(policy):
    """拿不到状态时保持 active —— 不能凭猜测判离职。"""
    assert policy.decide(None).status == STATUS_ACTIVE
    assert policy.decide({}).status == STATUS_ACTIVE
    assert policy.decide(None).reason == REASON_NO_STATUS


def test_exited_maps_to_exited(policy):
    decision = policy.decide({"is_exited": True})
    assert decision.status == STATUS_EXITED
    assert decision.reason == REASON_EXITED
    assert decision.permanent is True
    assert decision.disable_account is True


def test_resigned_maps_to_exited(policy):
    """主动离职也算离职 —— 否则存在「已离职仍可登录」的安全漏洞。"""
    decision = policy.decide({"is_resigned": True})
    assert decision.status == STATUS_EXITED
    assert decision.reason == REASON_RESIGNED
    assert decision.permanent is True


def test_frozen_maps_to_inactive_and_is_recoverable(policy):
    """冻结是临时停用，解冻后应能自动恢复。"""
    decision = policy.decide({"is_frozen": True})
    assert decision.status == STATUS_INACTIVE
    assert decision.reason == REASON_FROZEN
    assert decision.permanent is False


def test_not_activated_maps_to_inactive(policy):
    decision = policy.decide({"is_activated": False})
    assert decision.status == STATUS_INACTIVE
    assert decision.reason == REASON_NOT_ACTIVATED


def test_unjoined_maps_to_inactive_by_default(policy):
    """默认：未加入企业按 inactive（更保守，不误伤在职员工）。"""
    decision = policy.decide({"is_unjoin": True})
    assert decision.status == STATUS_INACTIVE
    assert decision.reason == REASON_UNJOINED
    assert decision.permanent is False


def test_fully_active(policy):
    decision = policy.decide(
        {
            "is_exited": False,
            "is_resigned": False,
            "is_frozen": False,
            "is_activated": True,
            "is_unjoin": False,
        }
    )
    assert decision.status == STATUS_ACTIVE
    assert decision.reason == REASON_ACTIVE
    assert decision.disable_account is False


def test_exited_takes_priority_over_everything(policy):
    """离职优先级最高 —— 其它标志如何都判离职。"""
    decision = policy.decide(
        {
            "is_exited": True,
            "is_resigned": True,
            "is_frozen": True,
            "is_activated": False,
            "is_unjoin": True,
        }
    )
    assert decision.status == STATUS_EXITED
    assert decision.reason == REASON_EXITED


def test_frozen_takes_priority_over_unjoined(policy):
    """冻结优先于未加入 —— 冻结是明确的停用意图。"""
    decision = policy.decide({"is_frozen": True, "is_unjoin": True})
    assert decision.status == STATUS_INACTIVE
    assert decision.reason == REASON_FROZEN


def test_not_activated_takes_priority_over_unjoined(policy):
    """未激活优先于未加入。"""
    decision = policy.decide({"is_activated": False, "is_unjoin": True})
    assert decision.status == STATUS_INACTIVE
    assert decision.reason == REASON_NOT_ACTIVATED


# ─────────────────────────────────────────────────────────────
# 可配置口径（待业务确认）
# ─────────────────────────────────────────────────────────────


def test_unjoin_can_be_configured_as_exited():
    """业务若认为「未加入=已离职」，切换配置即可。"""
    policy = OffboardingPolicy(unjoin_as_exited=True)
    decision = policy.decide({"is_unjoin": True})
    assert decision.status == STATUS_EXITED
    assert decision.reason == REASON_UNJOINED
    assert decision.permanent is True


def test_exited_account_disabling_is_configurable():
    policy = OffboardingPolicy(exited_disables_account=False)
    decision = policy.decide({"is_exited": True})
    assert decision.status == STATUS_EXITED
    assert decision.disable_account is False


def test_inactive_account_disabling_is_configurable():
    policy = OffboardingPolicy(inactive_disables_account=False)
    decision = policy.decide({"is_frozen": True})
    assert decision.status == STATUS_INACTIVE
    assert decision.disable_account is False


def test_policy_from_settings_reads_config(monkeypatch):
    """策略应能从配置读取 —— 业务确认后只改环境变量。"""
    monkeypatch.setattr(settings, "feishu_sync_unjoin_as_exited", True)
    policy = OffboardingPolicy.from_settings()
    assert policy.unjoin_as_exited is True
    assert policy.decide({"is_unjoin": True}).status == STATUS_EXITED


# ─────────────────────────────────────────────────────────────
# 身份绑定停用
# ─────────────────────────────────────────────────────────────


def test_should_disable_identity():
    assert should_disable_identity(STATUS_EXITED) is True
    assert should_disable_identity(STATUS_INACTIVE) is True
    assert should_disable_identity(STATUS_ACTIVE) is False
    assert should_disable_identity(None) is False


# ─────────────────────────────────────────────────────────────
# 与同步主流程的一致性
# ─────────────────────────────────────────────────────────────


def test_resolve_employment_status_matches_policy(monkeypatch):
    """同步主流程的判定必须与策略一致（默认口径）。"""
    from backend.app.services.organization_sync import _resolve_employment_status

    cases = [
        (None, STATUS_ACTIVE),
        ({}, STATUS_ACTIVE),
        ({"is_exited": True}, STATUS_EXITED),
        ({"is_resigned": True}, STATUS_EXITED),
        ({"is_frozen": True}, STATUS_INACTIVE),
        ({"is_activated": False}, STATUS_INACTIVE),
        ({"is_unjoin": True}, STATUS_INACTIVE),
        ({"is_activated": True, "is_unjoin": False}, STATUS_ACTIVE),
    ]
    for status, expected in cases:
        assert _resolve_employment_status(status) == expected, status


def test_decision_to_dict_roundtrip(policy):
    """判定结果应可序列化，便于写进对账/审计。"""
    decision = policy.decide({"is_exited": True})
    payload = decision.to_dict()
    assert payload["status"] == STATUS_EXITED
    assert payload["reason"] == REASON_EXITED
    assert payload["permanent"] is True
    assert payload["source"] == {"is_exited": True}
