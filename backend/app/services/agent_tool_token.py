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


def _sign(encoded: str) -> str:
    if not settings.agent_tool_secret:
        raise AgentToolUnavailable()
    return hmac.new(
        settings.agent_tool_secret.encode(), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()


def _valid_claims(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    return (
        raw.get("aud") == AUDIENCE
        and isinstance(raw.get("sub"), str)
        and 0 < len(raw["sub"]) <= 64
        and isinstance(raw.get("task"), str)
        and len(raw["task"]) <= 64
        and raw.get("scope") in ("self", "department")
        and isinstance(raw.get("allow"), list)
        and 0 < len(raw["allow"]) <= 32
        and all(isinstance(x, str) and 0 < len(x) <= 64 for x in raw["allow"])
        and type(raw.get("iat")) is int
        and type(raw.get("exp")) is int
        and 0 < raw["exp"] - raw["iat"] <= MAX_TTL_SECONDS
    )


def issue_agent_tool_token(
    *, employee_id: str, task_id: str, allow: Sequence[str],
    scope: str = "self", ttl: int | None = None,
) -> str:
    if isinstance(allow, str):
        raise ValueError("allow 必须为工具名序列")
    now = int(time.time())
    effective_ttl = settings.agent_tool_token_ttl_seconds if ttl is None else ttl
    raw = {
        "aud": AUDIENCE, "sub": employee_id, "task": task_id,
        "allow": list(allow), "scope": scope, "iat": now,
        "exp": now + max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, int(effective_ttl))),
    }
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
    )


def assert_tool_allowed(payload: AgentToolTokenPayload, tool_name: str) -> None:
    if tool_name not in payload.allow:
        raise AgentToolTokenForbidden()
