"""飞书管理接口的 API 级鉴权测试。

覆盖：未认证（401）、非管理员访问（403）、管理员正常访问（200）、
身份绑定冲突（409）、管理员权限边界。
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
    FeishuIdentity,
    SystemAdmin,
)
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def client_and_db():
    """独立内存数据库 + TestClient。"""
    # StaticPool 保证内存 SQLite 复用同一连接，否则每个 session
    # 拿到的是不同的空数据库。
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    db = TestingSessionLocal()

    db.add(Department(id="D1", name="研发部", active=True))
    db.add_all(
        [
            Employee(
                id="ADMIN001",
                name="管理员",
                login_account="admin001",
                employment_status="active",
                account_status="enabled",
                token_version=1,
            ),
            Employee(
                id="EMP001",
                name="普通员工",
                login_account="emp001",
                employment_status="active",
                account_status="enabled",
                token_version=1,
            ),
        ]
    )
    db.add(SystemAdmin(employee_id="ADMIN001", active=True, granted_by="seed"))
    db.commit()

    def override_get_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, db
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _token(employee_id: str, username: str, token_version: int = 1) -> str:
    access_token, _ = _generate_token(employee_id, username, token_version)
    return access_token


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token('ADMIN001', 'admin001')}"}


def _emp_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token('EMP001', 'emp001')}"}


def _call(client, method: str, path: str, *, headers=None, json_body=None):
    """统一发起请求：GET 不带 body，POST 带 JSON。"""
    if method == "get":
        return client.get(path, headers=headers)
    return client.post(path, json=json_body or {}, headers=headers)


# ──────────────────────────────────────────────────────────────
# 未认证
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/v1/feishu/sync-organization"),
        ("post", "/v1/feishu/sync-organization/retry"),
        ("post", "/v1/feishu/sync-organization/recover-stale"),
        ("get", "/v1/feishu/sync-organization/batches"),
        ("get", "/v1/feishu/sync-organization/errors"),
        ("get", "/v1/feishu/sync-organization/audits"),
        ("get", "/v1/feishu/identities/unbound"),
        ("get", "/v1/feishu/identities/conflicts"),
        ("post", "/v1/feishu/identities/bind"),
        ("get", "/v1/feishu/identities/audits"),
    ],
)
def test_feishu_admin_endpoints_require_authentication(client_and_db, method, path):
    """所有管理员接口在无 Authorization 头时必须返回 401。"""
    client, _ = client_and_db
    resp = _call(client, method, path)
    assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"


def test_invalid_token_is_rejected(client_and_db):
    """伪造的 Token 必须被拒绝。"""
    client, _ = client_and_db
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": "Bearer forged.token.value"},
    )
    assert resp.status_code == 401


def test_malformed_authorization_header_is_rejected(client_and_db):
    """Authorization 头格式错误必须被拒绝。"""
    client, _ = client_and_db
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": "not-a-bearer-token"},
    )
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────
# 非管理员访问
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/v1/feishu/sync-organization"),
        ("post", "/v1/feishu/sync-organization/retry"),
        ("post", "/v1/feishu/sync-organization/recover-stale"),
        ("get", "/v1/feishu/sync-organization/batches"),
        ("get", "/v1/feishu/sync-organization/errors"),
        ("get", "/v1/feishu/sync-organization/audits"),
        ("get", "/v1/feishu/identities/unbound"),
        ("get", "/v1/feishu/identities/conflicts"),
        ("get", "/v1/feishu/identities/audits"),
    ],
)
def test_feishu_admin_endpoints_reject_non_admin(client_and_db, method, path):
    """普通员工调用管理员接口必须返回 403。"""
    client, _ = client_and_db
    resp = _call(client, method, path, headers=_emp_headers())
    assert resp.status_code == 403, f"{method.upper()} {path} -> {resp.status_code}"


def test_bind_identity_rejects_non_admin(client_and_db):
    """普通员工不能绑定身份。"""
    client, _ = client_and_db
    resp = client.post(
        "/v1/feishu/identities/bind",
        json={
            "employee_id": "EMP001",
            "identity_type": "user_id",
            "identity_value": "u_new",
            "reason": "test",
        },
        headers=_emp_headers(),
    )
    assert resp.status_code == 403


def test_disable_account_rejects_non_admin(client_and_db):
    """普通员工不能禁用他人账号。"""
    client, db = client_and_db
    identity = FeishuIdentity(
        id="ID1",
        employee_id="EMP001",
        identity_type="user_id",
        identity_value="u_emp",
        active=True,
    )
    db.add(identity)
    db.commit()

    resp = client.post(
        "/v1/feishu/identities/ID1/disable-account",
        json={"reason": "test"},
        headers=_emp_headers(),
    )
    assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────
# 管理员正常访问
# ──────────────────────────────────────────────────────────────


def test_admin_can_list_batches(client_and_db):
    """管理员可以查询同步批次。"""
    client, _ = client_and_db
    resp = client.get(
        "/v1/feishu/sync-organization/batches", headers=_admin_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "records" in body["data"]


def test_admin_can_list_unbound_identities(client_and_db):
    """管理员可以查询未绑定员工。"""
    client, _ = client_and_db
    resp = client.get("/v1/feishu/identities/unbound", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_admin_can_list_identity_conflicts(client_and_db):
    """管理员可以查询身份冲突。"""
    client, _ = client_and_db
    resp = client.get("/v1/feishu/identities/conflicts", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_admin_can_list_identity_audits(client_and_db):
    """管理员可以查询身份变更审计。"""
    client, _ = client_and_db
    resp = client.get("/v1/feishu/identities/audits", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


# ──────────────────────────────────────────────────────────────
# 身份绑定冲突
# ──────────────────────────────────────────────────────────────


def test_bind_identity_conflict_returns_conflict(client_and_db):
    """同一身份重复绑定必须返回冲突，而不是静默换绑。"""
    client, db = client_and_db
    db.add(
        FeishuIdentity(
            id="ID_EXIST",
            employee_id="EMP001",
            identity_type="user_id",
            identity_value="u_dup",
            active=True,
        )
    )
    db.commit()

    resp = client.post(
        "/v1/feishu/identities/bind",
        json={
            "employee_id": "ADMIN001",
            "identity_type": "user_id",
            "identity_value": "u_dup",
            "reason": "test conflict",
        },
        headers=_admin_headers(),
    )
    assert resp.status_code == 409


def test_bind_identity_succeeds_for_unbound_value(client_and_db):
    """未绑定的身份可以正常绑定。"""
    client, db = client_and_db
    resp = client.post(
        "/v1/feishu/identities/bind",
        json={
            "employee_id": "EMP001",
            "identity_type": "user_id",
            "identity_value": "u_fresh",
            "reason": "test bind",
        },
        headers=_admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


# ──────────────────────────────────────────────────────────────
# 权限边界
# ──────────────────────────────────────────────────────────────


def test_admin_permission_boundary_excludes_business_data_scope(client_and_db):
    """系统管理员只获得管理权限，不自动获得业务数据范围。"""
    from backend.app.services.access_control import build_access_context

    client, db = client_and_db
    admin = db.query(Employee).filter_by(id="ADMIN001").first()
    context = build_access_context(db, admin)

    assert "admin" in context.roles
    assert context.has_permission("sync_organization")
    # 业务数据范围不应自动开放
    assert not context.has_permission("view_all_scores")


def test_regular_employee_has_no_sync_permission(client_and_db):
    """普通员工不具备组织架构同步权限。"""
    from backend.app.services.access_control import build_access_context

    client, db = client_and_db
    emp = db.query(Employee).filter_by(id="EMP001").first()
    context = build_access_context(db, emp)
    assert not context.has_permission("sync_organization")
