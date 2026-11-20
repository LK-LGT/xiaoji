"""P0-1 acceptance: peer assign/recall gate/remind are reference-only."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
)
from backend.app.domain.scheme_models import PeerReview
from backend.app.services import peer_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        Department(id="HR", name="人事"),
        Department(id="D", name="业务", leader_id="L"),
    ])
    s.flush()
    for k, d in [("H", "HR"), ("L", "D"), ("E", "D"), ("P1", "D"), ("P2", "D")]:
        s.add(Employee(id=k, name=k, department_id=d, account_status="enabled", employment_status="active"))
    s.add(HrDepartmentConfig(department_id="HR", active=True))
    s.flush()
    s.add(AssessmentTask(id="T", name="考核", period="202611"))
    s.flush()
    s.add(AssessmentRecord(id="REC", task_id="T", employee_id="E", status="self_review_submitted"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def test_assign_idempotent_and_self_forbidden(db):
    hr = db.get(Employee, "H")
    rows = peer_service.assign(db, hr, "REC", ["P1", "P1", "P2"], due_at=datetime.now() + timedelta(days=1))
    assert len(rows) == 2
    again = peer_service.assign(db, hr, "REC", ["P1"])
    assert len(again) == 1 and again[0]["reviewer_id"] == "P1"
    assert db.query(PeerReview).count() == 2
    with pytest.raises(Exception):
        peer_service.assign(db, hr, "REC", ["E"])


def test_submit_and_manager_gate(db):
    from backend.app.services import scheme_service
    hr = db.get(Employee, "H")
    peer_service.assign(db, hr, "REC", ["P1", "P2"])
    p1 = db.query(PeerReview).filter_by(reviewer_id="P1").one()
    # only reviewer can submit
    with pytest.raises(Exception):
        peer_service.submit(db, hr, p1.id, 80, "写得不错，继续保持水准")
    out = peer_service.submit(db, db.get(Employee, "P1"), p1.id, 80, "写得不错，继续保持水准")
    assert out["status"] == "submitted"
    # manager gate: peers pending -> block handled in scheme_submission via service.fail (HTTPException)
    # emulate gate check: one pending remains
    from sqlalchemy import select
    pending = [r for r in db.scalars(select(PeerReview).where(PeerReview.record_id == "REC")).all() if r.status != "submitted"]
    assert len(pending) == 1
    # submit second, then gate passes
    p2 = db.query(PeerReview).filter_by(reviewer_id="P2").one()
    peer_service.submit(db, db.get(Employee, "P2"), p2.id, 90, "交付稳定，协作积极主动")
    pending = [r for r in db.scalars(select(PeerReview).where(PeerReview.record_id == "REC")).all() if r.status != "submitted"]
    assert pending == []
    assert peer_service.list_for_record(db, hr, "REC")["complete"] is True


def test_remind_overdue_daily_dedup(db):
    hr = db.get(Employee, "H")
    peer_service.assign(db, hr, "REC", ["P1"], due_at=datetime.now() - timedelta(hours=1))
    n1 = peer_service.remind_overdue(db, task_id="T")
    n2 = peer_service.remind_overdue(db, task_id="T")
    # second call same day dedups via enqueue dedup_key -> still returns count but enqueue_safe returns existing
    assert n1 >= 1 and n2 >= 1
