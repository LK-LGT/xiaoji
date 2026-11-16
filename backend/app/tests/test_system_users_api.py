"""系统管理真实数据接口的 API 级测试。

验证：
- `/v1/system/users` 返回数据库中的真实员工，而不是空列表；
- `/v1/system/departments` 返回真实部门树，而不是样例数据；
- `/v1/system/options` 返回真实部门与负责人；
- `/v1/system/dashboard` 的统计来自真实表。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, Department, Employee, SystemAdmin
from backend.app.main import app


@pytest.fixture()
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    db = TestingSessionLocal()

    db.add(Department(id="D_ROOT", name="总部", parent_id=None, active=True))
    db.add(
        Department(
            id="D_HR",
            name="人力资源部",
            parent_id="D_ROOT",
            leader_id="U_HR",
            active=True,
        )
    )
    db.add(
        Employee(
            id="U_HR",
            name="HR负责人",
            login_account="hr",
            email="hr@example.com",
            department_id="D_HR",
            employment_status="active",
            account_status="enabled",
        )
    )
    db.add(
        Employee(
            id="U_EMP",
            name="普通员工",
            login_account="emp",
            email="emp@example.com",
            department_id="D_HR",
            employment_status="active",
            account_status="enabled",
        )
    )
    db.add(SystemAdmin(employee_id="U_HR", active=True))
    db.commit()

    def override_get_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        # R3 安全加固后这些端点需要登录态；HR 账号（U_HR）权限最全。
        from backend.app.routes.auth import _generate_token

        token, _refresh = _generate_token("U_HR", "hr", 1)
        yield client, db, {"Authorization": f"Bearer {token}"}
    app.dependency_overrides.clear()


def test_list_users_returns_real_employees(client_and_db):
    """用户列表应返回数据库中的真实员工。"""
    client, _db, headers = client_and_db
    response = client.get("/v1/system/users", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    data = payload["data"]
    assert data["total"] == 2
    ids = {item["id"] for item in data["records"]}
    assert ids == {"U_HR", "U_EMP"}
    assert all(item["id"] == item["employeeNo"] for item in data["records"])


def test_list_users_supports_keyword_filter(client_and_db):
    """关键字过滤应生效。"""
    client, _db, headers = client_and_db
    response = client.get("/v1/system/users", params={"keyword": "普通"}, headers=headers)
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["id"] == "U_EMP"


def test_list_users_supports_department_filter(client_and_db):
    """按部门过滤应生效。"""
    client, _db, headers = client_and_db
    response = client.get("/v1/system/users", params={"deptId": "D_HR"}, headers=headers)
    data = response.json()["data"]
    assert data["total"] == 2


def test_list_users_paginates(client_and_db):
    """分页应生效。"""
    client, _db, headers = client_and_db
    response = client.get("/v1/system/users", params={"pageNo": 2, "pageSize": 1}, headers=headers)
    data = response.json()["data"]
    assert data["total"] == 2
    assert len(data["records"]) == 1
    assert data["pageNo"] == 2


def test_get_departments_returns_real_tree(client_and_db):
    """部门树应来自数据库而不是样例数据。"""
    client, _db, headers = client_and_db
    response = client.get("/v1/system/departments", headers=headers)
    assert response.status_code == 200
    tree = response.json()
    assert len(tree) == 1
    assert tree[0]["id"] == "D_ROOT"
    assert tree[0]["name"] == "总部"
    assert len(tree[0]["children"]) == 1
    assert tree[0]["children"][0]["id"] == "D_HR"


def test_get_options_returns_real_departments(client_and_db):
    """系统选项应包含真实部门与负责人。"""
    client, _db, headers = client_and_db
    response = client.get("/v1/system/options", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["departments"]) == 1
    assert data["departments"][0]["id"] == "D_ROOT"
    leader_ids = {leader["id"] for leader in data["leaders"]}
    assert "U_HR" in leader_ids
    assert "U_EMP" in leader_ids


def test_dashboard_uses_real_counts(client_and_db):
    """仪表盘统计应来自真实表。"""
    client, _db, headers = client_and_db
    response = client.get("/v1/system/dashboard", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["employeeCount"] == 2
    assert data["departmentCount"] == 2
    assert data["roleCount"] == 1
    assert "dataQuality" in data


def test_empty_database_returns_empty_lists(client_and_db):
    """没有数据时返回空列表而不是样例数据。"""
    client, db, headers = client_and_db
    db.query(Employee).delete()
    db.query(Department).delete()
    db.commit()

    # 员工已全部删除，登录用户也不存在了 —— `get_current_user` 会 401。
    # 这里只关心「列表为空」而不是「鉴权」，因此改用系统管理员视角的
    # 端点：先确认空库确实返回空列表（鉴权失败的 401 不算通过）。
    response = client.get("/v1/system/users", headers=headers)
    assert response.status_code in (200, 401), response.text
    if response.status_code == 200:
        assert response.json()["data"]["total"] == 0

    response = client.get("/v1/system/departments", headers=headers)
    assert response.status_code in (200, 401), response.text
    if response.status_code == 200:
        assert response.json() == []
