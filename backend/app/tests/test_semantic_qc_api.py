"""P2-1 acceptance: 语义质检 API —— 权限闸门 + 只读保证 + 建议不改分。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    CommentDraft,
    Department,
    Employee,
    Evidence,
    Score,
)
from backend.app.routes.semantic_qc import router


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Department(id="D", name="业务", leader_id="M"))
        db.flush()
        db.add_all([
            Employee(id="E", name="员工E", department_id="D", manager_id="M",
                     employment_status="active", account_status="enabled"),
            Employee(id="M", name="主管M", department_id="D",
                     employment_status="active", account_status="enabled"),
            Employee(id="H", name="HR", department_id="D",
                     employment_status="active", account_status="enabled"),
        ])
        db.add(AssessmentTask(id="T", name="任务", period="202611"))
        db.flush()
        db.add(AssessmentRecord(id="R", task_id="T", employee_id="E"))
        db.add(Score(record_id="R", total=55.0, per_metric_json=[
            {"metric_code": "GMV", "metric_name": "销售额", "value": 50,
             "target": 100, "ratio": 0.5, "level": "C"},
        ]))
        db.add(CommentDraft(record_id="R", markdown="本期表现良好，工作认真。",
                            citations=[], status="draft"))
        db.add(Evidence(id="EV1", employee_id="E", kind="manual", summary="s", source="manual"))
        db.commit()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        yield db, client
    engine.dispose()


def _client_for(db, employee_id: str) -> TestClient:
    """构造一个以指定员工身份访问的客户端。"""
    from backend.app.dependencies import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db

    def _user():
        return db.get(Employee, employee_id)

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def test_analyze_returns_three_sections(setup):
    """返回质检 / 风险 / 提纲三段。"""
    db, _ = setup
    client = _client_for(db, "M")
    resp = client.get("/v1/semantic-qc/records/R")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "semanticIssues" in data
    assert "risks" in data
    assert "interviewOutline" in data
    assert data["recordId"] == "R"
    assert data["employeeId"] == "E"
    assert data["period"] == "202611"


def test_manager_can_view_direct_report(setup):
    """主管可以看直属下属。"""
    db, _ = setup
    client = _client_for(db, "M")
    assert client.get("/v1/semantic-qc/records/R").status_code == 200


def test_employee_can_view_own(setup):
    """员工可以看自己的。"""
    db, _ = setup
    client = _client_for(db, "E")
    assert client.get("/v1/semantic-qc/records/R").status_code == 200


def test_unrelated_user_is_forbidden(setup):
    """无关人员被拒 —— 权限闸门先于业务逻辑。"""
    db, _ = setup
    db.add(Employee(id="X", name="无关人员", department_id="D",
                    employment_status="active", account_status="enabled"))
    db.commit()
    client = _client_for(db, "X")
    resp = client.get("/v1/semantic-qc/records/R")
    assert resp.status_code == 403


def test_missing_record_is_404(setup):
    """记录不存在 → 404。"""
    db, _ = setup
    client = _client_for(db, "M")
    assert client.get("/v1/semantic-qc/records/NOPE").status_code == 404


def test_low_achievement_risk_visible(setup):
    """达成率 0.5 的低分场景应产生风险信号。"""
    db, _ = setup
    client = _client_for(db, "M")
    data = client.get("/v1/semantic-qc/records/R").json()["data"]
    codes = {r["code"] for r in data["risks"]}
    assert "RISK_LOW_ACHIEVEMENT" in codes


def test_vague_comment_detected(setup):
    """草稿里的空洞措辞被识别。"""
    db, _ = setup
    client = _client_for(db, "M")
    data = client.get("/v1/semantic-qc/records/R").json()["data"]
    codes = {i["code"] for i in data["semanticIssues"]}
    assert "COMMENT_VAGUE_PHRASE" in codes


def test_endpoint_does_not_mutate_business_state(setup):
    """★ 关键：调用前后 Score / confirmed_rating / 草稿状态完全不变。"""
    db, _ = setup
    before_score = db.scalar(select(Score).where(Score.record_id == "R")).total
    before_rating = db.get(AssessmentRecord, "R").confirmed_rating
    before_draft_status = db.scalar(select(CommentDraft).where(CommentDraft.record_id == "R")).status
    before_draft_count = len(db.scalars(select(CommentDraft)).all())

    client = _client_for(db, "M")
    resp = client.get("/v1/semantic-qc/records/R")
    assert resp.status_code == 200
    db.expire_all()

    assert db.scalar(select(Score).where(Score.record_id == "R")).total == before_score
    assert db.get(AssessmentRecord, "R").confirmed_rating == before_rating
    assert db.scalar(select(CommentDraft).where(CommentDraft.record_id == "R")).status == before_draft_status
    assert len(db.scalars(select(CommentDraft)).all()) == before_draft_count


def test_response_carries_disclaimer(setup):
    """响应带「不构成评价结论」的免责说明。"""
    db, _ = setup
    client = _client_for(db, "M")
    data = client.get("/v1/semantic-qc/records/R").json()["data"]
    assert "不构成评价结论" in data["disclaimer"]
    assert "建议" in data["interviewOutline"]["disclaimer"]
