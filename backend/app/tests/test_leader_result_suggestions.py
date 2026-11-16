"""主管侧「成果建议」模块的回归测试。

背景
----
``backend/app/routes/leader_suggestion.py`` 的 ``/result-suggestions`` 系列端点
此前是**空壳** —— 恒返回 ``[]``，页面看着有功能，实则什么都没接上。

本文件锁定已接通的行为：

1. **端点真的注册了** —— 此前是 404；
2. **要鉴权** —— 未登录 401/403，绝不能裸奔；
3. **空结果也是合法契约** —— 返回 ``{code:0, data:[]}``，不是 500；
4. **只列已提交成果** —— 未提交条目不占审批人的处理队列；
5. **审批可见范围生效** —— 范围外的成果列不出来；
6. **筛选条件生效** —— ownerId 等查询参数真的起作用。

⚠️ SQLite 同线程限制：``TestClient`` 在另一个线程跑端点，
必须 ``check_same_thread=False`` + ``StaticPool``，
否则报「SQLite objects created in a thread can only be used in that same thread」。
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
LEADER_ID = "LS_LEADER"
STAFF_ID = "LS_STAFF"


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
    """造组织 + 主管 + 直属下属。

    ⚠️ 审批可见范围依赖 ``_owner_in_scope`` ——
    成果的 owner 必须是审批人的**直属下属**才能被列出来。
    所以不能拿主管自己当 owner。
    """
    db.add(Department(id=DEPT_ID, name="成果部", active=True))
    leader = Employee(
        id=LEADER_ID,
        name="主管",
        login_account="ls_leader",
        department_id=DEPT_ID,
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    db.add(leader)
    db.flush()

    staff = Employee(
        id=STAFF_ID,
        name="下属",
        login_account="ls_staff",
        department_id=DEPT_ID,
        manager_id=LEADER_ID,  # ← 直属下属关系，审批范围的依据
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    db.add(staff)
    db.commit()
    return leader


def _headers(employee_id: str = LEADER_ID) -> dict:
    from backend.app.routes.auth import _generate_token

    token, _ = _generate_token(employee_id, "ls_leader", 1)
    return {"Authorization": f"Bearer {token}"}


def _make_result(
    db: Session,
    result_id: str,
    status: str,
    owner_id: str = STAFF_ID,
):
    """造一条成果记录。"""
    from backend.app.domain.result_models import Result

    row = Result(
        id=result_id,
        owner_id=owner_id,
        plan_type="month",
        plan_id=f"PLAN_{result_id}",
        plan_item_id=None,
        title=f"成果 {result_id}",
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


# ═══════════════════════════════════════════════════════════════════════════
# 1. 路由与鉴权
# ═══════════════════════════════════════════════════════════════════════════


def test_result_suggestions_route_is_registered(client: TestClient):
    """端点必须真的注册了（此前是 404）。"""
    resp = client.get("/v1/leader/result-suggestions")
    assert resp.status_code != 404, f"端点未注册：{resp.status_code}"


def test_result_suggestions_requires_auth(client: TestClient):
    """未登录不得访问，也不得 500。"""
    resp = client.get("/v1/leader/result-suggestions")
    assert resp.status_code in (401, 403), resp.text
    assert resp.status_code != 500, resp.text


def test_result_suggestions_returns_envelope(client: TestClient, db: Session):
    """登录后返回统一信封 { code:0, data:[...] }，即使没有数据。"""
    _seed_org(db)
    resp = client.get("/v1/leader/result-suggestions", headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert isinstance(body["data"], list)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 只列已提交成果
# ═══════════════════════════════════════════════════════════════════════════


def test_draft_results_are_not_listed(client: TestClient, db: Session):
    """未提交的成果**不得**进入审批人的处理队列。

    过去端点恒返回 []，看不出这个区别；接通真实数据后，
    若把 draft 也列出来，主管会看到一堆还在写的条目。
    """
    _seed_org(db)
    _make_result(db, "LS_RES_DRAFT", "draft")

    resp = client.get("/v1/leader/result-suggestions", headers=_headers())
    assert resp.status_code == 200, resp.text
    ids = [row.get("id") for row in resp.json()["data"]]
    assert "LS_RES_DRAFT" not in ids, "draft 成果不应进入审批队列"


def test_submitted_results_are_listed(client: TestClient, db: Session):
    """已提交的成果**应当**出现在列表里（这是"接通真实数据"的正向证据）。"""
    _seed_org(db)
    _make_result(db, "LS_RES_SUBMITTED", "submitted")

    resp = client.get("/v1/leader/result-suggestions", headers=_headers())
    assert resp.status_code == 200, resp.text
    ids = [row.get("id") for row in resp.json()["data"]]
    assert "LS_RES_SUBMITTED" in ids, "已提交成果未出现在列表里"


# ═══════════════════════════════════════════════════════════════════════════
# 3. 审批可见范围
# ═══════════════════════════════════════════════════════════════════════════


def test_results_outside_approval_scope_are_not_listed(client: TestClient, db: Session):
    """范围外的成果**不得**出现在列表里。

    「接通真实数据」最危险的副作用就是**越权可见** ——
    端点从恒返回 [] 变成返回真实数据后，
    必须确认它仍然受审批可见范围约束。
    """
    _seed_org(db)
    # outsider 不在主管的管理范围内
    db.add(
        Employee(
            id="LS_OUTSIDER",
            name="外人",
            login_account="ls_outsider",
            department_id=DEPT_ID,
            manager_id=None,
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.commit()
    _make_result(db, "LS_RES_OUTSIDE", "submitted", owner_id="LS_OUTSIDER")

    resp = client.get("/v1/leader/result-suggestions", headers=_headers())
    assert resp.status_code == 200, resp.text
    ids = [row.get("id") for row in resp.json()["data"]]
    assert "LS_RES_OUTSIDE" not in ids, "范围外的成果不应被列出"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 筛选条件
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "query,expect_present",
    [
        (f"?ownerId={STAFF_ID}", True),
        ("?ownerId=NO_SUCH_USER", False),
    ],
)
def test_owner_id_filter(client: TestClient, db: Session, query, expect_present):
    """ownerId 筛选必须生效。"""
    _seed_org(db)
    _make_result(db, "LS_RES_1", "submitted")

    resp = client.get(f"/v1/leader/result-suggestions{query}", headers=_headers())
    assert resp.status_code == 200, resp.text
    ids = [row.get("id") for row in resp.json()["data"]]
    assert ("LS_RES_1" in ids) is expect_present
