"""飞书审批回流 + IM 通知服务（A8）。

定位
----
本模块负责**跨系统**的两件事，方向相反：

    1. **审批回流（飞书 → 本系统）**
       员工在飞书发起审批 → 飞书审批状态变化 → 本系统 `plan_approval` 落流水
       → 本系统计划状态机自动流转。

    2. **IM 通知（本系统 → 飞书）**
       员工在本系统提交计划 → 飞书给主管发待办消息。

设计要点
--------
1. **幂等是硬要求**。飞书 webhook 可能重复推送同一事件（至少一次投递语义），
   因此每次回流都以 ``dedup_key`` 去重，重复事件只更新状态、不重复写流水。
2. **回流不改业务语义**。本模块只把飞书的「通过 / 驳回」结论映射到本系统
   状态机的合法迁移；非法迁移（状态不对）只记日志、跳过该事件，不抛错打断
   整个 webhook 批次。
3. **通知失败不影响主流程**。发 IM 消息是旁路动作，失败只记日志，
   绝不回滚已经完成的业务事务。
4. **映射靠 `feishu_identity`**。飞书用户 → 本地员工走既有的
   `feishu_identity` 映射表，不新造一套。

关联关系
--------
    plan_approval (plan_type, plan_id) ──多态──> month_plan / week_plan / day_plan
    feishu_identity (identity_type, identity_value) ──> employee
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import PlanApprovalAction, PlanStatus
from ..domain.models import Employee
from ..domain.plan_models import PlanApproval
from . import plan_approval, planning

logger = logging.getLogger(__name__)


# ===========================================================================
# 常量
# ===========================================================================

# 飞书审批实例状态 → 本系统动作。
#
# 飞书 approval 实例 status 取值：
#   APPROVED   已通过
#   REJECTED   已拒绝
#   CANCELED   已撤销
#   PENDING    审批中
#   WITHDRAWN  已撤销（申请人主动撤回）
#
# 只有终态（APPROVED / REJECTED / CANCELED）才回流，其余状态忽略。
FEISHU_STATUS_TO_ACTION: dict[str, str] = {
    "APPROVED": PlanApprovalAction.APPROVE.value,
    "REJECTED": PlanApprovalAction.REJECT.value,
    "CANCELED": PlanApprovalAction.WITHDRAW.value,
}

# 回流动作 → 目标状态。与 `plan_approval` 内部常量保持一致。
_ACTION_TO_TARGET_STATUS: dict[str, str] = {
    PlanApprovalAction.APPROVE.value: PlanStatus.APPROVED.value,
    PlanApprovalAction.REJECT.value: PlanStatus.REJECTED.value,
}

# 回流事件默认动作（撤回 / 撤销）。
FEISHU_ACTION_WITHDRAW = PlanApprovalAction.WITHDRAW.value


# ===========================================================================
# 异常
# ===========================================================================


class FeishuApprovalSyncError(Exception):
    """飞书审批回流错误。"""

    def __init__(self, message: str, *, code: str = "FEISHU_SYNC_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass
class SyncOutcome:
    """单次回流的结果汇总。"""

    applied: int = 0
    skipped: int = 0
    failed: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "details": self.details,
        }


# ===========================================================================
# 飞书实例解析
# ===========================================================================


def extract_instance_status(instance: dict[str, Any]) -> str:
    """从飞书审批实例中取状态（大写）。

    兼容两种形态：
    - 直接给 `status` 字段
    - 包在 `approval_instances` / `items` 列表里
    """
    if not instance:
        return ""
    raw = instance.get("status") or ""
    for candidate in (
        instance,
        (instance.get("items") or [{}])[0] if instance.get("items") else {},
    ):
        if isinstance(candidate, dict):
            raw = candidate.get("status") or raw
            if raw:
                break
    return str(raw).upper()


def extract_instance_code(instance: dict[str, Any]) -> str:
    """取审批定义 code（`approval_code`）。"""
    if not instance:
        return ""
    raw = instance.get("approval_code") or instance.get("code") or ""
    if not raw and isinstance(instance.get("approval_instance"), dict):
        raw = instance["approval_instance"].get("approval_code") or ""
    return str(raw)


def extract_instance_id(instance: dict[str, Any]) -> str:
    """取审批实例 ID。"""
    if not instance:
        return ""
    raw = instance.get("instance_code") or instance.get("instance_id") or instance.get("id") or ""
    if not raw and isinstance(instance.get("approval_instance"), dict):
        raw = instance["approval_instance"].get("instance_code") or ""
    return str(raw)


def extract_user_id(instance: dict[str, Any]) -> str:
    """取审批发起人（飞书 user_id / open_id）。

    依次尝试 `user_id` → `open_id` → `applicant` 结构。
    """
    if not instance:
        return ""
    for key in ("user_id", "open_id"):
        value = instance.get(key)
        if value:
            return str(value)
    applicant = instance.get("applicant")
    if isinstance(applicant, dict):
        for key in ("user_id", "open_id"):
            value = applicant.get(key)
            if value:
                return str(value)
    return ""


def extract_comment(instance: dict[str, Any]) -> str:
    """取审批意见 / 备注。"""
    if not instance:
        return ""
    for key in ("comment", "approval_comment", "reason"):
        value = instance.get(key)
        if value:
            return str(value)
    return ""


# ===========================================================================
# 身份映射
# ===========================================================================


def resolve_employee_by_feishu_id(db: Session, feishu_id: str) -> Employee | None:
    """飞书用户 ID → 本地员工。

    依次尝试 `user_id` / `open_id` / `union_id` 三种映射类型，
    优先命中 `feishu_identity` 表（可解绑、可审计），回退到
    `employee` 表上的快照字段（组织架构同步时写入）。
    """
    if not feishu_id:
        return None

    from ..domain.models import FeishuIdentity

    for identity_type in ("user_id", "open_id", "union_id"):
        row = db.execute(
            select(FeishuIdentity).where(
                FeishuIdentity.identity_type == identity_type,
                FeishuIdentity.identity_value == feishu_id,
                FeishuIdentity.active.is_(True),
            )
        ).scalar_one_or_none()
        if row is not None:
            employee = db.get(Employee, row.employee_id)
            if employee is not None:
                return employee

    # 回退：employee 表上的飞书 ID 快照
    # ⚠️ 循环变量不要叫 `field` —— 会遮蔽 `dataclasses.field` 导入。
    for attr_name in ("feishu_user_id", "feishu_open_id", "feishu_union_id"):
        employee = db.execute(
            select(Employee).where(getattr(Employee, attr_name) == feishu_id)
        ).scalar_one_or_none()
        if employee is not None:
            return employee

    return None


def employee_feishu_receive_id(employee: Employee, *, prefer: str = "open_id") -> tuple[str, str]:
    """取可用于发 IM 的飞书 ID。

    Returns:
        `(receive_id, receive_id_type)`。取不到时返回 `("", "")`。
    """
    candidates = [
        ("open_id", employee.feishu_open_id),
        ("user_id", employee.feishu_user_id),
        ("union_id", employee.feishu_union_id),
    ]
    if prefer == "open_id":
        ordered = candidates
    else:
        ordered = [c for c in candidates if c[0] == prefer] + [
            c for c in candidates if c[0] != prefer
        ]
    for id_type, value in ordered:
        if value:
            return str(value), id_type
    return "", ""


# ===========================================================================
# 回流：飞书事件 → 本系统状态
# ===========================================================================


def _build_reflow_dedup_key(
    plan_type: str,
    plan_id: str,
    action: str,
    version_no: int,
    instance_id: str,
) -> str:
    """回流事件的幂等键。

    与本系统内部审批的幂等键**分开命名空间**（前缀 `feishu:`），
    避免飞书回流与本系统手动审批互相吞掉对方的流水。
    """
    return f"feishu:{plan_type}:{plan_id}:{action}:{version_no}:{instance_id}"


def apply_feishu_approval_event(
    db: Session,
    *,
    plan_type: str = "",
    plan_id: str = "",
    feishu_status: str,
    instance_id: str = "",
    correlation_key: str = "",
    approval_code: str = "",
    actor_feishu_id: str = "",
    comment: str | None = None,
) -> str:
    """把一个飞书审批状态应用到本系统计划。

    计划的定位方式（按优先级）：

    1. 事件**自带** ``plan_type`` / ``plan_id`` —— 直接用；
    2. 否则按 ``instance_code`` 查**绑定表**（本系统发起的审批）；
    3. 否则按 ``correlation_key`` 查绑定表（飞书侧发起的审批）。

    后两条正是「持久绑定」的价值 —— 让**员工从飞书侧发起**的审批也能回流。

    Returns:
        `applied` —— 状态已流转
        `skipped` —— 幂等命中 / 状态不匹配 / 非法迁移 / 无法定位计划，未变更

    Raises:
        FeishuApprovalSyncError: 计划不存在。
    """
    action = FEISHU_STATUS_TO_ACTION.get(str(feishu_status).upper())
    if action is None:
        # 非终态（如 PENDING）或未知状态 —— 忽略，不算失败
        logger.info("忽略非终态审批事件：status=%s plan=%s", feishu_status, plan_id)
        return "skipped"

    # ── 定位计划：事件自带 → 绑定表反查 ────────────────────────
    binding = None
    if not (plan_type and plan_id):
        from . import feishu_binding

        resolved_type, resolved_id, binding = feishu_binding.resolve_plan_by_instance(
            db,
            instance_code=instance_id,
            correlation_key=correlation_key,
            approval_code=approval_code,
        )
        if not resolved_id:
            # 定位不到计划 —— 不能瞎猜，记日志忽略（不报错，避免飞书反复重试）
            logger.warning(
                "飞书审批事件无法定位计划，已忽略：instance=%s corr=%s",
                instance_id or "-",
                correlation_key or "-",
            )
            return "skipped"
        plan_type, plan_id = resolved_type, resolved_id

    plan = plan_approval.fetch_plan(db, plan_type, plan_id)

    # 绑定表：回填实例状态（便于管理端排查「这个计划现在飞书那边什么状态」）
    if binding is not None:
        from . import feishu_binding

        feishu_binding.update_binding_status(db, binding, str(feishu_status).upper())

    # 已到终态就不重复流转（幂等的第二道保险）
    if plan.status in (PlanStatus.APPROVED.value, PlanStatus.REJECTED.value):
        logger.info(
            "计划已是终态，跳过飞书回流：plan=%s status=%s", plan_id, plan.status
        )
        return "skipped"

    from_status = plan.status
    target_status = _ACTION_TO_TARGET_STATUS.get(action)

    # 审批人：优先用飞书事件里的操作人；解析不到则用事件发起人占位
    actor_id = ""
    if actor_feishu_id:
        employee = resolve_employee_by_feishu_id(db, actor_feishu_id)
        if employee is not None:
            actor_id = employee.id

    if action == FEISHU_ACTION_WITHDRAW:
        # 撤回：飞书侧撤销 → 本系统回到草稿态
        target_status = PlanStatus.DRAFT.value
    elif target_status is None:
        return "skipped"

    # 校验状态机允许该迁移；不合法则跳过（不打断整个 webhook 批次）
    try:
        planning.assert_transition(from_status, target_status)
    except Exception as exc:
        logger.warning(
            "飞书回流迁移非法，跳过：plan=%s %s -> %s (%s)",
            plan_id,
            from_status,
            target_status,
            exc,
        )
        return "skipped"

    dedup_key = _build_reflow_dedup_key(
        plan_type, plan.id, action, plan.version_no, instance_id or "no-instance"
    )

    # 幂等：同一事件重复推送 → 命中已有流水，不重复写
    existing = db.execute(
        select(PlanApproval).where(PlanApproval.dedup_key == dedup_key)
    ).scalar_one_or_none()
    if existing is not None:
        logger.info("飞书回流幂等命中：%s", dedup_key)
        return "skipped"

    # 落库：状态 + 审批流水
    _apply_status_to_plan(plan, plan_type, action, target_status, comment)

    plan_approval._record_approval(
        db,
        plan_type=plan_type,
        plan_id=plan.id,
        actor_id=actor_id or "feishu",
        action=action,
        from_status=from_status,
        to_status=target_status,
        comment=comment,
        dedup_key=dedup_key,
    )
    db.flush()
    logger.info(
        "飞书审批回流成功：plan=%s %s -> %s action=%s",
        plan_id,
        from_status,
        target_status,
        action,
    )
    return "applied"


def _apply_status_to_plan(
    plan: Any,
    plan_type: str,
    action: str,
    target_status: str,
    comment: str | None,
) -> None:
    """把回流结果写回计划本体。

    三种计划的**列名不同**，必须分别处理，不能想当然地套用月计划：
    - 月计划：`approved_at` / `approval_comment`
    - 周计划：`approve_at` / `approval_comment`（注意是 `approve_at`）
    - 日计划：`review_status` / `reviewed_at` / `leader_comment`，
      且**不改主状态机**（抽检语义）
    """
    now = plan_approval._now()

    if plan_type == plan_approval.PLAN_TYPE_DAY:
        # 日计划抽检：不改主状态机，只记抽检结论
        if action == PlanApprovalAction.APPROVE.value:
            plan.review_status = plan_approval.REVIEW_APPROVED
        elif action == PlanApprovalAction.REJECT.value:
            plan.review_status = plan_approval.REVIEW_REJECTED
        plan.reviewed_at = now
        plan.leader_comment = comment or None
        plan.version_no = planning._bump_version(plan)
        return

    if plan_type == plan_approval.PLAN_TYPE_WEEK:
        plan.status = target_status
        plan.approve_at = now
        plan.approval_comment = comment or None
        plan.version_no = planning._bump_version(plan)
        return

    # 月计划
    plan.status = target_status
    plan.approved_at = now
    plan.approval_comment = comment or None
    plan.version_no = planning._bump_version(plan)


def sync_feishu_approval_instance(
    db: Session,
    *,
    plan_type: str = "",
    plan_id: str = "",
    instance_id: str,
    client: Any,
    actor_feishu_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """拉取飞书审批实例详情并回流到本系统。

    用于「主动拉取」模式（定时任务 / 手工触发），
    与「webhook 推送」模式互补 —— webhook 漏推时可由此补偿。

    ``plan_type`` / ``plan_id`` 可留空 —— 此时按 ``instance_id`` 走绑定表反查。
    """
    instance = client.fetch_approval_instance(instance_id)
    status = extract_instance_status(instance)
    if not status:
        raise FeishuApprovalSyncError(
            f"飞书审批实例 {instance_id} 未返回状态", code="FEISHU_NO_STATUS"
        )

    comment = extract_comment(instance)
    result = apply_feishu_approval_event(
        db,
        plan_type=plan_type,
        plan_id=plan_id,
        feishu_status=status,
        instance_id=instance_id,
        actor_feishu_id=actor_feishu_id or extract_user_id(instance),
        comment=comment or None,
    )
    return result, {"instance_id": instance_id, "status": status, "result": result}


# ===========================================================================
# IM 通知
# ===========================================================================


def build_plan_submitted_message(
    *,
    owner_name: str,
    plan_type: str,
    plan_title: str,
    submitter_note: str = "",
) -> str:
    """构造「计划待审批」通知文案。"""
    type_label = {"month": "月计划", "week": "周计划", "day": "日计划"}.get(
        plan_type, plan_type
    )
    lines = [f"【{type_label}待审批】{owner_name} 提交了{plan_title}。"]
    if submitter_note:
        lines.append(f"备注：{submitter_note}")
    lines.append("请在绩效管理系统中审批。")
    return "\n".join(lines)


def notify_approver_by_im(
    db: Session,
    *,
    approver_id: str,
    message: str,
    client: Any,
    receive_id_type: str = "open_id",
) -> bool:
    """给审批人发飞书 IM 消息。

    **失败不抛错** —— 通知是旁路动作，失败只记日志。
    Returns:
        True 表示已发出；False 表示未发出（无飞书 ID / 接口失败）。
    """
    employee = db.get(Employee, approver_id)
    if employee is None:
        logger.warning("通知跳过：审批人不存在 %s", approver_id)
        return False

    receive_id, actual_type = employee_feishu_receive_id(
        employee, prefer=receive_id_type
    )
    if not receive_id:
        logger.warning("通知跳过：员工 %s 无飞书 ID", employee.id)
        return False

    try:
        client.send_text_message(
            receive_id,
            message,
            receive_id_type=actual_type,
        )
    except Exception as exc:
        logger.warning("飞书 IM 通知失败（不影响主流程）：%s", exc)
        return False

    logger.info("飞书 IM 通知已发出：approver=%s type=%s", approver_id, actual_type)
    return True


def find_plan_approvers(db: Session, owner_id: str) -> list[Employee]:
    """取计划归属人的审批人（直属主管）。

    审批人语义与 `plan_approval` 一致：直属主管。
    """
    owner = db.get(Employee, owner_id)
    if owner is None or not owner.manager_id:
        return []
    manager = db.get(Employee, owner.manager_id)
    return [manager] if manager is not None else []


def notify_plan_submitted(
    db: Session,
    *,
    owner_id: str,
    plan_type: str,
    plan_id: str,
    plan_title: str,
    client: Any,
    submitter_note: str = "",
) -> bool:
    """计划提交后的完整通知流程：找审批人 → 发 IM。

    Returns:
        True 表示至少成功发出一条通知。
    """
    owner = db.get(Employee, owner_id)
    if owner is None:
        return False

    approvers = find_plan_approvers(db, owner_id)
    if not approvers:
        logger.info("计划 %s 无审批人，跳过通知", plan_id)
        return False

    message = build_plan_submitted_message(
        owner_name=owner.name,
        plan_type=plan_type,
        plan_title=plan_title,
        submitter_note=submitter_note,
    )

    sent = False
    for approver in approvers:
        if notify_approver_by_im(
            db, approver_id=approver.id, message=message, client=client
        ):
            sent = True
    return sent


def notify_plan_submitted_safe(
    db: Session,
    *,
    owner_id: str,
    plan_type: str,
    plan_id: str,
    plan_title: str,
    submitter_note: str = "",
) -> bool:
    """计划提交后发通知的**路由层入口**：内部吞掉一切异常。

    通知是旁路动作 —— 发不出去也不能让员工看到「提交失败」。
    因此这里：
    1. 飞书未配置 → 直接跳过；
    2. 任何异常 → 只记日志，不外抛。

    Returns:
        True 表示通知已发出（仅用于日志 / 测试断言）。
    """
    from ..core.config import settings

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.debug("飞书未配置，跳过计划提交通知：plan=%s", plan_id)
        return False

    from .feishu_client import FeishuClient

    client: FeishuClient | None = None
    try:
        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
        return notify_plan_submitted(
            db,
            owner_id=owner_id,
            plan_type=plan_type,
            plan_id=plan_id,
            plan_title=plan_title,
            client=client,
            submitter_note=submitter_note,
        )
    except Exception as exc:
        logger.warning("计划提交通知失败（不影响提交）：%s", exc)
        return False
    finally:
        if client is not None:
            client.close()


def bind_and_notify_plan_submitted_safe(
    db: Session,
    *,
    owner_id: str,
    plan_type: str,
    plan_id: str,
    plan_title: str,
    submitter_note: str = "",
) -> bool:
    """计划提交后建立绑定并写带关联码的发件箱，投递由后台任务执行。

    这是「飞书侧发起审批」这条通道的关键 ——
    绑定行上的 ``correlation_key`` 会随通知发给员工，
    员工在飞书审批表单里填入该码，回流时就能反查到本系统的计划。

    绑定建立失败不影响提交（绑定表写不进去只是少一条通道，
    不该让员工看到「提交失败」）。

    Returns:
        True 表示至少一条通知已入队，不代表飞书已收到。
    """
    from . import feishu_binding
    from .notification_service import enqueue_safe

    correlation_key = ""
    try:
        with db.begin_nested():
            binding = feishu_binding.bind_plan_to_instance(
                db, plan_type=plan_type, plan_id=plan_id,
                bind_source=feishu_binding.BIND_SOURCE_LOCAL_SUBMIT, created_by=owner_id,
            )
            correlation_key = binding.correlation_key or ""
    except Exception:
        logger.warning("计划绑定失败（不影响提交）", exc_info=True)

    owner = db.get(Employee, owner_id)
    plan = plan_approval.fetch_plan(db, plan_type.lower(), plan_id)
    queued = False
    for approver in find_plan_approvers(db, owner_id):
        row = enqueue_safe(
            db, employee_id=approver.id, scene="N1" if plan_type == "month" else "N2",
            period=plan_id, object_id=plan_id, object_type=f"{plan_type}_plan",
            label=f"{owner.name if owner else ''} {plan_title}",
            event_key=str(getattr(plan, "version_no", 1)),
        )
        if row is not None:
            if correlation_key and correlation_key not in row.content:
                row.content += f"\n飞书审批关联码：{correlation_key}"
            queued = True
    return queued


# ===========================================================================
# 流水查询（供管理端排查）
# ===========================================================================


def list_feishu_reflow_records(
    db: Session,
    *,
    plan_type: str | None = None,
    plan_id: str | None = None,
    limit: int = 100,
) -> list[PlanApproval]:
    """列出飞书回流产生的审批流水。

    依据 `dedup_key` 的 `feishu:` 前缀识别。
    """
    stmt = select(PlanApproval).where(PlanApproval.dedup_key.like("feishu:%"))
    if plan_type:
        stmt = stmt.where(PlanApproval.plan_type == plan_type)
    if plan_id:
        stmt = stmt.where(PlanApproval.plan_id == plan_id)
    stmt = stmt.order_by(PlanApproval.created_at.desc()).limit(min(limit, 500))
    return list(db.execute(stmt).scalars().all())


def serialize_reflow_record(row: PlanApproval) -> dict[str, Any]:
    """序列化回流流水。"""
    return {
        "id": row.id,
        "planType": row.plan_type,
        "planId": row.plan_id,
        "actorId": row.actor_id,
        "action": row.action,
        "fromStatus": row.from_status,
        "toStatus": row.to_status,
        "comment": row.comment or "",
        "dedupKey": row.dedup_key or "",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }


def find_plan_by_feishu_instance(
    db: Session,
    *,
    plan_type: str,
    plan_id: str,
) -> dict[str, Any] | None:
    """取计划与其飞书审批实例的关联信息。

    当前设计里计划与飞书实例的关联放在 `plan_draft_version` / 扩展列，
    这里先返回计划本身的基本信息，供回调用。
    """
    plan = plan_approval.fetch_plan(db, plan_type, plan_id)
    return {
        "planType": plan_type,
        "planId": plan.id,
        "status": plan.status,
        "versionNo": plan.version_no,
        "ownerId": getattr(plan, "owner_id", "") or "",
    }


def summarize_outcome(outcome: SyncOutcome) -> str:
    """把回流结果汇总成一行日志。"""
    return (
        f"applied={outcome.applied} skipped={outcome.skipped} "
        f"failed={outcome.failed}"
    )
