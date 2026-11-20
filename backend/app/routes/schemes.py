"""HR scheme management; all mutation privileges are checked on the server."""

from __future__ import annotations

import copy
import hashlib
from datetime import date
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Department, Employee
from ..domain.scheme_models import PerformanceScheme, SchemeImportBatch, SchemeVersion
from ..services import scheme_service as service

router = APIRouter(prefix="/v1/performance", tags=["performance-schemes"])


class PayloadRequest(BaseModel):
    payload: dict[str, Any]
    revision: int = Field(default=1, ge=1)


class MappingRequest(BaseModel):
    mapping: dict[str, dict[str, str]]
    revision: int = Field(ge=1)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class StartRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    period: str
    period_type: str = "monthly"
    employee_ids: list[str] = Field(min_length=1, max_length=2000)
    self_review_deadline: str
    manager_review_deadline: str


def ok(data):
    return {"code": 0, "data": data}


def get_version(db, user, version_id, write=False):
    if write:
        service.hr(db, user)
    scope = service.allowed_departments(db, user)
    version = db.get(SchemeVersion, version_id)
    if version is None:
        service.fail("方案版本不存在", 404)
    if scope is not None and version.payload["binding"]["department_id"] not in scope:
        service.fail("无权查看其他部门方案", 403)
    return version


def get_batch(db, user, batch_id):
    service.hr(db, user)
    batch = db.get(SchemeImportBatch, batch_id)
    if batch is None or batch.operator_id != user.id:
        service.fail("导入批次不存在", 404)
    return batch


def batch_data(batch):
    return {
        "id": batch.id,
        "file_name": batch.file_name,
        "status": batch.status,
        "revision": batch.revision,
        "mapping": batch.mapping,
        "preview": batch.preview,
    }


class ResponseRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    metric_values: dict[str, Any] = Field(default_factory=dict)
    reviews: dict[str, Any] = Field(default_factory=dict)
    self_scores: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(default=0, ge=0)


class WorkflowRequest(BaseModel):
    action: str
    note: str = Field(min_length=1, max_length=4000)
    revision: int = Field(ge=0)


@router.post("/scheme-records/{record_id}/workflow")
def workflow_action(record_id: str, body: WorkflowRequest, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..services.scheme_workflow import transition
    return ok(transition(db, user, record_id, body))


@router.get("/schemes/import-template")
def template(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    service.hr(db, user)
    return Response(
        service.workbook_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="performance-schemes.xlsx"'},
    )


@router.get("/schemes/options")
def options(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    scope = service.allowed_departments(db, user)
    query = select(Department).where(Department.active.is_(True))
    if scope is not None:
        query = query.where(Department.id.in_(scope))
    departments = db.scalars(query).all()
    return ok(
        {
            "can_manage": scope is None,
            "columns": service.SHEETS,
            "departments": [{"id": d.id, "name": d.name} for d in departments],
            "positions": sorted(
                {
                    p
                    for p in db.scalars(
                        select(Employee.position).where(
                            Employee.department_id.in_([d.id for d in departments])
                        )
                    ).all()
                    if p
                }
            ),
        }
    )


@router.get("/schemes")
def listing(
    department_id: str | None = None,
    status: str | None = None,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = service.allowed_departments(db, user)
    # Payload scopes are validated against DB department IDs at every draft save and publish.
    versions = db.scalars(select(SchemeVersion).order_by(SchemeVersion.created_at.desc())).all()
    return ok(
        [
            service.serialize(db, v)
            for v in versions
            if (scope is None or v.payload["binding"]["department_id"] in scope)
            and (not department_id or v.payload["binding"]["department_id"] == department_id)
            and (not status or v.status == status)
        ]
    )


@router.post("/scheme-imports")
async def upload(
    file: UploadFile = File(...),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service.hr(db, user)
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".docx")):
        service.fail("请上传Excel（.xlsx）或Word（.docx）文件；旧版xls/doc请另存为新格式")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        service.fail("文件不能超过10MB")
    digest = hashlib.sha256(content).hexdigest()
    batch = db.scalar(
        select(SchemeImportBatch).where(
            SchemeImportBatch.operator_id == user.id, SchemeImportBatch.file_hash == digest
        )
    )
    if batch:
        if batch.status in ("invalid", "uploaded"):
            batch.revision += 1
            service.preview_import(db, batch)
        return ok(batch_data(batch))
    batch = SchemeImportBatch(
        operator_id=user.id, file_name=file.filename[:256], source_file=content, file_hash=digest
    )
    db.add(batch)
    db.flush()
    service.preview_import(db, batch)
    service.audit(db, user, "upload", batch.id, file_name=batch.file_name)
    return ok(batch_data(batch))


@router.get("/scheme-imports/{batch_id}")
def get_import(
    batch_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    return ok(batch_data(get_batch(db, user, batch_id)))


@router.put("/scheme-imports/{batch_id}/mapping")
def mapping(
    batch_id: str,
    body: MappingRequest,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_batch(db, user, batch_id)
    result = db.execute(
        update(SchemeImportBatch)
        .where(
            SchemeImportBatch.id == batch.id,
            SchemeImportBatch.revision == body.revision,
            SchemeImportBatch.status != "committed",
        )
        .values(
            mapping=body.mapping,
            status="uploaded",
            preview={},
            revision=SchemeImportBatch.revision + 1,
        )
    )
    if result.rowcount != 1:
        service.fail("批次已变化或已提交，请刷新", 409)
    db.refresh(batch)
    return ok(batch_data(batch))


@router.post("/scheme-imports/{batch_id}/validate")
def validate(
    batch_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    batch = get_batch(db, user, batch_id)
    result = db.execute(
        update(SchemeImportBatch)
        .where(SchemeImportBatch.id == batch.id, SchemeImportBatch.status != "committed")
        .values(revision=SchemeImportBatch.revision + 1)
    )
    if result.rowcount != 1:
        service.fail("已提交批次不能重新校验", 409)
    db.refresh(batch)
    service.preview_import(db, batch)
    return ok(batch_data(batch))


@router.post("/scheme-imports/{batch_id}/commit")
def commit(
    batch_id: str,
    body: RevisionRequest,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = get_batch(db, user, batch_id)
    if batch.status == "committed":
        return ok(
            [
                service.serialize(db, v)
                for v in db.scalars(
                    select(SchemeVersion).where(SchemeVersion.source_import_id == batch.id)
                ).all()
            ]
        )
    result = db.execute(
        update(SchemeImportBatch)
        .where(
            SchemeImportBatch.id == batch.id,
            SchemeImportBatch.revision == body.revision,
            SchemeImportBatch.status == "validated",
        )
        .values(status="committed", revision=SchemeImportBatch.revision + 1)
    )
    if result.rowcount != 1:
        service.fail("批次未通过校验或已变化，请刷新", 409)
    # Validate references again inside the same transaction before creating any versions.
    for p in batch.preview["schemes"]:
        errors = service.validate_payload(db, p, draft=True)
        if errors:
            service.fail("；".join(errors))
    versions = [
        service.create_version(db, user, p, source=batch.id) for p in batch.preview["schemes"]
    ]
    service.audit(db, user, "commit_import", batch.id)
    return ok([service.serialize(db, v) for v in versions])


@router.post("/schemes")
def create(
    body: PayloadRequest, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    service.hr(db, user)
    return ok(service.serialize(db, service.create_version(db, user, body.payload)))


@router.get("/scheme-versions/{version_id}")
def detail(
    version_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    return ok(service.serialize(db, get_version(db, user, version_id)))


@router.patch("/scheme-versions/{version_id}")
def save(
    version_id: str,
    body: PayloadRequest,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = get_version(db, user, version_id, write=True)
    scheme = db.get(PerformanceScheme, version.scheme_id)
    if body.payload.get("code") != scheme.code:
        service.fail("版本不能改变方案编码")
    errors = service.validate_payload(db, body.payload, draft=True)
    if errors:
        service.fail("；".join(errors))
    result = db.execute(
        update(SchemeVersion)
        .where(
            SchemeVersion.id == version.id,
            SchemeVersion.status == "draft",
            SchemeVersion.revision == body.revision,
        )
        .values(payload=body.payload, revision=SchemeVersion.revision + 1)
    )
    if result.rowcount != 1:
        service.fail("仅可修改未变化的草稿版本，请刷新", 409)
    service.audit(db, user, "save", version.id)
    db.refresh(version)
    return ok(service.serialize(db, version))


@router.post("/scheme-versions/{version_id}/publish")
def publish(
    version_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    version = get_version(db, user, version_id, write=True)
    return ok(service.serialize(db, service.publish(db, user, version)))


@router.post("/scheme-versions/{version_id}/copy")
def copy_version(
    version_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    old = get_version(db, user, version_id, write=True)
    return ok(
        service.serialize(
            db, service.create_version(db, user, copy.deepcopy(old.payload), copied_from=old.id)
        )
    )


@router.post("/scheme-versions/{version_id}/retire")
def retire(
    version_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    version = get_version(db, user, version_id, write=True)
    result = db.execute(
        update(SchemeVersion)
        .where(SchemeVersion.id == version.id, SchemeVersion.status == "published")
        .values(status="retired")
    )
    if result.rowcount != 1:
        service.fail("仅已发布方案可停用", 409)
    service.audit(db, user, "retire", version.id)
    db.refresh(version)
    return ok(service.serialize(db, version))


@router.get("/scheme-versions/{version_id}/audit")
def history(
    version_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    from ..domain.models import AuditLog

    get_version(db, user, version_id)
    return ok(
        [
            {
                "action": a.action,
                "actor_id": a.actor_id,
                "created_at": a.created_at,
                "metadata": a.metadata_json,
            }
            for a in db.scalars(
                select(AuditLog)
                .where(AuditLog.resource_id == version_id)
                .order_by(AuditLog.created_at)
            ).all()
        ]
    )


@router.get("/scheme-coverage")
def coverage(
    period: str = date.today().strftime("%Y%m"),
    period_type: str = "monthly",
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = service.allowed_departments(db, user)
    service.period_range(period, period_type)
    employees = db.scalars(select(Employee).where(Employee.employment_status == "active")).all()
    rows = []
    for e in employees:
        if scope is not None and e.department_id not in scope:
            continue
        row = {
            "employee_id": e.id,
            "name": e.name,
            "department_id": e.department_id,
            "position": e.position,
        }
        try:
            v = service.resolve(db, e, period, period_type)
            row.update(status="configured", version_id=v.id, scheme_name=v.payload["name"])
        except Exception as exc:
            from fastapi import HTTPException

            if not isinstance(exc, HTTPException):
                raise
            row.update(status="missing", message=exc.detail)
        rows.append(row)
    empty = [
        {"department_id": d.id, "name": d.name}
        for d in db.scalars(select(Department).where(Department.active.is_(True))).all()
        if (scope is None or d.id in scope) and not any(e.department_id == d.id for e in employees)
    ]
    return ok({"employees": rows, "empty_departments": empty})


@router.get("/scheme-resolution/me")
def me(
    period: str,
    period_type: str = "monthly",
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = service.resolve(db, user, period, period_type)
    # Employees see the published plan and result fields, not reviewer-only fields or internal notes.
    p = copy.deepcopy(version.payload)
    p["fields"] = [f for f in p["fields"] if f["stage"] != "manager_review"]
    return ok({"version_id": version.id, "version_no": version.version_no, "payload": p})


def response_context(db, user, record_id):
    from ..services.scheme_submission import response_context as context

    return context(db, user, record_id)


@router.get("/scheme-records")
def records(user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..domain.models import AssessmentRecord, AssessmentTask, ConfigSnapshot
    from ..services.access_control import build_access_context, can_access_employee

    ctx = build_access_context(db, user)
    result = []
    for r, t, s in db.execute(
        select(AssessmentRecord, AssessmentTask, ConfigSnapshot)
        .join(AssessmentTask, AssessmentRecord.task_id == AssessmentTask.id)
        .join(ConfigSnapshot, AssessmentTask.snapshot_id == ConfigSnapshot.id)
    ).all():
        if not (s.form_template_json or {}).get("scheme_version_id"):
            continue
        e = db.get(Employee, r.employee_id)
        if can_access_employee(ctx, e, db):
            result.append(
                {
                    "id": r.id,
                    "task_id": t.id,
                    "name": t.name,
                    "period": t.period,
                    "employee_name": e.name,
                    "is_own": e.id == user.id,
                    "status": r.status,
                }
            )
    return ok(result)


def _scheme_result(db, record_id):
    from ..domain.models import Score

    score = db.scalar(select(Score).where(Score.record_id == record_id))
    if score is None:
        return None
    return {
        "total": score.total,
        "rating": (score.trace_json or {}).get("scheme_rating"),
        "per_metric": score.per_metric_json,
    }


@router.get("/scheme-records/{record_id}/verification")
def record_verification(
    record_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    """网盘实测值 vs 员工申报值 —— **只提示，不判定，不自动改分**。

    主管在评分页调这个端点，看到「申报 6 条 / 实测 5 条」，
    自己判断是口径问题还是申报问题。

    ⚠️ **差异不产生任何分数影响** —— 与 ``semantic_qc`` /
    ``distribution_qc`` 口径一致：数据源只提供依据，判定权在人。
    """
    from ..domain.baidu_pan_models import BaiduPanCount
    from ..services.baidu_pan_service import SOURCE_CODE, compare_with_report

    record, employee, ctx, form, response = response_context(db, user, record_id)
    own = record.employee_id == user.id

    # ⚠️ 员工**本人**也能看自己的核对结果 —— 这是他的数据。
    # 主管/HR 也能看（``response_context`` 已经做过权限校验）。
    payload = form["scheme_payload"]
    metrics = payload.get("metrics") or []

    # 取最新的计数快照（覆盖周期窗口）。
    snapshot = db.scalar(
        select(BaiduPanCount)
        .where(BaiduPanCount.source_code == SOURCE_CODE)
        .order_by(BaiduPanCount.window_end.desc())
        .limit(1)
    )

    reported_values = (response.values if response else {}) or {}
    metric_values = (response.metric_values if response else {}) or {}

    items: list[dict[str, Any]] = []
    for metric in metrics:
        code = metric.get("code")
        if not code:
            continue
        # 员工申报的「实际完成值」—— ratio 型指标在 metric_values 里。
        reported = None
        mv = metric_values.get(code)
        if isinstance(mv, dict):
            reported = mv.get("actual")
        if reported in (None, ""):
            reported = reported_values.get(f"actual_{code}")
        if reported in (None, ""):
            continue

        actual = None
        if snapshot is not None:
            # ⚠️ 网盘是**部门级**计数，不是个人级 —— 只有当指标配了
            #    部门维度时才可比。这里保守处理：只报部门总量，
            #    由主管自己判断归属，不做自动匹配。
            actual = None

        items.append(
            {
                "metric_code": code,
                "metric_name": metric.get("name"),
                "reported": reported,
                "actual": actual,
                "comparison": compare_with_report(reported, actual),
            }
        )

    return ok(
        {
            "record_id": record.id,
            "is_own": own,
            "snapshot": {
                "window_start": snapshot.window_start,
                "window_end": snapshot.window_end,
                "total": snapshot.total,
                "daily_counts": snapshot.daily_counts or {},
                "as_of": snapshot.as_of.isoformat() if snapshot.as_of else None,
                "scanned_path": snapshot.scanned_path,
            }
            if snapshot
            else None,
            "items": items,
            "notice": (
                "网盘计数为**部门级**总量，未按人拆分；"
                "差异提示仅供参考，不产生任何分数影响，请人工评定。"
            ),
        }
    )


@router.get("/scheme-records/{record_id}")
def record_form(
    record_id: str, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    record, employee, ctx, form, response = response_context(db, user, record_id)
    own = record.employee_id == user.id
    p = copy.deepcopy(form["scheme_payload"])
    if own:
        p["fields"] = [f for f in p["fields"] if f["stage"] != "manager_review"]
    return ok(
        {
            "payload": p,
            "is_own": own,
            "task_id": record.task_id,
            "can_review": not own and ("department_leader" in ctx.roles or "manager" in ctx.roles),
            "status": record.status,
            "revision": response.revision if response else 0,
            "values": {k: v for k, v in (response.values if response else {}).items() if not k.startswith('_')},
            "self_scores": (response.values or {}).get('_self_scores', {}) if response else {},
            "workflow": copy.deepcopy((response.values or {}).get('_workflow', {})) if response else {},
            "can_finalize": 'hr' in ctx.roles,
            "metric_values": response.metric_values if response else {},
            "reviews": response.reviews if response and (not own or record.status in ('manager_review_submitted', 'confirmed', 'archived')) else {},
            "result": _scheme_result(db, record.id),
        }
    )


@router.put("/scheme-records/{record_id}")
def submit_form(
    record_id: str,
    body: ResponseRequest,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ..services.scheme_submission import submit_response

    return ok(submit_response(db, user, record_id, body))


class PeerAssignRequest(BaseModel):
    reviewer_ids: list[str] = Field(min_length=1, max_length=20)
    due_at: str | None = None


class PeerSubmitRequest(BaseModel):
    score: float | None = None
    comment: str = Field(min_length=1, max_length=2000)


@router.post("/scheme-records/{record_id}/peers")
def peer_assign(
    record_id: str,
    body: PeerAssignRequest,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from datetime import datetime

    from ..services import peer_service

    due = None
    if body.due_at:
        try:
            due = datetime.fromisoformat(body.due_at)
        except ValueError:
            service.fail("due_at 必须是 ISO 时间", 422)
    return ok(peer_service.assign(db, user, record_id, body.reviewer_ids, due))


@router.get("/scheme-records/{record_id}/peers")
def peer_list(
    record_id: str,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ..services import peer_service

    return ok(peer_service.list_for_record(db, user, record_id))


@router.put("/peers/{peer_id}")
def peer_submit(
    peer_id: str,
    body: PeerSubmitRequest,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ..services import peer_service

    return ok(peer_service.submit(db, user, peer_id, body.score, body.comment))


@router.get("/scheme-tasks/{task_id}/peer-progress")
def peer_progress(
    task_id: str,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ..services import peer_service
    from ..services.access_control import build_access_context

    ctx = build_access_context(db, user)
    if not ({"hr", "department_leader", "manager"} & ctx.roles):
        service.fail("仅HR/负责人/主管可查看互评进度", 403)
    return ok(peer_service.progress_for_task(db, task_id))


@router.post("/scheme-tasks/{task_id}/peer-remind")
def peer_remind(
    task_id: str,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ..services import peer_service

    service.hr(db, user)
    return ok({"reminded": peer_service.remind_overdue(db, task_id=task_id)})


@router.post("/scheme-tasks")
def start_tasks(
    body: StartRequest, user: Employee = Depends(get_current_user), db: Session = Depends(get_db)
):
    from datetime import datetime

    from ..domain.models import AssessmentRecord, AssessmentTask, ConfigSnapshot
    from ..services.notification_service import record_event

    service.hr(db, user)
    start, end = service.period_range(body.period, body.period_type)
    try:
        sr, mr = (
            datetime.fromisoformat(body.self_review_deadline),
            datetime.fromisoformat(body.manager_review_deadline),
        )
        if sr >= mr:
            raise ValueError
    except ValueError:
        service.fail("请设置有效的自评和主管评分截止时间，且自评早于主管评分")
    employees = db.scalars(
        select(Employee).where(
            Employee.id.in_(set(body.employee_ids)), Employee.employment_status == "active"
        )
    ).all()
    if len(employees) != len(set(body.employee_ids)):
        service.fail("被评员工不存在或已离职")
    groups = {}
    for e in employees:
        version = service.resolve(db, e, body.period, body.period_type)
        groups.setdefault(version.id, (version, []))[1].append(e)
    tasks = []
    # Scope mutex prevents parallel duplicate starts. All groups commit atomically.
    for version_id in sorted(groups):
        version, people = groups[version_id]
        db.execute(
            update(PerformanceScheme)
            .where(PerformanceScheme.id == version.scheme_id)
            .values(revision=PerformanceScheme.revision + 1)
        )
        db.refresh(version)
        if version.status != "published":
            service.fail("方案状态已变化，请重新发起", 409)
        period = body.period.replace("-", "")
        for old_task, _snapshot in db.execute(
            select(AssessmentTask, ConfigSnapshot)
            .join(ConfigSnapshot, AssessmentTask.snapshot_id == ConfigSnapshot.id)
            .where(AssessmentTask.period == period)
        ).all():
            old_ids = {
                r.employee_id
                for r in db.scalars(
                    select(AssessmentRecord).where(AssessmentRecord.task_id == old_task.id)
                ).all()
            }
            if old_ids & {e.id for e in people}:
                service.fail("该周期部分员工已有考核任务，请勿重复发起", 409)
        task = AssessmentTask(
            id=str(uuid4()),
            name=body.title,
            period=period,
            status="created",
            owner_id=user.id,
            self_review_deadline=sr,
            manager_review_deadline=mr,
        )
        db.add(task)
        db.flush()
        service.freeze_version(db, task, version, people)
        for e in people:
            record = AssessmentRecord(task_id=task.id, employee_id=e.id, status="not_started")
            db.add(record)
            db.flush()
            record_event(db, record, "N9")
        tasks.append(
            {"id": task.id, "scheme_version_id": version_id, "employee_count": len(people)}
        )
        service.audit(db, user, "start_task", task.id, scheme_version_id=version_id)
    return ok(tasks)
