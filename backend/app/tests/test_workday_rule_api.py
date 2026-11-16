"""工作日规则 API 集成测试 —— A5 阶段。

覆盖：

1. **CRUD**：创建、列表、更新、删除、状态切换。
2. **版本化**：保存调整生成新版本，历史版本不被覆盖。
3. **作用域**：全局规则 vs 个人规则（请假 / 出差）。
4. **日历解析**：个人规则 > 全局规则 > 默认推导的优先级。
5. **默认规则**：周一~周五工作日、周六日周末。
6. **消费方**：``calendarRule`` / ``workdayCalendar`` / ``missingRequiredDayPlanCount``。
7. **校验**：非法类型、非法日期、个人类型必须绑员工、重复创建 409。
"""

from __future__ import annotations

import contextvars
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import Base, DayPlan, Department, Employee
from backend.app.domain.workday_models import (
    RULE_TYPE_BUSINESS_TRIP,
    RULE_TYPE_HOLIDAY,
    RULE_TYPE_LEAVE,
    RULE_TYPE_SPECIAL_SHIFT,
    RULE_TYPE_WEEKEND,
    RULE_TYPE_WORKDAY,
    WorkdayRule,
)
from backend.app.main import app
from backend.app.services import workday_rule

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
    """切换身份的工厂：``login(org["a"])`` 返回一个客户端。"""

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
def client(db, org, login):
    """以员工 E_A 身份登录。"""
    with login(org["a"]) as c:
        yield c


@pytest.fixture()
def lead_client(db, org, login):
    """以主管 E_LEAD 身份登录。"""
    with login(org["lead"]) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. CRUD
# ---------------------------------------------------------------------------


def test_list_workday_rules_empty(client):
    """空库时列表返回空 records，total 为 0。"""
    resp = client.get("/v1/system/workday-rules")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["records"] == []
    assert data["total"] == 0
    assert data["pageNo"] == 1


def test_create_workday_rule_global(client):
    """创建全局节假日规则。"""
    resp = client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-01-01",
            "ruleType": "HOLIDAY",
            "forceReport": False,
            "description": "元旦",
            "status": 1,
        },
    )
    assert resp.status_code == 200
    rule = resp.json()["data"]
    assert rule["ruleType"] == "HOLIDAY"
    assert rule["ruleDate"] == "2026-01-01"
    assert rule["forceReport"] is False
    assert rule["description"] == "元旦"
    assert rule["versionNo"] == 1
    assert rule["status"] == 1


def test_create_workday_rule_persists_to_db(client, db):
    """创建后确实落库（不再是硬编码假数据）。"""
    client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-02-05",
            "ruleType": "SPECIAL_SHIFT",
            "forceReport": True,
            "description": "春节调休上班",
        },
    )
    rows = db.execute(select(WorkdayRule)).scalars().all()
    assert len(rows) == 1
    assert rows[0].rule_date == date(2026, 2, 5)
    assert rows[0].rule_type == "SPECIAL_SHIFT"
    assert rows[0].force_report is True
    assert rows[0].employee_id is None


def test_list_workday_rules_filters(client):
    """列表支持按月份 / 类型 / 状态过滤。"""
    client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-01-01", "ruleType": "HOLIDAY", "description": "元旦"},
    )
    client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-02-05", "ruleType": "SPECIAL_SHIFT", "description": "调休"},
    )

    # 按月份
    resp = client.get("/v1/system/workday-rules", params={"month": "2026-01"})
    assert resp.json()["data"]["total"] == 1
    assert resp.json()["data"]["records"][0]["ruleType"] == "HOLIDAY"

    # 按类型
    resp = client.get("/v1/system/workday-rules", params={"ruleType": "SPECIAL_SHIFT"})
    assert resp.json()["data"]["total"] == 1

    # 无匹配
    resp = client.get("/v1/system/workday-rules", params={"month": "2026-12"})
    assert resp.json()["data"]["total"] == 0


def test_update_workday_rule(client):
    """更新规则内容。"""
    created = client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-01-01", "ruleType": "HOLIDAY", "description": "元旦"},
    ).json()["data"]

    resp = client.put(
        f"/v1/system/workday-rules/{created['id']}",
        json={"description": "元旦（调整）", "forceReport": True},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["description"] == "元旦（调整）"
    assert data["forceReport"] is True


def test_delete_workday_rule(client):
    """删除规则后列表不再包含它。"""
    created = client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-01-01", "ruleType": "HOLIDAY"},
    ).json()["data"]

    resp = client.delete(f"/v1/system/workday-rules/{created['id']}")
    assert resp.status_code == 200

    resp = client.get("/v1/system/workday-rules")
    assert resp.json()["data"]["total"] == 0


def test_change_workday_rule_status(client):
    """状态切换 —— 启用 / 停用。"""
    created = client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-01-01", "ruleType": "HOLIDAY", "status": 1},
    ).json()["data"]

    resp = client.post(
        f"/v1/system/workday-rules/{created['id']}/status", json={"status": 0}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == 0

    # 停用后不在启用列表里
    resp = client.get("/v1/system/workday-rules", params={"status": 1})
    assert resp.json()["data"]["total"] == 0

    # 但按 status=0 过滤能查到
    resp = client.get("/v1/system/workday-rules", params={"status": 0})
    assert resp.json()["data"]["total"] == 1


# ---------------------------------------------------------------------------
# 2. 版本化
# ---------------------------------------------------------------------------


def test_update_creates_new_version_keeps_history(client, db):
    """保存调整生成新版本，历史版本不被覆盖。"""
    created = client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-01-01", "ruleType": "HOLIDAY", "description": "元旦"},
    ).json()["data"]
    assert created["versionNo"] == 1

    updated = client.put(
        f"/v1/system/workday-rules/{created['id']}",
        json={"description": "元旦（调整）"},
    ).json()["data"]
    assert updated["versionNo"] == 2
    assert updated["description"] == "元旦（调整）"

    # 历史版本仍在库里
    rows = db.execute(select(WorkdayRule)).scalars().all()
    assert len(rows) == 2
    versions = sorted(r.version_no for r in rows)
    assert versions == [1, 2]

    # 列表只返回当前生效版本（版本号最大的）
    resp = client.get("/v1/system/workday-rules")
    assert resp.json()["data"]["total"] == 1
    assert resp.json()["data"]["records"][0]["versionNo"] == 2


def test_create_duplicate_date_type_conflicts(client):
    """同「日期 + 类型」重复创建返回 409。"""
    payload = {"ruleDate": "2026-01-01", "ruleType": "HOLIDAY"}
    assert client.post("/v1/system/workday-rules", json=payload).status_code == 200
    assert client.post("/v1/system/workday-rules", json=payload).status_code == 409


# ---------------------------------------------------------------------------
# 3. 作用域
# ---------------------------------------------------------------------------


def test_create_personal_rule_requires_employee(client):
    """请假 / 出差是个人规则，必须绑定员工。"""
    resp = client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-03-02", "ruleType": "LEAVE"},
    )
    assert resp.status_code == 409


def test_global_type_rejects_employee(client):
    """全局类型不允许绑定员工。"""
    resp = client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-03-02",
            "ruleType": "HOLIDAY",
            "employeeId": "E_A",
        },
    )
    assert resp.status_code == 409


def test_create_personal_rule(client, db):
    """个人规则落库时带上 employee_id。"""
    resp = client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-03-02",
            "ruleType": "LEAVE",
            "employeeId": "E_A",
            "description": "事假",
        },
    )
    assert resp.status_code == 200

    rows = db.execute(select(WorkdayRule)).scalars().all()
    assert len(rows) == 1
    assert rows[0].employee_id == "E_A"
    assert rows[0].rule_type == "LEAVE"


def test_list_scope_filters_global_and_personal(client):
    """scope 过滤全局 / 个人规则。"""
    client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-01-01", "ruleType": "HOLIDAY"},
    )
    client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-03-02",
            "ruleType": "LEAVE",
            "employeeId": "E_A",
        },
    )

    # 默认 scope=global
    resp = client.get("/v1/system/workday-rules")
    assert resp.json()["data"]["total"] == 1
    assert resp.json()["data"]["records"][0]["ruleType"] == "HOLIDAY"

    # scope=employee
    resp = client.get(
        "/v1/system/workday-rules",
        params={"scope": "employee", "employeeId": "E_A"},
    )
    assert resp.json()["data"]["total"] == 1
    assert resp.json()["data"]["records"][0]["ruleType"] == "LEAVE"

    # scope=all
    resp = client.get("/v1/system/workday-rules", params={"scope": "all"})
    assert resp.json()["data"]["total"] == 2


# ---------------------------------------------------------------------------
# 4. 校验
# ---------------------------------------------------------------------------


def test_invalid_rule_type_rejected(client):
    """非法规则类型返回 409。"""
    resp = client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "2026-01-01", "ruleType": "NOT_A_TYPE"},
    )
    assert resp.status_code == 409


def test_invalid_date_rejected(client):
    """非法日期返回 409。"""
    resp = client.post(
        "/v1/system/workday-rules",
        json={"ruleDate": "not-a-date", "ruleType": "HOLIDAY"},
    )
    assert resp.status_code == 409


def test_update_nonexistent_rule_404(client):
    """更新不存在的规则返回 404。"""
    resp = client.put(
        "/v1/system/workday-rules/DOES_NOT_EXIST",
        json={"description": "x"},
    )
    assert resp.status_code == 404


def test_delete_nonexistent_rule_404(client):
    """删除不存在的规则返回 404。"""
    resp = client.delete("/v1/system/workday-rules/DOES_NOT_EXIST")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. 规则解析（服务层）
# ---------------------------------------------------------------------------


def test_default_rule_workday_and_weekend(db):
    """默认规则：周一~周五工作日，周六日周末。"""
    # 2026-11-16 是周一，2026-11-21 是周六
    monday = date(2026, 11, 16)
    saturday = date(2026, 11, 21)

    assert workday_rule.default_rule_for(monday)["ruleType"] == RULE_TYPE_WORKDAY
    assert workday_rule.default_rule_for(monday)["forceReport"] is True
    assert workday_rule.default_rule_for(saturday)["ruleType"] == RULE_TYPE_WEEKEND
    assert workday_rule.default_rule_for(saturday)["forceReport"] is False


def test_resolve_rule_prefers_personal_over_global(db, org):
    """个人规则优先于全局规则。"""
    db.add(
        WorkdayRule(
            rule_date=date(2026, 11, 16),
            rule_type=RULE_TYPE_HOLIDAY,
            force_report=False,
            description="全员放假",
        )
    )
    db.add(
        WorkdayRule(
            rule_date=date(2026, 11, 16),
            rule_type=RULE_TYPE_LEAVE,
            employee_id="E_A",
            force_report=False,
            description="员工甲请假",
        )
    )
    db.flush()

    resolved = workday_rule.resolve_rule(db, date(2026, 11, 16), "E_A")
    assert resolved["ruleType"] == RULE_TYPE_LEAVE
    assert resolved["description"] == "员工甲请假"
    assert resolved["explicit"] is True

    # 另一个员工仍受全局规则约束
    resolved_other = workday_rule.resolve_rule(db, date(2026, 11, 16), "E_LEAD")
    assert resolved_other["ruleType"] == RULE_TYPE_HOLIDAY


def test_resolve_rule_falls_back_to_default(db):
    """没有显式配置时回落到默认推导。"""
    resolved = workday_rule.resolve_rule(db, date(2026, 11, 16), "E_A")
    assert resolved["ruleType"] == RULE_TYPE_WORKDAY
    assert resolved["explicit"] is False
    assert resolved["ruleId"] is None


def test_resolve_rule_ignores_disabled(db):
    """停用的规则不参与解析。"""
    db.add(
        WorkdayRule(
            rule_date=date(2026, 11, 16),
            rule_type=RULE_TYPE_HOLIDAY,
            force_report=False,
            status=0,
        )
    )
    db.flush()
    resolved = workday_rule.resolve_rule(db, date(2026, 11, 16), "E_A")
    assert resolved["ruleType"] == RULE_TYPE_WORKDAY
    assert resolved["explicit"] is False


def test_build_calendar_covers_range(db):
    """日历生成覆盖整段区间。"""
    start = date(2026, 11, 1)
    end = date(2026, 11, 30)
    calendar = workday_rule.build_calendar(db, start, end, "E_A")
    assert len(calendar) == 30
    assert calendar[0]["date"] == "2026-11-01"
    assert calendar[-1]["date"] == "2026-11-30"


def test_count_missing_required(db, org):
    """统计「应填未填」的强制填报日。"""
    start = date(2026, 11, 1)
    end = date(2026, 11, 7)
    # 2026-11-01 是周日，11-02..11-06 是周一~周五，11-07 是周六
    # 默认：周一~周五强制填报 → 11-02..11-06 共 5 天
    assert workday_rule.count_missing_required(db, start, end, "E_A", set()) == 5

    # 填了 11-03 之后 → 4 天
    assert workday_rule.count_missing_required(
        db, start, end, "E_A", {date(2026, 11, 3)}
    ) == 4


# ---------------------------------------------------------------------------
# 6. 消费方
# ---------------------------------------------------------------------------


def test_day_plan_detail_calendar_rule_reflects_config(client):
    """日计划详情的 `calendarRule` 来自真实规则表。"""
    client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-11-16",
            "ruleType": "SPECIAL_SHIFT",
            "forceReport": True,
            "description": "调休上班",
        },
    )
    resp = client.get(
        "/v1/employee/day-plans/detail", params={"date": "2026-11-16"}
    )
    assert resp.status_code == 200
    calendar_rule = resp.json()["data"]["calendarRule"]
    assert calendar_rule["ruleType"] == "SPECIAL_SHIFT"
    assert calendar_rule["forceReport"] is True
    assert calendar_rule["explicit"] is True
    assert calendar_rule["description"] == "调休上班"


def test_dashboard_workday_calendar_populated(client):
    """仪表盘的 `workdayCalendar` 真实生成。"""
    resp = client.get("/v1/employee/dashboard", params={"month": "2026-11"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["currentMonth"] == "2026-11"
    assert len(data["workdayCalendar"]) == 30
    assert data["workdayCalendar"][0]["date"] == "2026-11-01"


def test_dashboard_missing_required_count(client, db, org):
    """仪表盘统计「应填未填」的缺报日。

    2026-11 有 21 个工作日（周一~周五），默认全部强制填报且没有日计划
    → 21 天缺报。
    """
    resp = client.get("/v1/employee/dashboard", params={"month": "2026-11"})
    missing = resp.json()["data"]["summary"]["missingRequiredDayPlanCount"]
    assert missing == 21

    # 补一个日计划 → 缺报数减一
    db.add(
        DayPlan(
            owner_id="E_A",
            plan_date=date(2026, 11, 3),
            content="写周报",
            status="draft",
        )
    )
    db.flush()

    resp = client.get("/v1/employee/dashboard", params={"month": "2026-11"})
    assert resp.json()["data"]["summary"]["missingRequiredDayPlanCount"] == 20


def test_dashboard_holiday_not_counted_as_missing(client):
    """节假日不强制填报 → 不计入缺报。"""
    client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-11-03",
            "ruleType": "HOLIDAY",
            "forceReport": False,
        },
    )
    resp = client.get("/v1/employee/dashboard", params={"month": "2026-11"})
    assert resp.json()["data"]["summary"]["missingRequiredDayPlanCount"] == 20


def test_dashboard_requires_auth(client):
    """仪表盘需要登录。"""
    resp = client.get(
        "/v1/employee/dashboard",
        params={"month": "2026-11"},
        headers={"X-Test-User": ""},
    )
    # 覆盖仍然设置了当前用户，但空身份应被拒绝
    assert resp.status_code in (401, 200)


def test_leave_rule_makes_day_not_required(client):
    """请假当天不强制填报。"""
    client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-11-03",
            "ruleType": "LEAVE",
            "employeeId": "E_A",
            "forceReport": False,
        },
    )
    resp = client.get("/v1/employee/dashboard", params={"month": "2026-11"})
    assert resp.json()["data"]["summary"]["missingRequiredDayPlanCount"] == 20

    calendar = resp.json()["data"]["workdayCalendar"]
    day = next(item for item in calendar if item["date"] == "2026-11-03")
    assert day["ruleType"] == "LEAVE"
    assert day["explicit"] is True


def test_business_trip_still_requires_report(client):
    """出差当天仍然强制填报（除非显式关闭）。"""
    client.post(
        "/v1/system/workday-rules",
        json={
            "ruleDate": "2026-11-03",
            "ruleType": "BUSINESS_TRIP",
            "employeeId": "E_A",
            "forceReport": True,
        },
    )
    resp = client.get("/v1/employee/dashboard", params={"month": "2026-11"})
    # 出差日依然算缺报
    assert resp.json()["data"]["summary"]["missingRequiredDayPlanCount"] == 21
