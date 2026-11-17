"""飞书卡片构造器（FS-3，约束 C6）。

为什么单独一个模块
----------------
卡片 JSON 2.0 的**字段层级错一个就是渲染空白** —— 而且飞书不会报错，
只会显示一个空卡片。踩过这个坑的人都知道那种「接口返回 200 但用户看到空白」。

所以本模块的定位是：**卡片结构的唯一出口**。

- 业务代码只调 ``build_*_card``，**不自己拼 dict**；
- 单测直接对着构造器的输出断言层级；
- 想加字段，先改这里，再改调用方。

卡片 JSON 2.0 的骨架
------------------
官方格式（``card`` 字段用 JSON 2.0 时）::

    {
      "schema": "2.0",
      "config": { "update_multi": true, "style": { "text_size": { "normal_v2": { "default": "normal" } } } },
      "header": {
        "title": { "tag": "plain_text", "content": "标题" },
        "template": "blue"
      },
      "body": {
        "elements": [ ... ]
      }
    }

⚠️ **几个最容易写错的点**
----------------------
1. ``header.title`` 是**对象** ``{"tag": "plain_text", "content": "..."}``，
   不是裸字符串 —— 写成字符串整个卡片不渲染。
2. ``body.elements`` 是**数组**，每个元素有自己的 ``tag``。
   错写成 ``body.element``（少个 s）→ 空白卡片。
3. 输入框（``input``）在 JSON 2.0 里是独立 tag，
   **不是** ``plain_text`` 的 ``element`` 属性。
4. ``form`` 的 ``elements`` 只能包含 ``input`` / ``select_static`` /
   ``multi_select_static`` / ``date_picker`` / ``overflow`` 等**表单控件**，
   不能塞 ``markdown`` —— 要放说明文字得用 ``form`` 外的 ``markdown`` 元素。
5. 按钮放在 ``button`` tag 里，``value`` 字典是**回调时原样回传**的载荷，
   里面放业务参数（会话 ID、动作名），飞书会原样带回。
6. ``schema`` 是字符串 ``"2.0"``，不是数字 ``2.0``。

透传字段
--------
``update_multi`` 允许同一张卡片的多个版本共存（更新时不覆盖历史），
``update_multi`` + ``streaming_mode`` 是飞书新版流式卡片的用法，
这里保持默认关闭，避免和普通卡片行为混在一起搞混。
"""

from __future__ import annotations

from typing import Any

#: 卡片 JSON 2.0 版本标识（**字符串**，不是数字）
CARD_SCHEMA_VERSION = "2.0"

#: 卡片构造器里用的动作名（回调时从 ``action.value.action`` 取回）
ACTION_FILL_MONTH_PLAN = "fill_month_plan"
ACTION_SUBMIT_DRAFT = "submit_draft"
ACTION_CANCEL = "cancel"
ACTION_CONFIRM = "confirm"


def _text(content: str) -> dict[str, Any]:
    """构造纯文本元素。"""
    return {"tag": "plain_text", "content": content}


def _markdown(content: str) -> dict[str, Any]:
    """构造 Markdown 元素。"""
    return {"tag": "markdown", "content": content}


def _header(title: str, template: str = "blue") -> dict[str, Any]:
    """构造卡片头。

    ⚠️ ``title`` 必须是 ``{"tag": "plain_text", "content": ...}`` 对象。
    """
    return {
        "title": _text(title),
        "template": template,
    }


def _button(
    label: str,
    action: str,
    value: dict[str, Any] | None = None,
    *,
    type: str = "primary",
    url: str = "",
) -> dict[str, Any]:
    """构造按钮元素。

    Args:
        label: 按钮文字。
        action: 动作名 —— 回调时从 ``action.value.action`` 取回。
        value: 回调时原样回传的载荷（放业务参数）。
        type: 按钮样式（primary / default / danger）。
        url: 跳转链接（给了就变成链接按钮，不触发回调）。
    """
    payload: dict[str, Any] = {"action": action}
    if value:
        payload.update(value)
    element: dict[str, Any] = {
        "tag": "button",
        "text": _text(label),
        "type": type,
        "value": payload,
    }
    if url:
        element["url"] = url
    return element


def build_card(
    *,
    title: str,
    elements: list[dict[str, Any]],
    header_template: str = "blue",
    update_multi: bool = False,
) -> dict[str, Any]:
    """组装一张完整的 JSON 2.0 卡片。

    这是**唯一**的卡片组装入口 —— 所有 ``build_*_card`` 都走它，
    保证每张卡片的骨架结构完全一致（少一处漏字段就是空白卡片）。
    """
    return {
        "schema": CARD_SCHEMA_VERSION,
        "config": {
            "update_multi": update_multi,
        },
        "header": _header(title, header_template),
        "body": {
            "elements": elements,
        },
    }


def build_month_plan_form_card(
    *,
    plan_month: str,
    session_id: str,
    existing_title: str = "",
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造「填月计划」表单卡片。

    这是机器人会话填表的**入口卡片**。员工填完点「保存草稿」，
    回调里带着 ``form`` 字段值过来，服务端据此写草稿。

    Args:
        plan_month: 目标月份（YYYY-MM）。
        session_id: 机器人会话 ID —— 回调用它定位会话。
        existing_title: 已存在的草稿标题（草稿续填场景）。
        fields: 预填值（键：字段名）。

    字段集与 ``month_plan`` / ``month_plan_item`` 对齐：
    ``task_name`` / ``task_content`` / ``deliverable`` / ``quality`` /
    ``performance_weight`` / ``estimated_value``。
    """
    fields = fields or {}

    elements: list[dict[str, Any]] = [
        _markdown(
            f"**{plan_month} 月计划**\n\n"
            "填写下列内容后点「保存草稿」。"
            "保存只是**存草稿**，提交仍需在系统里走正常审批流程。"
        ),
    ]

    if existing_title:
        elements.append(_markdown(f"当前草稿：*{existing_title}*"))

    elements.append(
        {
            "tag": "form",
            "elements": [
                {
                    "tag": "input",
                    "element_id": "task_name",
                    "name": "task_name",
                    "input_type": "plain_text",
                    "placeholder": {"tag": "plain_text", "content": "任务名称（必填）"},
                    "default_value": fields.get("task_name", ""),
                    "required": True,
                    "max_length": 256,
                },
                {
                    "tag": "input",
                    "element_id": "task_content",
                    "name": "task_content",
                    "input_type": "plain_text",
                    "placeholder": {"tag": "plain_text", "content": "任务内容"},
                    "default_value": fields.get("task_content", ""),
                    "max_length": 1000,
                },
                {
                    "tag": "input",
                    "element_id": "deliverable",
                    "name": "deliverable",
                    "input_type": "plain_text",
                    "placeholder": {"tag": "plain_text", "content": "交付物"},
                    "default_value": fields.get("deliverable", ""),
                    "max_length": 1000,
                },
                {
                    "tag": "input",
                    "element_id": "quality",
                    "name": "quality",
                    "input_type": "plain_text",
                    "placeholder": {"tag": "plain_text", "content": "质量标准"},
                    "default_value": fields.get("quality", ""),
                    "max_length": 1000,
                },
                {
                    "tag": "input",
                    "element_id": "performance_weight",
                    "name": "performance_weight",
                    "input_type": "number",
                    "placeholder": {"tag": "plain_text", "content": "绩效权重（0-100）"},
                    "default_value": str(fields.get("performance_weight", "") or ""),
                },
                {
                    "tag": "input",
                    "element_id": "estimated_value",
                    "name": "estimated_value",
                    "input_type": "number",
                    "placeholder": {"tag": "plain_text", "content": "预估价值"},
                    "default_value": str(fields.get("estimated_value", "") or ""),
                },
            ],
        }
    )

    elements.append(
        _button(
            "保存草稿",
            ACTION_SUBMIT_DRAFT,
            {"session_id": session_id, "plan_month": plan_month},
        )
    )
    elements.append(
        _button(
            "取消",
            ACTION_CANCEL,
            {"session_id": session_id},
            type="default",
        )
    )

    return build_card(
        title=f"填 {plan_month} 月计划",
        elements=elements,
    )


def build_draft_saved_card(
    *,
    plan_month: str,
    plan_id: str,
    item_count: int,
    submit_url: str = "",
) -> dict[str, Any]:
    """构造「草稿已保存」回执卡片。

    ⚠️ **这里绝不能说「已提交」**（约束 C4）——
    机器人只写草稿，状态流转归 L2 状态机管。
    措辞上说「已保存草稿，请到系统里提交」，
    否则员工会以为已经走完审批了。

    Args:
        plan_month: 月份。
        plan_id: 草稿 ID。
        item_count: 条目数。
        submit_url: 「去提交」链接（前端页面）。
    """
    elements: list[dict[str, Any]] = [
        _markdown(
            f"**{plan_month} 月计划草稿已保存**\n\n"
            f"共 {item_count} 条条目，草稿 ID `{plan_id}`。\n\n"
            "⚠️ 目前**只是草稿**，尚未提交。"
            "请到系统内点「提交」走正常审批流程。"
        ),
    ]

    if submit_url:
        elements.append(
            _button(
                "去提交",
                ACTION_CONFIRM,
                {"plan_id": plan_id, "plan_month": plan_month},
                type="primary",
                url=submit_url,
            )
        )

    return build_card(
        title="草稿已保存",
        elements=elements,
        header_template="green",
    )


def build_prompt_card(
    *,
    session_id: str,
    plan_month: str,
    hint: str = "",
) -> dict[str, Any]:
    """构造引导卡片（员工发了关键词但会话还没建起来时用）。"""
    elements: list[dict[str, Any]] = [
        _markdown(hint or "请在下方填写月计划内容。"),
        _button(
            "开始填写",
            ACTION_FILL_MONTH_PLAN,
            {"session_id": session_id, "plan_month": plan_month},
        ),
    ]
    return build_card(title="月计划填表", elements=elements, header_template="indigo")


def build_error_card(message: str) -> dict[str, Any]:
    """构造错误提示卡片。"""
    return build_card(
        title="操作未完成",
        elements=[_markdown(message)],
        header_template="red",
    )


__all__ = [
    "ACTION_CANCEL",
    "ACTION_CONFIRM",
    "ACTION_FILL_MONTH_PLAN",
    "ACTION_SUBMIT_DRAFT",
    "CARD_SCHEMA_VERSION",
    "build_card",
    "build_draft_saved_card",
    "build_error_card",
    "build_month_plan_form_card",
    "build_prompt_card",
]
