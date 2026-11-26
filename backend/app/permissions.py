"""
权限模型和访问控制 —— 从 ReviewOps 借鉴的设计

设计原则：
1. 所有权限检查都是 PURE 函数（无副作用）
2. 权限检查在数据到达 API 之前执行
3. 权限错误明确区分：401(认证)、403(授权)、404(对象不存在)
"""

from dataclasses import dataclass
from enum import StrEnum

from .services.role_vocabulary import Role


class RoleEnum(StrEnum):
    """系统中的所有角色。

    ⚠️⚠️ **已废弃（2026-11-26）—— 本模块整体不再被任何代码引用**
    ------------------------------------------------------------------
    这个文件是「阶段一权限模型」的遗留物，从 ReviewOps 借鉴的设计，
    但项目后来改用了「角色 × 部门 × 汇报线」的**动态判定**
    （``services/access_control.py`` + ``services/role_vocabulary.py``），
    ⇒ 本文件里的 ``can_user_*`` / ``assert_user_can_*`` **一个都没被调用**。

    ⚠️ **为什么还留着**
        本文件里的 ``RoleEnum`` 曾是**第 4 套**角色词表，且与真实词表对不上：
        - ``DEPARTMENT_LEADER = "department"`` —— 真实码是 ``department_leader``；
        - ``TEAM_LEADER = "leader"`` —— 真实码是 ``manager``；
        - ``REVIEWER = "reviewer"`` —— **后端根本不存在「裁决评审员」这个角色**。

        ⇒ 留着它就等于留着一个「看起来是权威、其实全错」的词表，
          后人照着它写代码必然出错。

    ✅ **现在它只是「角色词表」的单薄转发**
        所有角色码都从 ``services/role_vocabulary.py`` 取 ——
        那是**唯一事实源** ⇒ 本文件不再可能与真实词表漂移。

    ✅ **真实入口**
        - 角色词表与推导 → ``services/role_vocabulary.py``
        - 权限判定 → ``services/access_control.py``
        - 角色中文标签 → ``services/role_vocabulary.ROLE_LABELS``
    """

    # ⚠️ 直接转发到唯一事实源 —— 不再自己定义一份。
    EMPLOYEE = Role.EMPLOYEE
    MANAGER = Role.MANAGER
    DEPARTMENT_LEADER = Role.DEPARTMENT_LEADER
    HR = Role.HR
    ADMIN = Role.ADMIN


class PermissionError(Exception):
    """403 Forbidden"""
    def __init__(self, message="Access denied"):
        super().__init__(message)
        self.status_code = 403


class AuthenticationError(Exception):
    """401 Unauthorized"""
    def __init__(self, message="Not authenticated"):
        super().__init__(message)
        self.status_code = 401


class NotFoundError(Exception):
    """404 Not Found"""
    def __init__(self, message="Resource not found"):
        super().__init__(message)
        self.status_code = 404


@dataclass
class User:
    """当前用户上下文"""
    user_id: str
    username: str
    roles: list[str]              # 用户的所有角色
    permissions: list[str]        # 用户的权限列表
    dept_id: str | None = None  # 部门ID
    is_admin: bool = False         # 是否是系统管理员


@dataclass
class Employee:
    """员工数据引用 —— 用于权限检查"""
    id: str
    dept_id: str
    manager_id: str | None = None  # 直属领导 ID
    department_manager_id: str | None = None  # 部门负责人 ID


@dataclass
class Plan:
    """计划数据引用 —— 用于权限检查"""
    id: str
    owner_id: str
    department_id: str
    status: str  # draft, submitted, approved, rejected
    type: str    # MONTH, WEEK, DAY


# ============================================================================
# 核心权限检查函数 —— PURE FUNCTIONS
# ============================================================================

def can_user_view_employee(user: User, employee: Employee) -> bool:
    """
    用户是否可以查看员工数据

    允许条件：
    1. 用户是管理员
    2. 用户是该员工本人
    3. 用户是该员工的直属领导
    4. 用户是该员工所在部门的负责人
    """
    if user.is_admin:
        return True
    if user.user_id == employee.id:
        return True
    if user.user_id == employee.manager_id:  # 直属领导
        return True
    if user.user_id == employee.department_manager_id:  # 部门负责人
        return True
    return False


def can_user_edit_employee(user: User, employee: Employee) -> bool:
    """
    用户是否可以编辑员工信息

    允许条件：
    1. 用户是管理员
    2. 用户是员工本人（仅限个人资料）
    3. 用户是该员工所在部门的负责人
    """
    if user.is_admin:
        return True
    if user.user_id == employee.id:
        return True  # 可以编辑自己的资料
    if user.user_id == employee.department_manager_id:
        return True  # 部门负责人可以编辑部门员工
    return False


def can_user_manage_employees(user: User) -> bool:
    """用户是否可以进行员工管理（注册、启用/禁用、权限分配）"""
    return "system:employee:view" in user.permissions


def can_user_view_plan(user: User, plan: Plan) -> bool:
    """
    用户是否可以查看计划

    允许条件：
    1. 用户是管理员
    2. 用户是计划所有者
    3. 用户是计划所有者的直属领导（可以审批）
    4. 用户是计划所有者所在部门的负责人（可以审批）
    5. 计划已发布且用户在同一组织范围内
    """
    if user.is_admin:
        return True
    if user.user_id == plan.owner_id:
        return True
    # 需要更多上下文来判断领导关系（这里简化）
    return False


def can_user_edit_plan(user: User, plan: Plan) -> bool:
    """
    用户是否可以编辑计划

    允许条件：
    1. 用户是计划所有者且计划还在草稿状态
    2. 用户是管理员
    """
    if user.is_admin:
        return True
    if user.user_id == plan.owner_id and plan.status == "draft":
        return True
    return False


def can_user_approve_plan(user: User, plan: Plan, approver_role: str) -> bool:
    """
    用户是否可以批准计划

    approver_role: 审批人角色（**规范角色码**）
        （``manager`` / ``department_leader`` / ``admin``）

    ⚠️ **历史坑（已修，2026-11-26）**
        这里过去写的是 ``'department_head'`` / ``'department'`` ——
        两者都**不是**真实角色码（真实码是 ``department_leader``）
        ⇒ 传 ``department_head`` 会静默走到最后一行返回 ``False``，
        即「部门负责人永远批不了计划」。
    """
    if user.is_admin:
        return True
    if approver_role == Role.MANAGER:
        return "leader:month-approval:view" in user.permissions
    if approver_role == Role.DEPARTMENT_LEADER:
        return "department:month-approval:view" in user.permissions
    return False


def can_user_submit_result(user: User, employee_id: str, owner_id: str) -> bool:
    """
    用户是否可以提交成果

    允许条件：
    1. 用户是成果所有者
    2. 用户是该员工的直属领导（代理提交）
    3. 用户是管理员
    """
    if user.is_admin:
        return True
    if user.user_id == owner_id:
        return True
    if "leader:workbench:view" in user.permissions:  # 领导可以代理提交
        return True
    return False


def can_user_confirm_result(user: User, employee_id: str) -> bool:
    """
    用户是否可以确认（批准）成果

    允许条件：
    1. 用户是管理员
    2. 用户是该员工的直属领导
    3. 用户是该员工所在部门的负责人
    """
    if user.is_admin:
        return True
    if "leader:result-suggest:view" in user.permissions:
        return True
    if "department:result-confirm:view" in user.permissions:
        return True
    return False


# ============================================================================
# 断言函数 —— 用于 API 中的权限检查
# ============================================================================

def assert_user_can_view_employee(user: User, employee: Employee | None) -> None:
    """断言：用户可以查看员工"""
    if not employee:
        raise NotFoundError("Employee not found")
    if not can_user_view_employee(user, employee):
        raise PermissionError("Cannot view this employee")


def assert_user_can_edit_employee(user: User, employee: Employee | None) -> None:
    """断言：用户可以编辑员工"""
    if not employee:
        raise NotFoundError("Employee not found")
    if not can_user_edit_employee(user, employee):
        raise PermissionError("Cannot edit this employee")


def assert_user_can_manage_employees(user: User) -> None:
    """断言：用户可以管理员工"""
    if not can_user_manage_employees(user):
        raise PermissionError("No permission to manage employees")


def assert_user_can_view_plan(user: User, plan: Plan | None) -> None:
    """断言：用户可以查看计划"""
    if not plan:
        raise NotFoundError("Plan not found")
    if not can_user_view_plan(user, plan):
        raise PermissionError("Cannot view this plan")


def assert_user_can_edit_plan(user: User, plan: Plan | None) -> None:
    """断言：用户可以编辑计划"""
    if not plan:
        raise NotFoundError("Plan not found")
    if not can_user_edit_plan(user, plan):
        raise PermissionError("Cannot edit this plan")
