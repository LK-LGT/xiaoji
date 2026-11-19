"""复核闭环：权限隔离、冻结复算、独立审批、重试与审计。"""
import copy
from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    ConfigSnapshot,
    Department,
    Employee,
    HrDepartmentConfig,
    Score,
    _now,
)
from backend.app.domain.review_models import (
    ReviewCase,
    ReviewEvent,
    ReviewOutbox,
    ScoreAdjustment,
    ScoreHead,
    ScoreVersion,
)
from backend.app.domain.scheme_models import SchemeResponse
from backend.app.routes.review_center import router
from backend.app.services import review_center as svc
from backend.app.services import review_service
from backend.app.services.review_delivery import dispatch


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([Department(id="D", name="业务", leader_id="L"), Department(id="X", name="其他", leader_id="XL"), Department(id="HR", name="人事")])
    db.flush()
    people = {k: Employee(id=k, name=k, department_id=d, manager_id=m, account_status="enabled", employment_status="active") for k, d, m in [("E", "D", "L"), ("L", "D", None), ("R", "D", None), ("H", "HR", None), ("H2", "HR", None), ("XL", "X", None), ("X", "X", None)]}
    db.add_all(people.values())
    db.add(HrDepartmentConfig(department_id="HR", active=True))
    payload = {"scoring_mode": "direct_points", "metrics": [{"code": "K", "name": "业绩", "max_score": 100, "scoring_method": "manager_review"}], "document": {"adjustment_min": -5, "adjustment_max": 5, "grade_rules": [{"level": "A"}, {"level": "B"}]}}
    db.add(ConfigSnapshot(id="S", task_id="T", metrics_json=[], weights_json=[], rubric_json=[], form_template_json={"scheme_version_id": "V", "scheme_payload": payload}))
    db.flush()
    db.add(AssessmentTask(id="T", name="考核", period="202611", snapshot_id="S"))
    db.flush()
    db.add(AssessmentRecord(id="REC", task_id="T", employee_id="E", status="confirmed", confirmed_rating="B"))
    db.flush()
    db.add(SchemeResponse(record_id="REC", values={}, metric_values={}, reviews={"K": {"score": 70, "comment": "原始依据"}, "_grade": {"level": "B", "comment": "等级依据"}, "_adjustment": {"score": 0, "comment": "无加减分"}}, reviewer_id="L"))
    db.flush()
    review_service.persist_score(db, record_id="REC", result=review_service.compute_score(db, record_id="REC"))
    db.commit()
    app = FastAPI()
    app.include_router(router)
    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, "E")
    yield db, people, app, TestClient(app)
    db.close()
    engine.dispose()


def as_user(app, people, key):
    app.dependency_overrides[get_current_user] = lambda: people[key]


def new_case(db, people):
    row = svc.create_case(db, people["E"], target="score:REC", title="评分异议", reason="证据遗漏")
    db.commit()
    return row


def action(db, user, case, value, reviewer=None):
    ext = db.get(ReviewCase, case.id)
    result = svc.act_case(db, user, case, action=value, revision=ext.revision, comment="经核查确认的处理依据", reviewer_id=reviewer)
    db.commit()
    return result


def decided(db, people):
    case = new_case(db, people)
    action(db, people["H"], case, "ACCEPT")
    action(db, people["H"], case, "ASSIGN", "R")
    svc.generate_report(db, people["R"], case, db.get(ReviewCase, case.id).revision)
    db.commit()
    svc.opinion(db, people["R"], case, revision=db.get(ReviewCase, case.id).revision, value="SUPPORT", comment="证据支持修订")
    db.commit()
    action(db, people["H"], case, "SUPPORT")
    return case


def test_real_create_acl_and_original_reviewer_recusal(env):
    db, people, app, client = env
    response = client.post("/v1/review-center/cases", json={"target": "score:REC", "title": "异议", "reason": "核查证据"})
    assert response.status_code == 200, response.text
    row = response.json()["data"]
    assert row["revision"] == 1
    as_user(app, people, "XL")
    assert client.get(f"/v1/review-center/cases/{row['id']}").status_code == 403
    assert client.get("/v1/review-center/cases").json()["data"]["total"] == 0
    as_user(app, people, "L")
    assert client.post(f"/v1/review-center/cases/{row['id']}/actions", json={"revision": 1, "action": "ACCEPT", "comment": "受理"}).status_code == 403
    as_user(app, people, "E")
    assert client.post("/v1/review-center/cases", json={"target": "score:REC", "title": "重复", "reason": "重复"}).status_code == 409


def test_evidence_report_integrity_package_and_version_conflict(env):
    db, people, app, client = env
    case = new_case(db, people)
    url = f"/v1/review-center/cases/{case.id}"
    bad = client.post(url + "/evidence", data={"revision": 1}, files={"file": ("fake.pdf", b"executable", "application/pdf")})
    assert bad.status_code == 422
    response = client.post(url + "/evidence", data={"revision": 1}, files={"file": ("proof.pdf", b"%PDF-1.7\nproof", "application/pdf")})
    assert response.status_code == 200, response.text
    artifact = response.json()["data"]["id"]
    assert client.post(url + "/actions", json={"revision": 1, "action": "WITHDRAW", "comment": "撤回"}).status_code == 409
    assert client.get(url + "/package").content.startswith(b"PK")
    assert client.get(url + "/integrity").json()["data"]["ok"]
    as_user(app, people, "X")
    assert client.get(url + f"/evidence/{artifact}").status_code == 403
    as_user(app, people, "H")
    action(db, people["H"], case, "ACCEPT")
    frozen = copy.deepcopy(db.get(ReviewCase, case.id).snapshot)
    db.get(ConfigSnapshot, "S").form_template_json = {"scheme_payload": {"invalid": True}}
    db.commit()
    report = svc.generate_report(db, people["H"], case, db.get(ReviewCase, case.id).revision)
    assert report.content["recomputed"]["total"] == 70
    assert report.content["snapshot_hash"] == svc.digest(frozen)
    db.commit()
    event = db.scalar(select(ReviewEvent).where(ReviewEvent.case_id == case.id))
    event.payload = {"changed": True}
    with pytest.raises(ValueError, match="禁止覆盖"):
        db.flush()
    db.rollback()


def test_adjustment_separation_recompute_protection_and_delivery(env):
    db, people, app, client = env
    case = decided(db, people)
    row = svc.request_adjustment(db, people["H"], case_id=case.id, meeting_id=None, record_id="REC", expected_version=db.get(ScoreHead, "REC").version, proposed={"K": {"score": 85, "comment": "新增业务证据"}, "_grade": {"level": "A", "comment": "确认等级"}}, reason="证据遗漏纠正")
    db.commit()
    adjustment_id = row.id
    with pytest.raises(HTTPException) as blocked:
        svc.approve_adjustment(db, people["H"], row, revision=1, approve=True, comment="批准")
    assert blocked.value.status_code == 403
    db.rollback()
    row = db.get(ScoreAdjustment, adjustment_id)
    svc.approve_adjustment(db, people["H2"], row, revision=1, approve=True, comment="独立复核通过")
    db.commit()
    assert row.status == "EXECUTED"
    assert db.scalar(select(Score).where(Score.record_id == "REC")).total == 85
    assert db.get(AssessmentRecord, "REC").confirmed_rating == "A"
    assert db.scalar(select(ScoreVersion).where(ScoreVersion.source_id == row.id)).content["total"] == 85
    with pytest.raises(HTTPException):
        svc.approve_adjustment(db, people["H2"], row, revision=1, approve=True, comment="重复点击")
    db.rollback()
    from backend.app.services.scoring import ScoreResult
    with pytest.raises(Exception, match="复核保护"):
        review_service.persist_score(db, record_id="REC", result=ScoreResult(total=20, per_metric=[]))
    db.rollback()
    dispatch(db)
    db.commit()
    event = db.scalar(select(ReviewOutbox).where(ReviewOutbox.topic == "score.adjusted"))
    assert event.status == "MANUAL_REQUIRED"
    assert "奖金" in event.last_error
    attempts = event.attempts
    dispatch(db)
    db.commit()
    assert event.attempts == attempts


def test_meeting_scope_sample_suppression_resolution_and_close(env):
    db, people, app, client = env
    with pytest.raises(HTTPException):
        svc.department_stats(db, people["XL"], "202611", ["D"])
    row = svc.create_meeting(db, people["H"], title="校准", period="202611", departments=["D"], participants=["H2"])
    db.commit()
    assert row.snapshot["distributions"][0]["average"] is None
    svc.resolve_meeting(db, people["H"], row, revision=1, record_id="REC", action="REVIEW", basis="需要补充核查", owner_id="R", due_at=_now() + timedelta(days=3))
    db.commit()
    assert row.revision == 2
    assert db.scalar(select(ReviewCase.id))
    svc.close_meeting(db, people["H"], row, 2)
    db.commit()
    assert row.status == "CLOSED"


def test_stale_adjustment_does_not_mutate_score_or_reviews(env):
    db, people, app, client = env
    case = decided(db, people)
    row = svc.request_adjustment(db, people["H"], case_id=case.id, meeting_id=None, record_id="REC", expected_version=db.get(ScoreHead, "REC").version, proposed={"K": {"score": 90, "comment": "新证据"}}, reason="更正")
    db.commit()
    adjustment_id = row.id
    db.get(SchemeResponse, "REC").revision += 1
    db.commit()
    with pytest.raises(HTTPException):
        svc.approve_adjustment(db, people["H2"], row, revision=1, approve=True, comment="批准")
    db.rollback()
    assert db.get(ScoreAdjustment, adjustment_id).status == "PENDING"
    assert db.scalar(select(Score).where(Score.record_id == "REC")).total == 70
    assert db.get(SchemeResponse, "REC").reviews["K"]["score"] == 70
