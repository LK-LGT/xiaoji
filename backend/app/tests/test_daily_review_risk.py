"""日计划风险标记（2026-11-27）。

原实现只回 ``RISK_MARKED`` 字符串，数据库没变；
现在写 ``day_plan.risk_level`` / ``review_status``，并校验主管范围。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, DayPlan, Employee
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def db():
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


@pytest.fixture()
def org(db):
    db.add_all(
        [
            Employee(
                id="E_MGR", name="主管", employment_status="active",
                account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_EMP", name="员工", manager_id="E_MGR", employment_status="active",
                account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_OTHER", name="外人", employment_status="active",
                account_status="enabled", token_version=1,
            ),
            DayPlan(
                id="DP1", owner_id="E_EMP", plan_date=date(2026, 11, 20),
                content="日计划", status="submitted",
            ),
        ]
    )
    db.commit()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _headers(employee_id: str) -> dict:
    token, _ = _generate_token(employee_id, "u", 1)
    return {"Authorization": f"Bearer {token}"}


def test_标记风险真的落库(client, db, org):
    resp = client.post(
        "/v1/leader/daily-reviews/DP1/risk",
        json={"riskLevel": "HIGH", "comment": "交付风险"},
        headers=_headers("E_MGR"),
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    plan = db.get(DayPlan, "DP1")
    assert plan.risk_level == "HIGH"
    assert plan.review_status == "RISK_MARKED"
    assert plan.leader_comment == "交付风险"


def test_外部门不能标风险(client, org):
    resp = client.post(
        "/v1/leader/daily-reviews/DP1/risk",
        json={"riskLevel": "HIGH"},
        headers=_headers("E_OTHER"),
    )
    assert resp.status_code == 403, resp.text


def test_风险等级非法返回422(client, org):
    resp = client.post(
        "/v1/leader/daily-reviews/DP1/risk",
        json={"riskLevel": "URGENT"},
        headers=_headers("E_MGR"),
    )
    assert resp.status_code == 422, resp.text
