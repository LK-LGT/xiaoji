"""🔒 **组织架构本地只读回归**（2026-11-27）。

⚠️ 原实现：POST/PUT/DELETE `/v1/system/departments` 返回结构完整的假对象
（临时 id / “已删除”），但**数据库里什么都没发生**。
组织架构的唯一事实源是飞书通讯录 ⇒ 本地增删改会与飞书分叉。

现在三个写端点统一 405，并给出明确原因；GET 仍返回真实部门树。
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
def admin_headers(db):
    db.add(Department(id="D_ROOT", name="总部", parent_id=None, active=True))
    db.add(
        Employee(
            id="E_ADMIN",
            name="管理员",
            department_id="D_ROOT",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.add(SystemAdmin(employee_id="E_ADMIN", active=True))
    db.commit()
    token, _ = _generate_token("E_ADMIN", "admin", 1)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def test_组织树仍可读(client, admin_headers):
    resp = client.get("/v1/system/departments", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["id"] == "D_ROOT"


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("post", "/v1/system/departments"),
        ("put", "/v1/system/departments/D_ROOT"),
        ("delete", "/v1/system/departments/D_ROOT"),
    ],
)
def test_组织写端点统一405(client, admin_headers, db, method, url):
    kwargs = {"headers": admin_headers}
    if method in {"post", "put"}:
        kwargs["json"] = {"name": "新部门"}
    resp = getattr(client, method)(url, **kwargs)
    assert resp.status_code == 405, resp.text
    assert "飞书" in resp.json()["detail"]
    # 数据不得被修改
    assert db.get(Department, "D_ROOT") is not None
