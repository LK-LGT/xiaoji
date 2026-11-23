"""部门功能板块隔离（2026-11-21）。

⚠️ **为什么单独一个模块**
用户投诉：「别的部门为什么会有剪辑板块？��— **各部门功能板块没有隔离**。

**根因**：前端菜单与路由守卫的过滤维度**只有权限串**，
而 ``pan:workload:view`` 授予了基础 ``employee`` 角色
⇒ 全公司**每个部门**的侧边栏都出现「剪辑工作量」。

⇒ **权限 ≠ 板块**
- **权限**回答「这个人能不能干这件事」（角色维度）
- **板块**回答「这个部门有没有这个业务」（组织维度）

两个维度**必须都在**，否则必然出现「别的部门看到不属于自己的板块」。

⚠️ **为什么不能只在前端过滤**
前端隐藏菜单只是**表象** —— 用户仍可直接敲 URL 访问
⇒ 必须有**后端守卫**，否则是「看不见入口但照样能访问」。

⚠️ **默认关闭**
没有配置行 = 不开通。与框架 ``DataSourcePolicy``「默认不信任」同一套哲学：
漏配导致「功能没出现」（可见、可修），而不是「功能到处乱出现」（不可见、不可修）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.models import Department, Employee
from ..domain.pan_workload_models import DepartmentModule
from .access_control import build_access_context

logger = logging.getLogger(__name__)


#: **网盘剪辑工作量**板块代码。
#:
#: ⚠️ 命名用**业务名**而不是实现名 —— 这个板块将来可能换数据源
#: （网盘 → 其它系统），但对用户来说始终是「剪辑工作量」。
PAN_WORKLOAD_MODULE = "pan_workload"

#: 所有已知板块（供 HR 配置界面下拉用）。
KNOWN_MODULES: tuple[tuple[str, str], ...] = (
    (PAN_WORKLOAD_MODULE, "剪辑工作量"),
)

# ═════════════════════════════════════════════════════════════════════════
# ★ 新增一个板块时的**标准步骤**（照做就不会污染其它部门）
# ═════════════════════════════════════════════════════════════════════════
#
# 用户诉求（2026-11-21）：
#   「以后如果加哪个部门特定的功能板块的时候不会污染到其他部门其他的板块中去」
#
# ⇒ **本文件（服务层）是唯一事实源**，任何新板块都必须走完这 5 步：
#
#   1. **在本文件注册板块代码**
#      新增常量（如 ``QC_WORKLOAD_MODULE = "qc_workload"``），
#      并加进 ``KNOWN_MODULES``（HR 配置界面的下拉框靠它）。
#      ⚠️ 代码用**业务名**而非实现名 —— 数据源将来可能换。
#
#   2. **新建板块自己的路由文件**，并在 router 上挂**板块守卫**：
#      ```python
#      def _require_qc_module(
#          db: Session = Depends(get_db),
#          user: Employee = Depends(get_current_user),
#      ) -> Employee:
#          if not has_module(db, user, QC_WORKLOAD_MODULE):   # ⚠️ 必须是**具体板块**
#              raise HTTPException(status_code=403, detail="当前部门未开通「质检工作量」板块")
#          return user
#
#      router = APIRouter(prefix="/v1/qc/workload", dependencies=[Depends(_require_qc_module)])
#      ```
#      ⚠️⚠️ **必须用 ``has_module(db, user, 具体板块)``**，
#      **不能**用 ``if not enabled_modules_for_employee(db, user)`` ——
#      后者只看「有没有**任一**板块」⇒ 新增第二个板块时，
#      开了 B 板块的部门就能访问 A 板块的 API ⇒ **隔离直接失效**。
#      ⚠️ 守卫挂在 **router 级**（不是逐端点）⇒ 新端点自动受保护。
#
#   3. **前端菜单声明 module**（``frontend/src/navigation/workspaces.ts``）：
#      ```ts
#      { label: '质检工作量', path: '/performance/qc-workload',
#        icon: '▦', permission: 'qc:workload:view', module: 'qc_workload' }
#      ```
#      ⚠️ **必须显式写 ``module``** —— 留空 = 不做板块限制 = 全公司都能看到。
#      ⇒ 这正是「别的部门出现某个部门特有板块」的**直接原因**。
#
#   4. **前端路由守卫已通用**（``canAccessPath`` 按 module 过滤）
#      ⇒ 新增菜单项**无需**改路由守卫代码。
#
#   5. **开配置行**（``department_module`` 表）：
#      走「部门管理 → 功能板块」界面，或调用
#      ``POST /v1/department-modules``。
#      ⚠️ **默认关闭** —— 没有配置行 = 该部门没有这个板块。
#
# ─────────────────────────────────────────────────────────────────────────
# ⚠️ **为什么「默认关闭」是这套设计的核心**
#   漏配 ⇒ 「功能没出现」（**可见、可修**）
#   配错 ⇒ 「功能到处乱出现」（**不可见、不可修**）
#   ⇒ 宁可让人发现「少了功能」，也不让人看到「不该有的功能」。
# ═════════════════════════════════════════════════════════════════════════


def _department_ancestors(db: Session, dept_ids: set[str]) -> set[str]:
    """返回指定部门**及其所有上级部门**（自顶向下闭包）。

    ⚠️ **为什么是「祖先」而不是「后代」**
    板块开通应该**从上往下继承**：
    「运营中心」开通了剪辑工作量 ⇒ 它下面的「短视频」理应能用。

    ⇒ 对一个员工，要查的是**他所在部门 + 该部门的所有上级**
    ⇒ 而不是「他所在部门 + 该部门的下级」。

    ⚠️ **如果搞反了会怎样**
    用「后代」的话 ⇒ 员工在「运营中心」时，
    其 scope 会包含「短视频」⇒ 短视频开通了剪辑板块
    ⇒ **运营中心的老总也会看到剪辑面板**
    ⇒ 又变成「上面的部门莫名看到下面子部门的业务」⇒ 同样不对。

    ⚠️ 与 ``access_control._department_descendants`` **方向相反** ——
    那边是「权限范围」（管着一个部门 ⇒ 权限覆盖其子树），
    这里是「板块范围」（上级开通 ⇒ 子部门继承）。
    """
    if not dept_ids:
        return set()
    parents: dict[str, str] = {}
    for pid, cid in db.execute(
        select(Department.parent_id, Department.id).where(
            Department.parent_id.is_not(None)
        )
    ).all():
        parents[str(cid)] = str(pid)

    result: set[str] = set()
    stack = list(dept_ids)
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        parent = parents.get(current)
        if parent:
            stack.append(parent)
    return result


def employee_department_ids(db: Session, employee: Employee) -> set[str]:
    """员工所属部门集合（主部门 + 兼岗部门）。

    ⚠️ 与 ``access_control.employee_department_ids`` **口径一致** ——
    两边都取「主部门 + 兼岗」，否则会出现
    「板块按兼岗部门开通了，但判定时只看了主部门」的不一致。
    """
    from ..domain.models import EmployeeDepartment

    rows = db.execute(
        select(EmployeeDepartment.department_id).where(
            EmployeeDepartment.employee_id == employee.id
        )
    ).scalars().all()
    result = {str(item) for item in rows if item}
    if employee.department_id:
        result.add(str(employee.department_id))
    return result


def is_admin_or_hr(db: Session, employee: Employee) -> bool:
    """是否是 HR / 系统管理员。

    ⚠️ **HR 与管理员永远不受板块隔离约束** ——
    他们要配置板块、维护绑定、核对全局数据。
    若把他们也挡住，就没人能开通新板块了。
    """
    from ..domain.models import HrDepartmentConfig, SystemAdmin

    # ⚠️ **必须按 ``employee_id`` 查，不能用 ``db.get(SystemAdmin, employee.id)``**。
    #    ``SystemAdmin`` 的**主键是 ``id``**（自动生成），管理员身份挂在
    #    ``employee_id`` 字段上（唯一约束 uq_systemadminemployee）。
    #    ⇒ 用主键查等于拿「员工 ID」去比「管理员记录 ID」，**永远查不到**
    #    ⇒ 管理员被当成普通员工 ⇒ 板块隔离把管理员也挡住了
    #    ⇒ 而管理员正是唯一能开通板块的人 ⇒ 死锁（没人能开通新板块）。
    if (
        db.execute(
            select(SystemAdmin.id).where(
                SystemAdmin.employee_id == employee.id,
                SystemAdmin.active.is_(True),
            )
        ).first()
        is not None
    ):
        return True

    dept_ids = employee_department_ids(db, employee)
    if not dept_ids:
        return False
    hr_departments = set(
        db.execute(
            select(HrDepartmentConfig.department_id).where(
                HrDepartmentConfig.active.is_(True)
            )
        ).scalars().all()
    )
    return bool(dept_ids & {str(x) for x in hr_departments})


def enabled_modules_for_employee(
    db: Session, employee: Employee
) -> set[str]:
    """算出这个员工**能用的板块集合**。

    ⚠️ **判定链**（任一命中即开通）：

    1. **HR / 系统管理员** ⇒ 全部板块
    2. 员工所属部门（含兼岗）**及其所有上级部门**里有任一部门
       开通了该板块 ⇒ 开通
       （⇒ 上级开通 ⇒ 下级继承，符合「部门有该业务」的直觉）
    3. 否则 ⇒ 未开通

    ⚠️ **为什么向上展开（而不是向下）**
    「运营中心」开通了剪辑工作量 ⇒ 它下面的「短视频」理应能用。
    ⇒ 查的是「我所在部门 + 我的所有上级」。

    ⚠️ **如果向下展开会怎样**
    员工在「运营中心」时，其 scope 会包含「短视频」
    ⇒ 短视频开通了剪辑板块 ⇒ **运营中心的老总也会看到剪辑面板**
    ⇒ 变成「上面的部门莫名看到下面子部门的业务」⇒ 同样不对。

    ⚠️ **为什么主管不自动继承下属的板块**
    主管能**看到**下属的数据（那是 ``pan_workload`` 自己的范围问题），
    但「这个部门有没有剪辑这个业务」是**部门属性**，不是汇报线属性。
    ⇒ 主管能否用板块，看**他自己所在部门**是否开通。
    ⇒ 这也避免「运营中心的老总莫名看到剪辑面板」。
    """
    if is_admin_or_hr(db, employee):
        return {code for code, _ in KNOWN_MODULES}

    dept_ids = employee_department_ids(db, employee)
    if not dept_ids:
        return set()

    # ⚠️ 员工所在部门 + 其所有**上级**部门
    scope = _department_ancestors(db, dept_ids)

    rows = db.execute(
        select(DepartmentModule.module_code).where(
            DepartmentModule.department_id.in_(scope),
            DepartmentModule.active.is_(True),
        )
    ).scalars().all()
    return {str(code) for code in rows if code}


def has_module(db: Session, employee: Employee, module_code: str) -> bool:
    """这个员工能不能用这个板块。"""
    return module_code in enabled_modules_for_employee(db, employee)


def module_guard_message(module_code: str) -> str:
    """板块未开通时的**统一提示文案**。

    ⚠️ **文案要统一**
    同一个板块，HTTP 端点与飞书端点都要拒绝 —— 两处各写一句文案，
    用户会以为「飞书和系统是两种不同的业务」。
    ⇒ 提示口径统一为「**这是部门级功能板块**」，
      让用户知道要找 HR 开权限，而不是以为自己操作错了。
    """
    label = dict(KNOWN_MODULES).get(module_code, module_code)
    return (
        f"「{label}」是**部门级功能板块**，"
        "当前所在部门未开通。\n\n"
        "请让 HR 在「系统管理 → 部门功能板块」里为你的部门开通后再试。"
    )


def require_module(module_code: str):
    """构造一个 **FastAPI 依赖**，用作板块守卫。

    ⚠️ **为什么做成「工厂」而不是各路由各写一份**
    板块守卫如果分散在多个路由文件里：
    - 新加路由的人**可能忘记加** ⇒ 隔离失效
    - 两处的判定口径可能**慢慢漂移** ⇒ 同样的板块，两个地方行为不同

    ⇒ 统一从这里取 ⇒ **一处定义，处处生效**。

    Usage:
        >>> router = APIRouter(
        ...     prefix="/v1/xxx",
        ...     dependencies=[Depends(require_module(PAN_WORKLOAD_MODULE))],
        ... )
    """
    from fastapi import Depends, HTTPException

    from ..dependencies import get_current_user

    def _guard(
        db: Session = Depends(get_db),
        employee: Employee = Depends(get_current_user),
    ) -> Employee:
        if not has_module(db, employee, module_code):
            raise HTTPException(
                status_code=403,
                detail=module_guard_message(module_code),
            )
        return employee

    return _guard


#: **部门功能板块管理**权限串。
#:
#: ⚠️ **前端菜单与后端守卫必须用同一个串**
#: —— 否则会出现「菜单看不见但接口能调」或「菜单能看见但接口 403」。
#:
#: ⚠️ **为什么独立于 ``system:org:view``**
#: 「能看部门树」≠「能改板块开通范围」。
#: 板块开关直接决定谁能访问剪辑工作量这类业务接口 ⇒ 必须是 HR 专属动作。
DEPARTMENT_MODULE_MANAGE_PERMISSION = "system:department-module:manage"


def has_department_module_manage(db: Session, employee: Employee) -> bool:
    """这个员工有没有「部门功能板块管理」权限。

    ⚠️ **口径必须与前端一致**
    前端 ``ROLE_UI_PERMISSIONS`` 里 ``hr`` / ``admin`` 两个角色带这个串
    ⇒ 这里按**同一套角色解析**判定（``access_control.build_access_context``）
    ⇒ 避免两处各写一套规则、慢慢漂移。
    """
    from ..routes.auth import ROLE_UI_PERMISSIONS

    context = build_access_context(db, employee)
    for role in context.roles:
        if DEPARTMENT_MODULE_MANAGE_PERMISSION in ROLE_UI_PERMISSIONS.get(role, []):
            return True
    return False
