"""📊 **领导工作台 / 员工工作项 API 回归**（2026-11-27）。

⚠️ **本文件锁住的缺陷**
------------------------------------------------------------------
``/leader/org-tree``、``/leader/workbench``、``/leader/employees``、
``/leader/employees/{id}/work-items``、月计划详情以及部门侧同名接口
原先全部返回空数组 / 空对象：

- 主管首页显示“当前没有直属员工”，尽管确实有下属；
- 点进员工看不到任何月/周/日计划与成果。

本文件同时锁住**范围**：直属主管只能看直属下属；部门负责人只能看管理部门；
外部门一律 403。
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
    MonthPlan,
    MonthPlanItem,
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
            Employee(
                id="E_OTHER_EMP", name="员工乙", department_id="D_B", manager_id="E_OTHER",
                employment_status="active", account_status="enabled", token_version=1,
            ),
        ]
    )
    db.commit()


@pytest.fixture()
def plans(db, org):
    db.add(
        MonthPlan(
            id="MP1", owner_id="E_EMP", plan_month="2026-11", title="十一月计划",
            status="submitted", completion_rate=50.0,
        )
    )
    db.add_all(
        [
            MonthPlanItem(
                id="MI1", plan_id="MP1", task_name="任务一", task_content="内容一",
                deliverable="交付物一", quality="质量标准", performance_weight=0.6,
                estimated_value=10.0, estimated_start_date=date(2026, 11, 1),
                estimated_end_date=date(2026, 11, 10), status="submitted", sort_no=1,
            ),
            MonthPlanItem(
                id="MI2", plan_id="MP1", task_name="任务二", task_content="内容二",
                deliverable="交付物二", quality="质量标准", performance_weight=0.4,
                estimated_value=5.0, estimated_start_date=date(2026, 11, 11),
                estimated_end_date=date(2026, 11, 20), status="draft", sort_no=2,
            ),
        ]
    )
    db.add(
        WeekPlan(
            id="WP1", owner_id="E_EMP", title="第 45 周",
            week_start=date(2026, 11, 2), week_end=date(2026, 11, 8),
            status="SUBMITTED",
        )
    )
    db.add(
        WeekPlanItem(
            id="WI1", plan_id="WP1", content="周任务一", deliverable="周交付",
            planned_finish_date=date(2026, 11, 7), completion_status="DONE", sort_no=1,
        )
    )
    db.add(
        DayPlan(
            id="DP1", owner_id="E_EMP", plan_date=date(2026, 11, 3),
            content="日计划内容", status="submitted",
        )
    )
    db.add(DayPlanItem(id="DI1", plan_id="DP1", content="日任务一", sort_no=1))
    db.add(
        Result(
            id="R1", owner_id="E_EMP", plan_type="month", plan_id="MP1",
            plan_item_id="MI1", title="成果一", content="成果内容",
            deliverable="成果交付物", status="submitted",
            result_date=date(2026, 11, 10), completion_rate=80.0,
            evidence_status="COMPLETE",
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


def test_直属员工列表只含本人下属(client, plans):
    resp = client.get("/v1/leader/employees", headers=_headers("E_MGR"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert [row["ownerId"] for row in rows] == ["E_EMP"]
    row = rows[0]
    assert row["employeeName"] == "员工甲"
    assert row["monthPlanCount"] == 1
    assert row["weekPlanCount"] == 1
    assert row["dayPlanCount"] == 1
    assert row["resultCount"] == 1
    assert row["totalWorkCount"] == 4


def test_组织树返回真实部门(client, plans):
    resp = client.get("/v1/leader/org-tree", headers=_headers("E_MGR"))
    assert resp.status_code == 200, resp.text
    tree = resp.json()["data"]
    assert any(node["id"] == "D_A" for node in tree)


def test_工作台指标来自真实计数(client, plans):
    resp = client.get("/v1/leader/workbench", headers=_headers("E_MGR"))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    metrics = {item["code"]: item["value"] for item in data["metrics"]}
    assert metrics["teamSize"] == 1
    assert metrics["monthPlans"] == 1
    assert metrics["results"] == 1


def test_员工工作项包含月周日与成果(client, plans):
    resp = client.get(
        "/v1/leader/employees/E_EMP/work-items",
        headers=_headers("E_MGR"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 月计划 2 条 + 周计划 1 条 + 日计划 1 条 + 成果 1 条
    assert data["total"] == 5
    types = {item["workType"] for item in data["items"]}
    assert types == {"MONTH_PLAN", "WEEK_PLAN", "DAY_PLAN", "RESULT"}
    month_rows = [item for item in data["items"] if item["workType"] == "MONTH_PLAN"]
    assert {row["title"] for row in month_rows} == {"任务一", "任务二"}
    assert data["employee"]["ownerId"] == "E_EMP"


def test_工作项可按类型过滤并分页(client, plans):
    only_result = client.get(
        "/v1/leader/employees/E_EMP/work-items",
        params={"workType": "RESULT"},
        headers=_headers("E_MGR"),
    ).json()["data"]
    assert only_result["total"] == 1
    assert only_result["items"][0]["title"] == "成果一"

    page = client.get(
        "/v1/leader/employees/E_EMP/work-items",
        params={"pageNo": 1, "pageSize": 2},
        headers=_headers("E_MGR"),
    ).json()["data"]
    assert page["total"] == 5
    assert len(page["items"]) == 2


def test_月计划详情返回真实任务(client, plans):
    resp = client.get(
        "/v1/leader/employees/E_EMP/month-plans/MP1",
        headers=_headers("E_MGR"),
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    assert detail["id"] == "MP1"
    assert detail["planMonth"] == "2026-11"
    assert [task["taskName"] for task in detail["tasks"]] == ["任务一", "任务二"]


def test_外部门主管不能看员工工作项(client, plans):
    resp = client.get(
        "/v1/leader/employees/E_OTHER_EMP/work-items",
        headers=_headers("E_MGR"),
    )
    assert resp.status_code == 403, resp.text


def test_部门负责人可以看本部门员工(client, plans):
    resp = client.get(
        "/v1/department/employees/E_EMP/work-items",
        headers=_headers("E_LEAD"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["total"] == 5


def test_外部门负责人不能看本部门员工(client, plans):
    resp = client.get(
        "/v1/department/employees/E_EMP/work-items",
        headers=_headers("E_OTHER"),
    )
    assert resp.status_code == 403, resp.text
