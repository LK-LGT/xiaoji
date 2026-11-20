"""P2-3 —— 案例库 + A/B 灰度回流 API。

权限口径
--------
- 反馈提交：本人 / 其主管 / HR（与「看绩效」口径一致）。
- Prompt 版本管理与灰度对比：**仅 HR**（改 prompt 会影响全局产出）。
- 案例库：全员可读（这是「参考做法」不是「查某人」）；**收录仅 HR/主管**。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import AssessmentRecord, Employee
from ..services import access_control
from ..services import feedback_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedbackIn(Input):
    record_id: str = Field(..., min_length=1, max_length=64)
    action: str = Field(..., min_length=1, max_length=32)
    agent: str = Field("comment_draft", min_length=1, max_length=64)
    prompt_version: str | None = Field(None, max_length=64)
    diff_summary: str | None = Field(None, max_length=2000)
    reason: str | None = Field(None, max_length=2000)
    dimension: str | None = Field(None, max_length=64)


class PromptIn(Input):
    agent: str = Field(..., min_length=1, max_length=64)
    version: str = Field(..., min_length=1, max_length=64)
    prompt_text: str = Field(..., min_length=1)
    rollout: str = Field("off", min_length=1, max_length=32)
    canary_ratio: float = Field(0.0, ge=0.0, le=1.0)
    baseline_version: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=2000)


class CaseIn(Input):
    title: str = Field(..., min_length=1, max_length=256)
    scenario: str = Field("general", min_length=1, max_length=64)
    resolution: str = Field(..., min_length=1, max_length=10000)
    source_record_id: str | None = Field(None, max_length=64)
    score_range: str | None = Field(None, max_length=32)


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "data": data}


def _require_hr(db: Session, user: Employee) -> None:
    if "hr" not in access_control.build_access_context(db, user).roles:
        raise HTTPException(status_code=403, detail="仅 HR 可管理 Prompt 版本")


def _require_manager_or_hr(db: Session, user: Employee) -> None:
    ctx = access_control.build_access_context(db, user)
    if not ({"hr", "manager", "department_leader"} & ctx.roles):
        raise HTTPException(status_code=403, detail="无权收录案例")


# ── 反馈 ────────────────────────────────────────────────────────────────────

@router.post("")
def submit_feedback(
    body: FeedbackIn,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """提交一条人工反馈（只落 ai_feedback，不改任何业务结论）。"""
    ctx = access_control.build_access_context(db, user)
    rec = db.get(AssessmentRecord, body.record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    if not ctx.can_view_score_of(rec.employee_id):
        raise HTTPException(status_code=403, detail="无权对该记录提交反馈")
    try:
        row = svc.record_feedback(
            db,
            record_id=body.record_id,
            actor_id=user.id,
            action=body.action,
            agent=body.agent,
            prompt_version=body.prompt_version,
            diff_summary=body.diff_summary,
            reason=body.reason,
            dimension=body.dimension,
        )
    except svc.FeedbackError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _ok(svc.serialize_feedback(row))


@router.get("")
def list_feedback(
    agent: str = Query("comment_draft", max_length=64),
    limit: int = Query(50, ge=1, le=200),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查看反馈（仅 HR）。"""
    _require_hr(db, user)
    from sqlalchemy import select

    from ..domain.models import AiFeedback

    rows = db.scalars(
        select(AiFeedback)
        .where(AiFeedback.agent == agent)
        .order_by(AiFeedback.created_at.desc())
        .limit(limit)
    ).all()
    return _ok(
        {
            "items": [svc.serialize_feedback(r) for r in rows],
            "stats": svc.feedback_stats(db, agent=agent),
        }
    )


# ── Prompt 版本与灰度 ───────────────────────────────────────────────────────

@router.get("/prompts")
def list_prompts(
    agent: str = Query("comment_draft", max_length=64),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """列出 Prompt 版本（仅 HR）。"""
    _require_hr(db, user)
    from sqlalchemy import select

    from ..domain.models import PromptVersion

    rows = db.scalars(
        select(PromptVersion)
        .where(PromptVersion.agent == agent)
        .order_by(PromptVersion.created_at.desc())
    ).all()
    return _ok([svc.serialize_prompt_version(r) for r in rows])


@router.put("/prompts")
def upsert_prompt(
    body: PromptIn,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """新增 / 更新 Prompt 版本（仅 HR）。"""
    _require_hr(db, user)
    try:
        row = svc.upsert_prompt_version(
            db,
            agent=body.agent,
            version=body.version,
            prompt_text=body.prompt_text,
            rollout=body.rollout,
            canary_ratio=body.canary_ratio,
            baseline_version=body.baseline_version,
            note=body.note,
            created_by=user.id,
        )
    except svc.FeedbackError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _ok(svc.serialize_prompt_version(row))


@router.get("/prompts/resolve")
def resolve_prompt(
    agent: str = Query("comment_draft", max_length=64),
    record_id: str = Query(..., min_length=1, max_length=64),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询某条记录会命中哪个 Prompt 版本（确定性分桶）。"""
    _require_hr(db, user)
    return _ok(svc.resolve_prompt(db, agent=agent, record_id=record_id))


@router.get("/prompts/compare")
def compare_prompts(
    agent: str = Query("comment_draft", max_length=64),
    versions: str | None = Query(None, description="逗号分隔的版本号"),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """灰度对比（仅 HR）—— 只对比人工反馈。"""
    _require_hr(db, user)
    version_list = [v.strip() for v in (versions or "").split(",") if v.strip()]
    return _ok(svc.compare_rollout(db, agent=agent, versions=version_list or None))


# ── 案例库 ──────────────────────────────────────────────────────────────────

@router.get("/cases")
def list_cases(
    scenario: str | None = Query(None, max_length=64),
    keyword: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """检索案例库（全员可读）。"""
    rows = svc.search_cases(db, scenario=scenario, keyword=keyword, limit=limit)
    return _ok([svc.serialize_case(r) for r in rows])


@router.post("/cases")
def create_case(
    body: CaseIn,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """收录案例（HR / 主管 / 部门负责人），强制脱敏。"""
    _require_manager_or_hr(db, user)
    try:
        row = svc.publish_case(
            db,
            title=body.title,
            scenario=body.scenario,
            resolution=body.resolution,
            actor_id=user.id,
            source_record_id=body.source_record_id,
            score_range=body.score_range,
        )
    except svc.FeedbackError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _ok(svc.serialize_case(row))
