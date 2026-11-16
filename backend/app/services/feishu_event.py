"""飞书事件信封的解密、验签与归一化。

背景
----
飞书「事件订阅」有两种推送形态，取决于后台是否启用了加密策略：

**明文模式**（未配置 Encrypt Key）

    {
      "schema": "2.0",
      "header": { "token": "...", "event_id": "...", "event_type": "..." },
      "event":  { "object": { "approval_id": "...", "status": "APPROVED" } }
    }

**加密模式**（配置了 Encrypt Key）—— **只有这一个字段**

    { "encrypt": "<base64(iv + AES-256-CBC(SHA256(key), PKCS7(明文)))>" }

加密模式下，连 ``header.token`` 都读不到，**必须先解密**才能拿到事件内容
与 Verification Token。

本模块提供三件事：

1. :func:`decrypt_event` —— AES-256-CBC 解密（官方算法）
2. :func:`verify_signature` —— SHA-256 签名校验（可不解密就完成来源校验）
3. :func:`parse_event` —— 把 v2.0 / v1.0 信封归一化成统一的内部事件

⚠️ **签名校验必须用原始 body 字节**
   官方原文：先拼 ``timestamp + nonce + encrypt_key``，再拼**原始 body**，
   然后 sha256。反序列化后再算会得到不同结果（键序、空白都可能变）。

安全边界
--------
- 解密失败 / 验签失败 → 一律拒绝，**不做降级放行**
- 未配置 Encrypt Key 却收到加密 body → 拒绝（不能猜测）
- 校验用 :func:`hmac.compare_digest` 防时序攻击
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class FeishuEventError(Exception):
    """飞书事件解析 / 验签失败。"""

    def __init__(self, message: str, *, code: str = "FEISHU_EVENT_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ===========================================================================
# 常量
# ===========================================================================

#: 飞书签名请求头
HEADER_TIMESTAMP = "X-Lark-Request-Timestamp"
HEADER_NONCE = "X-Lark-Request-Nonce"
HEADER_SIGNATURE = "X-Lark-Signature"

#: 飞书审批状态事件名
EVENT_TYPE_APPROVAL_UPDATED = "approval.approval.updated_v4"


# ===========================================================================
# 解密
# ===========================================================================


def decrypt_event(encrypt_key: str, encrypted: str) -> str:
    """解密飞书加密事件，返回明文 JSON 字符串。

    官方算法（逐字）：

    1. ``key = SHA256(Encrypt Key)``（32 字节）
    2. base64 解码后**前 16 字节为 IV**，其余为密文
    3. ``AES-256-CBC`` 解密，PKCS7 去填充

    Args:
        encrypt_key: 后台「加密策略」页的 Encrypt Key。
        encrypted: 请求体 ``encrypt`` 字段的值（base64）。

    Returns:
        解密后的明文字符串（通常是 JSON）。

    Raises:
        FeishuEventError: base64 非法、密文过短或去填充失败（通常是密钥不对）。
    """
    if not encrypt_key:
        raise FeishuEventError("未配置 Encrypt Key，无法解密事件", code="NO_ENCRYPT_KEY")
    if not encrypted:
        raise FeishuEventError("encrypt 字段为空", code="EMPTY_ENCRYPT")

    # 延迟导入 —— 没装 pycryptodome 时也能启动服务，
    # 只有真的收到加密事件才会报错（而不是 import 期直接崩）。
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:  # pragma: no cover —— 依赖缺失时的兜底
        raise FeishuEventError(
            "缺少 pycryptodome 依赖，无法解密飞书事件（pip install pycryptodome）",
            code="MISSING_CRYPTO",
        ) from exc

    try:
        blob = base64.b64decode(encrypted, validate=True)
    except Exception as exc:
        raise FeishuEventError(f"encrypt 字段不是合法 base64：{exc}", code="BAD_BASE64") from exc

    if len(blob) < 16:
        raise FeishuEventError("密文长度不足一个 AES 块", code="BAD_CIPHER")

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv, ciphertext = blob[:16], blob[16:]

    plain = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)

    # PKCS7 去填充：末字节是填充长度
    pad_len = plain[-1]
    if pad_len < 1 or pad_len > 16 or pad_len > len(plain):
        raise FeishuEventError("解密失败：填充长度非法（Encrypt Key 可能不对）", code="BAD_PADDING")

    try:
        return plain[:-pad_len].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FeishuEventError("解密结果不是合法 UTF-8（Encrypt Key 可能不对）", code="BAD_UTF8") from exc


# ===========================================================================
# 验签
# ===========================================================================


def verify_signature(
    *,
    encrypt_key: str,
    timestamp: str,
    nonce: str,
    signature: str,
    raw_body: bytes,
) -> bool:
    """校验飞书请求签名（SHA-256）。

    官方算法（逐字）：

    1. ``b1 = (timestamp + nonce + encrypt_key).encode("utf-8")``
    2. ``b = b1 + 原始 body``
    3. ``s = sha256(b).hexdigest()``
    4. 比对 ``s`` 与请求头 ``X-Lark-Signature``

    ⚠️ ``raw_body`` 必须是**原始请求体字节** —— 反序列化再序列化会得到
    不同的字节序列，签名就对了不上。

    Returns:
        True 表示签名正确。
    """
    if not encrypt_key:
        return False
    if not timestamp or not nonce or not signature:
        return False

    b1 = f"{timestamp}{nonce}{encrypt_key}".encode()
    expected = hashlib.sha256(b1 + raw_body).hexdigest()
    return hmac.compare_digest(expected, signature)


# ===========================================================================
# 归一化
# ===========================================================================


@dataclass(slots=True)
class NormalizedEvent:
    """归一化后的飞书事件。

    把 v2.0 / v1.0 两种信封抹平成同一个结构，调用方不必再关心版本差异。
    """

    token: str = ""
    event_id: str = ""
    event_type: str = ""
    app_id: str = ""
    tenant_key: str = ""
    #: 审批实例 ID（v2.0: event.object.approval_id）
    instance_code: str = ""
    #: 审批定义 code（v2.0: event.object.approval_code）
    approval_code: str = ""
    #: 审批状态（v2.0: event.object.status）
    instance_status: str = ""
    #: 操作人（v2o: event.object.user_id / operator）
    user_id: str = ""
    open_id: str = ""
    #: 审批意见
    comment: str = ""
    #: 关联码（若事件里带了）
    correlation_key: str = ""
    #: 本系统计划标识（若事件里带了）
    plan_type: str = ""
    plan_id: str = ""
    #: 原始事件体（供扩展字段读取）
    raw: dict[str, Any] = field(default_factory=dict)
    #: 信封版本
    schema_version: str = ""


def _first_str(source: dict[str, Any], *keys: str) -> str:
    """按顺序取第一个非空字符串字段。"""
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _extract_status(obj: dict[str, Any]) -> str:
    """从审批事件对象里取状态。

    v4 事件的 ``event.object`` 结构大致是::

        {
          "approval_id": "...",
          "approval_code": "...",
          "status": "APPROVED",
          "process_obj": "{\\"status\\": \\"APPROVED\\", ...}"   # JSON 字符串
        }

    优先用顶层 ``status``；没有则从 ``process_obj`` 里解出来。
    """
    status = _first_str(obj, "status", "instance_status")
    if status:
        return status.upper()

    raw_process = obj.get("process_obj")
    if isinstance(raw_process, str) and raw_process.strip():
        try:
            parsed = json.loads(raw_process)
        except (TypeError, ValueError):
            return ""
        if isinstance(parsed, dict):
            return _first_str(parsed, "status", "instance_status").upper()
    return ""


def parse_event(body: dict[str, Any]) -> NormalizedEvent:
    """把飞书事件信封归一化。

    支持三种形态：

    1. **v2.0 信封**（现行）—— ``header`` + ``event``
    2. **v1.0 信封**（旧版）—— 顶层字段 + ``event``
    3. **扁平格式**（本项目早期测试用）—— 顶层直接放 ``instance_code`` 等

    Args:
        body: 已解析的 JSON body。

    Returns:
        :class:`NormalizedEvent`。

    Raises:
        FeishuEventError: body 不是 dict。
    """
    if not isinstance(body, dict):
        raise FeishuEventError("事件体不是 JSON 对象", code="BAD_BODY")

    header = body.get("header")
    header = header if isinstance(header, dict) else {}
    event = body.get("event")
    event = event if isinstance(event, dict) else {}

    # 事件对象：v2.0 在 event.object；v1.0 在 event 本身
    obj = event.get("object")
    obj = obj if isinstance(obj, dict) else event

    result = NormalizedEvent(raw=body)

    # ── 信封版本 ───────────────────────────────────────────────
    result.schema_version = _first_str(body, "schema") or ("2.0" if header else "")

    # ── 通用字段（header 优先，其次顶层）──────────────────────
    result.token = _first_str(header, "token") or _first_str(body, "token")
    result.event_id = _first_str(header, "event_id") or _first_str(body, "event_id") or _first_str(
        body, "uuid"
    )
    result.event_type = _first_str(header, "event_type") or _first_str(body, "event_type")
    result.app_id = _first_str(header, "app_id")
    result.tenant_key = _first_str(header, "tenant_key")

    # ── 审批实例字段 ──────────────────────────────────────────
    result.instance_code = (
        _first_str(obj, "approval_id", "instance_code", "instance_id")
        or _first_str(body, "instance_code", "instance_id")
    )
    result.approval_code = (
        _first_str(obj, "approval_code", "definition_code")
        or _first_str(body, "approval_code")
    )
    result.instance_status = (
        _extract_status(obj)
        or _first_str(body, "instance_status", "status").upper()
    )

    # ── 操作人 ─────────────────────────────────────────────────
    operator = obj.get("operator")
    operator = operator if isinstance(operator, dict) else {}
    result.user_id = (
        _first_str(obj, "user_id")
        or _first_str(operator, "user_id")
        or _first_str(body, "user_id")
    )
    result.open_id = (
        _first_str(obj, "open_id")
        or _first_str(operator, "open_id")
        or _first_str(body, "open_id")
    )

    # ── 意见 / 关联标识 / 计划标识 ─────────────────────────────
    result.comment = (
        _first_str(obj, "comment", "approval_comment")
        or _first_str(body, "comment")
    )
    result.correlation_key = (
        _first_str(obj, "correlation_key")
        or _first_str(body, "correlation_key")
    )
    result.plan_type = _first_str(body, "plan_type").lower()
    result.plan_id = _first_str(body, "plan_id")

    return result


def is_url_verification(body: dict[str, Any]) -> bool:
    """判断是否是飞书的**回调地址校验握手**请求。

    官方：飞书保存回调地址时会 POST 一个带 ``challenge`` 的请求，
    必须在 1 秒内原样返回 ``{"challenge": "<值>"}``。
    """
    if not isinstance(body, dict):
        return False
    if body.get("type") == "url_verification":
        return True
    return bool(body.get("challenge")) and not body.get("event")


def extract_challenge(body: dict[str, Any]) -> str:
    """取出握手请求里的 ``challenge``。"""
    if not isinstance(body, dict):
        return ""
    return _first_str(body, "challenge")
