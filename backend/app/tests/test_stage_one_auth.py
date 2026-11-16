"""阶段一认证与异常处理的回归测试。

覆盖：
- 登录时角色来自 access_context，而非硬编码部门 ID
- 多部门/多角色叠加正确
- 领域异常被全局处理器转换为正确 HTTP 状态码（而非 500）
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
    SystemAdmin,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


def _seed_org(db):
    """构造：HR 部门 + 兼岗员工 + 部门负责人 + 系统管理员。"""
    db.add_all([
        Department(id="D_HR", name="人事行政部", active=True),
        Department(id="D_SALES", name="销售部", active=True),
        Department(id="D_SALES_SUB", name="销售一组", parent_id="D_SALES", active=True),
        Employee(id="U_HR", name="HR主管", department_id="D_HR",
                 employment_status="active"),
        Employee(id="U_LEADER", name="销售总监", department_id="D_SALES",
                 employment_status="active"),
        Employee(id="U_STAFF", name="销售员工", department_id="D_SALES_SUB",
                 employment_status="active"),
    ])
    db.flush()

    # 兼岗：员工同时属于 HR 部门与业务部门
    db.add_all([
        EmployeeDepartment(employee_id="U_HR", department_id="D_HR", is_primary=True),
        EmployeeDepartment(employee_id="U_HR", department_id="D_SALES", is_primary=False),
        EmployeeDepartment(employee_id="U_STAFF", department_id="D_SALES_SUB", is_primary=True),
    ])

    # 飞书部门主管
    db.get(Department, "D_SALES").leader_id = "U_LEADER"

    # HR 部门配置 + 系统管理员
    db.add_all([
        HrDepartmentConfig(id="C1", department_id="D_HR", active=True),
        SystemAdmin(id="A1", employee_id="U_HR", active=True),
    ])
    db.commit()


def test_hr_roles_come_from_access_context(db):
    """HR / admin 角色必须来自 access_context，不能依赖硬编码部门 ID。"""
    from backend.app.services.access_control import build_access_context

    _seed_org(db)
    employee = db.get(Employee, "U_HR")
    context = build_access_context(db, employee)

    assert "hr" in context.roles
    assert "admin" in context.roles
    assert "department_leader" not in context.roles
    assert "employee" in context.roles


def test_non_hr_leader_has_no_hr_role(db):
    """非 HR 部门的负责人不能获得 HR 权限。"""
    from backend.app.services.access_control import build_access_context

    _seed_org(db)
    leader = db.get(Employee, "U_LEADER")
    context = build_access_context(db, leader)

    assert "department_leader" in context.roles
    assert "hr" not in context.roles
    assert "admin" not in context.roles


def test_multi_department_hr_detection(db):
    """兼岗员工只要命中任一启用 HR 部门即获得 HR 角色。"""
    from backend.app.services.access_control import build_access_context

    _seed_org(db)
    # U_STAFF 仅属于业务部门 -> 非 HR
    staff = db.get(Employee, "U_STAFF")
    assert "hr" not in build_access_context(db, staff).roles

    # 再把 U_STAFF 加入 HR 部门 -> 变为 HR
    db.add(EmployeeDepartment(
        employee_id="U_STAFF", department_id="D_HR", is_primary=False
    ))
    db.commit()
    assert "hr" in build_access_context(db, staff).roles


def test_role_ui_permission_mapping():
    """角色 -> 前端权限串的映射必须覆盖所有阶段一角色。"""
    from backend.app.routes.auth import ROLE_UI_PERMISSIONS

    for role in ("employee", "manager", "department_leader", "hr", "admin"):
        assert role in ROLE_UI_PERMISSIONS
        assert ROLE_UI_PERMISSIONS[role], f"{role} 不应有空权限列表"


def test_base_app_error_handler_maps_to_http_status():
    """BaseAppError 必须被全局处理器转成对应状态码，而不是 500。"""
    from fastapi.testclient import TestClient

    from backend.app.exceptions import PermissionDenied
    from backend.app.main import app

    @app.get("/__test_raise_permission__")
    def _raise():
        raise PermissionDenied(user_id="u1", action="test", resource="thing")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/__test_raise_permission__")
    assert resp.status_code == 403
    assert resp.json()["code"] == 403
