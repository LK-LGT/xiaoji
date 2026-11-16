"""R6 / BUG-R6-01 回归测试：员工计划与成果「列表端点」。

背景
----
前端 `src/api/planning.ts` 有三个列表函数：

```ts
listMonthPlansApi()  → GET /planning/month-plans
listDayPlansApi()    → GET /planning/day-plans
listResultsApi()     → GET /planning/results
```

前端 `http.ts` 的 `baseURL='/api'` + Vite 代理 `rewrite: /api → /v1`，
所以实际请求的是 `/v1/planning/month-plans` 等。

而后端此前**只有** `/v1/planning/stats`，
`main.py` 里的 mock 版 `/planning/month-plans`（**无 `/v1` 前缀**）
前端永远命中不到 —— 于是
`EmployeeMonthPlanList` / `EmployeeDayPlanList` / `EmployeeResultList`
三个页面长期 404。

本测试锁定：
1. 三个列表端点存在且返回 200；
2. 返回真实数据（不是 mock 样例）；
3. 数据范围隔离：普通员工看不到别人的计划；
4. `ownerId` 参数不能突破数据范围。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, DayPlan, Department, Employee, MonthPlan
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def client_with_data():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(Department(id="D_ROOT", name="总部", parent_id=None, active=True))
    session.add(
        Department(id="D_BIZ", name="业务部", parent_id="D_ROOT", active=True)
    )
    session.add(
        Employee(
            id="E_LEADER",
            name="主管",
            department_id="D_BIZ",
            employment_status="active",
            account_status="enabled",
        )
    )
    session.add(
        Employee(
            id="E_EMP",
            name="员工甲",
            department_id="D_BIZ",
            manager_id="E_LEADER",
            employment_status="active",
            account_status="enabled",
        )
    )
    session.add(
        Employee(
            id="E_OTHER",
            name="员工乙",
            department_id="D_BIZ",
            manager_id="E_LEADER",
            employment_status="active",
            account_status="enabled",
        )
    )
    session.add(
        MonthPlan(
            id="MP_1",
            owner_id="E_EMP",
            plan_month="2026-11",
            title="甲的月计划",
            status="draft",
        )
    )
    session.add(
        MonthPlan(
            id="MP_2",
            owner_id="E_OTHER",
            plan_month="2026-11",
            title="乙的月计划",
            status="draft",
        )
    )
    session.add(
        DayPlan(
            id="DP_1",
            owner_id="E_EMP",
            plan_date=date(2026, 11, 16),
            content="甲的日计划",
            status="draft",
        )
    )
    session.commit()

    def override_get_db():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


def _auth(employee_id: str) -> dict[str, str]:
    token, _ = _generate_token(employee_id, employee_id, 1)
    return {"Authorization": f"Bearer {token}"}


# ── 端点存在且返回真实数据 ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    ["/v1/planning/month-plans", "/v1/planning/day-plans", "/v1/planning/results"],
)
def test_planning_list_endpoints_exist(client_with_data, path: str) -> None:
    """三个列表端点必须存在（BUG-R6-01：此前全是 404）。"""
    client, _ = client_with_data
    resp = client.get(path, headers=_auth("E_LEADER"))
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"
    assert isinstance(resp.json()["data"], list)


def test_month_plans_returns_real_rows(client_with_data) -> None:
    """返回的必须是数据库里的真实计划，不是硬编码样例。"""
    client, _ = client_with_data
    resp = client.get("/v1/planning/month-plans", headers=_auth("E_LEADER"))
    data = resp.json()["data"]
    titles = {item["title"] for item in data}
    assert {"甲的月计划", "乙的月计划"} <= titles, titles


def test_month_plan_dto_matches_frontend_contract(client_with_data) -> None:
    """每个字段都必须在（缺字段在前端会渲染成空值，属于静默 bug）。"""
    client, _ = client_with_data
    resp = client.get("/v1/planning/month-plans", headers=_auth("E_LEADER"))
    data = resp.json()["data"]
    assert data
    required = {
        "id", "title", "planMonth", "status", "ownerUserId", "deptId",
        "completionRate", "versionNo", "submitAt", "approverId", "approveAt",
        "approvalComment", "reviewStatus", "riskLevel", "reviewedAt",
    }
    for item in data:
        missing = required - set(item)
        assert not missing, f"缺少字段: {missing}"


# ── 数据范围隔离 ──────────────────────────────────────────────────────────

def test_employee_sees_only_own_month_plans(client_with_data) -> None:
    """普通员工只能看到自己的月计划。"""
    client, _ = client_with_data
    resp = client.get("/v1/planning/month-plans", headers=_auth("E_EMP"))
    data = resp.json()["data"]
    assert {item["ownerUserId"] for item in data} == {"E_EMP"}
    assert len(data) == 1


def test_leader_sees_direct_reports_plans(client_with_data) -> None:
    """主管能看到直属下属的计划。"""
    client, _ = client_with_data
    resp = client.get("/v1/planning/month-plans", headers=_auth("E_LEADER"))
    owners = {item["ownerUserId"] for item in resp.json()["data"]}
    assert {"E_EMP", "E_OTHER"} <= owners, owners


def test_owner_id_param_cannot_escape_scope(client_with_data) -> None:
    """`ownerId` 参数不能突破数据范围（防止 IDOR 越权）。"""
    client, _ = client_with_data
    resp = client.get(
        "/v1/planning/month-plans?ownerId=E_OTHER", headers=_auth("E_EMP")
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == [], "普通员工指定他人 ownerId 竟返回了数据"


def test_day_plans_scope_isolation(client_with_data) -> None:
    client, _ = client_with_data
    resp = client.get("/v1/planning/day-plans", headers=_auth("E_EMP"))
    data = resp.json()["data"]
    assert {item["ownerUserId"] for item in data} == {"E_EMP"}


# ── 鉴权 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    ["/v1/planning/month-plans", "/v1/planning/day-plans", "/v1/planning/results"],
)
def test_planning_lists_require_login(client_with_data, path: str) -> None:
    client, _ = client_with_data
    resp = client.get(path)
    assert resp.status_code in (401, 403), f"{path} -> {resp.status_code}"
