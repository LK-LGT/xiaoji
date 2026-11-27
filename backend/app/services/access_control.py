"""阶段一统一权限上下文。

角色来源：
- HR：员工所属任一部门命中启用的 HR 部门配置。
- 部门负责人：飞书同步的部门 leader_id。
- 直属主管：员工存在直属下属。
- 系统管理员：必须由 system_admin 表显式授权。

管理员仅拥有系统管理权限，不自动获得员工绩效等业务数据范围。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import (
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
    SystemAdmin,
)
from .role_vocabulary import Role, derive_roles

logger = logging.getLogger(__name__)


class PermissionAction(StrEnum):
    """权限标识。"""

    VIEW_OWN_SCORE = "view_own_score"
    VIEW_DIRECT_REPORT_SCORE = "view_direct_report_score"
    VIEW_ALL_SCORES = "view_all_scores"
    #: ★ P0：同部门**同级别**同事互查（**只读**）。
    #:
    #: ⚠️ 这个权限**只**出现在 ``can_view_score_of`` 里，
    #:    **绝不**出现在 ``can_write_score_of`` 里 ——
    #:    「能查不能改」的权限层落点。
    #:
    #: ⚠️ 若把它接进写接口（如指标实测值写入），等于
    #:    「能查同事分数」顺带变成「能改同事分数」，
    #:    而指标实测值是算分的唯一输入 —— 那就是变相改分。
    VIEW_DEPARTMENT_PEER = "view_department_peer"
    SUBMIT_OWN_REVIEW = "submit_own_review"
    SUBMIT_DIRECT_REPORT_REVIEW = "submit_direct_report_review"
    CONFIRM_RATING = "confirm_rating"
    ADD_METRIC_TO_RECORD = "add_metric_to_record"
    VIEW_AUDIT_LOG = "view_audit_log"
    MANAGE_EMPLOYEES = "manage_employees"
    MANAGE_SYSTEM_CONFIG = "manage_system_config"
    SYNC_ORGANIZATION = "sync_organization"
    SYNC_ATTENDANCE = "sync_attendance"
    #: ★ 2026-11-27：企业知识库的**写**操作（上传 / 换版 / 停用 / 删除 / 同步）。
    #:
    #: ⚠️ **为什么单独一个权限位**
    #: 知识文档是**AI 检索的依据来源** ⇒ 停用一篇文档 =
    #: 让 AI 少一条依据；删除一篇 = 让 AI 少一条依据。
    #: ⇒ 这是**影响 AI 输出**的写操作，不能只校验「已登录」。
    MANAGE_KNOWLEDGE = "manage_knowledge"


@dataclass(frozen=True)
class AccessContext:
    """当前用户的角色、权限和数据范围快照。"""

    employee_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    direct_report_ids: frozenset[str] = field(default_factory=frozenset)
    #: ★ P0：汇报线**全链**下属（含间接下属）。
    #:
    #: ⚠️ 与 ``direct_report_ids`` 的区别：
    #:    前者是"直属下属"（名字与语义都保持原样，既有调用方不受影响），
    #:    后者是"汇报线上的全部下属"（闭包）。
    #:    权限判定用**后者**，这样主管才能查到"下属的下属"。
    reporting_line_ids: frozenset[str] = field(default_factory=frozenset)
    managed_department_ids: frozenset[str] = field(default_factory=frozenset)
    employee_department_ids: frozenset[str] = field(default_factory=frozenset)
    #: P1-4 代理授权：受托人通过委托获得的员工范围。
    #: 到期后自动缩小 —— 委托失效不需要任何后台任务去「撤销」。
    delegated_employee_ids: frozenset[str] = field(default_factory=frozenset)
    #: ★ P0：我的直属上级（飞书汇报线）。
    #:
    #: ⚠️ 为什么只存 id 不存 Employee 对象：
    #:    这个 dataclass 是 ``frozen`` 的**轻量快照**，被大量地方传递；
    #:    存整个 ORM 对象会把 session 依赖带进快照，跨 session 使用会炸
    #:    （DetachedInstanceError）。
    #:    同级判定只需要「同一个 manager_id」，一个字符串足够。
    #:
    #: ⚠️ 为什么需要它：P0 的「同一级」判定基于汇报线
    #:    （见 ``is_peer_viewable``）—— 现有模型没有职级字段。
    manager_id: str | None = None

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def can_view_score_of(
        self, target_employee_id: str, *, db: Session | None = None
    ) -> bool:
        """能否**查看**指定员工的绩效数据。

        规则（与 `PermissionAction` 保持一致）：
        - 本人 → `VIEW_OWN_SCORE`
        - 直属下属 / 汇报线全链下属 / 所管理部门成员 → `VIEW_DIRECT_REPORT_SCORE`
        - 代理授权范围内的人（P1-4）→ 同上
        - HR → `VIEW_ALL_SCORES`（看全员）
        - ★ P0：同部门**同级别**同事 → `VIEW_DEPARTMENT_PEER`（**只读**）

        ⚠️ 管理员**不**自动拥有查看全员成绩的权限 —— 管理权限
        （系统配置、组织同步）与业务数据权限是两条线，
        避免「能管系统就能看所有人绩效」的越权。

        参数 ``db``
            默认 ``None`` —— **保持既有语义不变**（本方法有 13+ 个调用方：
            `main.py` 6 处 / `qa_permission` / `agent_tools` / `trend_report` /
            `semantic_qc` / `feedback` / `exec_tool_service` …，
            改默认语义会波及一大片）。

            ⚠️ **``db=None`` 时不进入同部门互查分支** —— 因为判定同部门需要查库。
            这意味着老调用方**不会**因为这次改造突然获得新权限。
            权限扩大必须是**显式**行为，不能悄悄生效。

            新调用方（问答链路的 peer 查询）必须显式传 ``db=db``。
        """
        target = str(target_employee_id)
        if not target:
            return False
        if target == self.employee_id:
            return self.has_permission(PermissionAction.VIEW_OWN_SCORE)
        if target in self.direct_report_ids:
            return self.has_permission(PermissionAction.VIEW_DIRECT_REPORT_SCORE)
        if target in self.reporting_line_ids:
            # ★ P0：汇报线全链下属（间接下属）。
            # ⚠️ 用**闭包**而不是只查直属 —— 否则主管查不到"下属的下属"。
            return self.has_permission(PermissionAction.VIEW_DIRECT_REPORT_SCORE)
        if target in self.delegated_employee_ids:
            # 代理授权：受托人对委托范围内的人拥有查看权。
            # 委托到期后该集合自动缩小，权限随之消失。
            return self.has_permission(PermissionAction.VIEW_DIRECT_REPORT_SCORE)
        if self.has_permission(PermissionAction.VIEW_ALL_SCORES):
            return True
        # ★ P0：同部门同级别互查（只读）。
        # ⚠️ 必须放在 `VIEW_ALL_SCORES` 之后 —— 否则 HR 查外部门
        #    会先落到部门分支被判成"跨部门"误杀。
        if db is not None and self.has_permission(PermissionAction.VIEW_DEPARTMENT_PEER):
            target_employee = db.get(Employee, target)
            if target_employee is not None and is_peer_viewable(db, self, target_employee):
                return True
        return False

    def can_write_score_of(
        self, target_employee_id: str, *, db: Session | None = None
    ) -> bool:
        """能否**写入**目标员工的绩效数据（指标实测值 / 草稿 / 重算 / 反馈 / 催办）。

        ⚠️ **刻意不含同部门平级**（`VIEW_DEPARTMENT_PEER`）。
        「能查不能改」的权限层落点就在这里 —— 读权限与写权限是
        两个**独立**判定，peer 权限只出现在 `can_view_score_of`。

        ⚠️ 为什么必须拆开：
        指标实测值是**算分的唯一输入**（见 `evidence_service.add_metric_value`）。
        若 peer 权限渗透到写接口，普通员工就能给同部门同事写实测值 ——
        等于**可以改别人的绩效分数**，这正是需求明确禁止的。
        """
        target = str(target_employee_id)
        if not target:
            return False
        if target == self.employee_id:
            return self.has_permission(PermissionAction.SUBMIT_OWN_REVIEW)
        if target in self.direct_report_ids:
            return self.has_permission(PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW)
        if target in self.reporting_line_ids:
            return self.has_permission(PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW)
        if target in self.delegated_employee_ids:
            return self.has_permission(PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW)
        if self.has_permission(PermissionAction.VIEW_ALL_SCORES):
            return True
        if db is not None and self.managed_department_ids:
            target_employee = db.get(Employee, target)
            if target_employee is None:
                return False
            target_departments = _employee_department_ids(
                db, target, target_employee.department_id
            )
            if target_departments & self.managed_department_ids:
                return self.has_permission(PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW)
        # ⚠️ 没有 peer 分支 —— 到这里就是拒绝
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


def employee_department_ids(
    db: Session, employee: Employee
) -> set[str]:
    """员工所属部门集合（主部门 + 兼岗部门）。"""
    return _employee_department_ids(db, employee.id, employee.department_id)


def is_same_department(db: Session, a: Employee, b: Employee) -> bool:
    """两人是否同部门。

    判定口径：**所属部门集合有交集**即为同部门。

    ⚠️ 为什么用「集合交集」而不是「主部门相等」：
      - 兼岗员工（主部门 A，兼岗部门 B）在 B 部门与 B 同事互查 ⇒ 允许；
      - 虚拟组/项目组同理，HR 已在 `EmployeeDepartment` 表里维护；
      - 只比 `department_id` 会让兼岗员工查不到本组同事 → 误伤。

    ⚠️ 为什么**不是**「同一祖先部门」：
      「研发部下所有人在同一大部门」会导致跨部门互查 —— 正是要防的泄露。

    ⚠️ 部门归属取自 `EmployeeDepartment`（飞书同步 + HR 补录），
       不是部门树推导，避免部门结构调整后权限漂移。
    """
    return bool(
        employee_department_ids(db, a) & employee_department_ids(db, b)
    )


def _same_reporting_level(a: Employee, b: Employee) -> bool:
    """汇报线同级判定 —— P0「同一级」的核心口径。

    规则：**同一直属上级**即为同级。

    ⚠️ 为什么用「同一直属上级」而不是「层级深度相同」：
      - 层级深度要沿 `manager_id` 递归计算，而汇报线**可能成环**
        （飞书数据质量问题；`system_views.py` 的环检测说明确实出现过）；
      - 「同一直属上级」是 O(1) 判定，**天然不受环影响**；
      - 语义更贴需求 ——「不能查上一级」就是"我的上级与我直属上级不同"。

    ⚠️ 为什么**不能**用 `Employee.position`：
      `position` 来自飞书 `job_title` **自由文本**（"高级工程师"/"资深工程师"/
      "工程师"），它描述的是**岗位**不是**职级** —— 可能是同一级，
      也可能差一级。用它判权限会同时产生误放行与误拦截。

    ⚠️ 双方都没有上级（部门根节点员工）⇒ 视为同级。
    """
    if not a.manager_id and not b.manager_id:
        return True
    return bool(a.manager_id) and a.manager_id == b.manager_id


def is_peer_viewable(
    db: Session, asker_ctx: AccessContext, target: Employee
) -> bool:
    """目标人是否可被提问者作为「同部门同级别同事」查询（**只读**）。

    ⚠️ 三个条件**同时**满足（需求原文：
    「同部门的同一级可以查对方完成的情况，但是不能查上一级或者跨部门查询」）：

      ① 同部门      —— 部门集合有交集（兼容兼岗）
      ② 同一级      —— 汇报线同级（同一直属上级）
      ③ 不是我的上级 —— 显式拦截

    ⇒ 「不能查上一级」由 ② 自动保证：上级的 `manager_id` 与我不同
      ⇒ 汇报线不同级 ⇒ 拒绝。

    ⚠️ 为什么 ① 和 ② **都要查**：
      汇报线同级 **不等于** 同部门 —— 两个小部门的人汇报给同一个
      大部门负责人时，汇报线同级但跨部门。需求禁止跨部门查询，
      因此两个条件缺一不可。

    ⚠️ 失败方向是**保守**的：判定不出同级 ⇒ 拒绝，不会误放行。
    """
    asker = db.get(Employee, asker_ctx.employee_id)
    if asker is None:
        return False
    if target.id == asker.id:
        return False

    # ① 同部门
    if not is_same_department(db, asker, target):
        return False

    # ② 同一级（汇报线同级）
    if not _same_reporting_level(asker, target):
        return False

    # ③ 目标是我的直属上级 ⇒ 永远不可查
    #    （② 已排除大部分情况，这里再挡一次 ——
    #      防止 manager_id 数据异常导致的漏判）
    if target.id == asker.manager_id:
        return False

    return True


def _reporting_line_closure(db: Session, root_id: str) -> set[str]:
    """汇报线**全链**下属（递归闭包）。

    ⚠️ 为什么要闭包而不是只取直接下属：
      飞书汇报线是多层的 —— A 的下属 B，B 的下属 C，
      A 也是 C 的汇报线上的主管。需求③要求主管能查「汇报线下属」，
      只取直接下属会漏掉 C。

    ⚠️ 环保护：**必须**用 visited 集合。
      飞书数据可能出现环（A→B→A），不防会无限递归。
      `system_views.py` 已有环检测，说明这类数据确实出现过。
    """
    rows = db.execute(select(Employee.id, Employee.manager_id)).all()
    children: dict[str, list[str]] = {}
    for emp_id, mgr_id in rows:
        if mgr_id:
            children.setdefault(str(mgr_id), []).append(str(emp_id))
    result: set[str] = set()
    stack = [str(root_id)]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in result:
                result.add(child)
                stack.append(child)
    return result


def can_access_employee(
    context: AccessContext,
    target_employee: Employee,
    db: Session,
    *,
    require_active_target: bool = False,
) -> bool:
    """校验员工数据访问范围。

    部门负责人可访问其管理范围（所属部门及下级部门）内的员工；
    兼岗员工按任一所属部门命中管理范围即可访问。

    参数 ``require_active_target``
        默认 ``False`` —— **保持既有语义不变**（本函数有 7+ 个调用方：
        计划审批 / 成果审批 / 考勤 / 考核记录 / 依赖注入 …，
        改默认语义会波及一大片）。

        置为 ``True`` 时追加一道**目标员工在职**闸门：
        目标员工不是 ``active``（离职 / 停职）时，
        **本人**与**部门负责人 / 主管**两条路径不放行，
        但 **HR 仍然放行** —— HR 要给离职员工做结算算分，是真实业务需求。

        ⚠️ 为什么需要这道闸门：
        ``build_access_context`` 的 ``direct_report_ids`` 已经过滤了
        ``employment_status == "active"``（离职后不再是直属下属），
        但**部门树路径没过滤** —— 主管通常同时是部门 leader，
        于是离职员工仍能通过部门树被访问、仍能被日常评分。
        「已离职的人不该出现在日常考核里」这条常识，
        在数据范围层没有被完整表达。
    """
    target_active = (target_employee.employment_status or "active") == "active"

    if not require_active_target or target_active:
        # ── 既有语义：本人 / HR / 直属下属 / 部门负责人管理范围 ──
        if context.employee_id == target_employee.id:
            return True
        if "hr" in context.roles:
            return True
        if target_employee.id in context.direct_report_ids:
            return True
        if target_employee.id in context.delegated_employee_ids:
            # P1-4 代理授权：委托有效期内，受托人对委托人管理的对象
            # 有同等访问权。委托到期后集合自动缩小。
            return True
        if "department_leader" in context.roles:
            target_departments = _employee_department_ids(
                db, target_employee.id, target_employee.department_id
            )
            if target_departments & context.managed_department_ids:
                return True
        return False

    # ── 目标已离职：只保留 HR（结算算分） ──
    # 本人与主管/部门负责人不放行 —— 离职不该再参与日常评分。
    if "hr" in context.roles:
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

    # ★ P0：汇报线**全链**下属（闭包）。
    #
    # ⚠️ 为什么要闭包：
    #   飞书汇报线是多层的 —— A 的下属 B，B 的下属 C，
    #   A 也是 C 的汇报线上的主管。需求③要求主管能查「汇报线下属」，
    #   只取直接下属会漏掉 C。
    #
    # ⚠️ 为什么不改成"沿 manager_id 递归"：
    #   汇报线可能成环（飞书数据质量问题，`system_views.py` 有环检测，
    #   说明这类数据确实出现过）—— 递归必须带 visited 集合防死循环。
    #   `_reporting_line_closure` 用栈 + result 集合实现，天然防环。
    #
    # ⚠️ 为什么**替换**而不是**并入** `direct_report_ids`：
    #   `direct_report_ids` 这个名字在既有代码里被当作"直属下属"用
    #   （`can_access_employee` / `exec_tool_service` / `test_delegation`），
    #   改成闭包会改变那些调用方的语义。
    #   ⇒ 保留 `direct_report_ids` 原样，另加 `reporting_line_ids`，
    #     并让**权限判定**用闭包。
    reporting_line_ids = frozenset(
        _reporting_line_closure(db, employee.id)
    )
    # 只有在职的才算有效下属 —— 离职者不该再出现在日常考核里
    inactive_in_line = set(
        db.execute(
            select(Employee.id).where(
                Employee.id.in_(reporting_line_ids or {"__none__"}),
                Employee.employment_status != "active",
            )
        ).scalars().all()
    )
    reporting_line_ids = frozenset(reporting_line_ids - inactive_in_line)

    is_admin = db.execute(
        select(SystemAdmin.id).where(
            SystemAdmin.employee_id == employee.id,
            SystemAdmin.active.is_(True),
        )
    ).first() is not None

    # ⚠️ **角色集合由 ``role_vocabulary.derive_roles`` 统一推导**（2026-11-26）
    #
    # 过去这里手写 ``roles.add("manager")`` / ``roles.add("department_leader")``，
    # 而视图层 ``system_views._role_names`` 又另写了一份（还用了 ``leader``
    # 而不是 ``manager``，且漏了 ``hr``）⇒ 同一个人的角色在两处显示不一样。
    # ⇒ 现在两处都调 ``derive_roles``，判定条件与产出的角色码**只有一处定义**。
    #
    # ⚠️⚠️ **必须放在「代理授权查询」之后**（2026-11-26 修的真实 BUG）
    #   `derive_roles` 的入参含 ``delegated_ids``（有没有代理授权也算下属），
    #   而 ``delegated_ids` 在下面才赋值 ⇒ 提前调用会抛
    #   ``UnboundLocalError: cannot access local variable 'delegated_ids'``。
    #   ⇒ 把整个推导块**下移**到委托查询之后。
    permissions: set[str] = {
        PermissionAction.VIEW_OWN_SCORE,
        PermissionAction.SUBMIT_OWN_REVIEW,
        # ★ P0：同部门同级别同事互查（**只读**）。
        #
        # ⚠️ 为什么默认给所有员工：
        #   需求原文「同部门的同一级可以查对方完成的情况」。
        #   这条与职级无关 —— 主管也是员工，同样要能查同部门平级。
        #
        # ⚠️ 它**只在 `can_view_score_of` 里被消费**，
        #   写路径（`can_write_score_of`）完全不看它 ⇒ 「能查不能改」。
        PermissionAction.VIEW_DEPARTMENT_PEER,
    }

    # P1-4 代理授权：委托有效期内，受托人对委托人管理的对象拥有同等权限。
    # 到期后集合自动缩小，不需要后台任务去撤销。
    delegated_ids: set[str] = set()
    try:
        from .delegation_service import delegated_employee_ids

        delegated_ids = set(delegated_employee_ids(db, delegate_id=employee.id))
    except Exception:  # pragma: no cover - 委托表缺失时不影响既有权限
        logger.warning("读取代理授权失败（按无委托处理）", exc_info=True)

    # ✅ 位置正确：此时 ``delegated_ids`` / ``direct_report_ids`` /
    #    ``reporting_line_ids`` / ``managed_department_ids`` / ``is_hr`` /
    #    ``is_admin`` **全部已就绪**。
    roles: set[str] = set(
        derive_roles(
            has_subordinates=bool(
                direct_report_ids or delegated_ids or reporting_line_ids
            ),
            manages_department=bool(managed_department_ids),
            is_hr=is_hr,
            is_admin=is_admin,
        )
    )

    # ⚠️ 角色集合**已在上方由 ``derive_roles`` 推导完毕** ——
    # 这里只负责**权限位**（角色 ⇒ 权限位的映射仍按角色分别展开）。
    if Role.MANAGER in roles:
        permissions.update(
            {
                PermissionAction.VIEW_DIRECT_REPORT_SCORE,
                PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW,
                PermissionAction.ADD_METRIC_TO_RECORD,
            }
        )

    if Role.DEPARTMENT_LEADER in roles:
        permissions.update(
            {
                PermissionAction.VIEW_DIRECT_REPORT_SCORE,
                PermissionAction.SUBMIT_DIRECT_REPORT_REVIEW,
            }
        )

    if Role.HR in roles:
        permissions.update(
            {
                PermissionAction.VIEW_ALL_SCORES,
                PermissionAction.CONFIRM_RATING,
                PermissionAction.ADD_METRIC_TO_RECORD,
                PermissionAction.VIEW_AUDIT_LOG,
                PermissionAction.MANAGE_EMPLOYEES,
                PermissionAction.SYNC_ATTENDANCE,
                # BUG-R6-31：算分配置（指标 / 权重集 / 等级区间）的归属者是
                # **HR**，不是只有 system_admin。
                #
                # 决策③原文是「多维表格 HR 直改」—— 配置的**业务所有者**是 HR。
                # 过去只给 system_admin，导致：
                #   - weight_set / rubric 表永远是空的（没有任何产品入口能写）；
                #   - 唯一的 admin 账号口令一旦变更，配置入口就**彻底锁死**，
                #     整个算分链路再也无法通过产品路径启动。
                #
                # 现在 HR 也能管理算分配置；system_admin 仍然保留该权限
                # （运维兜底），但不再是唯一入口。
                PermissionAction.MANAGE_SYSTEM_CONFIG,
                # ⚠️ ★ 2026-11-27：企业知识库的写操作。
                #   知识文档是 **AI 检索的依据来源** ⇒ 停用 / 删除一篇
                #   = 直接改变 AI 的回答依据 ⇒ 必须有独立权限位。
                PermissionAction.MANAGE_KNOWLEDGE,
            }
        )

    if Role.ADMIN in roles:
        permissions.update(
            {
                PermissionAction.MANAGE_SYSTEM_CONFIG,
                PermissionAction.SYNC_ORGANIZATION,
                PermissionAction.SYNC_ATTENDANCE,
                PermissionAction.VIEW_AUDIT_LOG,
                # ⚠️ **2026-11-26 补** —— 系统管理员也要能**管员工**。
                #
                # 背景：``POST /system/users/{id}/reset-password`` 等端点
                # 补上 ``MANAGE_EMPLOYEES`` 权限闸后（修 P0 越权后门），
                # 只给 HR 的话 ⇒ **管理员被挡住** ⇒ 管理员改不了员工状态 /
                # 重置不了员工密码 ⇒ 管理端功能形同虚设。
                #
                # 口径依据：前端菜单 ``ROLE_UI_PERMISSIONS`` 里
                # ``hr`` 与 ``admin`` **都**有 ``system:employee:register``
                # 与 ``system:employee:view`` ⇒ 后端动作权限必须对齐，
                # 否则就是「前端能看、后端放行」/「前端能看、后端 403」两种漂移。
                PermissionAction.MANAGE_EMPLOYEES,
                # ⚠️ ★ 2026-11-27：管理员也要能管知识库
                #   （与 HR 同口径 —— 前端菜单里 ``hr`` 与 ``admin``
                #   都有 ``system:knowledge:view``）。
                PermissionAction.MANAGE_KNOWLEDGE,
            }
        )

    return AccessContext(
        employee_id=employee.id,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
        direct_report_ids=direct_report_ids,
        reporting_line_ids=reporting_line_ids,
        managed_department_ids=frozenset(managed_department_ids),
        employee_department_ids=frozenset(employee_department_ids),
        delegated_employee_ids=frozenset(delegated_ids),
        manager_id=employee.manager_id,
    )
