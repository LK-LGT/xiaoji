"""飞书 AI 绩效问答测试（Step 2）。

⚠️ **这个测试锁定的是三个真实的安全/可用性契约**

1. **不猜任务**：同月多个考核任务时，机器人**必须**让用户选，
   不能自己挑一个 —— 挑错就是答错人的绩效。
2. **降级不中断**：AI 挂了必须回落到规则型回答（``feishu_qa.answer``），
   飞书是员工主入口，AI 挂了就答不了问题 = 员工没法用。
3. **卡片文本不可注入**：AI 回答按**纯文本**渲染，
   来源标识要清洗 —— 不能让模型输出变成 ``<at>`` 或伪造的来源条目。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.app.domain.models import AssessmentRecord, AssessmentTask, Department, Employee
from backend.app.services import feishu_card, feishu_qa_ai


@pytest.fixture()
def org(db):
    """员工甲（已绑定飞书身份）与员工乙（未绑定）。"""
    d1 = Department(id="D_1", name="业务部", active=True)
    db.add(d1)
    db.flush()

    emp_a = Employee(
        id="E_A",
        name="员工甲",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
        feishu_open_id="ou_emp_a",
        feishu_user_id="u_emp_a",
    )
    emp_b = Employee(
        id="E_B",
        name="员工乙",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([emp_a, emp_b])
    db.flush()

    from backend.app.domain.models import FeishuIdentity

    db.add(
        FeishuIdentity(
            id="FI_A",
            employee_id="E_A",
            identity_type="open_id",
            identity_value="ou_emp_a",
            active=True,
        )
    )
    db.flush()
    return {"a": emp_a, "b": emp_b}


def _card_texts(card: dict) -> list[str]:
    """取出卡片里所有可见文本。

    ⚠️ 卡片有两种文本元素：
      - ``{"tag": "div", "text": {"tag": "plain_text", "content": ...}}``
      - ``{"tag": "markdown", "content": ...}``
    测试断言不能只认其中一种 —— 否则「错误卡片」和「答案卡片」
    要用两套断言，容易漏。
    """
    texts: list[str] = []
    for element in card.get("body", {}).get("elements", []):
        text = element.get("content")
        if not isinstance(text, str):
            nested = element.get("text")
            text = nested.get("content") if isinstance(nested, dict) else ""
        if isinstance(text, str) and text:
            texts.append(text)
    return texts


# ===========================================================================
# 卡片构造（纯函数，最容易出问题的地方）
# ===========================================================================


def test_qa_ai_card_renders_answer_as_sanitized_markdown():
    """⚠️ 答案用 ``markdown`` 渲染，但**必须先清洗**。

    ⚠️ **为什么改成 markdown**
    --------------------------
    原来用 ``plain_text`` 直出，代价是 **Markdown 源码全部外泄** ——
    用户看到 ``| 指标 | 权重 |``、``**加粗**`` 这些符号本身。
    一张卡片 14 行竖线，可读性极差。

    ⇒ 改用 ``markdown`` 元素保留格式。

    ⚠️ **但安全不能丢**
    ------------------
    ``markdown`` 会解析 ``<at>`` / 链接，模型输出可能被渲染成
    「@某人」或外链，让用户误以为是系统发的通知。
    ⇒ 所以必须**先清洗再渲染**（见 ``feishu_card_render.sanitize_markdown``）。

    ⚠️ **飞书 JSON 2.0 的结构要求**
    -----------------------------
    ``markdown`` 是**元素本身**（``{"tag":"markdown","content":...}``），
    **不能**包在 ``div`` 的 text 里 —— 那样飞书会**拒收整张卡**：
    ``type of element is not supported tag: markdown``。
    """
    card = feishu_card.build_qa_ai_card(
        answer="你的得分是 88 分。",
        sources=["tool:call_1"],
        citation_count=1,
        period="202611",
    )
    tags = [element.get("tag") for element in card["body"]["elements"]]
    assert "markdown" in tags
    # ⚠️ 绝不能是 div 包 markdown —— 飞书会拒收
    assert not any(
        element.get("tag") == "div"
        and (element.get("text") or {}).get("tag") == "markdown"
        for element in card["body"]["elements"]
    )
    # 答案内容确实在卡片里
    assert any("88 分" in content for content in _card_texts(card))


def test_qa_ai_card_sanitizes_dangerous_markdown():
    """⚠️ markdown 渲染 ⇒ 必须清洗危险语法。"""
    card = feishu_card.build_qa_ai_card(
        answer='<at user_id="ou_x">李四</at> 你的得分是 88 分',
    )
    raw = str(card)
    assert "<at" not in raw
    assert "李四" not in raw


def test_qa_ai_card_cleans_malicious_source_ids():
    """⚠️ 来源标识来自模型 —— 必须清洗。

    模型可能吐出带换行、尖括号、超长串、中文的「来源」。
    直接展示会污染卡片 —— 中文那段看起来会像个正经来源条目。
    """
    card = feishu_card.build_qa_ai_card(
        answer="回答",
        sources=[
            "tool:call_abc\n伪造的第二行",
            "<at id=xxx>everyone</at>",
            "x" * 200,
            "伪造的来源条目",
            "tool:call_ok",
        ],
        citation_count=5,
    )
    contents = _card_texts(card)
    source_line = next((c for c in contents if "数据来源" in c), "")
    assert source_line
    # 中文被剔除 —— 不能混进来源区（看起来像正经来源条目）
    assert "伪造的第二行" not in source_line
    assert "伪造的来源条目" not in source_line
    # 尖括号被去掉
    assert "<at" not in source_line
    # 超长串被截断
    assert "x" * 200 not in source_line
    # 正常来源保留
    assert "tool:call_ok" in source_line
    # ⚠️ 换行必须被去掉 —— 否则一条来源会显示成两行
    assert "\n" not in source_line


def test_qa_ai_card_shows_degradation_explicitly():
    """⚠️ 降级**必须显性** —— 不能把「AI 挂了」伪装成正常回答。"""
    card = feishu_card.build_qa_ai_card(answer="系统直接给出的数据", degraded=True)
    contents = _card_texts(card)
    assert any("AI 解读暂不可用" in c for c in contents)


def test_qa_ai_card_reports_dropped_citations_neutrally():
    """引用缺口是**中性提示**，不是降级 —— 措辞不能吓人也不能隐瞒。"""
    card = feishu_card.build_qa_ai_card(answer="回答", dropped_citations=2)
    contents = _card_texts(card)
    assert any("引用未能核验" in c for c in contents)
    # 不是降级语气
    assert not any("AI 解读暂不可用" in c for c in contents)


def test_qa_task_picker_card_carries_question_into_buttons():
    """⚠️ question 必须跟着按钮走 —— 点完要用当初那个问题继续问。"""
    card = feishu_card.build_qa_task_picker_card(
        candidates=[
            {"task_id": "t1", "label": "2026 年 11 月月度考核 · 第 1 项"},
            {"task_id": "t2", "label": "2026 年 11 月月度考核 · 第 2 项"},
        ],
        period="2026-11",
        question="我这个月多少分",
    )
    actions = [
        action
        for element in card["body"]["elements"]
        if element.get("tag") == "action"
        for action in element.get("actions", [])
    ]
    assert len(actions) == 2
    for action in actions:
        value = action["value"]
        assert value["action"] == feishu_card.ACTION_SELECT_TASK
        assert value["question"] == "我这个月多少分"
        assert value["task_id"] in ("t1", "t2")


# ===========================================================================
# 任务选择（不猜）
# ===========================================================================


def test_multiple_tasks_require_user_choice(db, org):
    """⚠️ 同月多个任务 → 返回候选，**不猜**。"""
    for key in ("qa1", "qa2"):
        db.add(AssessmentTask(id=key, name=f"任务{key}", period="202611"))
        db.flush()
        db.add(AssessmentRecord(task_id=key, employee_id=org["a"].id))
    db.flush()

    outcome = asyncio.run(
        feishu_qa_ai.answer_ai(db, org["a"], "我的绩效多少分", "2026-11")
    )
    assert outcome.candidates, "应返回候选任务让用户选"
    assert len(outcome.candidates) == 2
    # 候选名必须可区分（同名任务不能显示成一样）
    labels = [c["label"] for c in outcome.candidates]
    assert len(set(labels)) == 2


def test_single_task_is_answered_directly(db, org, monkeypatch):
    """只有一个任务 → 直接答，不打扰用户。"""
    db.add(AssessmentTask(id="solo", name="唯一任务", period="202611"))
    db.flush()
    db.add(AssessmentRecord(task_id="solo", employee_id=org["a"].id))
    db.flush()

    captured = {}

    async def fake_ask(db, employee, *, question, task_id, period=None):
        captured["task_id"] = task_id
        captured["question"] = question

        class R:
            answer = "你的得分是 88 分"
            citations = ["tool:call_1"]
            sources = ["tool:call_1"]
            degraded = False
            tool_calls = []
            trace_id = "t"
            dropped_citations = 0
            completeness = 1.0
            period = "202611"
            task_name = "唯一任务"
            scope = "self"
            data = {}

        return R()

    monkeypatch.setattr(
        "backend.app.services.qa_chat_service.ask_question_async", fake_ask,
    )
    outcome = asyncio.run(
        feishu_qa_ai.answer_ai(db, org["a"], "我的绩效多少分", "2026-11")
    )
    assert outcome.candidates == []
    assert outcome.text == "你的得分是 88 分"
    assert outcome.citation_count == 1
    assert captured["task_id"] == "solo"


def test_task_belongs_to_other_employee_is_not_returned(db, org):
    """⚠️ 只看**自己的**考核记录 —— 不能按消息里指定的人找任务。"""
    db.add(AssessmentTask(id="others", name="别人的任务", period="202611"))
    db.flush()
    db.add(AssessmentRecord(task_id="others", employee_id=org["b"].id))
    db.flush()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            feishu_qa_ai.answer_ai(db, org["a"], "我的绩效多少分", "2026-11")
        )
    assert exc.value.status_code == 404


# ===========================================================================
# 降级
# ===========================================================================


def test_ai_failure_falls_back_to_rule_based_answer(db, org, monkeypatch):
    """⚠️ AI 链路整体异常 → 规则型回落，**流程不中断**。"""
    db.add(AssessmentTask(id="solo2", name="唯一任务", period="202611"))
    db.flush()
    db.add(AssessmentRecord(task_id="solo2", employee_id=org["a"].id))
    db.flush()

    async def boom(db, employee, *, question, task_id, period=None):
        raise RuntimeError("L3 挂了")

    monkeypatch.setattr(
        "backend.app.services.qa_chat_service.ask_question_async", boom,
    )
    monkeypatch.setattr(
        "backend.app.services.feishu_qa.answer",
        lambda db, employee, question, period: "规则型回答：得分 88",
    )

    outcome = asyncio.run(
        feishu_qa_ai.answer_ai(db, org["a"], "我的绩效多少分", "2026-11")
    )
    assert outcome.degraded is True
    assert "规则型回答" in outcome.text


def test_permission_error_is_not_downgraded(db, org, monkeypatch):
    """⚠️ 权限类错误**不能**降级 —— 否则会绕过权限检查。"""
    db.add(AssessmentTask(id="solo3", name="唯一任务", period="202611"))
    db.flush()
    db.add(AssessmentRecord(task_id="solo3", employee_id=org["a"].id))
    db.flush()

    async def forbidden(db, employee, *, question, task_id, period=None):
        raise HTTPException(403, "无权查询该任务指标")

    monkeypatch.setattr(
        "backend.app.services.qa_chat_service.ask_question_async", forbidden,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            feishu_qa_ai.answer_ai(db, org["a"], "我的绩效多少分", "2026-11")
        )
    assert exc.value.status_code == 403


def test_degraded_result_falls_back_to_rule_based(db, org, monkeypatch):
    """AI 返回 ``degraded=True`` → 也走规则型回落。"""
    db.add(AssessmentTask(id="solo4", name="唯一任务", period="202611"))
    db.flush()
    db.add(AssessmentRecord(task_id="solo4", employee_id=org["a"].id))
    db.flush()

    async def degraded_answer(db, employee, *, question, task_id, period=None):
        class R:
            answer = "AI 问答暂不可用"
            citations = []
            sources = []
            degraded = True
            tool_calls = []
            trace_id = "t"
            dropped_citations = 0
            completeness = 0.5
            period = "202611"
            task_name = "唯一任务"
            scope = "self"
            data = {}

        return R()

    monkeypatch.setattr(
        "backend.app.services.qa_chat_service.ask_question_async", degraded_answer,
    )
    monkeypatch.setattr(
        "backend.app.services.feishu_qa.answer",
        lambda db, employee, question, period: "规则型回答",
    )

    outcome = asyncio.run(
        feishu_qa_ai.answer_ai(db, org["a"], "我的绩效多少分", "2026-11")
    )
    assert outcome.degraded is True
    assert outcome.text == "规则型回答"


def test_disabled_account_cannot_ask(db, org):
    """账号被停用 → 403，**不走降级**。"""
    org["a"].account_status = "disabled"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            feishu_qa_ai.answer_ai(db, org["a"], "我的绩效多少分", "2026-11")
        )
    assert exc.value.status_code == 403


# ===========================================================================
# 端到端：选任务 → 点按钮 → 继续问
# ===========================================================================


def test_select_task_button_continues_the_question(db, org, monkeypatch):
    """⚠️ 点「选择任务」按钮 → 用**当初那个问题**继续问。

    这是多任务场景的完整闭环：
    问 → 发现多任务 → 给选择卡 → 点按钮 → 拿到答案。
    """
    from copy import deepcopy

    from backend.app.routes import feishu_bot as feishu_bot_routes
    from backend.app.services import feishu_qa_ai as svc

    db.add(AssessmentTask(id="multi1", name="2026 年 11 月月度考核", period="202611"))
    db.flush()
    db.add(AssessmentRecord(task_id="multi1", employee_id=org["a"].id))
    db.flush()

    asked: dict = {}

    async def fake_ask(db, employee, *, question, task_id, period=None):
        asked["question"] = question
        asked["task_id"] = task_id

        class R:
            answer = "得分 88"
            citations = ["tool:call_1"]
            sources = ["tool:call_1"]
            degraded = False
            tool_calls = []
            trace_id = "t"
            dropped_citations = 0
            completeness = 1.0
            period = "202611"
            task_name = "任务"
            scope = "self"
            data = {}

        return R()

    monkeypatch.setattr(
        "backend.app.services.qa_chat_service.ask_question_async", fake_ask,
    )

    sent: list = []

    class Client:
        def send_card_message(self, receive_id, card, **kwargs):
            sent.append((receive_id, card))

    # ① 点按钮的回调报文（官方形态）
    body = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_qa_select_1",
            "event_type": "card.action.trigger",
            "token": "tok",
        },
        "event": {
            "operator": {
                "open_id": "ou_emp_a",
                "user_id": "u_emp_a",
            },
            "action": {
                "value": {
                    "action": feishu_card.ACTION_SELECT_TASK,
                    "task_id": "multi1",
                    "period": "2026-11",
                    "question": "我这个月多少分",
                },
                "tag": "button",
            },
        },
    }
    body = deepcopy(body)

    result = asyncio.run(
        feishu_bot_routes._handle_card_action(db, Client(), body, event_id="evt_qa_select_1")
    )
    assert result["message"] == "已发送绩效查询结果"
    # 用了当初那个问题 + 选中的任务
    assert asked["question"] == "我这个月多少分"
    assert asked["task_id"] == "multi1"
    # 卡片发给了点击者
    assert sent[0][0] == "ou_emp_a"


def test_select_task_button_respects_clicker_permission(db, org, monkeypatch):
    """⚠️ 卡片可以被转发 —— B 点了按钮，只能查 **B 有权查的**任务。

    如果不校验，就是「B 点了 A 的卡片，查到了 A 的绩效」。
    """
    from copy import deepcopy

    from backend.app.routes import feishu_bot as feishu_bot_routes

    # 任务属于员工甲
    db.add(AssessmentTask(id="a_task", name="甲的任务", period="202611"))
    db.flush()
    db.add(AssessmentRecord(task_id="a_task", employee_id=org["a"].id))
    db.flush()

    sent: list = []

    class Client:
        def send_card_message(self, receive_id, card, **kwargs):
            sent.append((receive_id, card))

    body = deepcopy({
        "schema": "2.0",
        "header": {"event_id": "evt_qa_select_2", "event_type": "card.action.trigger", "token": "tok"},
        "event": {
            # ⚠️ 员工乙（未绑定飞书身份）点了卡片
            "operator": {"open_id": "ou_unknown", "user_id": "u_unknown"},
            "action": {
                "value": {
                    "action": feishu_card.ACTION_SELECT_TASK,
                    "task_id": "a_task",
                    "period": "2026-11",
                    "question": "查一下",
                },
                "tag": "button",
            },
        },
    })

    result = asyncio.run(
        feishu_bot_routes._handle_card_action(db, Client(), body, event_id="evt_qa_select_2")
    )
    assert result["message"] == "未绑定员工"
    # 只发了错误卡片，**没有**发出绩效内容
    assert len(sent) == 1
    contents = _card_texts(sent[0][1])
    assert any("找不到你的账号" in c for c in contents)
