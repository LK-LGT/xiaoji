"""阶段二：考勤 API 集成测试。

覆盖权限矩阵（HR / 管理员 / 主管 / 普通员工）、红线拦截、参数校验。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
    SystemAdmin,
)
from backend.app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def org(db):
    """搭建最小组织：HR 部门 + 管理员 + 部门负责人 + 普通员工。

    注意：普通员工必须放在**非** HR 部门，否则按阶段一「HR 按部门自动判断」
    的规则，他会被识别成 HR。
    """
    hr_dept = Department(id="D_HR", name="人力资源部", active=True)
    biz_dept = Department(id="D_BIZ", name="业务部", active=True)
    db.add(hr_dept)
    db.add(biz_dept)
    db.flush()

    hr = Employee(
        id="E_HR",
        name="HR负责人",
        department_id="D_HR",
        employment_status="active",
        account_status="enabled",
    )
    admin = Employee(
        id="E_ADMIN",
        name="系统管理员",
        employment_status="active",
        account_status="enabled",
    )
    leader = Employee(
        id="E_LEADER",
        name="部门负责人",
        department_id="D_BIZ",
        employment_status="active",
        account_status="enabled",
    )
    emp = Employee(
        id="E_EMP",
        name="普通员工",
        department_id="D_BIZ",
        manager_id="E_LEADER",
        employment_status="active",
        account_status="enabled",
    )
    for row in (hr, admin, leader, emp):
        db.add(row)
    db.flush()

    db.add(EmployeeDepartment(employee_id="E_HR", department_id="D_HR", is_primary=True))
    db.add(
        EmployeeDepartment(employee_id="E_EMP", department_id="D_BIZ", is_primary=True)
    )
    db.add(
        EmployeeDepartment(employee_id="E_LEADER", department_id="D_BIZ", is_primary=True)
    )
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.add(SystemAdmin(employee_id="E_ADMIN", active=True))
    db.flush()
    return {"hr": hr, "admin": admin, "leader": leader, "emp": emp}


@pytest.fixture()
def client(db, org, monkeypatch):
    # 用 FastAPI 官方的依赖覆盖机制，把所有 get_db 依赖指向内存库
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    # 认证走 mock token 分支，便于直接构造身份
    monkeypatch.setattr("backend.app.core.config.settings.secure_auth", False)
    from backend.app.dependencies import MOCK_TOKENS

    MOCK_TOKENS.clear()
    MOCK_TOKENS.update(
        {
            "hr_token_001": {
                "employee_id": "E_HR",
                "name": "HR负责人",
                "role": "hr",
                "position": "HR经理",
                "manager_id": None,
                "department_id": "D_HR",
            },
            "admin_token_001": {
                "employee_id": "E_ADMIN",
                "name": "系统管理员",
                "role": "hr",
                "position": "管理员",
                "manager_id": None,
                "department_id": "D_HR",
            },
            "emp_token_001": {
                "employee_id": "E_EMP",
                "name": "普通员工",
                "role": "ordinary_employee",
                "position": "员工",
                "manager_id": "E_LEADER",
                "department_id": "D_BIZ",
            },
        }
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 异常类型
# ---------------------------------------------------------------------------


def test_anomaly_types_lists_all_six(client):
    resp = client.get("/v1/attendance/anomaly-types", headers=auth("hr_token_001"))
    assert resp.status_code == 200
    values = [item["value"] for item in resp.json()["data"]]
    assert values == [
        "late",
        "early_leave",
        "missing_punch",
        "leave",
        "overtime",
        "outside",
    ]


def test_anomaly_types_requires_auth(client):
    resp = client.get("/v1/attendance/anomaly-types")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 权限矩阵
# ---------------------------------------------------------------------------


def test_employee_can_view_own_attendance(client):
    resp = client.get(
        "/v1/attendance/summary",
        params={
            "employee_id": "E_EMP",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
        headers=auth("emp_token_001"),
    )
    assert resp.status_code == 200


def test_employee_cannot_view_others_attendance(client):
    """普通员工查看他人考勤 → 403。"""
    resp = client.get(
        "/v1/attendance/summary",
        params={
            "employee_id": "E_HR",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
        headers=auth("emp_token_001"),
    )
    assert resp.status_code == 403


def test_hr_can_view_any_employee_attendance(client):
    resp = client.get(
        "/v1/attendance/summary",
        params={
            "employee_id": "E_EMP",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
        headers=auth("hr_token_001"),
    )
    assert resp.status_code == 200


def test_employee_cannot_trigger_sync(client):
    """普通员工无权触发考勤同步 → 403。"""
    resp = client.post(
        "/v1/attendance/sync",
        json={"start_date": "2026-11-01", "end_date": "2026-11-02"},
        headers=auth("emp_token_001"),
    )
    assert resp.status_code == 403


def test_hr_can_trigger_sync(client, monkeypatch):
    """HR 可以触发同步（用桩替换飞书调用）。"""
    from backend.app.routes.v1 import attendance as attendance_routes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_attendance_tasks(self, *args, **kwargs):
            return []

        def close(self):
            pass

    monkeypatch.setattr(attendance_routes, "_build_client", lambda: FakeClient())

    resp = client.post(
        "/v1/attendance/sync",
        json={"start_date": "2026-11-01", "end_date": "2026-11-02"},
        headers=auth("hr_token_001"),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["errors"] == []


def test_admin_can_trigger_sync(client, monkeypatch):
    from backend.app.routes.v1 import attendance as attendance_routes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_attendance_tasks(self, *args, **kwargs):
            return []

        def close(self):
            pass

    monkeypatch.setattr(attendance_routes, "_build_client", lambda: FakeClient())

    resp = client.post(
        "/v1/attendance/sync",
        json={"start_date": "2026-11-01", "end_date": "2026-11-02"},
        headers=auth("admin_token_001"),
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------


def test_sync_rejects_bad_date_format(client):
    resp = client.post(
        "/v1/attendance/sync",
        json={"start_date": "2026/11/01", "end_date": "2026-11-02"},
        headers=auth("hr_token_001"),
    )
    assert resp.status_code in (400, 422)


def test_sync_rejects_reversed_dates(client):
    resp = client.post(
        "/v1/attendance/sync",
        json={"start_date": "2026-11-30", "end_date": "2026-11-01"},
        headers=auth("hr_token_001"),
    )
    assert resp.status_code in (400, 422)


def test_summary_rejects_bad_date_format(client):
    resp = client.get(
        "/v1/attendance/summary",
        params={
            "employee_id": "E_EMP",
            "start_date": "not-a-date",
            "end_date": "2026-11-30",
        },
        headers=auth("emp_token_001"),
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 红线
# ---------------------------------------------------------------------------


def test_attendance_source_rejected_by_metric_value_endpoint(client):
    """红线：考勤来源写不进算分集。"""
    resp = client.post(
        "/api/metric-values",
        json={
            "employee_id": "E_EMP",
            "metric_code": "attendance_days",
            "period": "2026-11",
            "value": 1.0,
            "source": "attendance",
        },
    )
    assert resp.status_code == 400
    assert "不得进入算分集" in resp.json()["detail"]


def test_evidence_endpoint_returns_reference_only(client):
    resp = client.post(
        "/api/attendance/anomalies",
        json={
            "employee_id": "E_EMP",
            "period": "2026-11",
            "anomaly_type": "late",
            "count": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"] == "reference_only"
    assert body["disclaimer"] == "考勤异常不自动扣分，仅作参考"


def test_evidence_endpoint_rejects_unknown_anomaly_type(client):
    resp = client.post(
        "/api/attendance/anomalies",
        json={
            "employee_id": "E_EMP",
            "period": "2026-11",
            "anomaly_type": "not_a_type",
            "count": 1,
        },
    )
    assert resp.status_code == 400


def test_anomalies_endpoint_lists_evidence(client):
    client.post(
        "/api/attendance/anomalies",
        json={
            "employee_id": "E_EMP",
            "period": "2026-11",
            "anomaly_type": "late",
            "count": 3,
        },
    )
    resp = client.get(
        "/v1/attendance/anomalies",
        params={"employee_id": "E_EMP", "period": "2026-11"},
        headers=auth("emp_token_001"),
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["usage"] == "reference_only"
    assert len(body["anomalies"]) == 1
    assert body["anomalies"][0]["count"] == 3
    assert body["anomalies"][0]["disclaimer"] == "考勤异常不自动扣分，仅作参考"


def test_adjustment_endpoint_is_reference_only(client):
    resp = client.get(
        "/v1/attendance/adjustment",
        params={
            "employee_id": "E_EMP",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
        headers=auth("emp_token_001"),
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["usage"] == "reference_only"
    assert "需人工确认" in body["disclaimer"]
