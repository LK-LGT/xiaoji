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

from . import feishu_card_render

#: 卡片 JSON 2.0 版本标识（**字符串**，不是数字）
CARD_SCHEMA_VERSION = "2.0"

#: 卡片构造器里用的动作名（回调时从 ``action.value.action`` 取回）
ACTION_FILL_MONTH_PLAN = "fill_month_plan"
ACTION_SUBMIT_DRAFT = "submit_draft"
ACTION_CANCEL = "cancel"
ACTION_CONFIRM = "confirm"
#: 绩效问答 —— 选择考核任务（多任务时让用户明确选）
ACTION_SELECT_TASK = "qa_select_task"
#: 绩效问答 —— 选择查询对象（P0-8：同名/多候选同事时让用户明确选）
ACTION_SELECT_SUBJECT = "qa_select_subject"


def build_qa_card(content: str) -> dict[str, Any]:
    """查询依据按纯文本展示，防止证据文本注入卡片链接或提及。"""
    return {
        "schema": CARD_SCHEMA_VERSION,
        "header": _header("绩效查询与计算依据"),
        "body": {"elements": [{"tag": "div", "text": _text(content)}]},
    }


def build_qa_ai_card(
    *,
    answer: str,
    sources: list[str] | None = None,
    citation_count: int = 0,
    degraded: bool = False,
    dropped_citations: int = 0,
    period: str = "",
    task_name: str = "",
) -> dict[str, Any]:
    """构造「AI 绩效问答」卡片。

    ⚠️ **为什么答案用 `plain_text` 而不是 `markdown`**
    ----------------------------------------------
    AI 回答的文本是**模型输出**，不是可信的模板内容。
    飞书的 `markdown` 元素会解析 `<at>`、链接、强调等语法 ——
    恶意或幻觉的内容可能被渲染成「@某人」或外链，
    让用户误以为是系统发的通知。

    ⇒ 答案一律 `plain_text`。代价是没有粗体/列表，
    但**安全优先**。

    ⚠️ **来源徽章也要清洗**
    ----------------------
    `sources` 来自模型（形如 `tool:call_xxx`、evidence UUID）。
    直接展示等于把模型输出原样透给用户 ——
    可能包含换行、超长串、甚至伪造的「来源」条目。
    ⇒ 逐条清洗：只保留可打印字符、限长、限条数。

    Args:
        answer: AI 回答正文。
        sources: 已核验的数据来源标识。
        citation_count: 引用数量（展示用）。
        degraded: 是否降级（AI 不可用时的规则型回答）。
        dropped_citations: 被剔除的无效引用数（> 0 时给中性提示）。
        period: 周期（展示用）。
        task_name: 任务名（展示用）。
    """
    elements: list[dict[str, Any]] = []

    # ── 上下文抬头（纯文本，不用 markdown）────────────────────
    context_bits = [bit for bit in (period, task_name) if bit]
    if context_bits:
        elements.append({"tag": "div", "text": _text(" · ".join(context_bits))})

    # ── 答案正文：Markdown + 真表格 ───────────────────────────
    #
    # ⚠️ **为什么不用 plain_text 直出**
    #   AI 回答是**模型的 Markdown 输出**。用 plain_text 直出等于把
    #   Markdown 源码原样打印给用户 ——
    #   ``| 指标 | 权重 |``、``**加粗**``、``- 列表`` 符号全露出来，
    #   一张卡片 14 行竖线，可读性极差。
    #
    # ⚠️ **但 markdown 元素必须先清洗**
    #   markdown 会解析 ``<at>``、链接等语法，模型输出是**不可信输入** ——
    #   幻觉或恶意内容可能被渲染成「@某人」或外链。
    #   ⇒ 清洗只做删除（见 ``feishu_card_render.sanitize_markdown``）。
    #
    # ⚠️ **而且飞书 markdown 元素不支持表格**
    #   ⇒ 表格必须提取出来，转成飞书 ``table`` 组件（真表格）。
    #
    # ⚠️ **顺序必须保持**
    #   模型常写成「结论 → 表格 → 补充说明」。
    #   把表格全塞到末尾会打乱语义 ⇒ 按**出现位置**逐块渲染。
    #
    # ⚠️ **markdown 是元素本身**，不能包在 ``div`` 的 text 里 ——
    #   那样飞书会**拒收整张卡**：
    #   ``type of element is not supported tag: markdown``。
    blocks = feishu_card_render.extract_blocks(answer)
    for block in blocks:
        if block.table is not None:
            component = feishu_card_render.build_table_component(block.table)
            if component is not None:
                elements.append(component)
                continue
            # ⚠️ 超限（列数/行数）→ 回退成文本，至少别丢内容
            fallback = feishu_card_render.sanitize_markdown(block.table.raw)
            if fallback:
                elements.append(_markdown(fallback))
            continue
        body = feishu_card_render.sanitize_markdown(block.text)
        if body:
            elements.append(_markdown(body))

    if not blocks:
        elements.append(_div("（无内容）"))

    # ── 来源徽章 ──────────────────────────────────────────────
    cleaned = _clean_sources(sources)
    if cleaned:
        elements.append({
            "tag": "div",
            "text": _text(f"数据来源（{len(cleaned)} 项）：" + "、".join(cleaned)),
        })
    elif citation_count:
        elements.append({"tag": "div", "text": _text(f"引用来源 {citation_count} 项")})

    # ── 降级提示：必须显性，不能把「AI 挂了」伪装成正常回答 ──
    if degraded:
        elements.append({
            "tag": "div",
            "text": _text("⚠️ AI 解读暂不可用，以下为系统直接给出的数据。"),
        })

    # ── 引用缺口：中性提示（不是降级）─────────────────────────
    if dropped_citations > 0:
        elements.append({
            "tag": "div",
            "text": _text(f"注：有 {dropped_citations} 处引用未能核验，已从来源中剔除。"),
        })

    return build_card(
        title="绩效问答",
        elements=elements,
        header_template="orange" if degraded else "blue",
    )


def _clean_sources(sources: list[str] | None, *, limit: int = 6, max_len: int = 40) -> list[str]:
    """清洗来源标识（模型输出，不可信）。

    ⚠️ 只保留 **ASCII 标识符字符** —— 字母、数字、下划线、连字符、冒号、点、井号。

    为什么不是「保留所有字母数字」：``str.isalnum()`` 对中文也返回 True，
    模型吐一句「伪造的第二行」会原样混进来源区，看起来像个正经来源条目。
    真实的来源标识（``tool:call_xxx``、UUID）**全是 ASCII**，
    所以限定 ASCII 字符集既保留了正常条目，又挡掉了自然语言注入。

    ⚠️ 另外还要去掉换行 —— 换行会把一条来源拆成两行，
    后一行看起来像另一条来源。

    Args:
        limit: 最多保留几条。
        max_len: 单条最长字符数。
    """
    if not sources:
        return []
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:.#")
    cleaned: list[str] = []
    for raw in sources:
        if not isinstance(raw, str):
            continue
        text = "".join(ch for ch in raw if ch in allowed)
        if not text:
            continue
        if len(text) > max_len:
            text = text[: max_len - 1] + "…"
        if text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _text(content: str) -> dict[str, Any]:
    """构造纯文本元素。

    ⚠️⚠️ **不能直接当 body 元素用**（2026-11-23 真机实测）
    ------------------------------------------------------
    JSON 2.0 的 ``body.elements`` **不接受** ``plain_text`` ——
    实测报错：

        type of element is not supported tag: plain_text,
        path: ROOT -> body -> elements -> [0]

    ⇒ 当 body 元素必须包一层 ``div`` ⇒ 用 ``_div()``。
    ⇒ ``_text`` 只用于**字段值**（label / placeholder / button.text /
    header.title / div.text）。
    """
    return {"tag": "plain_text", "content": content}


def _div(content: str) -> dict[str, Any]:
    """构造**纯文本正文块**（JSON 2.0 的 ``div`` 元素）。

    ⚠️ **这是 body 元素的正确形态** —— 纯文本要包 ``div``，
    直接放 ``plain_text`` 会被飞书拒收（整张卡发不出去）。
    """
    return {"tag": "div", "text": _text(content)}


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


#: P0-9：拒绝场景 → 引导去后台的页面。
#:
#: ⚠️ **为什么不直接把页面名放进卡片**
#:    页面路径因部署而异，写死会让用户点不到。
#:    ⇒ 这里只给**页面名**，让用户自己在后台找。
QA_DENIED_GUIDANCE_PAGES: dict[str, str] = {
    "score": "考核记录",
    "metric_value": "算分集 / 指标实测值",
    "rule": "绩效方案",
    "rating": "考核记录（评级确认）",
    "nudge": "绩效待办",
}


def build_qa_denied_card(
    *,
    message: str,
    guidance: str = "",
    page: str = "",
) -> dict[str, Any]:
    """构造「无权查看」提示卡片（P0-9）。

    ⚠️ **为什么拒绝时必须给「为什么 + 下一步」**
    ------------------------------------------
    只说"没有权限"用户会**反复试同一个问题** ——
    他不知道是自己搞错了、还是系统坏了、还是换个问法就行。
    ⇒ 卡片里必须有：**原因**（组织范围隔离）+ **下一步**（找谁 / 去哪做）。

    ⚠️ **但文案不能区分具体原因**
    ----------------------------
    「跨部门」和「上一级」给**不同文案** ⇒ 用户能通过文案差异
    推断出目标人的层级与部门 —— 这是**信息泄露**，比拒绝对他更糟。
    ⇒ 调用方传的 ``message`` 必须是**统一口径**的文案。
    本函数**不做**任何原因推断，只负责把上游给的话渲染出来。

    ⚠️ **用 plain_text 而非 markdown**
    ------------------------------
    文案可能含员工姓名（如「张三不在可见范围内」）——
    markdown 会把 ``<at>`` 之类的语法解析成「@某人」。
    """
    elements: list[dict[str, Any]] = []
    if message:
        elements.append(_div(message))
    if guidance:
        elements.append({
            "tag": "div",
            "text": _text(f"💡 {guidance}"),
        })
    if page:
        elements.append({
            "tag": "div",
            "text": _text(f"📍 请前往后台「{page}」页面操作。"),
        })
    if not elements:
        elements.append(_div("该操作不在可见范围内。"))

    return build_card(
        title="无法查看该数据",
        elements=elements,
        header_template="orange",
    )


def build_qa_write_denied_card(
    *,
    message: str = "",
    page: str = "",
) -> dict[str, Any]:
    """构造「AI 问答不提供绩效修改功能」卡片（P0-9）。

    ⚠️ **写操作要用红色**
    --------------
    读操作的拒绝是「范围问题」，写操作的拒绝是「能力边界」——
    两者性质不同，标题与配色都该不一样。
    写成"操作未完成"会让用户以为是系统故障，反复重试。
    """
    elements: list[dict[str, Any]] = [
        _div(message or "AI 问答不提供绩效修改功能。"),
    ]
    target_page = page or QA_DENIED_GUIDANCE_PAGES["score"]
    elements.append({
        "tag": "div",
        "text": _text(f"📍 请前往后台「{target_page}」页面操作。"),
    })
    return build_card(
        title="此操作需在后台完成",
        elements=elements,
        header_template="red",
    )


def build_manual_metric_saved_card(
    *,
    metric_name: str,
    value: float,
    metric_date: str,
) -> dict[str, Any]:
    """构造「数据已记录」回执卡片。

    ⚠️ **为什么用 ``plain_text`` 而不是 ``markdown``**
    指标名与日期来自用户输入，可能是模型/OCR 产物 ——
    用 markdown 会被解析成「@某人」或外链。

    ⚠️ **措辞要说清「还没进考核」**
    填了不等于已确认 ⇒ 回执里点明「可修改」。
    """
    percent = f"{value * 100:.1f}%" if 0 <= value <= 1 else f"{value}"
    return build_card(
        title="数据已记录",
        elements=[
            _div(
                f"{metric_date}  {metric_name}：{percent}\n\n"
                "已记录。若数据有误，可再次发送同样的内容覆盖。"
            ),
        ],
        header_template="green",
    )


def build_evidence_saved_card(
    *,
    has_metric: bool,
    metric_date: str = "",
) -> dict[str, Any]:
    """构造「截图已收到」回执卡片。

    ⚠️ **为什么要单独一张卡**
    截图只作**证据**，不构成指标 ⇒ 必须明确告诉员工
    「还需要你填数值」，否则员工以为已经填完了。

    ⚠️ **不要自动 OCR 数值**
    截图里的数字识别错了就是**错误绩效数据**。
    ⇒ 数值必须由员工自己填。
    """
    if has_metric:
        body = (
            "截图已存为证据。\n\n"
            "若数值有变动，请再发一条文字（例如「完播率 38%」）覆盖。"
        )
    else:
        body = (
            "截图已收到，但还缺数值。\n\n"
            "请再发一条文字，例如：\n"
            "完播率 35%\n"
            "留存率 20%"
        )
    return build_card(
        title="截图已收到",
        elements=[_div(body)],
        header_template="blue",
    )


def build_qa_task_picker_card(
    *,
    candidates: list[dict[str, Any]],
    period: str = "",
    question: str = "",
) -> dict[str, Any]:
    """构造「请选择要查询的考核任务」卡片。

    ⚠️ **为什么要这个卡片**
    ----------------------
    同月有多个考核任务时，机器人**不能猜** ——
    猜错就是答错人的绩效 / 错月份的绩效，比答不出来严重得多。
    所以让用户明确选一次。

    ⚠️ **候选名必须可区分**
    ----------------------
    同名任务（比如 23 个「2026 年 11 月月度考核」）如果都显示成同一个名字，
    用户还是选不出来。所以直接用后端算好的 `label`（带区分后缀）。

    ⚠️ **用 `plain_text` 而非 `markdown`**
    ------------------------------
    任务名来自数据库，可能含 ``<at>`` 之类的飞书语法 ——
    用 markdown 会被解析成「@某人」。纯文本最安全。

    ⚠️ **question 跟着卡片走**
    ----------------------
    用户点完按钮后要用**当初那个问题**继续问，
    所以必须把 question 放进按钮的回调载荷。
    """
    lines: list[str] = []
    buttons: list[dict[str, Any]] = []
    for index, item in enumerate(candidates, start=1):
        label = str(item.get("label") or item.get("name") or item.get("task_id") or "")
        lines.append(f"{index}. {label}")
        buttons.append(
            _button(
                f"{index}",
                ACTION_SELECT_TASK,
                {
                    "task_id": str(item.get("task_id") or ""),
                    "period": period or str(item.get("period") or ""),
                    "question": question,
                },
                type="default",
            )
        )

    header = f"你在 {period} 有多个考核任务，请选择要查询的任务：" if period else "请选择要查询的考核任务："
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _text(header + "\n" + "\n".join(lines)),
        },
    ]
    if buttons:
        elements.append({"tag": "action", "actions": buttons})

    return build_card(title="选择考核任务", elements=elements, header_template="orange")


def build_qa_subject_picker_card(
    *,
    candidates: list[dict[str, Any]],
    question: str = "",
    period: str = "",
) -> dict[str, Any]:
    """构造「请选择要查询的同事」卡片（P0-8）。

    ⚠️ **什么时候弹这张卡**
    ----------------------
    问题里提到的名字在**可见范围内**匹配到**多个**同事时。

    ⚠️ **候选集已经过权限收窄**
    --------------------------
    卡片里的候选人**只可能是同部门同级别的同事** ——
    跨部门/跨级/上级**根本不会出现在候选集里**。

    ⚠️ **这既是权限设计，也是防泄露**
    --------------------------------
    如果候选集里包含越权的人，用户看到"有这个人但你不能选"就泄露了
    **此人存在**。收窄后的候选集里只可能出现"允许查的人"，
    所以这张卡本身**不会泄露任何越权信息**。

    ⚠️ **question 跟着卡片走**
    ----------------------
    用户点完按钮后要用**当初那个问题**继续问，
    所以必须把 question 放进按钮的回调载荷。

    ⚠️ **用 `plain_text` 而非 `markdown`**
    ------------------------------
    人名来自数据库，可能含 ``<at>`` 之类的飞书语法 ——
    用 markdown 会被解析成「@某人」。纯文本最安全。
    """
    lines: list[str] = []
    buttons: list[dict[str, Any]] = []
    for index, item in enumerate(candidates, start=1):
        label = str(
            item.get("label")
            or item.get("name")
            or item.get("employee_id")
            or ""
        )
        lines.append(f"{index}. {label}")
        buttons.append(
            _button(
                f"{index}",
                ACTION_SELECT_SUBJECT,
                {
                    "subject_id": str(item.get("employee_id") or ""),
                    "subject_name": str(item.get("name") or ""),
                    "question": question,
                    "period": period,
                },
                type="default",
            )
        )

    if period:
        header = f"匹配到多个同事（{period}），请选择要查询的对象："
    else:
        header = "匹配到多个同事，请选择要查询的对象："

    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _text(header + "\n" + "\n".join(lines)),
        },
    ]
    if buttons:
        elements.append({"tag": "action", "actions": buttons})

    return build_card(title="选择查询对象", elements=elements, header_template="orange")


# ═════════════════════════════════════════════════════════════════════════
# ★ 每日填报提醒（2026-11-23）
# ═════════════════════════════════════════════════════════════════════════

#: 每日填报的表单动作名 —— 卡片回调按它路由。
ACTION_SUBMIT_DAILY_METRIC = "submit_daily_metric"
#: ★ **主管提交效果指标**（2026-11-23 角色倒转）
#:
#: ⚠️ **为什么要有独立的动作名**
#: 剪辑提交的是「数量」，主管提交的是「比率」⇒
#: ⇒ 两者必须**分开处理** —— 归属校验规则不同
#: （剪辑只能填自己；主管可以填**管理范围内**的剪辑）。
#: ⇒ 共用一个动作名会把这两种语义搅在一起。
ACTION_SUBMIT_LEADER_METRIC = "submit_leader_metric"


def _percent_text(value: float) -> str:
    """把 0~1 的比率格式化成百分数文本。"""
    if 0 <= value <= 1:
        return f"{value * 100:.1f}%"
    return f"{value}"


def _daily_table_element(
    headers: list[str], rows: list[list[str]]
) -> dict[str, Any]:
    """构造 ``table`` 元素（**纯展示**）。

    ⚠️ **结构不能错** —— 飞书对非法表格**不报错**，
    只显示成空白卡片（极难排查）。这套结构已真机验证过。

    ⚠️ **列数上限 10、行数上限** —— 官方 ``page_size`` 范围 [1,10]。
    """
    columns = [
        {
            "name": f"col_{index}",
            "display_name": header,
            "data_type": "text",
            "width": "auto",
            "horizontal_align": "left",
            "vertical_align": "center",
        }
        for index, header in enumerate(headers)
    ]
    return {
        "tag": "table",
        "page_size": min(max(len(rows), 1), 10),
        "row_height": "low",
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        # ⚠️ **rows 是「对象数组」** —— 键是 columns[].name
        "rows": [
            {f"col_{index}": cell for index, cell in enumerate(row)}
            for row in rows
        ],
    }


def build_daily_metric_card(
    *,
    day: str,
    day_text: str,
    status_rows: list[list[str]],
    pending: list[tuple[str, str]],
    employee_id: str,
    session_id: str = "",
    generation: str = "",
    sheet_url: str = "",
    remark: str = "",
    screenshot_hint: bool = False,
    title_suffix: str = "",
) -> dict[str, Any]:
    """构造给**填报人**的「每日填报」卡片。

    ⚠️ **形态 = 表格（看状态）+ 表单（直接填）**
    ------------------------------------------
    这是 2026-11-23 **真机实测**确认的可行形态：
    ``table`` 与 ``form`` 作为**兄弟元素并列**是飞书允许的。

    ⚠️ **全「—」的表格不显示**（2026-11-23 实机结论后调整）
    ------------------------------------------------------
    一天都没填时，表格三行全是「— / 待填」，
    跟下面表单的三个输入框**说的是同一句话** ⇒ 重复且占高。
    ⇒ 这种情况下改成**一行文字**（"今天还没填：普通视频、OOTD、智能体"）。
    ⇒ 填过之后表格才有信息量，照常显示。

    ⚠️ **表格里放不了输入框**（2026-11-23 真机实测）
    ------------------------------------------------
    「表格即表单」试过，行不通：
    - 表格单元格里放 ``input`` → 接口接受，但**渲染成**
      ``map[element_id:... input_type:text…]``（Go 模板把字典当字符串打印）
    - ``form`` 里放 ``table`` → ``type of element is not supported tag: table``
    - 表格列 ``data_type: "input"`` → ``column idx:2``

    ⇒ 输入框**只能**在 ``form`` 里 ⇒ 「表格即表单」在飞书卡片上做不到。

    ⚠️ **待填指标为空时不给表单**
    ⇒ 都填完了还发一个空表单 = 骚扰。
    ⇒ 这种情况只给表格 + 一句「今天已填完」。

    ⚠️ **2026-11-23 角色倒转 —— 这张卡给「剪辑本人」**
    -------------------------------------------------
    剪辑填**数量**：普通视频 / OOTD / 智能体 各多少条，
    外加**备注框（选填）**。完播率 / 视频留存率**由主管填**。

    ⚠️ **截图不在表单里 —— 飞书不支持**（2026-11-23 真机实测）
    ------------------------------------------------------
    ``input_type="file"`` → ``invalid input type``；
    ``file_input`` / ``file_upload`` tag → ``not support tag``。
    ⇒ 截图只能**另外发到聊天里** ⇒ 卡片里给出明确指引。
    """
    elements: list[dict[str, Any]] = [
        _markdown(f"**{day_text} 剪辑数据填报**\n请填下面的条数。"),
    ]

    # ⚠️⚠️ **全「—」的表格不显示**（2026-11-23 实机结论后调整）
    # ------------------------------------------------------
    # ⚠️ **为什么** —— 一天都没填时，表格三行全是「— / 待填」，
    #    跟下面表单里的三个输入框**说的是同一句话**
    #    ⇒ 等于同一件事说两遍，卡片白白高了一截。
    #
    # ⚠️ **只隐藏「一个都没填」的情况** —— 填过之后表格才有信息量
    #    （「普通视频 8 条 已填」是剪辑要看的状态）。
    #
    # ⚠️ **不是「删掉表格列」** —— 已填的情况照常显示完整表格。
    has_any_filled = any(
        len(row) > 1 and row[1] not in ("", "—", "-", "待填")
        for row in status_rows
    )
    if status_rows and has_any_filled:
        elements.append(
            _daily_table_element(["指标", "当前值", "状态"], status_rows)
        )
    elif status_rows:
        # ⚠️ **没填过就用一行文字说清楚要填什么** —— 比一张空表紧凑
        pending_labels = "、".join(label for _, label in pending) if pending else ""
        if pending_labels:
            elements.append(_markdown(f"今天还没填：{pending_labels}"))

    if not pending:
        elements.append(_markdown("✅ **今天已填完**，不用再填了。"))
        return build_card(
            title="每日填报 · 已完成",
            elements=elements,
            header_template="green",
        )

    # ⚠️ **表单里只放「还没填」的指标** —— 已填的不该再问一遍。
    form_elements: list[dict[str, Any]] = []
    for code, label in pending:
        form_elements.append(
            {
                "tag": "input",
                "element_id": f"metric_{code}",
                "name": code,
                # ⚠️ **数字也用 ``text``** —— 飞书只有 text/multiline_text/password
                "input_type": "text",
                "label": _text(f"{label} · 必填"),
                "label_position": "top",
                "placeholder": _text(_metric_placeholder(code)),
                "default_value": "",
                "required": True,
                "max_length": 32,
            }
        )

    # ⚠️ **备注框**（2026-11-23）—— **选填**。
    #    ⚠️ 用 ``multiline_text`` —— 飞书只有 text/multiline_text/password 三种。
    #    ⚠️ **不填也能提交** —— 备注是补充说明，不是必填项。
    form_elements.append(
        {
            "tag": "input",
            "element_id": "remark",
            "name": "remark",
            "input_type": "multiline_text",
            "label": _text("备注（选填）"),
            "label_position": "top",
            "placeholder": _text("有什么要说明的？可不填"),
            "default_value": remark,
            "required": False,
            "max_length": 500,
        }
    )

    form_elements.append(
        _button(
            "提交今日数据",
            ACTION_SUBMIT_DAILY_METRIC,
            {
                "employee_id": employee_id,
                "metric_date": day,
                "session_id": session_id,
                "generation": generation,
            },
            name="daily_metric_submit_btn",
            form_action_type="submit",
        )
    )
    form_elements[-1]["width"] = "fill"

    elements.append({"tag": "form", "name": "daily_metric_form", "elements": form_elements})

    if screenshot_hint:
        # ⚠️ **截图必须走「另外发到聊天」这条通路** ——
        #    飞书卡片里放不了文件上传控件（2026-11-23 真机实测）。
        elements.append(
            _markdown(
                "📎 **截图 / 图片请另外发到本对话** —— "
                "卡片里不支持上传文件，发过来会自动存成当天的证据。"
            )
        )

    if sheet_url:
        elements.append(
            _markdown(f"也可以在[飞书表格]({sheet_url})里填 —— 系统会自动读取。")
        )

    return build_card(
        title=f"每日填报 · {day}{title_suffix}",
        elements=elements,
        header_template="orange",
    )


def _metric_placeholder(code: str) -> str:
    """指标码 → 输入框提示。

    ⚠️ **数量类与比率类提示不同** —— 数量填「8」，比率填「35%」。
    """
    from .daily_metric_specs import ALL_METRICS

    spec = ALL_METRICS.get(code)
    if spec is not None:
        return spec.placeholder
    return "例如：35%"


def build_daily_metric_saved_card(
    *,
    day_text: str,
    saved: list[tuple[str, float]],
    remark: str = "",
    kind: str = "count",
) -> dict[str, Any]:
    """构造「今日数据已提交」回执卡片。

    ⚠️ **数量与比率的显示不同** —— 数量是「8 条」，比率是「35.0%」。
    """
    if kind == "count":
        lines = "\n".join(f"· {label}：{value:.0f} 条" for label, value in saved)
    else:
        lines = "\n".join(f"· {label}：{_percent_text(value)}" for label, value in saved)
    remark_line = f"\n备注：{remark}" if remark else ""
    return build_card(
        title="今日数据已提交",
        elements=[_div(f"{day_text}\n{lines}{remark_line}\n\n已记录，主管可随时核对。")],
        header_template="green",
    )


def build_daily_metric_pending_card(
    *,
    day_text: str,
    pending: list[tuple[str, str]],
) -> dict[str, Any]:
    """构造「还差哪些指标」的提示卡（提交不完整时用）。"""
    lines = "\n".join(f"· {label}" for _, label in pending)
    return build_card(
        title="还没填完",
        elements=[_div(f"{day_text} 还差这些：\n{lines}")],
        header_template="orange",
    )


def build_leader_daily_status_card(
    *,
    day: str,
    day_text: str,
    rows: list[list[str]],
    all_done: bool,
    pending: list[tuple[str, str]] | None = None,
    targets: list[tuple[str, str]] | None = None,
    employee_id: str = "",
    session_id: str = "",
    generation: str = "",
) -> dict[str, Any]:
    """构造给**主管**的卡片。

    ⚠️⚠️ **2026-11-23 角色倒转 —— 主管卡现在「有表单」**
    -------------------------------------------------------
    之前主管卡是**纯只读**的（只有表格），因为当时是剪辑自己填比率。

    **现在业务确认**：剪辑只填**数量**（普通视频 / OOTD / 智能体），
    **完播率 / 视频留存率由主管填** ⇒
    ⇒ 主管卡必须有表单，否则比率这一栏**永远没人填**。

    ⚠️ **但主管填的是「比率」，不是替下属填数量」
    ---------------------------------------------------
    表格里展示的是**每个剪辑当天填了多少条**（只读）；
    表单里主管填的是**效果指标**（完播率 / 留存率）——
    ⚠️ **一条比率对所有剪辑生效吗？** 否 ⇒ 主管要**先选是哪位剪辑**。

    ⇒ 因此表单第一个控件是**「填给谁」的选择**（下拉）。
    ⚠️ 飞书表单里**没有下拉控件**（只有 text / multiline_text / password）⇒
    ⇒ 用一个文本框填**工号/姓名**，后端解析并校验。

    ⚠️ **没有 targets 时不给表单** —— 不知道填给谁就不该填。
    """
    elements: list[dict[str, Any]] = [
        _markdown(
            f"**{day_text} 剪辑数据填报情况**\n"
            "剪辑填的是**条数**；下面的**完播率 / 视频留存率由你来填**。"
        ),
    ]
    if rows:
        elements.append(_daily_table_element(["剪辑", "已填", "待填"], rows))

    if all_done and not pending:
        elements.append(_markdown("✅ **条数全部已填**。"))
        header = "green"
    else:
        elements.append(_markdown("⚠️ 尚未填完的，日报里会标出来。"))
        header = "orange"

    # ⚠️ **主管的表单** —— 填效果指标（比率）。
    if pending and targets:
        form_elements: list[dict[str, Any]] = [
            {
                "tag": "input",
                "element_id": "target_employee",
                "name": "target_employee",
                "input_type": "text",
                "label": _text("填给哪位剪辑 · 必填"),
                "label_position": "top",
                "placeholder": _text("填姓名或工号，例如：李婵雨"),
                "default_value": "",
                "required": True,
                "max_length": 64,
            },
        ]
        for code, label in pending:
            form_elements.append(
                {
                    "tag": "input",
                    "element_id": f"metric_{code}",
                    "name": code,
                    "input_type": "text",
                    "label": _text(f"{label} · 必填"),
                    "label_position": "top",
                    "placeholder": _text(_metric_placeholder(code)),
                    "default_value": "",
                    "required": True,
                    "max_length": 32,
                }
            )
        form_elements.append(
            _button(
                "提交效果指标",
                ACTION_SUBMIT_LEADER_METRIC,
                {
                    "employee_id": employee_id,
                    "metric_date": day,
                    "session_id": session_id,
                    "generation": generation,
                },
                name="leader_metric_submit_btn",
                form_action_type="submit",
            )
        )
        form_elements[-1]["width"] = "fill"
        elements.append(
            {"tag": "form", "name": "leader_metric_form", "elements": form_elements}
        )

    return build_card(title=f"剪辑填报汇总 · {day}", elements=elements, header_template=header)


__all__ = [
    "ACTION_CANCEL",
    "ACTION_CONFIRM",
    "ACTION_FILL_MONTH_PLAN",
    "ACTION_SELECT_SUBJECT",
    "ACTION_SELECT_TASK",
    "ACTION_SUBMIT_DAILY_METRIC",
    "ACTION_SUBMIT_LEADER_METRIC",
    "ACTION_SUBMIT_DRAFT",
    "CARD_SCHEMA_VERSION",
    "build_card",
    "build_daily_metric_card",
    "build_daily_metric_pending_card",
    "build_daily_metric_saved_card",
    "build_draft_saved_card",
    "build_error_card",
    "build_leader_daily_status_card",
    "build_month_plan_form_card",
    "build_prompt_card",
    "build_qa_ai_card",
    "build_qa_denied_card",
    "build_qa_write_denied_card",
    "build_qa_subject_picker_card",
    "build_qa_task_picker_card",
]
