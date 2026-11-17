"""飞书客户端的**网络错误重试**护栏（2026-11-17）。

背景
----
一次 DNS 抖动（``getaddrinfo failed``）让整条飞书链路 500 ——
排查发现 ``_get_access_token`` 把**网络错误**抛成了 ``FeishuTokenError``，
而它的默认 ``retryable=False`` ⇒ **重试逻辑整个被跳过**。

⚠️ 对比：同一个客户端里 ``_request_with_token`` 的网络错误
   是 ``retryable=True`` + 退避重试的。**不一致就是 bug。**

⇒ 瞬时故障（DNS 抖动 / 临时断网 / 连接被重置）应该重试，
   而不是直接失败。

⚠️ **区分**：
   - 网络层错误（超时 / 连接失败）→ **可重试**
   - 业务错误（``code != 0``，如 app_secret 错）→ **不可重试**
     （重试多少次都一样，错的是配置）
"""

from __future__ import annotations

import httpx
import pytest

from backend.app.services.feishu_client import (
    FeishuClient,
    FeishuError,
    FeishuTokenError,
    MockTransport,
)

TOKEN_PATH = "/auth/v3/tenant_access_token/internal"
APP_TOKEN_PATH = "/auth/v3/app_access_token/internal"


def _network_error(request: httpx.Request) -> httpx.Response:
    """构造一个「连接失败」响应（等价于 getaddrinfo failed）。"""
    return httpx.Response(
        0,
        request=request,
    )


def _client_with_network_error(path: str) -> FeishuClient:
    """第一次调用抛连接错误，之后正常返回 token。"""
    transport = MockTransport(
        {
            path: [
                httpx.Response(0, request=httpx.Request("POST", f"https://x{path}")),
                {
                    "status_code": 200,
                    "json": {"code": 0, "tenant_access_token": "tok-1", "expire": 7200},
                },
            ]
        }
    )
    return FeishuClient(
        "app",
        "secret",
        transport=transport,
        sleep=lambda _: None,
    )


def _raising_transport(path: str, exc: Exception) -> MockTransport:
    """总是抛指定异常的 transport。"""

    class _Raising(httpx.BaseTransport):
        def __init__(self) -> None:
            self.calls = 0

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            self.calls += 1
            raise exc

    return _Raising()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. 租户态 token：网络错误必须可重试
# ---------------------------------------------------------------------------


def test_tenant_token_network_error_is_retryable():
    """⚠️ 核心护栏：网络错误必须标 ``retryable=True``。

    破坏它（抛 ``FeishuTokenError``，默认 retryable=False）
    ⇒ 重试逻辑被跳过 ⇒ DNS 抖动直接 500。
    """
    exc = httpx.ConnectError("getaddrinfo failed")
    client = FeishuClient(
        "app",
        "secret",
        transport=_raising_transport(TOKEN_PATH, exc),  # type: ignore[arg-type]
        sleep=lambda _: None,
        max_retries=2,
    )

    with pytest.raises(FeishuError) as caught:
        client._get_access_token()

    error = caught.value
    assert error.retryable is True, (
        f"网络错误必须可重试，但 retryable={error.retryable!r} "
        f"（类型 {type(error).__name__}）—— 重试逻辑会被整个跳过"
    )


def test_tenant_token_network_error_actually_retries():
    """⚠️ 光标 ``retryable=True`` 不够 —— 必须**真的重试**。

    这里走**真实调用链**：``_request`` → ``_request_with_token`` →
    ``_get_access_token``。重试循环在 ``_request_with_token`` 里，
    它靠异常的 ``retryable`` 标志决定要不要退避重试。

    第一次连接失败、第二次成功 ⇒ 能拿到 token。
    """
    attempts = {"n": 0}

    class _Flaky(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                # 第 1 次：拿 token 时网络失败
                raise httpx.ConnectError("getaddrinfo failed")
            if request.url.path.endswith(TOKEN_PATH):
                # ⚠️ token 响应**不在 data 里** —— 是顶层字段
                return httpx.Response(
                    200,
                    json={"code": 0, "tenant_access_token": "tok-ok", "expire": 7200},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"code": 0, "data": {"items": []}},
                request=request,
            )

    client = FeishuClient(
        "app", "secret", transport=_Flaky(), sleep=lambda _: None, max_retries=3
    )
    # 走真实链路：会先取 token（失败 → 重试 → 成功），再发业务请求。
    client._request("GET", "/contact/v3/users")
    assert attempts["n"] >= 2, f"应该重试，实际请求了 {attempts['n']} 次"


def test_tenant_token_business_error_is_not_retryable():
    """⚠️ 回归护栏：业务错误（app_secret 错）**不可重试**。

    重试多少次都一样 —— 错的是配置，重试只是浪费配额。
    """
    transport = MockTransport(
        {
            TOKEN_PATH: [
                {"status_code": 200, "json": {"code": 99991663, "msg": "app not exist"}},
            ]
        }
    )
    client = FeishuClient("app", "bad", transport=transport, sleep=lambda _: None)

    with pytest.raises(FeishuTokenError) as caught:
        client._get_access_token()
    assert caught.value.retryable is False, "业务错误不该标成可重试"


# ---------------------------------------------------------------------------
# 2. 应用态 token
# ---------------------------------------------------------------------------


def test_app_token_network_error_is_retryable():
    """⚠️ 应用态 token 的网络错误同样必须可重试。"""
    exc = httpx.ConnectError("getaddrinfo failed")
    client = FeishuClient(
        "app",
        "secret",
        transport=_raising_transport(APP_TOKEN_PATH, exc),  # type: ignore[arg-type]
        sleep=lambda _: None,
        max_retries=1,
    )

    with pytest.raises(FeishuError) as caught:
        client.get_app_access_token()

    assert caught.value.retryable is True, (
        f"应用态网络错误必须可重试，但 retryable={caught.value.retryable!r}"
    )


# ---------------------------------------------------------------------------
# 3. 用户态 token
# ---------------------------------------------------------------------------


def test_user_token_network_error_is_retryable():
    """⚠️ 用户态 token 换取的网络错误同样必须可重试。"""
    exc = httpx.ConnectError("getaddrinfo failed")
    client = FeishuClient(
        "app",
        "secret",
        transport=_raising_transport("/authen/v2/oauth/token", exc),  # type: ignore[arg-type]
        sleep=lambda _: None,
        max_retries=1,
    )

    with pytest.raises(FeishuError) as caught:
        client.exchange_user_token("code-123")

    assert caught.value.retryable is True, (
        f"用户态网络错误必须可重试，但 retryable={caught.value.retryable!r}"
    )


# ---------------------------------------------------------------------------
# 4. 护栏自检：确认断言**不是恒真**
# ---------------------------------------------------------------------------


def test_retryable_guard_is_not_vacuous():
    """⚠️ 护栏自检：断言必须**真的能抓到**「retryable 被设成 False」。

    做法：手工造一个 ``retryable=False`` 的错误，
    确认同样的断言会失败 —— 说明断言不是恒真。
    """
    fake = FeishuError("模拟：网络错误被错标成不可重试", retryable=False)
    try:
        assert fake.retryable is True
    except AssertionError:
        pass
    else:
        raise AssertionError("断言恒真 —— 抓不到 retryable 被设成 False")


def test_feishu_token_error_defaults_to_not_retryable():
    """⚠️ 说明**为什么**这个 bug 危险：``FeishuTokenError`` 默认不可重试。

    ⇒ 所以「顺手抛个 FeishuTokenError」就会静默关掉重试。
    """
    err = FeishuTokenError("boom")
    assert err.retryable is False
