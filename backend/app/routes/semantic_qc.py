"""P2-1 —— 评语语义质检 / 风险识别 / 面谈提纲 API（**仅建议**）。

端点只做三件事：
1. 校验调用者对目标员工的可见性（复用 ``access_control``，不重复造权限逻辑）；
2. 从数据库读出**已存在的**事实（评分明细、证据数、评语草稿）；
3. 交给 ``semantic_qc`` 产出建议并原样返回。

⚠️ 本路由**不写任何业务表** —— 不改 Score、不改 confirmed_rating、
不改草稿状态、不推进流程。AI 的产出停留在「建议」层面，
由人在 UI 上决定采纳与否。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.enums import DraftStatus
from ..domain.models import (
    AssessmentRecord,
    CommentDraft,
    Employee,
    Evidence,
    Score,
)
from ..services import access_control, semantic_qc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/semantic-qc", tags=["semantic-qc"])


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "data": data}


def _load_context(db: Session, record_id: str) -> dict[str, Any]:
    """读取一条考核记录上的既有事实（只读）。"""
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")

    employee = db.get(Employee, rec.employee_id)

    # 评分明细：只读 Score，**不重算、不写回**。
    score = db.scalar(select(Score).where(Score.record_id == record_id))
    per_metric = list(score.per_metric_json) if score else []

    # 证据条数（用于「依据是否充分」的统计判断）。
    # 只判断「有没有」—— 不需要计数，也不需要把证据内容带出去。
    has_evidence = (
        db.scalar(
            select(Evidence.id).where(Evidence.employee_id == rec.employee_id).limit(1)
        )
        is not None
    )

    # 评语来源：优先取已确认/待确认的评语草稿；
    # 没有草稿时退回主管评语（人工写的原文）。
    draft = db.scalar(
        select(CommentDraft)
        .where(
            CommentDraft.record_id == record_id,
            CommentDraft.status.in_([DraftStatus.DRAFT.value, DraftStatus.CONFIRMED.value]),
        )
        .order_by(CommentDraft.created_at.desc())
        .limit(1)
    )
    comment = draft.markdown if draft else (rec.manager_comment or "")
    citation_count = len(draft.citations) if draft and draft.citations else 0

    task = rec.task
    return {
        "record": rec,
        "employee": employee,
        "period": task.period if task else None,
        "per_metric": per_metric,
        "has_evidence": has_evidence,
        "comment": comment,
        "citation_count": citation_count,
    }


@router.get("/records/{record_id}")
def analyze_record(
    record_id: str,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """对一条考核记录做语义质检 + 风险识别 + 面谈提纲。

    权限：本人、其主管、HR、相关部门负责人可见（与既有「看绩效」口径一致）。
    """
    ctx = access_control.build_access_context(db, user)
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    if not ctx.can_view_score_of(rec.employee_id):
        raise HTTPException(status_code=403, detail="无权查看该员工的绩效数据")

    data = _load_context(db, record_id)
    employee = data["employee"]

    result = semantic_qc.analyze(
        comment=data["comment"],
        per_metric=data["per_metric"],
        evidence_count=1 if data["has_evidence"] else 0,
        citation_count=data["citation_count"],
        has_gap_section=False,
        attendance_anomaly_count=0,
        confirmed_rating=rec.confirmed_rating,
        employee_name=employee.name if employee else "",
    )
    result["recordId"] = record_id
    result["employeeId"] = rec.employee_id
    result["period"] = data["period"]
    return _ok(result)
