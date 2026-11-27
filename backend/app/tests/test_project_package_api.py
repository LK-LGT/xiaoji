"""📦 **领导端项目日/周/成果包回归**（2026-11-27）。

⚠️ 原实现：列表恒 ``[]``、详情空对象、Excel 恒 404，而前端有完整页签。
本文件锁住：真实聚合 + 部门范围 + CSV 导出。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    DayPlan,
    DayPlanItem,
    Department,
    Employee,
    WeekPlan,
    WeekPlanItem,
)
from backend.app.domain.result_models import Result
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
            Department(id="D_ROOT", name="总部", parent_id=None, active=True),
            Department(id="D_A", name="A 部门", parent_id="D_ROOT", leader_id="E_LEAD", active=True),
            Department(id="D_B", name="B 部门", parent_id="D_ROOT", leader_id="E_OTHER", active=True),
        ]
    )
    db.add_all(
        [
            Employee(
                id="E_LEAD", name="A 负责人", department_id="D_A",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_MGR", name="主管", department_id="D_A",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_EMP", name="员工甲", department_id="D_A", manager_id="E_MGR",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_OTHER", name="B 负责人", department_id="D_B",
                employment_status="active", account_status="enabled", token_version=1,
            ),
        ]
    )
    db.commit()


@pytest.fixture()
def plan_data(db, org):
    db.add(
        DayPlan(
            id="DP1", owner_id="E_EMP", plan_date=date(2026, 11, 3),
            content="日计划内容", status="submitted", submitted_at=None,
        )
    )
    db.add(DayPlanItem(id="DI1", plan_id="DP1", content="日任务一", sort_no=1))
    db.add(
        WeekPlan(
            id="WP1", owner_id="E_EMP", title="第 45 周",
            week_start=date(2026, 11, 2), week_end=date(2026, 11, 8),
            status="APPROVED",
        )
    )
    db.add(
        WeekPlanItem(
            id="WI1", plan_id="WP1", content="周任务一", deliverable="周交付",
            quality="周质量", planned_finish_date=date(2026, 11, 7),
            completion_status="DONE", sort_no=1,
        )
    )
    db.add(
        Result(
            id="R1", owner_id="E_EMP", plan_type="week", plan_id="WP1",
            title="周成果一", content="周成果内容", deliverable="成果交付",
            remark="备注一", status="confirmed", result_date=date(2026, 11, 8),
            completion_status="DONE",
        )
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


def test_日计划包列表与详情(client, plan_data):
    listing = client.get(
        "/v1/leader/project-day-plan-packages",
        params={"planDate": "2026-11-03"},
        headers=_headers("E_MGR"),
    )
    assert listing.status_code == 200, listing.text
    packages = listing.json()["data"]
    assert len(packages) == 1
    package = packages[0]
    assert package["groupId"] == "D_A"
    assert package["totalMemberCount"] == 1
    assert package["submittedCount"] == 1
    assert package["status"] == "COLLECTING"

    detail = client.get(
        "/v1/leader/project-day-plan-packages/D_A",
        params={"planDate": "2026-11-03"},
        headers=_headers("E_MGR"),
    )
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["rows"][0]["workContent"] == "日计划内容"


def test_周计划包列表与详情(client, plan_data):
    listing = client.get(
        "/v1/leader/project-week-plan-packages",
        params={"weekStart": "2026-11-02"},
        headers=_headers("E_MGR"),
    )
    assert listing.status_code == 200, listing.text
    package = listing.json()["data"][0]
    assert package["groupId"] == "D_A"
    assert package["approvedCount"] == 1
    assert package["totalMemberCount"] == 1
    assert package["status"] == "OPEN"

    detail = client.get(
        "/v1/leader/project-week-plan-packages/D_A",
        params={"weekStart": "2026-11-02"},
        headers=_headers("E_MGR"),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["rows"][0]["workContent"] == "周任务一"


def test_周成果包列表与详情(client, plan_data):
    listing = client.get(
        "/v1/leader/project-week-result-packages",
        params={"weekStart": "2026-11-02"},
        headers=_headers("E_MGR"),
    )
    assert listing.status_code == 200, listing.text
    package = listing.json()["data"][0]
    assert package["groupId"] == "D_A"
    assert package["approvedCount"] == 1
    assert package["totalMemberCount"] == 1

    detail = client.get(
        "/v1/leader/project-week-result-packages/D_A",
        params={"weekStart": "2026-11-02"},
        headers=_headers("E_MGR"),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["rows"][0]["workContent"] == "周成果一"


def test_Excel导出返回CSV(client, plan_data):
    resp = client.get(
        "/v1/leader/project-week-plan-packages/D_A/excel",
        params={"weekStart": "2026-11-02"},
        headers=_headers("E_MGR"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")
    assert "姓名" in text
    assert "周任务一" in text

    result_resp = client.get(
        "/v1/leader/project-week-result-packages/D_A/excel",
        params={"weekStart": "2026-11-02"},
        headers=_headers("E_MGR"),
    )
    assert result_resp.status_code == 200, result_resp.text
    assert "周成果一" in result_resp.content.decode("utf-8-sig")


def test_外部门看不到本部门项目包(client, plan_data):
    listing = client.get(
        "/v1/leader/project-day-plan-packages",
        params={"planDate": "2026-11-03"},
        headers=_headers("E_OTHER"),
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"] == []

    detail = client.get(
        "/v1/leader/project-day-plan-packages/D_A",
        params={"planDate": "2026-11-03"},
        headers=_headers("E_OTHER"),
    )
    assert detail.status_code == 403, detail.text
