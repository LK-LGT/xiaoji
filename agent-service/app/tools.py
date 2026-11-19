"""只读工具注册、参数校验、预算控制和服务端固定地址的 HTTP 出口。"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator

from .grounding import redact_obj
from .tool_runtime import bounded_call

IDENTITY_PARAMS = frozenset({"employee_id", "target_employee_id", "user_id", "department_id"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    read_only: bool = True
    timeout_s: float = 8.0
    max_chars: int = 8000
    identity_params: frozenset[str] = field(default_factory=frozenset)
    max_result_items: int = 200


def _check_schema(node: Any, forbidden: frozenset[str]) -> None:
    if isinstance(node, dict):
        if "$ref" in node or "$dynamicRef" in node:
            raise ValueError("工具参数不接受 schema 引用")
        if forbidden.intersection(node.get("properties", {})):
            raise ValueError("工具参数不得包含身份字段")
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            raise ValueError("对象参数必须禁止额外字段")
        for value in node.values():
            _check_schema(value, forbidden)
    elif isinstance(node, list):
        for value in node:
            _check_schema(value, forbidden)


def _identity_in_args(node: Any, forbidden: frozenset[str]) -> bool:
    if isinstance(node, dict):
        return bool(forbidden.intersection(node)) or any(_identity_in_args(x, forbidden) for x in node.values())
    return isinstance(node, list) and any(_identity_in_args(x, forbidden) for x in node)


def _limit_items(node: Any, limit: int) -> tuple[Any, bool]:
    truncated = False
    if isinstance(node, list):
        truncated = len(node) > limit
        values = []
        for value in node[:limit]:
            item, cut = _limit_items(value, limit)
            values.append(item)
            truncated |= cut
        return values, truncated
    if isinstance(node, dict):
        values = {}
        for key, value in node.items():
            item, cut = _limit_items(value, limit)
            values[key] = item
            truncated |= cut
        return values, truncated
    return node, False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.read_only or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", spec.name):
            raise ValueError("仅允许合法名称的只读工具")
        if spec.name in self._tools or not spec.description:
            raise ValueError("重复工具或缺少描述")
        if not math.isfinite(spec.timeout_s) or spec.timeout_s <= 0 or spec.max_chars < 128 or spec.max_result_items < 1:
            raise ValueError("工具预算无效")
        Draft202012Validator.check_schema(spec.parameters)
        if spec.parameters.get("type") != "object":
            raise ValueError("参数必须为对象 schema")
        _check_schema(spec.parameters, IDENTITY_PARAMS | spec.identity_params)
        self._tools[spec.name] = copy.deepcopy(spec)

    def get(self, name: str) -> ToolSpec | None:
        spec = self._tools.get(name)
        return copy.deepcopy(spec) if spec else None

    def schema(self, allow: set[str]) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {
            "name": name, "description": spec.description, "parameters": copy.deepcopy(spec.parameters),
        }} for name, spec in self._tools.items() if name in allow]

    def call(
        self, name: str, args: dict[str, Any], allow: set[str], *, timeout_s: float | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        spec = self._tools.get(name)
        if spec is None or name not in allow:
            return None, "TOOL_FORBIDDEN"
        try:
            if not isinstance(args, dict) or _identity_in_args(args, IDENTITY_PARAMS | spec.identity_params):
                return None, "INVALID_ARGUMENTS"
            if len(json.dumps(args, allow_nan=False)) > 16000:
                return None, "ARGUMENTS_TOO_LARGE"
            if not Draft202012Validator(spec.parameters).is_valid(args):
                return None, "INVALID_ARGUMENTS"
            budget = min(spec.timeout_s, timeout_s) if timeout_s is not None else spec.timeout_s
            handler = spec.handler
            def invoke():
                if isinstance(handler, HttpToolHandler):
                    return handler.call(copy.deepcopy(args), budget)
                return handler(copy.deepcopy(args))
            data = bounded_call(invoke, budget)
            if not isinstance(data, dict):
                return None, "INVALID_TOOL_RESULT"
            data, cut = _limit_items(redact_obj(data), spec.max_result_items)
            if cut:
                data = {"data": data, "truncated": True}
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False)) > spec.max_chars:
                data = {"truncated": True, "note": "结果过长，请缩小查询范围"}
            return data, None
        except TimeoutError:
            return None, "TOOL_TIMEOUT"
        except Exception:
            return None, "TOOL_FAILED"


@dataclass(frozen=True)
class HttpToolHandler:
    base_url: str
    token: str = field(repr=False)
    name: str = ""
    trace_id: str = ""

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("工具网关地址无效")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", self.name):
            raise ValueError("工具名称无效")

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.call(args, 8.0)

    def call(self, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        with httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "POST", f"{self.base_url.rstrip('/')}/v1/agent-tools/{self.name}",
                json=args, headers={"Authorization": f"Bearer {self.token}", "X-Agent-Trace-Id": self.trace_id},
            ) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise ValueError("工具响应过大")
                body = json.loads(raw)
                if not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("data"), dict):
                    raise ValueError("工具响应无效")
                return body["data"]


def http_handler(base_url: str, token: str, name: str, trace_id: str = "") -> HttpToolHandler:
    """base_url 必须由服务端配置注入，不能从用户请求中取。"""
    return HttpToolHandler(base_url, token, name, trace_id)
