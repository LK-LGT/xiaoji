"""P0-4a：写端点的 **HTTP 层**集成测试。

⚠️ **为什么权限层测试还不够**
------------------------------
``test_qa_write_endpoints.py`` 测的是 ``can_write_score_of`` 这个**函数**。
但真正暴露在外的�� **HTTP 端点** —— 端点调没调用这个函数、
参数怎么传、错误码对不对，只有打真实的 HTTP 请求才能验。

⇒ 本文件用 FastAPI TestClient 打真实请求。

⚠️ **守的是「能查不能改」在 HTTP 层的落地**
------------------------------------------
核心场景：**同部门平级同事**能互相**查**，
但**不能**通过写端点改对方的数据。

⚠️ 尤其 ``POST /api/metric-values`` ——
   它是**算分的唯一入口**，能写就等于能改分。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
)
from backend.app.main import app


@pytest.fixture()
def db():
    """⚠️ 必须用 ``StaticPool`` —— 否则每次 ``session`` 拿到的是**新的连接**，
    而 SQLite ``:memory:`` 的库是**按连接**隔离的：
    建表的连接一关，表就没了 ⇒ 后续查询报 "no such table: employee"。

    ``StaticPool`` 让所有 session 复用同一个连接，内存库才真正共享。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def org(db):
    """组织：HR / 销售经理 / 销售甲 / 销售乙（甲乙互为平级）。

    ⚠️ 甲乙都汇报给 E_MGR ⇒ 同部门 + 同汇报线 = 同级。
    """
    for dept in (
        Department(id="D_HR", name="人力资源部", active=True),
        Department(id="D_SALES", name="销售部", active=True),
    ):
        db.add(dept)
    db.flush()

    rows = [
        Employee(id="E_HR", name="HR", department_id="D_HR",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_MGR", name="销售经理", department_id="D_SALES",
                 manager_id="E_HR", employment_status="active", account_status="enabled"),
        Employee(id="E_A", name="销售甲", department_id="D_SALES",
                 manager_id="E_MGR", employment_status="active", account_status="enabled"),
        Employee(id="E_B", name="销售乙", department_id="D_SALES",
                 manager_id="E_MGR", employment_status="active", account_status="enabled"),
    ]
    for row in rows:
        db.add(row)
    db.flush()

    for emp_id, dept_id in (
        ("E_HR", "D_HR"), ("E_MGR", "D_SALES"),
        ("E_A", "D_SALES"), ("E_B", "D_SALES"),
    ):
        db.add(EmployeeDepartment(employee_id=emp_id, department_id=dept_id, is_primary=True))
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()
    return {"hr": rows[0], "mgr": rows[1], "a": rows[2], "b": rows[3]}


@pytest.fixture()
def client(db, org):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def auth(employee_id: str) -> dict[str, str]:
    """按 employee_id 签发真实 Token。"""
    from backend.app.routes.auth import _generate_token

    real_token, _refresh = _generate_token(employee_id, employee_id, 1)
    return {"Authorization": f"Bearer {real_token}"}


# ---------------------------------------------------------------------------
# ★ 核心：平级能查不能写
# ---------------------------------------------------------------------------


def test_peer_cannot_write_metric_value(client, org) -> None:
    """★ 同部门平级**不能**给对方写实测值。

    ⚠️ 这是整个 P0 最关键的一条 ——
       实测值是算分的唯一入口，能写就等于能改分。
    """
    resp = client.post(
        "/api/metric-values",
        json={
            "employee_id": "E_B",
            "metric_code": "gmv",
            "period": "202611",
            "value": 99.0,
            "target": 100.0,
            "source": "manual",
        },
        headers=auth("E_A"),
    )
    assert resp.status_code == 403, resp.text
    assert "无权" in resp.json()["detail"]


def test_peer_cannot_write_attendance_anomaly(client, org) -> None:
    """★ 同部门平级不能给对方写考勤异常。"""
    resp = client.post(
        "/api/attendance/anomalies",
        json={
            "employee_id": "E_B",
            "period": "202611",
            "anomaly_type": "late",
            "count": 3,
        },
        headers=auth("E_A"),
    )
    assert resp.status_code == 403, resp.text
    assert "无权" in resp.json()["detail"]


def test_peer_cannot_write_own_peers_metric_value_reverse(client, org) -> None:
    """★ 反向同样拒绝 —— 互查是双向的，拦截也必须双向。"""
    resp = client.post(
        "/api/metric-values",
        json={
            "employee_id": "E_A",
            "metric_code": "gmv",
            "period": "202611",
            "value": 99.0,
            "source": "manual",
        },
        headers=auth("E_B"),
    )
    assert resp.status_code == 403, resp.text


def test_peer_cannot_write_to_manager(client, org) -> None:
    """★ 平级不能写上级的数据（上一级）。"""
    resp = client.post(
        "/api/metric-values",
        json={
            "employee_id": "E_MGR",
            "metric_code": "gmv",
            "period": "202611",
            "value": 99.0,
            "source": "manual",
        },
        headers=auth("E_A"),
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 既有写权限必须保留
# ---------------------------------------------------------------------------


def test_manager_can_write_direct_report_metric_value(client, org) -> None:
    """主管写下属实测值 —— 既有语义，必须保留。"""
    resp = client.post(
        "/api/metric-values",
        json={
            "employee_id": "E_A",
            "metric_code": "gmv",
            "period": "202611",
            "value": 88.0,
            "target": 100.0,
            "source": "manual",
        },
        headers=auth("E_MGR"),
    )
    assert resp.status_code == 200, resp.text


def test_hr_can_write_anyone_metric_value(client, org) -> None:
    """HR 写全员实测值 —— 既有语义，必须保留。"""
    resp = client.post(
        "/api/metric-values",
        json={
            "employee_id": "E_A",
            "metric_code": "gmv",
            "period": "202611",
            "value": 88.0,
            "source": "manual",
        },
        headers=auth("E_HR"),
    )
    assert resp.status_code == 200, resp.text


def test_manager_can_write_attendance_anomaly(client, org) -> None:
    """主管写下属考勤异常 —— 既有语义，必须保留。"""
    resp = client.post(
        "/api/attendance/anomalies",
        json={
            "employee_id": "E_A",
            "period": "202611",
            "anomaly_type": "late",
            "count": 2,
        },
        headers=auth("E_MGR"),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 匿名 / 未登录
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,payload",
    [
        (
            "/api/metric-values",
            {"employee_id": "E_B", "metric_code": "gmv",
             "period": "202611", "value": 1.0, "source": "manual"},
        ),
        (
            "/api/attendance/anomalies",
            {"employee_id": "E_B", "period": "202611",
             "anomaly_type": "late", "count": 1},
        ),
    ],
)
def test_write_endpoints_require_login(client, org, path, payload) -> None:
    """匿名写端点必须被拒。"""
    resp = client.post(path, json=payload)
    assert resp.status_code in (401, 403), resp.text


# ---------------------------------------------------------------------------
# ★ peer 读权限：权限层已放行，但**尚未**接到 HTTP 端点
# ---------------------------------------------------------------------------


def test_peer_cannot_read_other_record_via_http_yet(client, org, db) -> None:
    """⚠️ 记录当前状态：peer **读**权限尚未接到 HTTP 端点。

    ⇒ 现在 ``GET /api/records/{id}`` 对平级同事仍是 403。
    ⇒ 这是**故意的** —— P0-4b 完成后才会开放，届时本测试要改成断言 200。

    ⚠️ 本测试的作用是**锁住现状**：
    若哪天有人顺手把 ``db=db`` 加进了 ``GET /api/records/{id}``，
    而 P0-4b 的字段裁剪还没做好，就会泄露评语正文与证据。
    ⇒ 这个测试会失败，提醒裁剪必须先落地。
    """
    from backend.app.domain.models import AssessmentRecord, AssessmentTask

    task = AssessmentTask(
        id="T1", name="2026-11 月度考核", period="202611",
        department_id="D_SALES", status="published",
    )
    db.add(task)
    db.flush()
    db.add(AssessmentRecord(id="R_B", task_id="T1", employee_id="E_B"))
    db.commit()

    resp = client.get("/api/records/R_B", headers=auth("E_A"))
    assert resp.status_code == 403, (
        "peer 读权限不应在 P0-4b 之前到达 HTTP 端点 —— "
        "该端点会返回评语正文与证据原文，字段裁剪未落地前开放会泄露"
    )
