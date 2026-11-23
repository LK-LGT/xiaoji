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
        elements.append(_text("（无内容）"))

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
            _text(
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
        elements=[_text(body)],
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


__all__ = [
    "ACTION_CANCEL",
    "ACTION_CONFIRM",
    "ACTION_FILL_MONTH_PLAN",
    "ACTION_SELECT_SUBJECT",
    "ACTION_SELECT_TASK",
    "ACTION_SUBMIT_DRAFT",
    "CARD_SCHEMA_VERSION",
    "build_card",
    "build_draft_saved_card",
    "build_error_card",
    "build_month_plan_form_card",
    "build_prompt_card",
    "build_qa_ai_card",
    "build_qa_denied_card",
    "build_qa_write_denied_card",
    "build_qa_subject_picker_card",
    "build_qa_task_picker_card",
]
