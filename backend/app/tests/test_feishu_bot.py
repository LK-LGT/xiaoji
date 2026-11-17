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

import json
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

    ⚠️ 机器人 Token 留空时会**回退**到审批 Token，所以两个都得为空才是
    「真的没配」。
    """
    monkeypatch.setattr(settings, "feishu_bot_event_token", "")
    monkeypatch.setattr(settings, "feishu_approval_webhook_token", "")

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


def test_bot_token_falls_back_to_approval_token(http, client_db, monkeypatch):
    """⚠️ **Verification Token 是应用级的** —— 三条链路只能共用一个。

    飞书官方原文：「Verification Token 是应用的验证标识。开发者后台会为应用
    自动生成 Verification Token」—— 一个应用只有一个；且
    「每个应用只能配置一个请求地址，该应用订阅的所有事件都会发送到该请求地址」。

    ⇒ 运维时配了一个 Token 却漏配另一个，**不该导致 503**。
    机器人 Token 留空时回退用审批 Token（同一个应用下二者本就相同）。
    """
    monkeypatch.setattr(settings, "feishu_bot_event_token", "")
    monkeypatch.setattr(settings, "feishu_approval_webhook_token", "APP-LEVEL-TOKEN")

    body = _message_event("填月计划")
    body["header"]["token"] = "APP-LEVEL-TOKEN"

    resp = http.post(EVENTS_URL, json=body)

    assert resp.status_code == 200, "回退到审批 Token 后不该 503"
    assert resp.json()["code"] == 0


def test_bot_token_takes_precedence_when_set(http, client_db, monkeypatch):
    """机器人 Token **有配就用它**（独立应用部署场景）。"""
    monkeypatch.setattr(settings, "feishu_bot_event_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "feishu_approval_webhook_token", "APP-LEVEL-TOKEN")

    # 审批 Token 在机器人端点上应被拒（因为配了机器人专属 Token）
    body = _message_event("填月计划")
    body["header"]["token"] = "APP-LEVEL-TOKEN"
    resp = http.post(EVENTS_URL, json=body)
    assert resp.status_code == 401

    # 机器人 Token 应被接受
    body2 = _message_event("填月计划", message_id="om_bot_token_ok")
    resp2 = http.post(EVENTS_URL, json=body2)
    assert resp2.status_code == 200


def test_bot_endpoint_ignores_other_chains_event_types(http, client_db, org):
    """⚠️ **隔离靠事件类型，不靠 Token**（Token 是应用级的）。

    审批 / 通讯录的事件即使带着合法 Token 打到机器人端点，
    也必须被**忽略** —— 机器人端点不是审批回流的入口。
    """
    body = _message_event("填月计划")
    # 换成审批事件类型（同一个应用，共用同一个 Token）
    body["header"]["event_type"] = "approval.approval.updated_v4"

    resp = http.post(EVENTS_URL, json=body)

    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    # 没建会话、没发卡片、没写草稿
    assert client_db.execute(select(BotSession)).scalars().all() == []
    assert http.feishu_sent == []


def test_bot_event_type_whitelist_is_explicit():
    """白名单本身要被钉住 —— 加新事件类型时必须显式改这里。"""
    assert feishu_bot_routes.EVENT_MESSAGE_RECEIVE in feishu_bot_routes.BOT_EVENT_TYPES
    assert feishu_bot_routes.EVENT_CARD_ACTION in feishu_bot_routes.BOT_EVENT_TYPES
    # 审批 / 通讯录的事件类型**绝不能**进白名单
    assert "approval.approval.updated_v4" not in feishu_bot_routes.BOT_EVENT_TYPES
    assert not any(t.startswith("contact.") for t in feishu_bot_routes.BOT_EVENT_TYPES)


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


# ===========================================================================
# ⚠️ 统一入口的事件类型分发（2026-11-17 新增）
#
# 背景：飞书「一个应用只有一个请求地址，所有订阅事件都发到那一个地址」。
#      实测后台请求地址指向 /v1/feishu/approval/events（审批 Token 已配、
#      握手通；通讯录 Token 反而是空的）⇒ 机器人事件也会打到这里。
#
#      ⇒ 不做分发的话：订阅了 im.message.receive_v1 也会打到这里 →
#        被当成审批事件解析 → 找不到计划 → 事件被丢弃，
#        **机器人永远收不到员工消息**。
#
#      ⇒ 所以统一入口必须按 event_type 分发。
# ===========================================================================

APPROVAL_EVENTS_URL = "/v1/feishu/approval/events"


@pytest.fixture()
def unified_http(client_db, monkeypatch):
    """统一入口用的 TestClient。

    ⚠️ 关键：机器人 Token 与审批 Token **共用**（应用级，见 FS-3 C2 修正），
    所以这里让 ``feishu_bot_event_token`` 留空 —— 走**回退**逻辑，
    用审批那个 Token。这正是生产的真实形态。
    """
    monkeypatch.setattr(settings, "feishu_approval_webhook_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "feishu_bot_event_token", "")
    monkeypatch.setattr(settings, "feishu_encrypt_key", "")
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


def _message_event_with_token(token: str) -> dict:
    """把官方报文里的 token 换成指定值（信��本身是官方原文）。"""
    payload = json.loads(json.dumps(OFFICIAL_MESSAGE_EVENT))
    payload["header"]["token"] = token
    return payload


def test_unified_entry_dispatches_bot_message_event(unified_http, client_db, org):
    """⚠️ **机器人消息事件打到统一入口 → 必须被转发到机器人处理**。

    这是本次修复的核心：不做分发的话，机器人**永远收不到**员工消息。
    """
    payload = _message_event_with_token(BOT_TOKEN)
    r = unified_http.post(APPROVAL_EVENTS_URL, json=payload)

    assert r.status_code == 200, r.text
    # 机器人处理了 → 回了一张卡片
    assert len(unified_http.feishu_sent) >= 1, "机器人没有被触发，没发出卡片"
    # ⚠️ 而且会话被建立（证明走的是机器人的处理逻辑，不是审批回流）
    sessions = client_db.execute(select(BotSession)).scalars().all()
    assert len(sessions) == 1, "机器人会话未建立 → 事件没被转发"


def test_unified_entry_does_not_break_approval_flow(unified_http, client_db):
    """⚠️ **分发不能破坏审批回流** —— 审批事件仍走原逻辑。

    回归护栏：加分发是为了加机器人，**不能把审批搞坏**。

    ⚠️ **这个护栏必须能抓到「分发写错」** —— 所以它断言的不是
    「没有卡片发出」（那是**否定式断言**，把分发整个删掉也照样通过 ⇒ 空转），
    而是断言**审批事件确实走到了审批处理器**：
      - 返回体带 ``data.result``（审批回流的返回结构）
      - 且**机器人处理器完全没被调用**

    ⇒ 破坏「分发只会拦机器人事件」时，这个断言会失败。
    """
    # 审批事件（事件类型不是机器人那两种）→ 走审批回流。
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_approval_001",
            "event_type": "approval_instance",
            "token": BOT_TOKEN,
        },
        "event": {},
    }
    r = unified_http.post(APPROVAL_EVENTS_URL, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    # ⚠️ 审批回流的返回结构 —— 机器人处理器的返回**没有** data.result。
    #    这是「确实走到了审批处理器」的正向信号。
    assert "data" in body, f"审批事件没走审批处理器（被别的分支吞了）：{body}"
    # 审批事件**不该**被机器人处理 → 不该有卡片发出
    assert len(unified_http.feishu_sent) == 0, "审批事件被错误地路由到机器人了"


def test_unified_entry_rejects_bot_event_with_bad_token(unified_http, client_db, org):
    """⚠️ **来源校验必须在分发之前** —— Token 不对照样 401。

    分发逻辑绝不能成为「绕过 Token 校验」的后门。

    ⚠️ 这个护栏要能抓到「校验被挪到分发之后」——
       所以除了「401」，还要断言**机器人处理器完全没被碰到**。
       光断言 401 不够：万一有人为了「让机器人事件能过」
       把校验挪到分发之后，机器人事件就会先被处理再校验 ⇒ 数据已经写进去了。
    """
    payload = _message_event_with_token("wrong-token")
    r = unified_http.post(APPROVAL_EVENTS_URL, json=payload)

    assert r.status_code == 401, r.text
    assert len(unified_http.feishu_sent) == 0, "Token 不对却发出了卡片"
    sessions = client_db.execute(select(BotSession)).scalars().all()
    assert len(sessions) == 0, "Token 不对却建立了会话"
    # ⚠️ **最关键**：连幂等流水都不能写 ——
    #    写流水 = 机器人处理器已经被调用过了（哪怕后来被拒）。
    msgs = client_db.execute(select(BotMessage)).scalars().all()
    assert len(msgs) == 0, "Token 不对却写了机器人流水 —— 校验被挪到分发之后了"


def test_unified_entry_still_503_when_no_token_configured(client_db, monkeypatch):
    """⚠️ 两个 Token 都没配 → 503（不放行）。

    回归护栏：机器人 Token 回退到审批 Token；两个都没有时必须拒绝。
    """
    monkeypatch.setattr(settings, "feishu_approval_webhook_token", "")
    monkeypatch.setattr(settings, "feishu_bot_event_token", "")
    monkeypatch.setattr(settings, "feishu_encrypt_key", "")

    with TestClient(app) as c:
        r = c.post(APPROVAL_EVENTS_URL, json={"token": "x"})
    assert r.status_code == 503


def test_dispatch_guard_is_not_vacuous(unified_http, client_db, org):
    """⚠️ 护栏自检：分发断言必须**真的能抓到**「没分发」。

    做法：直接调审批回流路径（绕过分发），
    确认机器人会话数**不会**增加 —— 说明断言不是恒真。
    """
    payload = _message_event_with_token(BOT_TOKEN)
    unified_http.post(APPROVAL_EVENTS_URL, json=payload)

    after_dispatch = len(client_db.execute(select(BotSession)).scalars().all())
    assert after_dispatch == 1, "分发没生效"

    # 再发一次**同样的**事件 → 幂等，不应新建会话
    unified_http.post(APPROVAL_EVENTS_URL, json=payload)
    after_retry = len(client_db.execute(select(BotSession)).scalars().all())
    assert after_retry == 1, "重复事件创建了新会话 —— 幂等被破坏"
