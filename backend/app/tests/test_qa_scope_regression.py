"""QA 聚合范围和主管查询参数回归。"""

import json
from datetime import datetime
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.app import schemas
from backend.app.domain.models import (
    AssessmentRecord, AssessmentTask, ConfigSnapshot, Department, Employee, RatingDraft, Score,
)
from backend.app.main import query_assessment_data
from backend.app.services import qa_service


@pytest.fixture
def result_record(db):
    employee = Employee(id="QE", name="查询员工")
    task = AssessmentTask(id="QT", name="测试考核", period="202611")
    db.add_all([employee, task])
    db.flush()
    record = AssessmentRecord(id="QR", employee_id=employee.id, task_id=task.id, status="not_started")
    db.add(record)
    db.flush()
    return employee, task, record


def add_result_snapshot(db, task):
    snapshot = ConfigSnapshot(
        task_id=task.id,
        metrics_json=[{"code": "A", "name": "交付质量"}, {"code": "B", "name": "协作"}],
        weights_json=[{"metric_code": "A", "weight": 0.7}, {"metric_code": "B", "weight": 0.3}],
        rubric_json=[],
    )
    db.add(snapshot)
    db.flush()
    task.snapshot_id = snapshot.id
    return snapshot


def test_self_result_without_score(db, result_record):
    employee, task, _ = result_record
    result = query_assessment_data(schemas.QAQueryRequest(
        query_type="self_result", target_employee_id=employee.id, task_id=task.id,
    ), employee, db)
    assert result["data"]["status"] == "pending"
    assert result["data"]["confirmed_rating"] is None
    assert result["data"]["score"] == {}
    assert result["data_quality"]["completeness"] == 0


def test_self_result_weighted_score_and_real_confirmation(db, result_record):
    employee, task, record = result_record
    add_result_snapshot(db, task)
    record.status, record.confirmed_rating = "confirmed", "A"
    at = datetime(2026, 11, 19, 10)
    db.add(Score(record_id=record.id, total=84, per_metric_json=[
        {"metric_code": "A", "level_score": 90, "normalized_weight": 0.7, "contribution": 63},
        {"metric_code": "B", "level_score": 70, "normalized_weight": 0.3, "contribution": 21},
    ], trace_json={"total_weight": 1, "participating_weight": 1}))
    db.add(RatingDraft(record_id=record.id, ratings_json=[], status="confirmed", confirmed_by="manager", confirmed_at=at))
    db.flush()
    result = query_assessment_data(schemas.QAQueryRequest(
        query_type="self_result", target_employee_id=employee.id, task_id=task.id,
    ), employee, db)
    data = result["data"]
    assert [m["display_name"] for m in data["per_metric"]] == ["交付质量", "协作"]
    assert [m["weight"] for m in data["per_metric"]] == [0.7, 0.3]
    assert sum(m["contribution"] for m in data["per_metric"]) == data["score"]["total"]
    assert data["confirmed_by"] == "manager" and data["confirmed_at"] == at.isoformat()
    assert data["appeal_deadline"] is None
    assert result["data_quality"] == data["score"]["data_quality"]
    assert result["data_quality"]["completeness"] == 1


@pytest.mark.parametrize("has_snapshot", [False, True])
def test_historical_result_does_not_invent_configuration(db, result_record, has_snapshot):
    employee, task, record = result_record
    if has_snapshot:
        add_result_snapshot(db, task)
    record.status = "manager_review_submitted"
    db.add(Score(record_id=record.id, total=90, per_metric_json=json.dumps({"A": 90}), trace_json="{}"))
    db.flush()
    result = qa_service.query_self_result(employee.id, task.id, db)
    assert result.score["total"] == 90
    assert result.confirmed_at is None and result.confirmed_by is None
    assert result.score["data_quality"]["degraded"]
    assert result.score["data_quality"]["completeness"] == (0.7 if has_snapshot else 0)
    assert result.per_metric[0].weight == (1 if has_snapshot else 0)


def test_direct_points_are_not_weighted_again(db, result_record):
    employee, task, record = result_record
    add_result_snapshot(db, task)
    record.status = "confirmed"
    db.add(Score(record_id=record.id, total=85, per_metric_json=[
        {"metric_code": "A", "contribution": 60}, {"metric_code": "B", "contribution": 30},
        {"metric_code": "_adjustment", "contribution": -5},
    ], trace_json={"rule": "scheme_direct_points_v1"}))
    db.flush()
    result = qa_service.query_self_result(employee.id, task.id, db)
    assert sum(item.contribution for item in result.per_metric) == 85
    assert result.per_metric[-1].display_name == "加减分"
    assert result.score["data_quality"]["completeness"] == 1


def test_self_result_cannot_query_another_employee(db, result_record):
    employee, task, _ = result_record
    with pytest.raises(HTTPException) as denied:
        query_assessment_data(schemas.QAQueryRequest(
            query_type="self_result", target_employee_id=employee.id, task_id=task.id,
        ), Employee(id="OTHER", name="其他员工"), db)
    assert denied.value.status_code == 403


def test_colleague_query_passes_identity_string(db, monkeypatch):
    from backend.app import dependencies

    employee = Employee(id="M", name="主管")
    check = Mock()
    monkeypatch.setattr(dependencies, "check_can_view_score", check)
    monkeypatch.setattr(qa_service, "query_colleague_result", lambda *args: Mock(model_dump=lambda: {}))
    query_assessment_data(
        schemas.QAQueryRequest(query_type="colleague_result", target_employee_id="E", task_id="T"),
        employee, db,
    )
    check.assert_called_once_with("M", "E", db)


def test_department_query_blocks_cross_department_even_for_direct_report(db, monkeypatch):
    db.add_all([Department(id="A", name="A"), Department(id="B", name="B")])
    db.flush()
    manager = Employee(id="M", name="主管", department_id="A", employment_status="active")
    target = Employee(id="E", name="下属", department_id="B", manager_id="M", employment_status="active")
    db.add_all([manager, target])
    db.flush()
    query = Mock(return_value=Mock(model_dump=lambda: {}))
    monkeypatch.setattr(qa_service, "query_department_summary", query)
    with pytest.raises(HTTPException) as denied:
        query_assessment_data(
            schemas.QAQueryRequest(query_type="peer_performance_distribution", target_employee_id="E", task_id="T"),
            manager, db,
        )
    assert denied.value.status_code == 403
    query.assert_not_called()
    query_assessment_data(
        schemas.QAQueryRequest(query_type="peer_performance_distribution", target_employee_id="M", task_id="T"),
        manager, db,
    )
    query.assert_called_once_with("A", "T", db)
