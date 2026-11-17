"""飞书 OAuth 网页授权服务（FS-1 授权底座 / FS-2 免登）。

职责：
1. 生成飞书授权 URL（含防 CSRF 的 ``state``）；
2. 签发 / 校验 ``state``（一次性、短时效、绑定 redirect_uri）；
3. 用授权码 ``code`` 换 ``user_access_token``；
4. 用用户态 token 换取用户身份（``open_id`` / ``union_id`` / ``user_id``）。

⚠️ 三条不可破的约束：

**C1. 飞书 token 绝不下发前端。**
    ``user_access_token`` 只在本进程内存活 —— 用它换完身份信息后立即丢弃。
    前端最终只拿到**本系统签发的 HMAC token**（``routes/auth.py::_generate_token``）。

**C2. state 必须一次性。**
    校验成功后立即作废。否则一个被截获的回调 URL 可以被反复重放，
    等于把「一次授权」变成「无限次授权」。

**C3. 身份解析只认在职且启用的员工。**
    复用 ``FeishuIdentityService.resolve_employee()`` —— 它已有
    「离职 / 禁用账号不参与身份解析」的防线。不要在这里另写一套查询。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..core.config import settings
from .feishu_client import FeishuClient, FeishuError, FeishuTokenError
from .feishu_identity_service import FeishuIdentityService

logger = logging.getLogger(__name__)

# 飞书 OAuth 授权页（新版统一入口）。
OAUTH_AUTHORIZE_ENDPOINT = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"


@dataclass(frozen=True)
class OAuthState:
    """一次授权请求的上下文。

    存在内存里 —— 进程重启后失效（安全侧：宁可让用户重新授权）。
    """

    redirect_uri: str
    created_at: int
    nonce: str


@dataclass(frozen=True)
class OAuthResult:
    """授权成功后的结果。

    ⚠️ 这里**不包含** user_access_token —— 它在服务内部用完即弃。
    """

    employee_id: str
    username: str
    real_name: str
    open_id: str
    union_id: str | None
    user_id: str | None


class FeishuOAuthError(Exception):
    """OAuth 流程错误。

    ⚠️ ``reason`` 是**细粒度诊断码**（如 ``exited`` / ``disabled`` /
    ``not_synced``），与 ``code``（粗粒度分类，决定 HTTP 状态码）分工不同。

    为什么需要它：
    同一个 ``code="identity_not_bound"`` 背后有**四种**要处理方式
    完全不同的情况（已离职 / 已停用 / 未同步 / 数据冲突）。
    只回一句中文文案，前端只能显示通用提示 ——
    用户看到「未绑定」会以为要重新绑定，但**已离职的人根本不该绑定**。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "feishu_oauth_error",
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.reason = reason


# ⚠️⚠️ **state 存储必须是模块级共享的**（不是实例字段）。
#
# 原因：签发 state 的请求（GET /v1/feishu/auth/url）与消费 state 的请求
# （POST /v1/feishu/auth/exchange）是**两个不同的 HTTP 请求**，
# 而路由层每次请求都会 new 一个 ``FeishuOAuthService``。
# 若 state 存在实例字段里，第二个请求根本看不到第一个请求签发的 state
# → 表现为「state 无效或已被使用」，且**每个用户都必然失败**。
#
# 内存实现的局限：多实例部署时各进程各存一份 → 授权请求落到 A 实例、
# 回调落到 B 实例就会失败。生产多实例需换 Redis（FS-7 任务 7.3）。
_STATE_STORE: dict[str, OAuthState] = {}


class FeishuOAuthService:
    """飞书 OAuth 授权服务。"""

    def __init__(self, client: FeishuClient, db: Session) -> None:
        self.client = client
        self.db = db
        # state 存储：state 值 → OAuthState。
        # ⚠️ 用**模块级共享**的 dict —— 见上面 _STATE_STORE 的说明。
        self._states = _STATE_STORE

    # ─────────────────────────────────────────────────────────────
    # 配置读取
    # ─────────────────────────────────────────────────────────────

    @property
    def _state_secret(self) -> bytes:
        """state 签名密钥。

        优先用 ``feishu_oauth_state_secret``；留空时回退到
        ``auth_token_secret`` —— 保证「没配也能跑」，但**建议独立配置**
        （密钥隔离：登录 token 泄露不应波及 state 防重放）。
        """
        secret = settings.feishu_oauth_state_secret or settings.auth_token_secret
        return secret.encode()

    def _allowed_redirect_uris(self) -> list[str]:
        return [
            uri.strip()
            for uri in settings.feishu_oauth_redirect_uris.split(",")
            if uri.strip()
        ]

    def _allowed_scopes(self) -> list[str]:
        return [
            scope.strip()
            for scope in settings.feishu_oauth_scopes.split(",")
            if scope.strip()
        ]

    # ─────────────────────────────────────────────────────────────
    # state 签发与校验
    # ─────────────────────────────────────────────────────────────

    def _sign_state(self, nonce: str, redirect_uri: str, issued_at: int) -> str:
        """给 state 附加 HMAC 签名。

        格式：``{nonce}.{issued_at}.{redirect_b64}.{sig}`` —— 明文可读，
        便于排障，但因为有 HMAC，**无法被篡改**（改任何一个字段签名就对不上）。

        ⚠️ **字段分隔符是 '.'**，所以每个字段都**不能含 '.'**：
          - ``nonce``    → base64url（字符集 [A-Za-z0-9_-]）
          - ``issued_at``→ **整数**秒（用 float 会把小数点变成第 5 个字段！）
          - ``redirect`` → base64url
          - ``sig``     → hex

        ⚠️ **签名载荷用的是 base64 后的 redirect**（不是明文）——
        签发与校验必须用**同一份字符串**，否则两边 HMAC 永远对不上。
        """
        encoded = base64.urlsafe_b64encode(redirect_uri.encode()).decode().rstrip("=")
        payload = f"{nonce}.{issued_at}.{encoded}"
        sig = hmac.new(
            self._state_secret, payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"{nonce}.{issued_at}.{encoded}.{sig}"

    def _verify_state_signature(self, state: str) -> tuple[str, int, str] | None:
        """校验 state 签名；不合法返回 ``None``。"""
        try:
            nonce, issued_at_raw, redirect_b64, sig = state.split(".", 3)
            issued_at = int(issued_at_raw)
            padding = "=" * (-len(redirect_b64) % 4)
            redirect_uri = base64.urlsafe_b64decode(redirect_b64 + padding).decode()
        except (ValueError, UnicodeDecodeError):
            return None

        # ⚠️ 签名载荷必须与签发时**完全一致**：
        #    签发时用的是 **base64 编码后的** redirect_uri，
        #    这里也必须用同一份编码串，不能用解码后的明文。
        #    否则两边算出的 HMAC 永远对不上（会表现为「state 签名无效」）。
        payload = f"{nonce}.{issued_at_raw}.{redirect_b64}"
        expected = hmac.new(
            self._state_secret, payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return nonce, issued_at, redirect_uri

    def issue_state(self, redirect_uri: str) -> str:
        """签发一个 state 并登记。"""
        allowed = self._allowed_redirect_uris()
        if not allowed:
            raise FeishuOAuthError("未配置飞书 OAuth 回调地址", code="oauth_not_configured")
        if redirect_uri not in allowed:
            # ⚠️ 开放重定向防护：只认白名单里的 redirect_uri。
            raise FeishuOAuthError(
                "回调地址不在允许列表中", code="redirect_not_allowed"
            )

        # ⚠️ nonce **不能含 '.'** —— state 的字段分隔符就是 '.'。
        #    这里用 urlsafe_b64（字符集 [A-Za-z0-9_-]），显式规避该风险。
        nonce = (
            base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip("=").replace(".", "-")
        )
        # ⚠️ **必须是整数秒** —— float 会把小数点变成 state 的第 5 个字段，
        #    导致 split(".", 3) 解析错位 → 表现为「state 签名无效」。
        issued_at = int(time.time())
        state = self._sign_state(nonce, redirect_uri, issued_at)
        self._states[state] = OAuthState(
            redirect_uri=redirect_uri, created_at=issued_at, nonce=nonce
        )
        self._prune_states()
        return state
    def _prune_states(self) -> None:
        """清理过期 state，避免内存无限增长。"""
        ttl = max(60, settings.feishu_oauth_state_ttl_seconds)
        cutoff = time.time() - ttl
        expired = [key for key, val in self._states.items() if val.created_at < cutoff]
        for key in expired:
            self._states.pop(key, None)

    def consume_state(self, state: str) -> OAuthState:
        """校验并**消费** state。

        ⚠️ 校验通过后立即从存储删除 —— 保证一次性（约束 C2）。
        """
        if not state:
            raise FeishuOAuthError("缺少 state 参数", code="state_missing")

        verified = self._verify_state_signature(state)
        if verified is None:
            raise FeishuOAuthError("state 签名无效", code="state_invalid")
        _nonce, issued_at, redirect_uri = verified

        ttl = max(60, settings.feishu_oauth_state_ttl_seconds)
        if time.time() - issued_at > ttl:
            self._states.pop(state, None)
            raise FeishuOAuthError("state 已过期", code="state_expired")

        stored = self._states.pop(state, None)
        if stored is None:
            # ⚠️ 签名有效但服务端没有对应记录 —— 说明：
            #    (a) state 已被消费过（重放），或
            #    (b) 进程重启过（内存态丢失）。
            #    两种情况都**拒绝** —— 安全侧。
            raise FeishuOAuthError("state 无效或已被使用", code="state_consumed")

        if stored.redirect_uri != redirect_uri:
            raise FeishuOAuthError("state 回调地址不匹配", code="state_mismatch")
        return stored

    # ─────────────────────────────────────────────────────────────
    # 授权 URL
    # ─────────────────────────────────────────────────────────────

    def build_authorize_url(self, redirect_uri: str, state: str) -> str:
        """拼装飞书授权页 URL。

        ⚠️ **参数名必须严格按官方文档**（`获取授权码` 接口）：

            https://accounts.feishu.cn/open-apis/authen/v1/authorize
                ?client_id=<App ID>
                &response_type=code
                &redirect_uri=<URL 编码后的回调地址>
                &scope=<空格分隔>
                &state=<防 CSRF>

        ⚠️⚠️ **曾经写成 `app_id=` —— 这是 bug**（2026-11-17 实测发现）。

        官方参数名是 **`client_id`**，不是 `app_id`；且 **`response_type=code` 是必填**。
        写错时飞书**不会**报「缺少参数」，而是渲染一个**错误页**：
            重定向 URL 有误，请联系应用管理员……错误码：20029

        ⇒ 症状极具误导性：明明后台配了 redirect_uri，却报「重定向 URL 有误」，
          让人反复去核对后台白名单（而后台是对的）。

        ⚠️ 另外 `redirect_uri` 必须经过 **URL 编码**（`urlencode` 已处理）。
        """
        from urllib.parse import urlencode

        params: dict[str, str] = {
            # ⚠️ 是 client_id，不是 app_id —— 见上方说明
            "client_id": settings.feishu_app_id,
            # ⚠️ 必填，授权码流程固定为 code
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
        scopes = self._allowed_scopes()
        if scopes:
            params["scope"] = " ".join(scopes)
        return f"{OAUTH_AUTHORIZE_ENDPOINT}?{urlencode(params)}"

    # ─────────────────────────────────────────────────────────────
    # 主流程
    # ─────────────────────────────────────────────────────────────

    def exchange_code_for_employee(self, code: str, state: str) -> OAuthResult:
        """用授权码换取本系统员工身份。

        流程：
            校验并消费 state
              → code 换 user_access_token
              → user_access_token 换用户信息（open_id / union_id）
              → resolve_employee(open_id=...) 解析员工
              → 返回员工身份（**不含任何飞书 token**）
        """
        # ⚠️ `consume_state` 返回的 stored 里有 redirect_uri ——
        #    必须原样传给 token 端点，飞书会校验它与发起授权时是否一致
        #    （不一致报 20071）。省掉它虽然有时也能过，但会让这类问题
        #    推迟到更难排查的时候才暴露。
        stored_state = self.consume_state(state)

        try:
            token_data = self.client.exchange_user_token(
                code,
                redirect_uri=stored_state.redirect_uri,
            )
        except FeishuTokenError as exc:
            raise FeishuOAuthError(
                f"飞书授权码换取失败：{exc.message}", code="code_exchange_failed"
            ) from exc
        except FeishuError as exc:
            raise FeishuOAuthError(
                f"飞书授权码换取失败：{exc.message}", code="code_exchange_failed"
            ) from exc

        user_access_token = str(token_data.get("access_token") or "")
        if not user_access_token:
            raise FeishuOAuthError("飞书未返回用户令牌", code="no_access_token")

        # ⚠️ user_access_token 从此往下只在**局部变量**中存在，
        #    函数返回后即随作用域释放。绝不写入日志、响应体或数据库。
        #
        # ⚠️⚠️ **必须用 ``get_login_user_info``**（2026-11-17 实测发现，F2）。
        #    之前用的是通讯录接口 ``get_user_info(token, user_id)`` ——
        #    那个接口路径里必须有 user_id，而 OAuth 令牌响应**不含 user_id**
        #    （官方响应字段：access_token / refresh_token / expires_in / scope …）
        #    ⇒ 必然抛「缺少 user_id」，**免登永远走不到最后一步**。
        #
        #    官方给登录用户准备的接口是 ``/authen/v1/user_info`` ——
        #    不需要 user_id，直接返回当前登录用户的身份。
        try:
            info = self.client.get_login_user_info(user_access_token)
        except FeishuError as exc:
            raise FeishuOAuthError(
                f"飞书用户信息获取失败：{exc.message}", code="user_info_failed"
            ) from exc

        # ⚠️ 「获取用户信息」接口的响应结构是：
        #      {"code":0, "data": {"open_id":..., "union_id":..., "name":...}}
        #    而 ``_request_with_token`` 已经剥掉了外层 ``data``，
        #    所以这里拿到的是扁平的用户信息字典。
        #
        #    ⚠️ 某些响应（或录制数据）会多包一层 ``user`` ——
        #    兼容两种形状，避免响应结构变动直接打挂免登。
        user_info = info.get("user") if isinstance(info.get("user"), dict) else info

        open_id = str(user_info.get("open_id") or "")
        if not open_id:
            raise FeishuOAuthError("飞书未返回 open_id", code="no_open_id")

        union_id = user_info.get("union_id")
        # ⚠️ user_id 是**可选字段** —— 需要额外的「获取用户 user ID」权限才会返回。
        #    没有它不影响免登（open_id 才是主键）；留着是为了多一路身份解析兜底。
        user_id = user_info.get("user_id") or token_data.get("user_id")

        # ⚠️ 身份解析交给 resolve_employee —— 它只认**在职且启用**的员工。
        #    这一步是安全边界，不要绕过（约束 C3）。
        identity_service = FeishuIdentityService(self.db)

        # ⚠️ 三种身份键都试一遍 —— 不同同步批次可能只落了其中一种。
        identity_candidates = [("open_id", open_id)]
        if user_id:
            identity_candidates.append(("user_id", str(user_id)))
        if union_id:
            identity_candidates.append(("union_id", str(union_id)))

        employee = identity_service.resolve_employee(open_id=open_id)
        if employee is None:
            # 再试一次 user_id / union_id —— 不同同步批次可能只落了其中一种。
            for kwargs in (
                {"user_id": str(user_id)} if user_id else None,
                {"union_id": str(union_id)} if union_id else None,
            ):
                if not kwargs:
                    continue
                employee = identity_service.resolve_employee(**kwargs)
                if employee is not None:
                    break

        if employee is None:
            # ⚠️⚠️ **不能只报一句「尚未绑定」**（2026-11-17 实测发现）。
            #
            #    「解析不到人」有**四种**截然不同的原因，要做的处理完全不同：
            #      not_synced       飞书里根本没这个人 → 该跑同步
            #      exited           人已离职           → 走离职流程，不该再登录
            #      disabled         账号被禁用         → 该找管理员解封
            #      ambiguous        一个身份绑多人     → 人工解绑（数据冲突）
            #
            #    把它们混成一句话，会让排查方向**完全错** ——
            #    例如「已离职」被当成「没绑定」，于是有人去反复跑同步（没用）。
            #
            #    ⚠️ 诊断必须用**全部**候选键 —— 单一键可能匹配不上，
            #       但另一个键能匹配上（不同同步批次只落了其中一种）。
            diagnosis = identity_service.diagnose_identity(
                **{key: value for key, value in identity_candidates if value}
            )
            status = str(diagnosis.get("status") or "not_synced")

            messages = {
                "exited": "该账号对应的员工已离职，无法使用飞书免登登录。",
                "disabled": "该员工账号已被停用，无法使用飞书免登登录，请联系管理员。",
                "ambiguous": "该飞书账号在系统中存在多条绑定记录，请联系管理员处理。",
                "inactive_binding": (
                    "该飞书账号的绑定记录已失效，请联系管理员执行组织架构同步。"
                ),
                "not_synced": (
                    "该飞书账号未同步到系统，请联系管理员执行组织架构同步后重试。"
                ),
            }
            raise FeishuOAuthError(
                messages.get(status, messages["not_synced"]),
                code="identity_not_bound",
                # ⚠️ 额外带上诊断码 —— 前端可据此给出**针对性**提示，
                #    而不用从中文文案里猜。
                reason=status,
            )

        return OAuthResult(
            employee_id=employee.id,
            username=employee.login_account or employee.id,
            real_name=employee.name,
            open_id=open_id,
            union_id=str(union_id) if union_id else None,
            user_id=str(user_id) if user_id else None,
        )


__all__ = [
    "FeishuOAuthError",
    "FeishuOAuthService",
    "OAuthResult",
    "OAuthState",
    "OAUTH_AUTHORIZE_ENDPOINT",
]
