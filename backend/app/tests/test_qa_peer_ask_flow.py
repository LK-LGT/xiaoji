"""★ P0-4b：``POST /v1/qa/ask`` 的**同部门平级同事**链路集成测试。

⚠️ **这个文件守的是"整条链路"**
--------------------------------
``test_qa_peer_score_tool.py`` 守的是**工具网关**（给定令牌，查得对不对）；
``test_qa_authorization.py`` 守的是**授权决策器**（能不能查）。

本文件守的是**两者之间的接线**：
   前端传 subject_id → 授权判定 → 工具白名单裁剪 → 令牌带 subj
   → 工具网关按令牌里的目标人查 → 返回裁剪后的字段。

⚠️ **接线最容易出的错**（本文件逐条覆盖）：
1. ``subject_id`` 传了但没传到服务层 ⇒ 静默查了自己（**最危险**：用户以为查了同事）
2. 令牌没带 ``subj`` ⇒ 工具网关查的是提问者自己
3. peer 场景却给了 ``get_evidence`` ⇒ 证据原文泄露
4. 跨部门查询没被拒 ⇒ 越权
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    Evidence,
    HrDepartmentConfig,
    Score,
)
from backend.app.main import app
from backend.app.services.review_service import AgentClient


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def seeded(db):
    for dept in (
        Department(id="D_ROOT", name="总部", active=True),
        Department(id="D_SALES", name="销售部", active=True),
        Department(id="D_MKT", name="市场部", active=True),
        Department(id="D_HR", name="人力资源部", active=True),
    ):
        db.add(dept)
    db.flush()

    def emp(emp_id, name, dept_id, manager):
        db.add(Employee(
            id=emp_id, name=name, department_id=dept_id, manager_id=manager,
            employment_status="active", account_status="enabled",
        ))
        db.flush()

    emp("BOSS", "总裁", "D_ROOT", None)
    emp("MGR", "销售经理", "D_SALES", "BOSS")
    emp("A", "销售甲", "D_SALES", "MGR")
    emp("B", "销售乙", "D_SALES", "MGR")
    emp("X", "市场甲", "D_MKT", "BOSS")
    emp("HR", "HR", "D_HR", "BOSS")
    for emp_id, dept_id in (
        ("BOSS", "D_ROOT"), ("MGR", "D_SALES"), ("A", "D_SALES"),
        ("B", "D_SALES"), ("X", "D_MKT"), ("HR", "D_HR"),
    ):
        db.add(EmployeeDepartment(
            employee_id=emp_id, department_id=dept_id, is_primary=True
        ))
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))

    task = AssessmentTask(
        id="T1", name="2026-11 月度考核", period="202611",
        department_id="D_SALES", status="published",
    )
    db.add(task)
    db.flush()

    # B 的记录 —— 含评语与证据，peer 查询时都必须被裁掉
    record = AssessmentRecord(
        id="R_B", task_id="T1", employee_id="B", status="scored",
        confirmed_rating="B",
        manager_comment="⚠️ 保密的主管评语正文",
    )
    db.add(record)
    db.flush()
    db.add(Score(
        id="S_B", record_id="R_B", total=84.5,
        per_metric_json=[{
            "metric_code": "gmv", "metric_name": "销售额",
            "value": 120.0, "target": 100.0, "ratio": 1.2, "level": "A",
            "normalized_weight": 0.4, "level_score": 90.0, "contribution": 36.0,
            "level_scale": [{"level": "A", "min_ratio": 1.05}],
        }],
        trace_json={"participating_count": 1, "scheme_rating": "B", "warnings": []},
    ))
    db.add(Evidence(
        id="E1", employee_id="B", record_id="R_B", kind="document",
        summary="⚠️ 证据原文：客户合同扫描件",
        impact="scoring", usage="scoring", source="manual",
        visibility="team", status="approved",
    ))
    # A 自己也有记录 —— 用来验证"传了 subject_id 确实换了目标人"
    a_record = AssessmentRecord(id="R_A", task_id="T1", employee_id="A", status="scored")
    db.add(a_record)
    db.flush()
    db.add(Score(
        id="S_A", record_id="R_A", total=55.0, per_metric_json=[],
        trace_json={"participating_count": 0},
    ))
    db.commit()
    return task


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, "A")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def no_ai(monkeypatch):
    """把 L3 换成"不可用" —— 这样我们只看 L2 侧预取与授权的行为。"""

    async def unavailable(self, payload):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(AgentClient, "__init__", lambda self: None)
    monkeypatch.setattr(AgentClient, "qa", unavailable)


# ---------------------------------------------------------------------------
# ★ 核心：subject_id 确实换了目标人
# ---------------------------------------------------------------------------


def test_subject_id_selects_peer_target(client, seeded, no_ai) -> None:
    """★ 传 subject_id=B ⇒ 返回的是 B 的分数（84.5），不是 A 自己的（55）。"""
    resp = client.post("/v1/qa/ask", json={
        "question": "销售乙这个月多少分", "task_id": "T1", "subject_id": "B",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "get_peer_score" in data
    assert data["get_peer_score"]["total_score"] == 84.5
    # ⚠️ 关键：A 自己是 55 分 —— 若返回 55，说明 subject_id 没生效
    assert data["get_peer_score"]["total_score"] != 55.0


def test_without_subject_id_queries_self(client, seeded, no_ai) -> None:
    """不传 subject_id ⇒ 查自己（既有行为不变）。"""
    resp = client.post("/v1/qa/ask", json={
        "question": "我这个月多少分", "task_id": "T1",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "get_metric_values" in data
    assert "get_peer_score" not in data


def test_peer_scope_gets_only_cropped_tool(client, seeded, no_ai) -> None:
    """★ peer 场景的工具白名单**只有** get_peer_score。

    ⚠️ 出现 get_evidence 就意味着证据原文可能被带出来。
    """
    resp = client.post("/v1/qa/ask", json={
        "question": "销售乙这个月多少分", "task_id": "T1", "subject_id": "B",
    })
    data = resp.json()["data"]
    assert set(data) == {"get_peer_score"}, data.keys()
    assert "get_evidence" not in data
    assert "get_scoring_breakdown" not in data


def test_peer_response_excludes_sensitive_fields(client, seeded, no_ai) -> None:
    """★ 整份响应里都不得出现评语与证据原文。"""
    resp = client.post("/v1/qa/ask", json={
        "question": "销售乙这个月多少分", "task_id": "T1", "subject_id": "B",
    })
    body = resp.text
    assert "保密的主管评语正文" not in body
    assert "客户合同扫描件" not in body
    assert "证据原文" not in body
    assert "level_scale" not in body


# ---------------------------------------------------------------------------
# ★ 核心：越权被拒
# ---------------------------------------------------------------------------


def test_cross_department_query_rejected(client, seeded, no_ai) -> None:
    """★ 跨部门 ⇒ 403。"""
    resp = client.post("/v1/qa/ask", json={
        "question": "市场甲这个月多少分", "task_id": "T1", "subject_id": "X",
    })
    assert resp.status_code == 403, resp.text


def test_query_manager_rejected(client, seeded, no_ai) -> None:
    """★ 查上一级 ⇒ 403。"""
    resp = client.post("/v1/qa/ask", json={
        "question": "销售经理这个月多少分", "task_id": "T1", "subject_id": "MGR",
    })
    assert resp.status_code == 403, resp.text


def test_denial_message_does_not_leak(client, seeded, no_ai) -> None:
    """★ 拒绝文案**不区分**具体原因 —— 区分会泄露目标人层级。"""
    cross = client.post("/v1/qa/ask", json={
        "question": "q", "task_id": "T1", "subject_id": "X",
    })
    manager = client.post("/v1/qa/ask", json={
        "question": "q", "task_id": "T1", "subject_id": "MGR",
    })
    assert cross.status_code == manager.status_code == 403
    assert cross.json()["detail"] == manager.json()["detail"]


def test_unknown_subject_rejected(client, seeded, no_ai) -> None:
    """★ 目标人不存在 ⇒ 404。"""
    resp = client.post("/v1/qa/ask", json={
        "question": "q", "task_id": "T1", "subject_id": "NOBODY",
    })
    assert resp.status_code == 404, resp.text


def test_exited_subject_rejected(client, seeded, no_ai, db) -> None:
    """★ 目标离职 ⇒ 403。"""
    db.get(Employee, "B").employment_status = "exited"
    db.commit()
    resp = client.post("/v1/qa/ask", json={
        "question": "q", "task_id": "T1", "subject_id": "B",
    })
    assert resp.status_code == 403, resp.text
