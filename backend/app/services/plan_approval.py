"""计划审批服务 —— 月计划 / 周计划 / 日计划的审批流转与审批流水。

定位
----
员工侧（自己填计划）由 `services/planning.py` 负责；本模块负责**管理侧**：
主管 / 部门负责人查看待批列表、批准、驳回，并把每次动作写入 `plan_approval` 流水表。

设计要点
--------
1. **状态机复用**：审批只是 `services/planning.py` 状态机的一个下游流转，
   本模块通过 `planning.assert_transition` 校验，不重复定义规则。
2. **审批流水**：`plan_approval` 表按 `(plan_type, plan_id)` 多态关联三层计划表，
   记录 `actor_id` / `action` / `from_status` / `to_status` / `comment`。
3. **幂等**：每次写流水都带 `dedup_key`，同一计划同一动作同一版本只写一次，
   重复提交（前端双击 / 网络重试）不会产生重复流水。
4. **权限边界**：本模块**不做**权限判定（那是路由层的职责），
   只接受已经过 `access_control` 校验的 `actor_id` 与目标计划。

审批层级
--------
当前实现为**一级审批**（直属主管审批即完成）：

    submitted ──approve──> approved
    submitted ──reject───> rejected

若后续需要「主管 → 部门负责人」两级审批，扩展方式：
在 `planning._TRANSITIONS` 中为 `approved` 之前引入中间态 `pending`，
并新增 `approve_tier2` / `reject_tier2` 与对应路由，**无需改动本模块的其余部分**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import PlanApprovalAction, PlanStatus
from ..domain.models import (
    DayPlan,
    Employee,
    EmployeeDepartment,
    MonthPlan,
    WeekPlan,
)
from ..domain.plan_models import PlanApproval
from . import planning

logger = logging.getLogger(__name__)


# ===========================================================================
# 常量
# ===========================================================================

# 计划类型（写入 plan_approval.plan_type）
PLAN_TYPE_MONTH = "month"
PLAN_TYPE_WEEK = "week"
PLAN_TYPE_DAY = "day"

# 审批动作 → 目标状态
_APPROVE_TARGET = PlanStatus.APPROVED.value
_REJECT_TARGET = PlanStatus.REJECTED.value

# 日计划「抽检」的结果。抽检不改主状态机，只写 review_status。
REVIEW_APPROVED = "APPROVED"
REVIEW_REJECTED = "REJECTED"


# ===========================================================================
# 异常
# ===========================================================================


class PlanApprovalError(Exception):
    """审批相关错误（无权限 / 状态不允许 / 幂等冲突）。"""

    def __init__(self, message: str, *, code: str = "PLAN_APPROVAL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PlanApprovalForbidden(PlanApprovalError):
    """无权审批该计划。"""

    def __init__(self, message: str = "无权审批该计划") -> None:
        super().__init__(message, code="PLAN_APPROVAL_FORBIDDEN")


class PlanApprovalConflict(PlanApprovalError):
    """审批状态冲突（如重复审批、版本过期）。"""

    def __init__(self, message: str = "审批冲突") -> None:
        super().__init__(message, code="PLAN_APPROVAL_CONFLICT")


# ===========================================================================
# 内部工具
# ===========================================================================


def _now() -> datetime:
    """当前 UTC 时间（naive），与 `services.planning._now` 同口径。"""
    return datetime.now().utcnow()


def plan_model(plan_type: str) -> Any:
    """计划类型 → ORM 模型。

    公开给路由层使用（路由需要按 id 取计划做详情展示与权限校验）。
    """
    if plan_type == PLAN_TYPE_MONTH:
        return MonthPlan
    if plan_type == PLAN_TYPE_WEEK:
        return WeekPlan
    if plan_type == PLAN_TYPE_DAY:
        return DayPlan
    raise PlanApprovalError(f"未知的计划类型：{plan_type}", code="PLAN_APPROVAL_BAD_TYPE")


def fetch_plan(db: Session, plan_type: str, plan_id: str) -> Any:
    """按类型 + id 取计划。不做归属人校验 —— 管理侧审批需要跨人读取。"""
    model = plan_model(plan_type)
    plan = db.get(model, plan_id)
    if plan is None:
        raise PlanApprovalError("计划不存在", code="PLAN_APPROVAL_NOT_FOUND")
    return plan


def _build_dedup_key(
    plan_type: str,
    plan_id: str,
    action: str,
    version_no: int,
) -> str:
    """幂等键：`计划类型:计划ID:动作:版本号`。

    同一个版本上的同一动作只会写一条流水。前端双击 / 网络重试
    （版本号未变）会被识别为重复；员工改一版再提交（版本号 +1）则是新动作。
    """
    return f"{plan_type}:{plan_id}:{action}:{version_no}"


def _record_approval(
    db: Session,
    *,
    plan_type: str,
    plan_id: str,
    actor_id: str,
    action: str,
    from_status: str | None,
    to_status: str,
    comment: str | None,
    dedup_key: str,
) -> PlanApproval:
    """写一条审批流水。`dedup_key` 已存在则复用该行（幂等）。"""
    existing = db.execute(
        select(PlanApproval).where(PlanApproval.dedup_key == dedup_key)
    ).scalar_one_or_none()
    if existing is not None:
        logger.info("审批流水幂等命中，跳过重复写入：%s", dedup_key)
        return existing

    row = PlanApproval(
        plan_type=plan_type,
        plan_id=plan_id,
        actor_id=actor_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        comment=comment or None,
        dedup_key=dedup_key,
    )
    db.add(row)
    db.flush()
    return row


def _apply_month_approval(plan: MonthPlan, approved: bool, comment: str | None) -> None:
    """月计划审批落库：写 `approved_at` / `approval_comment`。"""
    plan.status = _APPROVE_TARGET if approved else _REJECT_TARGET
    plan.approved_at = _now()
    plan.approval_comment = comment or None
    plan.version_no = planning._bump_version(plan)


def _apply_week_approval(plan: WeekPlan, approved: bool, comment: str | None) -> None:
    """周计划审批落库：写 `approve_at` / `approval_comment`。

    注意：周计划字段名是 `approve_at`（月计划是 `approved_at`），
    这里与各模型的实际列名保持一致，不做统一重命名，避免迁移。
    """
    plan.status = _APPROVE_TARGET if approved else _REJECT_TARGET
    plan.approve_at = _now()
    plan.approval_comment = comment or None
    plan.version_no = planning._bump_version(plan)


def _apply_day_review(plan: DayPlan, approved: bool, comment: str | None) -> None:
    """日计划抽检落库：写 `review_status` / `reviewed_at` / `leader_comment`。

    日计划**不改主状态机**（`plan.status`），只记录抽检结论。
    这样主管抽检不会阻塞员工的计划流程。
    """
    plan.review_status = REVIEW_APPROVED if approved else REVIEW_REJECTED
    plan.reviewed_at = _now()
    plan.leader_comment = comment or None
    plan.version_no = planning._bump_version(plan)


# ===========================================================================
# 待批列表
# ===========================================================================


@dataclass(frozen=True)
class ApprovalScope:
    """审批人可见范围。

    - `direct_report_ids`：直属下属（可审批其月 / 周计划）。
    - `managed_department_ids`：管理范围内的部门（可查看全部成员计划）。

    注意：`managed_department_ids` 存的是**部门 ID**，不能直接用来判断某个员工
    是否在范围内 —— 必须结合员工的实际部门归属。因此本结构只用于**粗筛**，
    精确判定交给 `access_control.can_access_employee`。
    """

    direct_report_ids: frozenset[str]
    managed_department_ids: frozenset[str]

    @classmethod
    def from_access_context(cls, context: Any) -> "ApprovalScope":
        return cls(
            direct_report_ids=frozenset(getattr(context, "direct_report_ids", ()) or ()),
            managed_department_ids=frozenset(
                getattr(context, "managed_department_ids", ()) or ()
            ),
        )

    def is_empty(self) -> bool:
        """没有任何可见范围 —— 审批人无任何计划可见。"""
        return not self.direct_report_ids and not self.managed_department_ids


def _owner_in_scope(
    db: Session,
    owner_id: str,
    scope: ApprovalScope,
    owner: Employee | None,
) -> bool:
    """计划归属人是否落在审批人可见范围内。

    判定顺序（与 `access_control.can_access_employee` 对齐）：
    1. 直属下属 → 可见。
    2. 管理范围内部门的成员 → 可见（按员工实际部门归属判定，
       不能只看 `managed_department_ids` 非空就放行）。
    """
    if owner_id in scope.direct_report_ids:
        return True
    if not scope.managed_department_ids:
        return False
    if owner is None:
        return False

    # 兼岗员工按任一所属部门命中管理范围即可
    dept_ids = {owner.department_id} if owner.department_id else set()
    rows = db.execute(
        select(EmployeeDepartment.department_id).where(
            EmployeeDepartment.employee_id == owner_id
        )
    ).scalars().all()
    dept_ids.update({str(r) for r in rows if r})

    return bool(dept_ids & scope.managed_department_ids)


def _filter_in_scope(
    db: Session,
    plans: Sequence[Any],
    scope: ApprovalScope,
) -> list[Any]:
    """按可见范围过滤计划列表。"""
    return [
        p
        for p in plans
        if _owner_in_scope(db, p.owner_id, scope, db.get(Employee, p.owner_id))
    ]


def list_pending_month_plans(
    db: Session,
    scope: ApprovalScope,
    *,
    plan_month: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[MonthPlan]:
    """待审批的月计划列表（审批人视角）。"""
    stmt = select(MonthPlan).where(MonthPlan.status == PlanStatus.SUBMITTED.value)
    if plan_month:
        stmt = stmt.where(MonthPlan.plan_month == planning.normalize_month(plan_month))
    if status:
        stmt = stmt.where(MonthPlan.status == status)
    stmt = stmt.order_by(MonthPlan.plan_month.desc(), MonthPlan.submitted_at.desc())

    rows = db.execute(stmt).scalars().all()
    return _filter_in_scope(db, rows, scope)[:limit]


def list_pending_week_plans(
    db: Session,
    scope: ApprovalScope,
    *,
    status: str | None = None,
    limit: int = 200,
) -> list[WeekPlan]:
    """待审批的周计划列表（审批人视角）。"""
    stmt = select(WeekPlan).where(WeekPlan.status == PlanStatus.SUBMITTED.value)
    if status:
        stmt = stmt.where(WeekPlan.status == status)
    stmt = stmt.order_by(WeekPlan.week_start.desc(), WeekPlan.submitted_at.desc())

    rows = db.execute(stmt).scalars().all()
    return _filter_in_scope(db, rows, scope)[:limit]


def list_pending_day_plans(
    db: Session,
    scope: ApprovalScope,
    *,
    review_status: str | None = None,
    limit: int = 200,
) -> list[DayPlan]:
    """待抽检的日计划列表（审批人视角）。

    日计划是抽检制：只列出 `review_status` 为空的已提交日计划。
    """
    stmt = select(DayPlan).where(
        DayPlan.status == PlanStatus.SUBMITTED.value,
        DayPlan.review_status.is_(None),
    )
    if review_status:
        stmt = stmt.where(DayPlan.review_status == review_status)
    stmt = stmt.order_by(DayPlan.plan_date.desc())

    rows = db.execute(stmt).scalars().all()
    return _filter_in_scope(db, rows, scope)[:limit]


# ===========================================================================
# 审批动作
# ===========================================================================


def _assert_approvable(status: str) -> None:
    """只有 `submitted` 状态可被审批。

    `pending` 是为将来两级审批预留的中间态：一级审批下不会出现。
    """
    if status != PlanStatus.SUBMITTED.value:
        raise PlanApprovalConflict(f"当前状态（{status}）不允许审批")


def _assert_version(plan: Any, expected: int | None) -> None:
    """乐观锁校验：前端回传的 `versionNo` 必须与服务端一致。

    前端 `weekPlan.ts` 的审批接口会带 `versionNo`，用于避免两个主管
    同时审批同一条计划。`expected` 为 None 表示前端未带版本号（不校验）。
    """
    if expected is None:
        return
    try:
        planning.assert_version(plan, int(expected))
    except planning.PlanVersionConflict as exc:
        raise PlanApprovalConflict(
            f"版本冲突：期望 {exc.expected}，实际 {exc.actual}"
        ) from exc


def _assert_not_self_approval(plan: Any, actor_id: str) -> None:
    """禁止审批自己的计划。

    ⚠️ 为什么服务层也要拦：`access_control.can_access_employee` 对
    「目标员工 == 当前用户」返回 True（本人可看自己的数据）。若只依赖
    路由层的权限校验，**部门负责人 / 主管就能审批自己提交的计划** ——
    审批与被审批是同一个人，审批流水的 `actor_id` 与 `owner_id` 相同。

    这里在服务层兜底，保证**任何**调用方（路由 / 脚本 / 未来其他端点）
    都绕不过这条规则。
    """
    if str(plan.owner_id) == str(actor_id):
        raise PlanApprovalForbidden("不能审批自己提交的计划")


def approve_month_plan(
    db: Session,
    actor_id: str,
    plan_id: str,
    *,
    comment: str | None = None,
    version_no: int | None = None,
) -> MonthPlan:
    """主管批准月计划：`submitted → approved`。"""
    plan = fetch_plan(db, PLAN_TYPE_MONTH, plan_id)
    _assert_not_self_approval(plan, actor_id)
    _assert_approvable(plan.status)
    _assert_version(plan, version_no)

    from_status = plan.status
    dedup_key = _build_dedup_key(
        PLAN_TYPE_MONTH, plan.id, PlanApprovalAction.APPROVE.value, plan.version_no
    )
    _apply_month_approval(plan, approved=True, comment=comment)
    _record_approval(
        db,
        plan_type=PLAN_TYPE_MONTH,
        plan_id=plan.id,
        actor_id=actor_id,
        action=PlanApprovalAction.APPROVE.value,
        from_status=from_status,
        to_status=_APPROVE_TARGET,
        comment=comment,
        dedup_key=dedup_key,
    )
    db.flush()
    return plan


def reject_month_plan(
    db: Session,
    actor_id: str,
    plan_id: str,
    *,
    comment: str | None = None,
    version_no: int | None = None,
) -> MonthPlan:
    """主管驳回月计划：`submitted → rejected`。"""
    plan = fetch_plan(db, PLAN_TYPE_MONTH, plan_id)
    _assert_not_self_approval(plan, actor_id)
    _assert_approvable(plan.status)
    _assert_version(plan, version_no)

    from_status = plan.status
    dedup_key = _build_dedup_key(
        PLAN_TYPE_MONTH, plan.id, PlanApprovalAction.REJECT.value, plan.version_no
    )
    _apply_month_approval(plan, approved=False, comment=comment)
    _record_approval(
        db,
        plan_type=PLAN_TYPE_MONTH,
        plan_id=plan.id,
        actor_id=actor_id,
        action=PlanApprovalAction.REJECT.value,
        from_status=from_status,
        to_status=_REJECT_TARGET,
        comment=comment,
        dedup_key=dedup_key,
    )
    db.flush()
    return plan


def approve_week_plan(
    db: Session,
    actor_id: str,
    plan_id: str,
    *,
    comment: str | None = None,
    version_no: int | None = None,
) -> WeekPlan:
    """主管批准周计划：`submitted → approved`。"""
    plan = fetch_plan(db, PLAN_TYPE_WEEK, plan_id)
    _assert_not_self_approval(plan, actor_id)
    _assert_approvable(plan.status)
    _assert_version(plan, version_no)

    from_status = plan.status
    dedup_key = _build_dedup_key(
        PLAN_TYPE_WEEK, plan.id, PlanApprovalAction.APPROVE.value, plan.version_no
    )
    _apply_week_approval(plan, approved=True, comment=comment)
    _record_approval(
        db,
        plan_type=PLAN_TYPE_WEEK,
        plan_id=plan.id,
        actor_id=actor_id,
        action=PlanApprovalAction.APPROVE.value,
        from_status=from_status,
        to_status=_APPROVE_TARGET,
        comment=comment,
        dedup_key=dedup_key,
    )
    db.flush()
    return plan


def reject_week_plan(
    db: Session,
    actor_id: str,
    plan_id: str,
    *,
    comment: str | None = None,
    version_no: int | None = None,
) -> WeekPlan:
    """主管驳回周计划：`submitted → rejected`。"""
    plan = fetch_plan(db, PLAN_TYPE_WEEK, plan_id)
    _assert_not_self_approval(plan, actor_id)
    _assert_approvable(plan.status)
    _assert_version(plan, version_no)

    from_status = plan.status
    dedup_key = _build_dedup_key(
        PLAN_TYPE_WEEK, plan.id, PlanApprovalAction.REJECT.value, plan.version_no
    )
    _apply_week_approval(plan, approved=False, comment=comment)
    _record_approval(
        db,
        plan_type=PLAN_TYPE_WEEK,
        plan_id=plan.id,
        actor_id=actor_id,
        action=PlanApprovalAction.REJECT.value,
        from_status=from_status,
        to_status=_REJECT_TARGET,
        comment=comment,
        dedup_key=dedup_key,
    )
    db.flush()
    return plan


def review_day_plan(
    db: Session,
    actor_id: str,
    plan_id: str,
    *,
    approved: bool,
    comment: str | None = None,
    version_no: int | None = None,
) -> DayPlan:
    """主管抽检日计划。

    日计划走**抽检**语义：只写 `review_status` / `reviewed_at` / `leader_comment`，
    不改 `plan.status`（员工仍可撤回 / 继续编辑）。
    """
    plan = fetch_plan(db, PLAN_TYPE_DAY, plan_id)
    _assert_not_self_approval(plan, actor_id)
    if plan.status != PlanStatus.SUBMITTED.value:
        raise PlanApprovalConflict(f"当前状态（{plan.status}）不允许抽检")
    _assert_version(plan, version_no)

    action = (
        PlanApprovalAction.APPROVE.value
        if approved
        else PlanApprovalAction.REJECT.value
    )
    from_status = plan.status
    dedup_key = _build_dedup_key(PLAN_TYPE_DAY, plan.id, action, plan.version_no)
    _apply_day_review(plan, approved=approved, comment=comment)
    _record_approval(
        db,
        plan_type=PLAN_TYPE_DAY,
        plan_id=plan.id,
        actor_id=actor_id,
        action=action,
        from_status=from_status,
        to_status=REVIEW_APPROVED if approved else REVIEW_REJECTED,
        comment=comment,
        dedup_key=dedup_key,
    )
    db.flush()
    return plan


def batch_approve(
    db: Session,
    actor_id: str,
    plan_type: str,
    plan_ids: Sequence[str],
    *,
    comment: str | None = None,
) -> dict[str, Any]:
    """批量审批。

    单条失败不影响其它条目 —— 返回逐条结果，调用方决定如何呈现。
    """
    succeeded: list[str] = []
    failed: list[dict[str, str]] = []

    for plan_id in plan_ids:
        try:
            if plan_type == PLAN_TYPE_MONTH:
                approve_month_plan(db, actor_id, plan_id, comment=comment)
            elif plan_type == PLAN_TYPE_WEEK:
                approve_week_plan(db, actor_id, plan_id, comment=comment)
            elif plan_type == PLAN_TYPE_DAY:
                review_day_plan(db, actor_id, plan_id, approved=True, comment=comment)
            else:
                raise PlanApprovalError(
                    f"未知的计划类型：{plan_type}", code="PLAN_APPROVAL_BAD_TYPE"
                )
            succeeded.append(plan_id)
        except PlanApprovalError as exc:
            failed.append({"planId": plan_id, "reason": exc.message})
        except planning.PlanStateError as exc:
            failed.append({"planId": plan_id, "reason": exc.message})

    return {"succeeded": succeeded, "failed": failed}


# ===========================================================================
# 序列化
# ===========================================================================


def serialize_approval_item(
    db: Session,
    plan: Any,
    *,
    plan_type: str,
) -> dict[str, Any]:
    """按计划类型分发到对应的序列化器。"""
    if plan_type == PLAN_TYPE_MONTH:
        return serialize_month_plan_approval_item(db, plan)
    if plan_type == PLAN_TYPE_WEEK:
        return serialize_week_plan_summary(db, plan)
    if plan_type == PLAN_TYPE_DAY:
        return serialize_daily_review_item(db, plan)
    raise PlanApprovalError(f"未知的计划类型：{plan_type}", code="PLAN_APPROVAL_BAD_TYPE")


def serialize_month_plan_approval_item(
    db: Session,
    plan: MonthPlan,
) -> dict[str, Any]:
    """月计划审批项 —— 对齐 `frontend/src/api/department.ts` 的 `MonthPlanApprovalItem`。

    注意：前端的 `ownerId` / `orgId` 是 **number** 类型，而后端主键是 string。
    这里统一转成 `int`，转换失败回退 0（与前端 `_empty_*` 构造器的口径一致）。
    """
    owner = db.get(Employee, plan.owner_id)
    items = planning.list_month_plan_items(db, plan.id)
    year, month = _split_plan_month(plan.plan_month)

    return {
        "id": plan.id,
        "planNo": plan.id,
        "ownerId": _as_int(owner.id if owner is not None else None),
        "employeeNo": (owner.login_account if owner is not None else "") or "",
        "employeeName": (owner.name if owner is not None else "") or "",
        "orgId": _as_int(owner.department_id if owner is not None else None),
        "orgName": planning.department_name_for(db, owner),
        "planYear": year,
        "planMonth": month,
        "deliverable": "；".join(i.deliverable for i in items if i.deliverable),
        "estimatedStartDate": _min_date(items, "estimated_start_date"),
        "estimatedEndDate": _max_date(items, "estimated_end_date"),
        "status": plan.status,
        "leaderComment": plan.approval_comment or "",
        "aiCheckResult": "",
        "missingFields": _missing_fields(items),
        "submittedAt": _iso_datetime(plan.submitted_at) or "",
        "version": plan.version_no,
        "items": [
            {
                "id": _as_int(item.id),
                "workType": item.task_type or "",
                "taskName": item.task_name or "",
                "taskContent": item.task_content or "",
                "deliverable": item.deliverable or "",
                "quality": item.quality or "",
                "performanceWeight": item.performance_weight or 0,
                "estimatedValue": item.estimated_value or 0,
                "estimatedStartDate": _iso_date(item.estimated_start_date) or "",
                "estimatedEndDate": _iso_date(item.estimated_end_date) or "",
                "status": item.status or "",
            }
            for item in items
        ],
    }


def serialize_week_plan_summary(
    db: Session,
    plan: WeekPlan,
) -> dict[str, Any]:
    """周计划摘要 —— 对齐 `frontend/src/api/weekPlan.ts` 的 `WeekPlanSummary`。"""
    owner = db.get(Employee, plan.owner_id)
    items = planning.list_week_plan_items(db, plan.id)
    return {
        "id": plan.id,
        "title": plan.title or "",
        "weekStart": _iso_date(plan.week_start) or "",
        "weekEnd": _iso_date(plan.week_end) or "",
        "status": plan.status,
        "versionNo": plan.version_no,
        "ownerUserId": _as_int(owner.id if owner is not None else None),
        "employeeName": (owner.name if owner is not None else "") or "",
        "departmentName": planning.department_name_for(db, owner),
        "itemCount": len(items),
    }


def serialize_daily_review_item(
    db: Session,
    plan: DayPlan,
) -> dict[str, Any]:
    """日计划抽检项 —— 对齐 `frontend/src/api/leader.ts` 的 `DailyReviewItem`。"""
    owner = db.get(Employee, plan.owner_id)
    week_start, _ = planning.week_range(plan.plan_date)
    week_items = _week_items_for_day(db, plan, week_start)

    due_at = plan.approval_due_at
    overdue = bool(due_at is not None and plan.reviewed_at is None and due_at < _now())

    return {
        "id": plan.id,
        "ownerId": _as_int(owner.id if owner is not None else None),
        "employeeNo": (owner.login_account if owner is not None else "") or "",
        "employeeName": (owner.name if owner is not None else "") or "",
        "orgId": _as_int(owner.department_id if owner is not None else None),
        "orgName": planning.department_name_for(db, owner),
        "planDate": _iso_date(plan.plan_date) or "",
        "submittedAt": _iso_datetime(plan.submitted_at),
        "workContent": plan.content or "",
        "deliverable": "；".join(i.content for i in week_items if i.content),
        "approvalDueAt": _iso_datetime(due_at),
        "overdueApproval": overdue,
        "missingFields": [],
        "aiCheckResult": "",
        "reviewStatus": plan.review_status or "PENDING",
        "riskLevel": plan.risk_level or "NONE",
        "leaderComment": plan.leader_comment or "",
        "reviewedAt": _iso_datetime(plan.reviewed_at),
    }


def serialize_approval_history(
    db: Session,
    plan_type: str,
    plan_id: str,
) -> list[dict[str, Any]]:
    """审批流水（时间线），按时间正序。"""
    rows = db.execute(
        select(PlanApproval)
        .where(
            PlanApproval.plan_type == plan_type,
            PlanApproval.plan_id == plan_id,
        )
        .order_by(PlanApproval.created_at.asc())
    ).scalars().all()

    history: list[dict[str, Any]] = []
    for row in rows:
        actor = db.get(Employee, row.actor_id)
        history.append(
            {
                "id": row.id,
                "action": row.action,
                "fromStatus": row.from_status,
                "toStatus": row.to_status,
                "comment": row.comment,
                "actorId": row.actor_id,
                "actorName": actor.name if actor is not None else "",
                "createdAt": _iso_datetime(row.created_at),
            }
        )
    return history


def count_pending_approvals(db: Session, scope: ApprovalScope) -> int:
    """待审批总数（用于工作台角标）。"""
    return (
        len(list_pending_month_plans(db, scope))
        + len(list_pending_week_plans(db, scope))
        + len(list_pending_day_plans(db, scope))
    )


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int:
    """把字符串主键转成 int（前端 TS 类型是 number）。失败回退 0。"""
    if value is None:
        return 0
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _split_plan_month(plan_month: str) -> tuple[int, int]:
    """`"2026-11"` → `(2026, 11)`。解析失败回退 `(0, 0)`。"""
    text = str(plan_month or "")
    if "-" in text:
        parts = text.split("-", 1)
        try:
            return int(parts[0]), int(parts[1])
        except (TypeError, ValueError):
            return 0, 0
    try:
        value = int(text)
        return value // 100, value % 100
    except (TypeError, ValueError):
        return 0, 0


def _min_date(items: Sequence[Any], attr: str) -> str:
    """取条目中最早的日期。"""
    values = [getattr(i, attr) for i in items if getattr(i, attr, None) is not None]
    if not values:
        return ""
    return _iso_date(min(values)) or ""


def _max_date(items: Sequence[Any], attr: str) -> str:
    """取条目中最晚的日期。"""
    values = [getattr(i, attr) for i in items if getattr(i, attr, None) is not None]
    if not values:
        return ""
    return _iso_date(max(values)) or ""


def _missing_fields(items: Sequence[Any]) -> list[str]:
    """月计划条目缺哪些必填项（用于 `missingFields`）。"""
    missing: list[str] = []
    for item in items:
        if not (item.task_name or "").strip():
            missing.append("任务名称")
        if not (item.deliverable or "").strip():
            missing.append("交付物")
    # 去重且保序
    return list(dict.fromkeys(missing))


def _week_items_for_day(
    db: Session,
    plan: DayPlan,
    week_start: date,
) -> list[Any]:
    """当天所属周的周计划条目（用于日计划的 `deliverable`）。"""
    rows = db.execute(
        select(WeekPlan).where(
            WeekPlan.owner_id == plan.owner_id,
            WeekPlan.week_start == week_start,
        )
    ).scalars().all()
    items: list[Any] = []
    for row in rows:
        items.extend(planning.list_week_plan_items(db, row.id))
    return items


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
