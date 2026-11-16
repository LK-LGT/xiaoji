"""测试账号权限一致性回归测试（R5 修复）。

背景
----
过去 `TEST_USERS` 里每个测试账号带一份**硬编码**的 `permissions` 列表，
而后端动作鉴权判的是 `access_control` 解析出来的权限。两套来源不一致，
于是出现：

- 前端拿硬编码列表判断 → 菜单**显示**「飞书同步」；
- 后端按 `PermissionAction.SYNC_ORGANIZATION` 判 → 该权限只授予
  `system_admin` 表里的人 → 点进去 **403**。

使用者会误判为「功能有 bug / 没做完」，实际是账号权限配置问题。

修复后：测试账号与真实员工走**同一条** `access_control` 解析路径，
权限只有一个事实源。本测试锁定这条不变量。

**测试隔离**：本用例自建内存库并造齐角色所需的组织数据
（部门、负责人、直属下属、系统管理员），不依赖真实库种子数据 ——
CI 用的是空库，必须自给自足。
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

# 角色 + 飞书同步管理：只有管理员该有。
FEISHU_SYNC_UI_PERM = "system:feishu-sync:manage"
FEISHU_SYNC_BACKEND_PERM = "sync_organization"

# 测试账号在 `TEST_USERS` 中映射的真实员工 ID。
# ⚠️ 必须与 `routes/auth.py` 的 `TEST_USERS` 保持一致：
# token 里写的是这个 id，`get_current_user` 会按它查库，
# 对不上就是 401（而不是 403）。
ADMIN_ID = "b8663c7c"
MANAGER_ID = "g426c684"
LEADER_ID = "939931ba"
EMPLOYEE_ID = "2aaf32ea"


@pytest.fixture()
def client_with_org():
    """构造一套完整的组织数据 + 鉴权环境。

    组织关系：
      D_HR（人事部，leader=U_LEADER）
        ├─ U_ADMIN    （system_admin 表内 → admin 角色）
        ├─ U_MANAGER  （有直属下属 U_EMPLOYEE → manager 角色）
        └─ U_LEADER   （D_HR.leader_id → department_leader 角色）
      U_EMPLOYEE 直属主管为 U_MANAGER → 普通员工
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(Department(id="D_HR", name="人事部", parent_id=None, active=True))
    session.add(
        Department(
            id="D_SALES",
            name="销售部",
            parent_id=None,
            leader_id=LEADER_ID,
            active=True,
        )
    )
    for eid, name, dept, manager in [
        (ADMIN_ID, "测试管理员", "D_HR", MANAGER_ID),
        (MANAGER_ID, "测试主管", "D_HR", None),
        (LEADER_ID, "测试负责人", "D_SALES", None),
        (EMPLOYEE_ID, "测试员工", "D_SALES", MANAGER_ID),
    ]:
        session.add(
            Employee(
                id=eid,
                name=name,
                department_id=dept,
                manager_id=manager,
                employment_status="active",
                account_status="enabled",
                token_version=1,
            )
        )
    session.add(SystemAdmin(employee_id=ADMIN_ID, active=True))
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
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


def _login(client: TestClient, username: str, password: str) -> dict:
    resp = client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_test_users_login_resolves_real_roles(client_with_org: TestClient) -> None:
    """测试账号登录后应拿到 access_control 解析出的真实角色。

    回归点：过去返回的是硬编码的 `user_info["roles"]`（恒 1 个角色）。
    """
    data = _login(client_with_org, "admin", "admin123")
    assert "admin" in data["roles"], (
        f"admin 账号应解析出 admin 角色，实际 {data['roles']}"
    )


def test_login_and_me_return_identical_permissions(client_with_org: TestClient) -> None:
    """`/auth/login` 与 `/auth/me` 返回的权限必须完全一致。

    回归点：过去 `/me` 走硬编码列表、`/login` 走另一套，
    两端不一致会导致前端刷新后菜单突然少一半。
    """
    data = _login(client_with_org, "admin", "admin123")
    headers = {"Authorization": f"Bearer {data['accessToken']}"}

    me = client_with_org.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    me_data = me.json()["data"]

    assert me_data["permissions"] == data["permissions"], (
        f"login/me 权限列表不一致："
        f"login={data['permissions']} me={me_data['permissions']}"
    )
    assert me_data["roles"] == data["roles"]


def test_admin_login_exposes_feishu_sync_permission(
    client_with_org: TestClient,
) -> None:
    """管理员登录后应拿到飞书同步管理权限，前端菜单才能显示。"""
    data = _login(client_with_org, "admin", "admin123")

    assert FEISHU_SYNC_UI_PERM in data["permissions"]
    # 后端动作权限也必须同时有 —— 前端显示与后端放行必须是同一套。
    assert FEISHU_SYNC_BACKEND_PERM in data.get("backendPermissions", [])


def test_admin_can_actually_reach_feishu_sync_endpoints(
    client_with_org: TestClient,
) -> None:
    """管理员访问飞书同步端点必须 200（不再是 403）。

    这是 R5 的核心验收点：前端显示菜单 → 点进去能用。
    """
    data = _login(client_with_org, "admin", "admin123")
    headers = {"Authorization": f"Bearer {data['accessToken']}"}

    for path in [
        "/v1/feishu/sync-organization/status",
        "/v1/feishu/sync-organization/batches",
        "/v1/feishu/sync-organization/errors",
    ]:
        resp = client_with_org.get(path, headers=headers)
        assert resp.status_code == 200, (
            f"{path} 返回 {resp.status_code}（期望 200）。"
            f"若为 403，说明前端菜单权限与后端动作权限又分家了。"
        )


def test_employee_still_denied_on_feishu_sync(client_with_org: TestClient) -> None:
    """普通员工必须仍被拒绝 —— 权限边界不能因修复而放宽。"""
    data = _login(client_with_org, "employee", "employee123")
    headers = {"Authorization": f"Bearer {data['accessToken']}"}

    resp = client_with_org.get("/v1/feishu/sync-organization/status", headers=headers)
    assert resp.status_code == 403, (
        f"普通员工访问飞书同步端点应 403，实际 {resp.status_code}"
    )


def test_login_still_works_after_token_version_bump(client_with_org: TestClient) -> None:
    """token_version 被递增后，测试账号必须仍能登录并通过鉴权。

    回归点（同步验证时发现）：
    依赖层用 ``payload["ver"] == employee.token_version`` 校验 token，
    不一致直接 401。而离职 / 停用 / 改密都会递增 ``token_version``。
    过去测试账号登录签发 token 时**漏传**了当前版本（默认 1），
    于是该员工一旦被递增过版本（如组织同步把其标记为离职又复职），
    就**再也登不进来**，且没有任何报错提示 —— 直接 401。

    这里模拟「版本被递增到 3」的场景，断言仍能正常登录使用。
    """
    from backend.app.core.db import get_db

    session_gen = app.dependency_overrides[get_db]
    db = next(session_gen())

    employee = db.get(Employee, ADMIN_ID)
    assert employee is not None
    employee.token_version = 3
    db.commit()

    data = _login(client_with_org, "admin", "admin123")
    headers = {"Authorization": f"Bearer {data['accessToken']}"}

    resp = client_with_org.get("/v1/auth/me", headers=headers)
    assert resp.status_code == 200, (
        f"token_version 递增后应仍能登录，实际 {resp.status_code} —— "
        f"签发的 token 没带上当前 token_version。"
    )

    resp = client_with_org.get("/v1/feishu/sync-organization/status", headers=headers)
    assert resp.status_code == 200, resp.text
