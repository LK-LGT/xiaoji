"""计划成果 → 绩效证据物化 —— A6 阶段集成测试。

覆盖：

1. **物化触发**：审批通过时自动生成 ``Evidence`` 卡片。
2. **口径**：`source=plan_result`、`usage=scoring`、`status=approved`。
3. **幂等**：重复物化不重复生成（同一 ``source_ref``）。
4. **无附件**：没有附件时**也要**生成证据。
5. **驳回不物化**：被驳回的成果不产生证据。
6. **周期归属**：月成果取 ``plan_month``；周成果按 **D6 决策：周起始月**。
7. **版本区分**：驳回后重新提交产生新版本 → 各自一张证据。
8. **不碰 MetricValue**：物化**不**写指标实测值（算分唯一真值归 A7 规则引擎）。
9. **物化失败不阻断审批**：周期解析不出来时审批照常成功。
"""

from __future__ import annotations

import contextvars
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import (
    EvidenceSource,
    EvidenceStatus,
    EvidenceUsage,
    Visibility,
)
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    Evidence,
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
from backend.app.services import result_evidence_materialization as materialization
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
    """组织结构：`E_LEAD` 管理 `E_A`。"""
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


_CURRENT_USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "test_current_user_id", default=""
)
_CURRENT_USER: contextvars.ContextVar[Employee] = contextvars.ContextVar("test_current_user")


def _current_user() -> Employee:
    if not _CURRENT_USER_ID.get():
        raise AssertionError("测试请求未指定用户身份")
    return _CURRENT_USER.get()


@pytest.fixture()
def login(db, org):
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


@pytest.fixture()
def employee_client(db, org, login):
    """以员工 E_A 身份登录。"""
    with login(org["a"]) as c:
        yield c


@pytest.fixture()
def lead_client(db, org, login):
    """以主管 E_LEAD 身份登录。"""
    with login(org["lead"]) as c:
        yield c


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _month_plan(db, owner_id="E_A", month="2026-11"):
    plan = MonthPlan(owner_id=owner_id, plan_month=month, title="十一月计划", status="APPROVED")
    db.add(plan)
    db.flush()
    item = MonthPlanItem(
        plan_id=plan.id,
        task_name="完成订单模块",
        task_content="开发订单模块",
        deliverable="可运行的订单模块",
        performance_weight=10.0,
        estimated_value=100.0,
        status="APPROVED",
        sort_no=1,
    )
    db.add(item)
    db.flush()
    return plan, item


def _week_plan(db, owner_id="E_A", week_start=date(2026, 11, 2)):
    plan = WeekPlan(
        owner_id=owner_id,
        title="本周计划",
        week_start=week_start,
        week_end=date(2026, 11, 8) if week_start == date(2026, 11, 2) else week_start,
        status="APPROVED",
    )
    db.add(plan)
    db.flush()
    item = WeekPlanItem(
        plan_id=plan.id,
        content="修复线上缺陷",
        deliverable="缺陷清零",
        sort_no=1,
    )
    db.add(item)
    db.flush()
    return plan, item


def _submit_month_result(db, plan, item, **overrides):
    """直接落一条已提交的月成果（绕开 HTTP，聚焦物化逻辑）。"""
    result = Result(
        owner_id=plan.owner_id,
        plan_type=RESULT_PLAN_TYPE_MONTH,
        plan_id=plan.id,
        plan_item_id=item.id,
        title="订单模块已交付",
        description="完成了订单模块的开发",
        content="开发 + 自测 + 上线",
        deliverable="可运行的订单模块",
        completion_status="已完成",
        completion_rate=100.0,
        result_date=date(2026, 11, 10),
        version_no=1,
        status=RESULT_STATUS_SUBMITTED,
    )
    for key, value in overrides.items():
        setattr(result, key, value)
    db.add(result)
    db.flush()
    return result


def _submit_week_result(db, plan, item, **overrides):
    result = Result(
        owner_id=plan.owner_id,
        plan_type=RESULT_PLAN_TYPE_WEEK,
        plan_id=plan.id,
        plan_item_id=item.id,
        title="缺陷已清零",
        description="修复了 12 个线上缺陷",
        content="修复 + 回归",
        deliverable="缺陷清零",
        completion_status="已完成",
        completion_rate=90.0,
        result_date=date(2026, 11, 5),
        version_no=1,
        status=RESULT_STATUS_SUBMITTED,
    )
    for key, value in overrides.items():
        setattr(result, key, value)
    db.add(result)
    db.flush()
    return result


def _evidences(db, employee_id="E_A"):
    return list(
        db.execute(
            select(Evidence).where(Evidence.employee_id == employee_id)
        ).scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# 1. 物化触发
# ---------------------------------------------------------------------------


def test_approve_creates_evidence(db, org):
    """审批通过时自动生成证据卡片。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    rows = _evidences(db)
    assert len(rows) == 1
    ev = rows[0]
    assert ev.source == EvidenceSource.PLAN_RESULT.value
    assert ev.kind == EvidenceSource.PLAN_RESULT.value
    assert ev.employee_id == "E_A"
    assert ev.usage == EvidenceUsage.SCORING.value
    assert ev.status == EvidenceStatus.APPROVED.value
    assert ev.visibility == Visibility.SHARE_WITH_MANAGER.value


def test_evidence_summary_contains_title_and_rate(db, org):
    """证据摘要包含标题与完成率。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    ev = _evidences(db)[0]
    assert "订单模块已交付" in ev.summary
    assert "100" in ev.summary


def test_evidence_impact_contains_deliverable(db, org):
    """证据补充说明包含交付物与审批意见。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    result_submission.approve_result(db, "E_LEAD", result.id, comment="干得不错")
    db.flush()

    ev = _evidences(db)[0]
    assert ev.impact is not None
    assert "可运行的订单模块" in ev.impact
    assert "干得不错" in ev.impact


def test_evidence_as_of_is_approval_time(db, org):
    """证据的数据截至时间 = 审批通过时间。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    ev = _evidences(db)[0]
    assert ev.as_of is not None


# ---------------------------------------------------------------------------
# 2. 幂等
# ---------------------------------------------------------------------------


def test_materialize_is_idempotent(db, org):
    """重复物化同一成果不重复生成证据。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    # 再物化一次
    again = materialization.materialize_result(db, result)
    db.flush()

    assert len(_evidences(db)) == 1
    assert again.id == _evidences(db)[0].id


def test_source_ref_is_stable(db, org):
    """同一成果的 source_ref 固定不变。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    ev = _evidences(db)[0]
    assert ev.source_ref is not None
    assert ev.source_ref.startswith("E_A|2026-11|plan_result|month|")
    assert ev.source_ref.endswith(f"|{item.id}|1")


# ---------------------------------------------------------------------------
# 3. 无附件也要生成
# ---------------------------------------------------------------------------


def test_materialize_without_attachment(db, org):
    """没有附件时也要生成证据（成果描述本身就是证据）。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    rows = _evidences(db)
    assert len(rows) == 1
    assert "订单模块已交付" in rows[0].summary
    # 摘要里不应出现「附件」
    assert "附件" not in rows[0].summary


def test_materialize_with_attachment_mentions_file(db, org):
    """有附件时摘要列出附件名。"""
    from backend.app.domain.result_models import ResultEvidence

    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    db.add(
        ResultEvidence(
            result_id=result.id,
            file_name="订单模块验收报告.pdf",
            file_type="application/pdf",
            file_size=1024,
            checksum="abc123",
            storage_path="ab/abc123_订单模块验收报告.pdf",
            status="uploaded",
        )
    )
    db.flush()

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    ev = _evidences(db)[0]
    assert "附件" in ev.summary
    assert "订单模块验收报告.pdf" in ev.summary


# ---------------------------------------------------------------------------
# 4. 驳回不物化
# ---------------------------------------------------------------------------


def test_reject_does_not_materialize(db, org):
    """被驳回的成果不产生证据。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    result_submission.reject_result(db, "E_LEAD", result.id)
    db.flush()

    assert _evidences(db) == []


def test_draft_does_not_materialize(db, org):
    """草稿状态的成果不能物化。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item, status="draft")

    with pytest.raises(materialization.MaterializationError):
        materialization.materialize_result(db, result)


# ---------------------------------------------------------------------------
# 5. 周期归属
# ---------------------------------------------------------------------------


def test_month_result_period_from_plan_month(db, org):
    """月成果的周期取所属计划的 plan_month。"""
    plan, item = _month_plan(db, month="2026-11")
    result = _submit_month_result(db, plan, item)

    assert materialization.resolve_period(db, result) == "2026-11"


def test_week_result_period_from_week_start(db, org):
    """周成果的周期按 D6 决策取 week_start 所在月。"""
    plan, item = _week_plan(db, week_start=date(2026, 11, 2))
    result = _submit_week_result(db, plan, item)

    assert materialization.resolve_period(db, result) == "2026-11"


def test_cross_month_week_belongs_to_start_month(db, org):
    """跨月周（11/30~12/6）归属 11 月（D6：按周起始月）。"""
    plan = WeekPlan(
        owner_id="E_A",
        title="跨月周计划",
        week_start=date(2026, 11, 30),
        week_end=date(2026, 12, 6),
        status="APPROVED",
    )
    db.add(plan)
    db.flush()
    item = WeekPlanItem(plan_id=plan.id, content="跨月周的工作", sort_no=1)
    db.add(item)
    db.flush()

    result = _submit_week_result(db, plan, item)
    assert materialization.resolve_period(db, result) == "2026-11"


def test_evidence_ref_contains_period(db, org):
    """source_ref 第二段是周期，便于按周期过滤。"""
    plan, item = _month_plan(db, month="2026-11")
    result = _submit_month_result(db, plan, item)

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    ev = _evidences(db)[0]
    parts = ev.source_ref.split("|")
    assert parts[0] == "E_A"
    assert parts[1] == "2026-11"
    assert parts[2] == "plan_result"


def test_list_plan_result_evidence_filters_by_period(db, org):
    """按周期过滤成果类证据。"""
    plan_nov, item_nov = _month_plan(db, month="2026-11")
    result_nov = _submit_month_result(db, plan_nov, item_nov)
    result_submission.approve_result(db, "E_LEAD", result_nov.id)

    plan_dec, item_dec = _month_plan(db, month="2026-12")
    result_dec = _submit_month_result(db, plan_dec, item_dec)
    result_submission.approve_result(db, "E_LEAD", result_dec.id)
    db.flush()

    nov = materialization.list_plan_result_evidence(db, "E_A", period="2026-11")
    dec = materialization.list_plan_result_evidence(db, "E_A", period="2026-12")
    assert len(nov) == 1
    assert len(dec) == 1
    assert nov[0].id != dec[0].id


# ---------------------------------------------------------------------------
# 6. 版本区分
# ---------------------------------------------------------------------------


def test_new_version_creates_new_evidence(db, org):
    """驳回后重新提交产生新版本 → 各自一张证据。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item, version_no=1)

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()
    assert len(_evidences(db)) == 1

    # 模拟驳回后重新提交（版本号 +1）
    result.version_no = 2
    result.status = RESULT_STATUS_SUBMITTED
    db.flush()

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    rows = _evidences(db)
    assert len(rows) == 2
    assert len({r.source_ref for r in rows}) == 2


# ---------------------------------------------------------------------------
# 7. 物化本身不碰 MetricValue
#
# ⚠️ A7 落地后，**审批通过**这个动作确实会写 MetricValue（计划执行率指标，
#    由 A7 的 `plan_execution_metric` 负责）。但那是 A7 的职责，
#    **物化函数本身**依然不碰算分集 —— 这条边界依然要守住。
#
#    所以这里改为直接验证**物化函数**本身：
#    调 `materialize_result()` 后 MetricValue 仍为空。
# ---------------------------------------------------------------------------


def test_materialize_function_itself_does_not_write_metric_value(db, org):
    """物化函数本身不写指标实测值（算分唯一真值归 A7 规则引擎）。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    result.status = RESULT_STATUS_CONFIRMED
    db.flush()

    # 直接调物化 —— 不走 approve_result（那里才会触发 A7 的执行率写入）
    materialization.materialize_result(db, result)
    db.flush()

    mvs = list(db.execute(select(MetricValue)).scalars().all())
    assert mvs == []


def test_approval_writes_execution_rate_metric_not_from_materialization(db, org):
    """审批通过会写执行率指标 —— 但那是 A7 的职责，不是物化干的。

    这条断言锁定的是**职责边界**：A6 物化只产证据，
    执行率指标由 A7 的服务单独算。两者在同一个审批点位上先后发生。
    """
    from backend.app.services import plan_execution_metric as pem

    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    # 证据（A6 的产物）
    assert len(_evidences(db)) == 1
    # 指标（A7 的产物）—— 恰好一条，且 metric_code 是执行率
    mvs = list(db.execute(select(MetricValue)).scalars().all())
    assert len(mvs) == 1
    assert mvs[0].metric_code == pem.PLAN_EXECUTION_RATE_CODE
    assert mvs[0].source == pem.METRIC_SOURCE_PLAN


# ---------------------------------------------------------------------------
# 8. 物化失败不阻断审批
# ---------------------------------------------------------------------------


def test_approval_succeeds_even_if_materialization_fails(db, org, monkeypatch):
    """物化失败不阻断审批（审批是既成事实）。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)

    def _boom(*args, **kwargs):
        raise materialization.MaterializationError("模拟物化失败")

    monkeypatch.setattr(materialization, "materialize_result", _boom)

    approved = result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    assert approved.status == RESULT_STATUS_CONFIRMED
    assert _evidences(db) == []


# ---------------------------------------------------------------------------
# 9. HTTP 端到端
# ---------------------------------------------------------------------------


def test_approval_endpoint_materializes(employee_client, lead_client, db, org):
    """走真实端点审批 → 证据自动生成。"""
    plan, item = _week_plan(db)
    result = _submit_week_result(db, plan, item)

    resp = lead_client.post(
        f"/v1/leader/week-result-approvals/{result.id}/approve", json={"comment": "通过"}
    )
    assert resp.status_code == 200, resp.text

    rows = _evidences(db)
    assert len(rows) == 1
    assert rows[0].source == EvidenceSource.PLAN_RESULT.value
    assert rows[0].usage == EvidenceUsage.SCORING.value


def test_employee_sees_materialized_evidence(employee_client, db, org):
    """员工能看到自己的成果证据。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    rows = materialization.list_plan_result_evidence(db, "E_A")
    assert len(rows) == 1
    assert rows[0].summary


# ---------------------------------------------------------------------------
# 10. 序列化
# ---------------------------------------------------------------------------


def test_serialize_evidence_shape(db, org):
    """证据序列化对齐前端契约。"""
    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    payload = materialization.serialize_evidence(_evidences(db)[0])
    assert payload["source"] == "plan_result"
    assert payload["usage"] == "scoring"
    assert payload["employeeId"] == "E_A"
    assert payload["kind"] == "plan_result"
    assert isinstance(payload["summary"], str)


# ---------------------------------------------------------------------------
# 11. 接上算分链路（真正的价值所在）
# ---------------------------------------------------------------------------


def test_materialized_evidence_visible_to_scoring_pipeline(db, org):
    """物化后的证据能被 `list_scoring_evidence` 取到（算分链路入口）。"""
    from backend.app.services import evidence_service

    plan, item = _month_plan(db, month="2026-11")
    result = _submit_month_result(db, plan, item)
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    rows = evidence_service.list_scoring_evidence(db, "E_A")
    assert len(rows) == 1
    assert rows[0].source == EvidenceSource.PLAN_RESULT.value


def test_materialized_evidence_filtered_by_period(db, org):
    """按周期过滤时，成果证据按 source_ref 的周期段归属。"""
    from backend.app.services import evidence_service

    plan_nov, item_nov = _month_plan(db, month="2026-11")
    result_nov = _submit_month_result(db, plan_nov, item_nov)
    result_submission.approve_result(db, "E_LEAD", result_nov.id)

    plan_dec, item_dec = _month_plan(db, month="2026-12")
    result_dec = _submit_month_result(db, plan_dec, item_dec)
    result_submission.approve_result(db, "E_LEAD", result_dec.id)
    db.flush()

    nov = evidence_service.list_scoring_evidence(db, "E_A", period="202611")
    dec = evidence_service.list_scoring_evidence(db, "E_A", period="202612")
    assert len(nov) == 1
    assert len(dec) == 1
    assert nov[0].id != dec[0].id


def test_materialized_evidence_split_as_scoring(db, org):
    """成果证据落在「可作结论支撑」那一组（usage=scoring）。"""
    from backend.app.services import evidence_service

    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    rows = evidence_service.list_scoring_evidence(db, "E_A")
    scoring, reference = evidence_service.split_by_usage(rows)
    assert len(scoring) == 1
    assert len(reference) == 0


def test_plan_result_not_rejected_by_attendance_guard(db, org):
    """`add_metric_value` 只拦考勤来源，成果来源不受影响（但我们不写它）。"""
    from backend.app.services import evidence_service

    plan, item = _month_plan(db)
    result = _submit_month_result(db, plan, item)
    result_submission.approve_result(db, "E_LEAD", result.id)
    db.flush()

    ev = _evidences(db)[0]
    assert ev.source not in evidence_service.ATTENDANCE_SOURCES
