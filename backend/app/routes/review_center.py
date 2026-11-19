"""绩效复核与校准中心。所有端点强制认证，业务服务统一校验数据范围。"""
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import AssessmentRecord, Department, Employee
from ..domain.plan_models import DisputeCase
from ..domain.review_models import (
    CalibrationMeeting,
    CalibrationResolution,
    ReviewArtifact,
    ReviewCase,
    ReviewOutbox,
    ScoreAdjustment,
    ScoreVersion,
)
from ..services import review_center as svc

router = APIRouter(prefix="/v1/review-center", tags=["review-center"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCase(Input):
    target: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=10000)
    parent_id: str | None = None


class Action(Input):
    revision: int = Field(ge=1)
    action: str
    comment: str = Field(min_length=1, max_length=10000)
    reviewer_id: str | None = None


class Revision(Input):
    revision: int = Field(ge=1)


class Opinion(Revision):
    value: Literal["SUPPORT", "REJECT", "SUPPLEMENT"]
    comment: str = Field(min_length=1, max_length=10000)


class Adjustment(Input):
    case_id: str | None = None
    meeting_id: str | None = None
    record_id: str
    expected_version: int = Field(ge=1)
    proposed: dict[str, Any] = Field(default_factory=dict)
    corrections: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=10000)


class Approval(Revision):
    approve: bool
    comment: str = Field(min_length=1, max_length=10000)


class Meeting(Input):
    title: str = Field(min_length=1, max_length=200)
    period: str = Field(pattern=r"^\d{6}$")
    departments: list[str] = Field(min_length=1, max_length=100)
    participants: list[str] = Field(default_factory=list, max_length=100)


class Resolution(Revision):
    record_id: str | None = None
    action: Literal["KEEP", "REVIEW", "ADJUST", "POLICY"]
    basis: str = Field(min_length=1, max_length=10000)
    owner_id: str
    due_at: datetime


def ok(data):
    return {"code": 0, "data": data}


@router.get("/capabilities")
def capabilities(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    ctx = svc.context(db, user)
    departments = db.execute(select(Department)).scalars().all()
    return ok({"userId": user.id, "canManage": bool({"hr", "department_leader"} & ctx.roles), "isHr": "hr" in ctx.roles, "departments": [{"id": d.id, "name": d.name} for d in departments if svc.manages(db, user, d.id)], "policy": svc.DEFAULT_POLICY, "storage": "数据库内私有附件；未接入独立对象存储及恶意文件扫描", "integrity": "应用只追加及哈希校验；独立存证未部署"})


@router.get("/options")
def options(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(svc.appeal_options(db, user))


@router.get("/people")
def people(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    ctx = svc.context(db, user)
    if not ({"hr", "department_leader"} & ctx.roles):
        svc.fail("仅复核管理人员可查询候选人员", 403)
    rows = db.scalars(select(Employee).where(Employee.employment_status == "active", Employee.account_status == "enabled")).all()
    return ok([{"id": e.id, "name": e.name} for e in rows if "hr" in ctx.roles or e.department_id in ctx.managed_department_ids])


@router.get("/cases")
def cases(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    # 权限过滤先于分页，避免分页泄露与漏项。
    rows = db.scalars(select(DisputeCase).join(ReviewCase, ReviewCase.id == DisputeCase.id).order_by(DisputeCase.created_at.desc())).all()
    visible = [c for c in rows if svc.can_view(db, user, c)]
    return ok({"items": [svc.serialize_case(db, c, user) for c in visible[offset:offset + limit]], "total": len(visible)})


@router.post("/cases")
def create_case(body: CreateCase, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.create_case(db, user, target=body.target, title=body.title, reason=body.reason, parent_id=body.parent_id)
    return ok(svc.serialize_case(db, row, user, detail=True))


@router.get("/cases/{case_id}")
def case_detail(case_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.get_case(db, case_id)
    svc.require_view(db, user, row)
    return ok(svc.serialize_case(db, row, user, detail=True))


@router.post("/cases/{case_id}/actions")
def case_action(case_id: str, body: Action, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.act_case(db, user, svc.get_case(db, case_id), **body.model_dump())
    return ok(svc.serialize_case(db, row, user, detail=True))


@router.post("/cases/{case_id}/opinions")
def case_opinion(case_id: str, body: Opinion, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.get_case(db, case_id)
    svc.opinion(db, user, row, **body.model_dump())
    return ok(svc.serialize_case(db, row, user, detail=True))


@router.post("/cases/{case_id}/evidence")
async def evidence_upload(case_id: str, revision: int = Form(...), file: UploadFile = File(...), user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    content = await file.read(svc.DEFAULT_POLICY["max_file_bytes"] + 1)
    row = svc.upload(db, user, svc.get_case(db, case_id), revision=revision, name=file.filename or "", content=content)
    return ok({"id": row.id, "name": row.name, "digest": row.digest})


@router.get("/cases/{case_id}/evidence/{artifact_id}")
def evidence_download(case_id: str, artifact_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    case = svc.get_case(db, case_id)
    svc.require_view(db, user, case)
    row = db.get(ReviewArtifact, artifact_id)
    if not row or row.case_id != case.id:
        svc.fail("附件不存在", 404)
    import hashlib
    if hashlib.sha256(row.content).hexdigest() != row.digest:
        svc.fail("附件完整性异常，已阻止下载")
    svc.emit(db, f"download:{uuid4()}", "evidence.downloaded", {"case_id": case.id, "artifact_id": row.id, "actor_id": user.id})
    return Response(row.content, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="evidence-{row.id}.{row.name.rsplit(".", 1)[-1]}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/cases/{case_id}/package")
def case_package(case_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    content = svc.package(db, user, svc.get_case(db, case_id))
    svc.emit(db, f"package:{uuid4()}", "evidence.exported", {"case_id": case_id, "actor_id": user.id})
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="review-{case_id}.zip"', "Cache-Control": "no-store"})


@router.post("/cases/{case_id}/reports")
def report(case_id: str, body: Revision, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.generate_report(db, user, svc.get_case(db, case_id), body.revision)
    return ok({"id": row.id, "version": row.version, "content": row.content})


@router.get("/cases/{case_id}/integrity")
def integrity(case_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(svc.integrity(db, user, svc.get_case(db, case_id)))


@router.get("/records/{record_id}/versions")
def versions(record_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = db.get(AssessmentRecord, record_id)
    if not rec:
        svc.fail("考核记录不存在", 404)
    if rec.employee_id != user.id:
        svc.require_manager(db, user, db.get(Employee, rec.employee_id).department_id)
    return ok([{"id": v.id, "version": v.version, "content": v.content, "reason": v.reason, "actorId": v.actor_id, "digest": v.digest, "previousHash": v.previous_hash, "sourceId": v.source_id, "createdAt": v.created_at.isoformat()} for v in db.scalars(select(ScoreVersion).where(ScoreVersion.record_id == record_id).order_by(ScoreVersion.version)).all()])


@router.get("/records/{record_id}/adjustment-context")
def adjustment_context(record_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(svc.adjustment_context(db, user, record_id))


@router.get("/adjustments")
def adjustments(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ScoreAdjustment).order_by(ScoreAdjustment.created_at.desc())).all()
    result = []
    for row in rows:
        rec = db.get(AssessmentRecord, row.record_id)
        if rec.employee_id == user.id or svc.manages(db, user, db.get(Employee, rec.employee_id).department_id):
            result.append(svc.adjustment_item(row))
    return ok(result)


@router.post("/adjustments")
def adjustment(body: Adjustment, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(svc.adjustment_item(svc.request_adjustment(db, user, **body.model_dump())))


@router.post("/adjustments/{adjustment_id}/approval")
def approval(adjustment_id: str, body: Approval, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(ScoreAdjustment, adjustment_id)
    if not row:
        svc.fail("调整单不存在", 404)
    return ok(svc.adjustment_item(svc.approve_adjustment(db, user, row, **body.model_dump())))


@router.get("/meetings")
def meetings(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CalibrationMeeting).order_by(CalibrationMeeting.created_at.desc())).all()
    ctx = svc.context(db, user)
    return ok([{"id": m.id, "title": m.title, "period": m.period, "status": m.status, "revision": m.revision} for m in rows if "hr" in ctx.roles or ("department_leader" in ctx.roles and set(m.department_ids) <= ctx.managed_department_ids) or user.id in m.participants])


@router.post("/meetings")
def meeting_create(body: Meeting, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.create_meeting(db, user, **body.model_dump())
    return ok({"id": row.id})


@router.get("/meetings/{meeting_id}")
def meeting_detail(meeting_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    m = svc.get_meeting(db, user, meeting_id)
    return ok({"id": m.id, "title": m.title, "period": m.period, "status": m.status, "revision": m.revision, "snapshot": m.snapshot, "participants": m.participants, "resolutions": [{"id": r.id, "recordId": r.record_id, "action": r.action, "basis": r.basis, "ownerId": r.owner_id, "dueAt": r.due_at.isoformat(), "content": r.content} for r in db.scalars(select(CalibrationResolution).where(CalibrationResolution.meeting_id == m.id)).all()]})


@router.post("/meetings/{meeting_id}/resolutions")
def meeting_resolution(meeting_id: str, body: Resolution, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.resolve_meeting(db, user, svc.get_meeting(db, user, meeting_id, manage=True), **body.model_dump())
    return ok({"id": row.id})


@router.post("/meetings/{meeting_id}/close")
def meeting_close(meeting_id: str, body: Revision, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.close_meeting(db, user, svc.get_meeting(db, user, meeting_id, manage=True), body.revision)
    return ok(None)


@router.get("/outbox")
def outbox(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    if "hr" not in svc.context(db, user).roles:
        svc.fail("仅HR可查看投递状态", 403)
    return ok([{"id": r.id, "topic": r.topic, "status": r.status, "attempts": r.attempts, "lastError": r.last_error} for r in db.scalars(select(ReviewOutbox).order_by(ReviewOutbox.created_at.desc()).limit(200)).all()])
