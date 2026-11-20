"""工具调用失败时的服务端日志留痕测试。

⚠️ **为什么要有这个测试**
--------------------
``ToolRegistry.call`` 以前把工具异常**静默吞掉**（``except Exception``），
只给模型一个笼统的 ``TOOL_FAILED``。后果：

- 运维看到「问答降级了」，但**看不出断在哪一环** ——
  工具网关地址错、令牌过期、后端在重启、权限不足，全是同一个字符串；
- 排障只能靠临时插桩（试过一次：改代码 → 重启服务 → 复现 → 撤回改动，
  一轮下来十几分钟）。

所以现在要求：**异常必须留痕，且必须能区分故障类别**。

⚠️ **边界**：
- 日志里**不能出现令牌**（工具令牌是身份凭据）；
- 日志里**不能出现参数值**（可能含员工姓名等 PII）——
  只允许出现参数**键名**；
- 日志只进服务端，**不进模型上下文，也不回传给用户**。
"""

import logging

import httpx
import pytest
from app.tools import (
    ToolRegistry,
    ToolSpec,
    _classify_tool_error,
    _safe_error_detail,
)


def _registry(handler, *, name="get_metric_values", trace_id="trace-1"):
    result = ToolRegistry()
    result.register(ToolSpec(
        name=name,
        description="测试工具",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        trace_id=trace_id,
    ))
    return result


def _boom(exc: Exception):
    def handler(_args):
        raise exc
    return handler


# ---------------------------------------------------------------------------
# 1. 失败仍返回 TOOL_FAILED（契约不变）
# ---------------------------------------------------------------------------


def test_failure_still_returns_tool_failed(caplog):
    """对外契约不变：失败依然是 TOOL_FAILED，不抛异常。"""
    with caplog.at_level(logging.ERROR, logger="app.tools"):
        data, err = _registry(_boom(RuntimeError("boom"))).call(
            "get_metric_values", {}, {"get_metric_values"},
        )
    assert data is None
    assert err == "TOOL_FAILED"


# ---------------------------------------------------------------------------
# 2. 必须留痕，且带上工具名与 trace_id
# ---------------------------------------------------------------------------


def test_failure_is_logged_with_name_and_trace(caplog):
    """日志必须能定位到「哪个工具、哪一次请求」。"""
    with caplog.at_level(logging.ERROR, logger="app.tools"):
        _registry(_boom(RuntimeError("boom")), trace_id="trace-xyz").call(
            "get_metric_values", {}, {"get_metric_values"},
        )
    assert caplog.records, "工具失败必须写日志"
    text = caplog.text
    assert "get_metric_values" in text
    assert "trace-xyz" in text


# ---------------------------------------------------------------------------
# 3. 故障分类必须能区分「连不上」与「被拒绝」
# ---------------------------------------------------------------------------


def test_error_kind_distinguishes_connect_from_http_status():
    """这是这次改动的核心价值。

    以前两类故障都是 TOOL_FAILED，运维无法区分：
    - 后端进程在重启 → connect
    - 令牌过期       → http_401
    """

    def _http_error(status: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://127.0.0.1:8000/v1/agent-tools/x")
        response = httpx.Response(status, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    assert _classify_tool_error(httpx.ConnectError("refused")) == "connect"
    assert _classify_tool_error(_http_error(401)) == "http_401"
    assert _classify_tool_error(_http_error(403)) == "http_403"
    assert _classify_tool_error(_http_error(404)) == "http_404"
    assert _classify_tool_error(_http_error(500)) == "http_500"
    assert _classify_tool_error(httpx.ReadTimeout("slow")) == "read"
    assert _classify_tool_error(ValueError("工具响应无效")) == "other"


def test_log_contains_kind(caplog):
    """错误日志里必须带 kind，否则分类白做了。"""
    with caplog.at_level(logging.ERROR, logger="app.tools"):
        _registry(_boom(httpx.ConnectError("refused"))).call(
            "get_metric_values", {}, {"get_metric_values"},
        )
    assert "kind=connect" in caplog.text


# ---------------------------------------------------------------------------
# 4. 不得泄露令牌与参数值
# ---------------------------------------------------------------------------


def test_log_does_not_leak_token_or_argument_values(caplog):
    """⚠️ 令牌是身份凭据，参数值可能含 PII —— 都不能进日志。"""
    secret_token = "SUPER-SECRET-TOKEN-VALUE"
    secret_arg = "张三的手机号13800000000"

    with caplog.at_level(logging.ERROR, logger="app.tools"):
        _registry(_boom(RuntimeError(secret_arg))).call(
            "get_metric_values", {}, {"get_metric_values"},
        )
    text = caplog.text
    assert secret_token not in text
    assert secret_arg not in text


def test_safe_error_detail_for_status_error_has_no_body():
    """HTTP 错误的摘要只给状态码，不给响应体。

    响应体可能含用户数据（指标值、证据内容）。
    """
    request = httpx.Request("POST", "http://127.0.0.1:8000/v1/agent-tools/x")
    response = httpx.Response(401, request=request, json={"detail": "员工张三的成绩"})
    exc = httpx.HTTPStatusError("boom", request=request, response=response)
    detail = _safe_error_detail(exc)
    assert detail == "status=401"
    assert "张三" not in detail


# ---------------------------------------------------------------------------
# 5. 超时单独告警（不算错误，但同样要留痕）
# ---------------------------------------------------------------------------


def test_timeout_is_logged_as_warning(caplog):
    """超时走 TOOL_TIMEOUT，且是 warning 级别（不是 error）。"""
    with caplog.at_level(logging.WARNING, logger="app.tools"):
        data, err = _registry(_boom(TimeoutError("slow"))).call(
            "get_metric_values", {}, {"get_metric_values"},
        )
    assert data is None and err == "TOOL_TIMEOUT"
    assert any(record.levelno == logging.WARNING for record in caplog.records)
