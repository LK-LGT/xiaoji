"""计划域验收测试 —— 月 / 周 / 日计划的 CRUD、状态机、乐观锁、权限边界。

覆盖：
1. **CRUD**：月计划草稿的新建 / 更新 / 清空 / 条目删除；
   周计划的创建 / 更新 / 删除；日计划的草稿 / 提交 / 删除。
2. **状态机**：合法流转（草稿 → 已提交 → 撤回）、非法流转拒绝（409）。
3. **乐观锁**：versionNo 不匹配返回 409。
4. **数据范围**：跨人访问返回 404，不泄露他人计划。
5. **层级关联**：周计划条目关联月计划条目；日计划条目关联周计划条目。
6. **鉴权**：无 token 返回 401。

测试用内存 SQLite，每个用例独立建表。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import PlanStatus
from backend.app.domain.models import (
    Base,
    DayPlan,
    DayPlanItem,
    Department,
    Employee,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
    WeekPlanItem,
)
from backend.app.main import app
from backend.app.services import planning


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """独立的内存 SQLite 会话。"""
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
    """两名员工 + 部门。A 是普通员工，B 是另一名员工（用于越权测试）。"""
    dept = Department(id="D_1", name="业务部", active=True)
    db.add(dept)
    db.flush()
    emp_a = Employee(
        id="E_A",
        name="员工甲",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
    )
    emp_b = Employee(
        id="E_B",
        name="员工乙",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([emp_a, emp_b])
    db.flush()
    return {"a": emp_a, "b": emp_b, "dept": dept}


@pytest.fixture()
def client(db, org):
    """绑定内存 DB 与当前用户的 TestClient。"""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: org["a"]
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def _month_item_payload(**overrides) -> dict:
    base = {
        "taskName": "完成 Q3 销售目标",
        "taskContent": "推进大客户签约",
        "deliverable": "合同扫描件",
        "quality": "符合验收标准",
        "performanceWeight": 40,
        "estimatedValue": 100,
        "estimatedStartDate": "2026-11-01",
        "estimatedEndDate": "2026-11-30",
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. 月计划 CRUD
# ===========================================================================


def test_create_month_plan_draft_persists(client, db):
    """新建月计划草稿 → 落库 + 返回完整详情。"""
    resp = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["planMonth"] == "2026-11"
    assert data["status"] == PlanStatus.DRAFT.value
    assert data["employeeName"] == "员工甲"
    assert data["departmentName"] == "业务部"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["taskName"] == "完成 Q3 销售目标"
    assert item["performanceWeight"] == 40
    assert item["estimatedStartDate"] == "2026-11-01"

    # 真正落库
    plan = db.execute(select(MonthPlan)).scalar_one()
    assert plan.owner_id == "E_A"
    assert plan.plan_month == "2026-11"
    assert db.execute(select(MonthPlanItem)).scalars().all()


def test_month_plan_detail_returns_saved_items(client):
    """草稿保存后再次读取，条目应一致。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    ).json()["data"]
    plan_id = created["id"]

    detail = client.get(f"/v1/employee/month-plans/{plan_id}").json()["data"]
    assert detail["id"] == plan_id
    assert len(detail["items"]) == 1
    assert detail["items"][0]["taskName"] == "完成 Q3 销售目标"


def test_update_month_plan_draft_replaces_items(client):
    """更新草稿：带 id 的条目更新，不带 id 的新增。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    ).json()["data"]
    plan_id = created["id"]
    item_id = created["items"][0]["id"]

    resp = client.post(
        f"/v1/employee/month-plans/{plan_id}/draft",
        json={
            "planMonth": "2026-11",
            "items": [
                {**_month_item_payload(taskName="改名后的任务"), "id": item_id},
                _month_item_payload(taskName="新增任务"),
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert len(items) == 2
    names = {i["taskName"] for i in items}
    assert names == {"改名后的任务", "新增任务"}


def test_clear_month_plan_draft_keeps_plan_keeps_id_null(client):
    """清空草稿：条目清空，id 返回 null（前端 EmployeeMonthPlanDraftClearResp）。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    ).json()["data"]
    plan_id = created["id"]

    resp = client.post(f"/v1/employee/month-plans/{plan_id}/draft/clear")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["id"] is None
    assert data["items"] == []


def test_delete_month_plan_item(client, db):
    """删除月计划条目。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    ).json()["data"]
    plan_id, item_id = created["id"], created["items"][0]["id"]

    resp = client.delete(f"/v1/employee/month-plans/{plan_id}/items/{item_id}")
    assert resp.status_code == 200, resp.text
    assert db.execute(select(MonthPlanItem)).scalars().all() == []


def test_month_plan_template_preview(client):
    """模板预览：行号 + 预估价值合计。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={
            "planMonth": "2026-11",
            "items": [
                _month_item_payload(estimatedValue=30),
                _month_item_payload(estimatedValue=70),
            ],
        },
    ).json()["data"]

    resp = client.get(f"/v1/employee/month-plans/{created['id']}/template-preview")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["planId"] == created["id"]
    assert data["planMonth"] == "2026-11"
    assert len(data["items"]) == 2
    assert data["items"][0]["rowNo"] == 1
    assert data["items"][1]["rowNo"] == 2
    assert data["estimatedValueTotal"] == 100


# ===========================================================================
# 2. 月计划状态机
# ===========================================================================


def test_month_plan_submit_then_withdraw(client):
    """草稿 → 已提交 → 撤回（回到草稿）。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    ).json()["data"]
    plan_id = created["id"]

    submitted = client.post(f"/v1/employee/month-plans/{plan_id}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["data"]["status"] == PlanStatus.SUBMITTED.value
    assert submitted.json()["data"]["submittedAt"]

    withdrawn = client.post(f"/v1/employee/month-plans/{plan_id}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["data"]["status"] == PlanStatus.DRAFT.value


def test_month_plan_cannot_submit_twice(client):
    """已提交状态再次提交 → 409。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": []},
    ).json()["data"]
    plan_id = created["id"]
    assert client.post(f"/v1/employee/month-plans/{plan_id}/submit").status_code == 200

    resp = client.post(f"/v1/employee/month-plans/{plan_id}/submit")
    assert resp.status_code == 409
    assert "不允许的状态流转" in resp.json()["detail"]


def test_month_plan_submitted_cannot_edit(client):
    """已提交状态不允许再编辑 → 409。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    ).json()["data"]
    plan_id = created["id"]
    client.post(f"/v1/employee/month-plans/{plan_id}/submit")

    resp = client.post(
        f"/v1/employee/month-plans/{plan_id}/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    )
    assert resp.status_code == 409


def test_month_plan_cannot_withdraw_from_draft(client):
    """草稿状态不能撤回（非法流转）→ 409。"""
    created = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": []},
    ).json()["data"]

    resp = client.post(f"/v1/employee/month-plans/{created['id']}/withdraw")
    assert resp.status_code == 409


# ===========================================================================
# 3. 周计划 CRUD + 状态机
# ===========================================================================


def test_create_week_plan(client, db):
    """创建周计划：week_start 自动补出 week_end（周一~周日）。"""
    resp = client.post(
        "/v1/employee/week-plans",
        json={
            "weekStart": "2026-11-09",
            "title": "第 46 周计划",
            "items": [{"content": "完成月计划第一项", "sortNo": 0}],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["summary"]["title"] == "第 46 周计划"
    assert data["summary"]["weekStart"] == "2026-11-09"
    assert data["summary"]["weekEnd"] == "2026-11-15"
    assert data["summary"]["status"] == "DRAFT"
    assert data["summary"]["employeeName"] == "员工甲"
    assert data["summary"]["departmentName"] == "业务部"
    assert data["summary"]["itemCount"] == 1
    assert len(data["items"]) == 1

    plan = db.execute(select(WeekPlan)).scalar_one()
    assert plan.week_end == date(2026, 11, 15)


def test_list_week_plans_filters_by_status(client):
    """列表按状态过滤。"""
    client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-09", "items": [{"content": "A"}]},
    )
    client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-16", "items": [{"content": "B"}]},
    )
    ids = [p["id"] for p in client.get("/v1/employee/week-plans").json()["data"]]
    assert len(ids) == 2

    # 提交其中一个后再按状态过滤
    client.post(f"/v1/employee/week-plans/{ids[0]}/submit")
    pending = client.get("/v1/employee/week-plans", params={"status": "PENDING"}).json()["data"]
    assert len(pending) == 1
    assert pending[0]["id"] == ids[0]

    drafts = client.get("/v1/employee/week-plans", params={"status": "DRAFT"}).json()["data"]
    assert len(drafts) == 1
    assert drafts[0]["id"] == ids[1]


def test_update_week_plan(client):
    """更新周计划内容。"""
    created = client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-09", "items": [{"content": "原内容"}]},
    ).json()["data"]
    plan_id = created["summary"]["id"]

    resp = client.put(
        f"/v1/employee/week-plans/{plan_id}",
        json={"weekStart": "2026-11-09", "items": [{"content": "新内容"}]},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["content"] == "新内容"


def test_delete_week_plan_only_in_draft(client, db):
    """草稿可删除；已提交不可删除（409）。"""
    created = client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-09", "items": [{"content": "x"}]},
    ).json()["data"]
    plan_id = created["summary"]["id"]

    client.post(f"/v1/employee/week-plans/{plan_id}/submit")
    assert (
        client.delete(f"/v1/employee/week-plans/{plan_id}").status_code == 409
    )

    client.post(f"/v1/employee/week-plans/{plan_id}/withdraw")
    resp = client.delete(f"/v1/employee/week-plans/{plan_id}")
    assert resp.status_code == 200, resp.text
    assert db.execute(select(WeekPlan)).scalars().all() == []


def test_week_plan_version_conflict_returns_409(client):
    """versionNo 不匹配 → 409。"""
    created = client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-09", "items": [{"content": "x"}]},
    ).json()["data"]
    plan_id = created["summary"]["id"]
    current = created["summary"]["versionNo"]

    # 正确版本号 → 成功
    assert (
        client.post(
            f"/v1/employee/week-plans/{plan_id}/submit", params={"versionNo": current}
        ).status_code
        == 200
    )
    # 过期版本号 → 409
    resp = client.post(
        f"/v1/employee/week-plans/{plan_id}/withdraw", params={"versionNo": current}
    )
    assert resp.status_code == 409
    assert "版本冲突" in resp.json()["detail"]


def test_week_plan_submit_withdraw_cycle(client):
    """周计划 草稿 → PENDING → 撤回 → DRAFT。"""
    created = client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-09", "items": [{"content": "x"}]},
    ).json()["data"]
    plan_id = created["summary"]["id"]

    submitted = client.post(f"/v1/employee/week-plans/{plan_id}/submit")
    assert submitted.json()["data"]["status"] == "PENDING"

    withdrawn = client.post(f"/v1/employee/week-plans/{plan_id}/withdraw")
    assert withdrawn.json()["data"]["status"] == "DRAFT"


# ===========================================================================
# 4. 日计划 CRUD + 状态机
# ===========================================================================


def test_day_plan_draft_create_and_read(client, db):
    """日计划草稿：按日期唯一，同日再次保存是更新而非新建。"""
    resp = client.post(
        "/v1/employee/day-plans/draft",
        json={"planDate": "2026-11-14", "content": "今天要做的事", "remark": "备注"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["planDate"] == "2026-11-14"
    assert data["content"] == "今天要做的事"
    assert data["status"] == PlanStatus.DRAFT.value
    assert data["calendarRule"]["ruleType"] == "WORKDAY"

    plan = db.execute(select(DayPlan)).scalar_one()
    assert plan.owner_id == "E_A"

    # 同日再次保存 → 仍是同一条
    again = client.post(
        "/v1/employee/day-plans/draft",
        json={"planDate": "2026-11-14", "content": "改过了", "remark": ""},
    ).json()["data"]
    assert again["id"] == data["id"]
    assert again["content"] == "改过了"
    assert len(db.execute(select(DayPlan)).scalars().all()) == 1


def test_day_plan_detail_before_creation(client):
    """当天没有计划时，详情返回空壳但结构完整。"""
    resp = client.get("/v1/employee/day-plans/detail", params={"date": "2026-11-20"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["id"] == 0
    assert data["planDate"] == "2026-11-20"
    assert data["content"] == ""
    assert data["status"] == PlanStatus.DRAFT.value
    assert data["weekPlanItemOptions"] == []


def test_day_plan_submit_and_withdraw(client):
    """日计划 草稿 → 已提交 → 撤回。"""
    created = client.post(
        "/v1/employee/day-plans/draft",
        json={"planDate": "2026-11-14", "content": "x", "remark": ""},
    ).json()["data"]

    submitted = client.post(
        "/v1/employee/day-plans/submit",
        json={"planDate": "2026-11-14", "content": "x", "remark": ""},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["data"]["status"] == PlanStatus.SUBMITTED.value
    assert submitted.json()["data"]["submittedAt"]

    withdrawn = client.post(f"/v1/employee/day-plans/{created['id']}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["data"]["status"] == PlanStatus.DRAFT.value


def test_day_plan_delete(client, db):
    """删除日计划草稿。"""
    created = client.post(
        "/v1/employee/day-plans/draft",
        json={"planDate": "2026-11-14", "content": "x", "remark": ""},
    ).json()["data"]

    resp = client.delete(f"/v1/employee/day-plans/{created['id']}")
    assert resp.status_code == 200, resp.text
    assert db.execute(select(DayPlan)).scalars().all() == []


def test_day_plan_invalid_date_returns_400(client):
    """日期格式非法 → 400。"""
    resp = client.get("/v1/employee/day-plans/detail", params={"date": "not-a-date"})
    assert resp.status_code == 400


# ===========================================================================
# 5. 层级关联：月 → 周 → 日
# ===========================================================================


def test_week_plan_item_links_month_plan_item(client, db):
    """周计划条目可关联月计划条目（父选项接口）。"""
    month = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": [_month_item_payload()]},
    ).json()["data"]
    month_item_id = month["items"][0]["id"]

    # 父选项应包含该月计划条目
    options = client.get("/v1/employee/week-plans/parent-options").json()["data"]
    assert len(options) == 1
    assert options[0]["monthPlanItemId"] == month_item_id
    assert options[0]["taskName"] == "完成 Q3 销售目标"
    assert options[0]["planMonth"] == "2026-11"

    # 建周计划并关联
    week = client.post(
        "/v1/employee/week-plans",
        json={
            "weekStart": "2026-11-09",
            "items": [{"content": "推进任务", "monthPlanItemId": month_item_id}],
        },
    ).json()["data"]
    assert week["items"][0]["monthPlanItemId"] == month_item_id
    assert week["items"][0]["parent"]["taskName"] == "完成 Q3 销售目标"

    # 已被引用的月计划条目不再出现在父选项里
    options_after = client.get("/v1/employee/week-plans/parent-options").json()["data"]
    assert len(options_after) == 0


def test_day_plan_item_links_week_plan_item(client, db):
    """日计划条目可关联周计划条目。"""
    week = client.post(
        "/v1/employee/week-plans",
        json={
            "weekStart": "2026-11-09",
            "items": [{"content": "周任务一"}, {"content": "周任务二"}],
        },
    ).json()["data"]
    week_item_id = week["items"][0]["id"]

    # 详情接口给出可选项
    detail = client.get(
        "/v1/employee/day-plans/detail", params={"date": "2026-11-11"}
    ).json()["data"]
    assert len(detail["weekPlanItemOptions"]) == 2
    assert detail["weekPlanItemOptions"][0]["id"] == week_item_id

    # 草稿保存关联
    saved = client.post(
        "/v1/employee/day-plans/draft",
        json={
            "planDate": "2026-11-11",
            "content": "日任务",
            "remark": "",
            "relatedWeekPlanItemIds": [week_item_id],
        },
    ).json()["data"]
    assert saved["relatedWeekPlanItemIds"] == [week_item_id]

    item = db.execute(select(DayPlanItem)).scalar_one()
    assert item.week_plan_item_id == week_item_id


# ===========================================================================
# 6. 数据范围（越权）
# ===========================================================================


def test_cannot_read_other_employee_month_plan(client, db, org):
    """访问他人月计划 → 404。"""
    month = client.post(
        "/v1/employee/month-plans/draft",
        json={"planMonth": "2026-11", "items": []},
    ).json()["data"]

    app.dependency_overrides[get_current_user] = lambda: org["b"]
    try:
        resp = client.get(f"/v1/employee/month-plans/{month['id']}")
    finally:
        app.dependency_overrides[get_current_user] = lambda: org["a"]

    assert resp.status_code == 404
    assert resp.json()["detail"] == "月计划不存在"


def test_cannot_modify_other_employee_week_plan(client, db, org):
    """修改他人周计划 → 404。"""
    week = client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-09", "items": [{"content": "x"}]},
    ).json()["data"]

    app.dependency_overrides[get_current_user] = lambda: org["b"]
    try:
        resp = client.put(
            f"/v1/employee/week-plans/{week['summary']['id']}",
            json={"weekStart": "2026-11-09", "items": []},
        )
    finally:
        app.dependency_overrides[get_current_user] = lambda: org["a"]

    assert resp.status_code == 404


def test_week_plan_list_only_returns_own(client, org):
    """列表只返回本人计划。"""
    client.post(
        "/v1/employee/week-plans",
        json={"weekStart": "2026-11-09", "items": [{"content": "甲的"}]},
    )

    app.dependency_overrides[get_current_user] = lambda: org["b"]
    try:
        rows = client.get("/v1/employee/week-plans").json()["data"]
    finally:
        app.dependency_overrides[get_current_user] = lambda: org["a"]

    assert rows == []


# ===========================================================================
# 7. 鉴权
# ===========================================================================


def test_plan_endpoints_require_auth(db, org):
    """无 token 访问计划端点 → 401。"""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides.pop(get_current_user, None)
    try:
        with TestClient(app) as anon:
            assert anon.get("/v1/employee/week-plans").status_code == 401
            assert anon.get("/v1/employee/month-plans/1").status_code == 401
            assert anon.get("/v1/employee/day-plans/detail").status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


# ===========================================================================
# 8. 状态机纯函数单测
# ===========================================================================


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (PlanStatus.DRAFT.value, PlanStatus.SUBMITTED.value, True),
        (PlanStatus.SUBMITTED.value, PlanStatus.DRAFT.value, True),
        (PlanStatus.SUBMITTED.value, PlanStatus.APPROVED.value, True),
        (PlanStatus.APPROVED.value, PlanStatus.DRAFT.value, True),
        (PlanStatus.REJECTED.value, PlanStatus.DRAFT.value, True),
        (PlanStatus.DRAFT.value, PlanStatus.APPROVED.value, False),
        (PlanStatus.APPROVED.value, PlanStatus.SUBMITTED.value, False),
        (PlanStatus.DRAFT.value, PlanStatus.DRAFT.value, False),
    ],
)
def test_state_machine_transitions(current, target, allowed):
    """状态机迁移表断言。"""
    assert planning.can_transition(current, target) is allowed


def test_state_machine_edit_states():
    """只有草稿 / 已撤回 / 被驳回可编辑。"""
    assert planning.is_editable(PlanStatus.DRAFT.value)
    assert planning.is_editable(PlanStatus.WITHDRAWN.value)
    assert planning.is_editable(PlanStatus.REJECTED.value)
    assert not planning.is_editable(PlanStatus.SUBMITTED.value)
    assert not planning.is_editable(PlanStatus.PENDING.value)
    assert not planning.is_editable(PlanStatus.APPROVED.value)


def test_week_status_alias():
    """内部状态 → 前端周计划状态的映射。"""
    assert planning.to_week_status(PlanStatus.DRAFT.value) == "DRAFT"
    assert planning.to_week_status(PlanStatus.SUBMITTED.value) == "PENDING"
    assert planning.to_week_status(PlanStatus.APPROVED.value) == "APPROVED"
    assert planning.to_week_status(PlanStatus.REJECTED.value) == "REJECTED"


def test_week_range_is_monday_to_sunday():
    """周区间：周一 ~ 周日。"""
    monday, sunday = planning.week_range(date(2026, 11, 14))
    assert monday == date(2026, 11, 9)
    assert sunday == date(2026, 11, 15)


def test_normalize_month_formats():
    """月份归一化：兼容 `YYYY-MM` 与 `YYYYMM`。"""
    assert planning.normalize_month("2026-11") == "2026-11"
    assert planning.normalize_month("202611") == "2026-11"
    assert planning.normalize_month("") == planning.current_month()


def test_parse_date_rejects_bad_format():
    """非法日期抛 ValueError。"""
    with pytest.raises(ValueError):
        planning.parse_date("bad-date", "planDate")
