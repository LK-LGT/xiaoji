"""OpenAI 兼容客户端测试（P3-2）。

⚠️ 全部用 **Mock HTTP**（`httpx.MockTransport`）—— **不烧真实 token**，
也不依赖外网。真实链路的验证在 `test_model_integration.py`（可跳过）。

测的是**契约与安全不变量**，不是渲染细节：
- 任何异常都归一为 ``(None, error)``，绝不抛给调用方
- API Key 只出现在 Authorization 头，不进日志、不进错误信息
- 工具调用与文本输出两条分支各自正确
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.openai_client import OpenAICompatibleClient, build_client_from_env

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def make_client(handler, **kwargs) -> OpenAICompatibleClient:
    """构造一个用 MockTransport 的客户端。"""
    defaults = {
        "api_key": "sk-test-key",
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        "max_retries": 0,
    }
    defaults.update(kwargs)
    return OpenAICompatibleClient(transport=httpx.MockTransport(handler), **defaults)


def chat_body(content: str | None = None, tool_calls: list | None = None) -> dict:
    message: dict = {}
    if content is not None:
        message["content"] = content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


# ---------------------------------------------------------------------------
# 构造与配置
# ---------------------------------------------------------------------------


def test_missing_api_key_is_rejected():
    """Key 缺失必须**构造时**就失败 —— 不能等到请求时才 401。"""
    with pytest.raises(ValueError, match="API Key"):
        OpenAICompatibleClient(api_key="", base_url="https://x/v1", model="m")


def test_missing_base_url_is_rejected():
    with pytest.raises(ValueError, match="Base URL"):
        OpenAICompatibleClient(api_key="k", base_url="", model="m")


def test_missing_model_is_rejected():
    with pytest.raises(ValueError, match="模型名"):
        OpenAICompatibleClient(api_key="k", base_url="https://x/v1", model="")


def test_build_client_from_env_returns_none_when_incomplete(monkeypatch):
    """配置不全 → 返回 None（由调用方退回 echo），不抛异常。"""
    monkeypatch.delenv("AGENT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_MODEL_NAME", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert build_client_from_env() is None


def test_build_client_from_env_reads_config(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_API_KEY", "sk-abc")
    monkeypatch.setenv("AGENT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("AGENT_MODEL_NAME", "deepseek-chat")
    client = build_client_from_env()
    assert client is not None
    assert client.model == "deepseek-chat"
    assert client._base_url == "https://api.deepseek.com/v1"


def test_build_client_falls_back_to_model_name(monkeypatch):
    """只给 MODEL_NAME 时也能构造（兼容旧配置写法）。"""
    monkeypatch.setenv("AGENT_MODEL_API_KEY", "sk-abc")
    monkeypatch.setenv("AGENT_MODEL_BASE_URL", "https://x/v1")
    monkeypatch.delenv("AGENT_MODEL_NAME", raising=False)
    monkeypatch.setenv("MODEL_NAME", "legacy-model")
    client = build_client_from_env()
    assert client is not None and client.model == "legacy-model"


# ---------------------------------------------------------------------------
# 单轮调用
# ---------------------------------------------------------------------------


def test_complete_parses_json_object():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body(json.dumps({"ratings": [{"level": "A"}]})))

    client = make_client(handler)
    data, error = client.complete("sys", json.dumps({"agent": "rating"}))
    assert error is None
    assert data == {"ratings": [{"level": "A"}]}


def test_complete_rejects_non_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body("这不是 JSON"))

    client = make_client(handler)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert error and "JSON" in error


def test_complete_rejects_empty_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body("   "))

    client = make_client(handler)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert error and "为空" in error


def test_complete_rejects_empty_choices():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = make_client(handler)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert error


def test_complete_sends_authorization_header():
    """Key 必须走 Authorization 头。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=chat_body("{}"))

    client = make_client(handler)
    client.complete("sys", "{}")
    assert seen["auth"] == "Bearer sk-test-key"
    assert seen["url"] == "https://api.example.com/v1/chat/completions"


def test_complete_requests_json_object_response_format():
    """要求 JSON 输出 —— 否则模型会返回带 markdown 围栏的字符串。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat_body("{}"))

    client = make_client(handler)
    client.complete("sys", "{}")
    assert seen["body"]["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# 工具调用
# ---------------------------------------------------------------------------


def test_complete_with_tools_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body(tool_calls=[{
            "id": "call_abc",
            "type": "function",
            "function": {"name": "get_metric_values", "arguments": '{"period": "202611"}'},
        }]))

    client = make_client(handler)
    completion, error = client.complete_with_tools([{"role": "user", "content": "q"}], [])
    assert error is None
    assert completion is not None
    assert len(completion.tool_calls) == 1
    call = completion.tool_calls[0]
    assert call.id == "call_abc"
    assert call.name == "get_metric_values"
    assert call.arguments == {"period": "202611"}


def test_complete_with_tools_sends_tools_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=chat_body("{}"))

    client = make_client(handler)
    tools = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}]
    client.complete_with_tools([{"role": "user", "content": "q"}], tools)
    assert seen["body"]["tools"] == tools
    assert seen["body"]["tool_choice"] == "auto"


def test_tool_call_with_broken_arguments_becomes_empty_dict():
    """模型返回非法 arguments JSON —— 不能崩，退化成空参数（上层会校验拦截）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body(tool_calls=[{
            "id": "c1", "function": {"name": "get_evidence", "arguments": "{不是 JSON"},
        }]))

    client = make_client(handler)
    completion, error = client.complete_with_tools([{"role": "user", "content": "q"}], [])
    assert error is None
    assert completion.tool_calls[0].arguments == {}


def test_tool_call_without_id_gets_synthetic_id():
    """模型漏给 id —— 补一个合成 id，否则上层的去重逻辑会崩。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body(tool_calls=[{
            "function": {"name": "get_evidence", "arguments": "{}"},
        }]))

    client = make_client(handler)
    completion, error = client.complete_with_tools([{"role": "user", "content": "q"}], [])
    assert error is None
    assert completion.tool_calls[0].id


def test_tool_call_content_is_dropped_to_avoid_ambiguity():
    """content 与 tool_calls 同时出现 = 歧义 —— 这里**丢掉 content**。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = chat_body(content="顺便说句话", tool_calls=[{
            "id": "c1", "function": {"name": "get_evidence", "arguments": "{}"},
        }])
        return httpx.Response(200, json=body)

    client = make_client(handler)
    completion, error = client.complete_with_tools([{"role": "user", "content": "q"}], [])
    assert error is None
    assert completion.content is None
    assert len(completion.tool_calls) == 1


def test_complete_with_tools_wraps_plain_text_as_answer():
    """模型不返回 JSON 时 —— 包装成 answer，交给上层 Schema 校验。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body("直接回答"))

    client = make_client(handler)
    completion, error = client.complete_with_tools([{"role": "user", "content": "q"}], [])
    assert error is None
    assert completion.content["answer"] == "直接回答"


# ---------------------------------------------------------------------------
# 错误处理 —— 关键安全不变量
# ---------------------------------------------------------------------------


def test_auth_error_does_not_retry():
    """401/403 重试没有意义 —— 必须只打一次。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    client = make_client(handler, max_retries=2)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert error and "鉴权" in error
    assert calls["n"] == 1


def test_server_error_is_retried_then_reported():
    """5xx 可重试 —— 重试耗尽后必须报错而不是静默返回空。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "busy"})

    client = make_client(handler, max_retries=2)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert error and "503" in error
    assert calls["n"] == 3


def test_timeout_is_reported_not_raised():
    """超时必须被捕获 —— 上层要能降级，不能抛异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    client = make_client(handler, max_retries=1)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert error and "超时" in error


def test_network_error_does_not_leak_key():
    """异常信息里绝不能出现 API Key。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = make_client(handler, max_retries=0)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert error is not None
    assert "sk-test-key" not in error


def test_error_message_never_contains_api_key():
    """非 200 响应时，错误信息也不该带 Key。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "sk-test-key leaked"}})

    client = make_client(handler, max_retries=0)
    data, error = client.complete("sys", "{}")
    assert data is None
    assert "sk-test-key" not in (error or "")


# ---------------------------------------------------------------------------
# 与智能体管线的集成（Mock HTTP）
# ---------------------------------------------------------------------------


def test_rating_agent_produces_schema_valid_output():
    """真模型路径：评级建议必须能过 Schema 校验（且**不含 score 字段**）。"""
    from app.agents import RatingSuggestionAgent
    from app.base import RatingOutput

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "ratings": [{
                "dimension": "质量",
                "level": "A",
                "reason": "达成率 1.2，落在 A 区间",
                "evidence_ids": ["e1"],
            }],
        }
        return httpx.Response(200, json=chat_body(json.dumps(payload, ensure_ascii=False)))

    client = make_client(handler)
    agent = RatingSuggestionAgent(client)
    result = agent.run(
        metrics=[{"code": "q", "dimension": "质量", "value": 120, "target": 100,
                  "ratio": 1.2, "ratio_level": "A", "level_scale": ["C", "B", "A"]}],
        evidence=[{"id": "e1", "summary": "交付了 3 个需求"}],
        period="202611",
    )
    assert not result.degraded
    assert result.data["ratings"][0]["level"] == "A"
    # ⚠️ 输出契约里物理上没有 score 字段 —— 模型无法改分。
    assert "score" not in RatingOutput.model_fields
    assert "score" not in result.data


def test_comment_agent_output_is_grounded_in_evidence():
    """评语必须带引用，且引用来自传入的证据。"""
    from app.agents import SummarizeAgent

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "markdown": "## 本月总结\n- 质量：达成率 1.2\n  依据：e1",
            "evidence_references": ["e1"],
        }
        return httpx.Response(200, json=chat_body(json.dumps(payload, ensure_ascii=False)))

    client = make_client(handler)
    agent = SummarizeAgent(client)
    result = agent.run(
        employee={"name": "张三", "id": "E1"},
        metrics=[{"code": "q", "dimension": "质量", "ratio": 1.2, "ratio_level": "A"}],
        evidence=[{"id": "e1", "summary": "交付了 3 个需求"}],
        period="202611",
    )
    assert not result.degraded
    assert result.data["evidence_references"] == ["e1"]


def test_degrades_when_model_returns_invalid_schema():
    """模型返回不合契约的内容 —— 不能把模型瞎编的字段透传出去。"""
    from app.agents import RatingSuggestionAgent

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_body(json.dumps({"unexpected": "shape"})))

    client = make_client(handler)
    agent = RatingSuggestionAgent(client)
    result = agent.run(
        metrics=[{"code": "q", "dimension": "质量", "ratio": 1.2, "ratio_level": "A",
                  "level_scale": ["C", "B", "A"]}],
        evidence=[{"id": "e1"}],
        period="202611",
    )
    assert "unexpected" not in result.data
