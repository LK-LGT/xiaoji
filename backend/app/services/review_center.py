"""复核校准应用服务。事务由请求依赖提交；所有写操作使用版本闸门。"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import statistics
import zipfile
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update

from ..dependencies import build_access_context_for_user
from ..domain.models import (
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
    Employee,
    ManagerReview,
    MetricValue,
    Score,
    _now,
)
from ..domain.plan_models import DisputeCase, DisputeOpinionRecord, DisputeReviewer
from ..domain.result_models import Result
from ..domain.review_models import (
    CalibrationMeeting,
    CalibrationResolution,
    ReviewArtifact,
    ReviewCase,
    ReviewEvent,
    ReviewOutbox,
    ScoreAdjustment,
    ScoreHead,
    ScoreVersion,
    VerificationReport,
)

DEFAULT_POLICY = {"version": "review-v1", "appeal_days": 3, "processing_days": 7, "supplement_days": 3, "max_file_bytes": 10 * 1024 * 1024}
TERMINAL = {"DECIDED", "ARCHIVED", "WITHDRAWN", "NOT_ACCEPTED"}


def fail(message, code=409):
    raise HTTPException(code, message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=lambda x: x.isoformat())


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def context(db, user):
    return build_access_context_for_user(user, db)


def manages(db, user, department_id):
    ctx = context(db, user)
    return "hr" in ctx.roles or ("department_leader" in ctx.roles and department_id in ctx.managed_department_ids)


def require_manager(db, user, department_id):
    if not manages(db, user, department_id):
        fail("无权管理该部门复核事项", 403)


def get_case(db, case_id):
    case = db.get(DisputeCase, case_id)
    if case is None:
        fail("复核案件不存在", 404)
    return case


def can_view(db, user, case):
    if case.owner_id == user.id or manages(db, user, case.department_id):
        return True
    return db.scalar(select(DisputeReviewer.id).where(DisputeReviewer.case_id == case.id, DisputeReviewer.user_id == user.id, DisputeReviewer.recusal_status == "NONE")) is not None


def require_view(db, user, case):
    if not can_view(db, user, case):
        fail("无权查看该复核案件", 403)


def require_independent(db, user, case):
    ext = db.get(ReviewCase, case.id)
    if user.id == case.owner_id or (ext and user.id == ext.original_reviewer_id):
        fail("案件本人和原评分人不得独立复核或裁决，请回避并升级", 403)


def lock_case(db, case_id, expected):
    ext = db.get(ReviewCase, case_id)
    if ext is None:
        fail("历史案件尚未建立复核快照，请先办理迁移", 409)
    changed = db.execute(update(ReviewCase).where(ReviewCase.id == case_id, ReviewCase.revision == expected).values(revision=expected + 1), execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        fail("案件已更新，请刷新后重试")
    db.refresh(ext)
    return ext


def case_event(db, case, user, action, payload):
    previous = db.scalar(select(ReviewEvent).where(ReviewEvent.case_id == case.id).order_by(ReviewEvent.sequence.desc()).limit(1))
    sequence = previous.sequence + 1 if previous else 1
    prev_hash = previous.digest if previous else ""
    created = _now()
    body = {"case_id": case.id, "sequence": sequence, "actor_id": user.id, "action": action, "payload": payload, "previous_hash": prev_hash, "created_at": created.isoformat()}
    db.add(ReviewEvent(**{**body, "created_at": created}, digest=digest(body)))
    db.flush()


def score_content(score, rec):
    return {"total": score.total, "per_metric": copy.deepcopy(score.per_metric_json), "trace": copy.deepcopy(score.trace_json), "rating": (score.trace_json or {}).get("scheme_rating") or rec.confirmed_rating}


def append_score_version(db, rec, content, *, actor_id, reason, source_id=None, expected=None, protect=False):
    head = db.get(ScoreHead, rec.id)
    if head is None:
        head = ScoreHead(record_id=rec.id, version=0, protected=0)
        db.add(head)
        db.flush()
    version = head.version
    if expected is not None and version != expected:
        fail("评分版本已变化，必须重新核验和审批")
    result = db.execute(update(ScoreHead).where(ScoreHead.record_id == rec.id, ScoreHead.version == version).values(version=version + 1, protected=1 if protect else head.protected), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        fail("评分正在被其他操作更新，请重试")
    previous = db.scalar(select(ScoreVersion).where(ScoreVersion.record_id == rec.id, ScoreVersion.version == version))
    prev_hash = previous.digest if previous else ""
    created = _now()
    body = {"record_id": rec.id, "version": version + 1, "content": content, "reason": reason, "actor_id": actor_id, "source_id": source_id, "previous_hash": prev_hash, "created_at": created.isoformat()}
    row = ScoreVersion(**{**body, "created_at": created}, digest=digest(body))
    db.add(row)
    db.flush()
    db.refresh(head)
    return row


def capture_score(db, rec, score, actor_id="rule-engine", reason="规则计分"):
    content = score_content(score, rec)
    head = db.get(ScoreHead, rec.id)
    previous = db.scalar(select(ScoreVersion).where(ScoreVersion.record_id == rec.id).order_by(ScoreVersion.version.desc()).limit(1))
    if previous and previous.content == content:
        return previous
    if head and head.protected:
        fail("该评分已进入复核保护，修改必须经分数调整单审批")
    return append_score_version(db, rec, content, actor_id=actor_id, reason=reason)


def record_snapshot(db, rec):
    task = db.get(AssessmentTask, rec.task_id)
    snap = db.get(ConfigSnapshot, task.snapshot_id) if task and task.snapshot_id else None
    score = db.scalar(select(Score).where(Score.record_id == rec.id))
    if not task or not snap or not score:
        fail("考核结果或冻结规则不完整，不能启动评分复核")
    version = capture_score(db, rec, score)
    inputs = db.scalars(select(MetricValue).where(MetricValue.employee_id == rec.employee_id, MetricValue.period == task.period)).all()
    from ..domain.scheme_models import SchemeResponse
    response = db.get(SchemeResponse, rec.id)
    reviewer = db.scalar(select(ManagerReview).where(ManagerReview.record_id == rec.id))
    result = {"record_id": rec.id, "task_id": task.id, "period": task.period, "score_version": version.version, "score": version.content, "rules": {"id": snap.id, "version": snap.config_version, "metrics": snap.metrics_json, "weights": snap.weights_json, "rubrics": snap.rubric_json, "form": snap.form_template_json}, "inputs": [{"id": v.id, "metric_code": v.metric_code, "value": v.value, "target": v.target, "unit": v.unit, "source": v.source, "source_ref": v.source_ref, "as_of": v.as_of.isoformat() if v.as_of else None} for v in inputs], "reviews": copy.deepcopy(response.reviews if response else {}), "manager_comment": rec.manager_comment, "captured_at": _now().isoformat()}
    if (snapshot_payload := (snap.form_template_json or {}).get("scheme_payload", {})).get("scoring_mode") != "direct_points":
        from dataclasses import asdict

        from .metric_context import build_metric_context
        specs, normalized = build_metric_context(db, snap, rec.employee_id, task.period)
        result["normalized_specs"] = [asdict(s) for s in specs]
        result["normalized_inputs"] = [asdict(v) for v in normalized]
    result["scheme_payload"] = snapshot_payload
    original = response.reviewer_id if response else reviewer.reviewer_id if reviewer else None
    if not original:
        original = (db.get(Employee, rec.employee_id)).manager_id
    return result, original


def appeal_options(db, user):
    options = []
    records = db.scalars(select(AssessmentRecord).where(AssessmentRecord.employee_id == user.id)).all()
    for rec in records:
        score = db.scalar(select(Score).where(Score.record_id == rec.id))
        if not score or rec.status not in {"manager_review_submitted", "confirmed", "archived"}:
            continue
        deadline = score.computed_at + timedelta(days=DEFAULT_POLICY["appeal_days"])
        if deadline < _now() or db.scalar(select(ReviewCase.id).where(ReviewCase.active_key == f"score:{rec.id}")):
            continue
        task = db.get(AssessmentTask, rec.task_id)
        options.append({"resultId": f"score:{rec.id}", "label": f"{task.name} / {task.period} / {score.total}分", "status": "confirmed", "deadline": deadline.isoformat()})
    for result in db.scalars(select(Result).where(Result.owner_id == user.id, Result.status.in_(["confirmed", "rejected"]))).all():
        if not result.approved_at:
            continue
        deadline = result.approved_at + timedelta(days=DEFAULT_POLICY["appeal_days"])
        if deadline < _now() or db.scalar(select(ReviewCase.id).where(ReviewCase.active_key == f"result:{result.id}")):
            continue
        options.append({"resultId": result.id, "label": result.title, "status": result.status, "deadline": deadline.isoformat()})
    return options


def create_case(db, user, *, target, title, reason, parent_id=None, initiated=False):
    if not title.strip() or not reason.strip():
        fail("申诉标题和理由不能为空", 422)
    record_id = target.removeprefix("score:") if target.startswith("score:") else None
    rec = db.get(AssessmentRecord, record_id) if record_id else None
    result = db.get(Result, target) if not record_id else None
    if not rec and not result:
        fail("关联复核对象不存在", 404)
    owner_id = rec.employee_id if rec else result.owner_id
    owner = db.get(Employee, owner_id)
    if initiated:
        require_manager(db, user, owner.department_id)
    elif owner_id != user.id:
        fail("只能对本人的结果提起申诉", 403)
    parent = get_case(db, parent_id) if parent_id else None
    if parent and (parent.owner_id != owner_id or parent.status not in TERMINAL or parent.related_result_id != (result.id if result else None)):
        fail("再次复核必须关联同一对象的已结束案件")
    if parent:
        old = db.get(ReviewCase, parent.id)
        if not old or old.record_id != record_id:
            fail("再次复核对象不一致")
    if not initiated and not parent and target not in {o["resultId"] for o in appeal_options(db, user)}:
        fail("该结果不在申诉期内，或已有处理中案件")
    active_key = f"score:{rec.id}" if rec else f"result:{result.id}"
    if db.scalar(select(ReviewCase.id).where(ReviewCase.active_key == active_key)):
        fail("该对象已有处理中案件")
    if rec:
        snapshot, reviewer = record_snapshot(db, rec)
        db.get(ScoreHead, rec.id).protected = 1
    else:
        snapshot = {"result_id": result.id, "title": result.title, "status": result.status, "version": result.version_no, "description": result.description, "completion_rate": result.completion_rate, "approval_comment": result.approval_comment, "captured_at": _now().isoformat()}
        reviewer = result.approver_id
    case = DisputeCase(id=str(uuid4()), case_no=f"DS{_now():%Y%m%d}{uuid4().hex[:12].upper()}", owner_id=owner_id, department_id=owner.department_id, appeal_title=title.strip(), appeal_reason=reason.strip(), dispute_subject=target, related_result_id=result.id if result else None, result_title=result.title if result else db.get(AssessmentTask, rec.task_id).name, result_status=result.status if result else rec.status, status="SUBMITTED", appeal_status="submitted", package_status="PENDING", package_items=[], deadline_at=_now() + timedelta(days=DEFAULT_POLICY["processing_days"]))
    db.add(case)
    db.flush()
    db.add(ReviewCase(id=case.id, record_id=record_id, snapshot=snapshot, policy=DEFAULT_POLICY.copy(), original_reviewer_id=reviewer, active_key=active_key, parent_case_id=parent_id))
    db.flush()
    case_event(db, case, user, "CREATED", {"target": target, "snapshot_hash": digest(snapshot), "parent_id": parent_id})
    emit(db, f"case:{case.id}:created", "case.created", {"case_id": case.id, "employee_id": owner_id})
    return case


def emit(db, key, topic, payload):
    db.add(ReviewOutbox(event_key=key, topic=topic, payload=payload))


def employee_item(case):
    return {"id": case.id, "appealNo": case.case_no, "title": case.appeal_title, "reason": case.appeal_reason, "status": case.appeal_status, "handleComment": case.decision_comment or "", "handledAt": case.decided_at.isoformat() if case.decided_at else None, "createdAt": case.created_at.isoformat()}


def serialize_case(db, case, user, *, detail=False):
    ext = db.get(ReviewCase, case.id)
    owner = db.get(Employee, case.owner_id)
    item = {"id": case.id, "caseNo": case.case_no, "employeeName": owner.name, "ownerId": owner.id, "departmentId": case.department_id, "title": case.appeal_title, "reason": case.appeal_reason, "status": case.status, "decision": case.decision, "decisionComment": case.decision_comment, "revision": ext.revision if ext else None, "recordId": ext.record_id if ext else None, "deadline": case.deadline_at.isoformat() if case.deadline_at else None, "overdue": bool(case.deadline_at and case.deadline_at < _now() and case.status not in TERMINAL), "canManage": manages(db, user, case.department_id) and user.id != case.owner_id and (not ext or user.id != ext.original_reviewer_id), "isOwner": user.id == case.owner_id}
    if detail:
        item["snapshot"] = ext.snapshot if ext else {}
        item["policy"] = ext.policy if ext else {}
        item["evidence"] = [{"id": a.id, "name": a.name, "digest": a.digest, "uploadedBy": a.uploaded_by, "createdAt": a.created_at.isoformat()} for a in db.scalars(select(ReviewArtifact).where(ReviewArtifact.case_id == case.id)).all()]
        item["events"] = [{"id": e.id, "sequence": e.sequence, "action": e.action, "actorId": e.actor_id, "payload": e.payload, "digest": e.digest, "createdAt": e.created_at.isoformat()} for e in db.scalars(select(ReviewEvent).where(ReviewEvent.case_id == case.id).order_by(ReviewEvent.sequence)).all()]
        item["reports"] = [{"id": r.id, "version": r.version, "content": r.content} for r in db.scalars(select(VerificationReport).where(VerificationReport.case_id == case.id).order_by(VerificationReport.version)).all()]
        item["reviewers"] = [{"userId": r.user_id, "status": r.recusal_status} for r in db.scalars(select(DisputeReviewer).where(DisputeReviewer.case_id == case.id)).all()]
        item["opinions"] = [{"opinion": o.opinion, "comment": o.comment, "version": o.version_no} for o in db.scalars(select(DisputeOpinionRecord).where(DisputeOpinionRecord.case_id == case.id)).all()]
        item["adjustments"] = [adjustment_item(a) for a in db.scalars(select(ScoreAdjustment).where(ScoreAdjustment.case_id == case.id)).all()]
    return item


def act_case(db, user, case, *, action, revision, comment, reviewer_id=None):
    require_view(db, user, case)
    ext = lock_case(db, case.id, revision)
    if not comment.strip():
        fail("处理说明不能为空", 422)
    if action in {"WITHDRAW", "SUPPLEMENT"}:
        if case.owner_id != user.id:
            fail("仅案件本人可以执行", 403)
    elif action not in {"RECUSE", "SUPPORT", "REJECT", "PARTIAL"}:
        require_manager(db, user, case.department_id)
        require_independent(db, user, case)
    if action == "ACCEPT":
        if case.status != "SUBMITTED":
            fail("仅待受理案件可以受理")
        ext.accepted_by = user.id
        case.status = "ACCEPTED"
        # P0-3：受理即自动产出中立核验报告（确定性复算 + 跨周期对比），
        # 让评审人一进来就拿到事实基线，不必人工再点一次。
        # ⚠️ 不在此处调用 generate_report：它内部会再次 lock_case 推进版本，
        #    且快照缺失时抛 HTTPException 会连带回滚「受理」这一步。
        #    故此处只做前置判断，真正的报告生成由调用方在受理后单独触发。
        if ext.snapshot:
            case_event(
                db,
                case,
                user,
                "REPORT_PENDING",
                {"reason": "案件已受理，核验报告待生成", "record_id": ext.record_id},
            )
    elif action == "ASSIGN":
        if case.status not in {"ACCEPTED", "REVIEWING"}:
            fail("请先受理案件")
        reviewer = db.get(Employee, reviewer_id)
        if not reviewer or reviewer.account_status != "enabled" or reviewer.employment_status != "active":
            fail("评审人不存在或不可用", 422)
        require_independent(db, reviewer, case)
        existing = db.scalar(select(DisputeReviewer).where(DisputeReviewer.case_id == case.id, DisputeReviewer.user_id == reviewer.id))
        if existing:
            fail("该人员已被指派或已回避，请选择其他评审人")
        db.add(DisputeReviewer(case_id=case.id, user_id=reviewer.id, source_type="MANUAL", recusal_status="NONE"))
        case.status = "REVIEWING"
    elif action == "RECUSE":
        reviewer = db.scalar(select(DisputeReviewer).where(DisputeReviewer.case_id == case.id, DisputeReviewer.user_id == user.id, DisputeReviewer.recusal_status == "NONE"))
        if not reviewer or case.status in TERMINAL:
            fail("当前不能申请回避")
        reviewer.recusal_status, reviewer.recusal_reason = "RECUSED", comment
    elif action == "REQUEST_SUPPLEMENT":
        if case.status not in {"ACCEPTED", "REVIEWING"}:
            fail("当前不能退回补充")
        case.status = "NEEDS_SUPPLEMENT"
        case.deadline_at = _now() + timedelta(days=ext.policy["supplement_days"])
    elif action == "SUPPLEMENT":
        if case.status != "NEEDS_SUPPLEMENT":
            fail("案件当前不在待补充阶段")
        case.status = "REVIEWING"
        case.deadline_at = _now() + timedelta(days=ext.policy["processing_days"])
    elif action == "WITHDRAW":
        if case.status in TERMINAL:
            fail("已结束案件不能撤回")
        case.status, case.appeal_status, ext.active_key = "WITHDRAWN", "closed", None
    elif action == "NOT_ACCEPTED":
        if case.status != "SUBMITTED":
            fail("仅待受理案件可以不予受理")
        case.status, case.appeal_status, ext.active_key = "NOT_ACCEPTED", "closed", None
        case.decision_comment = comment
    elif action in {"SUPPORT", "REJECT", "PARTIAL"}:
        require_manager(db, user, case.department_id)
        require_independent(db, user, case)
        if case.status not in {"ACCEPTED", "REVIEWING"}:
            fail("当前不能裁决")
        report = db.scalar(select(VerificationReport).where(VerificationReport.case_id == case.id).order_by(VerificationReport.version.desc()).limit(1))
        if not report:
            fail("裁决前必须生成核验报告")
        artifacts = db.scalars(select(ReviewArtifact).where(ReviewArtifact.case_id == case.id)).all()
        if {(a.id, a.digest) for a in artifacts} != {(a["id"], a["digest"]) for a in report.content["evidence"]}:
            fail("证据已补充，请重新生成核验报告后再裁决")
        if any(not a["integrity"] for a in report.content["evidence"]):
            fail("证据完整性异常，禁止裁决")
        opinions = db.scalars(select(DisputeOpinionRecord).join(DisputeReviewer, DisputeReviewer.id == DisputeOpinionRecord.reviewer_id).where(DisputeOpinionRecord.case_id == case.id, DisputeReviewer.recusal_status == "NONE", DisputeOpinionRecord.submitted_at >= report.created_at)).all()
        if not opinions:
            fail("裁决前至少需要一名独立复核人的意见")
        case.status, case.appeal_status = "DECIDED", "resolved"
        case.decision, case.decision_comment = action, comment
        case.decided_by, case.decided_at = user.id, _now()
        ext.active_key = None
    elif action == "ARCHIVE":
        if case.status != "DECIDED":
            fail("仅已裁决案件可归档")
        pending = db.scalar(select(ScoreAdjustment.id).where(ScoreAdjustment.case_id == case.id, ScoreAdjustment.status == "PENDING"))
        if pending:
            fail("仍有待审批调整单，暂不能归档")
        case.status = "ARCHIVED"
    else:
        fail("不支持的案件动作", 422)
    if case.status not in TERMINAL:
        case.appeal_status = "processing" if case.status != "SUBMITTED" else "submitted"
    case_event(db, case, user, action, {"comment": comment, "reviewer_id": reviewer_id})
    emit(db, f"case:{case.id}:{ext.revision}", "case.updated", {"case_id": case.id, "employee_id": case.owner_id, "action": action})
    return case


def auto_generate_report(db, user, case):
    """受理后自动补生成中立核验报告（P0-3 自动化）。

    与 :func:`generate_report` 的区别：本函数**不消费版本号**，
    内部捕获异常而不抛出，因此不会因为历史数据缺快照而让「受理」失败。

    Returns:
        ``(report_or_None, reason)`` —— 报告行（可能为 None）与说明。
    """
    ext = db.get(ReviewCase, case.id)
    if ext is None:
        return None, "历史案件尚未建立复核快照，无法生成核验报告"
    if not ext.snapshot:
        return None, "案件缺少冻结快照，无法生成核验报告"
    existing = db.scalar(
        select(VerificationReport.id)
        .where(VerificationReport.case_id == case.id)
        .order_by(VerificationReport.version.desc())
        .limit(1)
    )
    if existing:
        return None, "该案件已存在核验报告"
    try:
        report = generate_report(db, user, case, ext.revision)
    except HTTPException as exc:
        return None, exc.detail or "核验报告生成失败"
    return report, None


def opinion(db, user, case, *, revision, value, comment):
    require_view(db, user, case)
    require_independent(db, user, case)
    lock_case(db, case.id, revision)
    reviewer = db.scalar(select(DisputeReviewer).where(DisputeReviewer.case_id == case.id, DisputeReviewer.user_id == user.id, DisputeReviewer.recusal_status == "NONE"))
    if not reviewer or case.status != "REVIEWING":
        fail("仅当前指派的评审人可在评审阶段提交意见", 403)
    if value not in {"SUPPORT", "REJECT", "SUPPLEMENT"} or not comment.strip():
        fail("意见类型无效或说明为空", 422)
    version = (db.scalar(select(func.max(DisputeOpinionRecord.version_no)).where(DisputeOpinionRecord.reviewer_id == reviewer.id)) or 0) + 1
    db.add(DisputeOpinionRecord(case_id=case.id, reviewer_id=reviewer.id, opinion=value, comment=comment, version_no=version))
    case_event(db, case, user, "OPINION", {"opinion": value, "comment": comment, "version": version})


def upload(db, user, case, *, revision, name, content):
    require_view(db, user, case)
    if user.id != case.owner_id or case.status not in {"SUBMITTED", "NEEDS_SUPPLEMENT"}:
        fail("仅本人可在待受理或待补充阶段追加证据", 403)
    ext = lock_case(db, case.id, revision)
    if not content or len(content) > ext.policy["max_file_bytes"]:
        fail("附件为空或超过10MB", 422)
    extname = name.rsplit(".", 1)[-1].lower()
    signatures = {"pdf": (b"%PDF-", "application/pdf"), "png": (b"\x89PNG\r\n\x1a\n", "image/png"), "jpg": (b"\xff\xd8\xff", "image/jpeg"), "jpeg": (b"\xff\xd8\xff", "image/jpeg")}
    if extname not in signatures or not content.startswith(signatures[extname][0]):
        fail("仅支持内容格式匹配的PDF、PNG和JPEG文件", 422)
    count = db.scalar(select(func.count()).select_from(ReviewArtifact).where(ReviewArtifact.case_id == case.id))
    if count >= 20:
        fail("每个案件最多20个附件", 422)
    safe_name = name.replace("\\", "/").split("/")[-1].replace("\r", "").replace("\n", "")[:180]
    row = ReviewArtifact(case_id=case.id, name=safe_name, media_type=signatures[extname][1], content=content, digest=hashlib.sha256(content).hexdigest(), uploaded_by=user.id)
    db.add(row)
    db.flush()
    case.package_status = "READY"
    case_event(db, case, user, "EVIDENCE_ADDED", {"id": row.id, "name": safe_name, "digest": row.digest})
    return row


def generate_report(db, user, case, revision):
    require_view(db, user, case)
    require_independent(db, user, case)
    if case.status not in {"ACCEPTED", "REVIEWING"}:
        fail("仅已受理或评审中案件可以生成核验报告")
    ext = lock_case(db, case.id, revision)
    frozen = ext.snapshot
    report = {"scope": case.dispute_subject, "snapshot_hash": digest(frozen), "engine": "deterministic-v1", "ai_status": "未调用外部模型；使用确定性核验报告", "facts": [], "differences": [], "missing": [], "history": [], "limitations": ["历史变化仅是核查线索，不构成评分错误结论", "报告不作裁决，不自动改分"]}
    if ext.record_id:
        rules = frozen["rules"]
        try:
            from . import scoring
            payload = (rules.get("form") or {}).get("scheme_payload", {})
            if payload.get("scoring_mode") == "direct_points":
                from .scheme_points import score_points
                result = score_points(payload, frozen["reviews"])
            else:
                specs = [scoring.MetricSpec(**{**s, "levels": tuple(s["levels"])}) for s in frozen["normalized_specs"]]
                inputs = [scoring.MetricInput(**v) for v in frozen["normalized_inputs"]]
                result = scoring.score_record(specs, inputs)
            report["recomputed"] = result.to_dict()
            report["facts"].append({"type": "RULE_RECALCULATION", "source": rules["id"], "rule_version": rules["version"], "original": frozen["score"]["total"], "recomputed": result.total})
            if abs(result.total - frozen["score"]["total"]) > 0.000001:
                report["differences"].append({"type": "TOTAL_DIFFERENCE", "delta": round(result.total - frozen["score"]["total"], 6)})
            report["missing"].extend(result.warnings)
        except (ValueError, KeyError, TypeError, scoring.ScoringError) as exc:
            report["missing"].append(f"冻结数据无法完成规则复算：{exc}")
        for version in db.scalars(select(ScoreVersion).where(ScoreVersion.record_id == ext.record_id).order_by(ScoreVersion.version)).all():
            report["history"].append({"version": version.version, "total": version.content["total"], "source": version.id, "created_at": version.created_at.isoformat()})
        current = db.scalar(select(Score).where(Score.record_id == ext.record_id))
        if current and digest(score_content(current, db.get(AssessmentRecord, ext.record_id))) != digest(frozen["score"]):
            report["differences"].append({"type": "CURRENT_RESULT_CHANGED", "message": "当前评分已变化，调整时需重新核验最新版本"})
        # ── P0-3 跨周期历史对比（只作线索，不作结论） ──
        history = _cross_period_history(db, ext.record_id, frozen)
        report["cross_period"] = history
        for item in history["items"]:
            report["facts"].append(
                {
                    "type": "CROSS_PERIOD_SCORE",
                    "period": item["period"],
                    "total": item["total"],
                    "rating": item["rating"],
                    "delta_vs_current": item["delta"],
                }
            )
        if history["trend"]:
            report["differences"].append(
                {"type": "CROSS_PERIOD_TREND", "message": history["trend"]}
            )
    else:
        report["facts"].append({"type": "RESULT_SNAPSHOT", "source": frozen.get("result_id"), "status": frozen.get("status"), "version": frozen.get("version")})
        report["missing"].append("成果认定需要人工复核；未关联考核记录，不能自动推导绩效分数")
    evidence = db.scalars(select(ReviewArtifact).where(ReviewArtifact.case_id == case.id)).all()
    report["evidence"] = [{"id": a.id, "name": a.name, "digest": a.digest, "integrity": hashlib.sha256(a.content).hexdigest() == a.digest} for a in evidence]
    version = (db.scalar(select(func.max(VerificationReport.version)).where(VerificationReport.case_id == case.id)) or 0) + 1
    row = VerificationReport(case_id=case.id, version=version, content=report, generated_by=user.id)
    db.add(row)
    db.flush()
    case_event(db, case, user, "REPORT_GENERATED", {"report_id": row.id, "version": version, "digest": digest(report)})
    return row


def _cross_period_history(db, record_id, frozen) -> dict:
    """跨周期分数对比（P0-3）：同一员工此前各周期的得分与趋势。

    仅作核查线索：不同周期方案可能不同，**不构成评分错误结论**。
    """
    rec = db.get(AssessmentRecord, record_id)
    if not rec:
        return {"items": [], "trend": None}
    rows = db.execute(
        select(AssessmentTask.period, Score.total, Score.trace_json)
        .join(AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id)
        .join(Score, Score.record_id == AssessmentRecord.id)
        .where(
            AssessmentRecord.employee_id == rec.employee_id,
            AssessmentRecord.id != record_id,
        )
        .order_by(AssessmentTask.period)
    ).all()
    current_total = float((frozen.get("score") or {}).get("total") or 0.0)
    items = [
        {
            "period": period,
            "total": float(total),
            "rating": (trace or {}).get("scheme_rating"),
            "delta": round(float(total) - current_total, 4),
        }
        for period, total, trace in rows
    ]
    trend = None
    if len(items) >= 2:
        first, last = items[0]["total"], items[-1]["total"]
        if last > first:
            trend = f"历史分数由 {first} 上升至 {last}，当前 {current_total} 需结合本期目标核查"
        elif last < first:
            trend = f"历史分数由 {first} 下降至 {last}，当前 {current_total} 需结合本期目标核查"
    return {"items": items, "trend": trend}


def package(db, user, case):
    require_view(db, user, case)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("case.json", canonical(serialize_case(db, case, user, detail=True)))
        for row in db.scalars(select(ReviewArtifact).where(ReviewArtifact.case_id == case.id)).all():
            if hashlib.sha256(row.content).hexdigest() != row.digest:
                fail("附件完整性校验失败，已阻止导出")
            archive.writestr(f"evidence/{row.id}.{row.name.rsplit('.', 1)[-1]}", row.content)
    return stream.getvalue()


def adjustment_item(row):
    return {"id": row.id, "recordId": row.record_id, "caseId": row.case_id, "meetingId": row.meeting_id, "expectedVersion": row.expected_version, "before": row.before, "proposed": row.proposed, "corrections": row.input_corrections, "reason": row.reason, "status": row.status, "requestedBy": row.requested_by, "approvedBy": row.approved_by, "approvalComment": row.approval_comment, "revision": row.revision}


def adjustment_context(db, user, record_id):
    rec = db.get(AssessmentRecord, record_id)
    if not rec:
        fail("考核记录不存在", 404)
    require_manager(db, user, db.get(Employee, rec.employee_id).department_id)
    from ..domain.scheme_models import SchemeResponse
    response = db.get(SchemeResponse, rec.id)
    task = db.get(AssessmentTask, rec.task_id)
    snap = db.get(ConfigSnapshot, task.snapshot_id) if task.snapshot_id else None
    payload = (snap.form_template_json or {}).get("scheme_payload", {}) if snap else {}
    score = db.scalar(select(Score).where(Score.record_id == rec.id))
    if not response or not payload or not score:
        fail("该记录缺少可调整的方案评价明细")
    version = capture_score(db, rec, score)
    fields = []
    direct = payload.get("scoring_mode") == "direct_points"
    for metric in payload.get("metrics", []):
        if metric.get("scoring_method") == "manager_review":
            current = response.reviews.get(metric["code"], {})
            fields.append({"code": metric["code"], "name": metric["name"], "min": 0, "max": metric.get("max_score", 100) if direct else 100, "score": current.get("score"), "comment": ""})
    document = payload.get("document", {})
    if direct:
        fields.append({"code": "_adjustment", "name": "制度允许的加减分", "min": document.get("adjustment_min", 0), "max": document.get("adjustment_max", 0), "score": response.reviews.get("_adjustment", {}).get("score", 0), "comment": ""})
    return {"record_id": rec.id, "version": version.version, "total": score.total, "fields": fields, "grade": response.reviews.get("_grade", {}).get("level") if direct else None, "grade_options": [g["level"] for g in document.get("grade_rules", [])] if direct else [], "quantitative": [{"code": m["code"], "name": m["name"], "actual": response.metric_values.get(m["code"], {}).get("actual", 0), "target": response.metric_values.get(m["code"], {}).get("target", 1), "comment": ""} for m in payload.get("metrics", []) if m.get("scoring_method") == "ratio"]}


def request_adjustment(db, user, *, case_id, meeting_id, record_id, expected_version, proposed, reason, corrections=None):
    rec = db.get(AssessmentRecord, record_id)
    if not rec:
        fail("考核记录不存在", 404)
    owner = db.get(Employee, rec.employee_id)
    require_manager(db, user, owner.department_id)
    if user.id == owner.id:
        fail("不能为本人提出分数调整", 403)
    if bool(case_id) == bool(meeting_id):
        fail("调整必须且只能关联一个裁决案件或校准会议", 422)
    if case_id:
        case = get_case(db, case_id)
        ext = db.get(ReviewCase, case_id)
        if not ext or ext.record_id != rec.id or case.status != "DECIDED" or case.decision not in {"SUPPORT", "PARTIAL"}:
            fail("调整必须关联同一评分记录且支持申诉的已裁决案件")
    if meeting_id:
        meeting = get_meeting(db, user, meeting_id, manage=True)
        if meeting.status != "CLOSED":
            fail("会议关闭并形成正式决议后才可提出调整")
        resolution = db.scalar(select(CalibrationResolution.id).where(CalibrationResolution.meeting_id == meeting_id, CalibrationResolution.record_id == rec.id, CalibrationResolution.action == "ADJUST"))
        if not resolution:
            fail("该评分尚无会议调整决议")
    if not reason.strip():
        fail("调整理由不能为空", 422)
    score = db.scalar(select(Score).where(Score.record_id == rec.id))
    if not score:
        fail("尚无可调整分数")
    previous = capture_score(db, rec, score)
    if previous.version != expected_version:
        fail("评分版本已变化，请重新核验")
    # 允许修订已存在的人工分项，不允许直接输入总分或修改量化数据。
    from ..domain.scheme_models import SchemeResponse
    response = db.get(SchemeResponse, rec.id)
    task = db.get(AssessmentTask, rec.task_id)
    snap = db.get(ConfigSnapshot, task.snapshot_id)
    payload = (snap.form_template_json or {}).get("scheme_payload", {})
    if not payload or not response:
        fail("此记录尚无可验证的方案人工评价项，需先补全方案或数据纠错适配")
    corrections = corrections or {}
    normalized_corrections = {}
    quantitative = {m["code"] for m in payload.get("metrics", []) if m.get("scoring_method") == "ratio"}
    if not isinstance(corrections, dict) or set(corrections) - quantitative:
        fail("数据纠错只能引用冻结方案的量化指标", 422)
    for code, raw in corrections.items():
        if not isinstance(raw, dict) or not str(raw.get("comment", "")).strip():
            fail("数据纠错必须说明证据与来源", 422)
        actual, target = raw.get("actual"), raw.get("target")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (actual, target)) or actual < 0 or target <= 0:
            fail("纠错实际值须为非负有限数，目标值须大于0", 422)
        normalized_corrections[code] = {"actual": actual, "target": target, "comment": raw["comment"]}
    if not isinstance(proposed, dict) or (not proposed and not normalized_corrections) or set(proposed) - set(response.reviews):
        fail("只能修订本次方案已有人工评价项", 422)
    reviews = copy.deepcopy(response.reviews)
    allowed = {m["code"] for m in payload.get("metrics", []) if m.get("scoring_method") == "manager_review"}
    if payload.get("scoring_mode") == "direct_points":
        allowed |= {"_grade", "_adjustment"}
    if set(proposed) - allowed:
        fail("量化项不能通过人工调整单改分", 422)
    for code, value in proposed.items():
        if not isinstance(value, dict) or not str(value.get("comment", "")).strip():
            fail("每个调整项必须说明依据", 422)
        if code == "_grade":
            if value.get("level") not in {g["level"] for g in payload.get("document", {}).get("grade_rules", [])}:
                fail("等级不属于冻结方案", 422)
            reviews[code] = {"level": value["level"], "comment": value["comment"]}
            continue
        raw = value.get("score")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            fail("分数必须为有限数值", 422)
        lower, upper = 0, 100
        if payload.get("scoring_mode") == "direct_points":
            if code == "_adjustment":
                lower, upper = payload["document"].get("adjustment_min", 0), payload["document"].get("adjustment_max", 0)
            else:
                upper = next(m["max_score"] for m in payload["metrics"] if m["code"] == code)
        if not lower <= raw <= upper:
            fail(f"{code}超出冻结方案评分范围", 422)
        reviews[code] = {"score": raw, "comment": value["comment"]}
    row = ScoreAdjustment(record_id=rec.id, case_id=case_id, meeting_id=meeting_id, expected_version=expected_version, before={"score": previous.content, "reviews": copy.deepcopy(response.reviews), "response_revision": response.revision, "metric_values": copy.deepcopy(response.metric_values)}, proposed=reviews, input_corrections=normalized_corrections, reason=reason, requested_by=user.id)
    db.add(row)
    db.get(ScoreHead, rec.id).protected = 1
    db.flush()
    emit(db, f"adjustment:{row.id}:requested", "adjustment.requested", {"adjustment_id": row.id, "employee_id": rec.employee_id})
    return row


def approve_adjustment(db, user, row, *, revision, approve, comment):
    rec = db.get(AssessmentRecord, row.record_id)
    owner = db.get(Employee, rec.employee_id)
    require_manager(db, user, owner.department_id)
    if user.id in {row.requested_by, owner.id}:
        fail("调整申请人和被评员工不能审批该调整", 403)
    if row.case_id:
        require_independent(db, user, get_case(db, row.case_id))
    if row.status != "PENDING" or not comment.strip():
        fail("调整已处理或审批说明为空")
    changed = db.execute(update(ScoreAdjustment).where(ScoreAdjustment.id == row.id, ScoreAdjustment.revision == revision, ScoreAdjustment.status == "PENDING").values(revision=revision + 1, status="EXECUTING"), execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        fail("调整单已变化，请刷新")
    db.refresh(row)
    row.approved_by, row.approval_comment = user.id, comment
    if not approve:
        row.status = "REJECTED"
        return row
    from ..domain.scheme_models import SchemeResponse
    from . import review_service
    response = db.get(SchemeResponse, rec.id)
    if response.revision != row.before["response_revision"] or response.reviews != row.before["reviews"] or response.metric_values != row.before.get("metric_values", response.metric_values):
        fail("原人工评价已变化，必须重新核验")
    head = db.get(ScoreHead, rec.id)
    if head.version != row.expected_version:
        fail("原评分版本已变化，必须重新核验")
    score = db.scalar(select(Score).where(Score.record_id == rec.id))
    if score_content(score, rec) != row.before["score"]:
        fail("当前评分与申请快照不一致，已阻止调整")
    corrected_values = copy.deepcopy(response.metric_values)
    for code, correction in row.input_corrections.items():
        corrected_values[code] = {"actual": correction["actual"], "target": correction["target"]}
    changed = db.execute(update(SchemeResponse).where(SchemeResponse.record_id == rec.id, SchemeResponse.revision == response.revision).values(reviews=row.proposed, metric_values=corrected_values, revision=response.revision + 1, updated_at=_now()), execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        fail("评价正在更新，请重新核验")
    db.refresh(response)
    result = review_service.compute_score(db, record_id=rec.id)
    trace = copy.deepcopy(result.trace)
    task = db.get(AssessmentTask, rec.task_id)
    snap = db.get(ConfigSnapshot, task.snapshot_id)
    payload = (snap.form_template_json or {})["scheme_payload"]
    if payload.get("scoring_mode") == "direct_points":
        rating = row.proposed["_grade"]["level"]
        trace["grade_basis"] = row.proposed["_grade"]
    else:
        grade = next((g for g in payload["grades"] if g["min_score"] <= result.total and (result.total < g["max_score"] or result.total == g["max_score"] == 100)), None)
        if not grade:
            fail("调整后分数未命中冻结等级区间")
        rating = grade["level"]
    trace["scheme_rating"] = rating
    trace["adjustment_id"] = row.id
    content = {"total": result.total, "per_metric": result.per_metric, "trace": trace, "rating": rating}
    version = append_score_version(db, rec, content, actor_id=user.id, reason=row.reason, source_id=row.id, expected=row.expected_version, protect=True)
    score.total, score.per_metric_json, score.trace_json, score.computed_at = result.total, result.per_metric, trace, _now()
    rec.confirmed_rating = rating
    row.status, row.executed_at = "EXECUTED", _now()
    emit(db, f"score:{rec.id}:v{version.version}", "score.adjusted", {"record_id": rec.id, "version": version.version, "adjustment_id": row.id, "employee_id": rec.employee_id, "period": task.period})
    if row.case_id:
        case = get_case(db, row.case_id)
        ext = db.get(ReviewCase, case.id)
        lock_case(db, case.id, ext.revision)
        case_event(db, case, user, "SCORE_ADJUSTED", {"adjustment_id": row.id, "version": version.version})
    return row


def department_stats(db, user, period, department_ids):
    ctx = context(db, user)
    if not department_ids or ("hr" not in ctx.roles and set(department_ids) - ctx.managed_department_ids):
        fail("无权查看所选部门校准数据", 403)
    rows = db.execute(select(AssessmentRecord, Score, Employee, AssessmentTask).join(Score, Score.record_id == AssessmentRecord.id).join(Employee, Employee.id == AssessmentRecord.employee_id).join(AssessmentTask, AssessmentTask.id == AssessmentRecord.task_id).where(AssessmentTask.period == period, Employee.department_id.in_(department_ids))).all()
    groups, records = {}, []
    for rec, score, emp, task in rows:
        # 同岗位且同冻结方案才构成比较组；小样本仅显示人数。
        snap = db.get(ConfigSnapshot, task.snapshot_id) if task.snapshot_id else None
        scheme = (snap.form_template_json or {}).get("scheme_version_id") if snap else None
        key = (emp.department_id, emp.position or "未设岗位", scheme or task.snapshot_id or task.id)
        groups.setdefault(key, []).append(score.total)
        records.append({"record_id": rec.id, "employee_id": emp.id, "employee_name": emp.name, "department_id": emp.department_id, "position": emp.position, "rule_group": key[2], "total": score.total, "rating": (score.trace_json or {}).get("scheme_rating") or rec.confirmed_rating})
    distributions, alerts = [], []
    for (dept, position, rules), values in groups.items():
        small = len(values) < 5
        distributions.append({"department_id": dept, "position": position, "rule_group": rules, "count": len(values), "average": None if small else round(statistics.mean(values), 4), "median": None if small else statistics.median(values), "small_sample": small})
        if not small and max(values) - min(values) >= 30:
            alerts.append({"department_id": dept, "position": position, "rule_group": rules, "type": "LARGE_SPREAD", "message": "同岗位同规则组分差较大，请结合目标与业绩核查；不能直接据此调整"})
    return {"period": period, "distributions": distributions, "alerts": alerts, "records": records, "captured_at": _now().isoformat(), "limitations": ["不足5人的组不展示均值和中位数", "不同岗位、规则版本不直接比较", "异常仅作为核查线索，不强制绩效分布"]}


def get_meeting(db, user, meeting_id, manage=False):
    meeting = db.get(CalibrationMeeting, meeting_id)
    if not meeting:
        fail("校准会议不存在", 404)
    ctx = context(db, user)
    allowed = "hr" in ctx.roles or ("department_leader" in ctx.roles and set(meeting.department_ids) <= ctx.managed_department_ids)
    if not allowed and (manage or user.id not in meeting.participants):
        fail("无权访问校准会议", 403)
    return meeting


def create_meeting(db, user, *, title, period, departments, participants):
    stats = department_stats(db, user, period, departments)
    if not title.strip():
        fail("会议标题不能为空", 422)
    if user.id not in participants:
        participants = [*participants, user.id]
    for employee_id in participants:
        participant = db.get(Employee, employee_id)
        if not participant or participant.account_status != "enabled" or participant.employment_status != "active":
            fail("参会人不存在或不可用", 422)
        if not all(manages(db, participant, d) for d in departments):
            fail("参会人必须具备所有会议部门的数据管理权限；跨部门会议请指派HR", 403)
    meeting = CalibrationMeeting(title=title, period=period, department_ids=departments, participants=participants, snapshot=stats, created_by=user.id)
    db.add(meeting)
    db.flush()
    return meeting


def resolve_meeting(db, user, meeting, *, revision, record_id, action, basis, owner_id, due_at):
    get_meeting(db, user, meeting.id, manage=True)
    if meeting.status != "OPEN" or action not in {"KEEP", "REVIEW", "ADJUST", "POLICY"} or not basis.strip():
        fail("会议状态、决议动作或依据无效", 422)
    if not db.get(Employee, owner_id) or due_at < _now():
        fail("责任人不存在或期限已过", 422)
    rec = db.get(AssessmentRecord, record_id) if record_id else None
    if action != "POLICY" and not rec:
        fail("该决议必须关联评分记录", 422)
    if rec:
        emp = db.get(Employee, rec.employee_id)
        if rec.id not in {r["record_id"] for r in meeting.snapshot["records"]}:
            fail("评分记录不在本次会议快照中", 403)
        if user.id == emp.id:
            fail("不能为本人形成校准决议", 403)
    changed = db.execute(update(CalibrationMeeting).where(CalibrationMeeting.id == meeting.id, CalibrationMeeting.revision == revision, CalibrationMeeting.status == "OPEN").values(revision=revision + 1), execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        fail("会议已更新，请刷新")
    linked = None
    if action == "REVIEW":
        linked = create_case(db, user, target=f"score:{record_id}", title=f"校准复核：{meeting.title}", reason=basis, initiated=True).id
    row = CalibrationResolution(meeting_id=meeting.id, record_id=record_id, action=action, basis=basis, owner_id=owner_id, due_at=due_at, content={"case_id": linked}, created_by=user.id)
    db.add(row)
    db.flush()
    db.refresh(meeting)
    return row


def close_meeting(db, user, meeting, revision):
    get_meeting(db, user, meeting.id, manage=True)
    if not db.scalar(select(CalibrationResolution.id).where(CalibrationResolution.meeting_id == meeting.id)):
        fail("请先形成会议决议")
    changed = db.execute(update(CalibrationMeeting).where(CalibrationMeeting.id == meeting.id, CalibrationMeeting.revision == revision, CalibrationMeeting.status == "OPEN").values(status="CLOSED", closed_at=_now(), revision=revision + 1), execution_options={"synchronize_session": False})
    if changed.rowcount != 1:
        fail("会议状态已变化")
    db.refresh(meeting)


def integrity(db, user, case):
    require_view(db, user, case)
    issues, previous = [], ""
    events = db.scalars(select(ReviewEvent).where(ReviewEvent.case_id == case.id).order_by(ReviewEvent.sequence)).all()
    for index, e in enumerate(events, 1):
        body = {"case_id": e.case_id, "sequence": e.sequence, "actor_id": e.actor_id, "action": e.action, "payload": e.payload, "previous_hash": e.previous_hash, "created_at": e.created_at.isoformat()}
        if e.sequence != index or e.previous_hash != previous or digest(body) != e.digest:
            issues.append(f"事件链异常：{e.id}")
        previous = e.digest
    for a in db.scalars(select(ReviewArtifact).where(ReviewArtifact.case_id == case.id)).all():
        if hashlib.sha256(a.content).hexdigest() != a.digest:
            issues.append(f"附件摘要异常：{a.id}")
    ext = db.get(ReviewCase, case.id)
    if events and ext and events[0].payload.get("snapshot_hash") != digest(ext.snapshot):
        issues.append("案件快照摘要异常")
    return {"ok": not issues, "issues": issues, "event_count": len(events), "head_hash": previous, "guarantee": "应用层只追加及本地哈希校验；独立存证尚需部署，不能证明数据库管理员未同时改写整条链"}
