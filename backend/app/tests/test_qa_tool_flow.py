"""两个 FastAPI 应用通过 HTTP transport 连接的完整问答链路。"""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.dependencies import get_current_user
from backend.app.domain.models import AiToolCall, AiTrace, Employee, Score
from backend.app.routes.qa import router
from backend.app.services.agent_tool_token import verify_agent_tool_token
from backend.app.services.review_service import AgentClient
from backend.app.tests.test_agent_business_tools import setup  # noqa: F401


@pytest.fixture
def flow(setup, monkeypatch):
    db, client = setup
    app = client.app
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, "E")
    yield db, client


def test_fallback_preserves_data_and_releases_transaction(flow, monkeypatch):
    db, client = flow
    async def unavailable(self, payload):
        assert not db.in_transaction()
        claims = verify_agent_tool_token(payload["tool_token"])
        assert claims.sub == "E" and set(claims.allow) == {"get_metric_values", "get_evidence"}
        raise RuntimeError("unavailable")
    monkeypatch.setattr(AgentClient, "__init__", lambda self: None)
    monkeypatch.setattr(AgentClient, "qa", unavailable)
    result = client.post("/v1/qa/ask", json={"question": "指标如何", "task_id": "T"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["degraded"] and body["data"]["get_metric_values"]["metrics"][0]["ratio"] == 1.2
    assert db.query(AiTrace).one().status == "degraded"
    assert db.query(Score).count() == 0


def test_invalid_agent_response_degrades(flow, monkeypatch):
    _, client = flow
    monkeypatch.setattr(AgentClient, "__init__", lambda self: None)
    async def invalid(self, payload):
        return {"answer": "伪造", "citations": ["missing"], "sources": [], "score": 100}
    monkeypatch.setattr(AgentClient, "qa", invalid)
    result = client.post("/v1/qa/ask", json={"question": "?", "task_id": "T"})
    assert result.json()["degraded"] and "score" not in result.json()


def test_disabled_user_rejected(flow):
    db, client = flow
    db.get(Employee, "E").account_status = "disabled"
    db.commit()
    assert client.post("/v1/qa/ask", json={"question": "?", "task_id": "T"}).status_code == 403


def test_invalid_scope_and_parameters_rejected(flow):
    _, client = flow
    assert client.post("/v1/qa/ask", json={"question": "?", "task_id": "missing"}).status_code == 404
    assert client.post("/v1/qa/ask", json={"question": "?", "task_id": "T", "period": "202610"}).status_code == 400
    assert client.post("/v1/qa/ask", json={"question": "?", "task_id": "T", "target_employee_id": "other"}).status_code == 422


def test_full_http_tool_roundtrip(flow, monkeypatch):
    db, client = flow
    pytest.importorskip("jsonschema", reason="跨服务集成需安装 agent-service/requirements.txt")
    # 用独立包名加载 L3，避免与 backend/app 的包名冲突。
    root = Path(__file__).resolve().parents[3] / "agent-service" / "app"
    name = "qa_flow_agent"
    package = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(package)
    monkeypatch.setitem(sys.modules, name, module)
    package.loader.exec_module(module)
    qa = importlib.import_module(f"{name}.qa")
    base = importlib.import_module(f"{name}.base")
    calls = []
    class Model:
        model = "integration"
        def complete_with_tools(self, messages, tools, schema=None):
            calls.append(messages)
            assert "tool_token" not in str(messages)
            if messages[-1]["role"] != "tool":
                return base.Completion(tool_calls=[{"id": "roundtrip", "name": "get_metric_values", "arguments": {"period": "202611"}}]), None
            assert "1.2" in messages[-1]["content"]
            return base.Completion(content={"answer": "本月达成率为1.2", "citations": ["tool:roundtrip"]}), None
    l3 = FastAPI()
    @l3.post("/v1/agent/qa")
    def endpoint(req: qa.QARequest):
        return qa.answer_question(req, Model())
    l3client = TestClient(l3)
    real_client = httpx.Client
    def route(request):
        return client.post(request.url.path, content=request.content, headers={"Authorization": request.headers["Authorization"], "X-Agent-Trace-Id": request.headers.get("X-Agent-Trace-Id", ""), "Content-Type": "application/json"})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(route), **kwargs))
    monkeypatch.setattr(AgentClient, "__init__", lambda self: None)
    async def to_l3(self, payload):
        assert not db.in_transaction()
        import asyncio
        response = await asyncio.to_thread(l3client.post, "/v1/agent/qa", json=payload)
        response.raise_for_status()
        return response.json()
    monkeypatch.setattr(AgentClient, "qa", to_l3)
    result = client.post("/v1/qa/ask", json={"question": "指标如何", "task_id": "T"})
    assert result.status_code == 200, result.text
    assert not result.json()["degraded"], result.text
    assert result.json()["citations"] == ["tool:roundtrip"]
    assert len(calls) == 2 and db.query(AiToolCall).one().ok
    assert db.query(AiTrace).one().status == "ok"
    assert db.query(AiToolCall).one().trace_id == result.json()["trace_id"]
    assert db.query(Score).count() == 0
