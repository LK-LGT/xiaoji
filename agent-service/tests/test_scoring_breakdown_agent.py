"""评分构成工具（``get_scoring_breakdown``）在 L3 侧的装配与提示词测试。

⚠️ **这里锁定三件事**
-------------------
1. **工具被正确注册** —— 模型能看见它、参数形状对。
2. **提示词教会模型怎么用** —— 特别是「未参与算分 ≠ 扣分」
   和「规则重算分 ≠ 最终成绩」这两条。
   没写进提示词的话，模型很容易把分数说成「你的最终成绩」，
   或者让用户误以为缺数据被扣分了。
3. **工具名不会被客户端伪造** —— 允许列表由服务端签发的令牌决定。
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


def test_registry_registers_scoring_breakdown_with_period_param():
    """⚠️ 新工具必须注册，且参数形状与其它指标类工具一致。"""
    registry = build_tool_registry("tok")
    spec = registry.get("get_scoring_breakdown")
    assert spec is not None
    assert spec.description
    assert "算式" in spec.description or "贡献分" in spec.description
    # period 参数与 get_metric_values 同形
    assert spec.parameters["properties"]["period"]["pattern"] == r"^\d{6}$"


def test_breakdown_is_not_available_without_allowance():
    """⚠️ 令牌允许列表里没有 → 模型看不见这个工具。"""
    registry = build_tool_registry("tok")
    allowed = {"get_metric_values"}
    schema = registry.schema(allowed)
    names = {t["function"]["name"] for t in schema}
    assert "get_scoring_breakdown" not in names
    assert "get_metric_values" in names


def test_breakdown_available_when_allowed():
    registry = build_tool_registry("tok")
    schema = registry.schema({"get_scoring_breakdown"})
    names = {t["function"]["name"] for t in schema}
    assert names == {"get_scoring_breakdown"}


def test_qa_request_accepts_four_tools():
    """⚠️ allowed_tools 上限要跟着放宽到 4。"""
    req = QARequest(
        question="这分数怎么算的",
        context={"period": "202611", "task_id": "T", "scope": "self"},
        tool_token="tok",
        allowed_tools=[
            "get_metric_values",
            "get_evidence",
            "get_department_stats",
            "get_scoring_breakdown",
        ],
    )
    assert len(req.allowed_tools) == 4


def test_qa_request_rejects_unknown_tool():
    """⚠️ 未注册的工具名 → 422（不能靠客户端塞任意工具名）。"""
    with pytest.raises(Exception):
        QARequest(
            question="q",
            context={"period": "202611", "task_id": "T", "scope": "self"},
            tool_token="tok",
            allowed_tools=["get_everything"],  # type: ignore[list-item]
        )


def test_prompt_teaches_breakdown_usage():
    """⚠️ 提示词必须教会模型「怎么解释分数」。

    漏了这两条，后果分别是：
      - 说成「你的最终成绩是 X 分」（规则分 ≠ 最终成绩）
      - 用户以为「缺数据 = 扣分」（实际是权重重新归一）
    """
    from app.agents import QAAgent

    prompt = QAAgent.system_prompt(None)
    assert "get_scoring_breakdown" in prompt
    assert "最终成绩" in prompt
    assert "excluded_items" in prompt
    assert "重新归一" in prompt


def test_breakdown_tool_roundtrip_cites_source(monkeypatch):
    """⚠️ 端到端：模型调用 breakdown → 引用必须原样带回。"""
    client = TestClient(main.app)
    seen = []
    real_client = httpx.Client

    def handler(req):
        assert req.url.path == "/v1/agent-tools/get_scoring_breakdown"
        assert req.headers["Authorization"] == "Bearer private-tool-token"
        return httpx.Response(200, json={"code": 0, "data": {
            "available": True,
            "items": [{
                "metric_code": "GMV", "metric_name": "销售额",
                "raw_weight": 100, "normalized_weight": 1.0,
                "level": "A", "level_score": 100.0, "contribution": 100.0,
                "formula": "1.0 × 100.0 = 100.0",
            }],
            "excluded_items": [],
            "total_score": 100.0,
            "note": "以上为按冻结配置重算的规则得分",
        }})

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    class Model:
        model = "script"

        def complete_with_tools(self, messages, tools, schema=None):
            seen.append((messages, tools))
            if messages[-1]["role"] != "tool":
                return Completion(tool_calls=[{
                    "id": "bd1", "name": "get_scoring_breakdown", "arguments": {"period": "202611"},
                }]), None
            # 工具结果里确实带着算式
            assert "1.0 × 100.0 = 100.0" in messages[-1]["content"]
            return Completion(content={
                "answer": "销售额权重 100%，等级 A，贡献 100 分。",
                "citations": ["tool:bd1"],
            }), None

    monkeypatch.setattr(main, "_client", Model())
    result = client.post(
        "/v1/agent/qa",
        json={
            "question": "这分数是怎么算出来的",
            "context": {"period": "202611", "task_id": "T", "scope": "self"},
            "tool_token": "private-tool-token",
            "allowed_tools": ["get_scoring_breakdown"],
        },
        headers=AUTH_HEADERS,
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["citations"] == ["tool:bd1"]
    assert not body["degraded"]
    assert body["tool_calls"][0]["tool"] == "get_scoring_breakdown"
    assert body["tool_calls"][0]["ok"]
