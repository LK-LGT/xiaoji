"""Peer review distribution & collection (P0-1).

- Only HR / department_leader / manager can assign peers.
- Reviewer must be active employee, cannot review self.
- Scores/comments are reference-only, never enter Score.
- Idempotent on (record_id, reviewer_id).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AssessmentRecord, AssessmentTask, Employee, _now
from ..domain.scheme_models import PeerReview
from . import scheme_service as service
from .access_control import build_access_context
from .notification_service import enqueue_safe


def _check_assign_perm(db: Session, user: Employee, record: AssessmentRecord) -> None:
    ctx = build_access_context(db, user)
    if "hr" in ctx.roles:
        return
    if bool({"department_leader", "manager"} & ctx.roles):
        return
    service.fail("仅HR/部门负责人/主管可指派互评人", 403)


def assign(db: Session, user: Employee, record_id: str, reviewer_ids: list[str], due_at=None) -> list[dict]:
    record = db.get(AssessmentRecord, record_id)
    if not record:
        service.fail("考核记录不存在", 404)
    if record.status in ("confirmed", "archived"):
        service.fail("记录已定稿归档，不能再指派互评", 409)
    _check_assign_perm(db, user, record)
    # dedupe, preserve order
    seen: list[str] = []
    for rid in reviewer_ids or []:
        rid = str(rid or "").strip()
        if rid and rid not in seen:
            seen.append(rid)
    if not seen:
        service.fail("互评人不能为空", 422)
    if len(seen) > 20:
        service.fail("单条记录互评人不超过20人", 422)
    task = db.get(AssessmentTask, record.task_id)
    out: list[dict] = []
    for reviewer_id in seen:
        reviewer = db.get(Employee, reviewer_id)
        if not reviewer or reviewer.employment_status != "active" or reviewer.account_status != "enabled":
            service.fail(f"互评人 {reviewer_id} 不存在或不可用", 422)
        if reviewer_id == record.employee_id:
            service.fail("不能指派本人互评", 422)
        existing = db.scalar(
            select(PeerReview).where(
                PeerReview.record_id == record_id, PeerReview.reviewer_id == reviewer_id
            )
        )
        if existing:
            if due_at and not existing.submitted_at:
                existing.due_at = due_at
            out.append(serialize(existing))
            continue
        row = PeerReview(
            record_id=record_id,
            task_id=record.task_id,
            reviewee_id=record.employee_id,
            reviewer_id=reviewer_id,
            status="assigned",
            due_at=due_at,
            assigned_by=user.id,
        )
        db.add(row)
        db.flush()
        service.audit(db, user, "peer_assign", record_id, reviewer_id=reviewer_id)
        enqueue_safe(
            db, employee_id=reviewer_id, scene="N12", period=record.task_id,
            object_id=record_id, object_type="assessment_record",
            label=f"{task.name if task else '考核'}：请完成对同事的互评",
            event_key=f"peer:{record_id}:{reviewer_id}",
            due_at=due_at,
        )
        out.append(serialize(row))
    return out


def serialize(row: PeerReview) -> dict:
    return {
        "id": row.id,
        "record_id": row.record_id,
        "reviewee_id": row.reviewee_id,
        "reviewer_id": row.reviewer_id,
        "status": row.status,
        "score": row.score,
        "comment": row.comment,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
    }


def list_for_record(db: Session, user: Employee, record_id: str) -> dict:
    from ..domain.models import Employee as _Employee
    from .access_control import build_access_context, can_access_employee

    record = db.get(AssessmentRecord, record_id)
    if not record:
        service.fail("考核记录不存在", 404)
    employee = db.get(_Employee, record.employee_id)
    ctx = build_access_context(db, user)
    if not can_access_employee(ctx, employee, db):
        service.fail("无权查看该考核记录", 403)
    rows = db.scalars(select(PeerReview).where(PeerReview.record_id == record_id)).all()
    own = record.employee_id == user.id
    is_manager = bool({"department_leader", "manager", "hr"} & ctx.roles)
    items: list[dict] = []
    for r in rows:
        # Reviewer sees own item + others' completion status (no scores); employee sees only progress counts.
        if user.id == r.reviewer_id or is_manager:
            items.append(serialize(r))
        else:
            items.append({
                "id": r.id, "record_id": r.record_id, "reviewee_id": r.reviewee_id,
                "reviewer_id": r.reviewer_id, "status": r.status,
                "score": None, "comment": None,
                "due_at": r.due_at.isoformat() if r.due_at else None,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            })
    total = len(rows)
    done = sum(1 for r in rows if r.status == "submitted")
    return {
        "record_id": record_id, "total": total, "submitted": done,
        "pending": total - done,
        "complete": total > 0 and done == total,
        "items": items,
        "can_review": not own and is_manager,
    }


def submit(db: Session, user: Employee, peer_id: str, score: float | None, comment: str) -> dict:
    row = db.get(PeerReview, peer_id)
    if not row:
        service.fail("互评任务不存在", 404)
    if row.reviewer_id != user.id:
        service.fail("仅互评人本人可提交", 403)
    if row.status == "submitted":
        return serialize(row)
    record = db.get(AssessmentRecord, row.record_id)
    if record and record.status in ("confirmed", "archived"):
        service.fail("记录已定稿归档，不能再提交互评", 409)
    comment = (comment or "").strip()
    if not comment or len(comment) < 5:
        service.fail("互评意见至少5个字", 422)
    if len(comment) > 2000:
        service.fail("互评意见不超过2000字", 422)
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            service.fail("互评分必须是数值", 422)
        if not 0 <= score <= 100:
            service.fail("互评分应在0至100之间", 422)
    row.score = score
    row.comment = comment
    row.status = "submitted"
    row.submitted_at = _now()
    db.flush()
    service.audit(db, user, "peer_submit", row.record_id, peer_id=peer_id)
    return serialize(row)


def progress_for_task(db: Session, task_id: str) -> list[dict]:
    """主管回收进度：按被评人聚合。"""
    rows = db.scalars(select(PeerReview).where(PeerReview.task_id == task_id)).all()
    by_record: dict[str, dict] = {}
    for r in rows:
        agg = by_record.setdefault(r.record_id, {"record_id": r.record_id, "total": 0, "submitted": 0, "overdue": 0})
        agg["total"] += 1
        if r.status == "submitted":
            agg["submitted"] += 1
        elif r.due_at and r.due_at < datetime.now() and r.status != "submitted":
            agg["overdue"] += 1
    for agg in by_record.values():
        agg["pending"] = agg["total"] - agg["submitted"]
    return list(by_record.values())


def remind_overdue(db: Session, *, task_id: str | None = None, now: datetime | None = None) -> int:
    """逾期催办：对已过 due_at 未提交的互评发每日一次 N13。每日频控靠 event_key=日期。"""
    now = now or datetime.now()
    stmt = select(PeerReview).where(PeerReview.status != "submitted", PeerReview.due_at.is_not(None), PeerReview.due_at < now)
    if task_id:
        stmt = stmt.where(PeerReview.task_id == task_id)
    rows = db.scalars(stmt).all()
    sent = 0
    day = now.date().isoformat()
    for r in rows:
        record = db.get(AssessmentRecord, r.record_id)
        if not record or record.status in ("confirmed", "archived"):
            continue
        reviewer = db.get(Employee, r.reviewer_id)
        if not reviewer or reviewer.employment_status != "active" or reviewer.account_status != "enabled":
            continue
        task = db.get(AssessmentTask, r.task_id)
        row = enqueue_safe(
            db, employee_id=r.reviewer_id, scene="N13", period=r.task_id,
            object_id=r.record_id, object_type="assessment_record",
            label=f"{task.name if task else '考核'}：互评已逾期，请尽快提交",
            event_key=f"peer-overdue:{r.id}:{day}",
            due_at=r.due_at,
        )
        if row is not None:
            sent += 1
    return sent
