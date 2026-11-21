"""★ 同部门平级同事工具（``get_peer_score``）在 L3 侧的装配与提示词测试。

⚠️ **这里锁定三件事**
-------------------
1. **工具被正确注册**，且**参数只有 period** ——
   目标人由后端令牌决定，模型**不能**指定查谁。
   这是"后端硬权限"在 L3 侧的体现：模型没有"查谁"的自由度。
2. **令牌允许列表之外模型看不见它** —— 允许列表由服务端签发的令牌决定。
3. **提示词教会模型三条边界**：
   - 不能指定查谁；查不到就如实说，**不要编造**分数
   - peer 数据**只有分数与构成**，没有评语/证据/自评，不要推测
   - 规则得分 ≠ 最终成绩
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from tests._test_secret import AUTH_HEADERS

# isort: split
from app import main
from app.base import Completion
from app.qa import QARequest, build_tool_registry

# ---------------------------------------------------------------------------
# 1. 工具注册
# ---------------------------------------------------------------------------


def test_registry_registers_peer_score():
    """⚠️ 新工具必须注册。"""
    registry = build_tool_registry("tok")
    spec = registry.get("get_peer_score")
    assert spec is not None
    assert spec.description


def test_peer_score_only_accepts_period():
    """★ 参数**只有 period** —— 模型无法指定查谁。

    ⚠️ 这是最关键的一条：一旦有了 employee_id / name 参数，
    模型就能（被诱导）指定任意目标人，后端硬权限形同虚设。
    """
    registry = build_tool_registry("tok")
    spec = registry.get("get_peer_score")
    properties = spec.parameters["properties"]
    assert set(properties) == {"period"}
    assert properties["period"]["pattern"] == r"^\d{6}$"
    # ⚠️ additionalProperties=False —— 多传参数会被网关拒
    assert spec.parameters["additionalProperties"] is False


def test_peer_score_available_when_allowed():
    registry = build_tool_registry("tok")
    schema = registry.schema({"get_peer_score"})
    names = {t["function"]["name"] for t in schema}
    assert names == {"get_peer_score"}


def test_peer_score_not_available_without_allowance():
    """⚠️ 令牌允许列表里没有 → 模型看不见这个工具。"""
    registry = build_tool_registry("tok")
    schema = registry.schema({"get_metric_values"})
    names = {t["function"]["name"] for t in schema}
    assert "get_peer_score" not in names


# ---------------------------------------------------------------------------
# 2. 请求契约
# ---------------------------------------------------------------------------


def test_qa_request_accepts_peer_scope():
    """⚠️ context.scope 要能表达 peer。"""
    req = QARequest(
        question="销售乙这个月考得怎么样",
        context={"period": "202611", "task_id": "T", "scope": "peer"},
        tool_token="tok",
        allowed_tools=["get_peer_score"],
    )
    assert req.context.scope == "peer"
    assert req.allowed_tools == ["get_peer_score"]


def test_qa_request_allows_five_tools():
    """⚠️ allowed_tools 上限要跟着放宽到 5。"""
    req = QARequest(
        question="q",
        context={"period": "202611", "task_id": "T", "scope": "self"},
        tool_token="tok",
        allowed_tools=[
            "get_metric_values",
            "get_evidence",
            "get_department_stats",
            "get_scoring_breakdown",
            "get_peer_score",
        ],
    )
    assert len(req.allowed_tools) == 5


def test_qa_request_rejects_unknown_tool():
    with pytest.raises(Exception):
        QARequest(
            question="q",
            context={"period": "202611", "task_id": "T", "scope": "self"},
            tool_token="tok",
            allowed_tools=["get_everything"],  # type: ignore[list-item]
        )


# ---------------------------------------------------------------------------
# 3. 提示词
# ---------------------------------------------------------------------------


def test_prompt_teaches_peer_boundaries():
    """⚠️ 提示词必须教会模型 peer 场景的三条边界。"""
    from app.agents import QAAgent

    prompt = QAAgent.system_prompt(None)
    assert "get_peer_score" in prompt
    # ① 不能指定查谁
    assert "不能指定查谁" in prompt
    # ② 查不到不要编造
    assert "不要编造" in prompt
    # ③ peer 只有分数，没有评语/证据
    assert "评语" in prompt
    # ④ 规则分 ≠ 最终成绩
    assert "最终成绩" in prompt


def test_prompt_mentions_no_evidence_for_peer():
    """⚠️ 明确告诉模型 peer 数据**没有**证据与自评，避免它编造。"""
    from app.agents import QAAgent

    prompt = QAAgent.system_prompt(None)
    assert "证据" in prompt
    assert "自评" in prompt


# ---------------------------------------------------------------------------
# 4. 端到端
# ---------------------------------------------------------------------------


def test_peer_score_tool_roundtrip_cites_source(monkeypatch):
    """⚠️ 端到端：模型调用 peer 工具 → 引用必须原样带回。"""
    client = TestClient(main.app)
    real_client = httpx.Client

    def handler(req):
        assert req.url.path == "/v1/agent-tools/get_peer_score"
        assert req.headers["Authorization"] == "Bearer private-tool-token"
        return httpx.Response(200, json={"code": 0, "data": {
            "available": True,
            "total_score": 84.5,
            "confirmed_rating": "B",
            "metrics": [{
                "metric_code": "gmv", "metric_name": "销售额",
                "value": 120.0, "target": 100.0, "ratio": 1.2,
                "level": "A", "normalized_weight": 0.4,
                "level_score": 90.0, "contribution": 36.0,
                "formula": "0.4 × 90.0 = 36.0",
            }],
            "excluded_reason": "有指标未参与算分（缺少目标值），其余指标权重已重新归一",
            "note": "以上为该同事本考核周期的绩效摘要",
        }})

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    class Model:
        model = "script"

        def complete_with_tools(self, messages, tools, schema=None):
            if messages[-1]["role"] != "tool":
                # ⚠️ 模型只应传 period，不得传目标人
                args = {"period": "202611"}
                return Completion(tool_calls=[{
                    "id": "peer1", "name": "get_peer_score", "arguments": args,
                }]), None
            assert "84.5" in messages[-1]["content"]
            return Completion(content={
                "answer": "他本月总分 84.5 分，销售额指标达成率 120%，贡献 36 分。",
                "citations": ["tool:peer1"],
            }), None

    monkeypatch.setattr(main, "_client", Model())
    result = client.post(
        "/v1/agent/qa",
        json={
            "question": "销售乙这个月考得怎么样",
            "context": {"period": "202611", "task_id": "T", "scope": "peer"},
            "tool_token": "private-tool-token",
            "allowed_tools": ["get_peer_score"],
        },
        headers=AUTH_HEADERS,
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["citations"] == ["tool:peer1"]
    assert not body["degraded"]
    assert body["tool_calls"][0]["tool"] == "get_peer_score"
    assert body["tool_calls"][0]["ok"]
