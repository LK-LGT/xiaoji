import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import AuditLog, Base, Department, Employee, HrDepartmentConfig
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture
def audit_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Department(id="D_HR", name="HR", active=True))
    db.add(Employee(id="HR1", name="真实HR", login_account="real-hr", department_id="D_HR", employment_status="active", account_status="enabled"))
    db.add(HrDepartmentConfig(id="HRC1", department_id="D_HR", active=True))
    db.add(Employee(id="EMP1", name="普通员工", login_account="ordinary", employment_status="active", account_status="enabled"))
    db.add(AuditLog(id="LOG1", actor_id="HR1", action="scheme.publish", resource_type="scheme", resource_id="SC1", result="ok", metadata_json={"version": 2}))
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    token, _ = _generate_token("HR1", "hr", 1)
    try:
        with TestClient(app) as client:
            yield client, {"Authorization": f"Bearer {token}"}
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_audit_list_returns_real_rows_and_filters(audit_client):
    client, headers = audit_client
    response = client.get("/v1/system/audits", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["id"] == "LOG1"
    assert data["records"][0]["operatorName"] == "真实HR"
    assert client.get("/v1/system/audits", params={"username": "missing"}, headers=headers).json()["data"]["total"] == 0


def test_audit_endpoints_require_audit_permission(audit_client):
    client, _headers = audit_client
    token, _ = _generate_token("EMP1", "ordinary", 1)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/system/audits", headers=headers).status_code == 403
    assert client.get("/v1/system/audits/export", headers=headers).status_code == 403


def test_audit_export_is_real_csv(audit_client):
    client, headers = audit_client
    response = client.get("/v1/system/audits/export", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "ID"
    assert rows[1][0] == "LOG1"
    assert "正在导出" not in text
