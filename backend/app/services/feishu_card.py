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
4. ``form`` 必须有全局唯一的 ``name``，且内嵌至少一个带
   ``name`` 和 ``form_action_type="submit"`` 的按钮。
5. 按钮放在 ``button`` tag 里，``value`` 字典是**回调时原样回传**的载荷，
   里面放业务参数（会话 ID、动作名），飞书会原样带回。
6. ``schema`` 是字符串 ``"2.0"``，不是数字 ``2.0``。

透传字段
--------
JSON 2.0 的 ``update_multi`` 只支持 true（共享卡片），默认也是 true。
本模块省略可选的 ``config``；不能显式设置 ``update_multi=False``。
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


def build_qa_card(content: str) -> dict[str, Any]:
    """查询依据按纯文本展示，防止证据文本注入卡片链接或提及。"""
    return {
        "schema": CARD_SCHEMA_VERSION,
        "header": _header("绩效查询与计算依据"),
        "body": {"elements": [{"tag": "div", "text": _text(content)}]},
    }


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
    name: str = "",
    form_action_type: str = "",
) -> dict[str, Any]:
    """构造按钮元素。

    Args:
        label: 按钮文字。
        action: 动作名 —— 回调时从 ``action.value.action`` 取回。
        value: 回调时原样回传的载荷（放业务参数）。
        type: 按钮样式（primary / default / danger）。
        url: 跳转链接（给了就变成链接按钮，不触发回调）。
        name: 组件唯一标识。**内嵌在 ``form`` 里时必填**，
            且需在**整张卡片内全局唯一** —— 否则提交失败。
        form_action_type: 按钮在表单容器里的交互类型。
            ``submit`` = 提交整个表单；``reset`` = 重置输入值。
            **内嵌在 ``form`` 里时必填**，且表单内**至少要有一个
            ``submit`` 按钮** —— 否则飞书报「no submit button in the
            form container」。

    ⚠️ **回调载荷位置**（2026-11-17 实测确认）：

    - **不在** ``form`` 里的按钮 → 载荷在 ``action.value``
    - **在** ``form`` 里的按钮 → 表单字段值在 ``action.form_value``
      （按各 input 的 ``name`` 映射），按钮自身的 ``value`` 仍在
      ``action.value``

    ⇒ 见 ``feishu_bot.extract_card_action``。
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
    else:
        # JSON 2.0 explicitly declares callback behavior and its business payload.
        element["behaviors"] = [{"type": "callback", "value": payload}]
    if name:
        element["name"] = name
    if form_action_type:
        element["form_action_type"] = form_action_type
    return element


def build_card(
    *,
    title: str,
    elements: list[dict[str, Any]],
    header_template: str = "blue",
) -> dict[str, Any]:
    """组装一张完整的 JSON 2.0 卡片。

    这是**唯一**的卡片组装入口 —— 所有 ``build_*_card`` 都走它，
    保证每张卡片的骨架结构完全一致（少一处漏字段就是空白卡片）。

    ⚠️⚠️ **不要写 ``config.update_multi``**（2026-11-17 修）

    飞书官方文档（卡片 JSON 2.0 结构 → ``config``）原文：

        ``update_multi`` | 否 | Boolean | true | 是否为共享卡片。
        **JSON 2.0 目前只支持设置为 true**，
        表示卡片内容更新后所有收到卡片的人都能看到。

    ⇒ **写 ``false`` 会被飞书直接拒绝**，实测报错：

        Failed to create card content, ext=ErrCode: 300302;
        ErrMsg: update_multi is false; （错误码 230099）

    ⇒ 而**整个 ``config`` 都不写**是可以的（它是可选字段）——
      实测「无 config」比「config={}」更保险：后者虽然当前能过，
      但官方说 ``update_multi`` 默认 true，写空 dict 依赖默认值，
      **不写则完全不依赖任何默认值**。

    ⇒ 所以这里**根本不生成 ``config``**。
    """
    return {
        "schema": CARD_SCHEMA_VERSION,
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

    ⚠️⚠️ **JSON 2.0 表单容器的硬性要求**（2026-11-17 真机实测修正）

    飞书官方文档（表单容器 / 输入框 / 按钮 三篇）：

    | # | 要求 | 不满足时的实测报错 |
    |---|---|---|
    | 1 | ``form`` 必须有 ``name``（全局唯一） | ``form's name is required and can not be empty string`` |
    | 2 | ``input_type`` **只有** ``text`` / ``multiline_text`` / ``password`` | ``invalid input type`` |
    | 3 | 表单内**至少一个** ``form_action_type: "submit"`` 的按钮，且按钮要**内嵌在 form 里** | ``there is no submit button in the form container, at least one`` |
    | 4 | 表单内所有交互组件都要有 ``name``（全局唯一） | 提交失败 |

    ⇒ 这四点**单测测不出来**（构造出来的 dict 本身「看起来对」），
    **只有真发给飞书才知道** —— 所以本文件的所有卡片结构变更
    都必须过一遍真机验证。
    """
    fields = fields or {}

    display_month = plan_month.replace("-", "")
    if len(display_month) == 6 and display_month.isdigit():
        display_month = f"{display_month[:4]} 年 {int(display_month[4:])} 月"

    elements: list[dict[str, Any]] = [
        _markdown("**填写一项工作任务**\n已自动填入的内容可直接修改；任务名称必填。"),
    ]

    if existing_title:
        elements.append(_markdown(f"当前草稿：*{existing_title}*"))

    # ⚠️ ``input_type`` 只有三种合法值（2026-11-17 真机实测）：
    #   text / multiline_text / password
    #   ⚠️ 之前用的 ``plain_text`` / ``number`` **都不存在** ——
    #   飞书报 ``invalid input type``。
    #   ⇒ 数字字段也用 ``text``（服务端再转数字）。
    form_elements: list[dict[str, Any]] = [
        {
            "tag": "input",
            "element_id": "task_name",
            "name": "task_name",
            "input_type": "text",
            "label": _text("任务名称 · 必填"),
            "label_position": "top",
            "placeholder": _text("例如：完成客户管理模块上线"),
            "default_value": str(fields.get("task_name", "") or ""),
            "required": True,
            "max_length": 256,
        },
        {
            "tag": "input",
            "element_id": "task_content",
            "name": "task_content",
            "input_type": "multiline_text",
            "label": _text("工作内容"),
            "label_position": "top",
            "placeholder": _text("准备做什么？写清主要工作和完成范围"),
            "default_value": str(fields.get("task_content", "") or ""),
            "max_length": 1000,
        },
        {
            "tag": "input",
            "element_id": "deliverable",
            "name": "deliverable",
            "input_type": "multiline_text",
            "label": _text("交付成果"),
            "label_position": "top",
            "placeholder": _text("例如：上线功能、验收报告或交付文档"),
            "default_value": str(fields.get("deliverable", "") or ""),
            "max_length": 1000,
        },
        {
            "tag": "input",
            "element_id": "quality",
            "name": "quality",
            "input_type": "multiline_text",
            "label": _text("验收标准"),
            "label_position": "top",
            "placeholder": _text("达到什么标准算完成？尽量写成可核对的结果"),
            "default_value": str(fields.get("quality", "") or ""),
            "max_length": 1000,
        },
        {
            "tag": "input",
            "element_id": "performance_weight",
            "name": "performance_weight",
            "input_type": "text",
            "label": _text("绩效权重（%）"),
            "label_position": "top",
            "placeholder": _text("填写 0–100，例如 30"),
            "default_value": str(fields.get("performance_weight", "")) if fields.get("performance_weight") is not None else "",
        },
        {
            "tag": "input",
            "element_id": "estimated_value",
            "name": "estimated_value",
            "input_type": "text",
            "label": _text("预估价值"),
            "label_position": "top",
            "placeholder": _text("填写预估数值"),
            "default_value": str(fields.get("estimated_value", "")) if fields.get("estimated_value") is not None else "",
        },
        # ⚠️ **提交按钮必须内嵌在 form 里**（2026-11-17 真机实测）：
        #   放在 form 外 → 飞书报「no submit button in the form container」。
        #   ⚠️ ``form_action_type="submit"`` 是「让整个表单被提交」的标记；
        #   没有它，按钮点了只是普通回传，不会带表单字段值。
        _button(
            "保存草稿",
            ACTION_SUBMIT_DRAFT,
            {"session_id": session_id, "plan_month": plan_month},
            name="submit_draft_btn",
            form_action_type="submit",
        ),
    ]

    form_elements[-1]["width"] = "fill"
    elements.append(
        {
            "tag": "form",
            # ⚠️ ``name`` 必填（2026-11-17 真机实测）
            "name": "month_plan_form",
            "elements": form_elements,
        }
    )
    elements.append(_markdown("保存后可在绩效系统中继续完善并正式提交审批。"))
    # Keep cancel outside the form so it never submits or validates inputs.
    cancel = _button("取消填写", ACTION_CANCEL, {"session_id": session_id}, type="default")
    cancel["width"] = "fill"
    elements.append(cancel)

    return build_card(
        title=f"{display_month} · 月计划",
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
