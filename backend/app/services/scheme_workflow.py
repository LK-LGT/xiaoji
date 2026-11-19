"""Versioned, audited completion workflow for frozen scheme assessments."""
import copy

from sqlalchemy import select, update

from ..domain.models import AssessmentRecord, AssessmentTask, Score
from . import scheme_service as service
from .notification_service import enqueue_safe
from .scheme_submission import response_context


def workflow(response):
    return copy.deepcopy((response.values or {}).get("_workflow", {})) if response else {}


def transition(db, user, record_id, body):
    db.execute(update(AssessmentRecord).where(AssessmentRecord.id == record_id).values(status=AssessmentRecord.status))
    db.expire_all()
    record, employee, ctx, _, response = response_context(db, user, record_id)
    if not response or body.revision != response.revision:
        service.fail("记录已变化，请刷新后重试", 409)
    state = workflow(response)
    phase = state.get("phase", "awaiting_interview" if record.status == "manager_review_submitted" else "filling")
    action = body.action
    own = user.id == employee.id
    manager = not own and bool({"department_leader", "manager"} & ctx.roles)
    is_hr = "hr" in ctx.roles
    note = body.note.strip()
    if not note:
        service.fail("请填写操作说明或面谈结论")
    if record.status == "archived":
        service.fail("记录已归档，不可修改", 409)
    target = None
    if action == "interview":
        if not manager:
            service.fail("仅授权主管可记录面谈", 403)
        if record.status != "manager_review_submitted" or phase not in ("awaiting_interview", "disputed"):
            service.fail("当前阶段不能记录面谈", 409)
        state.update(phase="awaiting_employee", interview=note, interview_by=user.id)
        target = employee.id
    elif action in ("acknowledge", "dispute"):
        if not own:
            service.fail("仅员工本人可确认或提出异议", 403)
        if phase != "awaiting_employee":
            service.fail("请等待主管记录面谈", 409)
        state.update(phase="awaiting_hr" if action == "acknowledge" else "disputed", employee_note=note)
        target = db.get(AssessmentTask, record.task_id).owner_id if action == "acknowledge" else response.reviewer_id
    elif action == "reopen":
        from ..domain.review_models import ScoreHead
        head = db.get(ScoreHead, record_id)
        if head and head.protected:
            service.fail("该结果已进入复核保护，请通过复核中心处理和调整", 409)
        if not is_hr:
            service.fail("仅HR可退回重新评分", 403)
        if phase not in ("disputed", "awaiting_hr") or record.status != "manager_review_submitted":
            service.fail("当前阶段不能退回", 409)
        state.update(phase="reviewing", reopen_reason=note)
        record.status = "self_review_submitted"
        # Keep prior score in history, remove the obsolete result from active reports.
        score = db.scalar(select(Score).where(Score.record_id == record_id))
        state.setdefault("previous_results", []).append({"total": score.total, "trace": score.trace_json, "reviews": copy.deepcopy(response.reviews)} if score else {})
        if score:
            db.delete(score)
        record.confirmed_rating = None
        target = response.reviewer_id
    elif action == "finalize":
        if not is_hr:
            service.fail("仅HR可定稿", 403)
        if phase != "awaiting_hr" or record.status != "manager_review_submitted":
            service.fail("须先完成面谈及员工确认，异议须处理后才能定稿", 409)
        score = db.scalar(select(Score).where(Score.record_id == record_id))
        if not score or not (score.trace_json or {}).get("scheme_rating"):
            service.fail("缺少有效分数或等级", 409)
        rating = score.trace_json["scheme_rating"]
        if len(rating) > 8:
            service.fail("最终等级编码不可超过8个字符，请先调整方案等级编码")
        record.confirmed_rating = rating
        record.status = "confirmed"
        state.update(phase="finalized", finalized_by=user.id, final_note=note)
        target = employee.id
    elif action == "archive":
        if not is_hr:
            service.fail("仅HR可归档", 403)
        if phase != "finalized" or record.status != "confirmed":
            service.fail("仅已定稿记录可归档", 409)
        record.status = "archived"
        state.update(phase="archived", archive_note=note)
        target = employee.id
    else:
        service.fail("不支持的流程操作")
    state.setdefault("history", []).append({"action": action, "actor_id": user.id, "note": note, "at": service._now().isoformat()})
    response.values = {**response.values, "_workflow": state}
    response.revision += 1
    response.updated_at = service._now()
    service.audit(db, user, action, record_id, note=note, phase=state["phase"])
    task = db.get(AssessmentTask, record.task_id)
    if action in ("finalize", "archive"):
        db.flush()
        records = db.scalars(select(AssessmentRecord).where(AssessmentRecord.task_id == task.id)).all()
        if all(r.status == "archived" for r in records):
            task.status = "archived"
        elif all(r.status in ("confirmed", "archived") for r in records):
            task.status = "confirmed"
    if action == 'finalize':
        from .notification_service import record_event
        record_event(db, record, 'N8', event_key=str(response.revision))
    if target:
        enqueue_safe(db, employee_id=target, scene="N11", period=task.id, object_id=record_id,
                     object_type="assessment_record", label=f"{task.name}：{note}", event_key=str(response.revision))
    return {"record_id": record_id, "status": record.status, "phase": state["phase"], "revision": response.revision}
