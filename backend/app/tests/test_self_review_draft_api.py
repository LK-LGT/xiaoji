"""员工自评草稿落库（2026-11-27）。

原来「保存草稿」只提示“仅保留在当前页面” ⇒ 刷新即丢；
现在 PUT /v1/assessment/records/{id}/self-review/draft 真正写入
``assessment_record.self_review_content``，且不改记录状态。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import AssessmentRecord, AssessmentTask, Base, Employee
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def records(db):
    db.add_all(
        [
            Employee(
                id="E_ME", name="我", employment_status="active",
                account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_OTHER", name="别人", employment_status="active",
                account_status="enabled", token_version=1,
            ),
            AssessmentTask(
                id="T1", name="考核", period="2026-11", status="draft",
            ),
            AssessmentRecord(
                id="R1", task_id="T1", employee_id="E_ME", status="not_started",
            ),
            AssessmentRecord(
                id="R2", task_id="T1", employee_id="E_OTHER", status="not_started",
            ),
        ]
    )
    db.commit()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _headers(employee_id: str = "E_ME") -> dict:
    token, _ = _generate_token(employee_id, "u", 1)
    return {"Authorization": f"Bearer {token}"}


def test_保存草稿真的落库且不改状态(client, db, records):
    resp = client.put(
        "/v1/assessment/records/R1/self-review/draft",
        json={
            "self_score": 5,
            "self_comment": "【业绩总结】草稿内容",
            "answers": [
                {"field_key": "performance_summary", "text": "草稿内容"},
                {"field_key": "challenges", "text": "挑战草稿"},
            ],
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    record = db.get(AssessmentRecord, "R1")
    assert record.self_review_content["self_score"] == 5
    assert record.self_review_content["performance_summary"] == "草稿内容"
    assert record.self_review_content["challenges"] == "挑战草稿"
    assert record.status == "not_started"


def test_不能保存别人的草稿(client, records):
    resp = client.put(
        "/v1/assessment/records/R2/self-review/draft",
        json={"self_score": 4, "self_comment": "越权"},
        headers=_headers(),
    )
    assert resp.status_code == 403, resp.text


def test_已提交后不能再保存草稿(client, db, records):
    record = db.get(AssessmentRecord, "R1")
    record.status = "self_review_submitted"
    db.commit()
    resp = client.put(
        "/v1/assessment/records/R1/self-review/draft",
        json={"self_score": 4, "self_comment": "太晚"},
        headers=_headers(),
    )
    assert resp.status_code == 409, resp.text
