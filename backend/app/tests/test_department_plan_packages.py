from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, DayPlan, Department, Employee, MonthPlan, MonthPlanItem, WeekPlan
from backend.app.domain.plan_models import PlanApproval
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture
def package_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Department(id="D1", name="研发部", active=True, leader_id="M1"))
    db.add(Employee(id="M1", name="主管", department_id="D1", employment_status="active", account_status="enabled"))
    db.add(Employee(id="E1", name="员工甲", login_account="e1", department_id="D1", manager_id="M1", employment_status="active", account_status="enabled"))
    db.add(Employee(id="X1", name="外部门员工", department_id="D2", manager_id="M1", employment_status="active", account_status="enabled"))
    plan = MonthPlan(id="P1", owner_id="E1", plan_month="2026-11", title="真实计划", status="approved", version_no=2, approved_at=datetime(2026, 11, 10))
    plan.items.append(MonthPlanItem(id="I1", task_name="真实任务", task_content="交付说明", deliverable="交付物", quality="质量标准", performance_weight=50, estimated_value=12.5, estimated_start_date=date(2026, 11, 1), estimated_end_date=date(2026, 11, 30)))
    db.add(plan)
    db.add(PlanApproval(id="A1", plan_type="month", plan_id="P1", actor_id="M1", action="APPROVE", from_status="submitted", to_status="approved", comment="已核验", dedup_key="test-approval"))
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    token, _ = _generate_token("M1", "manager", 1)
    try:
        with TestClient(app) as client:
            yield client, {"Authorization": f"Bearer {token}"}
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_employee_summaries_return_real_zero_counts(package_client):
    client, headers = package_client
    response = client.get("/v1/department/employees", params={"org_id": "D1"}, headers=headers)
    assert response.status_code == 200, response.text
    rows = {row["ownerId"]: row for row in response.json()}
    assert rows["E1"]["monthPlanCount"] == 1
    assert rows["E1"]["totalWorkCount"] == 1
    assert rows["M1"]["monthPlanCount"] == 0
    assert rows["M1"]["totalWorkCount"] == 0
    assert rows["M1"]["latestActivityAt"] is None


def test_month_package_uses_real_data_and_scopes_by_manager(package_client):
    client, headers = package_client
    response = client.get("/v1/department/project-month-plan-packages", params={"planMonth": "202611"}, headers=headers)
    assert response.status_code == 200, response.text
    packages = response.json()
    assert len(packages) == 1
    assert packages[0]["groupId"] == "D1"
    assert packages[0]["groupName"] == "研发部"
    assert packages[0]["totalMemberCount"] == 1
    assert packages[0]["approvedCount"] == 1
    assert packages[0]["contentAvailable"] is True


def test_month_package_detail_is_real_and_forbidden_outside_scope(package_client):
    client, headers = package_client
    response = client.get("/v1/department/project-month-plan-packages/D1", params={"planMonth": "2026-11"}, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["plans"][0]["id"] == "P1"
    assert data["plans"][0]["items"][0]["taskName"] == "真实任务"
    assert data["plans"][0]["leaderComment"] == "已核验"
    assert client.get("/v1/department/project-month-plan-packages/D2", params={"planMonth": "2026-11"}, headers=headers).status_code == 404
