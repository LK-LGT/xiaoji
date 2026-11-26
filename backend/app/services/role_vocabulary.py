"""角色词表的**唯一事实源**（2026-11-26 建立）。

⚠️ **为什么要有这个模块**
------------------------------------------------------------------
历史上后端同时存在 **4 套**互相对不上的角色码：

| # | 位置 | 用的码 | 问题 |
|---|---|---|---|
| 1 | ``services/access_control.py`` | `employee` / `manager` / `department_leader` / `hr` / `admin` | ✅ **这套是对的** —— ``/v1/auth/me`` 的 ``roles`` 字段、前端角色标签、数据源白名单都按它 |
| 2 | ``services/system_views.py``（员工列表 ``roleNames``） | `employee` / `leader` / `department_leader` / `admin` | ❌ 用 ``leader`` 代替 ``manager``，且**漏了 ``hr``** |
| 3 | ``services/system_views.py``（``/system/options`` 的角色候选） | ``id`` 是小写但 ``code`` 是大写自造码（``ADMIN`` / ``EMPLOYEE`` / ``DEPARTMENT_LEADER`` / ``LEADER``） | ❌ 同一份数据里两套码并存 |
| 4 | ``app/permissions.py`` 的 ``RoleEnum`` | `admin` / `department` / `leader` / `employee` / `reviewer` | ❌ ``department`` 与 ``department_leader`` 对不上；``reviewer`` **这个角色根本不存在** |

⇒ 同一个人的角色，在「个人中心」和「员工管理」里显示**不一样**；
⇒ 前端想显示中文标签时得维护映射表，一旦后端改了码就静默回退成英文
（真实发生过，见 ``frontend/tests/profile-role-labels.test.ts``）。

⚠️ **本模块的定位**
------------------------------------------------------------------
**只放「词表 + 纯推导规则」，不碰数据库** ⇒ 任何模块都能 import，
不会有循环依赖。

- **词表**：``Role`` 枚举 + ``ROLE_LABELS``（中文标签）+ ``LEGACY_ROLE_ALIASES``（历史码归一）。
- **推导规则**：``derive_roles()`` —— 「什么条件算是什么角色」只此一处。

⚠️ **角色是「推导」出来的，不是「分配」出来的**
------------------------------------------------------------------
现在的权限模型**不是** RBAC 角色表，而是「角色 × 部门 × 汇报线」的
**动态判定**（见 ``services/access_control.py``）：

- ``employee``        —— 人人都有（基础角色）
- ``manager``         —— 有下属 / 有汇报线 / 有代理授权
- ``department_leader`` —— 管辖某个部门（飞书同步的 ``leader_id``）
- ``hr``              —— 所属部门命中启用的 HR 部门配置
- ``admin``           —— ``system_admin`` 表显式授权

⇒ **没有「超级管理员」**：管理员是公司内**真实员工**（``system_admin`` 表
绑定 ``employee_id``），只是被授予了系统配置权限，**不是**能随意查看 /
伪装全公司数据的万能账号。

⚠️ **前端必须与本模块保持一致**
------------------------------------------------------------------
前端 ``frontend/src/views/profile/profilePresentation.ts`` 的
``BUILT_IN_ROLE_LABELS`` 是前端侧的同一份词表。
⇒ ``frontend/tests/role-vocabulary-parity.test.ts`` 锁死两边一致。
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """系统全部角色（**唯一**词表）。

    ⚠️ **值必须与前端下发的字符串完全一致** —— 前端按字符串做匹配，
    改这里的值等于改前端契约。
    """

    EMPLOYEE = "employee"
    MANAGER = "manager"
    DEPARTMENT_LEADER = "department_leader"
    HR = "hr"
    ADMIN = "admin"


#: 角色码 → 中文标签。
#:
#: ⚠️ **与前端 ``profilePresentation.ts`` 的 ``BUILT_IN_ROLE_LABELS`` 必须一致**
#: （由 ``frontend/tests/role-vocabulary-parity.test.ts`` 锁定）。
#:
#: ⚠️ **没有「超级管理员」** —— 后端不存在该角色，且这个词会让人误以为
#: 存在能伪装身份的万能账号。
ROLE_LABELS: dict[str, str] = {
    Role.EMPLOYEE: "员工",
    Role.MANAGER: "主管",
    Role.DEPARTMENT_LEADER: "部门负责人",
    Role.HR: "HR",
    Role.ADMIN: "系统管理员",
}


#: 历史遗留角色码 → 规范角色码。
#:
#: ⚠️ **只用于「读进来的是旧数据」时归一**，不是「可以随便用这些码」。
#: ⚠️ 映射规则：
#: - ``SUPER_ADMIN`` / ``SYS_ADMIN`` / ``ADMIN`` → ``admin``
#: - ``DEPT_LEADER`` / ``DEPT_OWNER`` / ``DEPARTMENT_LEADER`` / ``department``
#:   → ``department_leader``
#: - ``DIRECT_LEADER`` / ``TEAM_LEADER`` / ``PROJECT_MANAGER`` / ``LEADER``
#:   → ``manager``
#: - ``EMPLOYEE`` → ``employee``
#:
#: ⚠️ **不映射 ``REVIEWER``** —— 后端从来就没有「裁决评审员」这个角色，
#: 它只存在于废弃的 ``app/permissions.py`` 里。归一它等于凭空造角色。
LEGACY_ROLE_ALIASES: dict[str, str] = {
    "SUPER_ADMIN": Role.ADMIN,
    "SYS_ADMIN": Role.ADMIN,
    "ADMIN": Role.ADMIN,
    "DEPT_LEADER": Role.DEPARTMENT_LEADER,
    "DEPT_OWNER": Role.DEPARTMENT_LEADER,
    "DEPARTMENT_LEADER": Role.DEPARTMENT_LEADER,
    "department": Role.DEPARTMENT_LEADER,
    "DIRECT_LEADER": Role.MANAGER,
    "TEAM_LEADER": Role.MANAGER,
    "PROJECT_MANAGER": Role.MANAGER,
    "LEADER": Role.MANAGER,
    "leader": Role.MANAGER,
    "MANAGER": Role.MANAGER,
    "EMPLOYEE": Role.EMPLOYEE,
}


def normalize_role(code: str) -> str:
    """把任意（可能是历史的）角色码归一成规范角色码。

    ⚠️ **未知码原样返回** —— 不返回空、不抛异常。
    后端新增角色而前端/调用方还没跟上时，至少能看到原始码便于排查，
    总比静默变成一个错角色好。
    """
    if not code:
        return ""
    if code in ROLE_LABELS:
        return code
    return LEGACY_ROLE_ALIASES.get(code, code)


def normalize_roles(codes: object) -> list[str]:
    """归一角色码列表，去重并保持首次出现的顺序。"""
    if not codes:
        return []
    out: list[str] = []
    for raw in codes:  # type: ignore[union-attr]
        code = normalize_role(str(raw))
        if code and code not in out:
            out.append(code)
    return out


def role_label(code: str) -> str:
    """角色码 → 中文标签（未知码原样返回）。"""
    normalized = normalize_role(code)
    return ROLE_LABELS.get(normalized, normalized)


def role_choices() -> list[dict[str, object]]:
    """角色候选列表（给 ``/system/options`` 的角色下拉框用）。

    ⚠️ **``id`` 与 ``code`` 都是规范角色码**（字符串）——
    历史上这里是「``id`` 小写 / ``code`` 大写」两套码并存，
    前端想匹配 ``code === 'EMPLOYEE'`` 又想用真实码，纯粹是自找麻烦。
    """
    return [
        {
            "id": role.value,
            "name": ROLE_LABELS[role],
            "code": role.value,
            "status": 1,
        }
        for role in Role
    ]


def derive_roles(
    *,
    has_subordinates: bool,
    manages_department: bool,
    is_hr: bool,
    is_admin: bool,
) -> frozenset[str]:
    """由「判定证据」推出角色集合。

    ⚠️ **这是「什么条件算是什么角色」的唯一实现** ——
    ``access_control`` 与视图层都调它 ⇒ 两边不可能再漂移。

    Args:
        has_subordinates: 有直属下属 / 汇报线下属 / 代理授权 任一非空。
        manages_department: 管辖某个部门（含下级部门）。
        is_hr: 所属部门命中启用的 HR 部门配置。
        is_admin: ``system_admin`` 表显式授权且启用。

    Returns:
        规范角色码集合。``employee`` 恒在内（基础角色，人人都有）。
    """
    roles: set[str] = {Role.EMPLOYEE}
    if has_subordinates:
        roles.add(Role.MANAGER)
    if manages_department:
        roles.add(Role.DEPARTMENT_LEADER)
    if is_hr:
        roles.add(Role.HR)
    if is_admin:
        roles.add(Role.ADMIN)
    return frozenset(roles)


__all__ = [
    "LEGACY_ROLE_ALIASES",
    "ROLE_LABELS",
    "Role",
    "derive_roles",
    "normalize_role",
    "normalize_roles",
    "role_choices",
    "role_label",
]
