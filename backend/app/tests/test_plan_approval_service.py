"""计划审批服务测试 —— A1 阶段。

覆盖：
1. **一级审批流转**：月 / 周计划 `submitted → approved` / `rejected`。
2. **审批流水**：每次审批写一条 `plan_approval`，字段完整。
3. **幂等**：同一版本重复审批不产生重复流水。
4. **状态守卫**：非 `submitted` 状态审批被拒。
5. **日计划抽检**：只写 `review_status`，不改主状态机。
6. **数据范围**：待批列表只含可见范围内的计划。
7. **批量审批**：单条失败不影响其它条目。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.enums import PlanApprovalAction, PlanStatus
from backend.app.domain.models import (
    Base,
    DayPlan,
    Department,
    Employee,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
)
from backend.app.domain.plan_models import PlanApproval
from backend.app.services import plan_approval
from backend.app.services.plan_approval import (
    PLAN_TYPE_DAY,
    PLAN_TYPE_MONTH,
    PLAN_TYPE_WEEK,
    ApprovalScope,
    PlanApprovalConflict,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def org(db):
    """员工甲（计划归属人）、员工乙（其上级主管）、员工丙（总监）。"""
    dept = Department(id="D_1", name="业务部", active=True)
    db.add(dept)
    db.flush()

    emp_a = Employee(
        id="E_A", name="员工甲", department_id="D_1",
        employment_status="active", account_status="enabled",
    )
    emp_b = Employee(
        id="E_B", name="主管乙", department_id="D_1", manager_id="E_C",
        employment_status="active", account_status="enabled",
    )
    emp_c = Employee(
        id="E_C", name="总监丙", department_id="D_1",
        employment_status="active", account_status="enabled",
    )
    db.add_all([emp_a, emp_b, emp_c])
    db.flush()
    return {"a": emp_a, "b": emp_b, "c": emp_c, "dept": dept}


@pytest.fixture()
def scope():
    """审批人范围：乙的下属是甲；丙管理整个 D_1。"""
    return ApprovalScope(
        direct_report_ids=frozenset({"E_A"}),
        managed_department_ids=frozenset({"D_1"}),
    )


def _make_month_plan(
    db, owner_id="E_A", status=PlanStatus.SUBMITTED.value, month="2026-11"
):
    plan = MonthPlan(
        owner_id=owner_id,
        plan_month=month,
        title="十一月计划",
        status=status,
    )
    db.add(plan)
    db.flush()
    db.add(MonthPlanItem(plan_id=plan.id, task_name="推进大客户签约", sort_no=0))
    db.flush()
    return plan


def _make_week_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value, week_start=None):
    start = week_start or date(2026, 11, 9)
    plan = WeekPlan(
        owner_id=owner_id,
        title="周计划",
        week_start=start,
        week_end=start + timedelta(days=6),
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


def _make_day_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value, plan_date=None):
    plan = DayPlan(
        owner_id=owner_id,
        plan_date=plan_date or date(2026, 11, 10),
        content="完成需求评审",
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


# ===========================================================================
# 1. 一级审批流转
# ===========================================================================


def test_approve_month_plan_transitions_to_approved(db, org):
    """主管批准月计划 → 状态变 approved，写入 approved_at / approval_comment。"""
    plan = _make_month_plan(db)
    version_before = plan.version_no

    result = plan_approval.approve_month_plan(
        db, "E_C", plan.id, comment="同意，按计划推进"
    )

    assert result.status == PlanStatus.APPROVED.value
    assert result.approved_at is not None
    assert result.approval_comment == "同意，按计划推进"
    assert result.version_no == version_before + 1


def test_reject_month_plan_transitions_to_rejected(db, org):
    """主管驳回月计划 → 状态变 rejected。"""
    plan = _make_month_plan(db)

    result = plan_approval.reject_month_plan(db, "E_C", plan.id, comment="目标不清晰")

    assert result.status == PlanStatus.REJECTED.value
    assert result.approval_comment == "目标不清晰"
    assert result.approved_at is not None


def test_approve_week_plan_transitions_to_approved(db, org):
    """主管批准周计划 → 状态变 approved，写入 approve_at（周计划列名不同）。"""
    plan = _make_week_plan(db)

    result = plan_approval.approve_week_plan(db, "E_C", plan.id, comment="OK")

    assert result.status == PlanStatus.APPROVED.value
    assert result.approve_at is not None
    assert result.approval_comment == "OK"


def test_reject_week_plan_transitions_to_rejected(db, org):
    plan = _make_week_plan(db)

    result = plan_approval.reject_week_plan(db, "E_C", plan.id, comment="需要补充")

    assert result.status == PlanStatus.REJECTED.value


# ===========================================================================
# 2. 审批流水
# ===========================================================================


def test_approval_writes_plan_approval_row(db, org):
    """审批必须写一条 plan_approval 流水，字段完整。"""
    plan = _make_month_plan(db)

    plan_approval.approve_month_plan(db, "E_C", plan.id, comment="同意")

    row = db.execute(select(PlanApproval)).scalar_one()
    assert row.plan_type == PLAN_TYPE_MONTH
    assert row.plan_id == plan.id
    assert row.actor_id == "E_C"
    assert row.action == PlanApprovalAction.APPROVE.value
    assert row.from_status == PlanStatus.SUBMITTED.value
    assert row.to_status == PlanStatus.APPROVED.value
    assert row.comment == "同意"
    assert row.dedup_key


def test_reject_writes_reject_action(db, org):
    plan = _make_month_plan(db)

    plan_approval.reject_month_plan(db, "E_C", plan.id)

    row = db.execute(select(PlanApproval)).scalar_one()
    assert row.action == PlanApprovalAction.REJECT.value
    assert row.to_status == PlanStatus.REJECTED.value


def test_day_plan_review_writes_approval_row(db, org):
    """日计划抽检也要写流水。"""
    plan = _make_day_plan(db)

    plan_approval.review_day_plan(db, "E_C", plan.id, approved=True, comment="抽查通过")

    row = db.execute(select(PlanApproval)).scalar_one()
    assert row.plan_type == PLAN_TYPE_DAY
    assert row.action == PlanApprovalAction.APPROVE.value
    assert row.to_status == plan_approval.REVIEW_APPROVED


# ===========================================================================
# 3. 幂等
# ===========================================================================


def test_repeated_approval_same_version_does_not_duplicate_flow(db, org):
    """同一版本重复审批 → 只写一条流水（前端双击 / 网络重试）。"""
    plan = _make_month_plan(db)

    plan_approval.approve_month_plan(db, "E_C", plan.id, comment="同意")
    # 模拟重试：版本已被 bump，此时状态已 approved，会被状态守卫拦下
    with pytest.raises(PlanApprovalConflict):
        plan_approval.approve_month_plan(db, "E_C", plan.id, comment="同意")

    count = db.execute(select(func.count()).select_from(PlanApproval)).scalar_one()
    assert count == 1


def test_dedup_key_is_stable_for_same_version(db, org):
    """幂等键格式：类型:计划ID:动作:版本号。"""
    plan = _make_month_plan(db)
    version = plan.version_no

    key = plan_approval._build_dedup_key(
        PLAN_TYPE_MONTH, plan.id, PlanApprovalAction.APPROVE.value, version
    )
    assert key == f"month:{plan.id}:approve:{version}"


# ===========================================================================
# 4. 状态守卫
# ===========================================================================


def test_approve_draft_plan_is_rejected(db, org):
    """草稿态不允许审批。"""
    plan = _make_month_plan(db, status=PlanStatus.DRAFT.value)

    with pytest.raises(PlanApprovalConflict):
        plan_approval.approve_month_plan(db, "E_C", plan.id)

    assert db.execute(select(func.count()).select_from(PlanApproval)).scalar_one() == 0


def test_approve_already_approved_plan_is_rejected(db, org):
    """已批准的不能重复审批。"""
    plan = _make_month_plan(db, status=PlanStatus.APPROVED.value)

    with pytest.raises(PlanApprovalConflict):
        plan_approval.approve_month_plan(db, "E_C", plan.id)


def test_review_non_submitted_day_plan_is_rejected(db, org):
    """非 submitted 态的日计划不能抽检。"""
    plan = _make_day_plan(db, status=PlanStatus.DRAFT.value)

    with pytest.raises(PlanApprovalConflict):
        plan_approval.review_day_plan(db, "E_C", plan.id, approved=True)


# ===========================================================================
# 5. 日计划抽检语义
# ===========================================================================


def test_day_plan_review_does_not_change_main_status(db, org):
    """抽检只写 review_status，**不改** plan.status（不阻塞员工流程）。"""
    plan = _make_day_plan(db)

    result = plan_approval.review_day_plan(db, "E_C", plan.id, approved=True, comment="通过")

    assert result.status == PlanStatus.SUBMITTED.value
    assert result.review_status == plan_approval.REVIEW_APPROVED
    assert result.reviewed_at is not None
    assert result.leader_comment == "通过"


def test_day_plan_reject_sets_review_rejected(db, org):
    plan = _make_day_plan(db)

    result = plan_approval.review_day_plan(db, "E_C", plan.id, approved=False, comment="不合格")

    assert result.status == PlanStatus.SUBMITTED.value
    assert result.review_status == plan_approval.REVIEW_REJECTED
    assert result.leader_comment == "不合格"


# ===========================================================================
# 6. 数据范围
# ===========================================================================


def test_pending_month_plans_filters_by_scope(db, org, scope):
    """待批列表只含审批人可见范围内的计划。

    E_C 管理 D_1，所以 D_1 里的成员（含 E_A / E_B）都可见；
    其它部门的成员不可见。
    """
    _make_month_plan(db, owner_id="E_A", month="2026-11")
    _make_month_plan(db, owner_id="E_B", month="2026-11")

    # D_2 部门的员工 —— 不在管理范围内
    dept2 = Department(id="D_2", name="技术部", active=True)
    db.add(dept2)
    db.flush()
    outsider = Employee(
        id="E_X", name="外部员工", department_id="D_2",
        employment_status="active", account_status="enabled",
    )
    db.add(outsider)
    db.flush()
    _make_month_plan(db, owner_id="E_X", month="2026-11")

    rows = plan_approval.list_pending_month_plans(db, scope)

    owners = {p.owner_id for p in rows}
    assert "E_A" in owners
    assert "E_B" in owners  # 同属 D_1，部门负责人可见
    assert "E_X" not in owners  # D_2 不在管理范围内


def test_pending_month_plans_excludes_out_of_scope_department(db, org):
    """管理范围为空时，待批列表为空。"""
    _make_month_plan(db, owner_id="E_A")

    empty_scope = ApprovalScope(
        direct_report_ids=frozenset(),
        managed_department_ids=frozenset(),
    )
    rows = plan_approval.list_pending_month_plans(db, empty_scope)

    assert rows == []


def test_pending_week_plans_only_submitted(db, org, scope):
    """只有 submitted 的计划进入待批列表。"""
    _make_week_plan(db, status=PlanStatus.SUBMITTED.value)
    _make_week_plan(db, status=PlanStatus.DRAFT.value, week_start=date(2026, 11, 16))

    rows = plan_approval.list_pending_week_plans(db, scope)

    assert len(rows) == 1
    assert all(p.status == PlanStatus.SUBMITTED.value for p in rows)


def test_pending_day_plans_excludes_already_reviewed(db, org, scope):
    """已抽检过的日计划不再出现在待抽检列表。"""
    reviewed = _make_day_plan(db, plan_date=date(2026, 11, 10))
    reviewed.review_status = plan_approval.REVIEW_APPROVED
    db.flush()
    _make_day_plan(db, plan_date=date(2026, 11, 11))

    rows = plan_approval.list_pending_day_plans(db, scope)

    assert len(rows) == 1
    assert reviewed.id not in {p.id for p in rows}


def test_count_pending_approvals_counts_all_types(db, org, scope):
    _make_month_plan(db)
    _make_week_plan(db)
    _make_day_plan(db)

    assert plan_approval.count_pending_approvals(db, scope) == 3


# ===========================================================================
# 7. 批量审批
# ===========================================================================


def test_batch_approve_succeeds_for_all_valid(db, org):
    """批量审批：全部合法则全部成功。"""
    p1 = _make_month_plan(db, month="2026-11")
    p2 = _make_month_plan(db, month="2026-12")

    result = plan_approval.batch_approve(
        db, "E_C", PLAN_TYPE_MONTH, [p1.id, p2.id], comment="同意"
    )

    assert result["succeeded"] == [p1.id, p2.id]
    assert result["failed"] == []
    assert db.execute(select(func.count()).select_from(PlanApproval)).scalar_one() == 2


def test_batch_approve_isolates_failures(db, org):
    """单条失败不影响其它条目。"""
    good = _make_month_plan(db, month="2026-11")
    bad = _make_month_plan(db, month="2026-12", status=PlanStatus.DRAFT.value)

    result = plan_approval.batch_approve(
        db, "E_C", PLAN_TYPE_MONTH, [good.id, bad.id], comment="同意"
    )

    assert result["succeeded"] == [good.id]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["planId"] == bad.id
    assert result["failed"][0]["reason"]


# ===========================================================================
# 8. 序列化
# ===========================================================================


def test_serialize_month_plan_approval_item_shape(db, org):
    """月计划审批项字段与前端 `MonthPlanApprovalItem` 对齐。"""
    plan = _make_month_plan(db)

    data = plan_approval.serialize_month_plan_approval_item(db, plan)

    assert data["id"] == plan.id
    # ⚠️ BUG-R6-06：主键是飞书十六进制字符串，原样透出（前端类型也是 string）。
    #    旧断言 `isinstance(..., int)` 期望 int，转换失败会静默塌成 0。
    assert isinstance(data["ownerId"], str)
    assert data["ownerId"] == "E_A"
    assert data["employeeName"] == "员工甲"
    assert data["orgName"] == "业务部"
    # ⚠️ BUG-R6-08：后端内部状态是小写，前端 `approvalStatusLabels` 的键是大写
    #    （PENDING_APPROVAL / APPROVED / REJECTED）。不对齐则审批按钮永久禁用。
    assert data["status"] == "PENDING_APPROVAL"
    assert data["planYear"] == 2026
    assert data["planMonth"] == 11
    assert data["version"] == plan.version_no
    assert len(data["items"]) == 1
    assert data["items"][0]["taskName"] == "推进大客户签约"


def test_serialize_week_plan_summary_shape(db, org):
    """周计划摘要字段与前端 `WeekPlanSummary` 对齐。"""
    plan = _make_week_plan(db)

    data = plan_approval.serialize_week_plan_summary(db, plan)

    assert data["id"] == plan.id
    assert data["weekStart"] == "2026-11-09"
    assert data["weekEnd"] == "2026-11-15"
    assert data["status"] == "PENDING_APPROVAL"
    assert data["employeeName"] == "员工甲"
    assert data["departmentName"] == "业务部"
    assert data["itemCount"] == 0


def test_serialize_daily_review_item_shape(db, org):
    """日计划抽检项字段与前端 `DailyReviewItem` 对齐。"""
    plan = _make_day_plan(db)

    data = plan_approval.serialize_daily_review_item(db, plan)

    assert data["id"] == plan.id
    assert data["employeeName"] == "员工甲"
    assert data["orgName"] == "业务部"
    assert data["planDate"] == "2026-11-10"
    assert data["workContent"] == "完成需求评审"
    assert data["reviewStatus"] == "PENDING"
    assert data["riskLevel"] == "NONE"
    assert data["overdueApproval"] is False


def test_serialize_approval_item_dispatch_by_plan_type(db, org):
    """按计划类型分发到对应序列化器。"""
    month = _make_month_plan(db)
    week = _make_week_plan(db)
    day = _make_day_plan(db)

    assert "planYear" in plan_approval.serialize_approval_item(
        db, month, plan_type=PLAN_TYPE_MONTH
    )
    assert "weekStart" in plan_approval.serialize_approval_item(
        db, week, plan_type=PLAN_TYPE_WEEK
    )
    assert "planDate" in plan_approval.serialize_approval_item(
        db, day, plan_type=PLAN_TYPE_DAY
    )


def test_serialize_approval_history_ordered(db, org):
    """审批时间线按时间正序。"""
    plan = _make_month_plan(db)
    plan_approval.approve_month_plan(db, "E_C", plan.id, comment="同意")

    history = plan_approval.serialize_approval_history(db, PLAN_TYPE_MONTH, plan.id)

    assert len(history) == 1
    assert history[0]["action"] == PlanApprovalAction.APPROVE.value
    assert history[0]["actorName"] == "总监丙"
    assert history[0]["fromStatus"] == PlanStatus.SUBMITTED.value
    assert history[0]["toStatus"] == PlanStatus.APPROVED.value
