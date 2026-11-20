"""L2 工具权限、规则一致性、证据周期和只读保证。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import (
    AiToolCall,
    AssessmentRecord,
    AssessmentTask,
    Base,
    ConfigSnapshot,
    DataSourcePolicy,
    Department,
    Employee,
    Evidence,
    MetricValue,
    Score,
)
from backend.app.routes.agent_tools import router
from backend.app.services import agent_tool_service, review_service
from backend.app.services.agent_tool_token import issue_agent_tool_token

TOOLS = ["get_metric_values", "get_evidence", "get_department_stats"]


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_secret", "business-tools-independent-test-secret")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Department(id="D", name="部门"))
        db.flush()
        db.add(Employee(id="E", name="员工", department_id="D", employment_status="active", account_status="enabled"))
        db.add(AssessmentTask(id="T", name="任务", period="202611"))
        db.flush()
        snapshot = ConfigSnapshot(id="S", task_id="T", metrics_json=[{"code": "GMV", "name": "销售额"}],
            weights_json=[{"metric_code": "GMV", "weight": 100}],
            rubric_json=[{"metric_code": "GMV", "levels": [{"level": "A", "min_ratio": 1, "max_ratio": None}]}])
        db.add(snapshot)
        db.flush()
        db.get(AssessmentTask, "T").snapshot_id = "S"
        db.add(AssessmentRecord(id="R", task_id="T", employee_id="E"))
        db.add(MetricValue(employee_id="E", metric_code="GMV", period="202611", value=120, target=100, source="manual"))
        # P1-5：白名单默认不信任，显式放行 manual / attendance 两个源
        db.add(DataSourcePolicy(source_code="manual", display_name="人工录入",
                                allowed=True, allowed_roles="hr,manager,employee"))
        db.add(DataSourcePolicy(source_code="attendance", display_name="考勤系统",
                                allowed=True, allowed_roles="hr,manager,department_leader,employee"))
        db.commit()
        app = FastAPI()
        app.include_router(router)
        def dependency():
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
        app.dependency_overrides[get_db] = dependency
        with TestClient(app) as client:
            yield db, client
    engine.dispose()


def headers(scope="self", allow=None, task_id="T"):
    token = issue_agent_tool_token(employee_id="E", task_id=task_id, allow=allow or TOOLS, scope=scope)
    return {"Authorization": f"Bearer {token}"}


def test_metrics_match_scoring_and_never_persist_score(setup):
    db, client = setup
    expected = review_service.compute_score(db, record_id="R")
    response = client.post("/v1/agent-tools/get_metric_values", json={"period": "2026-11"}, headers=headers())
    assert response.status_code == 200
    item = response.json()["data"]["metrics"][0]
    assert item["ratio"] == expected.per_metric[0]["ratio"] == 1.2
    assert item["level"] == expected.per_metric[0]["level"] == "A"
    assert item["level_scale"][0]["level"] == "A"
    assert "total" not in response.json()["data"] and "score" not in item
    assert db.scalars(select(Score)).all() == []
    assert db.get(AssessmentRecord, "R").status == "not_started"
    assert db.scalars(select(AiToolCall)).one().ok


@pytest.mark.parametrize("body,status", [
    ({"period": "202610"}, 400), ({"period": "202613"}, 400),
    ({"period": "202611", "employee_id": "OTHER"}, 422),
])
def test_period_and_identity_boundaries(setup, body, status):
    db, client = setup
    response = client.post("/v1/agent-tools/get_metric_values", json=body, headers=headers())
    assert response.status_code == status
    if status != 422:
        audit = db.scalars(select(AiToolCall)).one()
        assert not audit.ok and audit.error == "PERIOD_MISMATCH"


def test_failed_calls_audited_after_request_commit(setup, monkeypatch):
    db, client = setup
    response = client.post("/v1/agent-tools/get_metric_values", json={"period": "202611"}, headers=headers(allow=["ping"]))
    assert response.status_code == 403
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive payload")
    monkeypatch.setattr(agent_tool_service, "load_metric_values", fail)
    response = client.post("/v1/agent-tools/get_metric_values", json={"period": "202611"}, headers=headers())
    assert response.status_code == 500 and "sensitive" not in response.text
    assert [x.error for x in db.scalars(select(AiToolCall).order_by(AiToolCall.created_at))] == ["TOOL_FORBIDDEN", "TOOL_EXECUTION_FAILED"]


def test_evidence_task_scope_visibility_and_redaction(setup):
    db, client = setup
    for id, overrides in [
        ("valid", {}), ("private", {"visibility": "private_draft"}),
        ("hr", {"visibility": "hr_only"}), ("rejected", {"status": "rejected"}),
        ("unbound", {"record_id": None}),
        ("current", {"record_id": None, "source": "attendance", "source_ref": "E|2026-11|attendance|late", "usage": "reference_only"}),
        ("old", {"record_id": None, "source": "attendance", "source_ref": "E|2026-10|attendance|late"}),
        ("wrong_attendance", {"source": "attendance", "source_ref": "E|2026-10|attendance|late"}),
    ]:
        db.add(Evidence(**{"id": id, "employee_id": "E", "record_id": "R", "kind": "manual",
                          "summary": "联系13812345678 test@example.com", "source": "manual", **overrides}))
    db.commit()
    response = client.post("/v1/agent-tools/get_evidence", json={}, headers=headers())
    rows = response.json()["data"]["evidence"]
    assert {r["id"] for r in rows} == {"valid", "current"}
    assert "13812345678" not in response.text and "test@example.com" not in response.text
    assert next(r for r in rows if r["id"] == "current")["disclaimer"] == "考勤异常不自动扣分，仅作参考"
    response = client.post("/v1/agent-tools/get_evidence", json={"usage": "reference_only"}, headers=headers())
    assert [r["id"] for r in response.json()["data"]["evidence"]] == ["current"]


def test_department_scope_and_current_role_rechecked(setup):
    db, client = setup
    assert client.post("/v1/agent-tools/get_department_stats", json={}, headers=headers()).status_code == 403
    assert client.post("/v1/agent-tools/get_department_stats", json={}, headers=headers(scope="department")).status_code == 403
    db.get(Department, "D").leader_id = "E"
    db.commit()
    assert client.post("/v1/agent-tools/get_department_stats", json={}, headers=headers(scope="department")).status_code == 200
    db.add(Department(id="OTHER", name="其他部门"))
    db.flush()
    db.get(AssessmentTask, "T").department_id = "OTHER"
    db.commit()
    assert client.post("/v1/agent-tools/get_department_stats", json={}, headers=headers(scope="department")).status_code == 403
    db.get(AssessmentTask, "T").department_id = None
    db.get(Department, "D").leader_id = None
    db.commit()
    assert client.post("/v1/agent-tools/get_department_stats", json={}, headers=headers(scope="department")).status_code == 403


def test_actual_scored_count_k_anonymity(setup):
    db, _ = setup
    for index in range(5):
        db.add(Employee(id=f"P{index}", name="成员", department_id="D", employment_status="active"))
    db.add(Score(record_id="R", total=80, per_metric_json=[], trace_json={}))
    db.commit()
    result = agent_tool_service.load_department_stats(db, department_id="D", task_id="T")
    assert result["total_employees"] == 6 and result["scored_employees"] == 1
    assert result["average_score"] is None


def test_no_snapshot_and_missing_task(setup):
    db, client = setup
    db.get(AssessmentTask, "T").snapshot_id = None
    db.commit()
    response = client.post("/v1/agent-tools/get_metric_values", json={"period": "202611"}, headers=headers())
    assert response.json()["data"]["available"] is False
    assert client.post("/v1/agent-tools/get_evidence", json={}, headers=headers(task_id="missing")).status_code == 404
