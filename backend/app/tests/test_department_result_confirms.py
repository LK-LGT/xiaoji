"""部门侧「成果确认」模块的回归测试。

背景
----
``backend/app/routes/department_confirm.py`` 的 ``/v1/department/result-confirms``
系列端点此前是**空壳** —— 恒返回 ``[]``，页面看着有功能，实则什么都没接上。

与主管侧 ``/v1/leader/result-suggestions`` 是**同一批端点的两个入口**
（决策 D2/D4：主管审批即完成，不是两级审批链），
所以可见范围语义完全一致 —— 本文件与
``test_leader_result_suggestions.py`` 刻意保持对称的用例结构。

锁住的行为：

1. **端点真的注册了** —— 此前 404；
2. **要鉴权** —— 未登录 401/403，绝不能裸奔；
3. **空结果也是合法契约** —— 返回 ``{code:0, data:{...}}``，不是 500；
4. **只列已提交成果** —— draft 不占处理队列；
5. **范围外的成果列不出来** —— 从空壳切到真实数据后，
   最危险的副作用就是**越权可见**；
6. **筛选条件生效** —— orgId / periodMonth / confirmStatus / keyword。

⚠️ SQLite 同线程限制：``TestClient`` 在另一个线程跑端点，
必须 ``check_same_thread=False`` + ``StaticPool``。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, Department, Employee
from backend.app.main import app

DEPT_ID = "LS_DEPT"
LEADER_ID = "DC_LEADER"
STAFF_ID = "DC_STAFF"
OUTSIDER_ID = "DC_OUTSIDER"


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


@pytest.fixture
def client(api):
    return api[0]


@pytest.fixture
def db(api):
    return api[1]


def _seed_org(db: Session) -> Employee:
    """组织 + 主管 + 直属下属 + 一个范围外的人。

    ⚠️ 可见范围依赖 ``_owner_in_scope`` ——
    成果 owner 必须是审批人的**直属下属**才能被列出。
    """
    db.add(Department(id=DEPT_ID, name="确认部", active=True))
    leader = Employee(
        id=LEADER_ID,
        name="主管",
        login_account="dc_leader",
        department_id=DEPT_ID,
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    db.add(leader)
    db.flush()

    db.add(
        Employee(
            id=STAFF_ID,
            name="下属",
            login_account="dc_staff",
            department_id=DEPT_ID,
            manager_id=LEADER_ID,
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.add(
        Employee(
            id=OUTSIDER_ID,
            name="外人",
            login_account="dc_outsider",
            department_id=DEPT_ID,
            manager_id=None,
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.commit()
    return leader


def _headers(employee_id: str = LEADER_ID) -> dict:
    from backend.app.routes.auth import _generate_token

    token, _ = _generate_token(employee_id, "dc_leader", 1)
    return {"Authorization": f"Bearer {token}"}


def _make_plan(db: Session, plan_id: str, plan_month: str, owner_id: str = STAFF_ID):
    """造一条月计划。

    ⚠️ **必须**造：成果的 ``period`` 取自关联计划的 ``plan_month``
    （``_plan_period_label``），不是 ``result_date``。
    没有关联计划 → ``period`` 为空串 → 周期筛选会整条跳过
    （筛选条件是 ``if period_month and period and ...``）。

    ⚠️ ``month_plan`` 有唯一约束 ``(owner_id, plan_month)`` ——
    同一员工同一月只能有一条计划。所以多个成果复用同一条计划，
    这里先查后建。
    """
    from backend.app.domain.models import MonthPlan

    existing = db.query(MonthPlan).filter(
        MonthPlan.owner_id == owner_id, MonthPlan.plan_month == plan_month
    ).one_or_none()
    if existing is not None:
        return existing

    plan = MonthPlan(
        id=plan_id,
        owner_id=owner_id,
        plan_month=plan_month,
        title=f"{plan_month} 计划",
        status="approved",
    )
    db.add(plan)
    db.commit()
    return plan


def _make_result(
    db: Session,
    result_id: str,
    status: str,
    owner_id: str = STAFF_ID,
    plan_month: str = "2026-06",
    title: str = "确认成果",
):
    from backend.app.domain.result_models import Result

    plan_id = f"PLAN_{result_id}"
    _make_plan(db, plan_id, plan_month, owner_id=owner_id)

    row = Result(
        id=result_id,
        owner_id=owner_id,
        plan_type="month",
        plan_id=plan_id,
        plan_item_id=None,
        title=title,
        description="",
        content="",
        deliverable="",
        remark="",
        completion_status="100%",
        completion_rate=1.0,
        result_date=date(2026, 6, 15),
        version_no=1,
        status=status,
    )
    db.add(row)
    db.commit()
    return row


def _list_ids(client: TestClient, query: str = "") -> list[str]:
    resp = client.get(f"/v1/department/result-confirms{query}", headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = body["data"]
    # 前端 PageResponse<T> 契约：{ items, total, pageNo, pageSize }
    rows = data.get("items", [])
    return [row.get("id") for row in rows]


# ═══════════════════════════════════════════════════════════════════════════
# 1. 路由与鉴权
# ═══════════════════════════════════════════════════════════════════════════


def test_result_confirms_route_is_registered(client: TestClient):
    """端点必须真的注册了（此前是 404）。"""
    resp = client.get("/v1/department/result-confirms")
    assert resp.status_code != 404, f"端点未注册：{resp.status_code}"


def test_result_confirms_requires_auth(client: TestClient):
    """未登录不得访问，也不得 500。"""
    resp = client.get("/v1/department/result-confirms")
    assert resp.status_code in (401, 403), resp.text
    assert resp.status_code != 500, resp.text


def test_result_confirms_returns_envelope(client: TestClient, db: Session):
    """登录后返回统一信封，即使没有数据。"""
    _seed_org(db)
    resp = client.get("/v1/department/result-confirms", headers=_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 2. 只列已提交成果
# ═══════════════════════════════════════════════════════════════════════════


def test_draft_results_are_not_listed(client: TestClient, db: Session):
    """未提交的成果不得进入确认队列。"""
    _seed_org(db)
    _make_result(db, "DC_RES_DRAFT", "draft")

    assert "DC_RES_DRAFT" not in _list_ids(client)


def test_submitted_results_are_listed(client: TestClient, db: Session):
    """已提交的成果应当出现在列表里（"接通真实数据"的正向证据）。"""
    _seed_org(db)
    _make_result(db, "DC_RES_SUBMITTED", "submitted")

    assert "DC_RES_SUBMITTED" in _list_ids(client)


# ═══════════════════════════════════════════════════════════════════════════
# 3. 审批可见范围
# ═══════════════════════════════════════════════════════════════════════════


def test_results_outside_scope_are_not_listed(client: TestClient, db: Session):
    """范围外的成果不得出现 —— 从空壳切到真实数据后最危险的副作用就是越权可见。"""
    _seed_org(db)
    _make_result(db, "DC_RES_OUTSIDE", "submitted", owner_id=OUTSIDER_ID)

    assert "DC_RES_OUTSIDE" not in _list_ids(client)


# ═══════════════════════════════════════════════════════════════════════════
# 4. 筛选条件
# ═══════════════════════════════════════════════════════════════════════════


def test_period_month_filter(client: TestClient, db: Session):
    """periodMonth 筛选必须生效。"""
    _seed_org(db)
    _make_result(db, "DC_RES_JUN", "submitted", plan_month="2026-06")
    _make_result(db, "DC_RES_JUL", "submitted", plan_month="2026-07")

    assert "DC_RES_JUN" in _list_ids(client, "?periodMonth=2026-06")
    assert "DC_RES_JUL" not in _list_ids(client, "?periodMonth=2026-06")


def test_keyword_filter(client: TestClient, db: Session):
    """keyword 筛选必须生效。"""
    _seed_org(db)
    _make_result(db, "DC_RES_ALPHA", "submitted", title="甲方案")
    _make_result(db, "DC_RES_BETA", "submitted", title="乙方案")

    found = _list_ids(client, "?keyword=甲方案")
    assert "DC_RES_ALPHA" in found
    assert "DC_RES_BETA" not in found


def test_pagination_does_not_lose_items(client: TestClient, db: Session):
    """分页不得丢数据 —— pageSize/pageNo 必须真的生效。"""
    _seed_org(db)
    for i in range(3):
        _make_result(db, f"DC_RES_{i}", "submitted")

    first = _list_ids(client, "?pageNo=1&pageSize=2")
    second = _list_ids(client, "?pageNo=2&pageSize=2")
    assert len(first) == 2, f"第 1 页应有 2 条，实际 {len(first)}"
    assert first and second
    assert not (set(first) & set(second)), "两页不应有重复"
