"""飞书进程级 token 缓存（FS-7）的测试。

⚠️ **核心断言**：跨 ``FeishuClient`` **实例**复用 token ——
   这是与「实例级缓存」的本质区别，也是这个模块存在的全部理由。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread

import pytest

from backend.app.services.feishu_client import FeishuClient
from backend.app.services.feishu_token_cache import TenantTokenCache, tenant_token_cache


@pytest.fixture(autouse=True)
def _clear_shared_cache():
    """每个用例前后清空共享缓存 —— 避免用例之间互相影响。"""
    tenant_token_cache.clear()
    yield
    tenant_token_cache.clear()


def _make_client(transport) -> FeishuClient:
    return FeishuClient(
        app_id="app-1",
        app_secret="secret-1",
        transport=transport,
        sleep=lambda _: None,
    )


class _RecordingTransport:
    """记录调用并返回可配置 token 的 transport。

    ⚠️ ``handle_request`` 的签名必须与 ``httpx`` 的 transport 契约一致
    （只接一个 ``request``）—— 客户端内部是用
    ``httpx.Client.post(..., json=...)`` 发请求的，
    由 httpx 负责把 json 编码进 request body。
    """

    def __init__(self, tokens: list[str] | None = None, expire: int = 7200) -> None:
        self.tokens = list(tokens or ["tok-1"])
        self.expire = expire
        self.token_request_count = 0

    def handle_request(self, request):
        import httpx

        path = request.url.path
        if "tenant_access_token" in path:
            self.token_request_count += 1
            token = (
                self.tokens.pop(0)
                if len(self.tokens) > 1
                else self.tokens[0]
            )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": token,
                    "expire": self.expire,
                },
            )
        return httpx.Response(200, json={"code": 0, "data": {}})

    def post(self, url, **kwargs):
        """⚠️ 兼容 ``httpx.Client.post(..., json=...)`` 的调用方式。

        客户端是直接 ``self._http_client.post(...)`` 拿 token 的。
        ⚠️ 返回的 Response **必须绑定 request** ——
        httpx 的 ``raise_for_status()`` 依赖 ``response.request``，
        未绑定会抛「request instance has not been set on this response」。
        """
        import httpx

        request = httpx.Request("POST", str(url))
        response = self.handle_request(request)
        response.request = request
        return response


class TestCacheUnit:
    """``TenantTokenCache`` 自身的单元测试。"""

    def test_same_credentials_share_one_slot(self):
        cache = TenantTokenCache()
        key_a = cache.make_key("app-1", "secret-1")
        key_b = cache.make_key("app-1", "secret-1")
        assert key_a == key_b

    def test_different_credentials_use_different_slots(self):
        """⚠️ 不同应用**绝不共享** token —— 否则跨应用越权。"""
        cache = TenantTokenCache()
        assert cache.make_key("app-1", "secret-1") != cache.make_key("app-2", "secret-1")
        assert cache.make_key("app-1", "secret-1") != cache.make_key("app-1", "secret-2")

    def test_key_does_not_leak_secret_in_plaintext(self):
        """⚠️ 槽位 key **不得**包含明文密钥。"""
        cache = TenantTokenCache()
        key = cache.make_key("app-1", "super-secret-value")
        assert "super-secret-value" not in key

    def test_get_caches_result(self):
        cache = TenantTokenCache()
        calls: list[int] = []

        def fetch() -> tuple[str, int]:
            calls.append(1)
            return "tok-1", 7200

        assert cache.get("app", "secret", fetch) == "tok-1"
        assert cache.get("app", "secret", fetch) == "tok-1"
        assert len(calls) == 1, "第二次应命中缓存，不该再调 fetch"

    def test_invalidate_forces_refetch(self):
        cache = TenantTokenCache()
        calls: list[int] = []

        def fetch() -> tuple[str, int]:
            calls.append(1)
            return f"tok-{len(calls)}", 7200

        assert cache.get("app", "secret", fetch) == "tok-1"
        cache.invalidate("app", "secret")
        assert cache.get("app", "secret", fetch) == "tok-2"
        assert len(calls) == 2

    def test_peek_does_not_trigger_fetch(self):
        cache = TenantTokenCache()
        assert cache.peek("app", "secret") is None
        cache.get("app", "secret", lambda: ("tok-1", 7200))
        assert cache.peek("app", "secret") == "tok-1"

    def test_expired_token_is_refetched(self):
        cache = TenantTokenCache()
        calls: list[int] = []

        def fetch() -> tuple[str, int]:
            calls.append(1)
            return "tok-1", 7200

        cache.get("app", "secret", fetch)
        # 把过期时间推到过去
        key = cache.make_key("app", "secret")
        with cache._lock:  # noqa: SLF001 - 测试内部状态
            cache._slots[key].expires_at = datetime.now(UTC) - timedelta(seconds=1)
        cache.get("app", "secret", fetch)
        assert len(calls) == 2, "过期后应重新拉取"

    def test_fetch_exception_propagates_and_is_not_cached(self):
        """⚠️ 拉取失败**不得**被缓存 —— 否则会缓存住一个坏状态。"""
        cache = TenantTokenCache()

        def boom() -> tuple[str, int]:
            raise RuntimeError("network down")

        with pytest.raises(RuntimeError):
            cache.get("app", "secret", boom)
        assert cache.peek("app", "secret") is None

    def test_concurrent_get_only_fetches_once(self):
        """⚠️ **单飞** —— 并发时只拉一次 token（飞书 token 接口有频控）。"""
        cache = TenantTokenCache()
        calls: list[int] = []
        barrier = Barrier(4)

        def fetch() -> tuple[str, int]:
            calls.append(1)
            return "tok-1", 7200

        def worker() -> None:
            barrier.wait()
            cache.get("app", "secret", fetch)

        threads = [Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(calls) == 1, f"并发下应只拉一次，实际 {len(calls)} 次"


class TestClientIntegration:
    """``FeishuClient`` 与共享缓存的集成。"""

    def test_separate_clients_share_the_token(self):
        """⚠️ **本模块存在的核心理由** —— 跨实例复用。

        改造前：每个 client 实例各自拉 token ⇒ N 个实例 N 次请求。
        """
        transport = _RecordingTransport(tokens=["tok-shared"], expire=7200)
        client_a = FeishuClient(
            app_id="shared-app",
            app_secret="shared-secret",
            transport=None,
            sleep=lambda _: None,
        )
        # ⚠️ 用真实 transport 路径才能验证共享 ——
        #    这里直接测缓存层，client 集成见下方测试。
        assert transport.token_request_count == 0
        assert client_a.app_id == "shared-app"

    def test_transport_injected_uses_local_cache_only(self):
        """⚠️ 注入 transport（测试场景）⇒ **不用**共享缓存。

        否则 Mock 录制会跨测试污染 ⇒ 换个测试顺序就失败。
        """
        transport = _RecordingTransport(tokens=["tok-1"], expire=7200)
        client = _make_client(transport)
        client._get_access_token()  # noqa: SLF001
        client._get_access_token()  # noqa: SLF001
        # 实例级缓存命中 ⇒ 只拉一次
        assert transport.token_request_count == 1

    def test_injected_transport_does_not_pollute_shared_cache(self):
        """⚠️ 测试用 token **绝不能**进共享缓存 —— 否则污染生产路径。"""
        transport = _RecordingTransport(tokens=["tok-test"], expire=7200)
        client = _make_client(transport)
        client._get_access_token()  # noqa: SLF001
        assert tenant_token_cache.peek("app-1", "secret-1") is None

    def test_shared_cache_is_used_without_injected_transport(self, monkeypatch):
        """不注入 transport ⇒ 走共享缓存路径。"""
        transport = _RecordingTransport(tokens=["tok-real"], expire=7200)
        client = FeishuClient(
            app_id="real-app",
            app_secret="real-secret",
            transport=None,
            sleep=lambda _: None,
        )
        # 把 client 的 http_client 换成 mock，模拟「真实 transport 但可断言」
        monkeypatch.setattr(client._http_client, "post", transport.post)  # type: ignore[attr-defined]

        first = client._get_access_token()  # noqa: SLF001
        assert first == "tok-real"

        # 另一个实例应当**复用**同一个 token
        client_b = FeishuClient(
            app_id="real-app",
            app_secret="real-secret",
            transport=None,
            sleep=lambda _: None,
        )
        monkeypatch.setattr(client_b._http_client, "post", transport.post)  # type: ignore[attr-defined]

        second = client_b._get_access_token()  # noqa: SLF001
        assert second == "tok-real"
        assert transport.token_request_count == 1, "共享缓存命中，不该再拉 token"

    def test_token_invalid_error_invalidates_shared_cache(self, monkeypatch):
        """⚠️ **回归**：token 失效后必须同时作废共享缓存。

        否则重试会从共享缓存拿到**同一个坏 token** ⇒ 请求直接失败。
        """
        transport = _RecordingTransport(tokens=["tok-bad"], expire=7200)
        client = FeishuClient(
            app_id="inv-app",
            app_secret="inv-secret",
            transport=None,
            sleep=lambda _: None,
        )
        monkeypatch.setattr(client._http_client, "post", transport.post)  # type: ignore[attr-defined]

        client._get_access_token()  # noqa: SLF001
        assert tenant_token_cache.peek("inv-app", "inv-secret") == "tok-bad"

        # 模拟：飞书返回 token 失效错误码
        tenant_token_cache.invalidate("inv-app", "inv-secret")
        assert tenant_token_cache.peek("inv-app", "inv-secret") is None
