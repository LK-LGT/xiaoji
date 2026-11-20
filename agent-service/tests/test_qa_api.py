"""问答接口鉴权、工具装配、凭据隔离及引用校验。"""

import httpx
from fastapi.testclient import TestClient
from tests._test_secret import AUTH_HEADERS

# isort: split
from app import main
from app.base import Completion


def body():
    return {"question": "本月指标如何", "context": {"period": "202611", "task_id": "T", "scope": "self"},
            "tool_token": "private-tool-token", "allowed_tools": ["get_metric_values"]}


def test_qa_auth_and_reject_client_gateway():
    client = TestClient(main.app)
    assert client.post("/v1/agent/qa", json=body()).status_code == 401
    assert client.post("/v1/agent/qa", json={**body(), "tool_base_url": "http://other"}, headers=AUTH_HEADERS).status_code == 422


def test_qa_tool_roundtrip_and_secret_isolation(monkeypatch):
    client = TestClient(main.app)
    seen = []
    real_client = httpx.Client
    def handler(req):
        assert req.headers["Authorization"] == "Bearer private-tool-token"
        return httpx.Response(200, json={"code": 0, "data": {"metrics": [{"ratio": 1.2}]}})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    class Model:
        model = "script"
        def complete_with_tools(self, messages, tools, schema=None):
            seen.append((messages, tools))
            assert "private-tool-token" not in str(messages) + str(tools)
            assert len(tools) == 1
            if messages[-1]["role"] != "tool":
                return Completion(tool_calls=[{"id": "a", "name": "get_metric_values", "arguments": {"period": "202611"}}]), None
            return Completion(content={"answer": "达成率为1.2", "citations": ["tool:a"]}), None
    monkeypatch.setattr(main, "_client", Model())
    result = client.post("/v1/agent/qa", json=body(), headers=AUTH_HEADERS)
    assert result.status_code == 200, result.text
    assert result.json()["citations"] == ["tool:a"] and not result.json()["degraded"]
    assert result.json()["tool_calls"][0]["ok"]
    assert "private-tool-token" not in result.text
    assert len(seen) == 2


def test_fabricated_citation_degrades(monkeypatch):
    class Model:
        model = "bad"
        def complete_with_tools(self, messages, tools, schema=None):
            return Completion(content={"answer": "伪造结论", "citations": ["other-employee"]}), None
    monkeypatch.setattr(main, "_client", Model())
    result = TestClient(main.app).post("/v1/agent/qa", json=body(), headers=AUTH_HEADERS)
    assert result.json()["degraded"] and result.json()["citations"] == []
