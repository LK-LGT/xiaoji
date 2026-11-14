"""计划审批 API 集成测试 —— A2 阶段（leader.py 接线）。

覆盖：
1. **待批列表**：返回真实待审批计划，字段对齐前端 TS 类型。
2. **审批动作**：通过 / 驳回后状态落库 + 写审批流水。
3. **权限边界**：越权审批返回 403；未认证返回 401。
4. **状态守卫**：重复审批返回 409。
5. **批量审批**：单条失败不影响其它条目。
6. **数据范围**：看不到范围外员工的计划。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import PlanStatus
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
from backend.app.main import app

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
    """D_1 部门：员工甲（归属人）、主管丙（审批人）；D_2：外部员工。"""
    d1 = Department(id="D_1", name="业务部", active=True)
    d2 = Department(id="D_2", name="技术部", active=True)
    db.add_all([d1, d2])
    db.flush()

    emp_a = Employee(
        id="E_A", name="员工甲", department_id="D_1",
        employment_status="active", account_status="enabled",
    )
    leader = Employee(
        id="E_C", name="总监丙", department_id="D_1",
        employment_status="active", account_status="enabled",
    )
    outsider = Employee(
        id="E_X", name="外部员工", department_id="D_2",
        employment_status="active", account_status="enabled",
    )
    db.add_all([emp_a, leader, outsider])
    db.flush()
    # 让员工甲成为总监丙的直属下属
    emp_a.manager_id = "E_C"
    db.flush()
    return {"a": emp_a, "c": leader, "x": outsider}


@pytest.fixture()
def client(db, org):
    """以总监丙身份登录的 TestClient。"""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: org["c"]
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def anon_client(db):
    """不带认证的 TestClient。"""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _month_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value, month="2026-11"):
    plan = MonthPlan(owner_id=owner_id, plan_month=month, title="十一月计划", status=status)
    db.add(plan)
    db.flush()
    db.add(MonthPlanItem(plan_id=plan.id, task_name="推进签约", deliverable="合同", sort_no=0))
    db.flush()
    return plan


def _week_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value):
    from datetime import date, timedelta

    plan = WeekPlan(
        owner_id=owner_id,
        title="周计划",
        week_start=date(2026, 11, 9),
        week_end=date(2026, 11, 9) + timedelta(days=6),
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


def _day_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value):
    from datetime import date

    plan = DayPlan(
        owner_id=owner_id,
        plan_date=date(2026, 11, 10),
        content="完成需求评审",
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


# ===========================================================================
# 1. 待批列表
# ===========================================================================


def test_pending_month_plans_returns_real_data(client, db, org):
    """待批列表返回真实计划，字段对齐前端 TS 类型。"""
    plan = _month_plan(db)

    resp = client.get("/v1/leader/month-plan-approvals")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data) == 1
    item = data[0]
    assert item["id"] == plan.id
    assert item["employeeName"] == "员工甲"
    assert item["orgName"] == "业务部"
    assert item["planYear"] == 2026
    assert item["planMonth"] == 11
    assert item["status"] == PlanStatus.SUBMITTED.value
    assert isinstance(item["ownerId"], int)
    assert len(item["items"]) == 1


def test_pending_month_plans_page(client, db, org):
    """分页端点返回 items / total / pageNo / pageSize。"""
    _month_plan(db, month="2026-11")
    _month_plan(db, month="2026-12")

    resp = client.get("/v1/leader/month-plan-approvals/page?pageNo=1&pageSize=1")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["pageNo"] == 1
    assert data["pageSize"] == 1
    assert data["submittedCount"] == 2


def test_pending_month_plans_page_accepts_camel_case_params(client, db, org):
    """分页参数用 camelCase（pageNo / pageSize），与前端实际发送的一致。

    ⚠️ 回归用例：此前路由用 snake_case（page_no / page_size），
    前端传的 pageNo / pageSize 被静默忽略，分页永远返回第一页全部数据。
    """
    _month_plan(db, month="2026-11")
    _month_plan(db, month="2026-12")
    _month_plan(db, month="2026-13" if False else "2027-01")

    resp = client.get("/v1/leader/month-plan-approvals/page?pageNo=2&pageSize=1")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert data["pageNo"] == 2


def test_pending_month_plans_page_filters_by_year_month(client, db, org):
    """前端传 planYear + planMonth 过滤，后端按 "2026-11" 精确匹配。"""
    _month_plan(db, month="2026-11")
    _month_plan(db, month="2026-12")

    resp = client.get(
        "/v1/leader/month-plan-approvals/page?planYear=2026&planMonth=11"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total"] == 1
    assert data["items"][0]["planMonth"] == 11


def test_pending_week_plans_returns_real_data(client, db, org):
    plan = _week_plan(db)

    resp = client.get("/v1/leader/week-plan-approvals")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data) == 1
    assert data[0]["id"] == plan.id
    assert data[0]["weekStart"] == "2026-11-09"
    assert data[0]["weekEnd"] == "2026-11-15"
    assert data[0]["employeeName"] == "员工甲"


def test_pending_daily_reviews_returns_real_data(client, db, org):
    plan = _day_plan(db)

    resp = client.get("/v1/leader/daily-reviews")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data) == 1
    assert data[0]["id"] == plan.id
    assert data[0]["workContent"] == "完成需求评审"
    assert data[0]["reviewStatus"] == "PENDING"


def test_pending_list_excludes_out_of_scope_owner(client, db, org):
    """范围外员工的计划不可见。"""
    _month_plan(db, owner_id="E_X")

    resp = client.get("/v1/leader/month-plan-approvals")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


def test_pending_list_excludes_non_submitted(client, db, org):
    """非 submitted 状态不进待批列表。"""
    _month_plan(db, status=PlanStatus.DRAFT.value)

    resp = client.get("/v1/leader/month-plan-approvals")
    assert resp.json()["data"] == []


# ===========================================================================
# 2. 审批动作
# ===========================================================================


def test_approve_month_plan_via_api(client, db, org):
    """通过月计划 → 状态落库 + 写流水。"""
    plan = _month_plan(db)

    resp = client.post(
        f"/v1/leader/month-plan-approvals/{plan.id}/approve",
        json={"comment": "同意"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "APPROVED"

    db.refresh(plan)
    assert plan.status == PlanStatus.APPROVED.value
    assert plan.approval_comment == "同意"
    assert plan.approved_at is not None

    row = db.execute(select(PlanApproval)).scalar_one()
    assert row.action == "approve"
    assert row.actor_id == "E_C"


def test_reject_month_plan_via_api(client, db, org):
    plan = _month_plan(db)

    resp = client.post(
        f"/v1/leader/month-plan-approvals/{plan.id}/reject",
        json={"comment": "目标不清晰"},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.REJECTED.value
    assert plan.approval_comment == "目标不清晰"


def test_approve_week_plan_via_api(client, db, org):
    plan = _week_plan(db)

    resp = client.post(f"/v1/leader/week-plan-approvals/{plan.id}/approve", json={})
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.APPROVED.value
    assert plan.approve_at is not None


def test_comment_daily_review_via_api(client, db, org):
    """日计划点评走抽检语义：改 review_status，不改 plan.status。"""
    plan = _day_plan(db)

    resp = client.post(
        f"/v1/leader/daily-reviews/{plan.id}/comment",
        json={"comment": "抽查通过"},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.SUBMITTED.value
    assert plan.review_status == "APPROVED"
    assert plan.leader_comment == "抽查通过"


def test_approval_detail_endpoint(client, db, org):
    """审批详情端点返回真实计划。"""
    plan = _month_plan(db)

    resp = client.get(f"/v1/leader/month-plan-approvals/{plan.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["plan"]["id"] == plan.id
    assert data["comparisonStatus"] == "NONE"


# ===========================================================================
# 3. 权限边界
# ===========================================================================


def test_approval_requires_authentication(anon_client, db, org):
    """未认证返回 401。"""
    plan = _month_plan(db)

    resp = anon_client.post(f"/v1/leader/month-plan-approvals/{plan.id}/approve", json={})
    assert resp.status_code == 401


def test_cannot_approve_out_of_scope_plan(client, db, org):
    """越权审批返回 403。"""
    plan = _month_plan(db, owner_id="E_X")

    resp = client.post(f"/v1/leader/month-plan-approvals/{plan.id}/approve", json={})
    assert resp.status_code == 403, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.SUBMITTED.value


def test_approval_of_nonexistent_plan_returns_404(client, db, org):
    """计划不存在返回 404。"""
    resp = client.post("/v1/leader/month-plan-approvals/NOPE/approve", json={})
    assert resp.status_code == 404


# ===========================================================================
# 4. 状态守卫
# ===========================================================================


def test_double_approval_returns_409(client, db, org):
    """重复审批返回 409。"""
    plan = _month_plan(db)

    first = client.post(f"/v1/leader/month-plan-approvals/{plan.id}/approve", json={})
    assert first.status_code == 200

    second = client.post(f"/v1/leader/month-plan-approvals/{plan.id}/approve", json={})
    assert second.status_code == 409


def test_approving_draft_returns_409(client, db, org):
    """草稿态审批返回 409。"""
    plan = _month_plan(db, status=PlanStatus.DRAFT.value)

    resp = client.post(f"/v1/leader/month-plan-approvals/{plan.id}/approve", json={})
    assert resp.status_code == 409


def test_stale_version_no_returns_409(client, db, org):
    """版本号过期返回 409（两个主管同时审批的场景）。

    前端 `weekPlan.ts` 的审批接口会带 `versionNo`。
    """
    plan = _month_plan(db)

    resp = client.post(
        f"/v1/leader/month-plan-approvals/{plan.id}/approve",
        json={"versionNo": 999},
    )
    assert resp.status_code == 409, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.SUBMITTED.value


def test_matching_version_no_succeeds(client, db, org):
    """版本号一致 → 审批成功。"""
    plan = _month_plan(db)
    version = plan.version_no

    resp = client.post(
        f"/v1/leader/month-plan-approvals/{plan.id}/approve",
        json={"versionNo": version},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.APPROVED.value


def test_week_plan_stale_version_no_returns_409(client, db, org):
    """周计划审批同样校验版本号。"""
    plan = _week_plan(db)

    resp = client.post(
        f"/v1/leader/week-plan-approvals/{plan.id}/approve",
        json={"versionNo": 999},
    )
    assert resp.status_code == 409, resp.text


# ===========================================================================
# 5. 批量审批
# ===========================================================================


def test_batch_approve_mixed_results(client, db, org):
    """批量审批：合法条目成功，非法条目标记 FAILED，不影响其它。"""
    good = _month_plan(db, month="2026-11")
    bad = _month_plan(db, month="2026-12", status=PlanStatus.DRAFT.value)

    resp = client.post(
        "/v1/leader/month-plan-approvals/batch-approve",
        json={"ids": [good.id, bad.id]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    statuses = {r["objectId"]: r["status"] for r in data}
    assert statuses[good.id] == "APPROVED"
    assert statuses[bad.id] == "FAILED"

    db.refresh(good)
    assert good.status == PlanStatus.APPROVED.value


def test_batch_approve_writes_one_flow_per_plan(client, db, org):
    """批量审批每条成功记录写一条流水。"""
    p1 = _month_plan(db, month="2026-11")
    p2 = _month_plan(db, month="2026-12")

    client.post(
        "/v1/leader/month-plan-approvals/batch-approve",
        json={"ids": [p1.id, p2.id]},
    )

    count = db.execute(select(func.count()).select_from(PlanApproval)).scalar_one()
    assert count == 2


# ===========================================================================
# 6. 日计划抽检列表行为
# ===========================================================================


def test_reviewed_day_plan_leaves_pending_list(client, db, org):
    """抽检过的日计划不再出现在待抽检列表。"""
    plan = _day_plan(db)

    client.post(f"/v1/leader/daily-reviews/{plan.id}/comment", json={"comment": "通过"})

    resp = client.get("/v1/leader/daily-reviews")
    assert resp.json()["data"] == []
