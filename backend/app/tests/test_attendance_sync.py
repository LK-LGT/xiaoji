"""阶段二：考勤与证据域测试。

覆盖：
1. 飞书打卡结果客户端（分片、日期窗口校验、错误处理）。
2. 六类异常识别规则。
3. 考勤同步落库幂等性。
4. reference_only 证据生成与幂等重建。
5. **红线**：考勤数据严禁进入 MetricValue 算分集。
6. 考勤统计聚合与调整分桥接。
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.enums import AttendanceAnomalyType, EvidenceUsage
from backend.app.domain.models import (
    AttendanceRecord,
    Base,
    Employee,
    Evidence,
    MetricValue,
)
from backend.app.services import attendance_stats, evidence_service
from backend.app.services.attendance_sync import (
    AttendanceSyncService,
    classify_record,
    period_of,
)
from backend.app.services.feishu_client import FeishuClient, FeishuError, MockTransport

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def employee(db):
    emp = Employee(
        id="e1",
        name="张三",
        employment_status="active",
        account_status="enabled",
    )
    db.add(emp)
    db.flush()
    return emp


def token_response(token: str = "token-1"):
    return {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": token, "expire": 7200},
    }


def attendance_response(tasks: list[dict]) -> dict:
    return {
        "status_code": 200,
        "json": {"code": 0, "data": {"user_task_results": tasks}},
    }


def make_task(
    user_id: str = "e1",
    day: int = 20261103,
    check_in: str = "Normal",
    check_out: str = "Normal",
    task_shift_type: int = 0,
    is_field: bool = False,
    result_id: str = "r-1",
) -> dict:
    check_in_record = {
        "record_id": "cir-1",
        "check_time": "1793498400",
        "location_name": "园区A栋",
        "is_field": is_field,
        "is_wifi": False,
        "type": 0,
    }
    check_out_record = {
        "record_id": "cor-1",
        "check_time": "1793538000",
        "location_name": "园区A栋",
        "is_field": is_field,
        "is_wifi": False,
        "type": 0,
    }
    return {
        "day": day,
        "employee_name": "张三",
        "group_id": "g-1",
        "shift_id": "s-1",
        "result_id": result_id,
        "user_id": user_id,
        "records": [
            {
                "check_in_record_id": "cir-1",
                "check_in_result": check_in,
                "check_in_result_supplement": "None",
                "check_in_shift_time": "1793498400",
                "check_in_record": check_in_record,
                "check_out_record_id": "cor-1",
                "check_out_result": check_out,
                "check_out_result_supplement": "None",
                "check_out_shift_time": "1793538000",
                "check_out_record": check_out_record,
                "task_shift_type": task_shift_type,
            }
        ],
    }


def build_client(tasks: list[dict]) -> FeishuClient:
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "POST /attendance/v1/user_tasks/query": [attendance_response(tasks)],
        }
    )
    return FeishuClient("app", "secret", transport=transport, sleep=lambda _: None)


# ---------------------------------------------------------------------------
# 1. 飞书客户端
# ---------------------------------------------------------------------------


def test_fetch_attendance_tasks_chunks_users_by_50(db):
    """超过 50 人时自动分片。"""
    tasks = [make_task(user_id=f"e{i}") for i in range(120)]
    client = build_client(tasks)
    result = client.fetch_attendance_tasks(
        [f"e{i}" for i in range(120)], "20261101", "20261114"
    )
    # MockTransport 对同一路径返回固定响应，120 人 → 3 次请求
    assert len(result) == 3 * len(tasks)


def test_fetch_attendance_tasks_rejects_date_span_over_30_days(db):
    client = build_client([])
    with pytest.raises(ValueError, match="30"):
        client.fetch_attendance_tasks(["e1"], "20261001", "20261114")


def test_fetch_attendance_tasks_rejects_reversed_dates(db):
    client = build_client([])
    with pytest.raises(ValueError, match="不能早于"):
        client.fetch_attendance_tasks(["e1"], "20261114", "20261101")


def test_fetch_attendance_tasks_sends_employee_id_and_dates(db):
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "POST /attendance/v1/user_tasks/query": [attendance_response([])],
        }
    )
    client = FeishuClient("app", "secret", transport=transport, sleep=lambda _: None)
    client.fetch_attendance_tasks(["e1"], "20261101", "20261114")
    req = transport.calls[-1]
    assert "employee_type=employee_id" in str(req.url)
    body = json.loads(req.content.decode())
    assert body["check_date_from"] == 20261101
    assert body["check_date_to"] == 20261114
    assert body["user_ids"] == ["e1"]
    assert body["need_overtime_result"] is True


def test_fetch_attendance_tasks_returns_empty_for_no_users(db):
    client = build_client([])
    assert client.fetch_attendance_tasks([], "20261101", "20261114") == []


def test_feishu_error_is_raised_for_business_failure(db):
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "POST /attendance/v1/user_tasks/query": [
                {"status_code": 200, "json": {"code": 1220001, "msg": "区间过大"}}
            ],
        }
    )
    client = FeishuClient("app", "secret", transport=transport, sleep=lambda _: None)
    with pytest.raises(FeishuError):
        client.fetch_attendance_tasks(["e1"], "20261101", "20261114")


# ---------------------------------------------------------------------------
# 2. 六类异常识别
# ---------------------------------------------------------------------------


def test_classify_late():
    assert "late" in classify_record({"check_in_result": "Late"})


def test_classify_serious_late_is_late():
    assert classify_record({"check_in_result": "SeriousLate"}) == ["late"]


def test_classify_early_leave():
    assert "early_leave" in classify_record({"check_out_result": "Early"})


def test_classify_missing_punch():
    assert "missing_punch" in classify_record({"check_in_result": "Lack"})
    assert "missing_punch" in classify_record({"check_out_result": "Lack"})


def test_classify_no_need_check_is_leave():
    assert "leave" in classify_record({"check_in_result": "NoNeedCheck"})


def test_classify_overtime_shift():
    assert "overtime" in classify_record({"task_shift_type": 1})


def test_classify_outside_from_is_field():
    record = {"check_in_record": {"is_field": True}}
    assert "outside" in classify_record(record)


def test_classify_outside_from_check_out_record():
    record = {"check_out_record": {"is_field": True}}
    assert "outside" in classify_record(record)


def test_classify_normal_has_no_anomaly():
    assert (
        classify_record({"check_in_result": "Normal", "check_out_result": "Normal"})
        == []
    )


def test_classify_todo_is_not_anomaly():
    """尚未打卡的未来班次不计异常。"""
    assert (
        classify_record({"check_in_result": "Todo", "check_out_result": "Todo"}) == []
    )


def test_classify_deduplicates_multiple_signals():
    record = {
        "check_in_result": "Late",
        "check_out_result": "Lack",
        "task_shift_type": 1,
    }
    anomalies = classify_record(record)
    assert len(anomalies) == len(set(anomalies))
    assert set(anomalies) == {"late", "missing_punch", "overtime"}


def test_period_of():
    assert period_of(date(2026, 11, 3)) == "2026-11"


# ---------------------------------------------------------------------------
# 3. 同步落库
# ---------------------------------------------------------------------------


def test_sync_persists_attendance_record(db, employee):
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    report = service.sync(date(2026, 11, 3), date(2026, 11, 3))

    assert report.inserted == 1
    assert report.updated == 0
    assert report.task_count == 1

    row = db.execute(select(AttendanceRecord)).scalar_one()
    assert row.employee_id == "e1"
    assert row.work_date == date(2026, 11, 3)
    assert row.check_in_result == "Late"
    assert "late" in row.anomaly_types
    assert row.as_of is not None


def test_sync_is_idempotent(db, employee):
    """重复同步同一区间不产生重复记录。"""
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)

    service.sync(date(2026, 11, 3), date(2026, 11, 3))
    second = service.sync(date(2026, 11, 3), date(2026, 11, 3))

    assert second.inserted == 0
    assert second.updated == 1
    count = len(list(db.execute(select(AttendanceRecord)).scalars().all()))
    assert count == 1


def test_sync_records_anomaly_counts(db, employee):
    client = build_client([make_task(check_in="Late", check_out="Early")])
    service = AttendanceSyncService(db, client)
    report = service.sync(date(2026, 11, 3), date(2026, 11, 3))

    assert report.anomaly_counts["late"] == 1
    assert report.anomaly_counts["early_leave"] == 1


def test_sync_handles_missing_employee_gracefully(db):
    """飞书返回本地不存在的员工时跳过，不抛异常。"""
    client = build_client([make_task(user_id="ghost")])
    service = AttendanceSyncService(db, client)
    report = service.sync(date(2026, 11, 3), date(2026, 11, 3))
    assert report.errors == []


# ---------------------------------------------------------------------------
# 4. 证据生成（reference_only）
# ---------------------------------------------------------------------------


def test_sync_generates_reference_only_evidence(db, employee):
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    rows = list(db.execute(select(Evidence)).scalars().all())
    assert len(rows) == 1
    ev = rows[0]
    assert ev.usage == EvidenceUsage.REFERENCE_ONLY.value
    assert ev.source == "attendance"
    assert ev.anomaly_type == AttendanceAnomalyType.LATE.value
    assert ev.anomaly_count == 1
    assert ev.employee_id == "e1"


def test_evidence_generation_is_idempotent(db, employee):
    """重复同步不重复累加证据。"""
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    rows = list(db.execute(select(Evidence)).scalars().all())
    assert len(rows) == 1
    assert rows[0].anomaly_count == 1


def test_evidence_aggregates_same_type_within_period(db, employee):
    """同周期同类型的多次异常合并成一条证据，count 累加。"""
    tasks = [
        make_task(day=20261103, check_in="Late", result_id="r1"),
        make_task(day=20261104, check_in="Late", result_id="r2"),
        make_task(day=20261105, check_in="Late", result_id="r3"),
    ]
    client = build_client(tasks)
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 5))

    rows = list(db.execute(select(Evidence)).scalars().all())
    assert len(rows) == 1
    assert rows[0].anomaly_count == 3


def test_evidence_separates_different_types(db, employee):
    tasks = [
        make_task(day=20261103, check_in="Late", result_id="r1"),
        make_task(day=20261104, check_out="Early", result_id="r2"),
    ]
    client = build_client(tasks)
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 4))

    rows = list(db.execute(select(Evidence)).scalars().all())
    assert len(rows) == 2
    types = {r.anomaly_type for r in rows}
    assert types == {"late", "early_leave"}


def test_evidence_separates_different_periods(db, employee):
    tasks = [
        make_task(day=20261030, check_in="Late", result_id="r1"),
        make_task(day=20261103, check_in="Late", result_id="r2"),
    ]
    client = build_client(tasks)
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 10, 30), date(2026, 11, 3))

    rows = list(db.execute(select(Evidence)).scalars().all())
    assert len(rows) == 2
    periods = {r.source_ref.split("|")[1] for r in rows}
    assert periods == {"2026-10", "2026-11"}


def test_leave_evidence_marked_approved(db, employee):
    client = build_client(
        [make_task(check_in="NoNeedCheck", check_out="NoNeedCheck")]
    )
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    rows = list(db.execute(select(Evidence)).scalars().all())
    assert len(rows) == 1
    assert rows[0].anomaly_type == "leave"
    assert rows[0].approved_flag is True


def test_evidence_carries_disclaimer(db, employee):
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    ev = db.execute(select(Evidence)).scalar_one()
    assert "不自动扣分" in ev.summary
    assert ev.impact == "迟到异常仅供参考，不自动扣分"


def test_source_ref_includes_employee_id(db, employee):
    """source_ref 必须包含员工 ID，避免跨员工误删。"""
    other = Employee(
        id="e2",
        name="李四",
        employment_status="active",
        account_status="enabled",
    )
    db.add(other)
    db.flush()

    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    ev = db.execute(select(Evidence)).scalar_one()
    assert ev.source_ref == "e1|2026-11|attendance|late"


def test_evidence_rebuild_does_not_delete_other_employees(db, employee):
    """重建某员工的证据时，不得删掉其他员工的证据。"""
    other = Employee(
        id="e2",
        name="李四",
        employment_status="active",
        account_status="enabled",
    )
    db.add(other)
    db.flush()

    # 先为 e2 生成一条证据
    db.add(
        Evidence(
            employee_id="e2",
            kind="attendance",
            summary="迟到 1 次",
            source="attendance",
            source_ref="e2|2026-11|attendance|late",
            usage=EvidenceUsage.REFERENCE_ONLY.value,
            anomaly_type="late",
            anomaly_count=1,
        )
    )
    db.flush()

    # 再为 e1 同步并生成证据
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    rows = list(db.execute(select(Evidence)).scalars().all())
    by_employee = {r.employee_id for r in rows}
    assert by_employee == {"e1", "e2"}


# ---------------------------------------------------------------------------
# 5. 红线：考勤严禁进入算分集
# ---------------------------------------------------------------------------


def test_attendance_source_rejected_from_metric_value(db, employee):
    """考勤来源写入 MetricValue 必须被拒绝。"""
    with pytest.raises(evidence_service.AttendanceEvidenceRejected):
        evidence_service.add_metric_value(
            db,
            employee_id="e1",
            metric_code="attendance_days",
            period="2026-11",
            value=1.0,
            source="attendance",
        )


def test_no_metric_value_created_after_sync(db, employee):
    """同步考勤后，算分集必须为空。"""
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    metrics = list(db.execute(select(MetricValue)).scalars().all())
    assert metrics == []


def test_non_attendance_source_allowed(db, employee):
    """非考勤来源仍可正常进入算分集（未被误伤）。"""
    mv = evidence_service.add_metric_value(
        db,
        employee_id="e1",
        metric_code="delivery_score",
        period="2026-11",
        value=88.0,
        source="manual_upload",
    )
    assert mv.value == 88.0
    assert len(list(db.execute(select(MetricValue)).scalars().all())) == 1


def test_build_attendance_view_marks_reference_only(db, employee):
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    rows = list(db.execute(select(Evidence)).scalars().all())
    view = evidence_service.build_attendance_view(rows)
    assert len(view) == 1
    assert view[0]["type"] == "late"
    assert view[0]["disclaimer"] == evidence_service.ATTENDANCE_DISCLAIMER


def test_split_by_usage_separates_reference(db, employee):
    client = build_client([make_task(check_in="Late")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))
    evidence_service.add_metric_value(
        db,
        employee_id="e1",
        metric_code="kpi",
        period="2026-11",
        value=90.0,
        source="manual_upload",
    )
    db.add(
        Evidence(
            employee_id="e1",
            kind="manual_upload",
            summary="项目交付材料",
            source="manual_upload",
            usage=EvidenceUsage.SCORING.value,
        )
    )
    db.flush()

    rows = list(db.execute(select(Evidence)).scalars().all())
    scoring, reference = evidence_service.split_by_usage(rows)
    assert len(reference) == 1
    assert len(scoring) == 1


# ---------------------------------------------------------------------------
# 6. 统计聚合与调整分桥接
# ---------------------------------------------------------------------------


def test_statistics_counts_anomaly_days(db, employee):
    tasks = [
        make_task(day=20261103, check_in="Late", result_id="r1"),
        make_task(day=20261104, check_in="Late", result_id="r2"),
        make_task(day=20261105, check_in="Normal", result_id="r3"),
    ]
    client = build_client(tasks)
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 5))

    stats_service = attendance_stats.AttendanceStatisticsService(db)
    summary = stats_service.summarize("e1", date(2026, 11, 3), date(2026, 11, 5))

    assert summary.total_records == 3
    assert summary.anomaly_counts["late"] == 2
    assert summary.anomaly_days["late"] == 2
    assert summary.absent_days == 2
    assert summary.period == "2026-11"


def test_statistics_excludes_leave_from_absent_days(db, employee):
    """请假是已审批的正当事由，不计入缺勤天数。"""
    client = build_client(
        [make_task(check_in="NoNeedCheck", check_out="NoNeedCheck")]
    )
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    stats_service = attendance_stats.AttendanceStatisticsService(db)
    summary = stats_service.summarize("e1", date(2026, 11, 3), date(2026, 11, 3))

    assert summary.anomaly_counts.get("leave") == 1
    assert summary.absent_days == 0


def test_statistics_splits_by_month_for_cross_month_range(db, employee):
    """跨月区间必须按自然月拆分，且 period 标签体现跨月。"""
    tasks = [
        make_task(day=20261030, check_in="Late", result_id="r1"),
        make_task(day=20261103, check_in="Lack", result_id="r2"),
    ]
    client = build_client(tasks)
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 10, 30), date(2026, 11, 3))

    stats_service = attendance_stats.AttendanceStatisticsService(db)
    summary = stats_service.summarize("e1", date(2026, 10, 30), date(2026, 11, 3))

    assert summary.period == "2026-10~2026-11"
    assert set(summary.by_period) == {"2026-10", "2026-11"}
    assert summary.by_period["2026-10"]["anomaly_counts"]["late"] == 1
    assert summary.by_period["2026-11"]["anomaly_counts"]["missing_punch"] == 1
    assert summary.by_period["2026-10"]["absent_days"] == 1
    assert summary.by_period["2026-11"]["absent_days"] == 1


def test_adjustment_uses_progressive_weekly_rule(db, employee):
    """5 天异常 → 5 × -2 = -10 分。"""
    tasks = [
        make_task(day=20261102 + d, check_in="Lack", result_id=f"r{d}")
        for d in range(5)
    ]
    client = build_client(tasks)
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 2), date(2026, 11, 6))

    stats_service = attendance_stats.AttendanceStatisticsService(db)
    summary = stats_service.summarize("e1", date(2026, 11, 2), date(2026, 11, 6))
    context = attendance_stats.build_adjustment_context(summary)

    assert summary.absent_days == 5
    assert context["suggested_adjustment"] == -10.0
    assert context["usage"] == "reference_only"


def test_adjustment_is_zero_when_no_anomaly(db, employee):
    client = build_client([make_task()])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    stats_service = attendance_stats.AttendanceStatisticsService(db)
    summary = stats_service.summarize("e1", date(2026, 11, 3), date(2026, 11, 3))
    context = attendance_stats.build_adjustment_context(summary)

    assert context["suggested_adjustment"] == 0.0


def test_adjustment_context_never_writes_metric_value(db, employee):
    """调整上下文只是建议，不产生任何算分集记录。"""
    client = build_client([make_task(check_in="Lack")])
    service = AttendanceSyncService(db, client)
    service.sync(date(2026, 11, 3), date(2026, 11, 3))

    stats_service = attendance_stats.AttendanceStatisticsService(db)
    summary = stats_service.summarize("e1", date(2026, 11, 3), date(2026, 11, 3))
    attendance_stats.build_adjustment_context(summary)

    assert list(db.execute(select(MetricValue)).scalars().all()) == []


def test_calculate_attendance_adjustment_matches_rules_engine(db):
    """与 rules_engine 的阶段递进口径保持一致。"""
    from backend.app.services.rules_engine import ScoreCalculator

    for days in (0, 1, 5, 7, 10, 14, 15, 20):
        ours = attendance_stats.calculate_attendance_adjustment(days)
        theirs = ScoreCalculator.calculate_attendance_adjustment(days)
        assert ours == theirs, f"{days} 天时口径不一致：{ours} != {theirs}"
