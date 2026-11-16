"""飞书同步错误分类。

把飞书错误码、HTTP 状态码和本地校验错误统一映射为「错误分级」，
让前端能据此决定展示什么操作按钮（自动重试 / 等待人工 / 需改配置）。

分级语义：
- ``retryable``：限流、5xx、超时等瞬时故障，可自动重试；
- ``config``：权限不足、应用未发布、可见范围不足，需要改配置；
- ``manual``：身份冲突等需人工介入的语义冲突；
- ``permanent``：数据非法，重试也无意义；
- ``unknown``：无法归类，默认不自动重试。
"""

from __future__ import annotations

from typing import Any

# 错误分级常量
CATEGORY_RETRYABLE = "retryable"
CATEGORY_CONFIG = "config"
CATEGORY_MANUAL = "manual"
CATEGORY_PERMANENT = "permanent"
CATEGORY_UNKNOWN = "unknown"

# 飞书错误码 -> 分级。
# 含义与 `feishu_client.FEISHU_ERROR_CODES` 保持一致，但视角不同：
# 这里回答「能不能自动重试」，而不是「发生了什么」。
FEISHU_CODE_CATEGORY: dict[int, str] = {
    # Token 无效 / 过期：刷新 Token 后可重试。
    99991663: CATEGORY_RETRYABLE,
    99991664: CATEGORY_RETRYABLE,
    # 权限类：需要管理员开通权限或发布应用版本。
    99991668: CATEGORY_CONFIG,
    99991669: CATEGORY_CONFIG,
    99991672: CATEGORY_CONFIG,
    # 可见范围不足：需要调整应用的可见范围配置。
    99991679: CATEGORY_CONFIG,
    # 租户 Token 不匹配：应用绑定错误，需修配置。
    99991688: CATEGORY_CONFIG,
    # 限流与服务端繁忙：可自动重试。
    1254290: CATEGORY_RETRYABLE,
    99991400: CATEGORY_RETRYABLE,
    # 参数校验失败：重试无意义。
    99992402: CATEGORY_PERMANENT,
    # 资源不存在：可能是员工已离职或部门已删除，属永久失败。
    1254043: CATEGORY_PERMANENT,
    99991661: CATEGORY_PERMANENT,
}

# 错误文本关键词 -> 分级。
# 用于本地校验异常与未登记的飞书错误码。
_TEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("已绑定到其他员工", CATEGORY_MANUAL),
    ("禁止静默换绑", CATEGORY_MANUAL),
    ("冲突", CATEGORY_MANUAL),
    ("限流", CATEGORY_RETRYABLE),
    ("busy", CATEGORY_RETRYABLE),
    ("繁忙", CATEGORY_RETRYABLE),
    ("超时", CATEGORY_RETRYABLE),
    ("timeout", CATEGORY_RETRYABLE),
    ("网络错误", CATEGORY_RETRYABLE),
    ("暂时不可用", CATEGORY_RETRYABLE),
    ("权限", CATEGORY_CONFIG),
    ("版本未发布", CATEGORY_CONFIG),
    ("可见范围", CATEGORY_CONFIG),
    ("配置", CATEGORY_CONFIG),
    ("校验失败", CATEGORY_PERMANENT),
    ("缺少 user_id", CATEGORY_PERMANENT),
    ("缺少 department_id", CATEGORY_PERMANENT),
    ("不存在", CATEGORY_PERMANENT),
)


def classify_feishu_error(exc: Exception) -> str:
    """根据异常对象推断错误分级。

    ``FeishuError`` 会优先使用其 ``code`` 与 ``retryable`` 标记；
    其它异常按消息关键词匹配。
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in FEISHU_CODE_CATEGORY:
        return FEISHU_CODE_CATEGORY[code]

    # 显式标记为可重试的错误（如 5xx、超时）优先。
    if getattr(exc, "retryable", False):
        return CATEGORY_RETRYABLE

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if status_code == 429 or status_code >= 500:
            return CATEGORY_RETRYABLE
        if status_code in (401, 403):
            return CATEGORY_CONFIG

    return classify_message(str(exc))


def classify_message(message: str) -> str:
    """按消息文本推断错误分级。"""
    text = (message or "").lower()
    for keyword, category in _TEXT_PATTERNS:
        if keyword.lower() in text:
            return category
    return CATEGORY_UNKNOWN


def classify_item_failure(
    message: str,
    *,
    error_code: str | None = None,
) -> str:
    """分类单条同步失败记录。

    ``error_code`` 形如 ``FEISHU_99991672`` 时优先按数字错误码判定。
    """
    if error_code:
        digits = "".join(ch for ch in error_code if ch.isdigit())
        if digits:
            try:
                numeric = int(digits)
            except ValueError:
                numeric = None
            if numeric is not None and numeric in FEISHU_CODE_CATEGORY:
                return FEISHU_CODE_CATEGORY[numeric]
    return classify_message(message)


def is_auto_retryable(category: str) -> bool:
    """该分级是否允许自动重试。"""
    return category == CATEGORY_RETRYABLE


def describe_category(category: str | None) -> str:
    """给前端用的中文说明。"""
    return {
        CATEGORY_RETRYABLE: "瞬时故障，可自动重试",
        CATEGORY_CONFIG: "需要调整飞书应用配置或权限",
        CATEGORY_MANUAL: "需人工确认处理",
        CATEGORY_PERMANENT: "数据本身有误，重试无效",
        CATEGORY_UNKNOWN: "未分类错误，建议人工排查",
    }.get(category or "", "未分类错误，建议人工排查")


def category_payload(category: str | None) -> dict[str, Any]:
    """构造前端可直接消费的分类信息。"""
    return {
        "category": category or CATEGORY_UNKNOWN,
        "label": describe_category(category),
        "autoRetryable": is_auto_retryable(category or CATEGORY_UNKNOWN),
    }
