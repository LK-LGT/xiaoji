"""计划执行率指标 + 计划统计 —— A7 阶段集成测试。

覆盖：

1. **执行率公式**：按条目绩效权重加权。
2. **退化规则**：权重全为 0 → 简单平均。
3. **只算已审批**：草稿 / 待审 / 被驳回不计入。
4. **只取最新版本**：同一条目多次提交，旧的低分版本不稀释。
5. **无成果不写 MetricValue**：value=None ≠ 0。
6. **写入口唯一**：走 `evidence_service.add_metric_value`（算分集唯一入口）。
7. **审批联动**：审批通过时自动重算并写入。
8. **周期归属**：月成果按 `plan_month`；周成果按周起始月（D6）。
9. **`/planning/stats` 真实统计**：四个字段不再是 0。
10. **数据范围**：HR/管理员全公司，部门负责人管理范围，主管直属下属，员工本人。
11. **鉴权**：`/planning/stats` 需要登录。
"""

from __future__ import annotations

import contextvars
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    Metric,
    MetricValue,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
    WeekPlanItem,
)
from backend.app.domain.result_models import (
    RESULT_PLAN_TYPE_MONTH,
    RESULT_PLAN_TYPE_WEEK,
    RESULT_STATUS_CONFIRMED,
    RESULT_STATUS_REJECTED,
    RESULT_STATUS_SUBMITTED,
    Result,
)
from backend.app.main import app
from backend.app.services import plan_execution_metric as pem
from backend.app.services import result_submission

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def org(db):
    """组织结构：`E_LEAD` 管理 `E_A` / `E_B`。"""
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
    emp_b = Employee(
        id="E_B",
        name="员工乙",
        department_id="D_ROOT",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([lead, emp_a, emp_b])
    db.flush()
    emp_a.manager_id = "E_LEAD"
    emp_b.manager_id = "E_LEAD"
    dept.leader_id = "E_LEAD"
    db.flush()
    return {"lead": lead, "a": emp_a, "b": emp_b, "dept": dept}


_CURRENT_USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "test_current_user_id", default=""
)
_CURRENT_USER: contextvars.ContextVar[Employee] = contextvars.ContextVar("test_current_user")


def _current_user() -> Employee:
    if not _CURRENT_USER_ID.get():
        raise AssertionError("测试请求未指定用户身份")
    return _CURRENT_USER.get()


@pytest.fixture()
def login(db):
    """切换身份的工厂：``login(org["lead"])`` 返回一个客户端。"""

    def _make(user: Employee) -> TestClient:
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = _current_user

        class _Client(TestClient):
            def request(self, method, url, **kwargs):  # type: ignore[override]
                headers = dict(kwargs.pop("headers", None) or {})
                headers["X-Test-User"] = user.id
                kwargs["headers"] = headers
                token = _CURRENT_USER_ID.set(user.id)
                obj = _CURRENT_USER.set(user)
                try:
                    return super().request(method, url, **kwargs)
                finally:
                    _CURRENT_USER_ID.reset(token)
                    _CURRENT_USER.reset(obj)

        return _Client(app)

    yield _make
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _month_plan(db, owner_id="E_A", month="2026-11", weights=(10.0,)):
    """建月计划 + N 个条目（权重按 ``weights`` 顺序分配）。"""
    plan = MonthPlan(owner_id=owner_id, plan_month=month, title="十一月计划", status="APPROVED")
    db.add(plan)
    db.flush()
    items = []
    for idx, weight in enumerate(weights, start=1):
        item = MonthPlanItem(
            plan_id=plan.id,
            task_name=f"任务 {idx}",
            task_content="内容",
            deliverable="交付物",
            performance_weight=weight,
            status="APPROVED",
            sort_no=idx,
        )
        db.add(item)
        items.append(item)
    db.flush()
    return plan, items


def _week_plan(db, owner_id="E_A", week_start=date(2026, 11, 2)):
    plan = WeekPlan(
        owner_id=owner_id,
        title="本周计划",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        status="APPROVED",
    )
    db.add(plan)
    db.flush()
    item = WeekPlanItem(plan_id=plan.id, content="修复缺陷", sort_no=1)
    db.add(item)
    db.flush()
    return plan, item


def _result(
    db,
    plan,
    item,
    *,
    plan_type=RESULT_PLAN_TYPE_MONTH,
    completion_rate=100.0,
    status=RESULT_STATUS_CONFIRMED,
    version_no=1,
):
    row = Result(
        owner_id=plan.owner_id,
        plan_type=plan_type,
        plan_id=plan.id,
        plan_item_id=item.id,
        title=f"成果 {item.id}",
        description="描述",
        deliverable="交付物",
        completion_status="已完成",
        completion_rate=completion_rate,
        result_date=date(2026, 11, 10),
        version_no=version_no,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def _metric_values(db, employee_id="E_A"):
    return list(
        db.execute(
            select(MetricValue).where(
                MetricValue.employee_id == employee_id,
                MetricValue.metric_code == pem.PLAN_EXECUTION_RATE_CODE,
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# 1. 执行率公式：按权重加权
# ---------------------------------------------------------------------------


def test_execution_rate_is_weighted(db, org):
    """按条目绩效权重加权：Σ(完成率×权重)/Σ权重。"""
    plan, items = _month_plan(db, weights=(60.0, 40.0))
    _result(db, plan, items[0], completion_rate=100.0)
    _result(db, plan, items[1], completion_rate=50.0)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    # (100×60 + 50×40) / 100 = 80
    assert rate.value == pytest.approx(80.0)
    assert rate.counted_results == 2
    assert rate.total_weight == pytest.approx(100.0)


def test_execution_rate_simple_average_differs_from_weighted(db, org):
    """加权 ≠ 简单平均（证明确实按权重算，不是碰巧）。"""
    plan, items = _month_plan(db, weights=(90.0, 10.0))
    _result(db, plan, items[0], completion_rate=100.0)
    _result(db, plan, items[1], completion_rate=0.0)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value == pytest.approx(90.0)  # 加权
    assert rate.value != pytest.approx(50.0)  # 简单平均


# ---------------------------------------------------------------------------
# 2. 退化规则
# ---------------------------------------------------------------------------


def test_execution_rate_degrades_to_simple_average_when_no_weights(db, org):
    """权重全为 0 → 退化为简单平均（不除零、不给 0 分）。"""
    plan, items = _month_plan(db, weights=(0.0, 0.0))
    _result(db, plan, items[0], completion_rate=100.0)
    _result(db, plan, items[1], completion_rate=60.0)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value == pytest.approx(80.0)
    assert rate.is_degraded is True


def test_execution_rate_not_zero_when_single_result_completed(db, org):
    """单条成果完成率 100 → 执行率 100（不是 0）。"""
    plan, items = _month_plan(db, weights=(0.0,))
    _result(db, plan, items[0], completion_rate=100.0)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 3. 只算已审批成果
# ---------------------------------------------------------------------------


def test_draft_result_not_counted(db, org):
    """草稿成果不计入执行率。"""
    plan, items = _month_plan(db)
    _result(db, plan, items[0], completion_rate=100.0, status="draft")
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value is None


def test_submitted_result_not_counted(db, org):
    """待审批成果不计入执行率。"""
    plan, items = _month_plan(db)
    _result(db, plan, items[0], completion_rate=100.0, status=RESULT_STATUS_SUBMITTED)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value is None


def test_rejected_result_not_counted(db, org):
    """被驳回的成果不计入执行率。"""
    plan, items = _month_plan(db)
    _result(db, plan, items[0], completion_rate=100.0, status=RESULT_STATUS_REJECTED)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value is None


# ---------------------------------------------------------------------------
# 4. 只取最新版本
# ---------------------------------------------------------------------------


def test_only_latest_version_counted(db, org):
    """同一条目的历史版本不参与计算（否则会稀释执行率）。"""
    plan, items = _month_plan(db, weights=(100.0,))
    # 旧版本：完成率 20
    _result(db, plan, items[0], completion_rate=20.0, version_no=1)
    # 新版本：完成率 90
    _result(db, plan, items[0], completion_rate=90.0, version_no=2)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value == pytest.approx(90.0)
    assert rate.counted_results == 1


def test_rejected_newer_version_does_not_fall_back_to_old(db, org):
    """最新版被驳回 → 该条目不计入（不回退到旧版本）。"""
    plan, items = _month_plan(db, weights=(100.0,))
    _result(db, plan, items[0], completion_rate=20.0, version_no=1)
    _result(db, plan, items[0], completion_rate=90.0, version_no=2, status=RESULT_STATUS_REJECTED)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value is None


# ---------------------------------------------------------------------------
# 5. 无成果不写 MetricValue
# ---------------------------------------------------------------------------


def test_no_metric_value_written_without_approved_result(db, org):
    """无已审批成果 → 不写 MetricValue（value=None ≠ 0）。"""
    plan, items = _month_plan(db)
    _result(db, plan, items[0], status=RESULT_STATUS_SUBMITTED)
    db.flush()

    value, written = pem.refresh_execution_rate_metric(db, "E_A", "2026-11")
    assert value is None
    assert written is False
    assert _metric_values(db) == []


# ---------------------------------------------------------------------------
# 6. 写入算分集
# ---------------------------------------------------------------------------


def test_metric_value_written_with_correct_fields(db, org):
    """写入的 MetricValue 字段正确。"""
    plan, items = _month_plan(db, weights=(100.0,))
    _result(db, plan, items[0], completion_rate=75.0)
    db.flush()

    value, written = pem.refresh_execution_rate_metric(db, "E_A", "2026-11")
    assert written is True
    assert value == pytest.approx(75.0)

    rows = _metric_values(db)
    assert len(rows) == 1
    mv = rows[0]
    assert mv.employee_id == "E_A"
    assert mv.metric_code == pem.PLAN_EXECUTION_RATE_CODE
    assert mv.period == "202611"  # BUG-R6-26：指标实测值周期统一为紧凑 `YYYYMM`
    assert mv.value == pytest.approx(75.0)
    assert mv.target == pytest.approx(100.0)
    assert mv.unit == "%"
    assert mv.source == pem.METRIC_SOURCE_PLAN


def test_metric_value_is_upsert_not_duplicate(db, org):
    """重复重算 → 更新同一条，不产生重复行。"""
    plan, items = _month_plan(db, weights=(100.0,))
    _result(db, plan, items[0], completion_rate=50.0)
    db.flush()

    pem.refresh_execution_rate_metric(db, "E_A", "2026-11")
    db.flush()

    _result(db, plan, items[0], completion_rate=90.0, version_no=2)
    db.flush()
    pem.refresh_execution_rate_metric(db, "E_A", "2026-11")
    db.flush()

    rows = _metric_values(db)
    assert len(rows) == 1
    assert rows[0].value == pytest.approx(90.0)


def test_metric_definition_created(db, org):
    """指标定义表里有「计划执行率」，且口径字段非空。"""
    pem.ensure_metric_definition(db)
    db.flush()

    metric = db.execute(
        select(Metric).where(Metric.code == pem.PLAN_EXECUTION_RATE_CODE)
    ).scalar_one()
    assert metric.name == "计划执行率"
    assert metric.unit == "%"
    assert metric.calculation  # 决策③：口径字段必须非空
    assert metric.data_source  # 决策③：口径字段必须非空
    assert metric.is_attendance is False


def test_metric_definition_is_idempotent(db, org):
    """重复创建指标定义不报错。"""
    pem.ensure_metric_definition(db)
    pem.ensure_metric_definition(db)
    db.flush()

    count = db.execute(
        select(Metric).where(Metric.code == pem.PLAN_EXECUTION_RATE_CODE)
    ).scalars().all()
    assert len(count) == 1


# ---------------------------------------------------------------------------
# 7. 审批联动
# ---------------------------------------------------------------------------


def test_approval_refreshes_execution_rate(db, org):
    """审批通过 → 自动重算并写入执行率。"""
    plan, items = _month_plan(db, weights=(100.0,))
    result = _result(db, plan, items[0], completion_rate=88.0, status=RESULT_STATUS_SUBMITTED)
    db.flush()

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    rows = _metric_values(db)
    assert len(rows) == 1
    assert rows[0].value == pytest.approx(88.0)
    assert rows[0].period == "202611"  # BUG-R6-26：紧凑 `YYYYMM`


def test_approval_does_not_write_metric_for_rejected(db, org):
    """驳回不写执行率。"""
    plan, items = _month_plan(db)
    result = _result(db, plan, items[0], status=RESULT_STATUS_SUBMITTED)
    db.flush()

    result_submission.reject_result(db, "E_LEAD", result.id)
    db.flush()

    assert _metric_values(db) == []


def test_approval_succeeds_even_if_metric_refresh_fails(db, org, monkeypatch):
    """执行率重算失败不阻断审批。"""
    plan, items = _month_plan(db)
    result = _result(db, plan, items[0], status=RESULT_STATUS_SUBMITTED)
    db.flush()

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟执行率重算失败")

    monkeypatch.setattr(
        pem, "refresh_execution_rate_metric", _boom
    )

    approved = result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()
    assert approved.status == RESULT_STATUS_CONFIRMED


# ---------------------------------------------------------------------------
# 8. 周期归属
# ---------------------------------------------------------------------------


def test_week_result_belongs_to_week_start_month(db, org):
    """周成果按周起始月归属（D6）。"""
    plan, item = _week_plan(db, week_start=date(2026, 11, 30))
    _result(db, plan, item, plan_type=RESULT_PLAN_TYPE_WEEK, completion_rate=70.0)
    db.flush()

    rate = pem.compute_execution_rate(db, "E_A", "2026-11")
    assert rate.value == pytest.approx(70.0)

    # 12 月不应重复计入
    assert pem.compute_execution_rate(db, "E_A", "2026-12").value is None


def test_month_result_belongs_to_plan_month(db, org):
    """月成果按 plan_month 归属。"""
    plan, items = _month_plan(db, month="2026-10")
    _result(db, plan, items[0], completion_rate=60.0)
    db.flush()

    assert pem.compute_execution_rate(db, "E_A", "2026-10").value == pytest.approx(60.0)
    assert pem.compute_execution_rate(db, "E_A", "2026-11").value is None


# ---------------------------------------------------------------------------
# 9. /planning/stats 真实统计
# ---------------------------------------------------------------------------


def test_stats_returns_real_numbers(db, org, login):
    """统计端点返回真实数据（不再是恒 0）。"""
    from backend.app.domain.models import DayPlan

    plan, items = _month_plan(db, weights=(100.0,))
    _result(db, plan, items[0], completion_rate=100.0)
    db.add(
        DayPlan(
            owner_id="E_A",
            plan_date=date(2026, 11, 10),
            content="今天的工作",
            status="submitted",
        )
    )
    db.flush()

    with login(org["lead"]) as c:
        resp = c.get("/v1/planning/stats?month=2026-11")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["pendingDayPlans"] == 1
    assert data["currentMonthResults"] == 1
    assert data["closureRate"] == "100.0%"
    assert data["planExecutionRate"] == pytest.approx(100.0)


def test_stats_overdue_count(db, org, login):
    """逾期未审日计划被统计。"""
    from backend.app.domain.models import DayPlan

    db.add(
        DayPlan(
            owner_id="E_A",
            plan_date=date(2026, 11, 10),
            content="逾期的工作",
            status="submitted",
            approval_due_at=datetime.now() - timedelta(days=1),
        )
    )
    db.flush()

    with login(org["lead"]) as c:
        resp = c.get("/v1/planning/stats?month=2026-11")
    data = resp.json()["data"]
    assert data["pendingDayPlans"] == 1
    assert data["overduePendingDayPlans"] == 1


def test_stats_excludes_reviewed_day_plans(db, org, login):
    """已抽检的日计划不再计入待审。"""
    from backend.app.domain.models import DayPlan

    db.add(
        DayPlan(
            owner_id="E_A",
            plan_date=date(2026, 11, 10),
            content="已抽检",
            status="submitted",
            review_status="APPROVED",
        )
    )
    db.flush()

    with login(org["lead"]) as c:
        resp = c.get("/v1/planning/stats?month=2026-11")
    assert resp.json()["data"]["pendingDayPlans"] == 0


def test_stats_requires_auth(db, org, login):
    """统计端点需要登录（未覆盖 get_current_user → 走真实鉴权）。"""
    # 只覆盖 get_db，**不覆盖** get_current_user —— 让真实鉴权依赖生效
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            resp = c.get("/v1/planning/stats")
        assert resp.status_code in (401, 403), f"未鉴权竟然返回 {resp.status_code}"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 10. 数据范围
# ---------------------------------------------------------------------------


def test_stats_scope_manager_sees_direct_reports(db, org, login):
    """主管看得到直属下属的数据。"""
    plan, items = _month_plan(db, owner_id="E_A")
    _result(db, plan, items[0])
    db.flush()

    with login(org["lead"]) as c:
        data = c.get("/v1/planning/stats?month=2026-11").json()["data"]
    assert data["currentMonthResults"] == 1
    assert "E_A" in data["detail"]["scopedEmployeeIds"]


def test_stats_scope_employee_sees_only_self(db, org, login):
    """普通员工只看自己的数据。"""
    plan, items = _month_plan(db, owner_id="E_A")
    _result(db, plan, items[0])
    db.flush()

    with login(org["a"]) as c:
        data = c.get("/v1/planning/stats?month=2026-11").json()["data"]
    assert data["detail"]["scopedEmployeeIds"] == ["E_A"]
    assert data["currentMonthResults"] == 1


def test_stats_scope_employee_does_not_see_others(db, org, login):
    """员工看不到同事的数据。"""
    plan, items = _month_plan(db, owner_id="E_B")
    _result(db, plan, items[0])
    db.flush()

    with login(org["a"]) as c:
        data = c.get("/v1/planning/stats?month=2026-11").json()["data"]
    assert data["currentMonthResults"] == 0


def test_stats_scope_department_leader_sees_whole_department(db, org, login):
    """部门负责人看得到整个管理范围。"""
    plan, items = _month_plan(db, owner_id="E_B")
    _result(db, plan, items[0])
    db.flush()

    with login(org["lead"]) as c:
        data = c.get("/v1/planning/stats?month=2026-11").json()["data"]
    assert data["currentMonthResults"] == 1
    assert "E_B" in data["detail"]["scopedEmployeeIds"]


# ---------------------------------------------------------------------------
# 11. 端到端：审批 → 指标 → 统计
# ---------------------------------------------------------------------------


def test_end_to_end_approval_metric_and_stats(db, org, login):
    """端到端：审批通过 → 写执行率 → 统计里能看到。"""
    plan, items = _month_plan(db, weights=(100.0,))
    result = _result(db, plan, items[0], completion_rate=66.0, status=RESULT_STATUS_SUBMITTED)
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(
            f"/v1/leader/week-result-approvals/{result.id}/approve", json={"comment": "通过"}
        )
    assert resp.status_code == 200, resp.text
    db.flush()

    rows = _metric_values(db)
    assert len(rows) == 1
    assert rows[0].value == pytest.approx(66.0)

    with login(org["lead"]) as c:
        data = c.get("/v1/planning/stats?month=2026-11").json()["data"]
    assert data["currentMonthResults"] == 1
    assert data["confirmedResults"] == 1
    assert data["planExecutionRate"] == pytest.approx(66.0)
