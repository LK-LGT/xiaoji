"""Token 失效与账号状态测试。

覆盖：
- 正常登录后的 Token 可用
- 账号禁用 / 离职后旧 Token 立即失效
- token_version 不匹配被拒绝
- 篡改签名的 Token 被拒绝
- 过期 Token 被拒绝
- 离职 / 禁用账号无法登录
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import Base, Department, Employee
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def client_and_db():
    """独立内存数据库 + TestClient。"""
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
                id="EMP001",
                name="在职员工",
                login_account="emp001",
                employment_status="active",
                account_status="enabled",
                token_version=1,
            ),
            Employee(
                id="EMP002",
                name="离职员工",
                login_account="emp002",
                employment_status="exited",
                account_status="disabled",
                token_version=2,
            ),
        ]
    )
    db.commit()

    def override_get_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def _make_token(sub: str, ver: int = 1, exp_delta: int = 3600, secret: str | None = None) -> str:
    """构造一个 HMAC Token（用于构造异常场景）。"""
    now = int(time.time())
    payload = {
        "sub": sub,
        "username": sub,
        "ver": ver,
        "iat": now,
        "exp": now + exp_delta,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        (secret or settings.auth_token_secret).encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


# ──────────────────────────────────────────────────────────────
# 正常路径
# ──────────────────────────────────────────────────────────────


def test_valid_token_is_accepted(client_and_db):
    """正常签发的 Token 可以访问受保护接口。"""
    client, _ = client_and_db
    token, _ = _generate_token("EMP001", "emp001", 1)
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    # 普通员工没有管理员权限 -> 403（说明 Token 已通过鉴权）
    assert resp.status_code == 403


def test_login_issues_usable_token(client_and_db):
    """登录接口返回的 Token 可以正常访问接口。"""
    client, db = client_and_db
    resp = client.post(
        "/v1/auth/login", json={"username": "emp001", "password": "123456"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    token = data["accessToken"]
    assert data.get("userId") == "EMP001"

    me = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200


# ──────────────────────────────────────────────────────────────
# 旧 Token 失效
# ──────────────────────────────────────────────────────────────


def test_old_token_rejected_after_account_disabled(client_and_db):
    """账号被禁用后，旧 Token 必须立即失效。"""
    client, db = client_and_db
    token, _ = _generate_token("EMP001", "emp001", 1)

    emp = db.query(Employee).filter_by(id="EMP001").first()
    emp.account_status = "disabled"
    emp.token_version = emp.token_version + 1
    db.commit()

    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_old_token_rejected_after_employee_exited(client_and_db):
    """员工离职后，旧 Token 必须立即失效。"""
    client, db = client_and_db
    token, _ = _generate_token("EMP001", "emp001", 1)

    emp = db.query(Employee).filter_by(id="EMP001").first()
    emp.employment_status = "exited"
    emp.token_version = emp.token_version + 1
    db.commit()

    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_token_version_mismatch_is_rejected(client_and_db):
    """token_version 与数据库不一致的 Token 被拒绝。"""
    client, _ = client_and_db
    # 数据库中 EMP001 的 token_version=1，这里构造 ver=99
    token = _make_token("EMP001", ver=99)
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_relogin_after_disable_gets_working_token(client_and_db):
    """解禁后重新登录获得的新 Token 可以使用。"""
    client, db = client_and_db
    emp = db.query(Employee).filter_by(id="EMP001").first()
    emp.account_status = "disabled"
    emp.token_version = 2
    db.commit()

    # 解禁
    emp.account_status = "enabled"
    emp.token_version = 3
    db.commit()

    token, _ = _generate_token("EMP001", "emp001", 3)
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403  # 鉴权通过，权限不足


# ──────────────────────────────────────────────────────────────
# Token 篡改 / 过期
# ──────────────────────────────────────────────────────────────


def test_tampered_payload_is_rejected(client_and_db):
    """篡改 payload 后签名不匹配，必须被拒绝。"""
    client, _ = client_and_db
    token = _make_token("EMP001", ver=1)
    encoded, signature = token.split(".", 1)
    tampered_payload = base64.urlsafe_b64encode(
        json.dumps(
            {"sub": "EMP001", "ver": 1, "exp": int(time.time()) + 999},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).decode().rstrip("=")
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {tampered_payload}.{signature}"},
    )
    assert resp.status_code == 401


def test_token_signed_with_wrong_secret_is_rejected(client_and_db):
    """用错误密钥签发的 Token 必须被拒绝。"""
    client, _ = client_and_db
    token = _make_token("EMP001", ver=1, secret="a-completely-different-secret-key-value")
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_expired_token_is_rejected(client_and_db):
    """过期 Token 必须被拒绝。"""
    client, _ = client_and_db
    token = _make_token("EMP001", ver=1, exp_delta=-3600)
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────
# 登录限制
# ──────────────────────────────────────────────────────────────


def test_exited_employee_cannot_login(client_and_db):
    """离职员工不能登录。"""
    client, _ = client_and_db
    resp = client.post(
        "/v1/auth/login", json={"username": "emp002", "password": "123456"}
    )
    assert resp.status_code == 403
    assert "离职" in resp.json().get("detail", "") or "禁用" in resp.json().get("detail", "")


def test_disabled_account_cannot_login(client_and_db):
    """被禁用的账号不能登录。"""
    client, db = client_and_db
    emp = db.query(Employee).filter_by(id="EMP001").first()
    emp.account_status = "disabled"
    db.commit()

    resp = client.post(
        "/v1/auth/login", json={"username": "emp001", "password": "123456"}
    )
    assert resp.status_code == 403


def test_login_with_wrong_password_is_rejected(client_and_db):
    """密码错误不能登录。"""
    client, _ = client_and_db
    resp = client.post(
        "/v1/auth/login", json={"username": "emp001", "password": "wrong-password"}
    )
    assert resp.status_code == 401


def test_token_for_unknown_employee_is_rejected(client_and_db):
    """Token 对应的员工不存在时必须被拒绝。"""
    client, _ = client_and_db
    token, _ = _generate_token("GHOST999", "ghost", 1)
    resp = client.get(
        "/v1/feishu/sync-organization/batches",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
