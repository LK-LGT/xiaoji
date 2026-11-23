"""部门功能板块管理 API 测试（2026-11-21）。

⚠️ **为什么要有这个模块**
用户投诉：「别的部门为什么会有剪辑板块？」

⇒ 修好了「读」的一侧（隔离），但还需要「写」的一侧
⇒ 否则 HR 没法给别的部门开通 / 停用板块 ⇒ 隔离就成了一锤子买卖。

覆盖
----
1. 读取 —— 已知板块 / 已开通列表
2. 写入 —— 开通 / 停用 / 幂等 / 未知板块
3. 权限 —— 只有 HR 与管理员能改
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Department, Employee, HrDepartmentConfig
from backend.app.domain.pan_workload_models import DepartmentModule

PREFIX = "/v1/department-modules"


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
    """真实组织片段 —— 短视频挂在运营中心下面。"""
    d_center = Department(id="D_CENTER", name="运营中心", active=True)
    d_video = Department(id="D_VIDEO", name="短视频", active=True)
    d_hr = Department(id="D_HR", name="人事行政部", active=True)
    db.add_all([d_center, d_video, d_hr])
    db.add_all([
        Employee(id="E_LCY", name="李婵雨", department_id="D_VIDEO",
                 position="剪辑", employment_status="active", account_status="enabled"),
        Employee(id="E_OTHER", name="外部门员工", department_id="D_CENTER",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_HR", name="HR", department_id="D_HR",
                 employment_status="active", account_status="enabled"),
    ])
    db.flush()
    d_video.parent_id = "D_CENTER"
    d_center.leader_id = "E_OTHER"
    d_hr.leader_id = "E_HR"
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()


@pytest.fixture()
def client(db):
    from backend.app.main import app
    from backend.app.routes import department_modules as routes

    current = {"user": None}

    def _override_user():
        return current["user"]

    def _override_db():
        yield db

    app.dependency_overrides[routes.get_current_user] = _override_user
    app.dependency_overrides[routes.get_db] = _override_db
    with TestClient(app) as c:
        c.current = current  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def as_user(client, db):
    def _set(user_id: str):
        emp = db.get(Employee, user_id)
        assert emp is not None
        client.current["user"] = emp  # type: ignore[attr-defined]
        return emp

    return _set


class TestModuleGrant:
    def test_HR能开通板块(self, client, db, org, as_user):
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}",
            json={"department_id": "D_VIDEO", "module_code": "pan_workload",
                  "note": "短视频部开通剪辑工作量"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["departmentName"] == "短视频"
        assert resp.json()["item"]["active"] is True

    def test_普通员工不能配板块(self, client, db, org, as_user):
        """⚠️ 只有 HR / 管理员能改板块。"""
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}",
            json={"department_id": "D_CENTER", "module_code": "pan_workload"},
        )
        assert resp.status_code == 403, resp.text

    def test_外部门员工不能读板块列表(self, client, db, org, as_user):
        as_user("E_OTHER")
        resp = client.get(f"{PREFIX}")
        assert resp.status_code == 403, resp.text

    def test_重复开通是幂等的(self, client, db, org, as_user):
        """⚠️ 幂等 —— 重复开通只更新，不会插两行、不会报错。"""
        as_user("E_HR")
        payload = {"department_id": "D_VIDEO", "module_code": "pan_workload"}
        first = client.post(f"{PREFIX}", json=payload)
        second = client.post(f"{PREFIX}", json=payload)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        rows = db.scalars(select(DepartmentModule)).all()
        assert len(rows) == 1, "重复开通不该插第二行"

    def test_停用板块保留行(self, client, db, org, as_user):
        """⚠️ **不删行** —— 停用置 ``active=False``，保留配置痕迹。"""
        as_user("E_HR")
        client.post(
            f"{PREFIX}",
            json={"department_id": "D_VIDEO", "module_code": "pan_workload"},
        )
        resp = client.post(
            f"{PREFIX}",
            json={"department_id": "D_VIDEO", "module_code": "pan_workload",
                  "active": False},
        )
        assert resp.status_code == 200, resp.text
        rows = db.scalars(select(DepartmentModule)).all()
        assert len(rows) == 1
        assert rows[0].active is False

    def test_未知板块被拒(self, client, db, org, as_user):
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}",
            json={"department_id": "D_VIDEO", "module_code": "not_a_real_module"},
        )
        assert resp.status_code == 400, resp.text

    def test_不存在的部门被拒(self, client, db, org, as_user):
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}",
            json={"department_id": "D_NOPE", "module_code": "pan_workload"},
        )
        assert resp.status_code == 404, resp.text

    def test_列出已知板块(self, client, db, org, as_user):
        as_user("E_HR")
        resp = client.get(f"{PREFIX}/known")
        assert resp.status_code == 200, resp.text
        codes = [item["code"] for item in resp.json()["items"]]
        assert "pan_workload" in codes

    def test_列出已开通(self, client, db, org, as_user):
        as_user("E_HR")
        client.post(
            f"{PREFIX}",
            json={"department_id": "D_VIDEO", "module_code": "pan_workload"},
        )
        resp = client.get(f"{PREFIX}")
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["departmentName"] == "短视频"
        assert items[0]["moduleCode"] == "pan_workload"


class TestModuleGrantPermission:
    """⚠️ **前端菜单权限位与后端守卫必须同一口径**（2026-11-21）。

    前端菜单项用的是权限串 ``system:department-module:manage``
    ⇒ 后端也必须按**同一个串**判定，
    否则会出现「菜单看不见但接口能调」或「菜单能看见但接口 403」。
    """

    def test_HR角色带板块管理权限位(self):
        """⚠️ 权限位必须挂在 `hr` 角色上（否则 HR 看不到菜单）。"""
        from backend.app.routes.auth import ROLE_UI_PERMISSIONS

        assert "system:department-module:manage" in ROLE_UI_PERMISSIONS["hr"]

    def test_管理员角色带板块管理权限位(self):
        from backend.app.routes.auth import ROLE_UI_PERMISSIONS

        assert "system:department-module:manage" in ROLE_UI_PERMISSIONS["admin"]

    def test_基础角色不带板块管理权限位(self):
        """⚠️ 其它角色**不能**有这个权限位 ——
        否则板块开关就不受控了。"""
        from backend.app.routes.auth import ROLE_UI_PERMISSIONS

        for role in ("employee", "manager", "department_leader"):
            assert "system:department-module:manage" not in ROLE_UI_PERMISSIONS.get(role, []), (
                f"{role} 不该有板块管理权限"
            )

    def test_HR判定命中权限位(self, client, db, org, as_user):
        from backend.app.services.department_modules import has_department_module_manage

        as_user("E_HR")
        assert has_department_module_manage(db, client.current["user"]) is True

    def test_普通员工判定不命中权限位(self, client, db, org, as_user):
        from backend.app.services.department_modules import has_department_module_manage

        as_user("E_LCY")
        assert has_department_module_manage(db, client.current["user"]) is False

    def test_管理员判定命中权限位(self, client, db, org, as_user):
        """⚠️ 系统管理员也要能管板块 —— 否则管理员被挡住 ⇒ 死锁。"""
        from backend.app.domain.models import SystemAdmin
        from backend.app.services.department_modules import has_department_module_manage

        db.add(SystemAdmin(employee_id="E_OTHER", active=True))
        db.commit()
        as_user("E_OTHER")
        assert has_department_module_manage(db, client.current["user"]) is True

    def test_前端菜单项权限串与后端一致(self):
        """⚠️ **契约测试** —— 前端菜单用的串必须等于后端认的串。

        ⚠️ 前端是 TS 文件，Python 测不到 ⇒ 直接读源文件断言。
        """
        from pathlib import Path

        from backend.app.services.department_modules import (
            DEPARTMENT_MODULE_MANAGE_PERMISSION,
        )

        repo = Path(__file__).resolve().parents[3]
        workspaces = (repo / "frontend" / "src" / "navigation" / "workspaces.ts").read_text(
            encoding="utf-8"
        )
        router = (repo / "frontend" / "src" / "router" / "index.ts").read_text(encoding="utf-8")

        assert DEPARTMENT_MODULE_MANAGE_PERMISSION in workspaces
        assert DEPARTMENT_MODULE_MANAGE_PERMISSION in router
        # ⚠️ 旧权限位 `system:org:view` 不该再出现在板块菜单项上
        assert (
            "path: '/system/department-modules', icon: '◫', permission: 'system:org:view'"
            not in workspaces
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
