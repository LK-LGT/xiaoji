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
from ...domain.models import AssessmentRecord, AssessmentTask, Employee
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
