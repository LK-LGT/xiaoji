"""HR 与系统管理员的角色判定（2026-11-24）。

⚠️ **为什么单独一个模块**
HR 与「系统管理员」是两个**来源完全不同**的角色，
但过去在多个路由里被写成 ``{"hr", "admin"} & set(ctx.roles)`` —— **合并成一个口径**，
于是「系统管理员」被白送了 HR 的**跨部门数据范围**。

🔴 **实测到的越权（2026-11-24）**
管理员账号登录后调 ``/v1/baidu/workload/summary``，
返回**全公司所有剪辑**的数据（巫宜珊 / 佟瑶 / 李婵雨 …），
而该管理员在人事行政部，与短视频部**无任何汇报线关系**。

⚠️ **这违反了业务规则**
「管理员角色也是公司内真实员工账号，只是分配了【绩效配置管理员】权限」——
  管理员是**系统配置**的运维角色，**不是**跨部门看绩效数据的角色。

    ⇒ 角色来源决定了数据范围：
      - ``hr``     ← ``hr_department_config``（HR 部门配置）  ⇒ **可跨部门看绩效数据**
      - ``admin``  ← ``system_admin`` 表（显式系统授权）      ⇒ **只管配置，不自动获得业务数据范围**

两者的**管理动作**权限（配置板块、维护绑定、同步组织）依然共享 ——
 那是「谁来点开通按钮」的问题，与「能看到谁的数据」无关。

⚠️ **为什么不能靠 ``access_control`` 的角色集合直接判**
``build_access_context`` 会把角色**并集**塞进 ``ctx.roles``，
其中 ``admin`` 角色的权限位只含 4 项系统管理权限（不含 ``VIEW_ALL_SCORES``）
⇒ **权限位是对的，坏的是路由层按角色名做的数据范围判定**。

⚠️ **口径统一**
本模块是「谁是 HR」「谁是系统管理员」的**唯一事实源** ——
  路由层不再自己拼角色集合，避免以后又写出 ``{"hr", "admin"}``。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Employee, HrDepartmentConfig, SystemAdmin

logger = logging.getLogger(__name__)


def is_system_admin(db: Session, employee: Employee) -> bool:
    """是否是**系统管理员**（``system_admin`` 表显式授权）。

    ⚠️ **必须按 ``employee_id`` 查，不能用 ``db.get(SystemAdmin, employee.id)``**。
    ``SystemAdmin`` 的主键是 ``id``（自动生成 UUID），管理员身份挂在
    ``employee_id`` 字段上（唯一约束 ``uq_systemadminemployee``）。
    ⇒ 用主键查等于拿「员工 ID」去比「管理员记录 ID」，**永远查不到**
    ⇒ 管理员被当成普通员工 ⇒ 板块隔离把管理员也挡住了
    ⇒ 而管理员正是唯一能开通板块的人 ⇒ 死锁（没人能开通新板块）。

    ⚠️ **停用后立刻失效**（``active=False``）—— 离职管理员不该保留全板块。
    """
    return (
        db.execute(
            select(SystemAdmin.id).where(
                SystemAdmin.employee_id == employee.id,
                SystemAdmin.active.is_(True),
            )
        ).first()
        is not None
    )


def is_hr(db: Session, employee: Employee) -> bool:
    """是否是 **HR**（成员属于任一启用的 HR 部门）。

    ⚠️ **口径**：与 ``access_control.build_access_context`` 的 ``is_hr`` **完全一致** ——
    两处各写一份必然漂移 ⇒ 显式说明依赖关系：
    HR 身份 = 「所属部门（主部门 + 兼岗）∩ 启用的 HR 部门配置」非空。

    ⇒ **HR 可以跨部门看绩效数据**（业务口径确认，2026-11-24）。
    """
    from .department_modules import employee_department_ids

    dept_ids = employee_department_ids(db, employee)
    if not dept_ids:
        return False

    hr_departments = {
        str(x)
        for x in db.execute(
            select(HrDepartmentConfig.department_id).where(
                HrDepartmentConfig.active.is_(True)
            )
        ).scalars().all()
    }
    return bool(dept_ids & hr_departments)


def can_view_all_scores(db: Session, employee: Employee) -> bool:
    """能否**跨部门**看绩效数据。

    ⚠️ **只有 HR 可以** —— 系统管理员**不**自动获得（2026-11-24 规则修订）。

    ⚠️ **为什么管理员不给**
    管理员是**系统配置**的运维角色（谁点开通板块、谁同步组织架构），
    不是「要看全公司绩效」的角色。
    ⇒ 管理员若需查看某部门数据，走**真实身份**（他是某部门负责人时自然能看到）
    或走**代理授权**（``delegation``）—— 都是可审计的路径，
    而不是「因为是管理员所以自动全放行」。

    ⇒ 这样管理员账号**不能**伪装成万能账号看页面，
    与「真实员工动态判定可见范围」的业务规则一致。
    """
    return is_hr(db, employee)


__all__ = [
    "can_view_all_scores",
    "is_hr",
    "is_system_admin",
]
