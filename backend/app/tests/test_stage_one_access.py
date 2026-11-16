"""阶段一权限上下文与多部门同步测试。"""

from datetime import datetime

from sqlalchemy.orm import Session

from backend.app.domain.models import (
    Department,
    Employee,
    EmployeeDepartment,
    FeishuIdentity,
    HrDepartmentConfig,
    SystemAdmin,
)
from backend.app.services.access_control import PermissionAction, build_access_context
from backend.app.services.organization_sync import OrganizationSyncService


class FakeFeishuClient:
    """模拟飞书真实返回结构。

    注意：飞书部门接口的 leader_user_id 实际返回 open_id，
    status 返回 {"is_deleted": False}，都与旧版数字/ID 形态不同。
    """

    def __init__(self):
        self.departments = [
            {
                "department_id": "D_ROOT",
                "name": "总部",
                "parent_department_id": "0",
                "leader_user_id": "OU_ROOT",
                "status": {"is_deleted": False},
            },
            {
                "department_id": "D_HR",
                "name": "人力资源部",
                "parent_department_id": "D_ROOT",
                "leader_user_id": "OU_HR",
                "status": {"is_deleted": False},
            },
            {
                "department_id": "D_CHILD",
                "name": "销售一组",
                "parent_department_id": "D_ROOT",
                "leader_user_id": "OU_LEADER",
                "status": {"is_deleted": False},
            },
        ]
        self.employees = [
            {
                "user_id": "U_HR",
                "open_id": "OU_HR",
                "name": "HR负责人",
                "email": "hr@example.com",
                "department_ids": ["D_HR"],
                "leader_user_id": "U_ROOT",
                "job_title": "HR经理",
                "status": {"is_activated": True, "is_exited": False},
            },
            {
                "user_id": "U_LEADER",
                "open_id": "OU_LEADER",
                "name": "部门负责人",
                "email": "leader@example.com",
                "department_ids": ["D_CHILD"],
                "leader_user_id": "U_ROOT",
                "job_title": "销售经理",
                "status": {"is_activated": True, "is_exited": False},
            },
            {
                "user_id": "U_EMP",
                "open_id": "OU_EMP",
                "name": "多部门员工",
                "email": "emp@example.com",
                "department_ids": ["D_CHILD", "D_HR"],
                "leader_user_id": "U_LEADER",
                "job_title": "销售代表",
                "status": {"is_activated": True, "is_exited": False},
            },
        ]

    def fetch_departments(self):
        return self.departments

    def fetch_employees(self, departments=None):
        return self.employees


class ReverseOrderFeishuClient(FakeFeishuClient):
    """下级员工排在上级之前，模拟飞书返回顺序不确定。

    员工 manager_id 是自引用外键，若不两轮写入，
    先出现的下级会因上级尚未落库而外键失败。
    """

    def fetch_employees(self, departments=None):
        return list(reversed(self.employees))


def test_hr_is_derived_from_configured_department(db: Session):
    db.add_all(
        [
            Department(id="D_HR", name="人力资源部", active=True),
            Employee(
                id="U_HR",
                name="HR负责人",
                department_id="D_HR",
                employment_status="active",
            ),
        ]
    )
    db.commit()

    employee = db.query(Employee).filter_by(id="U_HR").one()
    context = build_access_context(db, employee)
    assert "hr" not in context.roles

    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()
    context = build_access_context(db, employee)
    assert "hr" in context.roles
    assert context.has_permission(PermissionAction.VIEW_ALL_SCORES)
    assert context.has_permission(PermissionAction.CONFIRM_RATING)


def test_multi_department_employee_becomes_hr(db: Session):
    db.add_all(
        [
            Department(id="D_HR", name="人力资源部", active=True),
            Department(id="D_SALES", name="销售部", active=True),
            Employee(
                id="U_EMP",
                name="多部门员工",
                department_id="D_SALES",
                employment_status="active",
            ),
            EmployeeDepartment(
                employee_id="U_EMP",
                department_id="D_SALES",
                is_primary=True,
            ),
            EmployeeDepartment(
                employee_id="U_EMP",
                department_id="D_HR",
                is_primary=False,
            ),
            HrDepartmentConfig(department_id="D_HR", active=True),
        ]
    )
    db.commit()

    employee = db.query(Employee).filter_by(id="U_EMP").one()
    context = build_access_context(db, employee)
    assert "hr" in context.roles
    assert "employee" in context.roles


def test_department_leader_scope_includes_descendants(db: Session):
    db.add_all(
        [
            Department(id="D_ROOT", name="总部", leader_id="U_LEADER", active=True),
            Department(
                id="D_CHILD",
                name="销售一组",
                parent_id="D_ROOT",
                leader_id="U_LEADER",
                active=True,
            ),
            Employee(
                id="U_LEADER",
                name="部门负责人",
                department_id="D_ROOT",
                employment_status="active",
            ),
        ]
    )
    db.commit()

    employee = db.query(Employee).filter_by(id="U_LEADER").one()
    context = build_access_context(db, employee)
    assert "department_leader" in context.roles
    assert {"D_ROOT", "D_CHILD"}.issubset(context.managed_department_ids)


def test_admin_does_not_gain_business_scope(db: Session):
    db.add_all(
        [
            Department(id="D_ROOT", name="总部", active=True),
            Employee(
                id="U_ADMIN",
                name="管理员",
                department_id="D_ROOT",
                employment_status="active",
            ),
            SystemAdmin(employee_id="U_ADMIN", active=True),
        ]
    )
    db.commit()

    employee = db.query(Employee).filter_by(id="U_ADMIN").one()
    context = build_access_context(db, employee)
    assert "admin" in context.roles
    assert context.has_permission(PermissionAction.SYNC_ORGANIZATION)
    assert not context.has_permission(PermissionAction.VIEW_ALL_SCORES)
    assert not context.has_permission(PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW)


def test_organization_sync_is_idempotent_and_preserves_multiple_departments(db: Session):
    service = OrganizationSyncService(db, FakeFeishuClient())
    first = service.sync_all()
    second = service.sync_all()

    assert first["employees_synced"] == 3
    assert second["employees_skipped"] == 3
    assert db.query(Employee).count() == 3
    assert db.query(Department).count() == 3

    db.query(Employee).filter_by(id="U_EMP").one()
    links = db.query(EmployeeDepartment).filter_by(employee_id="U_EMP").all()
    assert {link.department_id for link in links} == {"D_CHILD", "D_HR"}
    assert sum(1 for link in links if link.is_primary) == 1
    assert db.query(FeishuIdentity).filter_by(
        employee_id="U_EMP", identity_type="user_id", identity_value="U_EMP"
    ).count() == 1


def test_employee_sync_handles_manager_appearing_later(db: Session):
    """回归：manager_id 是自引用外键，上级晚于下级出现时不能整批失败。

    旧实现单条失败会 rollback() 掉整个未提交的部门/员工事务，
    导致 departments=0、employees=0 的假失败。
    """
    service = OrganizationSyncService(db, ReverseOrderFeishuClient())
    result = service.sync_all()

    assert result["status"] == "ok"
    assert result["departments_failed"] == 0
    assert result["employees_failed"] == 0
    assert result["employees_synced"] == 3

    # 部门数据必须真正落库，不能被回滚
    assert db.query(Department).count() == 3
    assert db.query(Employee).count() == 3

    # manager_id 必须正确回填
    subordinate = db.query(Employee).filter_by(id="U_EMP").one()
    assert subordinate.manager_id == "U_LEADER"
    assert db.get(Employee, subordinate.manager_id) is not None


def test_employee_sync_is_idempotent_with_manager_backfill(db: Session):
    """回归：manager_id 两轮写入不能把每次同步都误报为"已变更"。"""
    service = OrganizationSyncService(db, FakeFeishuClient())
    service.sync_all()

    second = OrganizationSyncService(db, FakeFeishuClient()).sync_all()
    assert second["employees_synced"] == 0
    assert second["employees_skipped"] == 3
    assert second["employees_failed"] == 0


def test_department_leader_open_id_resolves_to_employee_user_id(db: Session):
    """回归：部门接口的 leader_user_id 返回 open_id，必须归一到 user_id。

    若不归一，Department.leader_id 会存成 open_id，
    部门负责人角色将永远无法匹配 Employee.id。
    """
    service = OrganizationSyncService(db, FakeFeishuClient())
    service.sync_all()

    dept = db.query(Department).filter_by(id="D_HR").one()
    assert dept.leader_id == "U_HR"
    assert db.get(Employee, dept.leader_id) is not None

    leader = db.get(Employee, "U_HR")
    context = build_access_context(db, leader)
    assert "department_leader" in context.roles
    assert "D_HR" in context.managed_department_ids


def test_department_status_dict_is_treated_as_active(db: Session):
    """回归：飞书部门 status 返回 {"is_deleted": False}，不能按数字解析。

    旧实现 int(status) 会对每个部门抛 TypeError，导致整批部门同步失败。
    """
    service = OrganizationSyncService(db, FakeFeishuClient())
    result = service.sync_all()

    assert result["departments_failed"] == 0
    assert result["departments_synced"] == 3
    assert all(d.active for d in db.query(Department).all())


def test_deleted_department_status_marks_inactive(db: Session):
    """回归：飞书 status.is_deleted=True 的部门应停用。"""
    client = FakeFeishuClient()
    client.departments[0]["status"] = {"is_deleted": True}
    service = OrganizationSyncService(db, client)
    service.sync_all()

    dept = db.query(Department).filter_by(id="D_ROOT").one()
    assert dept.active is False
