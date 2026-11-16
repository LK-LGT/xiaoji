"""主管评分页所需的「待评记录列表」API。

背景（阶段二收尾发现的缺口 1）：
- `ManagerReview.vue` 需要「待评下属列表」，但后端原本只有单条记录详情接口。
- 本模块补齐列表接口，并统一走**阶段一统一权限上下文**，
  不使用「URL 参数传 current_user_id」这种可被伪造的做法（P0-3：身份只认 token）。

接口清单：
- GET /v1/assessment/records/pending   待我评分的记录列表
- GET /v1/assessment/records/periods   我有可见记录的考核周期列表
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...dependencies import get_current_user
from ...domain.models import (
    AssessmentRecord,
    AssessmentTask,
    CommentDraft,
    Employee,
    RatingDraft,
    Score,
)
from ...services import evidence_service
from ...services.access_control import (
    AccessContext,
    PermissionAction,
    build_access_context,
    can_access_employee,
)
from ...services.period import normalize_period, to_dashed_period

router = APIRouter(prefix="/v1/assessment/records", tags=["assessment-records"])


# 待评状态：员工已交自评，等待主管评分
PENDING_REVIEW_STATUSES: tuple[str, ...] = ("self_review_submitted",)


def _visible_record_ids(
    db: Session, context: AccessContext, task_id: str | None = None
) -> set[str]:
    """算出当前用户可见的考核记录 ID 集合。

    规则（与阶段一权限体系一致）：
    - HR / 管理员：全部可见
    - 部门负责人：管理范围内可见
    - 主管：直属下级可见
    - 普通员工：仅自己可见
    """
    stmt = select(AssessmentRecord)
    if task_id:
        stmt = stmt.where(AssessmentRecord.task_id == task_id)
    records = list(db.execute(stmt).scalars().all())

    if context.has_permission(PermissionAction.VIEW_ALL_SCORES):
        return {r.id for r in records}

    visible: set[str] = set()
    for record in records:
        target = db.get(Employee, record.employee_id)
        if target is None:
            continue
        if can_access_employee(context, target, db):
            visible.add(record.id)
    return visible


@router.get("/pending")
def list_pending_records(
    period: str | None = Query(
        default=None, description="考核周期，支持 202611 或 2026-11 两种格式"
    ),
    task_id: str | None = Query(default=None, description="限定某个考核任务"),
    include_all_statuses: bool = Query(
        default=False, description="是否返回全部状态（不只待评分）"
    ),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """待评记录列表 —— 主管评分页的主数据源。

    返回的每条记录都带 `period` 与 `period_display`，
    前端据此确认考勤证据的周期口径。
    """
    context = build_access_context(db, current_user)

    # 周期归一化：支持紧凑与带横杠两种格式
    normalized_period = normalize_period(period) if period else None
    if period and normalized_period is None:
        raise HTTPException(
            status_code=400, detail=f"考核周期格式非法：{period}"
        )

    stmt = select(AssessmentRecord)
    if task_id:
        stmt = stmt.where(AssessmentRecord.task_id == task_id)
    records = list(db.execute(stmt).scalars().all())

    # 权限过滤
    visible_ids = _visible_record_ids(db, context, task_id)

    items: list[dict[str, Any]] = []
    for record in records:
        if record.id not in visible_ids:
            continue
        if not include_all_statuses and record.status not in PENDING_REVIEW_STATUSES:
            continue

        task = db.get(AssessmentTask, record.task_id)
        record_period = task.period if task is not None else None
        if normalized_period and normalize_period(record_period) != normalized_period:
            continue

        employee = db.get(Employee, record.employee_id)
        items.append(
            {
                "record_id": record.id,
                "task_id": record.task_id,
                "task_name": task.name if task is not None else None,
                "period": record_period,
                "period_display": to_dashed_period(record_period),
                "employee": (
                    {
                        "id": employee.id,
                        "name": employee.name,
                        "position": employee.position,
                        "department_id": employee.department_id,
                    }
                    if employee is not None
                    else None
                ),
                "status": record.status,
                "confirmed_rating": record.confirmed_rating,
                "manager_comment": record.manager_comment,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
            }
        )

    # 待评的排前面，其余按更新时间倒序
    items.sort(
        key=lambda item: (
            0 if item["status"] in PENDING_REVIEW_STATUSES else 1,
            item["updated_at"],
        ),
        reverse=False,
    )

    # ⚠️ BUG-R6-36（同周期重复记录 → 列表刷屏 / 评错人）：
    # 历史上任务创建不幂等（BUG-R6-35），同一个 (员工, 周期) 下可能存在
    # 数十条考核记录。待评列表会把它们**全部**返回，
    # 主管看到同一个下属重复 N 行，很容易点错那一条，
    # 点到旧记录提交就会 409（BUG-R6-33）。
    #
    # 现在按 (员工, 周期) 去重：**优先保留最新的一条**
    # （待评 > 已评；同级取 updated_at 更新的），
    # 其余重复记录不再出现在待评列表里。
    deduped: dict[tuple[str, str | None], dict[str, Any]] = {}
    for item in items:
        emp = item.get("employee") or {}
        key = (emp.get("id") or "", item.get("period"))
        current = deduped.get(key)
        if current is None:
            deduped[key] = item
            continue
        # 比较优先级：待评 > 已评；同级比更新时间
        cur_pending = current["status"] in PENDING_REVIEW_STATUSES
        new_pending = item["status"] in PENDING_REVIEW_STATUSES
        if new_pending != cur_pending:
            if new_pending:
                deduped[key] = item
            continue
        if item["updated_at"] > current["updated_at"]:
            deduped[key] = item

    items = list(deduped.values())

    # 去重后重新排序：待评在前，同组内按更新时间倒序（新的在前）
    # 分两步排序：先按时间倒序，再用稳定的 sort 把待评提到最前。
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    items.sort(key=lambda item: 0 if item["status"] in PENDING_REVIEW_STATUSES else 1)

    return {
        "code": 0,
        "data": {
            "items": items,
            "total": len(items),
            "pending_total": sum(
                1 for i in items if i["status"] in PENDING_REVIEW_STATUSES
            ),
            "period": normalized_period,
            "period_display": to_dashed_period(normalized_period),
        },
    }


@router.get("/periods")
def list_visible_periods(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """当前用户有可见考核记录的周期列表（供前端筛选下拉）。"""
    context = build_access_context(db, current_user)
    visible_ids = _visible_record_ids(db, context)

    stmt = select(AssessmentRecord)
    records = list(db.execute(stmt).scalars().all())

    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.id not in visible_ids:
            continue
        task = db.get(AssessmentTask, record.task_id)
        record_period = task.period if task is not None else None
        if not record_period:
            continue
        bucket = buckets.setdefault(
            record_period,
            {
                "period": record_period,
                "period_display": to_dashed_period(record_period),
                "task_ids": set(),
                "record_count": 0,
                "pending_count": 0,
            },
        )
        bucket["task_ids"].add(record.task_id)
        bucket["record_count"] += 1
        if record.status in PENDING_REVIEW_STATUSES:
            bucket["pending_count"] += 1

    items = []
    for bucket in sorted(buckets.values(), key=lambda b: b["period"], reverse=True):
        items.append(
            {
                "period": bucket["period"],
                "period_display": bucket["period_display"],
                "task_ids": sorted(bucket["task_ids"]),
                "record_count": bucket["record_count"],
                "pending_count": bucket["pending_count"],
            }
        )

    return {"code": 0, "data": {"items": items, "total": len(items)}}


@router.get("/{record_id}")
def get_assessment_record(
    record_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """考核记录详情（主管评分页主数据源）。

    证据按**考核周期**过滤：考勤证据的周期写在 source_ref 里，
    必须用考核任务自身的 period 去筛，否则评 10 月绩效会混进 11 月的考勤异常。
    """
    record = db.get(AssessmentRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")

    context = build_access_context(db, current_user)
    employee = db.get(Employee, record.employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="考核记录关联的员工不存在")
    if not can_access_employee(context, employee, db):
        raise HTTPException(status_code=403, detail="无权查看该考核记录")

    task = db.get(AssessmentTask, record.task_id)
    period = task.period if task is not None else None

    # 注意：Score 模型有 query 列，会遮蔽 db.query 方法，这里统一用 select。
    score = db.execute(
        select(Score).where(Score.record_id == record_id)
    ).scalar_one_or_none()
    rating = db.execute(
        select(RatingDraft)
        .where(RatingDraft.record_id == record_id)
        .order_by(RatingDraft.created_at.desc())
    ).scalars().first()
    comment = db.execute(
        select(CommentDraft)
        .where(CommentDraft.record_id == record_id)
        .order_by(CommentDraft.created_at.desc())
    ).scalars().first()

    # 按考核周期过滤证据，避免跨月考勤异常混入
    ev_rows = evidence_service.list_scoring_evidence(
        db, record.employee_id, period=period
    )
    scoring_ev, reference_ev = evidence_service.split_by_usage(ev_rows)

    return {
        "code": 0,
        "data": {
            "record_id": record.id,
            "task_id": record.task_id,
            "status": record.status,
            "period": period,
            "period_display": to_dashed_period(period),
            "employee": {
                "id": employee.id,
                "name": employee.name,
                "position": employee.position,
                "department_id": employee.department_id,
            },
            "submit_source": record.submit_source,
            "confirmed_rating": record.confirmed_rating,
            "manager_comment": record.manager_comment,
            "self_review": record.self_review_content,
            "score": (
                {
                    "total": score.total,
                    "per_metric": score.per_metric_json,
                    "computed_at": score.computed_at.isoformat(),
                }
                if score
                else None
            ),
            "rating_draft": (
                {
                    "ratings": rating.ratings_json,
                    "citations": rating.citations,
                    "gaps": rating.gaps_json,
                    "fairness": rating.fairness_json,
                    "status": rating.status,
                }
                if rating
                else None
            ),
            "comment_draft": (
                {
                    "markdown": comment.markdown,
                    "citations": comment.citations,
                    "status": comment.status,
                }
                if comment
                else None
            ),
            "evidence": {
                "scoring": [
                    {
                        "id": e.id,
                        "summary": e.summary,
                        "impact": e.impact,
                        "source": e.source,
                    }
                    for e in scoring_ev
                ],
                # 决策②：参考类证据（考勤）单独返回，带免责标注
                "reference_only": evidence_service.build_attendance_view(reference_ev),
            },
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        },
    }
