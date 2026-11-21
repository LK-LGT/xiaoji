"""P0-4b：``get_peer_score`` 工具的测试。

⚠️ **这个文件守的是「字段裁剪」**
--------------------------------
权限层（谁能查）已经测过了（``test_qa_peer_permission.py``）。
本文件守的是**第二道闸门**：**查到之后能看到什么**。

⚠️ 两道闸门是**独立**的 ——
   权限对了但字段没裁剪 ⇒ 泄露评语正文、证据原文；
   字段裁剪了但权限错了 ⇒ 越权查询。
   ⇒ 两边都要测，缺一不可。

⚠️ **核心不变量**
----------------
1. peer 查询**不能**返回评语正文 / 证据原文 / 自评 / 申诉
2. peer 查询**不能**通过请求体指定目标人
3. 目标人**只能**来自令牌（签名保护）
4. peer 分数**不能**跨部门 / 跨级 / 查上级
5. peer 目标离职 ⇒ 拒绝
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    CommentDraft,
    Department,
    Employee,
    EmployeeDepartment,
    Evidence,
    HrDepartmentConfig,
    Score,
    SelfReview,
)
from backend.app.main import app
from backend.app.services.agent_tool_token import issue_agent_tool_token


@pytest.fixture()
def db():
    """⚠️ 必须 ``StaticPool`` —— SQLite ``:memory:`` 按连接隔离。"""
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
def seeded(db: Session):
    """组织 + 一条完整的绩效记录（含评语、证据、自评 —— 都要被裁掉）。"""
    for dept in (
        Department(id="D_ROOT", name="总部", active=True),
        Department(id="D_SALES", name="销售部", active=True),
        Department(id="D_MKT", name="市场部", active=True),
        Department(id="D_HR", name="人力资源部", active=True),
    ):
        db.add(dept)
    db.flush()

    def emp(emp_id, name, dept_id, manager):
        e = Employee(
            id=emp_id, name=name, department_id=dept_id, manager_id=manager,
            employment_status="active", account_status="enabled",
        )
        db.add(e)
        db.flush()
        return e

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

    # 考核任务 + B 的绩效记录
    task = AssessmentTask(
        id="T1", name="2026-11 月度考核", period="202611",
        department_id="D_SALES", status="published",
    )
    db.add(task)
    db.flush()

    record = AssessmentRecord(
        id="R_B", task_id="T1", employee_id="B", status="scored",
        confirmed_rating="B",
        manager_comment="⚠️ 保密的主管评语正文：员工沟通态度需改进",
        self_review_content={"summary": "⚠️ 私密的自评内容"},
    )
    db.add(record)
    db.flush()

    db.add(Score(
        id="S_B", record_id="R_B", total=84.5,
        per_metric_json=[
            {
                "metric_code": "gmv", "metric_name": "销售额",
                "value": 120.0, "target": 100.0, "unit": "万",
                "ratio": 1.2, "level": "A", "level_desc": "超额完成",
                "raw_weight": 40.0, "normalized_weight": 0.4,
                "level_score": 90.0, "contribution": 36.0,
                # ⚠️ 以下字段**必须**被裁掉
                "level_scale": [{"level": "A", "min_ratio": 1.05}],
                "internal_note": "⚠️ 内部配置备注",
                "evidence_ref": "⚠️ 证据引用",
            },
            {
                "metric_code": "visit", "metric_name": "客户拜访",
                "value": 10.0, "target": 12.0, "unit": "次",
                "ratio": 0.833, "level": "C", "level_desc": "未达标",
                "raw_weight": 60.0, "normalized_weight": 0.6,
                "level_score": 80.0, "contribution": 48.0,
            },
        ],
        trace_json={
            "rule": "weighted", "spec_count": 2, "participating_count": 2,
            "total_weight": 100.0, "participating_weight": 100.0,
            "scheme_rating": "B",
            "warnings": ["指标 visit 缺目标值，未参与算分"],
        },
    ))

    # ⚠️ 敏感数据：证据原文、自评、评语草稿
    db.add(Evidence(
        id="E1", employee_id="B", record_id="R_B",
        kind="document", summary="⚠️ 证据原文：客户合同扫描件",
        impact="scoring", usage="scoring", source="manual",
        visibility="team", status="approved",
    ))
    db.add(SelfReview(
        id="SR1", record_id="R_B", answers_json={"q1": "⚠️ 私密自评答案"},
        submit_source="employee",
    ))
    db.add(CommentDraft(
        id="CD1", record_id="R_B", markdown="⚠️ 评语草稿正文",
    ))
    db.commit()
    return task


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def peer_token(asker: str, subject: str) -> str:
    """签发一个「查平级同事」的令牌。"""
    return issue_agent_tool_token(
        employee_id=asker, task_id="T1", allow=["get_peer_score"],
        scope="peer", subject_id=subject, reason="同部门同级别同事（只读）",
    )


def call(client, token: str, body: dict | None = None):
    return client.post(
        "/v1/agent-tools/get_peer_score",
        json=body if body is not None else {"period": "202611"},
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# ★ 核心：字段裁剪
# ---------------------------------------------------------------------------


def test_peer_score_returns_total_and_metrics(client, seeded) -> None:
    """peer 查询返回总分、等级与指标构成。"""
    resp = call(client, peer_token("A", "B"))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["available"] is True
    assert data["total_score"] == 84.5
    assert data["confirmed_rating"] == "B"
    assert len(data["metrics"]) == 2
    metric = data["metrics"][0]
    assert metric["metric_code"] == "gmv"
    assert metric["contribution"] == 36.0


def test_peer_score_includes_formula(client, seeded) -> None:
    """★ 含算式 —— 让 AI 能解释「怎么算出来的」。"""
    resp = call(client, peer_token("A", "B"))
    data = resp.json()["data"]
    formula = data["metrics"][0]["formula"]
    assert "0.4" in formula and "90.0" in formula and "36.0" in formula


def test_peer_score_excludes_manager_comment(client, seeded) -> None:
    """★ 评语正文**不得**出现。"""
    resp = call(client, peer_token("A", "B"))
    body = resp.text
    assert "保密的主管评语正文" not in body
    assert "manager_comment" not in body


def test_peer_score_excludes_self_review(client, seeded) -> None:
    """★ 自评内容**不得**出现。"""
    resp = call(client, peer_token("A", "B"))
    assert "私密的自评内容" not in resp.text
    assert "私密自评答案" not in resp.text


def test_peer_score_excludes_evidence(client, seeded) -> None:
    """★ 证据原文**不得**出现。"""
    resp = call(client, peer_token("A", "B"))
    assert "证据原文" not in resp.text
    assert "客户合同扫描件" not in resp.text


def test_peer_score_excludes_comment_draft(client, seeded) -> None:
    """★ 评语草稿**不得**出现。"""
    resp = call(client, peer_token("A", "B"))
    assert "评语草稿正文" not in resp.text


def test_peer_score_excludes_internal_metric_fields(client, seeded) -> None:
    """★ 指标里的内部配置字段**不得**出现。"""
    resp = call(client, peer_token("A", "B"))
    body = resp.text
    assert "内部配置备注" not in body
    assert "evidence_ref" not in body
    # ⚠️ level_scale（等级区间表）是内部算分配置
    assert "level_scale" not in body
    assert "min_ratio" not in body


def test_peer_score_excluded_reason_is_categorized(client, seeded) -> None:
    """★ 未参与算分的原因**给**，但原始 warning 文本**不给**。"""
    resp = call(client, peer_token("A", "B"))
    data = resp.json()["data"]
    reason = data["excluded_reason"]
    assert reason is not None
    assert "缺少目标值" in reason
    # ⚠️ 原始 warning 含指标名，属于内部细节
    assert "指标 visit" not in resp.text


# ---------------------------------------------------------------------------
# ★ 核心：目标人只能来自令牌
# ---------------------------------------------------------------------------


def test_request_body_cannot_specify_subject(client, seeded) -> None:
    """★ 请求体传 employee_id ⇒ 422（extra="forbid"）。

    ⚠️ 这是"后端硬权限"的关键一环 ——
       目标人**只能**来自签名令牌里的 ``subj``。
    """
    resp = call(client, peer_token("A", "B"), {
        "period": "202611", "employee_id": "HR",
    })
    assert resp.status_code == 422, resp.text


def test_token_subject_determines_target(client, seeded) -> None:
    """令牌里的 subj 决定查谁 —— 换个 subj 就换个目标。

    ⚠️ 注意：``subj`` 指回**自己**时，走的是「查自己」分支
    （``is_self`` 判定），不是 peer 分支 ——
    工具层用「目标人是不是提问者本人」判断，而不是「令牌有没有 subj」。
    """
    resp = call(client, peer_token("A", "B"))
    assert resp.status_code == 200
    # ⚠️ A 自己没有绩效记录 ⇒ 404（而不是 403）
    resp_self = call(client, peer_token("A", "A"))
    assert resp_self.status_code == 404, resp_self.text


def test_forged_token_is_rejected(client, seeded) -> None:
    """伪造令牌 ⇒ 401。"""
    resp = call(client, "not-a-real-token")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# ★ 核心：越权场景
# ---------------------------------------------------------------------------


def test_cross_department_peer_denied(client, seeded) -> None:
    """★ 跨部门 ⇒ 403。

    ⚠️ 注意 X 与 B 汇报线**不同**（X→BOSS，B→MGR），
    但即便汇报线相同，跨部门也必须拒绝。
    """
    resp = call(client, peer_token("A", "X"))
    assert resp.status_code == 403, resp.text


def test_query_manager_denied(client, seeded) -> None:
    """★ 查上一级 ⇒ 403。"""
    resp = call(client, peer_token("A", "MGR"))
    assert resp.status_code == 403, resp.text


def test_hr_target_via_peer_token_denied(client, seeded) -> None:
    """★ 用 peer 令牌查 HR ⇒ 403。

    ⚠️ HR 有全组织权限，但**peer 令牌**的语义是"查平级"，
    不能用 peer 令牌去查 HR —— 否则令牌就成了万能钥匙。
    """
    resp = call(client, peer_token("A", "HR"))
    assert resp.status_code == 403, resp.text


def test_exited_subject_denied(client, seeded, db) -> None:
    """★ 目标离职 ⇒ 403（离职员工的绩效不通过问答开放）。"""
    db.get(Employee, "B").employment_status = "exited"
    db.commit()
    resp = call(client, peer_token("A", "B"))
    assert resp.status_code == 403, resp.text


def test_self_token_still_works_for_own_score(client, seeded) -> None:
    """不带 subj 的令牌 ⇒ 查自己，走原有工具路径不受影响。"""
    token = issue_agent_tool_token(
        employee_id="B", task_id="T1",
        allow=["get_metric_values", "get_scoring_breakdown"],
    )
    resp = client.post(
        "/v1/agent-tools/get_scoring_breakdown",
        json={"period": "202611"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


def test_tool_not_in_allow_list_rejected(client, seeded) -> None:
    """令牌没授权 get_peer_score ⇒ 403。"""
    token = issue_agent_tool_token(
        employee_id="A", task_id="T1", allow=["get_metric_values"],
    )
    resp = call(client, token)
    assert resp.status_code == 403, resp.text


def test_evidence_tool_blocked_for_peer_token(client, seeded) -> None:
    """★ peer 令牌不能调 ``get_evidence`` —— 证据原文不外泄。

    ⚠️ 即使令牌 allow 里写了它，也不行 ——
    因为 peer 令牌只签发 ``get_peer_score``，
    且证据工具在 peer 语境下必须走裁剪版。
    """
    token = issue_agent_tool_token(
        employee_id="A", task_id="T1",
        allow=["get_peer_score", "get_evidence"],
        scope="peer", subject_id="B",
    )
    resp = client.post(
        "/v1/agent-tools/get_evidence",
        json={"period": "202611"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # ⚠️ 若放行，返回里会有「证据原文」 —— 必须 403
    assert resp.status_code == 403, resp.text
    assert "客户合同扫描件" not in resp.text
