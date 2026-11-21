"""P0-2：目标人授权令牌（``subj``）的测试。

⚠️ **这个文件守的是最后一道防线**
--------------------------------
「后端硬权限」的完整链条：

    授权判定（qa_authorization）
        ↓ 签发带 subj 的签名令牌
    令牌（agent_tool_token）
        ↓ 工具层从令牌读目标人
    工具执行（agent_tools）

⚠️ **关键不变量**：工具层**不接受**请求体里的目标人。
   即使模型被诱导传入别人的 employee_id，也必须被忽略 ——
   这就是"不把安全边界交给模型"的物理实现。

⇒ 本文件必须证明：
   ① 令牌能携带目标人
   ② 目标人被篡改 ⇒ 签名校验失败
   ③ scope 语义正确（self / peer / org）
   ④ 过期令牌被拒
   ⑤ 不带工具权限的工具被拒
"""

from __future__ import annotations

import base64
import json

import pytest

from backend.app.services import agent_tool_token as tok


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    """令牌需要签名密钥 —— 测试里给一个固定的。"""
    monkeypatch.setattr(
        tok.settings, "agent_tool_secret", "test-secret-for-token-verify", raising=False
    )


def _decode(token: str) -> dict:
    encoded = token.split(".")[0]
    padded = encoded + "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


# ---------------------------------------------------------------------------
# 签发与携带
# ---------------------------------------------------------------------------


def test_token_without_subject_targets_self() -> None:
    """不带 subject ⇒ 目标即本人。"""
    token = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["get_my_score"]
    )
    payload = tok.verify_agent_tool_token(token)
    assert payload.subj is None
    assert payload.sub == "E1"


def test_token_carries_subject() -> None:
    """★ 令牌能携带已授权的目标人。"""
    token = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["get_peer_score"],
        scope="peer", subject_id="E2", reason="同部门同级别同事（只读）",
    )
    payload = tok.verify_agent_tool_token(token)
    assert payload.subj == "E2"
    assert payload.scope == "peer"
    assert payload.reason == "同部门同级别同事（只读）"
    # ⚠️ 签发者仍是提问者
    assert payload.sub == "E1"


def test_subject_is_in_signed_claims() -> None:
    """⚠️ 目标人必须在**被签名的载荷**里 —— 否则改它不用破签名。"""
    token = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["get_peer_score"],
        scope="peer", subject_id="E2",
    )
    claims = _decode(token)
    assert claims["subj"] == "E2"
    assert claims["scope"] == "peer"


# ---------------------------------------------------------------------------
# 篡改 —— 核心防线
# ---------------------------------------------------------------------------


def test_tampered_subject_fails_signature() -> None:
    """★ 篡改目标人 ⇒ 签名校验失败（401）。"""
    token = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["get_peer_score"],
        scope="peer", subject_id="E2",
    )
    encoded, signature = token.split(".")
    claims = _decode(token)
    claims["subj"] = "HR_ADMIN"          # ← 试图越权到 HR
    forged = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    with pytest.raises(tok.AgentToolTokenError):
        tok.verify_agent_tool_token(f"{forged}.{signature}")


def test_tampered_scope_fails_signature() -> None:
    """★ 篡改 scope（self→org）⇒ 签名校验失败。"""
    token = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["get_peer_score"], scope="self"
    )
    encoded, signature = token.split(".")
    claims = _decode(token)
    claims["scope"] = "org"
    forged = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    with pytest.raises(tok.AgentToolTokenError):
        tok.verify_agent_tool_token(f"{forged}.{signature}")


def test_tampered_allow_list_fails_signature() -> None:
    """★ 篡改工具白名单 ⇒ 签名校验失败。

    这条保证模型**不能**自己往 allow 里塞写工具。
    """
    token = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["get_peer_score"]
    )
    encoded, signature = token.split(".")
    claims = _decode(token)
    claims["allow"] = ["get_peer_score", "add_metric_value"]
    forged = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    with pytest.raises(tok.AgentToolTokenError):
        tok.verify_agent_tool_token(f"{forged}.{signature}")


def test_invalid_subject_type_rejected_at_issue() -> None:
    """非法 subject（超长）⇒ 签发直接失败。"""
    with pytest.raises(ValueError):
        tok.issue_agent_tool_token(
            employee_id="E1", task_id="T1", allow=["get_peer_score"],
            subject_id="X" * 100,
        )


def test_invalid_scope_rejected_at_issue() -> None:
    """非法 scope ⇒ 签发直接失败。"""
    with pytest.raises(ValueError):
        tok.issue_agent_tool_token(
            employee_id="E1", task_id="T1", allow=["get_peer_score"],
            scope="everything",
        )


# ---------------------------------------------------------------------------
# 过期
# ---------------------------------------------------------------------------


def test_expired_token_rejected() -> None:
    """过期令牌被拒（403）。"""
    # 手工构造一个已过期的载荷（签名正确，exp 在过去）
    now = 1_000_000
    claims = {
        "aud": tok.AUDIENCE, "sub": "E1", "task": "T1",
        "allow": ["get_peer_score"], "scope": "peer", "subj": "E2",
        "iat": now, "exp": now + 30,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = tok._sign(encoded)
    with pytest.raises(tok.AgentToolTokenExpired):
        tok.verify_agent_tool_token(f"{encoded}.{signature}")


# ---------------------------------------------------------------------------
# 工具白名单
# ---------------------------------------------------------------------------


def test_tool_not_in_allow_list_rejected() -> None:
    """不在 allow 里的工具被拒（403）。"""
    token = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["get_peer_score"]
    )
    payload = tok.verify_agent_tool_token(token)
    tok.assert_tool_allowed(payload, "get_peer_score")
    with pytest.raises(tok.AgentToolTokenForbidden):
        tok.assert_tool_allowed(payload, "add_metric_value")


def test_allow_list_is_deduplicated_and_sorted() -> None:
    """allow 去重 + 排序 ⇒ 同样的工具集得到同样的令牌载荷。"""
    t1 = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["b", "a", "a"]
    )
    t2 = tok.issue_agent_tool_token(
        employee_id="E1", task_id="T1", allow=["a", "b"]
    )
    assert _decode(t1)["allow"] == _decode(t2)["allow"] == ["a", "b"]


def test_allow_must_not_be_a_string() -> None:
    """allow 传字符串 ⇒ 拒绝（否则会被当成字符序列）。"""
    with pytest.raises(ValueError):
        tok.issue_agent_tool_token(
            employee_id="E1", task_id="T1", allow="get_peer_score"  # type: ignore[arg-type]
        )
