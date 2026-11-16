"""部门负责人侧计划审批 API 集成测试 —— A3 阶段（department.py 接线）。

与 A2 的 `test_plan_approval_api.py` 同构，覆盖：

1. **待批列表**：返回真实待审批计划，字段对齐前端 TS 类型。
2. **审批动作**：通过 / 驳回后状态落库 + 写审批流水。
3. **权限边界**：越权审批返回 403；未认证返回 401；不存在返回 404。
4. **状态守卫**：重复审批返回 409；乐观锁版本冲突返回 409。
5. **批量审批**：单条失败不影响其它条目。
6. **数据范围**：部门负责人可见管理范围内部门成员的计划，看不到范围外的。
7. **禁止自审批**：不能审批自己提交的计划（403）。
8. **日计划抽检语义**：抽检不改 `plan.status`。

⚠️ 部门负责人与主管的区别：
主管（`manager`）只能看**直属下属**；部门负责人（`department_leader`）
还能看**管理范围内部门（含下级部门）**的全部成员。
"""

from __future__ import annotations

from datetime import date, timedelta

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
    """组织结构。

    - `D_ROOT`（根部门，leader = E_LEAD）→ `D_SUB`（下级部门）
    - `D_ROOT` 成员：`E_A`（直属下属）、`E_B`（同部门但非直属）
    - `D_SUB` 成员：`E_C`（下级部门成员，**不是**直属下属）
    - `D_OTHER` 成员：`E_X`（完全在管理范围外）
    """
    d_root = Department(id="D_ROOT", name="业务部", active=True)
    d_sub = Department(id="D_SUB", name="业务部下属组", active=True, parent_id="D_ROOT")
    d_other = Department(id="D_OTHER", name="其它部", active=True)
    db.add_all([d_root, d_sub, d_other])
    db.flush()

    lead = Employee(
        id="E_LEAD", name="部门负责人", department_id="D_ROOT",
        employment_status="active", account_status="enabled",
    )
    emp_a = Employee(
        id="E_A", name="员工甲", department_id="D_ROOT",
        employment_status="active", account_status="enabled",
    )
    emp_b = Employee(
        id="E_B", name="员工乙", department_id="D_ROOT",
        employment_status="active", account_status="enabled",
    )
    emp_c = Employee(
        id="E_C", name="下级部门员工丙", department_id="D_SUB",
        employment_status="active", account_status="enabled",
    )
    outsider = Employee(
        id="E_X", name="外部员工", department_id="D_OTHER",
        employment_status="active", account_status="enabled",
    )
    db.add_all([lead, emp_a, emp_b, emp_c, outsider])
    db.flush()

    # E_A 是 E_LEAD 的直属下属；E_B / E_C 不是
    emp_a.manager_id = "E_LEAD"
    # E_LEAD 是 D_ROOT 的负责人 → 管理范围 = D_ROOT + D_SUB
    d_root.leader_id = "E_LEAD"
    db.flush()
    return {
        "lead": lead, "a": emp_a, "b": emp_b, "c": emp_c, "x": outsider,
    }


@pytest.fixture()
def client(db, org):
    """以部门负责人 E_LEAD 身份登录的 TestClient。"""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: org["lead"]
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


@pytest.fixture()
def outsider_client(db, org):
    """以完全在管理范围外的员工 E_X 身份登录。"""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: org["x"]
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _month_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value, month="2026-11"):
    plan = MonthPlan(owner_id=owner_id, plan_month=month, title="十一月计划", status=status)
    db.add(plan)
    db.flush()
    db.add(MonthPlanItem(plan_id=plan.id, task_name="推进签约", deliverable="合同", sort_no=0))
    db.flush()
    return plan


def _week_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value, week_start=None):
    week_start = week_start or date(2026, 11, 9)
    plan = WeekPlan(
        owner_id=owner_id,
        title="周计划",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


def _day_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value, plan_date=None):
    plan = DayPlan(
        owner_id=owner_id,
        plan_date=plan_date or date(2026, 11, 10),
        content="今日工作内容",
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


# ===========================================================================
# 1. 列表
# ===========================================================================


def test_month_plan_approvals_returns_real_data(client, db, org):
    """月计划审批列表返回真实计划，字段对齐前端 TS 类型。"""
    plan = _month_plan(db)

    resp = client.get("/v1/department/month-plan-approvals")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data) == 1
    item = data[0]
    assert item["id"] == plan.id
    assert item["employeeName"] == "员工甲"
    assert item["orgName"] == "业务部"
    assert item["planYear"] == 2026
    assert item["planMonth"] == 11
    assert item["status"] == "PENDING_APPROVAL"
    # ⚠️ BUG-R6-06：主键是飞书十六进制字符串，原样透出（前端类型也是 string）。
    assert isinstance(item["ownerId"], str)
    assert item["ownerId"] == "E_A"
    assert len(item["items"]) == 1


def test_department_leader_sees_managed_department_members(client, db, org):
    """部门负责人能看到管理范围内（含下级部门）成员的计划。

    ⚠️ 这是部门负责人与直属主管的关键区别：E_C 不是 E_LEAD 的直属下属，
    但属于下级部门 D_SUB，因此可见。
    """
    _month_plan(db, owner_id="E_A", month="2026-11")
    _month_plan(db, owner_id="E_B", month="2026-11")
    _month_plan(db, owner_id="E_C", month="2026-11")

    resp = client.get("/v1/department/month-plan-approvals")
    assert resp.status_code == 200, resp.text
    owners = {x["employeeName"] for x in resp.json()["data"]}

    assert "员工甲" in owners
    assert "员工乙" in owners
    assert "下级部门员工丙" in owners  # 下级部门成员


def test_department_leader_cannot_see_out_of_scope_employee(client, db, org):
    """管理范围外的员工计划不可见。"""
    _month_plan(db, owner_id="E_X", month="2026-11")
    _month_plan(db, owner_id="E_A", month="2026-11")

    resp = client.get("/v1/department/month-plan-approvals")
    assert resp.status_code == 200, resp.text
    owners = {x["employeeName"] for x in resp.json()["data"]}

    assert "外部员工" not in owners
    assert "员工甲" in owners


def test_outsider_sees_nothing(outsider_client, db, org):
    """管理范围外的员工看到空列表。"""
    _month_plan(db, owner_id="E_A", month="2026-11")
    _month_plan(db, owner_id="E_X", month="2026-11")

    resp = outsider_client.get("/v1/department/month-plan-approvals")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


def test_month_plan_approvals_page(client, db, org):
    """分页端点返回 items / total / pageNo / pageSize。"""
    _month_plan(db, month="2026-11")
    _month_plan(db, month="2026-12")

    resp = client.get("/v1/department/month-plan-approvals/page?pageNo=1&pageSize=1")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["pageNo"] == 1
    assert data["pageSize"] == 1
    assert data["submittedCount"] == 2


def test_month_plan_approvals_page_accepts_camel_case_params(client, db, org):
    """⚠️ 回归用例：此前用 snake_case，前端传的 camelCase 被静默忽略。

    分页永远返回第一页全部数据 —— 这正是 A2 在 `leader.py` 修过的存量 bug。
    """
    _month_plan(db, month="2026-11")
    _month_plan(db, month="2026-12")
    _month_plan(db, month="2027-01")

    resp = client.get("/v1/department/month-plan-approvals/page?pageNo=2&pageSize=1")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert data["pageNo"] == 2
    assert data["pageSize"] == 1


def test_leader_month_plan_approvals_page(client, db, org):
    """`leader-month-plan-approvals/page` 返回 LeaderMonthPlanApprovalPage。"""
    _month_plan(db, month="2026-11")

    resp = client.get("/v1/department/leader-month-plan-approvals/page?pageNo=1&pageSize=20")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["submittedCount"] == 1
    assert data["approvedCount"] == 0
    assert data["totalEmployeeCount"] == 1
    assert data["pageNo"] == 1
    assert data["pageSize"] == 20


def test_month_plan_approvals_page_filters_by_year_month(client, db, org):
    """前端传 planYear + planMonth 过滤，后端按 "2026-11" 精确匹配。"""
    _month_plan(db, month="2026-11")
    _month_plan(db, month="2026-12")

    resp = client.get(
        "/v1/department/month-plan-approvals/page?planYear=2026&planMonth=11"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total"] == 1
    assert data["items"][0]["planMonth"] == 11


def test_week_plan_approvals_returns_real_data(client, db, org):
    """周计划审批列表返回真实计划。"""
    plan = _week_plan(db)

    resp = client.get("/v1/department/week-plan-approvals")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data) == 1
    assert data[0]["id"] == plan.id
    assert data[0]["employeeName"] == "员工甲"
    assert data[0]["status"] == "PENDING_APPROVAL"
    assert data[0]["versionNo"] == 1


def test_daily_reviews_returns_real_data(client, db, org):
    """日计划抽检列表返回真实计划。"""
    plan = _day_plan(db)

    resp = client.get("/v1/department/daily-reviews")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data) == 1
    assert data[0]["id"] == plan.id
    assert data[0]["employeeName"] == "员工甲"
    assert data[0]["reviewStatus"] == "PENDING"


# ===========================================================================
# 2. 详情
# ===========================================================================


def test_month_plan_approval_detail(client, db, org):
    """月计划审批详情返回真实数据。"""
    plan = _month_plan(db)

    resp = client.get(f"/v1/department/month-plan-approvals/{plan.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["id"] == plan.id
    assert data["employeeName"] == "员工甲"
    assert data["orgName"] == "业务部"


def test_leader_month_plan_approval_detail(client, db, org):
    """部门负责人审批详情返回 `{plan, comparisonStatus}`。"""
    plan = _month_plan(db)

    resp = client.get(f"/v1/department/leader-month-plan-approvals/{plan.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["plan"]["id"] == plan.id
    assert data["plan"]["employeeName"] == "员工甲"
    assert data["comparisonStatus"] == "NONE"


def test_week_plan_approval_detail(client, db, org):
    """周计划审批详情返回真实数据。"""
    plan = _week_plan(db)

    resp = client.get(f"/v1/department/week-plan-approvals/{plan.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["id"] == plan.id
    assert data["employeeName"] == "员工甲"


def test_daily_review_detail(client, db, org):
    """日计划点评详情返回真实数据。"""
    plan = _day_plan(db)

    resp = client.get(f"/v1/department/daily-reviews/{plan.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["id"] == plan.id
    assert data["employeeName"] == "员工甲"


def test_day_plan_review_detail_has_department_fields(client, db, org):
    """部门侧日计划详情含 `DepartmentDayPlanReview` 专属字段。"""
    plan = _day_plan(db)

    resp = client.get(f"/v1/department/day-plan-reviews/{plan.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["id"] == plan.id
    assert data["employeeName"] == "员工甲"
    for field in ("leaderName", "departmentComment", "departmentReviewerName", "status"):
        assert field in data, f"缺少部门侧字段 {field}"


# ===========================================================================
# 3. 审批动作
# ===========================================================================


def test_approve_month_plan_persists_and_writes_flow(client, db, org):
    """通过月计划 → 状态落库 + 写审批流水。"""
    plan = _month_plan(db)

    resp = client.post(
        f"/v1/department/leader-month-plan-approvals/{plan.id}/approve",
        json={"comment": "同意"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "APPROVED"

    db.refresh(plan)
    assert plan.status == PlanStatus.APPROVED.value
    assert plan.approved_at is not None
    assert plan.approval_comment == "同意"

    rows = db.execute(select(PlanApproval)).scalars().all()
    assert len(rows) == 1
    assert rows[0].plan_type == "month"
    assert rows[0].action == "approve"
    assert rows[0].actor_id == "E_LEAD"
    assert rows[0].comment == "同意"


def test_reject_month_plan_persists(client, db, org):
    """驳回月计划 → 状态落库。"""
    plan = _month_plan(db)

    resp = client.post(
        f"/v1/department/leader-month-plan-approvals/{plan.id}/reject",
        json={"comment": "需要补充"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "REJECTED"

    db.refresh(plan)
    assert plan.status == PlanStatus.REJECTED.value


def test_approve_week_plan_persists(client, db, org):
    """通过周计划 → 状态落库。"""
    plan = _week_plan(db)

    resp = client.post(
        f"/v1/department/week-plan-approvals/{plan.id}/approve",
        json={"comment": "同意"},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.APPROVED.value
    assert plan.approve_at is not None


def test_reject_week_plan_persists(client, db, org):
    """驳回周计划 → 状态落库。"""
    plan = _week_plan(db)

    resp = client.post(
        f"/v1/department/week-plan-approvals/{plan.id}/reject",
        json={"comment": "不通过"},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.REJECTED.value


def test_day_plan_review_does_not_change_plan_status(client, db, org):
    """⚠️ 日计划抽检：只写 review_status，**不改** plan.status（决策 D7）。"""
    plan = _day_plan(db)

    resp = client.post(
        f"/v1/department/day-plan-reviews/{plan.id}/approve",
        json={"comment": "抽检通过"},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.SUBMITTED.value  # 状态不变
    assert plan.review_status == "APPROVED"
    assert plan.reviewed_at is not None
    assert plan.leader_comment == "抽检通过"


def test_daily_review_comment_writes_review_fields(client, db, org):
    """日计划点评 → 写 review_status / reviewed_at / leader_comment。"""
    plan = _day_plan(db)

    resp = client.post(
        f"/v1/department/daily-reviews/{plan.id}/comment",
        json={"comment": "写得不错"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "COMMENTED"

    db.refresh(plan)
    assert plan.status == PlanStatus.SUBMITTED.value
    assert plan.review_status == "APPROVED"
    assert plan.leader_comment == "写得不错"


def test_reviewed_day_plan_leaves_pending_list(client, db, org):
    """抽检过的日计划不再出现在待抽检列表。"""
    reviewed = _day_plan(db, plan_date=date(2026, 11, 10))
    reviewed.review_status = "APPROVED"
    db.flush()
    _day_plan(db, plan_date=date(2026, 11, 11))

    resp = client.get("/v1/department/daily-reviews")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert len(data) == 1
    assert data[0]["planDate"] == "2026-11-11"


# ===========================================================================
# 4. 权限边界
# ===========================================================================


def test_unauthenticated_returns_401(anon_client, db, org):
    """未认证请求返回 401。"""
    plan = _month_plan(db)

    for method, path in [
        ("get", "/v1/department/month-plan-approvals"),
        ("get", "/v1/department/week-plan-approvals"),
        ("get", "/v1/department/daily-reviews"),
        ("get", f"/v1/department/month-plan-approvals/{plan.id}"),
        ("post", f"/v1/department/leader-month-plan-approvals/{plan.id}/approve"),
    ]:
        resp = anon_client.request(method, path, json={} if method == "post" else None)
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


def test_outsider_cannot_approve_returns_403(outsider_client, db, org):
    """越权审批返回 403。"""
    plan = _month_plan(db, owner_id="E_A")

    resp = outsider_client.post(
        f"/v1/department/leader-month-plan-approvals/{plan.id}/approve",
        json={},
    )
    assert resp.status_code == 403, resp.text


def test_outsider_cannot_view_detail_returns_403(outsider_client, db, org):
    """越权查看详情返回 403。"""
    plan = _month_plan(db, owner_id="E_A")

    resp = outsider_client.get(f"/v1/department/month-plan-approvals/{plan.id}")
    assert resp.status_code == 403, resp.text


def test_missing_plan_returns_404(client, db, org):
    """计划不存在返回 404。"""
    resp = client.post(
        "/v1/department/leader-month-plan-approvals/不存在的ID/approve",
        json={},
    )
    assert resp.status_code == 404, resp.text


def test_cannot_approve_own_plan_returns_403(db, org):
    """⚠️ 禁止自审批：部门负责人不能审批自己提交的计划。

    回归用例：此前 `can_access_employee` 对「目标 == 当前用户」返回 True，
    若只靠路由层权限校验，负责人能审批自己的计划（actor_id == owner_id）。
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: org["lead"]
    with TestClient(app) as c:
        plan = _month_plan(db, owner_id="E_LEAD")

        resp = c.post(
            f"/v1/department/leader-month-plan-approvals/{plan.id}/approve",
            json={},
        )
        assert resp.status_code == 403, resp.text

        # 状态未被改动，也没有流水
        db.refresh(plan)
        assert plan.status == PlanStatus.SUBMITTED.value
        count = db.execute(select(func.count()).select_from(PlanApproval)).scalar_one()
        assert count == 0
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


# ===========================================================================
# 5. 状态守卫 / 乐观锁
# ===========================================================================


def test_double_approval_returns_409(client, db, org):
    """重复审批返回 409。"""
    plan = _month_plan(db)

    first = client.post(
        f"/v1/department/leader-month-plan-approvals/{plan.id}/approve",
        json={},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/v1/department/leader-month-plan-approvals/{plan.id}/approve",
        json={},
    )
    assert second.status_code == 409, second.text


def test_version_mismatch_returns_409(client, db, org):
    """乐观锁：`versionNo` 不匹配返回 409。"""
    plan = _month_plan(db)

    resp = client.post(
        f"/v1/department/leader-month-plan-approvals/{plan.id}/approve",
        json={"versionNo": 999},
    )
    assert resp.status_code == 409, resp.text


def test_matching_version_accepted(client, db, org):
    """`versionNo` 与服务端一致 → 审批成功。"""
    plan = _month_plan(db)

    resp = client.post(
        f"/v1/department/leader-month-plan-approvals/{plan.id}/approve",
        json={"versionNo": plan.version_no},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.APPROVED.value


# ===========================================================================
# 6. 批量审批
# ===========================================================================


def test_batch_approve_mixed_results(client, db, org):
    """批量审批：合法条目成功，非法条目标记 FAILED，不影响其它。"""
    good = _month_plan(db, month="2026-11")
    bad = _month_plan(db, month="2026-12", status=PlanStatus.DRAFT.value)

    resp = client.post(
        "/v1/department/leader-month-plan-approvals/batch-approve",
        json={"ids": [good.id, bad.id]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    statuses = {r["objectId"]: r["status"] for r in data}
    assert statuses[good.id] == "APPROVED"
    assert statuses[bad.id] == "FAILED"

    db.refresh(good)
    assert good.status == PlanStatus.APPROVED.value


def test_batch_reject_persists(client, db, org):
    """批量驳回 → 状态落库。"""
    plan = _month_plan(db)

    resp = client.post(
        "/v1/department/leader-month-plan-approvals/batch-reject",
        json={"ids": [plan.id]},
    )
    assert resp.status_code == 200, resp.text

    db.refresh(plan)
    assert plan.status == PlanStatus.REJECTED.value


def test_batch_approve_writes_one_flow_per_plan(client, db, org):
    """批量审批每条成功记录写一条流水。"""
    p1 = _month_plan(db, month="2026-11")
    p2 = _month_plan(db, month="2026-12")

    client.post(
        "/v1/department/leader-month-plan-approvals/batch-approve",
        json={"ids": [p1.id, p2.id]},
    )

    count = db.execute(select(func.count()).select_from(PlanApproval)).scalar_one()
    assert count == 2


def test_batch_comment_daily_reviews(client, db, org):
    """批量日计划点评。"""
    p1 = _day_plan(db, plan_date=date(2026, 11, 10))
    p2 = _day_plan(db, plan_date=date(2026, 11, 11))

    resp = client.post(
        "/v1/department/daily-reviews/batch-comment",
        json={"ids": [p1.id, p2.id], "comment": "抽检通过"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert all(r["status"] == "APPROVED" for r in data)

    db.refresh(p1)
    assert p1.review_status == "APPROVED"
    assert p1.status == PlanStatus.SUBMITTED.value  # 抽检不改状态


# ===========================================================================
# 7. 兼容旧路径
# ===========================================================================


def test_legacy_paths_still_work(client, db, org):
    """兼容旧路径（前端已切换，但保留以防外部调用）。"""
    _month_plan(db)
    _week_plan(db)
    _day_plan(db)

    resp = client.get("/v1/department/plan-approval")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 1

    resp = client.get("/v1/department/week-plan-approval")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 1

    resp = client.get("/v1/department/daily-review")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 1
