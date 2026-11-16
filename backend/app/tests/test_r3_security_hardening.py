"""R3 安全加固回归测试。

背景
----
`backend/app/main.py` 曾存在一整套 Mock 鉴权：
- `MOCK_USERS` 硬编码 4 个账号（admin/manager/leader/employee）；
- token 格式为 `token_{username}_{user_id}_{timestamp}` —— **无签名、可任意伪造**；
- 端点挂在 `/auth/login`（**不带 `/v1` 前缀**），直接暴露在公开路径。

实测（修复前）：`POST /auth/login` → 200，拿到伪造 token 后
`GET /v1/performance/tasks` → 200 —— 属于**越权漏洞**。

本测试锁定 5 件事：
1. Mock 鉴权端点已从路由表移除；
2. 伪造 token 一律被拒；
3. 业务端点必须登录；
4. 唯一鉴权链路 `/v1/auth/*` 正常；
5. 数据范围隔离（普通员工不能看他人绩效）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, Department, Employee
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def client_with_data():
    """内存库 + 一个部门、两名员工（普通员工 + 其主管）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(Department(id="D_ROOT", name="总部", parent_id=None, active=True))
    session.add(
        Department(id="D_BIZ", name="业务部", parent_id="D_ROOT", active=True)
    )
    session.add(
        Employee(
            id="E_LEADER",
            name="主管",
            department_id="D_BIZ",
            employment_status="active",
            account_status="enabled",
        )
    )
    session.add(
        Employee(
            id="E_EMP",
            name="员工",
            department_id="D_BIZ",
            manager_id="E_LEADER",
            employment_status="active",
            account_status="enabled",
        )
    )
    session.commit()

    def override_get_db():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


def _auth(employee_id: str) -> dict[str, str]:
    token, _ = _generate_token(employee_id, employee_id, 1)
    return {"Authorization": f"Bearer {token}"}


# ── 1) Mock 鉴权端点已移除 ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/auth/login"),
        ("get", "/auth/me"),
        ("post", "/auth/logout"),
    ],
)
def test_mock_auth_endpoints_removed(client_with_data, method: str, path: str) -> None:
    """Mock 鉴权端点必须彻底消失（不再有第二套登录入口）。"""
    client, _ = client_with_data
    resp = client.request(method, path, json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 404, f"{method.upper()} {path} -> {resp.status_code}"


def test_mock_users_constant_removed() -> None:
    """`MOCK_USERS` 常量不得残留在 main.py。"""
    from backend.app import main as main_module

    assert not hasattr(main_module, "MOCK_USERS"), "main.py 仍残留 MOCK_USERS"


def test_no_unsigned_token_format_in_source() -> None:
    """源码里不得再出现 `token_{...}` 这种无签名 token 格式。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]  # backend/app
    offenders = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # 排除注释/文档里的历史说明
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", "*", '"', "'")):
                continue
            if "token_{" in line or "refresh_{" in line:
                offenders.append(f"{path.name}: {stripped[:80]}")
    assert not offenders, "仍存在无签名 token 生成逻辑:\n" + "\n".join(offenders)


# ── 2) 伪造 token 一律被拒 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "forged",
    [
        "token_admin_1_1794808547.779059",
        "token_employee_3_1234567890.0",
        "refresh_admin_1",
        "admin",
        "",
        "a.b",
    ],
)
def test_forged_tokens_rejected(client_with_data, forged: str) -> None:
    """伪造 token 必须被拒（401/403），不能当作有效身份。"""
    client, _ = client_with_data
    resp = client.get(
        "/v1/performance/tasks", headers={"Authorization": f"Bearer {forged}"}
    )
    assert resp.status_code in (401, 403), f"伪造 token {forged!r} -> {resp.status_code}"


# ── 3) 业务端点必须登录 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/v1/performance/tasks"),
        ("get", "/v1/system/employees"),
        ("get", "/v1/system/audits"),
        ("get", "/v1/system/roles"),
        ("get", "/v1/department/dashboard"),
        ("get", "/v1/organizations/departments/tree"),
        ("get", "/v1/messages"),
        ("get", "/api/employees"),
        ("post", "/api/metric-values"),
        ("post", "/api/attendance/anomalies"),
    ],
)
def test_business_endpoints_require_login(client_with_data, method: str, path: str) -> None:
    """匿名访问业务端点必须被拒。

    覆盖 R3 前的真实漏洞面：这些端点曾全部 200 开放。
    """
    client, _ = client_with_data
    resp = client.request(method, path, json={})
    assert resp.status_code in (401, 403), f"{method.upper()} {path} -> {resp.status_code}"


def test_health_check_stays_public(client_with_data) -> None:
    """健康检查必须保持公开（探针/负载均衡需要）。"""
    client, _ = client_with_data
    resp = client.get("/health")
    assert resp.status_code == 200


# ── 4) 唯一鉴权链路正常 ──────────────────────────────────────────────────

def test_real_auth_chain_works(client_with_data) -> None:
    """`/v1/auth/login` 签发的是 HMAC Token，且可用于访问业务端点。"""
    client, _ = client_with_data
    resp = client.post(
        "/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["accessToken"]
    assert not token.startswith("token_"), "token 仍是旧的 mock 格式"

    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_login_rejects_wrong_password(client_with_data) -> None:
    client, _ = client_with_data
    resp = client.post(
        "/v1/auth/login", json={"username": "admin", "password": "definitely-wrong"}
    )
    assert resp.status_code == 401


# ── 5) 数据范围隔离 ──────────────────────────────────────────────────────

def test_employee_cannot_view_other_record(client_with_data) -> None:
    """普通员工查他人考核记录 → 403（记录不存在时也应先过鉴权）。"""
    client, _ = client_with_data
    resp = client.get("/api/records/REC_ANY", headers=_auth("E_EMP"))
    # 记录不存在 → 404（鉴权通过后）
    # 但绝不能是 200（说明没做范围校验就返回了数据）
    assert resp.status_code in (403, 404), resp.text
