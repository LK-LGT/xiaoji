"""飞书卡片构造器测试（FS-3，约束 C6）。

为什么这类测试值得写
------------------
卡片 JSON 2.0 的结构错一个就是**空白卡片** —— 飞书**不报错**，
只显示一片空白。所以结构断言必须是**具体的**（断到叶子节点），
不能只断「是个 dict」。

本文件覆盖：
1. **骨架完整性** —— schema / config / header / body 的层级与字段名。
2. **最容易写错的几个点** —— header.title 必须是对象、
   body.elements 必须是数组（不是 element）、schema 是字符串 "2.0"。
3. **表单卡片** —— form 里的 input 元素、必填项、按钮回传值。
4. **回执卡片** —— **绝不能说「已提交」**（约束 C4）。
5. **构造器是唯一出口** —— 业务代码不自己拼卡片 dict。
"""

from __future__ import annotations

import json

from backend.app.services import feishu_card

# ---------------------------------------------------------------------------
# 骨架
# ---------------------------------------------------------------------------


def test_card_has_json2_skeleton():
    """卡片必须是 JSON 2.0 的四段式结构。"""
    card = feishu_card.build_card(title="测试", elements=[])

    assert card["schema"] == "2.0"
    assert "config" in card
    assert "header" in card
    assert "body" in card
    assert "elements" in card["body"]


def test_schema_is_string_not_number():
    """⚠️ schema 必须是字符串 "2.0" —— 写成数字 2.0 卡片不渲染。"""
    card = feishu_card.build_card(title="测试", elements=[])

    assert isinstance(card["schema"], str)
    assert card["schema"] == "2.0"
    # 显式反证：不是数字
    assert not isinstance(card["schema"], (int, float))


def test_header_title_is_object_not_string():
    """⚠️ header.title 必须是 {"tag": "plain_text", "content": ...} 对象。

    写成裸字符串 → 整个卡片不渲染。
    """
    card = feishu_card.build_card(title="标题", elements=[])

    title = card["header"]["title"]
    assert isinstance(title, dict), "header.title 必须是对象，不是裸字符串"
    assert title["tag"] == "plain_text"
    assert title["content"] == "标题"
    assert "template" in card["header"]


def test_body_elements_is_array():
    """⚠️ body.elements 必须是数组 —— 写成 body.element（少个 s）→ 空白卡片。"""
    card = feishu_card.build_card(title="测试", elements=[{"tag": "markdown", "content": "x"}])

    assert isinstance(card["body"]["elements"], list)
    assert len(card["body"]["elements"]) == 1
    assert "element" not in card["body"]


def test_card_is_json_serializable():
    """卡片最终要序列化成字符串发给飞书 —— 必须能序列化。"""
    card = feishu_card.build_month_plan_form_card(
        plan_month="2026-11",
        session_id="S_1",
    )

    text = json.dumps(card, ensure_ascii=False)
    assert json.loads(text)["schema"] == "2.0"


# ---------------------------------------------------------------------------
# 表单卡片
# ---------------------------------------------------------------------------


def test_month_plan_form_card_contains_form_with_inputs():
    """填表卡片必须有 form，且 form 里是 input 元素。"""
    card = feishu_card.build_month_plan_form_card(
        plan_month="2026-11",
        session_id="S_1",
    )

    forms = [e for e in card["body"]["elements"] if e.get("tag") == "form"]
    assert len(forms) == 1, "必须有且只有一个 form 元素"

    tags = {e.get("tag") for e in forms[0]["elements"]}
    assert tags == {"input"}, "form 内只允许表单控件"


def test_month_plan_form_card_has_required_task_name():
    """任务名称必填 —— 少了 required，服务端还得再兜一层。"""
    card = feishu_card.build_month_plan_form_card(
        plan_month="2026-11",
        session_id="S_1",
    )

    form = next(e for e in card["body"]["elements"] if e.get("tag") == "form")
    by_id = {e["element_id"]: e for e in form["elements"]}

    assert "task_name" in by_id
    assert by_id["task_name"]["required"] is True
    assert by_id["task_name"]["name"] == "task_name"


def test_month_plan_form_card_prefills_existing_values():
    """续填场景 —— 已填的值要回填到表单里。"""
    card = feishu_card.build_month_plan_form_card(
        plan_month="2026-11",
        session_id="S_1",
        fields={"task_name": "写周报", "performance_weight": 30},
    )

    form = next(e for e in card["body"]["elements"] if e.get("tag") == "form")
    by_id = {e["element_id"]: e for e in form["elements"]}

    assert by_id["task_name"]["default_value"] == "写周报"
    assert by_id["performance_weight"]["default_value"] == "30"


def test_month_plan_form_card_buttons_carry_session_id():
    """按钮的 value 里必须带 session_id —— 回调靠它定位会话。"""
    card = feishu_card.build_month_plan_form_card(
        plan_month="2026-11",
        session_id="S_123",
    )

    buttons = [e for e in card["body"]["elements"] if e.get("tag") == "button"]
    assert buttons, "必须有按钮"

    for button in buttons:
        assert button["value"]["session_id"] == "S_123"
        assert "action" in button["value"]

    actions = {b["value"]["action"] for b in buttons}
    assert feishu_card.ACTION_SUBMIT_DRAFT in actions
    assert feishu_card.ACTION_CANCEL in actions


def test_button_text_is_plain_text_object():
    """按钮文字同样是对象 —— 写成裸字符串会渲染异常。"""
    card = feishu_card.build_month_plan_form_card(
        plan_month="2026-11",
        session_id="S_1",
    )

    button = next(e for e in card["body"]["elements"] if e.get("tag") == "button")
    assert isinstance(button["text"], dict)
    assert button["text"]["tag"] == "plain_text"
    assert button["text"]["content"]


# ---------------------------------------------------------------------------
# 回执卡片（约束 C4）
# ---------------------------------------------------------------------------


def test_draft_saved_card_says_saved_not_submitted():
    """⚠️ 回执**绝不能说「已提交」** —— 机器人只写草稿（C4）。

    说「已提交」会让员工以为审批已经走完。
    """
    card = feishu_card.build_draft_saved_card(
        plan_month="2026-11",
        plan_id="P_1",
        item_count=2,
    )

    text = json.dumps(card, ensure_ascii=False)
    assert "已保存" in text
    assert "已提交" not in text
    assert "草稿" in text


def test_draft_saved_card_has_submit_button_with_url():
    """「去提交」是链接按钮 —— 带 url 时不触发回调。"""
    card = feishu_card.build_draft_saved_card(
        plan_month="2026-11",
        plan_id="P_1",
        item_count=1,
        submit_url="/planning/month/P_1",
    )

    buttons = [e for e in card["body"]["elements"] if e.get("tag") == "button"]
    assert len(buttons) == 1
    assert buttons[0]["url"] == "/planning/month/P_1"
    assert buttons[0]["value"]["plan_id"] == "P_1"


def test_draft_saved_card_without_url_has_no_button():
    """没有链接时不生成空按钮 —— 空 url 按钮点了没反应。"""
    card = feishu_card.build_draft_saved_card(
        plan_month="2026-11",
        plan_id="P_1",
        item_count=1,
    )

    buttons = [e for e in card["body"]["elements"] if e.get("tag") == "button"]
    assert buttons == []


# ---------------------------------------------------------------------------
# 其他卡片
# ---------------------------------------------------------------------------


def test_error_card_has_message():
    """错误卡片要把错误信息显示出来。"""
    card = feishu_card.build_error_card("出错了")

    text = json.dumps(card, ensure_ascii=False)
    assert "出错了" in text


def test_prompt_card_carries_plan_month():
    """引导卡片要带月份与会话。"""
    card = feishu_card.build_prompt_card(
        session_id="S_9",
        plan_month="2026-12",
    )

    button = next(e for e in card["body"]["elements"] if e.get("tag") == "button")
    assert button["value"]["session_id"] == "S_9"
    assert button["value"]["plan_month"] == "2026-12"


# ---------------------------------------------------------------------------
# 护栏：构造器是唯一出口
# ---------------------------------------------------------------------------


def test_no_module_outside_card_builder_builds_cards_directly():
    """⚠️ 业务代码**不许**自己拼卡片 dict —— 必须走构造器。

    卡片结构错一个就是空白卡片，散落到各处就没人改得动。

    ⚠️ 匹配要**精确**：飞书**事件**信封也有 ``"schema": "2.0"``，
    那不是卡片 —— 用「schema 附近必须有 body/header 卡片特征」来区分，
    否则会误伤事件解析代码。
    """
    import re
    from pathlib import Path

    services_dir = Path(__file__).resolve().parents[1] / "services"
    routes_dir = Path(__file__).resolve().parents[1] / "routes"

    # 卡片骨架特征：schema 与 body/elements 出现在同一个字面量里
    card_pattern = re.compile(
        r"[\"']schema[\"']\s*:\s*[\"']2\.0[\"'][\s\S]{0,400}?[\"']body[\"']\s*:",
    )

    offenders: list[str] = []
    for path in list(services_dir.glob("*.py")) + list(routes_dir.glob("*.py")):
        if path.name in {"feishu_card.py", "feishu_bot.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if card_pattern.search(text):
            offenders.append(f"{path.name}: 直接拼装了卡片结构")

    assert offenders == [], (
        "卡片结构必须统一走 services/feishu_card.py 构造器，"
        f"发现绕过构造器的位置：{offenders}"
    )


def test_card_builder_guard_is_not_vacuous():
    """⚠️ 护栏自身的非空转验证（这是本文件最容易出的事故）。

    上面的护栏第一版是**空转**的：它匹配 ``"schema": "2.0"``，
    结果把**事件**解析代码（``feishu_event.py``）也判成违规 ——
    规则太粗，真实违规反而可能漏。

    这里用一段**确实绕过构造器**的代码验证它能抓到。
    """
    import re

    card_pattern = re.compile(
        r"[\"']schema[\"']\s*:\s*[\"']2\.0[\"'][\s\S]{0,400}?[\"']body[\"']\s*:",
    )

    bad = """
    card = {
        "schema": "2.0",
        "body": {"elements": []},
    }
    """
    assert card_pattern.search(bad), "护栏抓不到真正绕过构造器的代码"

    # 事件信封（合法）不应被误伤
    good = """
    if body.get("schema") == "2.0":
        header = body.get("header")
    """
    assert not card_pattern.search(good), "护栏误伤了事件信封解析"
