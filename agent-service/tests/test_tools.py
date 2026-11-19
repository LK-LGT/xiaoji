"""工具注册与执行边界。"""

import threading
from dataclasses import replace

import httpx
import pytest
from app.tools import ToolRegistry, ToolSpec, http_handler

PARAMS = {"type": "object", "properties": {
    "period": {"type": "string", "pattern": r"^\d{6}$"},
}, "required": ["period"], "additionalProperties": False}


def spec(**kwargs):
    return ToolSpec(**{"name": "get_metric_values", "description": "查指标", "parameters": PARAMS,
                       "handler": lambda args: {"period": args["period"]}, **kwargs})


@pytest.mark.parametrize("changes", [
    {"read_only": False}, {"name": "../admin"}, {"timeout_s": 0},
    {"parameters": {"type": "object", "properties": {"employee_id": {"type": "string"}},
                    "additionalProperties": False}},
    {"parameters": {"type": "object", "properties": {"nested": {"type": "object", "properties": {
        "employee_id": {"type": "string"}}, "additionalProperties": False}}, "additionalProperties": False}},
    {"parameters": {"type": "object", "properties": {}}},
    {"parameters": {"type": "object", "$ref": "https://example.com/schema", "additionalProperties": False}},
])
def test_bad_registration(changes):
    with pytest.raises(ValueError):
        ToolRegistry().register(spec(**changes))


def test_duplicate_and_schema_copy():
    registry = ToolRegistry()
    registry.register(spec())
    with pytest.raises(ValueError):
        registry.register(spec())
    assert registry.schema(set()) == []
    schema = registry.schema({"get_metric_values"})
    schema[0]["function"]["parameters"]["required"].clear()
    assert registry.call("get_metric_values", {}, {"get_metric_values"})[1] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("args", [{}, {"period": 123456}, {"period": "2026-11"},
                                  {"period": "202611", "employee_id": "other"},
                                  {"period": "202611", "extra": True}])
def test_invalid_args_never_execute(args):
    def handler(args):
        pytest.fail("invalid args executed")
    registry = ToolRegistry()
    registry.register(spec(handler=handler))
    assert registry.call("get_metric_values", args, {"get_metric_values"})[1] == "INVALID_ARGUMENTS"


def test_result_limits_errors_and_redaction():
    registry = ToolRegistry()
    registry.register(spec(handler=lambda _: {"nested": {"items": [1, 2, 3]}, "phone": "13812345678"}, max_result_items=2))
    data, err = registry.call("get_metric_values", {"period": "202611"}, {"get_metric_values"})
    assert err is None and data["truncated"]
    assert data["data"]["nested"]["items"] == [1, 2]
    assert data["data"]["phone"] == "***"
    assert registry.call("get_metric_values", {}, set())[1] == "TOOL_FORBIDDEN"
    registry2 = ToolRegistry()
    registry2.register(spec(handler=lambda _: {"text": "x" * 1000}, max_chars=128))
    assert registry2.call("get_metric_values", {"period": "202611"}, {"get_metric_values"})[0]["truncated"]


def test_timeout_and_exception_do_not_escape():
    gate = threading.Event()
    registry = ToolRegistry()
    registry.register(spec(handler=lambda _: gate.wait(2), timeout_s=0.02))
    try:
        assert registry.call("get_metric_values", {"period": "202611"}, {"get_metric_values"})[1] == "TOOL_TIMEOUT"
    finally:
        gate.set()
    def fail(_):
        raise RuntimeError("secret-token-must-not-leak")
    other = ToolRegistry()
    other.register(replace(spec(), handler=fail))
    assert other.call("get_metric_values", {"period": "202611"}, {"get_metric_values"}) == (None, "TOOL_FAILED")


def test_http_fixed_url_auth_and_envelope(monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"ok": True}})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    handler = http_handler("http://backend:8000", "private-token", "ping")
    assert handler({}) == {"ok": True}
    assert str(requests[0].url) == "http://backend:8000/v1/agent-tools/ping"
    assert requests[0].headers["Authorization"] == "Bearer private-token"
    assert "private-token" not in repr(handler)
    def redirect(request):
        return httpx.Response(307, headers={"Location": "http://untrusted.example/private"})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(redirect), **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        handler({})
    with pytest.raises(ValueError):
        http_handler("file:///tmp/data", "token", "ping")
    with pytest.raises(ValueError):
        http_handler("http://backend", "token", "../admin")
