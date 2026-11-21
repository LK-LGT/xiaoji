"""飞书端 —— 网盘工作量数据填报的集成测试（BP-2 / P3）。

覆盖
----
1. 意图识别：「填报表数据」优先于「填表」（月计划）
2. 消息事件 → 落库 → 回卡片
3. ⚠️ **数值解析不出 → 回错误卡，不落库**
4. ⚠️ **指标认不出 → 回错误卡，不落库**
5. 截图消息 → 抽出 image_key → 存证据
6. ⚠️ **没绑定员工 → 拒绝**（不能替别人填）
7. 消息报文的媒体解析（image / file / post 富文本）
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.domain.pan_workload_models import (
    PanManualMetric,
    PanManualMetricArtifact,
)
from backend.app.routes import feishu_bot as feishu_bot_routes
from backend.app.services import feishu_bot, feishu_intent

# ===========================================================================
# 准备
# ===========================================================================

BOT_TOKEN = "v_bot_token_for_test"


def _message_event(text: str, *, open_id: str = "ou_emp_a", message_id: str = "om_1"):
    body = {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_{message_id}",
            "event_type": "im.message.receive_v1",
            "token": BOT_TOKEN,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": open_id, "user_id": "u_emp_a"},
                "sender_type": "user",
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }
    return body


def _image_event(image_key: str, *, open_id: str = "ou_emp_a", message_id: str = "om_img"):
    body = {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_{message_id}",
            "event_type": "im.message.receive_v1",
            "token": BOT_TOKEN,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": open_id, "user_id": "u_emp_a"},
                "sender_type": "user",
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "image",
                "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
            },
        },
    }
    return body


@pytest.fixture()
def http(db, monkeypatch):
    """带假飞书客户端的 TestClient。"""
    from backend.app.main import app

    monkeypatch.setattr(settings, "feishu_bot_event_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "feishu_approval_webhook_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "feishu_encrypt_key", "")
    monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
    monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")

    sent: list[dict] = []

    class _FakeClient:
        def send_card_message(self, receive_id, card, **kwargs):
            sent.append({"receive_id": receive_id, "card": card, **kwargs})
            return {"code": 0, "data": {"message_id": "om_fake"}}

    monkeypatch.setattr(feishu_bot_routes, "_client", lambda: _FakeClient())

    def _override():
        yield db

    app.dependency_overrides[feishu_bot_routes.get_db] = _override
    with TestClient(app) as c:
        c.feishu_sent = sent  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def org(db):
    """员工甲（已绑定飞书身份）与员工乙（未绑定）。"""
    from backend.app.domain.models import Department, Employee, FeishuIdentity

    d1 = Department(id="D_1", name="业务部", active=True)
    db.add(d1)
    emp_a = Employee(
        id="E_A", name="员工甲", department_id="D_1",
        employment_status="active", account_status="enabled",
        feishu_open_id="ou_emp_a", feishu_user_id="u_emp_a",
    )
    db.add(emp_a)
    db.add(FeishuIdentity(
        id="FI_A", employee_id="E_A",
        identity_type="open_id", identity_value="ou_emp_a", active=True,
    ))
    db.commit()
    return emp_a


EVENTS_URL = "/v1/feishu/bot/events"


@pytest.fixture()
def db():
    """⚠️ **必须用 StaticPool** —— 与 ``test_feishu_bot.py`` 同款。

    FastAPI TestClient 在**工作线程**里发请求，
    SQLite ``:memory:`` 默认每连接一个独立库（会报 "no such table"）。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from backend.app.domain.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ===========================================================================
# 1. 意图识别
# ===========================================================================


class TestIntent:
    def test_填报意图(self):
        assert feishu_intent.detect_intent("填报表数据") == (
            feishu_intent.INTENT_MANUAL_METRIC
        )
        assert feishu_intent.detect_intent("完播率 35%") == (
            feishu_intent.INTENT_MANUAL_METRIC
        )

    def test_填报优先于月计划(self):
        """⚠️ **关键顺序** —— 「填表」在月计划词表里。

        若月计划先判，「填报表数据」会被导进月计划表单 ⇒
        员工填了指标却进了错误的表单。
        """
        assert feishu_intent.detect_intent("填报表数据") != (
            feishu_intent.INTENT_MONTH_PLAN
        )
        assert feishu_intent.detect_intent("填月计划") == (
            feishu_intent.INTENT_MONTH_PLAN
        )

    def test_取消优先于一切(self):
        assert feishu_intent.detect_intent("算了") == feishu_intent.INTENT_CANCEL


# ===========================================================================
# 2. 消息 → 落库
# ===========================================================================


class TestSubmitFlow:
    def test_发消息完成填报(self, http, db, org):
        resp = http.post(EVENTS_URL, json=_message_event("完播率 35%"))
        assert resp.status_code == 200, resp.text
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 1
        assert rows[0].metric_code == "video_completion_rate"
        assert rows[0].metric_value == pytest.approx(0.35)
        assert rows[0].source == "feishu"
        assert rows[0].employee_id == "E_A"
        assert rows[0].feishu_message_id == "om_1"

    def test_回了一张卡(self, http, db, org):
        http.post(EVENTS_URL, json=_message_event("完播率 35%"))
        assert len(http.feishu_sent) >= 1
        card = http.feishu_sent[-1]["card"]
        assert card["header"]["title"]["content"] == "数据已记录"

    def test_数值解析不出_不落库(self, http, db, org):
        """⚠️ **绝不猜** —— 认不出就不落库。"""
        resp = http.post(EVENTS_URL, json=_message_event("完播率还行"))
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []
        # 回的是错误卡
        card = http.feishu_sent[-1]["card"]
        assert card["header"]["title"]["content"] == "操作未完成"

    def test_指标认不出_不落库(self, http, db, org):
        resp = http.post(EVENTS_URL, json=_message_event("随便说点什么 35%"))
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []

    def test_未绑定员工_拒绝(self, http, db, org):
        """⚠️ 不能替别人填 —— 必须确认发送者是谁。"""
        resp = http.post(
            EVENTS_URL, json=_message_event("完播率 35%", open_id="ou_stranger")
        )
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []
        card = http.feishu_sent[-1]["card"]
        assert "绑定" in json.dumps(card, ensure_ascii=False)

    def test_重复填报是覆盖(self, http, db, org):
        http.post(EVENTS_URL, json=_message_event("完播率 35%"))
        http.post(
            EVENTS_URL,
            json=_message_event("完播率 40%", message_id="om_2"),
        )
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 1
        assert rows[0].metric_value == pytest.approx(0.40)


# ===========================================================================
# 3. 截图证据
# ===========================================================================


class TestEvidenceFlow:
    def test_图片报文抽出image_key(self):
        body = _image_event("img_v3_abc")
        msg = feishu_bot_routes.extract_message_event(body)
        assert msg["image_keys"] == ["img_v3_abc"]
        assert msg["message_type"] == "image"

    def test_富文本里的图片也能抽出(self):
        body = _message_event("看下")
        body["event"]["message"]["message_type"] = "post"
        body["event"]["message"]["content"] = json.dumps(
            {
                "post": {
                    "zh_cn": {
                        "content": [
                            {"tag": "text", "text": "看这个"},
                            {"tag": "img", "img_key": "img_post_1"},
                        ]
                    }
                }
            },
            ensure_ascii=False,
        )
        msg = feishu_bot_routes.extract_message_event(body)
        assert "img_post_1" in msg["image_keys"]

    def test_发图存档为证据(self, http, db, org, monkeypatch):
        """⚠️ 需要先有指标才有地方挂证据。"""
        http.post(EVENTS_URL, json=_message_event("完播率 35%"))

        from backend.app.services import feishu_manual_metric as fmm
        from backend.app.services.feishu_docs import FeishuDocsClient

        monkeypatch.setattr(
            FeishuDocsClient,
            "fetch_message_resource",
            lambda self, mid, key, resource_type="file": (
                b"\x89PNGfake", "shot.png"
            ),
        )
        resp = http.post(
            EVENTS_URL, json=_image_event("img_v3_abc", message_id="om_img")
        )
        assert resp.status_code == 200
        arts = db.scalars(select(PanManualMetricArtifact)).all()
        assert len(arts) == 1
        assert arts[0].feishu_image_key == "img_v3_abc"
        assert arts[0].source == "feishu"

    def test_发图回执提示还需填数值(self, http, db, org, monkeypatch):
        from backend.app.services.feishu_docs import FeishuDocsClient

        monkeypatch.setattr(
            FeishuDocsClient,
            "fetch_message_resource",
            lambda self, mid, key, resource_type="file": (b"x", "s.png"),
        )
        http.post(EVENTS_URL, json=_image_event("img_abc", message_id="om_img2"))
        card = http.feishu_sent[-1]["card"]
        assert card["header"]["title"]["content"] == "截图已收到"
        body = json.dumps(card, ensure_ascii=False)
        assert "数值" in body

    def test_先发图后补数值_截图自动挂上(self, http, db, org, monkeypatch):
        """⚠️ **员工的自然顺序** —— 先截图再补数值，要能走通。"""
        from backend.app.services.feishu_docs import FeishuDocsClient

        monkeypatch.setattr(
            FeishuDocsClient,
            "fetch_message_resource",
            lambda self, mid, key, resource_type="file": (b"x", "s.png"),
        )
        http.post(EVENTS_URL, json=_image_event("img_abc", message_id="om_img3"))
        # 截图先到 ⇒ 待关联
        arts = db.scalars(select(PanManualMetricArtifact)).all()
        assert len(arts) == 1
        assert arts[0].metric_id is None

        # 再补数值 ⇒ 自动挂上
        http.post(
            EVENTS_URL,
            json=_message_event("完播率 35%", message_id="om_txt"),
        )
        arts = db.scalars(select(PanManualMetricArtifact)).all()
        assert len(arts) == 1
        assert arts[0].metric_id is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
