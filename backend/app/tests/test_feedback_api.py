"""P2-3 acceptance: 反馈 / 灰度 / 案例库 API —— 权限闸门 + 只读业务表。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    Score,
)
from backend.app.routes.feedback import router
from backend.app.services import feedback_service as svc


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Department(id="D", name="业务", leader_id="M"))
    s.add(Department(id="HR", name="人事"))
    s.add(HrDepartmentConfig(department_id="HR", active=True))
    s.add_all([
        Employee(id="E", name="员工E", department_id="D", manager_id="M",
                 employment_status="active", account_status="enabled"),
        Employee(id="M", name="主管M", department_id="D",
                 employment_status="active", account_status="enabled"),
        Employee(id="H", name="HR", department_id="HR",
                 employment_status="active", account_status="enabled"),
        Employee(id="X", name="无关人员", department_id="D",
                 employment_status="active", account_status="enabled"),
    ])
    s.add(AssessmentTask(id="T", name="任务", period="202611"))
    s.flush()
    s.add(AssessmentRecord(id="R", task_id="T", employee_id="E", confirmed_rating="B"))
    s.add(Score(record_id="R", total=72.0, per_metric_json=[
        {"metric_code": "GMV", "metric_name": "销售额", "value": 90,
         "target": 100, "ratio": 0.9, "level": "B"},
    ]))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def _client_for(db, employee_id: str) -> TestClient:
    from backend.app.dependencies import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, employee_id)
    return TestClient(app)


# ── 反馈 ────────────────────────────────────────────────────────────────────

def test_submit_feedback_by_manager(db):
    """主管可提交反馈。"""
    client = _client_for(db, "M")
    resp = client.post("/v1/feedback", json={
        "record_id": "R", "action": "ACCEPT", "agent": "comment_draft",
        "prompt_version": "v2", "reason": "可用",
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["action"] == "ACCEPT"


def test_submit_feedback_invalid_action_400(db):
    """非法动作 → 400。"""
    client = _client_for(db, "M")
    resp = client.post("/v1/feedback", json={"record_id": "R", "action": "BAD"})
    assert resp.status_code == 400


def test_unrelated_user_cannot_submit_feedback(db):
    """无关人员不能提交反馈。"""
    client = _client_for(db, "X")
    resp = client.post("/v1/feedback", json={"record_id": "R", "action": "ACCEPT"})
    assert resp.status_code == 403


def test_feedback_api_does_not_mutate_business_state(db):
    """★ 提交反馈前后 Score / confirmed_rating 不变。"""
    client = _client_for(db, "M")
    client.post("/v1/feedback", json={"record_id": "R", "action": "REJECT"})
    db.expire_all()
    assert db.scalar(select(Score).where(Score.record_id == "R")).total == 72.0
    assert db.get(AssessmentRecord, "R").confirmed_rating == "B"


def test_list_feedback_requires_hr(db):
    """普通主管不能列反馈。"""
    client = _client_for(db, "M")
    assert client.get("/v1/feedback").status_code == 403


def test_list_feedback_by_hr(db):
    """HR 可以列反馈。"""
    svc.record_feedback(db, record_id="R", actor_id="M", action="ACCEPT")
    db.commit()
    client = _client_for(db, "H")
    resp = client.get("/v1/feedback")
    assert resp.status_code == 200
    assert resp.json()["data"]["stats"]["total"] == 1


# ── Prompt 灰度 ─────────────────────────────────────────────────────────────

def test_upsert_prompt_requires_hr(db):
    """非 HR 不能改 Prompt 版本。"""
    client = _client_for(db, "M")
    resp = client.put("/v1/feedback/prompts", json={
        "agent": "comment_draft", "version": "v1", "prompt_text": "P",
    })
    assert resp.status_code == 403


def test_upsert_prompt_by_hr(db):
    """HR 可以改 Prompt 版本。"""
    client = _client_for(db, "H")
    resp = client.put("/v1/feedback/prompts", json={
        "agent": "comment_draft", "version": "v1", "prompt_text": "P",
        "rollout": "canary", "canary_ratio": 0.2,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["rollout"] == "canary"


def test_upsert_prompt_invalid_rollout_400(db):
    """非法灰度模式 → 400。"""
    client = _client_for(db, "H")
    resp = client.put("/v1/feedback/prompts", json={
        "agent": "a", "version": "v1", "prompt_text": "P", "rollout": "weird",
    })
    assert resp.status_code == 400


def test_resolve_prompt_endpoint(db):
    """灰度解析端点。"""
    svc.upsert_prompt_version(
        db, agent="comment_draft", version="v1", prompt_text="P1", rollout="full"
    )
    db.commit()
    client = _client_for(db, "H")
    resp = client.get("/v1/feedback/prompts/resolve?agent=comment_draft&record_id=R")
    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == "v1"


def test_compare_prompts_requires_hr(db):
    """非 HR 不能看灰度对比。"""
    client = _client_for(db, "M")
    assert client.get("/v1/feedback/prompts/compare").status_code == 403


def test_compare_prompts_by_hr(db):
    """HR 能看灰度对比。"""
    client = _client_for(db, "H")
    resp = client.get("/v1/feedback/prompts/compare?agent=comment_draft")
    assert resp.status_code == 200
    assert resp.json()["data"]["sufficient"] is False


# ── 案例库 ──────────────────────────────────────────────────────────────────

def test_everyone_can_read_cases(db):
    """案例库全员可读。"""
    svc.publish_case(db, title="案例A", scenario="veto", resolution="r", actor_id="H")
    db.commit()
    client = _client_for(db, "E")
    resp = client.get("/v1/feedback/cases")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


def test_employee_cannot_publish_case(db):
    """普通员工不能收录案例。"""
    client = _client_for(db, "E")
    resp = client.post("/v1/feedback/cases", json={
        "title": "T", "scenario": "x", "resolution": "r",
    })
    assert resp.status_code == 403


def test_manager_can_publish_case(db):
    """主管可以收录案例。"""
    client = _client_for(db, "M")
    resp = client.post("/v1/feedback/cases", json={
        "title": "低分处理", "scenario": "low_score", "resolution": "面谈 + 改进计划",
        "source_record_id": "R",
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["redacted"] is True


def test_publish_case_rejects_unconfirmed(db):
    """未确认评级 → 400。"""
    db.get(AssessmentRecord, "R").confirmed_rating = None
    db.commit()
    client = _client_for(db, "M")
    resp = client.post("/v1/feedback/cases", json={
        "title": "T", "scenario": "x", "resolution": "r", "source_record_id": "R",
    })
    assert resp.status_code == 400
    assert "已确认评级" in resp.json()["detail"]
