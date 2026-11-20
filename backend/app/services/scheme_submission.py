"""Shared scheme response submission for web and Feishu entry points."""

from sqlalchemy import update

from ..domain.enums import SubmitSource
from ..domain.models import AssessmentRecord, AssessmentTask, ConfigSnapshot, Employee
from ..domain.scheme_models import SchemeResponse
from . import review_service
from . import scheme_service as service
from .access_control import build_access_context, can_access_employee


def response_context(db, user, record_id):
    record = db.get(AssessmentRecord, record_id)
    if not record:
        service.fail("考核记录不存在", 404)
    employee = db.get(Employee, record.employee_id)
    ctx = build_access_context(db, user)
    if not can_access_employee(ctx, employee, db):
        service.fail("无权查看该考核记录", 403)
    task = db.get(AssessmentTask, record.task_id)
    snapshot = db.get(ConfigSnapshot, task.snapshot_id) if task.snapshot_id else None
    form = snapshot.form_template_json if snapshot else {}
    if not form or not form.get("scheme_version_id"):
        service.fail("该任务未绑定HR方案", 409)
    return record, employee, ctx, form, db.get(SchemeResponse, record_id)


def submit_response(db, user, record_id, body, *, source=SubmitSource.H5):
    # Serialize the first insert as well as status/revision validation across channels.
    db.execute(
        update(AssessmentRecord)
        .where(AssessmentRecord.id == record_id)
        .values(status=AssessmentRecord.status)
    )
    db.expire_all()
    record, employee, ctx, form, response = response_context(db, user, record_id)
    p = form["scheme_payload"]
    own = record.employee_id == user.id
    if record.status in ("confirmed", "archived", "manager_review_submitted", "score_computed"):
        service.fail("当前考核记录已锁定", 409)
    if body.revision != (response.revision if response else 0):
        service.fail("填报内容已变化，请刷新", 409)
    if response is None:
        response = SchemeResponse(record_id=record_id)
        db.add(response)
        db.flush()
    if own:
        if record.submit_locked or record.status == "self_review_submitted":
            service.fail("自评已提交，不能覆盖", 409)
        fields = [f for f in p["fields"] if f["stage"] != "manager_review"]
        allowed = {f["key"] for f in fields} | {
            f"evidence_{m['code']}" for m in p["metrics"] if m.get("evidence_required")
        }
        if set(body.values) - allowed:
            service.fail("存在未配置或主管专用字段")
        for f in fields:
            raw = body.values.get(f["key"])
            if f.get("required") and (raw is None or raw == "" or raw == []):
                service.fail(f"请填写{f['label']}")
            if f["type"] == "number" and raw not in (None, ""):
                try:
                    service.number(raw, f["label"])
                except ValueError as exc:
                    service.fail(str(exc))
        normalized = {}
        for m in p["metrics"]:
            if m["scoring_method"] == "ratio":
                raw = body.metric_values.get(m["code"], {})
                try:
                    actual = service.number(raw.get("actual"), "实际值")
                    target = service.number(raw.get("target"), "目标值")
                except ValueError as exc:
                    service.fail(str(exc))
                if actual < 0 or target <= 0:
                    service.fail("实际值不得为负，目标值必须大于0")
                normalized[m["code"]] = {"actual": actual, "target": target}
            if m.get("evidence_required") and not body.values.get(f"evidence_{m['code']}"):
                service.fail(f"指标{m['name']}需提供成果证据，请配置并填写证据字段")
        self_scores = getattr(body, "self_scores", {})
        normalized_scores = {}
        if self_scores and set(self_scores) != {m['code'] for m in p['metrics']}:
            service.fail('请完成全部指标自评分')
        if set(self_scores) - {m['code'] for m in p['metrics']}:
            service.fail("自评分引用未知指标")
        for metric in p['metrics']:
            raw = self_scores.get(metric['code'])
            if raw is None:
                continue  # Older web/Feishu clients remain compatible.
            try:
                value = service.number(raw, '自评分')
            except ValueError as exc:
                service.fail(str(exc))
            maximum = metric.get('max_score', 100) if p.get('scoring_mode') == 'direct_points' else 100
            if not 0 <= value <= maximum:
                service.fail(f"{metric['name']}自评分应为0至{maximum}")
            normalized_scores[metric['code']] = value
        response.values = {**body.values, **({'_self_scores': normalized_scores} if normalized_scores else {})}
        response.metric_values = normalized
        review_service.submit_self_review(
            db, record_id=record_id, answers=body.values, attachments=[], source=source
        )
    else:
        if not ("department_leader" in ctx.roles or "manager" in ctx.roles):
            service.fail("仅授权主管可以逐项评价", 403)
        if record.status != "self_review_submitted":
            service.fail("请等待员工完成填报", 409)
        # P0-1 互评回收门禁：已指派未回收完成时阻塞主管评分，避免跳过互评直接定分。
        # 互评仅供参考（永不写入 Score），但流程上必须先回收或由 HR/主管移除指派。
        from sqlalchemy import select as _select

        from ..domain.scheme_models import PeerReview as _PeerReview

        _peers = db.scalars(
            _select(_PeerReview).where(_PeerReview.record_id == record_id)
        ).all()
        _pending = [r for r in _peers if r.status != "submitted"]
        if _pending:
            service.fail(
                f"互评尚未回收完成（已提交 {len(_peers) - len(_pending)}/{len(_peers)}），"
                "请等待互评提交或移除无需评价的互评人后再评分",
                409,
            )
        manual = {m["code"] for m in p["metrics"] if m["scoring_method"] == "manager_review"}
        direct = p.get("scoring_mode") == "direct_points"
        if direct:
            manual |= {"_adjustment", "_grade"}
        if set(body.reviews) != manual:
            service.fail("请完成全部人工评价指标，且不能修改量化指标分数")
        reviews = {}
        for code, raw in body.reviews.items():
            if direct and code == "_grade":
                levels = {g["level"] for g in p["document"]["grade_rules"]}
                if raw.get("level") not in levels or not str(raw.get("comment", "")).strip():
                    service.fail("请选择本方案原文等级并说明判定依据")
                reviews[code] = {"level": raw["level"], "comment": raw["comment"]}
                continue
            try:
                score = service.number(raw.get("score"), "主管评分")
            except ValueError as exc:
                service.fail(str(exc))
            lower, upper = 0, 100
            if direct:
                if code == "_adjustment":
                    lower, upper = (
                        p["document"].get("adjustment_min", 0),
                        p["document"].get("adjustment_max", 0),
                    )
                else:
                    upper = next(m["max_score"] for m in p["metrics"] if m["code"] == code)
            if not lower <= score <= upper or not str(raw.get("comment", "")).strip():
                service.fail(f"评分应为{lower}至{upper}，评价说明必填")
            reviews[code] = {"score": score, "comment": raw["comment"]}
        response.reviews = reviews
        response.reviewer_id = user.id
        db.flush()
        result = review_service.compute_score(db, record_id=record_id)
        persisted = review_service.persist_score(db, record_id=record_id, result=result)
        if direct:
            persisted.trace_json = {
                **persisted.trace_json,
                "scheme_rating": reviews["_grade"]["level"],
                "grade_basis": reviews["_grade"],
            }
        else:
            grade = next(
                g
                for g in p["grades"]
                if g["min_score"] <= result.total
                and (result.total < g["max_score"] or result.total == g["max_score"] == 100)
            )
            persisted.trace_json = {**persisted.trace_json, "scheme_rating": grade["level"]}
        from .review_center import capture_score
        capture_score(db, record, persisted, actor_id=user.id, reason="方案人工评价及等级确认")
        import copy
        state = copy.deepcopy(response.values.get('_workflow', {}))
        state.update(phase='awaiting_interview')
        response.values = {**response.values, '_workflow': state}
        record.status = "manager_review_submitted"
        from .notification_service import enqueue_safe
        enqueue_safe(db, employee_id=user.id, scene='N11', period=record.task_id, object_id=record_id,
                     object_type='assessment_record', label='评分已完成，请记录绩效面谈', event_key=f'review_{response.revision}')
        service.audit(db, user, "review", record_id, total=result.total)
    response.revision += 1
    response.updated_at = service._now()
    return {"record_id": record_id, "status": record.status}
