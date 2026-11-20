"""只读工具注册、参数校验、预算控制和服务端固定地址的 HTTP 出口。"""

from __future__ import annotations

import copy
import json
import logging
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

logger = logging.getLogger(__name__)

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
    #: 链路追踪 ID —— 只进服务端日志，用于把一次降级和它的工具调用对上。
    #: ⚠️ 不进模型上下文，也不回传给用户。
    trace_id: str = ""


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
            logger.warning("工具调用超时 name=%s trace=%s", name, spec.trace_id)
            return None, "TOOL_TIMEOUT"
        except Exception as exc:  # noqa: BLE001
            # ⚠️ **必须留痕** —— 这里以前是静默吞掉，
            # 结果是「降级了但看不出为什么」：
            #   · 工具网关地址错 → 一样是 TOOL_FAILED
            #   · 令牌过期       → 一样是 TOOL_FAILED
            #   · 后端在重启     → 一样是 TOOL_FAILED
            # 排障只能靠临时插桩（试过一次，很慢）。
            #
            # 分类记录，方便一眼看出断在哪一环：
            #   http_4xx / http_5xx → 业务侧拒绝或业务侧异常
            #   connect / read       → 网络或后端进程不可达
            #   other                → 其它（如响应体不是预期信封）
            #
            # ⚠️ **不记令牌、不记参数值、不带异常原文** —— 参数只记键名。
            #
            # ⚠️ **不能加 exc_info=True** —— 那会把异常的原始消息和完整堆栈
            # 打进日志。异常消息里可能含用户数据（实测：工具抛出的异常消息
            # 会带上 PII），堆栈对这里是「统一工具出口」没有额外价值。
            # 分类信息（kind + status）已经足够定位问题。
            logger.error(
                "工具调用失败 name=%s trace=%s kind=%s detail=%s",
                name, spec.trace_id, _classify_tool_error(exc), _safe_error_detail(exc),
            )
            return None, "TOOL_FAILED"


def _classify_tool_error(exc: BaseException) -> str:
    """把工具异常分类成**可运维**的几类。

    ⚠️ 目的：让运维看一行日志就知道断在哪一环，
    而不是面对一个笼统的 ``TOOL_FAILED`` 去猜。

    - ``http_4xx`` —— 业务侧拒绝（令牌无效 / 参数不合法 / 无权限）
    - ``http_5xx`` —— 业务侧异常（后端崩了或抛异常）
    - ``connect``   —— 连不上（地址错 / 端口没监听 / 进程在重启）
    - ``read``      —— 连上了但没响应（后端卡死 / 事件循环被阻塞）
    - ``other``     —— 其它（响应体不是预期信封等）
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.ConnectError):
        return "connect"
    if isinstance(exc, httpx.ReadTimeout):
        return "read"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "http_error"
    if isinstance(exc, json.JSONDecodeError):
        return "bad_json"
    return "other"


def _safe_error_detail(exc: BaseException) -> str:
    """生成**不含敏感信息**的错误摘要。

    ⚠️ 令牌、参数值、**异常原文**都不进日志。
    HTTP 状态码很关键：401 说明令牌问题，403 说明权限问题，
    404 说明路径/任务不对，这三类在日志里必须能直接区分。

    ⚠️ 异常消息**一律不透传** —— 实测工具抛出的异常消息可能带 PII
    （例如把入参拼进消息里）。只保留异常**类型名**，
    足以判断是业务错误还是传输错误。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"status={exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return type(exc).__name__
    return type(exc).__name__


@dataclass(frozen=True)
class HttpToolHandler:
    base_url: str
    token: str = field(repr=False)
    name: str = ""
    trace_id: str = ""
    prefix: str = "/v1/agent-tools"

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("工具网关地址无效")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", self.name):
            raise ValueError("工具名称无效")
        if not re.fullmatch(r"/[A-Za-z0-9/_-]{0,120}", self.prefix):
            raise ValueError("工具网关路径前缀无效")

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.call(args, 8.0)

    def call(self, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        with httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "POST", f"{self.base_url.rstrip('/')}{self.prefix}/{self.name}",
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


def http_handler(base_url: str, token: str, name: str, trace_id: str = "", prefix: str = "/v1/agent-tools") -> HttpToolHandler:
    """base_url 必须由服务端配置注入，不能从用户请求中取。

    ``prefix`` 用于区分**工具域**：
    - ``/v1/agent-tools`` —— 考核任务域（指标/证据/部门聚合）
    - ``/v1/plan-tools``  —— 计划域（与考核任务解耦）
    """
    return HttpToolHandler(base_url, token, name, trace_id, prefix)
