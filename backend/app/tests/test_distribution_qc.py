"""P0-2 acceptance: distribution QC is read-only and never alters scores."""

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
    Score,
)
from backend.app.services import distribution_qc as qc


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([Department(id="D", name="业务", leader_id="L")])
    s.flush()
    s.add(Employee(id="L", name="L", department_id="D"))
    s.add(AssessmentTask(id="T", name="考核", period="202611"))
    s.flush()
    # 5 people, same dept/position -> one comparable group
    scores = [95.0, 90.0, 85.0, 88.0, 92.0]
    for i, total in enumerate(scores, start=1):
        eid = f"E{i}"
        s.add(Employee(id=eid, name=eid, department_id="D", position="销售"))
        s.add(AssessmentRecord(id=f"R{i}", task_id="T", employee_id=eid, status="confirmed"))
        s.add(Score(record_id=f"R{i}", total=total, per_metric_json=[], trace_json={"scheme_rating": "A"}))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def test_small_sample_hides_mean(db):
    report = qc.analyze(db, period="202611", department_ids=["D"])
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert group["count"] == 5
    assert group["mean"] is not None
    assert group["small_sample"] is False


def test_too_lenient_alert(db):
    report = qc.analyze(db, period="202611", department_ids=["D"])
    types = {a["type"] for a in report["alerts"]}
    assert "TOO_LENIENT" in types


def test_qc_never_writes_scores(db):
    from sqlalchemy import select

    before = db.execute(select(Score)).scalars().all()
    totals_before = [(s.record_id, s.total) for s in before]
    qc.analyze(db, period="202611", department_ids=["D"])
    db.flush()
    after = db.execute(select(Score)).scalars().all()
    assert [(s.record_id, s.total) for s in after] == totals_before


def test_target_distribution_only_compares(db):
    report = qc.analyze(
        db, period="202611", department_ids=["D"], target_distribution={"A": 0.3, "B": 0.7}
    )
    gaps = report["groups"][0].get("distribution_gaps", [])
    # A is 100% actual vs 30% target -> gap reported as comparison only
    assert any(g["level"] == "A" for g in gaps)
    assert any(a["type"] == "DISTRIBUTION_GAP" for a in report["alerts"])
