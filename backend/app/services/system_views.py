"""系统管理视图服务。

把飞书同步落库的 `department` / `employee` 数据组装成前端系统管理页面
需要的树与列表结构。

设计要点：
- **只读真实数据**：不再返回样例数据；
- **ID 统一为字符串**：飞书侧 `department_id` / `user_id` 均为字符串，
  前端 `system.ts` 里的 `number` 类型需要相应调整；
- **状态双字段**：区分「在职状态」(`employmentStatus`) 与
  「账号状态」(`accountStatus`)，避免离职与禁用混为一谈。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Department, Employee, EmployeeDepartment
from .role_vocabulary import role_choices

# 组织节点类型。飞书只有部门与虚拟组两类，这里按规则归一。
ORG_TYPE_DEPARTMENT = "DEPARTMENT"
ORG_TYPE_GROUP = "GROUP"

# 根节点哨兵值。飞书根部门的 parent_id 就是字符串 "0"，
# 全链路（后端序列化 / 前端比较）必须统一用字符串，不能写成数字 0。
ROOT_DEPARTMENT_ID = "0"


def _org_type(department: Department) -> str:
    """按部门特征归一组织类型。

    飞书「虚拟组」没有显式标记，这里用「有子节点且不是根」近似区分，
    保证前端 `orgType` 选择器有稳定取值。
    """
    return ORG_TYPE_DEPARTMENT


def _account_status_code(account_status: str | None) -> int:
    """账号状态 -> 前端约定的数字（1 启用 / 0 禁用）。"""
    return 1 if (account_status or "enabled") == "enabled" else 0


def list_department_tree(
    db: Session,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """构造部门树。

    节点结构与前端 `DeptNode` 对齐：
    ``{ id, parentId, name, code, orgType, leaderUserId, leaderName,
        sortNo, status, employeeCount, children }``
    """
    departments = db.execute(select(Department)).scalars().all()
    employees = db.execute(select(Employee)).scalars().all()

    employee_by_id = {e.id: e for e in employees}
    # 主部门统计：员工归属的唯一主部门。
    count_by_dept: dict[str, int] = {}
    for employee in employees:
        if employee.department_id:
            count_by_dept[employee.department_id] = count_by_dept.get(employee.department_id, 0) + 1

    children_map: dict[str | None, list[Department]] = {}
    for dept in departments:
        parent = dept.parent_id if dept.parent_id in {d.id for d in departments} else None
        children_map.setdefault(parent, []).append(dept)

    def build(dept: Department) -> dict[str, Any]:
        leader = employee_by_id.get(dept.leader_id or "")
        children = [
            build(child)
            for child in sorted(
                children_map.get(dept.id, []), key=lambda d: (d.name or "")
            )
        ]
        return {
            "id": dept.id,
            # 根节点哨兵值统一为字符串 "0"（飞书根部门 parent_id 就是 "0"）。
            # 不能写成数字 0，否则前端 `===` 严格比较会静默失效。
            "parentId": dept.parent_id or ROOT_DEPARTMENT_ID,
            "name": dept.name,
            # 飞书部门没有独立的组织编码字段，用部门 ID 作为稳定编码。
            "code": dept.id,
            "orgType": _org_type(dept),
            "leaderUserId": dept.leader_id or "",
            "leaderName": leader.name if leader else "",
            "sortNo": 0,
            "status": 1 if dept.active else 0,
            "employeeCount": count_by_dept.get(dept.id, 0),
            "children": children,
        }

    roots = sorted(children_map.get(None, []), key=lambda d: (d.name or ""))
    tree = [build(dept) for dept in roots]

    if include_inactive:
        # 停用且未挂载到任何节点的部门也要能被前端看到。
        mounted = {node["id"] for node in tree}

        def collect(nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                mounted.add(node["id"])
                collect(node.get("children") or [])

        collect(tree)
        for dept in departments:
            if dept.id in mounted:
                continue
            if not include_inactive and not dept.active:
                continue
            tree.append(build(dept))
    return tree


def list_system_options(db: Session) -> dict[str, Any]:
    """系统管理选项：部门树、直属负责人列表、可用角色列表。"""
    departments = list_department_tree(db, include_inactive=False)

    # 负责人候选：拥有下属的在职员工。
    employees = db.execute(select(Employee)).scalars().all()
    leaders = [
        {
            "id": employee.id,
            "label": employee.name,
            "secondary": _department_name(db, employee),
            "status": _account_status_code(employee.account_status),
        }
        for employee in employees
        if employee.employment_status == "active"
        and employee.account_status == "enabled"
    ]

    # ⚠️ **角色候选来自 ``role_vocabulary``（唯一词表）**（2026-11-26）
    #
    # 过去这里是**手写的一组字面量**，而且 ``id`` 与 ``code`` 用了**两套码**
    # （``id`` 小写真实码 / ``code`` 大写自造码，如 ``ADMIN`` / ``EMPLOYEE``），
    # 外加一个后端根本不存在的 ``leader``（真实码是 ``manager``）。
    # ⇒ 前端想用真实码就得看 ``id``，想看 ``code`` 却是自造码 ⇒ 两头不讨好。
    # ⇒ 现在 ``id`` 与 ``code`` **都是规范角色码**。
    roles = role_choices()

    return {"departments": departments, "leaders": leaders, "roles": roles}


def _department_name(db: Session, employee: Employee) -> str:
    if not employee.department_id:
        return ""
    dept = db.get(Department, employee.department_id)
    return dept.name if dept else ""


def list_user_items(
    db: Session,
    *,
    keyword: str | None = None,
    dept_id: str | None = None,
    status: int | None = None,
    page_no: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """员工列表（对应前端 `UserItem`）。

    关键字段映射：
    - ``employmentStatus``：active / inactive / exited（在职状态）；
    - ``accountStatus``：enabled / disabled（账号状态）；
    - ``status``：兼容旧前端约定的数字账号状态；
    - ``roleNames``：按系统授权推导的角色名称。
    """
    stmt = select(Employee)
    if dept_id:
        stmt = stmt.where(Employee.department_id == dept_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Employee.name.like(like)
            | Employee.login_account.like(like)
            | Employee.email.like(like)
            | Employee.id.like(like)
        )

    rows = db.execute(stmt).scalars().all()
    items = [build_user_item(db, employee) for employee in rows]

    if status is not None:
        items = [item for item in items if item["status"] == status]

    total = len(items)
    start = max(0, (page_no - 1) * page_size)
    return {
        "records": items[start : start + page_size],
        "total": total,
        "pageNo": page_no,
        "pageSize": page_size,
    }


def build_user_item(db: Session, employee: Employee) -> dict[str, Any]:
    """构造单个员工条目（对应前端 `UserItem`）。"""
    manager = db.get(Employee, employee.manager_id) if employee.manager_id else None
    role_names = _role_names(db, employee)
    account_enabled = _account_status_code(employee.account_status)
    return {
        "id": employee.id,
        "username": employee.login_account or "",
        "employeeNo": employee.id,
        "realName": employee.name,
        "positionName": employee.position or "",
        # ⚠️ **2026-11-26：真实手机号** —— 原先恒为 ""（字段在），
        # 导致「员工管理」列表的手机号列**永远是空的**。
        # 现在 ``employee.mobile`` 真的有值（见迁移 0046）。
        "mobile": employee.mobile or "",
        "email": employee.email or "",
        "deptId": employee.department_id or "",
        "departmentName": _department_name(db, employee),
        "groupId": "",
        "directLeaderId": employee.manager_id or "",
        "directLeaderName": manager.name if manager else "",
        "roleIds": role_names,
        "roleNames": role_names,
        # 兼容旧前端的数字账号状态。
        "status": account_enabled,
        # 新的双状态字段。
        "accountStatus": employee.account_status or "enabled",
        "employmentStatus": employee.employment_status or "active",
        "forceChangePassword": False,
        "lastLoginAt": None,
        "createdAt": employee.created_at.isoformat() if employee.created_at else "",
    }


def _role_names(db: Session, employee: Employee) -> list[str]:
    """按系统授权推导角色码。

    ⚠️ **角色判定已收敛到 ``access_control.build_access_context``**（2026-11-26）
    ------------------------------------------------------------------
    过去这里是**另写一份**判定逻辑，而且用的是 ``leader`` 而不是 ``manager``、
    还**漏掉了 ``hr``** ⇒ 同一个人的角色在「个人中心」和「员工管理」里
    显示**不一样**（真实发生过）。

    ⇒ 现在直接复用 ``build_access_context`` 的 ``roles`` ——
    它内部走 ``role_vocabulary.derive_roles``，是唯一的判定实现。
    ⇒ 代价：每次推导要多几次查询（列表页 N 行 ⇒ N 次）。
    ⚠️ 员工列表是分页的（默认 20 行），量级可接受；
    若将来要做大批量导出，应改成分批预取 `managed_department_ids` 等。

    返回**规范角色码**（``employee`` / ``manager`` / ``department_leader`` /
    ``hr`` / ``admin``），前端直接拿它渲染中文标签即可。
    """
    from .access_control import build_access_context

    context = build_access_context(db, employee)
    return sorted(context.roles)


def employee_multi_departments(db: Session, employee_id: str) -> list[str]:
    """员工所属的全部部门 ID（主部门在前）。"""
    rows = db.execute(
        select(EmployeeDepartment)
        .where(EmployeeDepartment.employee_id == employee_id)
        .order_by(EmployeeDepartment.is_primary.desc())
    ).scalars().all()
    return [row.department_id for row in rows]


def _data_quality_snapshot(db: Session) -> dict[str, Any]:
    """组织架构数据质量快照。

    与同步对账中的 `data_quality` 口径一致，供系统仪表盘使用。
    """
    employees = db.execute(select(Employee)).scalars().all()
    departments = db.execute(select(Department)).scalars().all()

    no_department = [e.id for e in employees if not e.department_id]
    no_manager = [
        e.id
        for e in employees
        if e.employment_status == "active" and not e.manager_id
    ]
    no_login_account = [
        e.id
        for e in employees
        if e.employment_status == "active" and not e.login_account
    ]
    departments_without_leader = [
        d.id for d in departments if d.active and not d.leader_id
    ]

    # 循环上级关系：沿 manager_id 上溯，命中已访问节点即为环。
    manager_map = {e.id: e.manager_id for e in employees}
    cyclic: list[str] = []
    for employee_id in manager_map:
        seen: set[str] = set()
        current: str | None = employee_id
        while current and current in manager_map:
            if current in seen:
                cyclic.append(employee_id)
                break
            seen.add(current)
            current = manager_map.get(current)

    return {
        "employeeCount": len(employees),
        "departmentCount": len(departments),
        "no_department_count": len(no_department),
        "no_department_ids": sorted(no_department)[:100],
        "no_manager_count": len(no_manager),
        "no_manager_ids": sorted(no_manager)[:100],
        "no_login_account_count": len(no_login_account),
        "no_login_account_ids": sorted(no_login_account)[:100],
        "department_without_leader_count": len(departments_without_leader),
        "department_without_leader_ids": sorted(departments_without_leader)[:100],
        "cyclic_manager_count": len(set(cyclic)),
        "cyclic_manager_ids": sorted(set(cyclic))[:100],
    }
