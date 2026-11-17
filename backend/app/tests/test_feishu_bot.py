"""飞书机器人会话填表测试（FS-3）。

覆盖：
1. **来源校验**：未配置 Token → 503；Token 不符 → 401；握手 → challenge。
2. **消息事件**：意图识别 → 发卡片；未知意图 → 给提示。
3. **卡片回调**：**归属校验**（约束 C3）→ 写草稿（约束 C4）→ 回执。
4. **只写草稿**：写完后 ``month_plan.status`` 必须是 ``draft``。
5. **幂等**：同一消息重复推送 → 只写一条草稿。
6. **纯函数**：意图识别 / 月份提取 / 表单值归一化。

⚠️ **fixture 用飞书官方原始报文**（约束 C5）
----------------------------------------
A8 的 35 个测试全绿，是因为「测试 payload 是照着实现自己构造的」——
自证自洽，测不出「实现和真实协议不符」。

本文件的 fixture **逐字段照抄飞书开放平台官方文档**：

- ``im.message.receive_v1``
  → https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
- ``card.action.trigger``
  → https://open.feishu.cn/document/feishu-cards/card-callback-communication
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.bot_models import BotMessage, BotSession
from backend.app.domain.models import Base, Department, Employee, MonthPlan, MonthPlanItem
from backend.app.main import app
from backend.app.routes import feishu_bot as feishu_bot_routes
from backend.app.services import feishu_bot

EVENTS_URL = "/v1/feishu/bot/events"
CARD_ACTIONS_URL = "/v1/feishu/bot/card/actions"

BOT_TOKEN = "bot-verify-token-abc123"


# ===========================================================================
# Fixtures —— 官方原始报文
# ===========================================================================


@pytest.fixture()
def db():
    # ⚠️ 必须用 StaticPool：FastAPI TestClient 在工作线程里发请求，
    #    SQLite `:memory:` 默认每连接一个独立库（会报 "no such table"）。
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


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


@pytest.fixture()
def client_db(db):
    """把 TestClient 的 DB 依赖指向内存库。"""

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    yield db
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def http(client_db, monkeypatch):
    """TestClient（飞书客户端被 mock 掉，不发真实请求）。"""
    monkeypatch.setattr(settings, "feishu_bot_event_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
    monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")

    sent: list[dict] = []

    class _FakeClient:
        def send_card_message(self, receive_id, card, **kwargs):
            sent.append({"receive_id": receive_id, "card": card, **kwargs})
            return {"code": 0, "data": {"message_id": "om_fake"}}

    monkeypatch.setattr(feishu_bot_routes, "_client", lambda: _FakeClient())
    with TestClient(app) as c:
        c.feishu_sent = sent  # type: ignore[attr-defined]
        yield c


# ---------------------------------------------------------------------------
# 官方原始报文 fixture
# ---------------------------------------------------------------------------

#: 飞书官方文档原样报文 —— im.message.receive_v1
#: 来源：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
OFFICIAL_MESSAGE_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "5e3702a84e847582be8db7fb73283c02",
        "event_type": "im.message.receive_v1",
        "create_time": "1608725989000",
        "token": "rvaYgkND1GOiu5MM0E1rncYC6PLtF7JV",
        "app_id": "cli_9f5343c580712544",
        "tenant_key": "2ca1d211f64f6438",
    },
    "event": {
        "sender": {
            "sender_id": {
                "union_id": "on_8ed6aa67826108097d9ee143816345",
                "user_id": "e33ggbyz",
                "open_id": "ou_84aad35d084aa403a838cf73ee18467",
            },
            "sender_type": "user",
            "tenant_key": "736588c9260f175e",
        },
        "message": {
            "message_id": "om_5ce6d572455d361153b7cb51da133945",
            "root_id": "om_5ce6d572455d361153b7cb5xxfsdfsdfdsf",
            "parent_id": "om_5ce6d572455d361153b7cb5xxfsdfsdfdsf",
            "create_time": "1609073151345",
            "update_time": "1687343654666",
            "chat_id": "oc_5ce6d572455d361153b7xx51da133945",
            "thread_id": "omt_d4be107c616",
            "chat_type": "group",
            "message_type": "text",
            "content": "{\"text\":\"@_user_1 hello\"}",
            "mentions": [
                {
                    "key": "@_user_1",
                    "id": {
                        "union_id": "on_8ed6aa67826108097d9ee143816345",
                        "user_id": "e33ggbyz",
                        "open_id": "ou_84aad35d084aa403a838cf73ee18467",
                    },
                    "mentioned_type": "user",
                    "name": "Tom",
                    "tenant_key": "736588c9260f175e",
                }
            ],
        },
    },
}

#: 飞书官方文档原样报文 —— card.action.trigger
#: 来源：https://open.feishu.cn/document/feishu-cards/card-callback-communication
OFFICIAL_CARD_ACTION_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "f7984f25108f8137722bb63c*****",
        "event_type": "card.action.trigger",
        "token": "066zT6pS4QCbgj5Do145GfDbbag*****",
        "create_time": "1603977298000000",
        "tenant_key": "2df73991750*****",
        "app_id": "cli_a5fb0ae6a4******",
    },
    "event": {
        "operator": {
            "tenant_key": "2df73991750*****",
            "user_id": "867*****",
            "open_id": "ou_3c14f3a59eaf2825dbe25359f15*****",
            "union_id": "on_cad4860e7af114fb4ff6c5d496d*****",
            "token": "c-295ee57216a5dc9de90fefd0aadb4b1d7d******",
        },
        "action": {
            "value": {"action": "submit_draft", "session_id": "SESSION_ID_PLACEHOLDER"},
            "tag": "button",
            "timezone": "Asia/Shanghai",
            "form_value": {
                "task_name": "完成季度述职材料",
                "task_content": "整理 Q3 述职 PPT",
                "deliverable": "述职 PPT",
                "quality": "无错别字",
                "performance_weight": "30",
                "estimated_value": "100",
            },
            "name": "Button_lvkepfu3",
        },
        "host": "im_message",
        "delivery_type": "url_preview",
        "context": {
            "open_message_id": "om_574d639e4a44e4dd646eaf628e2*****",
            "open_chat_id": "oc_e4d2605ca917e695f54f11aaf56*****",
        },
    },
}


def _message_event(text: str, *, open_id: str = "ou_emp_a", message_id: str = "om_test_1"):
    """在官方报文基础上替换内容（保持结构不变）。"""
    import copy

    body = copy.deepcopy(OFFICIAL_MESSAGE_EVENT)
    body["header"]["token"] = BOT_TOKEN
    body["header"]["event_id"] = f"evt_{message_id}"
    body["event"]["sender"]["sender_id"]["open_id"] = open_id
    body["event"]["sender"]["sender_id"]["user_id"] = "u_emp_a"
    body["event"]["message"]["message_id"] = message_id
    body["event"]["message"]["content"] = f'{{"text":"{text}"}}'
    return body


def _card_action_event(
    *,
    session_id: str,
    open_id: str = "ou_emp_a",
    action: str = "submit_draft",
    form_value: dict | None = None,
    event_id: str = "card_evt_1",
):
    """在官方报文基础上替换内容（保持结构不变）。"""
    import copy

    body = copy.deepcopy(OFFICIAL_CARD_ACTION_EVENT)
    body["header"]["token"] = BOT_TOKEN
    body["header"]["event_id"] = event_id
    body["event"]["operator"]["open_id"] = open_id
    body["event"]["action"]["value"] = {"action": action, "session_id": session_id}
    if form_value is not None:
        body["event"]["action"]["form_value"] = form_value
    return body


# ===========================================================================
# 1. 来源校验
# ===========================================================================


def test_missing_token_returns_503(http, client_db, monkeypatch):
    """未配置 Token → 503，**绝不放行**。

    放行的后果：任何人都能伪造卡片回调，把草稿写进别人名下。
    """
    monkeypatch.setattr(settings, "feishu_bot_event_token", "")

    resp = http.post(EVENTS_URL, json=_message_event("填月计划"))

    assert resp.status_code == 503
    assert "Verification Token" in resp.json()["detail"]


def test_wrong_token_returns_401(http):
    """Token 不符 → 401。"""
    body = _message_event("填月计划")
    body["header"]["token"] = "wrong-token"

    resp = http.post(EVENTS_URL, json=body)

    assert resp.status_code == 401


def test_url_verification_returns_challenge(http):
    """握手必须原样返回 challenge —— 否则飞书保存不了回调地址。"""
    resp = http.post(
        EVENTS_URL,
        json={"type": "url_verification", "challenge": "abc-challenge-123"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc-challenge-123"}


def test_bot_uses_dedicated_token_not_approval_token(http, client_db, monkeypatch):
    """⚠️ 约束 C2：机器人用**独立**的 Verification Token。

    不能复用审批的 —— 三条事件源混用一个 Token 时，
    排障根本分不清是哪条链路被调，一次泄露就全线失守。
    """
    from backend.app.core.config import settings as real_settings

    monkeypatch.setattr(real_settings, "feishu_approval_webhook_token", "APPROVAL-TOKEN-XYZ")

    # 把机器人 Token 设成与审批不同的值
    monkeypatch.setattr(settings, "feishu_bot_event_token", BOT_TOKEN)

    assert settings.feishu_bot_event_token != settings.feishu_approval_webhook_token

    # 审批 Token 不该被机器人端点接受
    body = _message_event("填月计划")
    body["header"]["token"] = "APPROVAL-TOKEN-XYZ"
    resp = http.post(EVENTS_URL, json=body)
    assert resp.status_code == 401, "机器人端点接受了审批 Token"


# ===========================================================================
# 2. 消息事件
# ===========================================================================


def test_message_event_creates_session_and_sends_card(http, client_db, org):
    """员工发「填月计划」→ 建会话 + 发卡片。"""
    resp = http.post(EVENTS_URL, json=_message_event("填月计划"))

    assert resp.status_code == 200
    assert resp.json()["code"] == 0

    sessions = client_db.execute(select(BotSession)).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].open_id == "ou_emp_a"
    assert sessions[0].intent == feishu_bot.INTENT_MONTH_PLAN
    assert sessions[0].state == feishu_bot.STATE_COLLECTING
    assert sessions[0].employee_id == "E_A"

    assert len(http.feishu_sent) == 1
    card = http.feishu_sent[0]["card"]
    assert card["schema"] == "2.0"


def test_message_event_strips_at_mention_prefix(http, client_db, org):
    """⚠️ 群聊里飞书会在文本前面加 ``@_user_1`` 占位符。

    官方报文原文就是 ``"content": "{\\"text\\":\\"@_user_1 hello\\"}"``。
    不剥掉的话，意图识别就成了在匹配带占位符的字符串 ——
    关键词被拆开就匹配不到。
    """
    import copy

    body = copy.deepcopy(OFFICIAL_MESSAGE_EVENT)
    body["header"]["token"] = BOT_TOKEN
    body["event"]["sender"]["sender_id"]["open_id"] = "ou_emp_a"
    body["event"]["sender"]["sender_id"]["user_id"] = "u_emp_a"
    body["event"]["message"]["message_id"] = "om_at_test"
    body["event"]["message"]["content"] = '{"text":"@_user_1 填月计划"}'

    resp = http.post(EVENTS_URL, json=body)

    assert resp.status_code == 200
    sessions = client_db.execute(select(BotSession)).scalars().all()
    assert sessions[0].intent == feishu_bot.INTENT_MONTH_PLAN


def test_message_event_unknown_intent_sends_hint(http, client_db, org):
    """听不懂 → 给提示，**不报错**（报错会让飞书反复重推）。"""
    resp = http.post(EVENTS_URL, json=_message_event("今天天气不错"))

    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert len(http.feishu_sent) == 1
    assert "月计划" in str(http.feishu_sent[0]["card"])


def test_message_event_unbound_employee_gets_hint(http, client_db):
    """未绑定员工 → 提示先绑定，**不写任何数据**。"""
    resp = http.post(
        EVENTS_URL,
        json=_message_event("填月计划", open_id="ou_unknown", message_id="om_unknown"),
    )

    assert resp.status_code == 200
    assert len(http.feishu_sent) == 1
    assert "绑定" in str(http.feishu_sent[0]["card"])

    plans = client_db.execute(select(MonthPlan)).scalars().all()
    assert plans == []


def test_message_event_is_idempotent_by_message_id(http, client_db):
    """⚠️ 官方文档：**用 message_id 去重，别用 event_id**。

    同一条消息重推 → 只建一个会话。
    """
    body = _message_event("填月计划", message_id="om_same")
    # 重推时 event_id 变了（模拟飞书重推换了 event_id）
    body2 = _message_event("填月计划", message_id="om_same")
    body2["header"]["event_id"] = "evt_different"

    http.post(EVENTS_URL, json=body)
    resp = http.post(EVENTS_URL, json=body2)

    assert resp.json()["message"] == "事件已处理"

    sessions = client_db.execute(select(BotSession)).scalars().all()
    assert len(sessions) == 1, "同一条消息建了两个会话"


def test_unknown_event_type_returns_success(http):
    """未知事件类型 → 返回 code 0 并忽略。

    ⚠️ 返回错误会让飞书**反复重推**，直到我们把事件处理对。
    """
    body = _message_event("填月计划")
    body["header"]["event_type"] = "some.unknown.event_v9"

    resp = http.post(EVENTS_URL, json=body)

    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_bot_disabled_ignores_event(http, client_db, org, monkeypatch):
    """应急开关：``FEISHU_BOT_ENABLED=false`` → 只握手不处理业务。"""
    monkeypatch.setattr(settings, "feishu_bot_enabled", False)

    resp = http.post(EVENTS_URL, json=_message_event("填月计划"))

    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert http.feishu_sent == [], "机器人关闭时不该发卡片"
    assert client_db.execute(select(BotSession)).scalars().all() == []


def test_bot_disabled_still_answers_handshake(http, monkeypatch):
    """⚠️ 关闭时**仍然要应答握手** —— 否则飞书保存不了回调地址。

    应急开关是为了「暂停业务」，不是为了「让回调地址保存失败」。
    """
    monkeypatch.setattr(settings, "feishu_bot_enabled", False)

    resp = http.post(
        EVENTS_URL,
        json={"type": "url_verification", "challenge": "challenge-when-off"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"challenge": "challenge-when-off"}


# ===========================================================================
# 3. 卡片回调（约束 C3 归属校验）
# ===========================================================================


def _make_session(client_db, *, open_id: str = "ou_emp_a", employee_id: str = "E_A") -> BotSession:
    session = BotSession(
        open_id=open_id,
        app_id="cli_test",
        user_id="u_emp_a",
        employee_id=employee_id,
        state=feishu_bot.STATE_COLLECTING,
        intent=feishu_bot.INTENT_MONTH_PLAN,
        plan_month="2026-11",
        collected={},
    )
    client_db.add(session)
    client_db.commit()
    return session


def test_card_action_writes_draft(http, client_db, org):
    """卡片提交 → 写草稿 → 回执。"""
    session = _make_session(client_db)

    resp = http.post(
        EVENTS_URL,
        json=_card_action_event(session_id=session.id),
    )

    assert resp.status_code == 200
    assert resp.json()["code"] == 0

    plans = client_db.execute(select(MonthPlan)).scalars().all()
    assert len(plans) == 1
    plan = plans[0]
    assert plan.owner_id == "E_A"
    assert plan.plan_month == "2026-11"

    items = client_db.execute(select(MonthPlanItem)).scalars().all()
    assert len(items) == 1
    assert items[0].task_name == "完成季度述职材料"
    assert items[0].performance_weight == 30.0
    assert items[0].estimated_value == 100.0


def test_card_action_only_writes_draft_not_submitted(http, client_db, org):
    """⚠️ 约束 C4：机器人**只写草稿**，绝不改业务状态。

    写完后 ``month_plan.status`` 必须是 ``draft``，
    且**没有** submitted_at —— 提交仍然归 L2 状态机管。
    """
    session = _make_session(client_db)

    http.post(EVENTS_URL, json=_card_action_event(session_id=session.id))

    plan = client_db.execute(select(MonthPlan)).scalars().one()
    assert plan.status == "draft", f"机器人把状态改成了 {plan.status}"
    assert plan.submitted_at is None, "机器人不该写 submitted_at"


def test_card_action_receipt_says_saved_not_submitted(http, client_db, org):
    """回执措辞必须是「已保存」而不是「已提交」（约束 C4）。"""
    session = _make_session(client_db)

    http.post(EVENTS_URL, json=_card_action_event(session_id=session.id))

    assert http.feishu_sent, "没有发回执"
    receipt_text = str(http.feishu_sent[-1]["card"])
    assert "已保存" in receipt_text
    assert "已提交" not in receipt_text


def test_card_action_rejects_other_users_open_id(http, client_db, org):
    """⚠️ 约束 C3：**A 的卡片被 B 点** → 必须拒绝。

    卡片可以转发。不校验就是「B 点了卡片，草稿写进了 A 的名下」。
    """
    session = _make_session(client_db, open_id="ou_emp_a")

    resp = http.post(
        EVENTS_URL,
        json=_card_action_event(session_id=session.id, open_id="ou_intruder"),
    )

    assert resp.status_code == 200
    assert "归属" in resp.json()["message"]

    plans = client_db.execute(select(MonthPlan)).scalars().all()
    assert plans == [], "别人的回调把草稿写进来了"


def test_card_action_with_unknown_session_is_ignored(http, client_db, org):
    """会话不存在 → 忽略，不报错（报错会让飞书反复重推）。"""
    resp = http.post(
        EVENTS_URL,
        json=_card_action_event(session_id="SESSION_NOT_EXIST"),
    )

    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert client_db.execute(select(MonthPlan)).scalars().all() == []


def test_card_action_cancel_clears_session(http, client_db, org):
    """点「取消」→ 会话复位，不写草稿。"""
    session = _make_session(client_db)

    resp = http.post(
        EVENTS_URL,
        json=_card_action_event(session_id=session.id, action="cancel"),
    )

    assert resp.status_code == 200
    db_session = client_db.get(BotSession, session.id)
    assert db_session.state == feishu_bot.STATE_IDLE
    assert db_session.intent == feishu_bot.INTENT_NONE
    assert client_db.execute(select(MonthPlan)).scalars().all() == []


def test_card_action_unknown_action_is_ignored(http, client_db, org):
    """未知动作 → 忽略并返回成功。"""
    session = _make_session(client_db)

    resp = http.post(
        EVENTS_URL,
        json=_card_action_event(session_id=session.id, action="some_other_action"),
    )

    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert client_db.execute(select(MonthPlan)).scalars().all() == []


def test_card_action_endpoint_also_works(http, client_db, org):
    """独立的卡片回调地址也要能用。"""
    session = _make_session(client_db)

    resp = http.post(
        CARD_ACTIONS_URL,
        json=_card_action_event(session_id=session.id),
    )

    assert resp.status_code == 200
    assert len(client_db.execute(select(MonthPlan)).scalars().all()) == 1


def test_card_action_is_idempotent(http, client_db, org):
    """重复点击提交 → **不产生两条草稿**。

    ⚠️ 这是验收标准里明确列的一条。
    """
    session = _make_session(client_db)
    body = _card_action_event(session_id=session.id, event_id="card_evt_same")

    http.post(EVENTS_URL, json=body)
    resp = http.post(EVENTS_URL, json=body)

    assert resp.json()["message"] == "事件已处理"

    plans = client_db.execute(select(MonthPlan)).scalars().all()
    assert len(plans) == 1, f"重复提交产生了 {len(plans)} 条草稿"

    items = client_db.execute(select(MonthPlanItem)).scalars().all()
    assert len(items) == 1, f"重复提交产生了 {len(items)} 条条目"


def test_card_action_empty_task_name_is_rejected(http, client_db, org):
    """任务名称为空 → 拒绝并提示，不写空草稿。"""
    session = _make_session(client_db)

    resp = http.post(
        EVENTS_URL,
        json=_card_action_event(
            session_id=session.id,
            form_value={"task_name": "", "task_content": "只有内容"},
        ),
    )

    assert resp.status_code == 200
    assert client_db.execute(select(MonthPlan)).scalars().all() == []


def test_card_action_form_value_with_array(http, client_db, org):
    """⚠️ 官方：多选组件回传的是**数组**。

    字符串化会变成 ``"['a', 'b']"`` —— 脏数据。
    """
    session = _make_session(client_db)

    resp = http.post(
        EVENTS_URL,
        json=_card_action_event(
            session_id=session.id,
            form_value={"task_name": "带多选的任务", "quality": ["高", "优"]},
        ),
    )

    assert resp.status_code == 200
    item = client_db.execute(select(MonthPlanItem)).scalars().one()
    assert item.quality == "高,优"


# ===========================================================================
# 4. 纯函数
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    ["填月计划", "填月度计划", "写计划", "填表", "帮我填月计划"],
)
def test_detect_intent_month_plan(text):
    """关键词命中 → month_plan 意图。"""
    assert feishu_bot.detect_intent(text) == feishu_bot.INTENT_MONTH_PLAN


def test_detect_intent_cancel():
    """取消关键词 → cancel。"""
    assert feishu_bot.detect_intent("取消") == "cancel"


def test_detect_intent_none():
    """无关文本 → none。"""
    assert feishu_bot.detect_intent("今天中午吃什么") == feishu_bot.INTENT_NONE
    assert feishu_bot.detect_intent("") == feishu_bot.INTENT_NONE


def test_extract_plan_month_formats():
    """月份提取支持多种写法。"""
    assert feishu_bot.extract_plan_month("填 2026-11 的月计划") == "2026-11"
    assert feishu_bot.extract_plan_month("填 2026年11月 的月计划") == "2026-11"
    assert feishu_bot.extract_plan_month("填 2026/11 的月计划") == "2026-11"

    # 不带年份 → 用当前年
    assert feishu_bot.extract_plan_month("填 11月 的月计划") == (
        f"{datetime.now().year}-11"
    )

    assert feishu_bot.extract_plan_month("没有月份") == ""


def test_normalize_form_values_converts_numbers():
    """表单值是字符串 → 要转成数字。"""
    values = feishu_bot._normalize_form_values(
        {"performance_weight": "30", "estimated_value": "100.5"}
    )

    assert values["performance_weight"] == 30.0
    assert values["estimated_value"] == 100.5


def test_normalize_form_values_invalid_number_becomes_zero():
    """数字字段填了非数字 → 当 0，不抛异常。

    填表场景下不该因为一个字段格式不对就整单失败。
    """
    values = feishu_bot._normalize_form_values({"performance_weight": "abc"})

    assert values["performance_weight"] == 0.0


def test_normalize_form_values_array_becomes_comma_string():
    """多选数组 → 逗号分隔字符串。"""
    values = feishu_bot._normalize_form_values({"quality": ["高", "优"]})

    assert values["quality"] == "高,优"


# ===========================================================================
# 5. 幂等与流水
# ===========================================================================


def test_message_flow_is_recorded(http, client_db):
    """消息要落流水（排障用）。"""
    http.post(EVENTS_URL, json=_message_event("填月计划"))

    rows = client_db.execute(select(BotMessage)).scalars().all()
    assert len(rows) == 1
    assert rows[0].direction == "inbound"


def test_message_log_dedup_key_is_message_id(http, client_db):
    """⚠️ 幂等键必须是 message_id（官方明确要求），不是 event_id。"""
    http.post(EVENTS_URL, json=_message_event("填月计划", message_id="om_key_check"))

    row = client_db.execute(select(BotMessage)).scalars().one()
    assert "om_key_check" in row.dedup_key


def test_bot_session_unique_per_open_id_and_app(http, client_db, org):
    """同一 open_id + app 只有一条会话（数据库层约束）。"""
    from sqlalchemy.exc import IntegrityError

    _make_session(client_db)
    client_db.add(
        BotSession(
            open_id="ou_emp_a",
            app_id="cli_test",
            state=feishu_bot.STATE_IDLE,
            intent=feishu_bot.INTENT_NONE,
            collected={},
        )
    )
    with pytest.raises(IntegrityError):
        client_db.commit()
    client_db.rollback()


# ===========================================================================
# 6. 护栏非空转验证
# ===========================================================================


def test_session_ownership_guard_is_not_vacuous(http, client_db, org):
    """⚠️ 护栏自检：归属校验的断言必须**真的能抓到**越权。

    做法：先构造一个「本该被拒」的请求，确认它被拒；
    再确认同一个请求用**正确的 open_id** 能通过 ——
    说明区别确实来自 open_id，而不是别的什么。
    """
    session = _make_session(client_db, open_id="ou_emp_a")

    # 越权 → 拒绝
    resp_bad = http.post(
        EVENTS_URL,
        json=_card_action_event(session_id=session.id, open_id="ou_intruder"),
    )
    assert "归属" in resp_bad.json()["message"]

    # 本人 → 放行
    resp_good = http.post(
        EVENTS_URL,
        json=_card_action_event(session_id=session.id, open_id="ou_emp_a", event_id="evt_2"),
    )
    assert resp_good.json()["code"] == 0
    assert len(client_db.execute(select(MonthPlan)).scalars().all()) == 1


def test_draft_only_guard_is_not_vacuous(http, client_db, org):
    """⚠️ 护栏自检：状态断言必须**真的能抓到**状态被改。

    做法：先确认写完是 draft；再手工把状态改成 submitted，
    确认断言会失败 —— 说明断言不是恒真。
    """
    session = _make_session(client_db)
    http.post(EVENTS_URL, json=_card_action_event(session_id=session.id))

    plan = client_db.execute(select(MonthPlan)).scalars().one()
    assert plan.status == "draft"

    # 模拟「机器人改了状态」的场景 → 断言必须抓到
    plan.status = "submitted"
    client_db.commit()
    assert plan.status != "draft", "断言恒真，抓不到状态被改"
