"""BUG-R6-26：周期格式统一（``YYYYMM`` 为权威格式）回归测试。

## 背景

系统历史上并存两种周期格式：

| 来源 | 格式 | 示例 |
|---|---|---|
| ``AssessmentTask.period`` | 紧凑 ``YYYYMM`` | ``202611`` |
| ``MonthPlan.plan_month`` | 带横杠 ``YYYY-MM`` | ``2026-11`` |
| ``MetricValue.period`` | **混用**（历史脏数据） | ``2026-11`` / ``209906`` |

混用的致命后果：算分链路里 ``MetricValue.period == period`` 直接字符串比较，
``202611`` 永远匹配不到 ``2026-11`` 的行 —— **不报错，只是指标凭空消失**。

## 本文件锁定的契约

1. ``normalize_period`` 是**唯一**归一化入口，输出紧凑 ``YYYYMM``；
2. ``MetricValue.period`` 落库前必须归一化；
3. 拿周期去比对 ``MonthPlan.plan_month`` 时必须用 ``period_match_keys`` 给出全部候选写法；
4. 成果证据的周期过滤**格式无关**。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import Base, MetricValue
from backend.app.services.period import (
    normalize_period,
    period_match_keys,
    to_dashed_period,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def org(db):
    """组织结构：`E_LEAD` 管理 `E_A`。"""
    from backend.app.domain.models import Department, Employee

    dept = Department(id="D_ROOT", name="业务部", active=True)
    db.add(dept)
    db.flush()
    lead = Employee(
        id="E_LEAD",
        name="主管",
        department_id="D_ROOT",
        employment_status="active",
        account_status="enabled",
    )
    emp_a = Employee(
        id="E_A",
        name="员工甲",
        department_id="D_ROOT",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([lead, emp_a])
    db.flush()
    emp_a.manager_id = "E_LEAD"
    dept.leader_id = "E_LEAD"
    db.flush()
    return {"lead": lead, "a": emp_a}


# ---------------------------------------------------------------------------
# 1. normalize_period —— 唯一归一化入口
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("202611", "202611"),
        ("2026-11", "202611"),
        ("2026-11-01", "202611"),
        ("  202611  ", "202611"),
        ("202601", "202601"),
        ("2026-01", "202601"),
    ],
)
def test_normalize_period_accepts_all_supported_formats(raw, expected):
    assert normalize_period(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", None, "abc", "2026", "202613", "202600", "2026-13", "26-11", "2026111"],
)
def test_normalize_period_rejects_invalid_input(raw):
    """非法输入必须返回 None —— **不能**抛错，也不能静默产出垃圾串。"""
    assert normalize_period(raw) is None


def test_to_dashed_period():
    assert to_dashed_period("202611") == "2026-11"
    assert to_dashed_period("2026-11") == "2026-11"
    assert to_dashed_period("bad") is None


# ---------------------------------------------------------------------------
# 2. period_match_keys —— 跨格式 SQL 比对
# ---------------------------------------------------------------------------


def test_period_match_keys_returns_all_writings():
    """``period_match_keys`` 必须同时给出紧凑与带横杠两种写法。

    只给一种 → 查另一张表的周期列时静默查空。
    """
    keys = period_match_keys("202611")
    assert "202611" in keys
    assert "2026-11" in keys
    assert len(keys) == 2

    # 带横杠入参也必须给出同样的两个候选
    assert set(period_match_keys("2026-11")) == set(keys)


def test_period_match_keys_rejects_invalid():
    assert period_match_keys("bad") == ()
    assert period_match_keys(None) == ()
    assert period_match_keys("202613") == ()


# ---------------------------------------------------------------------------
# 3. MetricValue 落库前归一化
# ---------------------------------------------------------------------------


def test_add_metric_value_normalizes_period(db, org):
    """写入指标实测值时，周期必须被归一化成紧凑 ``YYYYMM``。

    这是 BUG-R6-26 的**核心修复点** —— 不归一化的话，
    算分时的 ``MetricValue.period == period`` 会静默失配。
    """
    from backend.app.services import evidence_service

    evidence_service.add_metric_value(
        db,
        employee_id="E_A",
        metric_code="PLAN_EXECUTION_RATE",
        value=88.0,
        period="2026-11",  # 带横杠入参
        source="PLAN_RESULT",
    )
    db.flush()

    row = db.query(MetricValue).filter(MetricValue.employee_id == "E_A").one()
    assert row.period == "202611", "MetricValue.period 必须落成紧凑格式"


def test_add_metric_value_is_idempotent_across_formats(db, org):
    """同一指标用两种格式写入，必须落到**同一行**（不能写出两条脏数据）。"""
    from backend.app.services import evidence_service

    evidence_service.add_metric_value(
        db,
        employee_id="E_A",
        metric_code="PLAN_EXECUTION_RATE",
        value=88.0,
        period="2026-11",
        source="PLAN_RESULT",
    )
    evidence_service.add_metric_value(
        db,
        employee_id="E_A",
        metric_code="PLAN_EXECUTION_RATE",
        value=92.0,
        period="202611",
        source="PLAN_RESULT",
    )
    db.flush()

    rows = db.query(MetricValue).filter(MetricValue.employee_id == "E_A").all()
    assert len(rows) == 1, f"跨格式重复写入不应产生多行，实际 {len(rows)} 行"
    assert rows[0].value == pytest.approx(92.0)


# ---------------------------------------------------------------------------
# 4. 成果证据周期过滤 —— 格式无关
# ---------------------------------------------------------------------------


def test_plan_result_evidence_period_filter_is_format_agnostic(db, org):
    """按周期过滤成果证据时，紧凑与带横杠入参都要能查到同一批数据。"""
    from datetime import date

    from backend.app.domain.models import MonthPlan, MonthPlanItem
    from backend.app.domain.result_models import (
        RESULT_PLAN_TYPE_MONTH,
        RESULT_STATUS_SUBMITTED,
        Result,
    )
    from backend.app.services import result_evidence_materialization, result_submission

    plan = MonthPlan(owner_id="E_A", plan_month="2026-11", title="十一月计划", status="APPROVED")
    db.add(plan)
    db.flush()
    item = MonthPlanItem(plan_id=plan.id, task_content="任务", sort_no=1)
    db.add(item)
    db.flush()

    result = Result(
        owner_id="E_A",
        plan_type=RESULT_PLAN_TYPE_MONTH,
        plan_id=plan.id,
        plan_item_id=item.id,
        title="成果",
        content="完成",
        completion_rate=100.0,
        result_date=date(2026, 11, 10),
        version_no=1,
        status=RESULT_STATUS_SUBMITTED,
    )
    db.add(result)
    db.flush()
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    compact_hits = result_evidence_materialization.list_plan_result_evidence(
        db, "E_A", period="202611"
    )
    dashed_hits = result_evidence_materialization.list_plan_result_evidence(
        db, "E_A", period="2026-11"
    )

    assert len(compact_hits) == 1
    assert len(dashed_hits) == 1
    assert compact_hits[0].id == dashed_hits[0].id


# ---------------------------------------------------------------------------
# 5. resolve_period 输出权威格式
# ---------------------------------------------------------------------------


def test_resolve_period_returns_compact_format(db, org):
    """成果物化的周期解析结果必须是紧凑 ``YYYYMM``。"""
    from datetime import date

    from backend.app.domain.models import MonthPlan, MonthPlanItem
    from backend.app.domain.result_models import (
        RESULT_PLAN_TYPE_MONTH,
        RESULT_STATUS_SUBMITTED,
        Result,
    )
    from backend.app.services import result_evidence_materialization

    plan = MonthPlan(owner_id="E_A", plan_month="2026-11", title="十一月计划", status="APPROVED")
    db.add(plan)
    db.flush()
    item = MonthPlanItem(plan_id=plan.id, task_content="任务", sort_no=1)
    db.add(item)
    db.flush()

    result = Result(
        owner_id="E_A",
        plan_type=RESULT_PLAN_TYPE_MONTH,
        plan_id=plan.id,
        plan_item_id=item.id,
        title="成果",
        content="完成",
        completion_rate=100.0,
        result_date=date(2026, 11, 10),
        version_no=1,
        status=RESULT_STATUS_SUBMITTED,
    )
    db.add(result)
    db.flush()

    assert result_evidence_materialization.resolve_period(db, result) == "202611"
