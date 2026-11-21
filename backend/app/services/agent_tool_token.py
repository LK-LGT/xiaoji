"""L2 内部签发的工具凭据，与登录凭据隔离，不提供 HTTP 签发接口。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

from ..core.config import settings

MAX_TTL_SECONDS = 300
MIN_TTL_SECONDS = 30
AUDIENCE = "agent-tools"


class AgentToolTokenError(Exception):
    status_code = 401
    message = "工具令牌无效"


class AgentToolTokenExpired(AgentToolTokenError):
    status_code = 403


class AgentToolTokenForbidden(AgentToolTokenError):
    status_code = 403


class AgentToolUnavailable(AgentToolTokenError):
    status_code = 503
    message = "工具网关未配置"


@dataclass(frozen=True)
class AgentToolTokenPayload:
    sub: str
    task: str
    allow: tuple[str, ...]
    scope: str
    iat: int
    exp: int
    #: ★ P0：本次调用被授权的**目标人**。
    #:
    #: ``None`` = 目标即提问者本人（默认，绝大多数调用）。
    #:
    #: ⚠️ **模型看不到这个值** —— 它只存在于签名令牌里，
    #:    工具层从令牌读取目标人，**不接受**请求体里的目标人。
    #:    这是"后端硬权限"的最后一环：即使模型被诱导传了
    #:    别人的 employee_id，也会被忽略。
    subj: str | None = None
    #: ★ P0：授权理由（写入审计）。
    reason: str = ""


def _sign(encoded: str) -> str:
    if not settings.agent_tool_secret:
        raise AgentToolUnavailable()
    return hmac.new(
        settings.agent_tool_secret.encode(), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()


def _valid_claims(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    if not (
        raw.get("aud") == AUDIENCE
        and isinstance(raw.get("sub"), str)
        and 0 < len(raw["sub"]) <= 64
        and isinstance(raw.get("task"), str)
        and len(raw["task"]) <= 64
        # ⚠️ scope 取值扩展（P0）：
        #   self       —— 目标即本人
        #   department —— 部门范围（管理范围）
        #   peer       —— ★ 同部门同级别同事（**只读**）
        #   org        —— 全组织（HR）
        and raw.get("scope") in ("self", "department", "peer", "org")
        and isinstance(raw.get("allow"), list)
        and 0 < len(raw["allow"]) <= 32
        and all(isinstance(x, str) and 0 < len(x) <= 64 for x in raw["allow"])
        and type(raw.get("iat")) is int
        and type(raw.get("exp")) is int
        and 0 < raw["exp"] - raw["iat"] <= MAX_TTL_SECONDS
    ):
        return False
    # ⚠️ ``subj`` 可选（缺省 = 查本人）。
    #    若存在则必须是合法 id —— 否则拒绝整个令牌，
    #    而不是"忽略这个字段"（忽略会让伪造的 subj 静默降级为查自己，
    #    掩盖安全问题）。
    subj = raw.get("subj")
    if subj is not None and not (
        isinstance(subj, str) and 0 < len(subj) <= 64
    ):
        return False
    reason = raw.get("reason")
    if reason is not None and not (
        isinstance(reason, str) and len(reason) <= 200
    ):
        return False
    return True


def issue_agent_tool_token(
    *, employee_id: str, task_id: str, allow: Sequence[str],
    scope: str = "self", ttl: int | None = None,
    subject_id: str | None = None, reason: str = "",
) -> str:
    """签发工具令牌。

    ⚠️ ``subject_id`` 是**服务端授权后的**目标人 ——
       调用方必须先通过授权判定（``qa_authorization.authorize_qa_subject``），
       再把授权通过的目标人传进来。

       ⚠️ **不要**把用户消息里提到的名字直接传进来 ——
       那等于把"用户说了什么"当成"用户被允许看什么"。
    """
    if isinstance(allow, str):
        raise ValueError("allow 必须为工具名序列")
    now = int(time.time())
    effective_ttl = settings.agent_tool_token_ttl_seconds if ttl is None else ttl
    raw = {
        "aud": AUDIENCE, "sub": employee_id, "task": task_id,
        "allow": list(allow), "scope": scope, "iat": now,
        "exp": now + max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, int(effective_ttl))),
    }
    if subject_id is not None:
        raw["subj"] = str(subject_id)
    if reason:
        raw["reason"] = str(reason)[:200]
    if not _valid_claims(raw):
        raise ValueError("工具令牌载荷无效")
    raw["allow"] = sorted(set(raw["allow"]))
    encoded = base64.urlsafe_b64encode(
        json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    return f"{encoded}.{_sign(encoded)}"


def verify_agent_tool_token(token: str) -> AgentToolTokenPayload:
    if not settings.agent_tool_secret:
        raise AgentToolUnavailable()
    if not isinstance(token, str) or len(token) > 8192 or token.count(".") != 1:
        raise AgentToolTokenError()
    encoded, signature = token.split(".")
    try:
        if not hmac.compare_digest(signature, _sign(encoded)):
            raise AgentToolTokenError()
        raw = json.loads(base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        ))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise AgentToolTokenError() from exc
    if not _valid_claims(raw) or raw["iat"] > int(time.time()):
        raise AgentToolTokenError()
    if raw["exp"] <= int(time.time()):
        raise AgentToolTokenExpired()
    return AgentToolTokenPayload(
        sub=raw["sub"], task=raw["task"], allow=tuple(raw["allow"]),
        scope=raw["scope"], iat=raw["iat"], exp=raw["exp"],
        subj=raw.get("subj"), reason=raw.get("reason") or "",
    )


def assert_tool_allowed(payload: AgentToolTokenPayload, tool_name: str) -> None:
    if tool_name not in payload.allow:
        raise AgentToolTokenForbidden()
