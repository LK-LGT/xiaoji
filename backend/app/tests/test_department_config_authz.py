"""🔴 **部门配置 / 计划调整 / 指标写入 / 用户列表的写权限回归**（2026-11-27）。

⚠️ **本文件锁住的缺陷**
------------------------------------------------------------------
2026-11-26 把「假成功」端点改成真实落库后，**写权限没有同步做实**：

- ``routes/department.py`` 的模板 / 验收标准 / 参考分规则 / 待办 / 导出
  只校验了「已登录」⇒ 任何员工都能改全公司配置；
- ``routes/leader.py`` 的计划调整列表不按汇报线过滤、详情/处理不校验
  「这条申请是不是我管的」⇒ 跨部门读取与篡改；
- ``routes/employee.py`` 发起调整时不校验计划归属 ⇒ 可引用他人计划；
- ``main.py`` 指标写入只校验登录 ⇒ 可间接改别人分数；
- ``routes/v1/system_users.py`` 用户列表对任意登录用户开放 ⇒ 全员手机号/邮箱泄漏。

⚠️ **断言策略**
每条都**同时**断言「无权时被拒」+「有权时能过」，
确保测的是权限差异，而不是端点坏了。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
    MonthPlan,
)
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
            Department(id="D_A", name="A 部门", parent_id="D_ROOT", leader_id="E_A_LEAD", active=True),
            Department(id="D_B", name="B 部门", parent_id="D_ROOT", leader_id="E_B_LEAD", active=True),
            Department(id="D_HR", name="人事行政部", parent_id="D_ROOT", active=True),
        ]
    )
    db.add_all(
        [
            Employee(
                id="E_A_LEAD", name="A 负责人", department_id="D_A",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_A_EMP", name="员工甲", department_id="D_A", manager_id="E_A_LEAD",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_B_LEAD", name="B 负责人", department_id="D_B",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_B_EMP", name="员工乙", department_id="D_B", manager_id="E_B_LEAD",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_HR", name="HR", department_id="D_HR",
                employment_status="active", account_status="enabled", token_version=1,
            ),
        ]
    )
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
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


# ---------------------------------------------------------------------------
# 1. 部门配置写权限
# ---------------------------------------------------------------------------


def test_普通员工不能创建部门模板(client, org):
    resp = client.post(
        "/v1/department/templates",
        json={"orgId": "D_A", "templateName": "T", "evidenceType": "FILE"},
        headers=_headers("E_A_EMP"),
    )
    assert resp.status_code == 403, resp.text


def test_本部门负责人可以创建模板(client, org):
    resp = client.post(
        "/v1/department/templates",
        json={"orgId": "D_A", "templateName": "T", "evidenceType": "FILE"},
        headers=_headers("E_A_LEAD"),
    )
    assert resp.status_code == 200, resp.text


def test_外部门负责人不能创建本部门模板(client, org):
    resp = client.post(
        "/v1/department/templates",
        json={"orgId": "D_A", "templateName": "T", "evidenceType": "FILE"},
        headers=_headers("E_B_LEAD"),
    )
    assert resp.status_code == 403, resp.text


def test_HR可以跨部门创建模板(client, org):
    resp = client.post(
        "/v1/department/templates",
        json={"orgId": "D_A", "templateName": "T", "evidenceType": "FILE"},
        headers=_headers("E_HR"),
    )
    assert resp.status_code == 200, resp.text


def test_外部门负责人不能停用本部门模板(client, org):
    created = client.post(
        "/v1/department/templates",
        json={"orgId": "D_A", "templateName": "T", "evidenceType": "FILE"},
        headers=_headers("E_A_LEAD"),
    ).json()["data"]

    resp = client.post(
        f"/v1/department/templates/{created['id']}/disable",
        headers=_headers("E_B_LEAD"),
    )
    assert resp.status_code == 403, resp.text


def test_导出产物只包含本部门员工(client, org):
    """部门负责人导出不能拿到其它部门员工（手机号/邮箱泄漏面）。"""
    resp = client.post(
        "/v1/department/export-tasks",
        json={"dimensionType": "EMPLOYEE", "periodType": "ALL", "formats": ["CSV"]},
        headers=_headers("E_A_LEAD"),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["id"]

    download = client.get(
        f"/v1/department/export-tasks/{task_id}/download",
        headers=_headers("E_A_LEAD"),
    )
    assert download.status_code == 200, download.text
    content = download.content.decode("utf-8-sig")
    assert "员工甲" in content
    assert "员工乙" not in content, "导出越界：拿到了其它部门员工"


def test_外部门负责人不能下载导出任务(client, org):
    created = client.post(
        "/v1/department/export-tasks",
        json={"dimensionType": "EMPLOYEE", "periodType": "ALL", "formats": ["CSV"]},
        headers=_headers("E_A_LEAD"),
    ).json()["data"]

    resp = client.get(
        f"/v1/department/export-tasks/{created['id']}/download",
        headers=_headers("E_B_LEAD"),
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 2. 计划调整
# ---------------------------------------------------------------------------


def _month_plan(db) -> MonthPlan:
    plan = MonthPlan(
        id="MP_A", owner_id="E_A_EMP", plan_month="2026-11",
        title="11 月计划", status="submitted",
    )
    db.add(plan)
    db.commit()
    return plan


def _create_adjustment(client) -> dict:
    resp = client.post(
        "/v1/employee/plan-adjustments",
        json={
            "planType": "MONTH",
            "planId": "MP_A",
            "adjustmentType": "PAUSE",
            "reason": "项目延期",
        },
        headers=_headers("E_A_EMP"),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_员工不能引用他人计划发起调整(client, db, org):
    _month_plan(db)
    resp = client.post(
        "/v1/employee/plan-adjustments",
        json={
            "planType": "MONTH",
            "planId": "MP_A",
            "adjustmentType": "PAUSE",
            "reason": "越权",
        },
        headers=_headers("E_B_EMP"),
    )
    assert resp.status_code == 422, resp.text


def test_本部门主管可以看并处理申请(client, db, org):
    _month_plan(db)
    created = _create_adjustment(client)

    detail = client.get(
        f"/v1/leader/plan-adjustments/{created['id']}",
        headers=_headers("E_A_LEAD"),
    )
    assert detail.status_code == 200, detail.text

    resp = client.post(
        f"/v1/leader/plan-adjustments/{created['id']}/process",
        json={"action": "PAUSE", "comment": "同意"},
        headers=_headers("E_A_LEAD"),
    )
    assert resp.status_code == 200, resp.text


def test_外部门主管不能看申请详情(client, db, org):
    _month_plan(db)
    created = _create_adjustment(client)
    resp = client.get(
        f"/v1/leader/plan-adjustments/{created['id']}",
        headers=_headers("E_B_LEAD"),
    )
    assert resp.status_code == 403, resp.text


def test_外部门主管不能处理申请(client, db, org):
    _month_plan(db)
    created = _create_adjustment(client)
    resp = client.post(
        f"/v1/leader/plan-adjustments/{created['id']}/process",
        json={"action": "PAUSE", "comment": "越权"},
        headers=_headers("E_B_LEAD"),
    )
    assert resp.status_code == 403, resp.text


def test_列表按汇报线过滤(client, db, org):
    _month_plan(db)
    created = _create_adjustment(client)

    own = client.get("/v1/leader/plan-adjustments", headers=_headers("E_A_LEAD")).json()["data"]
    assert [item["id"] for item in own] == [created["id"]]

    other = client.get("/v1/leader/plan-adjustments", headers=_headers("E_B_LEAD")).json()["data"]
    assert other == []


# ---------------------------------------------------------------------------
# 3. 指标写入
# ---------------------------------------------------------------------------


def test_无关主管不能写指标(client, db, org):
    db.add(
        AssessmentTask(
            id="T1", name="2026-11 考核", period="2026-11", status="draft",
        )
    )
    db.add(
        AssessmentRecord(
            id="R1", task_id="T1", employee_id="E_A_EMP", status="not_started",
        )
    )
    db.commit()

    resp = client.post(
        "/v1/assessment/records/R1/metrics",
        json={"metric_code": "plan_execution_rate", "value": 1.0, "source": "manager_input"},
        headers=_headers("E_B_LEAD"),
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 4. 用户列表
# ---------------------------------------------------------------------------


def test_普通员工不能拉全员通讯录(client, org):
    resp = client.get("/v1/system/users", headers=_headers("E_A_EMP"))
    assert resp.status_code == 403, resp.text


def test_HR可以拉用户列表(client, org):
    resp = client.get("/v1/system/users", headers=_headers("E_HR"))
    assert resp.status_code == 200, resp.text
