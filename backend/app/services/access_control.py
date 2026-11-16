"""阶段一统一权限上下文。

角色来源：
- HR：员工所属任一部门命中启用的 HR 部门配置。
- 部门负责人：飞书同步的部门 leader_id。
- 直属主管：员工存在直属下属。
- 系统管理员：必须由 system_admin 表显式授权。

管理员仅拥有系统管理权限，不自动获得员工绩效等业务数据范围。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import (
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
    SystemAdmin,
)


class PermissionAction(str, Enum):
    """权限标识。"""

    VIEW_OWN_SCORE = "view_own_score"
    VIEW_DIRECT_REPORT_SCORE = "view_direct_report_score"
    VIEW_ALL_SCORES = "view_all_scores"
    SUBMIT_OWN_REVIEW = "submit_own_review"
    SUBMIT_DIRECT_REPORT_REVIEW = "submit_direct_report_review"
    CONFIRM_RATING = "confirm_rating"
    ADD_METRIC_TO_RECORD = "add_metric_to_record"
    VIEW_AUDIT_LOG = "view_audit_log"
    MANAGE_EMPLOYEES = "manage_employees"
    MANAGE_SYSTEM_CONFIG = "manage_system_config"
    SYNC_ORGANIZATION = "sync_organization"
    SYNC_ATTENDANCE = "sync_attendance"


@dataclass(frozen=True)
class AccessContext:
    """当前用户的角色、权限和数据范围快照。"""

    employee_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    direct_report_ids: frozenset[str] = field(default_factory=frozenset)
    managed_department_ids: frozenset[str] = field(default_factory=frozenset)
    employee_department_ids: frozenset[str] = field(default_factory=frozenset)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def can_view_score_of(self, target_employee_id: str) -> bool:
        """能否查看指定员工的绩效数据。

        规则（与 `PermissionAction` 保持一致）：
        - 本人 → `VIEW_OWN_SCORE`
        - 直属下属 / 所管理部门成员 → `VIEW_DIRECT_REPORT_SCORE`
        - HR → `VIEW_ALL_SCORES`（看全员）

        ⚠️ 管理员**不**自动拥有查看全员成绩的权限 —— 管理权限
        （系统配置、组织同步）与业务数据权限是两条线，
        避免「能管系统就能看所有人绩效」的越权。
        """
        target = str(target_employee_id)
        if not target:
            return False
        if target == self.employee_id:
            return self.has_permission(PermissionAction.VIEW_OWN_SCORE)
        if target in self.direct_report_ids:
            return self.has_permission(PermissionAction.VIEW_DIRECT_REPORT_SCORE)
        if self.has_permission(PermissionAction.VIEW_ALL_SCORES):
            return True
        return False


def _department_descendants(db: Session, root_ids: Iterable[str]) -> set[str]:
    """返回指定部门及其所有下级部门 ID。"""
    roots = {str(item) for item in root_ids if item}
    if not roots:
        return set()

    rows = db.execute(select(Department.id, Department.parent_id)).all()
    children: dict[str | None, list[str]] = {}
    for dept_id, parent_id in rows:
        children.setdefault(parent_id, []).append(dept_id)

    result: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def _employee_department_ids(db: Session, employee_id: str, primary_department_id: str | None) -> set[str]:
    rows = db.execute(
        select(EmployeeDepartment.department_id).where(
            EmployeeDepartment.employee_id == employee_id
        )
    ).scalars().all()
    result = {str(item) for item in rows if item}
    if primary_department_id:
        result.add(str(primary_department_id))
    return result


def can_access_employee(
    context: AccessContext,
    target_employee: Employee,
    db: Session,
) -> bool:
    """校验员工数据访问范围。

    部门负责人可访问其管理范围（所属部门及下级部门）内的员工；
    兼岗员工按任一所属部门命中管理范围即可访问。
    """
    if context.employee_id == target_employee.id:
        return True
    if "hr" in context.roles:
        return True
    if target_employee.id in context.direct_report_ids:
        return True
    if "department_leader" in context.roles:
        target_departments = _employee_department_ids(
            db, target_employee.id, target_employee.department_id
        )
        if target_departments & context.managed_department_ids:
            return True
    return False


def build_access_context(db: Session, employee: Employee) -> AccessContext:
    """根据数据库当前配置计算用户上下文。"""
    employee_department_ids = _employee_department_ids(
        db, employee.id, employee.department_id
    )

    hr_department_ids = set(
        db.execute(
            select(HrDepartmentConfig.department_id).where(
                HrDepartmentConfig.active.is_(True)
            )
        ).scalars().all()
    )
    is_hr = bool(employee_department_ids & hr_department_ids)

    managed_roots = set(
        db.execute(
            select(Department.id).where(
                Department.leader_id == employee.id,
                Department.active.is_(True),
            )
        ).scalars().all()
    )
    managed_department_ids = _department_descendants(db, managed_roots)

    direct_report_ids = frozenset(
        db.execute(
            select(Employee.id).where(
                Employee.manager_id == employee.id,
                Employee.employment_status == "active",
            )
        ).scalars().all()
    )

    is_admin = db.execute(
        select(SystemAdmin.id).where(
            SystemAdmin.employee_id == employee.id,
            SystemAdmin.active.is_(True),
        )
    ).first() is not None

    roles: set[str] = {"employee"}
    permissions: set[str] = {
        PermissionAction.VIEW_OWN_SCORE,
        PermissionAction.SUBMIT_OWN_REVIEW,
    }

    if direct_report_ids:
        roles.add("manager")
        permissions.update(
            {
                PermissionAction.VIEW_DIRECT_REPORT_SCORE,
                PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW,
                PermissionAction.ADD_METRIC_TO_RECORD,
            }
        )

    if managed_department_ids:
        roles.add("department_leader")
        permissions.update(
            {
                PermissionAction.VIEW_DIRECT_REPORT_SCORE,
                PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW,
            }
        )

    if is_hr:
        roles.add("hr")
        permissions.update(
            {
                PermissionAction.VIEW_ALL_SCORES,
                PermissionAction.CONFIRM_RATING,
                PermissionAction.ADD_METRIC_TO_RECORD,
                PermissionAction.VIEW_AUDIT_LOG,
                PermissionAction.MANAGE_EMPLOYEES,
                PermissionAction.SYNC_ATTENDANCE,
            }
        )

    if is_admin:
        roles.add("admin")
        permissions.update(
            {
                PermissionAction.MANAGE_SYSTEM_CONFIG,
                PermissionAction.SYNC_ORGANIZATION,
                PermissionAction.SYNC_ATTENDANCE,
                PermissionAction.VIEW_AUDIT_LOG,
            }
        )

    return AccessContext(
        employee_id=employee.id,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
        direct_report_ids=direct_report_ids,
        managed_department_ids=frozenset(managed_department_ids),
        employee_department_ids=frozenset(employee_department_ids),
    )
