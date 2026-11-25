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
from dataclasses import dataclass
from typing import Any

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

#: ★ **部门工作量**板块代码（2026-11-25 新增）。
#:
#: ⚠️ **为什么要有这个通用板块**
#: --------------------------
# 之前**只有** ``pan_workload`` 一个板块 ⇒ 只有短视频能开通。
# ⇒ 别的部门（仓储/客服/电商/直播…）**没有可填的东西**。
#:
#: ⚠️ **为什么不每个部门建一个板块**
#:   26 个叶子部门 ⇒ 26 个板块 ⇒ 26 份几乎一样的路由/页面/前端菜单。
#: ⇒ 而各部门的**差异只在「指标填什么」**，那本来就由
#:   ``pan_workload_policy``（部门 × 岗位）承载。
#:
#: ⇒ **一个通用板块 + 岗位口径** = 隔离照旧（按部门开通），
#:   但没有代码重复。
#:
#: ⚠️ **与 ``pan_workload`` 的区别**
#:   ``pan_workload`` 有**百度网盘采集**能力（自动抓成片条数），
#:   只有短视频用得上 ⇒ 保持独立，不混进来。
#:   ``dept_workload`` 是**纯人工填报** —— 各部门都能用。
DEPT_WORKLOAD_MODULE = "dept_workload"

#: 所有已知板块（供 HR 配置界面下拉用）。
KNOWN_MODULES: tuple[tuple[str, str], ...] = (
    (PAN_WORKLOAD_MODULE, "剪辑工作量"),
    (DEPT_WORKLOAD_MODULE, "部门工作量"),
)


# ═════════════════════════════════════════════════════════════════════════
# ★ **板块 → 子模块** 注册表（2026-11-24）
#
# ⚠️ **为什么需要这一层（用户诉求）**
# ----------------------------------
# 用户原话（2026-11-24）：
#
#   「在【部门功能板块】这个配置菜单里做配置：
#     1. 新增板块：短视频部
#     2. 在短视频板块下面，挂载子模块：**剪辑工作量**
#     3. 侧边栏菜单**不是写死固定**，是**动态渲染**：
#        - 系统读取当前登录人的部门；
#        - 读取这个部门绑定了哪些功能子模块；
#        - 只有这个部门的人员，侧边栏才会加载出【短视频 → 剪辑工作量】；
#        - 其他部门的账号，侧边栏**根本不会出现这个入口**」
#
# ⇒ 之前是「**模块**直接写死在菜单里」，现在是
#   「**板块（部门维度）→ 子模块（功能维度）**」两级。
#
# ⚠️ **为什么子模块要挂在板块下面，而不是平铺**
# 平铺的话，将来「短视频」有 3 个子模块时，侧边栏会出现
# 「剪辑工作量 / 直播数据 / 质检工作量」三个平级项
# ⇒ 用户看不出「这三个都是短视频的业务」⇒ 分组更清晰。
#
# ⚠️ **子模块声明的字段**
# - ``code`` —— 子模块编码（唯一）
# - ``label`` —— 侧边栏显示名
# - ``module_code`` —— 归属哪个板块
# - ``path`` —— 路由路径（前端跳转用）
# - ``icon`` —— 侧边栏图标
# - ``permission`` —— 访问该子模块需要的权限串（角色维度）
#
# ⚠️ **为什么路由路径也放在后端**
# 前端菜单若自己写死路径，就出现「两份真相」——
# 后端说子模块叫 A，前端跳到 B ⇒ 404。
# ⇒ 路径由后端下发，前端只**渲染**不**定义**。

@dataclass(frozen=True)
class SubModuleSpec:
    """一个板块下的**子模块**（侧边栏的一个可点击入口）。"""

    #: 子模块编码（全局唯一）
    code: str
    #: 中文名（侧边栏显示）
    label: str
    #: 归属的板块代码
    module_code: str
    #: 路由路径
    path: str
    #: 侧边栏图标
    icon: str = ""
    #: 访问所需权限串（角色维度）
    permission: str = ""


#: ★ **子模块注册表** —— 所有板块的子模块都在这里登记。
#:
#: ⚠️ **新增子模块的标准步骤**
#: ① 在这里加一条 ``SubModuleSpec``
#: ② 若它是新板块，还要在 ``KNOWN_MODULES`` 加板块
#: ③ 前端**不用改** —— 菜单由后端下发的目录动态渲染
SUB_MODULES: tuple[SubModuleSpec, ...] = (
    SubModuleSpec(
        code="pan_workload",
        label="剪辑工作量",
        module_code=PAN_WORKLOAD_MODULE,
        path="/performance/pan-workload",
        icon="▦",
        permission="pan:workload:view",
    ),
    # ★ 通用「部门工作量」—— 纯人工填报，各部门共用。
    # ⚠️ **权限串与剪辑板块共用** —— 两者是**同一套填报机制**，
    #   区别只在「哪个部门开通」+「填什么指标」。
    #   ⇒ 分成两个权限位只会让 HR 配置时多一道无意义的区分。
    SubModuleSpec(
        code="dept_workload",
        label="部门工作量",
        module_code=DEPT_WORKLOAD_MODULE,
        path="/performance/dept-workload",
        icon="▤",
        permission="pan:workload:view",
    ),
)

#: 子模块编码 → 规格（按 code 索引）
SUB_MODULE_MAP: dict[str, SubModuleSpec] = {spec.code: spec for spec in SUB_MODULES}

#: 板块代码 → 该板块下的子模块列表
SUB_MODULES_BY_MODULE: dict[str, tuple[SubModuleSpec, ...]] = {
    module_code: tuple(spec for spec in SUB_MODULES if spec.module_code == module_code)
    for module_code, _ in KNOWN_MODULES
}


def sub_modules_for(module_code: str) -> tuple[SubModuleSpec, ...]:
    """取某个板块下的子模块。"""
    return SUB_MODULES_BY_MODULE.get(module_code, ())

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
    from .hr_admin_roles import is_hr, is_system_admin

    # ⚠️ **必须按 ``employee_id`` 查，不能用 ``db.get(SystemAdmin, employee.id)``**。
    #    ``SystemAdmin`` 的**主键是 ``id``**（自动生成），管理员身份挂在
    #    ``employee_id`` 字段上（唯一约束 uq_systemadminemployee）。
    #    ⇒ 用主键查等于拿「员工 ID」去比「管理员记录 ID」，**永远查不到**
    #    ⇒ 管理员被当成普通员工 ⇒ 板块隔离把管理员也挡住了
    #    ⇒ 而管理员正是唯一能开通板块的人 ⇒ 死锁（没人能开通新板块）。
    if is_system_admin(db, employee):
        return True

    return is_hr(db, employee)


def enabled_modules_for_employee(
    db: Session, employee: Employee
) -> set[str]:
    """算出这个员工**能用的板块集合**。

    ⚠️ **判定链**（任一命中即开通）：

    1. **HR / 系统管理员** ⇒ 全部板块
    2. 员工所属部门（含兼岗）**及其所有上级部门**里有任一部门
       开通了该板块 ⇒ 开通
       （⇒ 上级开通 ⇒ 下级继承，符合「部门有该业务」的直觉）
    3. **管辖范围**里有任一部门开通了该板块 ⇒ 开通
       （⇒ 部门负责人管的下级部门开通了 ⇒ 负责人也能用）
    4. 否则 ⇒ 未开通

    ⚠️ **为什么主管不自动继承下属的板块**
    主管能**看到**下属的数据（那是 ``pan_workload`` 自己的范围问题），
    但「这个部门有没有剪辑这个业务」是**部门属性**，不是汇报线属性。
    ⇒ 普通主管能否用板块，看**他自己所在部门**是否开通。

    ⚠️ **但「部门负责人」是例外（2026-11-24 实测修复）**
    真实组织：

        运营中心 (leader=杨飞)
        └── 短视频 (leader=刘旭)  ← 只有这里开通了 pan_workload
            └── 张洁 / 李婵雨 / 佟瑶

    杨飞坐在「总经办」，按规则 2 查出来是 ``set()``
    ⇒ 他**明明是短视频的负责人上级**却**看不到剪辑工作量**，
    只能去问刘旭要数据 ⇒ 业务口径确认：**负责人必须能看到**。

    ⇒ 规则 3：若本人**管辖**（``managed_department_ids``，已含子树）
    的任一部门开通了该板块 ⇒ 本人也能用。

    ⚠️ **为什么这不会造成「别的部门看到不属于自己的板块」**
    ``managed_department_ids`` 只包含**自己管的**部门及其**子树**
    ⇒ 短视频开通 ⇒ 杨飞（管短视频）开通；
    但「人事行政部」的普通员工 ``managed_department_ids`` 为空 ⇒ 不受影响。

    ⚠️ **为什么只对部门负责人开放**
    「管着一个部门」是**组织授权**，据此下钻板块是合理的；
    而「仅仅是个有下属的普通主管」不自动获得板块（板块是部门属性）。
    ⇒ 只在 ``department_leader`` 角色且有管辖部门时才生效。
    """
    if is_admin_or_hr(db, employee):
        return {code for code, _ in KNOWN_MODULES}

    dept_ids = employee_department_ids(db, employee)
    if not dept_ids:
        return set()

    # ⚠️ 员工所在部门 + 其所有**上级**部门
    scope = _department_ancestors(db, dept_ids)

    # ⚠️ 2026-11-24：部门负责人额外看**自己管辖的部门树**
    #    （``managed_department_ids`` 已含子树 ⇒ 短视频开通 ⇒ 运营中心负责人开通）
    scope = scope | _managed_department_ids(db, employee)

    rows = db.execute(
        select(DepartmentModule.module_code).where(
            DepartmentModule.department_id.in_(scope),
            DepartmentModule.active.is_(True),
        )
    ).scalars().all()
    return {str(code) for code in rows if code}


def _managed_department_ids(db: Session, employee: Employee) -> set[str]:
    """这个员工**管辖的部门集合**（含子树）；不是负责人则为空。

    ⚠️ **为什么走 ``access_context`` 而不是自己查 ``Department.leader_id``**
    ``access_control`` 是「数据范围」的唯一事实源
    （含代理授权、兼岗、子树展开等口径）
    ⇒ 板块范围与数据范围**必须同源**
    ⇒ 否则会出现「菜单看得见但接口 403」或「接口能调但菜单不显示」。

    ⚠️ **为什么必须带 ``department_leader`` 角色判定**
    ``managed_department_ids`` 非空即代表「在组织上管着这个部门」
    ⇒ 只有部门负责人才据此下钻板块。
    """
    try:
        context = build_access_context(db, employee)
    except Exception:  # pragma: no cover - 兜底：解析失败不应阻断登录
        logger.warning("解析管辖范围失败，按无管辖处理", exc_info=True)
        return set()

    if "department_leader" not in context.roles:
        return set()

    return {str(did) for did in (getattr(context, "managed_department_ids", None) or set())}


def has_module(db: Session, employee: Employee, module_code: str) -> bool:
    """这个员工能不能用这个板块。"""
    return module_code in enabled_modules_for_employee(db, employee)


def module_role_for_employee(
    db: Session, employee: Employee, module_code: str
) -> str:
    """这个员工在某个板块里的**岗位角色**。

    ⚠️ **为什么菜单要按角色再过滤一层（2026-11-24）**
    ----------------------------------------------
    板块是**部门级**的，但部门内部**岗位不同、参与方式不同**：

        短视频部
            剪辑 → reporter（要填报）
            编导 → reviewer（只审）
            运营 → reviewer（只审）
            主播 → none（**不参与**）

    板块开通后，**主播也会看到「剪辑工作量」入口**
    ⇒ 点进去发现自己一条数据都没有
    ⇒ **这正是用户投诉的「看得见点进去空数据」的残留部分**。

    ⇒ 菜单按**岗位角色**再过滤：
    - ``none`` ⇒ **完全不显示**该子模块
    - ``reporter`` / ``reviewer`` ⇒ 显示（页面内部再按角色渲染内容）

    ⚠️ **为什么复用 ``pan_workload_policy`` 而不是另建一张表**
    岗位口径表**已经存在**且是飞书推送、卡片渲染的权威口径
    ⇒ 再建一张表 ⇒ 两份真相 ⇒ 迟早漂移。

    ⚠️ **判定顺序（从具体到宽泛）**
    ① HR / 管理员 ⇒ ``reviewer``（要核对全局数据）
    ② **自己所在部门**（含兼岗）有岗位口径 ⇒ 用它
       （剪辑→reporter / 编导→reviewer / 主播→none）
    ③ **自己是负责人**，且管辖树里有部门开通了该板块 ⇒ ``reviewer``
       （杨飞坐在总经办，但管着运营中心→短视频 ⇒ 他必须能看到）
    ④ 其它 ⇒ ``none``

    ⚠️ **为什么 ③ 是兜底而不是主规则**
    岗位口径表是**部门内部**的事（谁填、谁审）
    ⇒ 一个「上级部门的负责人」在这个部门里**没有岗位**
    ⇒ 若严格要求「必须有岗位口径」，负责人反而看不到
    ⇒ 业务口径确认：**负责人必须能看到**（2026-11-24）。
    """
    if is_admin_or_hr(db, employee):
        return "reviewer"

    from .pan_workload_policy import resolve_policy

    # ② 自己所在部门（含兼岗）的岗位口径
    for dept_id in employee_department_ids(db, employee):
        policy = resolve_policy(
            db, dept_id, str(employee.position or ""), module_code
        )
        if policy.role in ("reporter", "reviewer"):
            return policy.role
        # ⚠️ **配了但明确是「不参与」** ⇒ 以此为准
        #   （主播在短视频配的就是 none ⇒ 不该被兜底规则捞回来）
        if policy.role == "none" and policy.configured:
            return "none"

    # ③ 负责人兜底 —— 管辖树里有部门开通了该板块 ⇒ reviewer
    if _managed_department_ids(db, employee) & _module_department_ids(
        db, employee, module_code
    ):
        return "reviewer"

    # ④ 不参与
    return "none"


def _module_department_ids(
    db: Session, employee: Employee, module_code: str
) -> set[str]:
    """**开通了该板块**的部门集合（在员工的判定范围内）。

    ⚠️ **为什么与 ``enabled_modules_for_employee`` 用同一套范围**
    否则会出现「板块开着但角色算成 none」这种自相矛盾的状态。
    """
    scope = _department_ancestors(db, employee_department_ids(db, employee))
    scope = scope | _managed_department_ids(db, employee)
    rows = db.execute(
        select(DepartmentModule.department_id).where(
            DepartmentModule.module_code == module_code,
            DepartmentModule.active.is_(True),
            DepartmentModule.department_id.in_(scope),
        )
    ).scalars().all()
    return {str(row) for row in rows if row}


def build_module_menu(
    db: Session,
    employee: Employee,
    permissions: list[str] | set[str],
) -> list[dict[str, Any]]:
    """构造这个员工**能看到的板块菜单**（板块 → 子模块，两级）。

    ⚠️ **这就是「动态渲染菜单」的服务端实现**
    ----------------------------------------------
    前端**不再写死**「剪辑工作量」这个菜单项
    ⇒ 而是拿本函数的返回值**渲染** ⇒ 板块没开通就没有入口。

    ⚠️ **三道过滤，缺一不可**
    ① **板块维度**（组织）：这个部门（及其管辖树）开通了哪些板块
    ② **角色维度**（岗位）：这个人在该板块里是 reporter / reviewer / none
       —— ``none`` ⇒ 该子模块**不显示**
    ③ **权限维度**（角色）：子模块声明的 ``permission`` 是否命中

    ⚠️ **为什么返回「板块 → 子模块」两级而不是扁平列表**
    用户诉求明确：「短视频 → 剪辑工作量」是**有层级**的
    ⇒ 前端渲染成可展开的分组。

    ⚠️ **为什么 HR / 管理员看到全部子模块**
    他们要配置板块、核对全局数据 ⇒ 不能被岗位口径挡住。
    """
    enabled = enabled_modules_for_employee(db, employee)
    if not enabled:
        return []

    perm_set = set(permissions or ())
    result: list[dict[str, Any]] = []

    for module_code, module_label in KNOWN_MODULES:
        if module_code not in enabled:
            continue

        role = module_role_for_employee(db, employee, module_code)
        if role == "none":
            # ⚠️ **该板块的岗位口径是「不参与」** ⇒ 整个板块不显示。
            continue

        children: list[dict[str, Any]] = []
        for spec in sub_modules_for(module_code):
            if spec.permission and spec.permission not in perm_set:
                continue
            children.append(
                {
                    "code": spec.code,
                    "label": spec.label,
                    "path": spec.path,
                    "icon": spec.icon,
                    "permission": spec.permission,
                    "moduleCode": module_code,
                }
            )

        if not children:
            # ⚠️ **没有任何子模块可见** ⇒ 板块也不显示
            #   （否则会出一个「点开是空的」分组）
            continue

        result.append(
            {
                "code": module_code,
                "label": module_label,
                "role": role,
                "children": children,
            }
        )

    return result


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
