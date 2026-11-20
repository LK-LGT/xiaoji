"""Cross-channel scheme filling and callback safety regression tests."""
from datetime import timedelta

import pytest
from fastapi import HTTPException

from backend.app.dependencies import get_current_user
from backend.app.domain.bot_models import BotSession
from backend.app.domain.models import AssessmentRecord, SelfReview
from backend.app.domain.scheme_models import SchemeResponse
from backend.app.routes.feishu_bot import _handle_card_action
from backend.app.services import feishu_bot, scheme_service
from backend.app.services import feishu_scheme as flow
from backend.app.tests.test_scheme_management import payload, setup


@pytest.fixture
def case(setup):
    db, people, app, client = setup
    p = payload()
    p["metrics"][0]["weight"] = 60
    p["metrics"].append({"code": "TEXT", "name": "成果", "type": "narrative", "weight": 40,
                         "scoring_method": "manager_review", "scoring_description": "主管按成果评价"})
    p["fields"].extend([
        {"metric_code": "TEXT", "key": "summary", "label": "成果说明", "stage": "result", "type": "textarea", "required": True},
        {"metric_code": "TEXT", "key": "secret", "label": "主管意见", "stage": "manager_review", "type": "textarea", "required": True},
    ])
    v = scheme_service.create_version(db, people["H"], p)
    scheme_service.publish(db, people["H"], v)
    db.commit()
    result = client.post("/v1/performance/scheme-tasks", json={
        "title": "混合考核", "period": "202701", "employee_ids": ["E"],
        "self_review_deadline": "2027-02-01T12:00:00", "manager_review_deadline": "2027-02-05T12:00:00"})
    assert result.status_code == 200, result.text
    session = feishu_bot.get_or_create_session(db, open_id="ou_E", app_id="cli")
    session.employee_id = "E"
    card = flow.start(db, session, people["E"], "考核填报")
    db.commit()
    return db, people, app, client, session, card


def advance(db, session, form):
    return flow.handle(db, session, {"step": "next"}, form)


def test_mixed_filling_submits_same_web_record(case):
    db, people, app, client, session, card = case
    assert "secret" not in str(card)
    advance(db, session, {"f0": "12", "f1": "10", "f2": "12"})
    db.commit()
    card = advance(db, session, {"f0": "完成交付并验收"})
    assert card["header"]["title"]["content"] == "确认考核填报"
    assert db.query(SchemeResponse).count() == 0
    flow.handle(db, session, {"step": "submit"}, {})
    db.commit()
    response = db.query(SchemeResponse).one()
    assert response.values == {"actual": "12", "summary": "完成交付并验收"}
    assert response.metric_values == {"KPI": {"actual": 12, "target": 10}}
    assert db.get(AssessmentRecord, response.record_id).status == "self_review_submitted"
    app.dependency_overrides[get_current_user] = lambda: people["E"]
    path = f"/v1/performance/scheme-records/{response.record_id}"
    assert client.get(path).json()["data"]["values"] == response.values
    assert client.put(path, json={"revision": response.revision, "values": response.values}).status_code == 409
    with pytest.raises(HTTPException):
        flow.handle(db, session, {"step": "submit"}, {})
    assert db.query(SchemeResponse).count() == 1


@pytest.mark.parametrize("raw", ["abc", "NaN", "Infinity", "-1"])
def test_invalid_numbers_preserve_step(case, raw):
    db, _, _, _, session, _ = case
    with pytest.raises(HTTPException):
        advance(db, session, {"f0": "12", "f1": "10", "f2": raw})
    assert session.collected["index"] == 0
    assert db.query(SchemeResponse).count() == 0


def test_callback_generation_owner_and_expiration(case):
    import asyncio

    db, _, _, _, session, _ = case
    class Client:
        def send_card_message(self, *args, **kwargs):
            return {}
    value = {"action": flow.ACTION, "step": "next", "session_id": session.id,
             "generation": session.last_message_id}
    def callback(open_id="ou_E"):
        return {"event": {"operator": {"open_id": open_id}, "action": {
            "value": value, "form_value": {"f0": "12", "f1": "10", "f2": "12"}}}}
    # ⚠️ `_handle_card_action` 是 async（绩效问答要等 L3）—— 必须 await。
    assert "不匹配" in asyncio.run(
        _handle_card_action(db, Client(), callback("ou_other"), event_id="a")
    )["message"]
    assert session.collected["index"] == 0
    asyncio.run(_handle_card_action(db, Client(), callback(), event_id="b"))
    db.commit()
    assert session.collected["index"] == 1
    assert "失效" in asyncio.run(
        _handle_card_action(db, Client(), callback(), event_id="c")
    )["message"]
    session.expires_at = feishu_bot._now() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException, match="过期"):
        flow.handle(db, session, {"step": "next"}, {"f0": "成果"})


def test_web_submission_invalidates_active_card(case):
    db, people, app, client, session, _ = case
    app.dependency_overrides[get_current_user] = lambda: people["E"]
    record_id = session.collected["record_id"]
    result = client.put(f"/v1/performance/scheme-records/{record_id}", json={
        "revision": 0, "values": {"actual": "10", "summary": "网页提交"},
        "metric_values": {"KPI": {"target": 10, "actual": 10}}})
    assert result.status_code == 200, result.text
    with pytest.raises(HTTPException, match="锁定"):
        advance(db, session, {"f0": "20", "f1": "10", "f2": "20"})
    assert db.query(SchemeResponse).one().values["summary"] == "网页提交"
