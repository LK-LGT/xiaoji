"""OpenAI 兼容模型客户端（P3-2）。

本模块是**唯一**新增的厂商相关代码 —— 其余六个智能体只依赖
`base.py::ModelClient` 协议，不依赖任何厂商。

支持范围（够用即止）：
- ``POST {base_url}/chat/completions``
- ``response_format={"type": "json_object"}`` —— 用于强制 JSON 输出
- ``tools`` / ``tool_calls`` —— OpenAI function calling 协议

可对接的厂商（同一套代码，只改配置）：
- DeepSeek   : https://api.deepseek.com/v1
- 阿里通义    : https://dashscope.aliyuncs.com/compatible-mode/v1
- 智谱 GLM    : https://open.bigmodel.cn/api/paas/v4
- 月之暗面    : https://api.moonshot.cn/v1
- 本地 Ollama : http://127.0.0.1:11434/v1

⚠️ 安全边界（与 `base.py` 的硬规矩一致）：
1. 模型**只产建议** —— 输出契约里没有 score 字段，物理上无法改分
2. 任何异常都**不抛给调用方** —— 返回 ``(None, error)``，由上层降级
3. API Key **只出现在 Authorization 头**，绝不进日志、绝不进错误信息
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

import httpx

from .base import Completion, ToolCall

logger = logging.getLogger(__name__)

# 模型输出的最大字符数 —— 与 `tool_agent.py::max_message_chars` 同量级，
# 防止模型自由发挥把上下文撑爆。
_MAX_OUTPUT_CHARS = 16000


# ---------------------------------------------------------------------------
# Schema → 形状提示
# ---------------------------------------------------------------------------


def _example_for_type(spec: dict[str, Any], root: dict[str, Any] | None = None) -> Any:
    """按 JSON Schema 的类型生成一个**示例值**（不是 schema 本身）。

    ⚠️ 必须解析 ``$ref`` —— Pydantic 生成的 schema 里，
    子模型是 ``{"$ref": "#/$defs/RatingItem"}``，
    不解析的话示例值就是 ``null``（毫无指导意义）。
    """
    root = root if root is not None else spec
    if not isinstance(spec, dict):
        return None
    # --- 解析 $ref（只支持本地 #/$defs/... —— Pydantic 的生成方式）---
    ref = spec.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.split("/")[-1]
        defs = (root.get("$defs") or {})
        target = defs.get(name)
        if isinstance(target, dict):
            return _example_for_type(target, root)
        return {"<字段名>": "<字段值>"}
    kind = spec.get("type")
    if isinstance(kind, list):
        kind = kind[0] if kind else None
    if kind == "object":
        properties = spec.get("properties")
        if not properties:
            # ⚠️ 无属性的对象（如 `dict[str, Any]`）—— schema 说不出里面有什么。
            #    返回空对象，并让调用方知道「这里需要额外说明」。
            return {"<字段名>": "<字段值>"}
        return {
            key: _example_for_type(sub or {}, root)
            for key, sub in properties.items()
        }
    if kind == "array":
        item = spec.get("items") or {}
        return [_example_for_type(item, root)]
    if kind == "boolean":
        return False
    if kind == "integer":
        return 0
    if kind == "number":
        return 0
    if kind == "null":
        return None
    if kind == "string":
        # ⚠️ 不用 schema 的 description —— 那是给人看的，会干扰模型。
        return "<字符串>"
    return None


def _schema_shape_hint(schema: dict[str, Any]) -> str:
    """把 JSON Schema 转成**具体示例 JSON**（不含 title/properties 等元字段）。

    ⚠️ 为什么不能直接把 Schema 原文给模型：
    实测 DeepSeek 会把 Schema **原样吐回来**当成答案 ——
    因为 ``title`` / ``properties`` 这些描述性字段让它以为
    「你要的就是这个 schema」。示例则不会被误认。

    ⚠️ 返回空串时，调用方**不追加**格式要求 —— 保持原 prompt 不变。
    """
    if not isinstance(schema, dict):
        return ""
    try:
        example = _example_for_type(schema)
    except Exception:
        return ""
    if not isinstance(example, dict) or not example:
        return ""
    return json.dumps(example, ensure_ascii=False, indent=2)


def _unwrap_nested_contract(parsed: dict[str, Any]) -> dict[str, Any]:
    """把「套娃」的输出契约**拆回一层**。

    ⚠️ 现象（DeepSeek-flash 实测）：
    模型有时把整个契约**再包一层 JSON 字符串**塞进 ``answer``：

        {"answer": "{\\"answer\\": \\"……\\", \\"citations\\": [\\"tool:…\\"]}", "citations": []}

    结果：``citations`` 变成空数组（外层没填），
    而真正的正文和引用**都埋在 answer 字符串里** ——
    界面上会直接显示一坨 JSON 原文。

    这里做一次**受限**的解包：
      - 只在 ``answer`` 是字符串、且去掉空白后以 ``{`` 开头时才尝试；
      - 只解一层，不递归（防止无限套娃）；
      - 解出来的对象必须同时含 ``answer`` 字段，否则放弃（保持原样）；
      - 保留外层已填的 ``citations``（外层优先，不被内层覆盖为空）。
    """
    answer = parsed.get("answer")
    if not isinstance(answer, str):
        return parsed
    stripped = answer.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return parsed
    try:
        inner = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return parsed
    if not isinstance(inner, dict) or not isinstance(inner.get("answer"), str):
        return parsed
    merged = dict(inner)
    # 外层已填的引用优先 —— 内层可能是模型随手写的空数组。
    if not parsed.get("citations") and inner.get("citations"):
        merged["citations"] = inner["citations"]
    return merged


class OpenAICompatibleClient:
    """OpenAI 兼容接口的最小实现。

    只依赖「Chat Completions + function calling」这一子集，
    不使用任何厂商专有扩展 —— 保证换供应商只改配置。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("模型 API Key 未配置")
        if not base_url:
            raise ValueError("模型 Base URL 未配置")
        if not model:
            raise ValueError("模型名未配置")
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        # trust_env=False —— 不读 HTTP_PROXY 之类的环境变量，
        # 避免代理把带 Key 的请求送到非预期的地方。
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> OpenAICompatibleClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # ModelClient 协议
    # ------------------------------------------------------------------

    def complete(
        self, system: str, user: str, *, schema: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """单轮问答。返回 (解析后的 JSON, 错误信息)。任一失败返回 (None, error)。

        ⚠️ 关于 ``schema``：
        各家厂商对「JSON Schema」的支持程度不一（有的只支持
        ``json_object``，有的支持完整的 ``json_schema``）。
        为了**跨厂商一致**，这里采用最保守的做法：
        **不依赖厂商的 schema 支持**，而是把形状要求写进 system prompt。

        ⚠️⚠️ **但不能直接把 JSON Schema 原文塞进 prompt** ——
        实测 DeepSeek 这么做会**把 Schema 本身当成答案吐回来**：

            {"title":"RatingOutput","properties":{"ratings":{...}}}

        因为 Schema 里带 ``title`` / ``properties`` 这些**描述性**字段，
        模型看到就以为「哦，你要的就是这个 schema」。
        所以这里**只取字段名与必填项**，配上**具体示例**：

            {"ratings": [{"dimension": "...", "level": "...", "reason": "..."}]}

        示例比 Schema 更能让模型对齐形状 —— 且不含 schema 元字段。
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        if schema:
            shape = _schema_shape_hint(schema)
            if shape:
                messages[0]["content"] = (
                    f"{system}\n\n"
                    f"⚠️ 输出格式硬要求：只输出**一个 JSON 对象**，"
                    f"字段结构必须**严格**如下（示例值仅供参考，请按实际内容填写）：\n"
                    f"{shape}\n"
                    f"只输出这个 JSON 对象本身，不要 markdown 代码块，不要任何解释文字。"
                )

        data, error = self._post(payload)
        if error:
            return None, error
        choices = data.get("choices") or []
        if not choices:
            return None, "模型未返回任何结果"
        content = (choices[0].get("message") or {}).get("content")
        if not content or not str(content).strip():
            return None, "模型返回内容为空"
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None, "模型返回内容不是合法 JSON"
        if not isinstance(parsed, dict):
            return None, "模型返回的 JSON 不是对象"
        return parsed, None

    def complete_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
    ) -> tuple[Completion | None, str | None]:
        """带工具的多轮调用。返回 (Completion, 错误信息)。

        ⚠️ 关于 ``schema``：
        工具链路的最后一轮（模型不再调工具、改为作答）同样需要
        **明确输出形状**。没有它时模型经常把整个契约**套娃**塞进
        ``answer`` 字符串里（形如 ``{"answer":"{\\"answer\\":...}"}``），
        导致 ``citations`` 字段丢失、答案里混着 JSON 原文。
        所以这里和 ``complete()`` 一样，把形状要求注入 system 消息。
        """
        messages = copy.deepcopy(messages)
        if schema:
            shape = _schema_shape_hint(schema)
            if shape:
                messages[0]["content"] = (
                    f"{messages[0].get('content', '')}\n\n"
                    f"⚠️ 输出格式硬要求：只输出**一个 JSON 对象**，"
                    f"字段结构必须**严格**如下（示例值仅供参考，请按实际内容填写）：\n"
                    f"{shape}\n"
                    f"只输出这个 JSON 对象本身，不要 markdown 代码块，不要任何解释文字。"
                    f"\ncitations 里每一项必须是工具返回内容中**原样出现**的 source_id 或 evidence id。"
                )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
        }
        data, error = self._post(payload)
        if error:
            return None, error
        choices = data.get("choices") or []
        if not choices:
            return None, "模型未返回任何结果"
        message = choices[0].get("message") or {}

        # --- 工具调用 ---
        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            calls: list[ToolCall] = []
            for index, raw in enumerate(raw_calls):
                function = raw.get("function") or {}
                name = function.get("name")
                if not name:
                    continue
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                calls.append(ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=str(name),
                    arguments=arguments,
                ))
            if not calls:
                return None, "模型返回了无法解析的工具调用"
            # ⚠️ content 与 tool_calls 同时出现 = 歧义 —— 上层会判 AMBIGUOUS_COMPLETION。
            #    这里**主动丢弃 content**，让上层走工具分支。
            return Completion(tool_calls=calls), None

        # --- 普通文本输出 ---
        content = message.get("content")
        if not content or not str(content).strip():
            return None, "模型返回内容为空"
        if len(str(content)) > _MAX_OUTPUT_CHARS:
            return None, "模型返回内容过长"
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            # 不是 JSON —— 包装成 answer 字段，交给上层 Schema 校验决定接受与否。
            parsed = {"answer": str(content), "citations": []}
        if not isinstance(parsed, dict):
            parsed = {"answer": str(content), "citations": []}
        parsed = _unwrap_nested_contract(parsed)
        return Completion(content=parsed), None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """POST /chat/completions，带有限重试。"""
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_error = "模型调用失败"
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException:
                last_error = "模型调用超时"
            except httpx.HTTPError as exc:
                # ⚠️ 不打印 exc 的完整文本 —— 可能带上带 Key 的请求头。
                last_error = f"模型网络错误：{type(exc).__name__}"
            else:
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        last_error = "模型返回内容不是合法 JSON"
                    else:
                        if isinstance(data, dict):
                            return data, None
                        last_error = "模型返回结构无效"
                elif response.status_code in (429, 500, 502, 503, 504):
                    # 可重试：限流或服务端临时故障。
                    last_error = f"模型服务暂时不可用（HTTP {response.status_code}）"
                elif response.status_code in (401, 403):
                    # 鉴权错误 —— 重试没有意义，直接放弃。
                    logger.error("模型鉴权失败（HTTP %s）", response.status_code)
                    return {}, "模型鉴权失败，请检查 API Key"
                else:
                    logger.error("模型调用失败（HTTP %s）", response.status_code)
                    return {}, f"模型调用失败（HTTP {response.status_code}）"
            if attempt < self._max_retries:
                continue
        return {}, last_error


def build_client_from_env() -> OpenAICompatibleClient | None:
    """按环境变量构造客户端；配置不全时返回 ``None``（由调用方决定是否降级）。"""
    api_key = os.getenv("AGENT_MODEL_API_KEY", "").strip()
    base_url = os.getenv("AGENT_MODEL_BASE_URL", "").strip()
    model = os.getenv("AGENT_MODEL_NAME", "").strip() or os.getenv("MODEL_NAME", "").strip()
    if not api_key or not base_url or not model:
        logger.warning(
            "真模型配置不完整（api_key=%s, base_url=%s, model=%s），将使用 echo 模式",
            bool(api_key), bool(base_url), bool(model),
        )
        return None
    try:
        timeout = float(os.getenv("AGENT_MODEL_TIMEOUT_SECONDS", "60"))
    except ValueError:
        timeout = 60.0
    try:
        retries = int(os.getenv("AGENT_MODEL_MAX_RETRIES", "2"))
    except ValueError:
        retries = 2
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_retries=retries,
    )
