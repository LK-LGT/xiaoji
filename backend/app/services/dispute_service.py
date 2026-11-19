"""绩效争议域服务层 —— 案件 CRUD、指派评审人、意见流转、裁决。

状态机（集中在 ``DISPUTE_TRANSITIONS``，非法迁移统一抛
``InvalidStateTransition``）::

    SUBMITTED ──assign──> REVIEWING
    SUBMITTED ──decide──> DECIDED
    REVIEWING ──supplement──> NEEDS_SUPPLEMENT
    REVIEWING ──decide──> DECIDED
    NEEDS_SUPPLEMENT ──supplement──> REVIEWING
    DECIDED ──archive──> ARCHIVED

权限模型：
- 员工：只能创建、查看自己的案件，补充材料时才能修改。
- 评审人（被指派）：可写意见。
- HR / 部门负责人：可指派评审人、可裁决、可归档。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import exceptions
from ..domain.enums import (
    DisputeDecision,
    DisputeOpinion,
    DisputePackageStatus,
    DisputeRecusalStatus,
    DisputeReviewerSource,
    DisputeStatus,
)
from ..domain.models import Employee
from ..domain.plan_models import (
    DisputeCase,
    DisputeOpinionRecord,
    DisputeReviewer,
)

logger = logging.getLogger(__name__)


#: 合法状态迁移：当前状态 → {动作: 目标状态}
DISPUTE_TRANSITIONS: dict[str, dict[str, str]] = {
    DisputeStatus.SUBMITTED: {
        "assign": DisputeStatus.REVIEWING,
        "decide": DisputeStatus.DECIDED,
    },
    DisputeStatus.REVIEWING: {
        "supplement": DisputeStatus.NEEDS_SUPPLEMENT,
        "decide": DisputeStatus.DECIDED,
    },
    DisputeStatus.NEEDS_SUPPLEMENT: {
        "supplement": DisputeStatus.REVIEWING,
        "decide": DisputeStatus.DECIDED,
    },
    DisputeStatus.DECIDED: {
        "archive": DisputeStatus.ARCHIVED,
    },
    DisputeStatus.ARCHIVED: {},
}


def assert_dispute_transition(current: str, action: str) -> str:
    """校验争议案件状态迁移合法，返回目标状态。"""
    target = DISPUTE_TRANSITIONS.get(current, {}).get(action)
    if target is None:
        raise exceptions.InvalidStateTransition("争议案件", current, action)
    return target


def _next_case_no(db: Session) -> str:
    """生成案件编号：``DS202611140001`` 形式（按天递增序号）。"""
    today = date.today()
    prefix = f"DS{today.strftime('%Y%m%d')}"
    stmt = select(func.count()).select_from(DisputeCase).where(
        DisputeCase.case_no.like(f"{prefix}%")
    )
    return f"{prefix}{int(db.execute(stmt).scalar_one()) + 1:04d}"


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def get_case(db: Session, case_id: str) -> DisputeCase:
    """按 ID 取争议案件。"""
    case = db.get(DisputeCase, case_id)
    if case is None:
        raise exceptions.RecordNotFound("争议案件", case_id)
    return case


def list_cases(
    db: Session,
    *,
    owner_id: str | None = None,
    department_id: str | None = None,
    reviewer_id: str | None = None,
    status: str | None = None,
    appeal_status: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DisputeCase]:
    """按条件查询争议案件列表。"""
    stmt = select(DisputeCase)
    if owner_id:
        stmt = stmt.where(DisputeCase.owner_id == owner_id)
    if department_id:
        stmt = stmt.where(DisputeCase.department_id == department_id)
    if reviewer_id:
        stmt = stmt.join(
            DisputeReviewer, DisputeReviewer.case_id == DisputeCase.id
        ).where(
            DisputeReviewer.user_id == reviewer_id,
            DisputeReviewer.recusal_status == DisputeRecusalStatus.NONE,
        )
    if status:
        stmt = stmt.where(DisputeCase.status == status)
    if appeal_status:
        stmt = stmt.where(DisputeCase.appeal_status == appeal_status)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            DisputeCase.case_no.like(like)
            | DisputeCase.appeal_title.like(like)
            | DisputeCase.dispute_subject.like(like)
        )
    stmt = stmt.order_by(DisputeCase.created_at.desc())
    stmt = stmt.limit(max(1, min(limit, 200))).offset(max(0, offset))
    return list(db.execute(stmt).scalars().unique().all())


def count_cases(
    db: Session,
    *,
    owner_id: str | None = None,
    department_id: str | None = None,
    reviewer_id: str | None = None,
    status: str | None = None,
    appeal_status: str | None = None,
    keyword: str | None = None,
) -> int:
    """统计争议案件数量（过滤条件与 list_cases 一致）。"""
    stmt = select(func.count()).select_from(DisputeCase)
    if owner_id:
        stmt = stmt.where(DisputeCase.owner_id == owner_id)
    if department_id:
        stmt = stmt.where(DisputeCase.department_id == department_id)
    if reviewer_id:
        stmt = stmt.join(
            DisputeReviewer, DisputeReviewer.case_id == DisputeCase.id
        ).where(
            DisputeReviewer.user_id == reviewer_id,
            DisputeReviewer.recusal_status == DisputeRecusalStatus.NONE,
        )
    if status:
        stmt = stmt.where(DisputeCase.status == status)
    if appeal_status:
        stmt = stmt.where(DisputeCase.appeal_status == appeal_status)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            DisputeCase.case_no.like(like)
            | DisputeCase.appeal_title.like(like)
            | DisputeCase.dispute_subject.like(like)
        )
    return int(db.execute(stmt).scalar_one())


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def create_case(
    db: Session,
    *,
    owner: Employee,
    appeal_title: str,
    appeal_reason: str,
    dispute_subject: str = "",
    department_id: str | None = None,
    appeal_id: str | None = None,
    related_result_id: str | None = None,
    result_title: str = "",
    result_status: str = "",
    period_start: date | None = None,
    period_end: date | None = None,
    package_items: list[dict[str, Any]] | None = None,
) -> DisputeCase:
    """创建争议案件（员工提交）。"""
    case = DisputeCase(
        case_no=_next_case_no(db),
        appeal_id=appeal_id,
        owner_id=owner.id,
        department_id=department_id if department_id is not None else owner.department_id,
        period_start=period_start,
        period_end=period_end,
        dispute_subject=dispute_subject,
        appeal_title=appeal_title,
        appeal_reason=appeal_reason,
        appeal_status="submitted",
        related_result_id=related_result_id,
        result_title=result_title,
        result_status=result_status,
        status=DisputeStatus.SUBMITTED,
        package_status=(
            DisputePackageStatus.READY
            if package_items
            else DisputePackageStatus.PENDING
        ),
        package_items=package_items or [],
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def assign_reviewer(
    db: Session,
    case: DisputeCase,
    reviewer: Employee,
    *,
    source_type: str = DisputeReviewerSource.MANUAL,
) -> DisputeReviewer:
    """指派评审人。

    重复指派同一评审人时返回已有记录（幂等）。

    首次指派会把案件从 ``SUBMITTED`` 推进到 ``REVIEWING`` ——
    案件进入评审中是「已开始评审」的客观信号，不应由路由层额外维护。
    """
    existing = db.execute(
        select(DisputeReviewer).where(
            DisputeReviewer.case_id == case.id,
            DisputeReviewer.user_id == reviewer.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    record = DisputeReviewer(
        case_id=case.id,
        user_id=reviewer.id,
        source_type=source_type,
        recusal_status=DisputeRecusalStatus.NONE,
    )
    db.add(record)
    db.flush()

    # SUBMITTED → REVIEWING（已评审中则保持不变）
    if case.status == DisputeStatus.SUBMITTED:
        case.status = assert_dispute_transition(case.status, "assign")
        db.flush()

    return record


def recuse_reviewer(
    db: Session,
    case: DisputeCase,
    reviewer_id: str,
    reason: str,
) -> DisputeReviewer:
    """评审人回避。"""
    record = db.execute(
        select(DisputeReviewer).where(
            DisputeReviewer.case_id == case.id,
            DisputeReviewer.user_id == reviewer_id,
        )
    ).scalar_one_or_none()
    if record is None:
        raise exceptions.RecordNotFound("争议评审人", reviewer_id)
    record.recusal_status = DisputeRecusalStatus.RECUSED
    record.recusal_reason = reason
    db.commit()
    db.refresh(record)
    return record


def submit_opinion(
    db: Session,
    case: DisputeCase,
    reviewer: Employee,
    opinion: str,
    comment: str = "",
) -> DisputeOpinionRecord:
    """提交评审意见（同一评审人再次提交则追加新版本）。"""
    if opinion not in {
        DisputeOpinion.SUPPORT,
        DisputeOpinion.REJECT,
        DisputeOpinion.SUPPLEMENT,
    }:
        raise exceptions.BaseAppError(
            code="INVALID_DISPUTE_OPINION",
            message=f"非法的评审意见类型：{opinion}",
            status_code=400,
        )

    reviewer_row = db.execute(
        select(DisputeReviewer).where(
            DisputeReviewer.case_id == case.id,
            DisputeReviewer.user_id == reviewer.id,
        )
    ).scalar_one_or_none()
    if reviewer_row is None:
        raise exceptions.PermissionDenied(
            user_id=reviewer.id,
            action="submit_dispute_opinion",
            resource=f"dispute_case:{case.id}",
        )
    if reviewer_row.recusal_status == DisputeRecusalStatus.RECUSED:
        raise exceptions.PermissionDenied(
            user_id=reviewer.id,
            action="submit_dispute_opinion",
            resource=f"dispute_case:{case.id}（该评审人已回避）",
        )

    next_version = (
        db.execute(
            select(func.max(DisputeOpinionRecord.version_no)).where(
                DisputeOpinionRecord.case_id == case.id,
                DisputeOpinionRecord.reviewer_id == reviewer_row.id,
            )
        ).scalar()
        or 0
    ) + 1

    record = DisputeOpinionRecord(
        case_id=case.id,
        reviewer_id=reviewer_row.id,
        opinion=opinion,
        comment=comment,
        version_no=next_version,
    )
    db.add(record)
    db.flush()

    # 需要补充材料时，案件退回待补充状态
    if opinion == DisputeOpinion.SUPPLEMENT:
        case.status = assert_dispute_transition(case.status, "supplement")
        case.package_status = DisputePackageStatus.MISSING
    elif case.status == DisputeStatus.SUBMITTED:
        case.status = assert_dispute_transition(case.status, "assign")

    db.commit()
    db.refresh(record)
    return record


def decide_case(
    db: Session,
    case: DisputeCase,
    decider: Employee,
    decision: str,
    comment: str = "",
) -> DisputeCase:
    """裁决争议案件。"""
    if decision not in {
        DisputeDecision.SUPPORT,
        DisputeDecision.REJECT,
        DisputeDecision.PARTIAL,
    }:
        raise exceptions.BaseAppError(
            code="INVALID_DISPUTE_DECISION",
            message=f"非法的裁决结果：{decision}",
            status_code=400,
        )

    case.status = assert_dispute_transition(case.status, "decide")
    case.decision = decision
    case.decision_comment = comment
    case.decided_at = datetime.utcnow()
    case.decided_by = decider.id
    case.appeal_status = "resolved"
    from .notification_service import enqueue_safe

    enqueue_safe(db, employee_id=case.owner_id, scene="N7", period=case.id,
                 object_id=case.id, object_type="dispute", label=case.result_title)
    db.commit()
    db.refresh(case)
    return case


def supplement_case(
    db: Session,
    case: DisputeCase,
    owner: Employee,
    package_items: list[dict[str, Any]] | None = None,
    appeal_reason: str | None = None,
) -> DisputeCase:
    """员工补充材料（仅本人、且案件处于待补充状态）。"""
    if case.owner_id != owner.id:
        raise exceptions.PermissionDenied(
            user_id=owner.id,
            action="supplement_dispute",
            resource=f"dispute_case:{case.id}",
        )
    if case.status != DisputeStatus.NEEDS_SUPPLEMENT:
        raise exceptions.InvalidStateTransition(
            "争议案件", case.status, "supplement"
        )

    if package_items is not None:
        case.package_items = package_items
        case.package_status = (
            DisputePackageStatus.READY
            if package_items
            else DisputePackageStatus.MISSING
        )
    if appeal_reason:
        case.appeal_reason = appeal_reason

    case.status = assert_dispute_transition(case.status, "supplement")
    case.appeal_status = "processing"
    db.commit()
    db.refresh(case)
    return case


def archive_case(db: Session, case: DisputeCase) -> DisputeCase:
    """归档争议案件（已裁决 → 已归档）。"""
    case.status = assert_dispute_transition(case.status, "archive")
    db.commit()
    db.refresh(case)
    return case


def list_opinions(db: Session, case_id: str) -> list[DisputeOpinionRecord]:
    """列出案件的评审意见（按时间正序）。"""
    stmt = (
        select(DisputeOpinionRecord)
        .where(DisputeOpinionRecord.case_id == case_id)
        .order_by(DisputeOpinionRecord.submitted_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def list_reviewers(db: Session, case_id: str) -> list[DisputeReviewer]:
    """列出案件的评审人。"""
    stmt = (
        select(DisputeReviewer)
        .where(DisputeReviewer.case_id == case_id)
        .order_by(DisputeReviewer.created_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# 候选评审人
# ---------------------------------------------------------------------------


def list_reviewer_candidates(
    db: Session,
    *,
    case_id: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
) -> list[Employee]:
    """列出可指派的评审人候选。

    排除：
    - 案件提交人本人（不能审自己的案子）；
    - 已被指派为该案件评审人的人；
    - 非在职 / 账号未启用的员工。
    """
    stmt = select(Employee).where(
        Employee.employment_status == "active",
        Employee.account_status == "enabled",
    )

    if case_id:
        case = get_case(db, case_id)
        stmt = stmt.where(Employee.id != case.owner_id)

        assigned = set(
            db.execute(
                select(DisputeReviewer.user_id).where(
                    DisputeReviewer.case_id == case_id
                )
            ).scalars()
        )
        if assigned:
            stmt = stmt.where(Employee.id.not_in(assigned))

    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Employee.name.like(like)
            | Employee.login_account.like(like)
            | Employee.position.like(like)
        )

    stmt = stmt.order_by(Employee.name.asc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# 序列化
#
# ⚠️ 字段名与 `frontend/src/api/dispute.ts` 的接口严格对齐。
# 后端主键是 UUID 字符串，前端类型声明为 `number` 是历史遗留；
# 在 JS 运行时字符串 ID 用于索引、比较与 URL 拼接都正常。
# ---------------------------------------------------------------------------


def serialize_case_summary(
    db: Session,
    case: DisputeCase,
    *,
    employee_name: str = "",
    org_name: str = "",
) -> dict[str, Any]:
    """案件摘要（列表与详情共用）。"""
    reviewer_count = db.execute(
        select(func.count())
        .select_from(DisputeReviewer)
        .where(DisputeReviewer.case_id == case.id)
    ).scalar_one()
    opinion_count = db.execute(
        select(func.count())
        .select_from(DisputeOpinionRecord)
        .where(DisputeOpinionRecord.case_id == case.id)
    ).scalar_one()

    return {
        "id": case.id,
        "caseNo": case.case_no,
        "appealId": case.appeal_id or "",
        "employeeName": employee_name,
        "orgName": org_name,
        "periodStart": _iso_date(case.period_start),
        "periodEnd": _iso_date(case.period_end),
        "disputeSubject": case.dispute_subject,
        "appealTitle": case.appeal_title,
        "status": case.status,
        "packageStatus": case.package_status,
        "deadlineAt": _iso_datetime(case.deadline_at),
        "reviewerCount": int(reviewer_count),
        "opinionCount": int(opinion_count),
    }


def serialize_reviewer(
    reviewer: DisputeReviewer,
    *,
    user_name: str = "",
    current_user_id: str | None = None,
) -> dict[str, Any]:
    """评审人记录。"""
    return {
        "id": reviewer.id,
        "userId": reviewer.user_id,
        "userName": user_name,
        "sourceType": reviewer.source_type,
        "recusalStatus": reviewer.recusal_status,
        "recusalReason": reviewer.recusal_reason or "",
        "currentUser": bool(current_user_id) and reviewer.user_id == current_user_id,
    }


def serialize_opinion(
    opinion: DisputeOpinionRecord,
    *,
    reviewer_name: str = "",
) -> dict[str, Any]:
    """评审意见记录。"""
    return {
        "id": opinion.id,
        "reviewerId": opinion.reviewer_id,
        "reviewerName": reviewer_name,
        "opinion": opinion.opinion,
        "comment": opinion.comment,
        "versionNo": opinion.version_no,
        "submittedAt": _iso_datetime(opinion.submitted_at),
    }


def serialize_candidate(employee: Employee) -> dict[str, Any]:
    """评审人候选。"""
    return {
        "userId": employee.id,
        "employeeNo": employee.login_account or "",
        "userName": employee.name,
        "deptId": employee.department_id or "",
    }


def _iso_date(value: date | None) -> str:
    """date → ISO 字符串。"""
    return value.isoformat() if value else ""


def _iso_datetime(value: datetime | None) -> str:
    """datetime → ISO 字符串。"""
    return value.isoformat() if value else ""
