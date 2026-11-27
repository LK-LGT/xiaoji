"""飞书 OpenAPI 生产级客户端。

包含：
- tenant_access_token 缓存、提前刷新、失效后单次强制刷新；
- HTTP 429 / 5xx / 超时的指数退避与抖动；
- Retry-After 优先；
- 可注入 HTTP transport，支持 Mock 与录制响应；
- 敏感字段脱敏，日志不输出 app_secret / token / Authorization。
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .feishu_token_cache import tenant_token_cache

logger = logging.getLogger(__name__)

Transport = Callable[[httpx.Request], httpx.Response]


# 飞书开放平台常见错误码 -> 可读业务语义。
# 用于把同步失败记录从「看不懂的错误码」变成「可执行的排查指引」。
FEISHU_ERROR_CODES: dict[int, str] = {
    99991663: "Access Token 无效",
    99991664: "Access Token 已过期",
    99991668: "应用未获得通讯录权限",
    99991669: "应用未获得用户基本信息权限",
    99991672: "缺少通讯录权限或应用版本未发布",
    99991679: "应用可见范围未包含目标部门",
    99991688: "租户 Access Token 不匹配",
    1254290: "接口触发限流，请稍后重试",
    99991400: "飞书服务繁忙，请稍后重试",
}

# 可重试的错误码（限流与服务端繁忙）。
RETRYABLE_ERROR_CODES = frozenset({99991400, 1254290})

# Token 失效类错误码：需要强制刷新 Token 后重放一次。
TOKEN_INVALID_ERROR_CODES = frozenset({99991663, 99991668, 99991669})


def describe_feishu_error(code: int | None, fallback: str = "飞书接口错误") -> str:
    """把飞书错误码转换为可读的业务语义。"""
    if code is None:
        return fallback
    return FEISHU_ERROR_CODES.get(code, f"{fallback}（错误码 {code}）")


#: 文件扩展名 → MIME 类型（上传飞书附件时用）。
#:
#: ⚠️ **不能只靠扩展名推断** —— 飞书按 MIME 决定客户端能否预览，
#:    给错了文件能上传成功但**打不开**。
_CONTENT_TYPES: dict[str, str] = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _guess_content_type(filename: str) -> str:
    """按扩展名推断 MIME 类型（未知扩展名退回二进制流）。"""
    _, ext = os.path.splitext(filename or "")
    return _CONTENT_TYPES.get(ext.lower(), "application/octet-stream")


class FeishuError(Exception):
    """飞书接口业务错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class FeishuTokenError(FeishuError):
    """Access Token 无效或获取失败。"""


class FeishuRateLimitError(FeishuError):
    """接口限流。"""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, retryable=True, status_code=429)
        self.retry_after = retry_after


class MockTransport(httpx.BaseTransport):
    """按请求路径/方法依次返回录制响应。

    录制数据格式：{"GET /contact/v3/users": [response, ...]}。
    每条 response 可以是 httpx.Response 或包含 status_code/json/text/headers 的字典。
    """

    def __init__(self, recordings: dict[str, list[Any]]) -> None:
        self.recordings = recordings
        self.calls: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        key = f"{request.method} {path}"
        normalized_path = path.removeprefix("/open-apis")
        candidates = (
            self.recordings.get(key)
            or self.recordings.get(f"{request.method} {normalized_path}")
            or self.recordings.get(normalized_path)
            or self.recordings.get(path)
            or []
        )
        if not candidates:
            return httpx.Response(
                404,
                json={"code": 404, "msg": f"mock response not found: {key}"},
                request=request,
            )
        item = candidates.pop(0) if len(candidates) > 1 else candidates[0]
        if isinstance(item, httpx.Response):
            item.request = request
            return item
        status_code = int(item.get("status_code", 200))
        headers = item.get("headers", {})
        if "json" in item:
            return httpx.Response(status_code, json=item["json"], headers=headers, request=request)
        return httpx.Response(
            status_code,
            text=item.get("text", ""),
            headers=headers,
            request=request,
        )


class FeishuClient:
    """飞书 OpenAPI 客户端。"""

    BASE_URL = "https://open.feishu.cn/open-apis"
    TOKEN_ENDPOINT = "/auth/v3/tenant_access_token/internal"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        tenant_id: str | None = None,
        *,
        transport: Transport | None = None,
        max_retries: int = 4,
        base_delay: float = 0.5,
        max_delay: float = 20.0,
        timeout: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.tenant_id = tenant_id
        self.max_retries = max(0, max_retries)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep = sleep
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        # ⚠️ **是否注入了 transport** —— 决定用不用进程级共享 token 缓存。
        #
        # 注入 transport = 测试用的 MockTransport（按录制顺序返回响应）。
        # 若共享缓存生效，token 会跨实例复用 ⇒ 测试之间互相污染，
        # 而且**换测试顺序就会失败**。
        # ⇒ 注入 transport ⇒ 只用实例级缓存（保持测试可预测）。
        self._transport_injected = transport is not None
        # ─────────────────────────────────────────────────────────────
        # ⚠️ 用户态 / 应用态 Token —— **独立槽位**，绝不与租户态共用
        #
        # 原因（RK3）：``_access_token`` 是**单槽位**。
        # 若把 user_access_token 写进同一个字段，那么
        # 「谁后调用谁中毒」—— 组织架构同步、审批回流会**随机 401**，
        # 而且是**间歇性故障**，极难排查。
        #
        # 三类 token 的生命周期与用途完全不同：
        #   - 租户态 tenant_access_token：应用身份（同步、审批回流）
        #   - 用户态 user_access_token：某个用户授权后的身份（免登、问答）
        #   - 应用态 app_access_token：应用身份（换 user_access_token）
        # ─────────────────────────────────────────────────────────────
        self._user_token: str | None = None
        self._user_token_expires_at: datetime | None = None
        self._app_token: str | None = None
        self._app_token_expires_at: datetime | None = None
        self._http_client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=timeout,
            transport=transport,
        )

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._access_token
            and self._token_expires_at
            and now < self._token_expires_at
        ):
            return self._access_token

        # ⚠️⚠️ **共享缓存只在「真实 transport」下启用**（FS-7）
        #
        # 注入 transport 的是**测试用的 MockTransport** ——
        # 它按录制顺序返回响应。若共享缓存生效，token 会跨实例复用
        # ⇒ A 测试拿到的 token 被 B 测试"继承" ⇒ 断言互相污染，
        #    而且**换个测试顺序就会失败**（典型的顺序依赖）。
        #
        # ⇒ 注入 transport ⇒ 只用实例级缓存（保持测试可预测）。
        if self._transport_injected:
            return self._fetch_tenant_token_local()

        # ⚠️ **进程级共享缓存** —— 跨 client 实例复用，
        #    避免每个请求都打一次 token 接口（飞书对 token 接口有频控）。
        #
        #    `force_refresh=True`（收到 token 失效错误码后的重试）
        #    ⇒ 先作废共享槽位再取，避免拿到同一个坏 token。
        if force_refresh:
            tenant_token_cache.invalidate(self.app_id, self.app_secret)

        return tenant_token_cache.get(
            self.app_id,
            self.app_secret,
            self._fetch_tenant_token,
        )

    def _fetch_tenant_token(self) -> tuple[str, int]:
        """向飞书拉取租户 token，返回 ``(token, expire_seconds)``。

        ⚠️ **不写任何缓存** —— 缓存由 ``TenantTokenCache`` 负责。
        """
        try:
            resp = self._http_client.post(
                self.TOKEN_ENDPOINT,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            if resp.status_code == 429:
                raise FeishuRateLimitError(
                    "飞书 Token 接口触发限流", self._retry_after(resp)
                )
            if resp.status_code >= 500:
                raise FeishuError(
                    "飞书 Token 服务暂时不可用",
                    retryable=True,
                    status_code=resp.status_code,
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise FeishuError("飞书 Token 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            # ⚠️⚠️ **网络错误必须标 retryable=True**（2026-11-17 修）
            #
            #   实测踩到：一次 DNS 抖动（``getaddrinfo failed``）就让
            #   整条链路 500 —— 而同样的网络错误在 ``_request_with_token``
            #   里是**会重试**的（retryable=True + 退避）。
            #
            #   ⚠️ 这里原本抛的是 ``FeishuTokenError`` —— 它的默认
            #      ``retryable=False`` ⇒ **重试逻辑整个被跳过**。
            #
            #   ⇒ DNS 抖动 / 连接被重置 / 临时断网 —— 这些都是**瞬时**故障，
            #      重试一下就好了，不该直接失败。
            #
            #   ⇒ 区分：``code != 0``（业务失败，如 app_secret 错）→ 不可重试；
            #      **HTTP 层面的错误**（网络/超时）→ 可重试。
            raise FeishuError("飞书 Token 请求失败", retryable=True) from exc

        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise FeishuTokenError("飞书 Token 获取失败")

        expires_in = max(60, int(data.get("expire", 7200)))
        return str(data["tenant_access_token"]), expires_in

    def _fetch_tenant_token_local(self) -> str:
        """实例级拉取 token（**仅注入 transport 时使用**）。

        ⚠️ 保留原有的 TTL 计算语义（见下方注释）——
        共享缓存那条路径由 ``TenantTokenCache`` 用同样的余量计算。
        """
        token, expires_in = self._fetch_tenant_token()

        self._access_token = token
        # ⚠️ 刷新余量 300 秒 —— 避免 token 在请求途中过期。
        #
        # 原来的写法 ``max(30, expires_in - 300)`` 有个**隐蔽的错误**：
        # 当 ``expires_in <= 330`` 时，``expires_in - 300`` 是负数，
        # 被 ``max(30, ...)`` 兜成 30 秒 —— 也就是**声称**这个 token
        # 还有 30 秒有效期，哪怕它 5 秒后就过期。
        #
        # 后果：token 明明快过期了，客户端却认为它有效 → 继续用 →
        # 撞上 401 才被迫强制刷新 → 整条请求链路多一次往返。
        #
        # 正确做法：**取真实剩余时间与余量的较小值**：
        #   实际有效期 = min(expires_in, max(30, expires_in - 300))
        # 即：正常 token 提前 300s 过期；极短命的 token 也不会被
        # 「延长」到超过它真实的寿命。
        #
        # 极端情况（expire <= 30）：token 根本撑不到一次请求，
        # 此时本地有效期直接设为 0 → 每次请求都重新拉 token，
        # 宁可多一次往返，也不能用一个必然过期的 token。
        if expires_in <= 30:
            effective_ttl = 0.0
        else:
            effective_ttl = float(min(expires_in, max(30, expires_in - 300)))
        self._token_expires_at = datetime.now(UTC) + timedelta(
            seconds=effective_ttl
        )
        logger.info(
            "飞书 Access Token 已刷新，有效期秒数=%s 本地有效期秒数=%s",
            expires_in,
            effective_ttl,
        )
        return self._access_token

    # ─────────────────────────────────────────────────────────────────
    # 应用态 / 用户态 Token（FS-1 授权底座）
    #
    # ⚠️ 与租户态**完全独立** —— 见 __init__ 里关于 RK3 的说明。
    # ─────────────────────────────────────────────────────────────────

    def get_app_access_token(self, *, force_refresh: bool = False) -> str:
        """获取应用身份凭证（app_access_token）。

        用途：用 OAuth ``code`` 换 ``user_access_token`` 时，
        需要以**应用身份**调用换取接口。

        与租户态的区别：租户态代表「企业」，应用态代表「应用自身」。
        两者不能互换 —— 用错会出现 99991688「租户 Access Token 不匹配」。
        """
        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._app_token
            and self._app_token_expires_at
            and now < self._app_token_expires_at
        ):
            return self._app_token

        try:
            resp = self._http_client.post(
                "/auth/v3/app_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
        except httpx.TimeoutException as exc:
            raise FeishuError("飞书应用 Token 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            # ⚠️ 网络错误**可重试** —— 与 `_get_access_token` 同理（2026-11-17 修）。
            #   DNS 抖动 / 临时断网不该直接失败。
            raise FeishuError("飞书应用 Token 请求失败", retryable=True) from exc

        if resp.status_code == 429:
            raise FeishuRateLimitError("飞书应用 Token 接口触发限流", self._retry_after(resp))
        if resp.status_code >= 500:
            raise FeishuError(
                "飞书应用 Token 服务暂时不可用",
                retryable=True,
                status_code=resp.status_code,
            )
        try:
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FeishuTokenError("飞书应用 Token 获取失败") from exc

        if data.get("code") != 0 or not data.get("app_access_token"):
            raise FeishuTokenError(
                describe_feishu_error(data.get("code"), "飞书应用 Token 获取失败")
            )

        expires_in = max(60, int(data.get("expire", 7200)))
        self._app_token = str(data["app_access_token"])
        self._app_token_expires_at = datetime.now(UTC) + timedelta(
            seconds=float(min(expires_in, max(30, expires_in - 300)))
        )
        logger.info("飞书应用 Access Token 已刷新，有效期秒数=%s", expires_in)
        return self._app_token

    def exchange_user_token(
        self,
        code: str,
        *,
        grant_type: str = "authorization_code",
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """用 OAuth 授权码换 ``user_access_token``。

        流程（FS-1）：
            client_id + client_secret（应用身份）
              + code（用户授权回调带回）
              → user_access_token（用户身份）

        返回值含 ``access_token`` / ``refresh_token`` / ``expires_in`` /
        ``refresh_token_expires_in`` / ``scope`` / ``token_type``。

        ⚠️ 返回的 token 由调用方负责处置 —— **绝不能下发到前端**。
        本系统只用它换取用户身份信息，随后立即丢弃。

        ⚠️⚠️ **端点与参数名必须按官方文档**（`获取 user_access_token`）：

            POST https://open.feishu.cn/open-apis/authen/v2/oauth/token
            {
              "grant_type": "authorization_code",
              "client_id":     <App ID>,
              "client_secret": <App Secret>,
              "code":          <授权码>,
              "redirect_uri":  <发起授权时用的同一个回调地址>   ← 可选但强烈建议
            }

        ⚠️ **曾经用错成** ``POST /authen/v1/oidc/access_token`` + ``app_access_token``。

        那个端点是「OIDC 用户信息」风格，**不是** OAuth 授权码流程的令牌端点：
        它不接受 ``client_id``/``client_secret``，也没有 OAuth 规定的
        ``error`` / ``error_description`` 字段 —— 用它换 token 不可靠。

        ⚠️ ``redirect_uri`` 必须与**发起授权时用的完全一致**（飞书会校验，
        不一致报 20071）。省略虽可能也能过，但显式传参能让问题早暴露。
        """
        if not code:
            raise FeishuTokenError("OAuth 授权码为空")

        payload: dict[str, Any] = {
            "grant_type": grant_type,
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "code": code,
        }
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri

        try:
            resp = self._http_client.post("/authen/v2/oauth/token", json=payload)
        except httpx.TimeoutException as exc:
            raise FeishuError("飞书用户 Token 换取超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            # ⚠️ 网络错误**可重试** —— 与 `_get_access_token` 同理（2026-11-17 修）。
            #   DNS 抖动 / 临时断网不该直接失败。
            raise FeishuError("飞书用户 Token 换取失败", retryable=True) from exc

        if resp.status_code == 429:
            raise FeishuRateLimitError("飞书用户 Token 接口触发限流", self._retry_after(resp))
        if resp.status_code >= 500:
            raise FeishuError(
                "飞书用户 Token 服务暂时不可用",
                retryable=True,
                status_code=resp.status_code,
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            # ⚠️ **OAuth 错误是用 400 + JSON body 表达的** ——
            #    `error` / `error_description` 里才是真正的失败原因。
            #    直接 raise_for_status 会把这段有用信息丢掉，
            #    用户只能看到一句「飞书用户 Token 换取失败」。
            detail = self._extract_oauth_error(resp)
            raise FeishuTokenError(detail) from None

        try:
            data = resp.json()
        except ValueError as exc:
            raise FeishuTokenError("飞书用户 Token 换取失败") from exc

        if data.get("code") != 0 or not data.get("access_token"):
            # ⚠️ OAuth 端点用 `error` / `error_description` 表达失败原因，
            #    不是 `msg`。两个都读，兼容不同版本的飞书响应。
            detail = (
                data.get("error_description")
                or data.get("msg")
                or describe_feishu_error(data.get("code"), "飞书用户 Token 换取失败")
            )
            raise FeishuTokenError(detail)
        return data

    @staticmethod
    def _extract_oauth_error(resp: httpx.Response) -> str:
        """从 OAuth 错误响应里取出可读的原因。

        ⚠️ 飞书 OAuth 端点有两种错误形态：
        1. ``{"code": 20003, "error": "invalid_grant", "error_description": "..."}``
        2. ``{"code": 20003, "msg": "..."}``
        """
        try:
            data = resp.json()
        except ValueError:
            return f"飞书用户 Token 换取失败（HTTP {resp.status_code}）"
        if not isinstance(data, dict):
            return f"飞书用户 Token 换取失败（HTTP {resp.status_code}）"
        detail = (
            data.get("error_description")
            or data.get("msg")
            or describe_feishu_error(data.get("code"), "")
        )
        return detail or f"飞书用户 Token 换取失败（HTTP {resp.status_code}）"

    def get_user_info(self, user_access_token: str, user_id: str) -> dict[str, Any]:
        """用 ``user_access_token`` 查询**通讯录里指定用户**的信息。

        ⚠️⚠️ **必须带 ``user_id_type=user_id``**（2026-11-17 实测发现，F1）。

        漏掉这个参数时，飞书会按 **open_id** 解析路径里的 ID，
        于是传 user_id 会返回：

            99992351  The request you send is not a valid {open_id} or not exists

        这个错误信息**完全不指向「少传参数」**，极易排查半天。
        已固化在 ``check_feishu_scopes.py`` 的探测项里做回归保护。

        返回值含 ``open_id`` / ``union_id`` / ``user_id`` / ``name`` 等。

        ⚠️ **这个接口不适合「免登」场景** —— 它要求先知道 ``user_id``，
        而免登时我们只有 token（见 ``get_login_user_info``）。
        """
        if not user_access_token:
            raise FeishuTokenError("缺少 user_access_token")
        if not user_id:
            raise FeishuTokenError("缺少 user_id")

        return self._request_with_token(
            "GET",
            f"/contact/v3/users/{user_id}",
            params={"user_id_type": "user_id"},
            token=user_access_token,
        )

    def get_login_user_info(self, user_access_token: str) -> dict[str, Any]:
        """用 ``user_access_token`` 查询**当前登录用户**的信息。

        ⚠️⚠️ **免登必须用这个接口**（2026-11-17 实测发现，F2）。

        官方「获取用户信息」接口：

            GET https://open.feishu.cn/open-apis/authen/v1/user_info
            Authorization: Bearer <user_access_token>

        **不需要传 user_id** —— 它直接返回**当前登录用户**的身份，
        这正是免登场景的语义（我们手上的 token 就代表「这个人」）。

        返回含 ``open_id`` / ``union_id`` / ``name`` / ``tenant_key``；
        ⚠️ ``user_id`` **可能不返回** —— 它需要额外的
        「获取用户 user ID」权限（仅自建应用）。所以**不能依赖它**。

        ⚠️ **为什么不能用** ``get_user_info()``（通讯录接口）：
        那个接口路径里必须有 ``user_id``，而 OAuth 令牌响应里
        **不含** ``user_id``（官方文档的响应字段只有
        ``access_token`` / ``refresh_token`` / ``expires_in`` / ``scope`` …）
        ⇒ 之前用它必然抛「缺少 user_id」，**免登永远走不到最后一步**。
        """
        if not user_access_token:
            raise FeishuTokenError("缺少 user_access_token")

        return self._request_with_token(
            "GET",
            "/authen/v1/user_info",
            token=user_access_token,
        )

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        delay = min(self.base_delay * (2**attempt), self.max_delay)
        return delay + random.uniform(0, min(0.5, delay / 2))

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送认证请求；网络错误、429、5xx 自动退避重试。

        使用**租户态** token（应用身份）。用户态请求请用
        ``_request_with_token`` —— 两者绝不能混用（RK3）。

        ⚠️ ``files`` 与 ``json`` **互斥** ——
        httpx 要求 multipart 与 json body 不能同时给。
        """
        return self._request_with_token(
            method,
            endpoint,
            params=params,
            json=json,
            files=files,
            token=None,
        )

    def _request_with_token(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """发送认证请求，可显式指定 token。

        ``token is None`` → 使用租户态 token（自动刷新 + 失效后重放）。
        ``token`` 非空   → 使用**调用方传入**的 token（如 user_access_token）。

        ⚠️ 显式 token 失效时**不做**自动刷新重放 —— 用户态 token 的刷新
        方式（refresh_token）与租户态完全不同，盲目重放只会把同一个失败
        放大成重试风暴。调用方应自行决定如何重新换取 token。

        ⚠️ ``files`` 用于 multipart 上传（如 ``/im/v1/files``）。
        ⚠️ **files 与 json 互斥** —— 同时给会让 httpx 抛异常。
        """
        token_refreshed = False
        for attempt in range(self.max_retries + 1):
            try:
                auth_token = token if token is not None else self._get_access_token()
            except FeishuRateLimitError as exc:
                if attempt >= self.max_retries:
                    raise
                self.sleep(self._backoff(attempt, exc.retry_after))
                continue
            except FeishuError as exc:
                if exc.retryable and attempt < self.max_retries:
                    self.sleep(self._backoff(attempt))
                    continue
                raise
            try:
                resp = self._http_client.request(
                    method,
                    endpoint,
                    params=params,
                    json=json,
                    files=files,
                    headers={"Authorization": f"Bearer {auth_token}"},
                )
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries:
                    raise FeishuError("飞书接口请求超时", retryable=True) from exc
                self.sleep(self._backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise FeishuError("飞书接口网络错误", retryable=True) from exc
                self.sleep(self._backoff(attempt))
                continue

            if resp.status_code == 429:
                if attempt >= self.max_retries:
                    raise FeishuRateLimitError("飞书接口触发限流", self._retry_after(resp))
                self.sleep(self._backoff(attempt, self._retry_after(resp)))
                continue

            if resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise FeishuError(
                        "飞书接口服务暂时不可用",
                        retryable=True,
                        status_code=resp.status_code,
                    )
                self.sleep(self._backoff(attempt, self._retry_after(resp)))
                continue

            if (
                resp.status_code in (401, 403)
                and not token_refreshed
                and token is None
            ):
                # Token 失效：强制刷新一次后重放原请求。
                # ⚠️ 仅对**租户态** token 生效 —— 显式 token 无从刷新。
                token_refreshed = True
                self._access_token = None
                self._token_expires_at = None
                continue

            try:
                data = resp.json()
            except ValueError as exc:
                raise FeishuError("飞书接口返回了非法 JSON") from exc

            if resp.status_code >= 400:
                raise FeishuError(
                    describe_feishu_error(
                        data.get("code"), str(data.get("msg") or "飞书接口请求失败")
                    ),
                    code=data.get("code"),
                    retryable=False,
                    status_code=resp.status_code,
                )
            if data.get("code") != 0:
                code = data.get("code")
                message = describe_feishu_error(
                    code, str(data.get("msg") or "飞书接口返回错误")
                )
                token_invalid = code in TOKEN_INVALID_ERROR_CODES
                if token_invalid and not token_refreshed and token is None:
                    token_refreshed = True
                    self._access_token = None
                    self._token_expires_at = None
                    # ⚠️⚠️ **必须同时作废进程级共享缓存**（FS-7）
                    #
                    # 共享缓存存在后，这里只清实例槽位**是不够的** ——
                    # 下一次重试会从共享缓存里拿到**同一个已失效的 token**
                    # ⇒ 重试再次 99991663 ⇒ 而 `token_refreshed` 已为 True
                    # ⇒ 直接抛错 ⇒ **一次 token 失效导致整个请求失败**。
                    #
                    # ⇒ 共享槽位与实例槽位**必须一起作废**。
                    if not self._transport_injected:
                        tenant_token_cache.invalidate(self.app_id, self.app_secret)
                    continue
                raise FeishuError(
                    message,
                    code=code,
                    retryable=code in RETRYABLE_ERROR_CODES,
                )
            return data.get("data") or {}

        raise FeishuError("飞书接口请求失败", retryable=True)

    def fetch_departments(
        self,
        page_size: int = 50,
        *,
        start_page_token: str | None = None,
        on_page_token: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        """拉取部门树。

        ⚠️ 飞书通讯录 API **没有**「按更新时间拉取变更」的接口
        （`GET /contact/v3/departments` 只支持 `page_token` 分页）。
        所以这里的「游标」是**分页游标**：`page_token`。

        Args:
            start_page_token: 续传起点。上次同步中断时残留的
                `page_token` —— 从这里接着拉，不从头开始。
            on_page_token: 每翻一页回调一次（把当前 `page_token`
                持久化到游标表）。**中断时能恢复到最后一页**，
                不必重拉已成功的页。
        """
        page_size = min(page_size, 50)
        departments: list[dict[str, Any]] = []
        page_token: str | None = start_page_token or None
        while True:
            params: dict[str, Any] = {
                "page_size": page_size,
                "department_id_type": "department_id",
                "fetch_child": "true",
            }
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET", "/contact/v3/departments/0/children", params=params
            )
            departments.extend(data.get("items", []))
            page_token = data.get("page_token")
            if on_page_token is not None:
                # ⚠️ 即使 page_token 为空也要回调 —— 那表示「已拉完」，
                #    调用方据此把游标清空（避免下次拿一个过期的 token
                #    去续传导致 400）。
                on_page_token(page_token or "")
            if not page_token:
                break
        logger.info("飞书部门拉取完成，数量=%s", departments)
        return departments

    def fetch_employees(
        self,
        page_size: int = 50,
        departments: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """按部门拉取员工并按 user_id 去重。"""
        page_size = min(page_size, 50)
        if departments is None:
            departments = self.fetch_departments()

        employees: dict[str, dict[str, Any]] = {}
        for dept in departments:
            dept_id = dept.get("department_id")
            if not dept_id:
                continue
            try:
                dept_employees = self.fetch_employees_in_department(str(dept_id), page_size)
            except FeishuError as exc:
                # 单个部门拉取失败不应阻断其他部门。
                logger.warning("飞书部门员工拉取失败，department_id=%s，原因=%s", dept_id, exc.message)
                continue
            for emp in dept_employees:
                key = str(emp.get("user_id") or emp.get("open_id") or "")
                if not key:
                    continue
                if key in employees:
                    existing = employees[key].get("department_ids") or []
                    for dep_id in emp.get("department_ids") or []:
                        if dep_id not in existing:
                            existing.append(dep_id)
                    employees[key]["department_ids"] = existing
                else:
                    employees[key] = emp
        logger.info("飞书员工拉取完成，唯一员工数=%s", len(employees))
        return list(employees.values())

    def fetch_employees_in_department(
        self,
        department_id: str,
        page_size: int = 50,
        *,
        start_page_token: str | None = None,
        on_page_token: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        """拉取单个部门的直属员工。

        Args:
            start_page_token: 续传起点 `page_token`。
            on_page_token: 每页回调，用于持久化进度。
        """
        page_size = min(page_size, 50)
        users: list[dict[str, Any]] = []
        page_token: str | None = start_page_token or None
        while True:
            params: dict[str, Any] = {
                "page_size": page_size,
                "department_id": department_id,
                "department_id_type": "department_id",
                "user_id_type": "user_id",
            }
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", "/contact/v3/users", params=params)
            users.extend(data.get("items", []))
            page_token = data.get("page_token")
            if on_page_token is not None:
                on_page_token(page_token or "")
            if not page_token:
                break
        return users

    def fetch_employee(self, user_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/contact/v3/users/{user_id}")
        return data.get("user", {})

    def fetch_department(self, department_id: str) -> dict[str, Any]:
        """拉取单个部门的最新信息（R18 事件驱动增量用）。

        ⚠️ 飞书通讯录 API 没有「按更新时间拉变更」的端点，
        事件载荷也只有部分字段（缺 ``parent_department_id`` 等）。
        所以收到部门变更事件后，必须**回查一次**拿完整数据 ——
        否则落库会把父部门关系清掉。

        ⚠️ 404 不代表「部门不存在」也可能是「应用没这个部门的权限」，
        调用方需按错误码区分处理。
        """
        data = self._request("GET", f"/contact/v3/departments/{department_id}")
        return data.get("department", {})

    def fetch_department_children(
        self, department_id: str, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """拉取单个部门的**直属子部门**。

        ⚠️ 用于「部门删除/移动」事件 —— 需要知道新的父部门是谁，
        以及该部门下还有哪些子部门（孤儿清理）。
        """
        page_size = min(page_size, 50)
        children: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "page_size": page_size,
                "department_id": department_id,
                "department_id_type": "department_id",
            }
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET", "/contact/v3/departments/:department_id/children", params=params
            )
            children.extend(data.get("items", []))
            page_token = data.get("page_token")
            if not page_token:
                break
        return children

    def fetch_approval_instance(self, instance_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/approval/v4/instances/{instance_id}")
        return data.get("instance", {})

    def fetch_approval_instances(
        self,
        *,
        code: str | None = None,
        user_id: str | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """拉取审批实例列表（`GET /approval/v4/instances`）。

        用于审批回流场景：按审批定义 code 过滤出本系统对应的实例，
        再逐个用 `fetch_approval_instance` 取详情。

        ⚠️ 该接口**必填** `code`（审批定义 code）与 `user_id`（发起人），
        缺失会返回 `99992402 field validation failed`。这里默认用占位值
        保证请求能通过校验，真正有意义的过滤由调用方在拿到结果后完成。

        Returns:
            `(实例列表, 下一页 page_token)`。`page_token` 为 None 表示已到末页。
        """
        params: dict[str, Any] = {
            "page_size": min(page_size, 100),
            "code": code or "",
            "user_id": user_id or "",
        }
        if page_token:
            params["page_token"] = page_token
        data = self._request("GET", "/approval/v4/instances", params=params)
        items = data.get("items") or []
        return list(items), data.get("page_token") or None

    def fetch_approval_instance_tasks(self, instance_id: str) -> list[dict[str, Any]]:
        """查审批实例的节点与审批人（`GET /approval/v4/instances/{id}/tasks`）。

        用于判断「当前这一轮该谁审」。返回项包含 `node_id` / `approver_id` /
        `status` 等字段。
        """
        data = self._request("GET", f"/approval/v4/instances/{instance_id}/tasks")
        return list(data.get("items") or [])

    # ─────────────────────────────────────────────────────────────
    # IM 域
    # ─────────────────────────────────────────────────────────────

    def send_text_message(
        self,
        receive_id: str,
        text: str,
        *,
        receive_id_type: str = "open_id",
        msg_type: str = "text",
    ) -> dict[str, Any]:
        """发飞书消息（`POST /im/v1/messages`）。

        Args:
            receive_id: 接收者 ID，取决于 `receive_id_type`
                （`open_id` / `user_id` / `chat_id`）。
            text: 消息正文（`msg_type="text"` 时为纯文本）。
            receive_id_type: 接收者 ID 类型。
            msg_type: 消息类型，目前实现仅支持 `text`。

        需要的权限：`im:message:send_as_bot`。
        """
        content = json.dumps({"text": text}, ensure_ascii=False)
        data = self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content,
            },
        )
        return data

    def send_card_message(
        self,
        receive_id: str,
        card: dict[str, Any],
        *,
        receive_id_type: str = "open_id",
        msg_type: str = "interactive",
    ) -> dict[str, Any]:
        """发飞书卡片消息（`POST /im/v1/messages`）。

        卡片消息的 ``content`` 是**卡片 JSON 的字符串形式** ——
        不是嵌套对象。飞书文档里写的是 ``"content": "{\\"schema\\":\\"2.0\\",...}"``，
        这里用 ``json.dumps`` 序列化。

        Args:
            receive_id: 接收者 ID（取决于 ``receive_id_type``）。
            card: 卡片结构（建议用 ``services.feishu_card`` 的构造器产出）。
            receive_id_type: ``open_id`` / ``user_id`` / ``chat_id``。
            msg_type: 卡片消息固定为 ``interactive``。

        需要的权限：`im:message:send_as_bot` + 机器人能力开启。
        """
        content = json.dumps(card, ensure_ascii=False)
        data = self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content,
            },
        )
        return data

    # ─────────────────────────────────────────────────────────────
    # 附件（FS-6）
    # ─────────────────────────────────────────────────────────────

    def upload_file(
        self,
        filename: str,
        content: bytes,
        *,
        file_type: str = "xlsx",
    ) -> str:
        """上传文件到飞书，返回 ``file_key``（`POST /im/v1/files`）。

        ⚠️ **飞书发附件是两步的**
        ------------------------------
        1. 先把文件**上传**（``/im/v1/files``）→ 拿到 ``file_key``；
        2. 再发消息时把 ``file_key`` 放进消息内容。

        ⇒ 直接把二进制塞进消息是**不行的**。

        ⚠️ **两步用的是不同的 token**
        ------------------------------
        上传文件用的是 **tenant_access_token**（应用身份），
        而「发给某个用户」时消息体里的 ``file_key`` 还需带
        接收方信息 —— 这里只上传，发送由 ``send_file_message`` 负责。

        ⚠️ **file_type 必须是飞书认识的类型**
        ------------------------------
        飞书按类型决定能否在客户端预览。常用取值：

        ==============  ==========================
        扩展名           file_type
        ==============  ==========================
        ``.xlsx``       ``xlsx``
        ``.pdf``        ``pdf``
        ``.docx``       ``doc``
        ``.txt``        ``txt``
        ==============  ==========================

        传错的话文件能上传成功，但客户端**打不开**。

        需要的权限：`im:resource`（或对应的新版权限点）。
        """
        if not filename or not content:
            raise FeishuError("上传飞书附件失败：文件名或内容为空")

        files = {"file": (filename, content, _guess_content_type(filename))}
        data = self._request(
            "POST",
            "/im/v1/files",
            params={"file_type": file_type, "file_name": filename},
            files=files,
        )
        file_key = data.get("file_key")
        if not file_key:
            raise FeishuError("上传飞书附件失败：未返回 file_key")
        logger.info("飞书附件上传成功：file=%s type=%s", filename, file_type)
        return str(file_key)

    def send_file_message(
        self,
        receive_id: str,
        file_key: str,
        *,
        receive_id_type: str = "open_id",
        title: str = "",
    ) -> dict[str, Any]:
        """发**文件消息**（`POST /im/v1/messages`，``msg_type="file"``）。

        ⚠️ **为什么单独一个方法**
        卡片消息（``interactive``）**不能**带附件 ——
        飞书只允许 ``file`` / ``image`` / ``audio`` / ``media`` / ``sticker``
        这几种消息类型携带 ``file_key``。

        ⇒ 需要附件时**必须**额外发一条 file 消息，
        不能把 file_key 塞进卡片。

        Args:
            receive_id: 接收者 ID。
            file_key: 上传文件拿到的 ``file_key``。
            receive_id_type: ``open_id`` / ``user_id`` / ``chat_id``。
            title: 文件标题（可空）。

        需要的权限：`im:message:send_as_bot`。
        """
        content = json.dumps({"file_key": file_key}, ensure_ascii=False)
        data = self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json={
                "receive_id": receive_id,
                "msg_type": "file",
                "content": content,
            },
        )
        if title:
            logger.debug("已发送飞书文件消息：title=%s", title)
        return data

    # ─────────────────────────────────────────────────────────────
    # 考勤域
    # ─────────────────────────────────────────────────────────────

    # 飞书「查询打卡结果」接口：单次最多 50 人、单次日期跨度最多 30 天。
    ATTENDANCE_MAX_USERS_PER_REQUEST = 50
    ATTENDANCE_MAX_DATE_RANGE_DAYS = 30

    def fetch_attendance_tasks(
        self,
        user_ids: list[str],
        start_date: str,
        end_date: str,
        *,
        employee_type: str = "employee_id",
        need_overtime_result: bool = True,
        ignore_invalid_users: bool = True,
    ) -> list[dict[str, Any]]:
        """查询打卡结果（POST /attendance/v1/user_tasks/query）。

        Args:
            user_ids: 员工 ID 列表（对应 employee_type）。本方法内部按 50 人分批。
            start_date: 起始日期，格式 yyyyMMdd。
            end_date: 结束日期，格式 yyyyMMdd。与起始日跨度不得超过 30 天。
            employee_type: 员工 ID 类型，默认为 employee_id（与本地 Employee.id 对齐）。
            need_overtime_result: 是否返回加班班段。
            ignore_invalid_users: 忽略无效/无权限用户，避免整批失败。

        Returns:
            user_task_results 列表，每项为「员工 × 日期」的考勤任务。
        """
        if not user_ids:
            return []
        if len(start_date) != 8 or len(end_date) != 8:
            raise ValueError("考勤查询日期必须为 yyyyMMdd 格式")

        # 飞书限制单次日期跨度 30 天，这里主动校验，避免拿到一个含义不明的错误码。
        span_days = (
            datetime.strptime(end_date, "%Y%m%d")
            - datetime.strptime(start_date, "%Y%m%d")
        ).days
        if span_days < 0:
            raise ValueError("考勤查询的结束日期不能早于起始日期")
        if span_days > self.ATTENDANCE_MAX_DATE_RANGE_DAYS - 1:
            raise ValueError(
                f"考勤查询日期跨度不得超过 {self.ATTENDANCE_MAX_DATE_RANGE_DAYS} 天，"
                f"当前为 {span_days + 1} 天"
            )

        chunk_size = self.ATTENDANCE_MAX_USERS_PER_REQUEST
        results: list[dict[str, Any]] = []
        for i in range(0, len(user_ids), chunk_size):
            chunk = user_ids[i : i + chunk_size]
            data = self._request(
                "POST",
                "/attendance/v1/user_tasks/query",
                params={
                    "employee_type": employee_type,
                    "ignore_invalid_users": (
                        "true" if ignore_invalid_users else "false"
                    ),
                },
                json={
                    "user_ids": chunk,
                    "check_date_from": int(start_date),
                    "check_date_to": int(end_date),
                    "need_overtime_result": need_overtime_result,
                },
            )
            results.extend(data.get("user_task_results") or [])
        logger.info(
            "飞书打卡结果拉取完成，用户数=%s，返回任务数=%s", len(user_ids), len(results)
        )
        return results

    def fetch_user_daily_shifts(
        self,
        user_ids: list[str],
        start_date: str,
        end_date: str,
        *,
        employee_type: str = "employee_id",
    ) -> list[dict[str, Any]]:
        """查询排班表（POST /attendance/v1/user_daily_shifts/query）。

        返回员工 × 日期的排班结果；``shift_id == "0"`` 表示休息/未排班。
        飞书限制：单次 ≤50 人、日期跨度 ≤30 天（含首尾）。
        """
        if not user_ids:
            return []
        if len(start_date) != 8 or len(end_date) != 8:
            raise ValueError("排班查询日期必须为 yyyyMMdd 格式")

        start = datetime.strptime(start_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()
        if end < start:
            raise ValueError("排班查询的结束日期不能早于起始日期")
        if (end - start).days + 1 > self.ATTENDANCE_MAX_DATE_RANGE_DAYS:
            raise ValueError(
                f"排班查询日期跨度不得超过 {self.ATTENDANCE_MAX_DATE_RANGE_DAYS} 天"
            )

        results: list[dict[str, Any]] = []
        for i in range(0, len(user_ids), self.ATTENDANCE_MAX_USERS_PER_REQUEST):
            chunk = user_ids[i : i + self.ATTENDANCE_MAX_USERS_PER_REQUEST]
            data = self._request(
                "POST",
                "/attendance/v1/user_daily_shifts/query",
                params={"employee_type": employee_type},
                json={
                    "user_ids": chunk,
                    "check_date_from": int(start_date),
                    "check_date_to": int(end_date),
                },
            )
            results.extend(data.get("user_daily_shifts") or [])
        logger.info(
            "飞书排班拉取完成，用户数=%s，返回条数=%s", len(user_ids), len(results)
        )
        return results

    def __enter__(self) -> FeishuClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self._http_client.close()
