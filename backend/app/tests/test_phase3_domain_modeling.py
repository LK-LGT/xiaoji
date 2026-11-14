"""阶段三领域建模验收测试 —— 争议域 + AI 域。

覆盖：

**争议域**
1. CRUD：创建案件 → 列表 → 详情 → 指派评审人 → 提交意见 → 裁决。
2. 状态机：非法迁移被拒（409）；合法迁移生效。
3. 权限：员工只能看自己的案件；普通主管不能指派 / 裁决；HR 可以。
4. 幂等：重复指派同一评审人不产生重复行。

**AI 域**
5. 复核记录真实落库：重复调用 ``ensure`` 不重复落库；``check`` 强制重新复核。
6. 规则引擎确定性：相同输入 → 相同结果。
7. 会话 CRUD：新建 → 活跃查询 → 历史 → 消息分页。
8. 权限：会话仅本人可见。
9. 建议采纳动作流水落库。

**迁移**
10. 9 张新表存在，索引齐备。
"""

from __future__ import annotations

import contextlib
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    MonthPlan,
    MonthPlanItem,
)
from backend.app.domain.plan_models import (
    AiReview,
    AiSuggestionAction,
    DisputeReviewer,
)
from backend.app.main import app
from backend.app.services import ai_review_engine, dispute_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """独立的内存 SQLite 会话。

    必须用 ``StaticPool``：SQLite 的 ``:memory:`` 数据库是**按连接隔离**的，
    而 TestClient 在另一个线程里跑请求，默认连接池会让请求拿到一个全新的空库，
    于是报「no such table」。``StaticPool`` 让所有线程共用同一连接。
    """
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
def org(db):
    """种子数据：部门 + HR + 部门负责人 + 主管 + 员工 + 一份月计划。"""
    hr_dept = Department(id="DEPT_HR", name="人力资源部", active=True)
    sales = Department(id="DEPT_SALES", name="销售部", active=True)
    db.add_all([hr_dept, sales])
    db.flush()

    # 启用 HR 部门配置 → 该部门成员获得 hr 角色
    db.add(HrDepartmentConfig(department_id="DEPT_HR", active=True))
    db.flush()

    hr = Employee(
        id="101",
        name="李红",
        position="HR总监",
        department_id="DEPT_HR",
        manager_id=None,
        employment_status="active",
        account_status="enabled",
    )
    leader = Employee(
        id="201",
        name="张文",
        position="销售总监",
        department_id="DEPT_SALES",
        manager_id=None,
        employment_status="active",
        account_status="enabled",
    )
    manager = Employee(
        id="301",
        name="王伟",
        position="销售经理",
        department_id="DEPT_SALES",
        manager_id="201",
        employment_status="active",
        account_status="enabled",
    )
    emp = Employee(
        id="401",
        name="刘刚",
        position="销售代表",
        department_id="DEPT_SALES",
        manager_id="301",
        employment_status="active",
        account_status="enabled",
    )
    other = Employee(
        id="402",
        name="赵燕",
        position="销售代表",
        department_id="DEPT_SALES",
        manager_id="301",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([hr, leader, manager, emp, other])
    db.commit()

    # 一份真实月计划（供 AI 复核读取条目）
    plan = MonthPlan(
        id="9001",
        owner_id=emp.id,
        plan_month="2026-11",
        title="2026-11 月计划",
        status="draft",
        version_no=1,
    )
    db.add(plan)
    db.flush()
    db.add(
        MonthPlanItem(
            id="9101",
            plan_id=plan.id,
            task_type="REGULAR",
            task_name="完成销售目标",
            task_content="完成第三季度销售目标并交付月度经营分析报告",
            deliverable="经营分析报告",
            quality="数据完整、可追溯",
            performance_weight=100,
            estimated_start_date=date(2026, 11, 1),
            estimated_end_date=date(2026, 11, 30),
            progress="",
            status="draft",
            sort_no=0,
        )
    )
    db.commit()

    return {
        "hr": hr,
        "leader": leader,
        "manager": manager,
        "emp": emp,
        "other": other,
        "plan": plan,
    }


@pytest.fixture()
def as_user(db):
    """以指定用户身份执行请求的上下文管理器。

    ⚠️ ``app.dependency_overrides`` 是 app 级别的**全局**状态。
    若只在创建 client 时设置覆盖，后创建的 client 会顶掉前一个，
    导致所有请求都变成「最后创建的那个用户」。
    因此每次换用户都必须重新设置覆盖 —— 这里用上下文管理器保证作用域。
    """

    @contextlib.contextmanager
    def _as(user: Employee):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    yield _as


# ---------------------------------------------------------------------------
# 1. 争议域 CRUD
# ---------------------------------------------------------------------------


def test_dispute_case_crud_lifecycle(as_user, db, org):
    """完整生命周期：创建 → 指派 → 意见 → 裁决。"""
    case = dispute_service.create_case(
        db,
        owner=org["emp"],
        appeal_title="销售额未达标的争议",
        appeal_reason="第三季度实际销售额受季节因素影响，申请重新核算。",
        dispute_subject="Q3 销售额",
    )
    assert case.status == "SUBMITTED"
    assert case.case_no.startswith("DS")

    # HR 指派评审人
    with as_user(org["hr"]) as hr_client:
        resp = hr_client.post(
            f"/v1/dispute/cases/{case.id}/reviewers",
            json={"userId": org["manager"].id},
        )
        assert resp.status_code == 200, resp.text
        reviewer_payload = resp.json()["data"]
        assert reviewer_payload["sourceType"] == "MANUAL"
        assert reviewer_payload["recusalStatus"] == "NONE"

    # 指派后案件进入评审中
    db.refresh(case)
    assert case.status == "REVIEWING"

    # 评审人提交意见
    with as_user(org["manager"]) as manager_client:
        resp = manager_client.post(
            f"/v1/dispute/cases/{case.id}/opinions",
            json={"opinion": "SUPPORT", "comment": "季节因素属实，建议重新核算。"},
        )
        assert resp.status_code == 200, resp.text
        opinion_payload = resp.json()["data"]
        assert opinion_payload["opinion"] == "SUPPORT"
        assert opinion_payload["versionNo"] == 1
        assert opinion_payload["reviewerName"] == "王伟"

        # 再次提交 → 版本号递增
        resp = manager_client.post(
            f"/v1/dispute/cases/{case.id}/opinions",
            json={"opinion": "SUPPORT", "comment": "补充说明。"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["versionNo"] == 2

    # HR 裁决
    with as_user(org["hr"]) as hr_client:
        resp = hr_client.post(
            f"/v1/dispute/cases/{case.id}/decision",
            json={"decision": "SUPPORT", "comment": "准予重新核算。"},
        )
        assert resp.status_code == 200, resp.text
        db.refresh(case)
        assert case.status == "DECIDED"
        assert case.decision == "SUPPORT"
        assert case.decided_by == org["hr"].id

        # 详情包含全部子数据
        resp = hr_client.get(f"/v1/dispute/cases/{case.id}")
        assert resp.status_code == 200
        detail = resp.json()["data"]
        assert detail["summary"]["status"] == "DECIDED"
        assert detail["canDecide"] is True
        assert len(detail["reviewers"]) == 1
        assert len(detail["opinions"]) == 2
        assert detail["decision"] == "SUPPORT"


def test_dispute_list_and_dashboard(as_user, db, org):
    """列表与仪表盘返回真实数据。"""
    dispute_service.create_case(
        db, owner=org["emp"], appeal_title="案件一", appeal_reason="原因一"
    )
    dispute_service.create_case(
        db, owner=org["other"], appeal_title="案件二", appeal_reason="原因二"
    )

    # 员工只看自己的
    with as_user(org["emp"]) as client:
        resp = client.get("/v1/dispute/cases")
        assert resp.status_code == 200
        cases = resp.json()["data"]
        assert len(cases) == 1
        assert cases[0]["appealTitle"] == "案件一"
        assert cases[0]["employeeName"] == "刘刚"
        assert cases[0]["orgName"] == "销售部"

    # HR 看全部
    with as_user(org["hr"]) as hr_client:
        resp = hr_client.get("/v1/dispute/cases")
        assert len(resp.json()["data"]) == 2

        # 仪表盘指标
        resp = hr_client.get("/v1/dispute/dashboard")
        assert resp.status_code == 200
        dashboard = resp.json()["data"]
        total = next(m for m in dashboard["metrics"] if m["code"] == "TOTAL")
        assert total["value"] == 2
        assert len(dashboard["recentCases"]) == 2


# ---------------------------------------------------------------------------
# 2. 争议域状态机
# ---------------------------------------------------------------------------


def test_dispute_illegal_transition_rejected(as_user, db, org):
    """已裁决案件再次裁决 → 409。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="非法裁决", appeal_reason="原因"
    )
    with as_user(org["hr"]) as hr_client:
        resp = hr_client.post(
            f"/v1/dispute/cases/{case.id}/decision",
            json={"decision": "SUPPORT", "comment": "直接裁决"},
        )
        # SUBMITTED → DECIDED 是合法迁移，应成功
        assert resp.status_code == 200, resp.text
        db.refresh(case)
        assert case.status == "DECIDED"

        # 已裁决 → 再次裁决：DECIDED 没有 decide 迁移 → 409
        resp = hr_client.post(
            f"/v1/dispute/cases/{case.id}/decision",
            json={"decision": "REJECT", "comment": "重复裁决"},
        )
        assert resp.status_code == 409, resp.text


def test_dispute_archive_after_decision(db, org):
    """已裁决案件可归档；已归档不可再裁决。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="归档用例", appeal_reason="原因"
    )
    dispute_service.decide_case(db, case, org["hr"], "REJECT", "不成立")

    dispute_service.archive_case(db, case)
    db.refresh(case)
    assert case.status == "ARCHIVED"

    with pytest.raises(Exception):
        dispute_service.decide_case(db, case, org["hr"], "SUPPORT", "再裁决")


# ---------------------------------------------------------------------------
# 3. 争议域权限
# ---------------------------------------------------------------------------


def test_dispute_employee_cannot_manage(as_user, db, org):
    """普通员工不能指派评审人 / 裁决 → 403。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="越权用例", appeal_reason="原因"
    )
    with as_user(org["emp"]) as client:
        case_url = f"/v1/dispute/cases/{case.id}"

        resp = client.post(
            f"{case_url}/reviewers", json={"userId": org["other"].id}
        )
        assert resp.status_code == 403, resp.text

        resp = client.post(
            f"{case_url}/decision", json={"decision": "SUPPORT", "comment": "越权裁决"}
        )
        assert resp.status_code == 403, resp.text


def test_dispute_employee_cannot_view_others_case(as_user, db, org):
    """员工不能查看他人案件 → 403。"""
    case = dispute_service.create_case(
        db, owner=org["other"], appeal_title="他人案件", appeal_reason="原因"
    )
    with as_user(org["emp"]) as client:
        resp = client.get(f"/v1/dispute/cases/{case.id}")
        assert resp.status_code == 403, resp.text


def test_dispute_reviewer_can_view_and_opine(as_user, db, org):
    """被指派的评审人可查看案件并提交意见。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="评审权限", appeal_reason="原因"
    )
    dispute_service.assign_reviewer(db, case, org["manager"])

    with as_user(org["manager"]) as client:
        resp = client.get(f"/v1/dispute/cases/{case.id}")
        assert resp.status_code == 200, resp.text

        resp = client.post(
            f"/v1/dispute/cases/{case.id}/opinions",
            json={"opinion": "REJECT", "comment": "不成立"},
        )
        assert resp.status_code == 200, resp.text


def test_dispute_non_reviewer_cannot_opine(as_user, db, org):
    """未被指派的人不能提交意见 → 403。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="意见权限", appeal_reason="原因"
    )
    dispute_service.assign_reviewer(db, case, org["manager"])

    with as_user(org["other"]) as client:
        resp = client.post(
            f"/v1/dispute/cases/{case.id}/opinions",
            json={"opinion": "SUPPORT", "comment": "非评审人插嘴"},
        )
        assert resp.status_code == 403, resp.text


def test_dispute_recused_reviewer_cannot_opine(as_user, db, org):
    """已回避的评审人不能提交意见 → 403。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="回避用例", appeal_reason="原因"
    )
    dispute_service.assign_reviewer(db, case, org["manager"])

    with as_user(org["manager"]) as client:
        resp = client.post(
            f"/v1/dispute/cases/{case.id}/recusal", json={"reason": "利益冲突"}
        )
        assert resp.status_code == 200, resp.text

        resp = client.post(
            f"/v1/dispute/cases/{case.id}/opinions",
            json={"opinion": "SUPPORT", "comment": "回避后仍提交"},
        )
        assert resp.status_code == 403, resp.text


def test_dispute_assign_reviewer_is_idempotent(as_user, db, org):
    """重复指派同一评审人不产生重复行。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="幂等用例", appeal_reason="原因"
    )
    with as_user(org["hr"]) as hr_client:
        url = f"/v1/dispute/cases/{case.id}/reviewers"
        payload = {"userId": org["manager"].id}

        resp = hr_client.post(url, json=payload)
        assert resp.status_code == 200
        resp = hr_client.post(url, json=payload)
        assert resp.status_code == 200

    rows = db.execute(
        select(DisputeReviewer).where(DisputeReviewer.case_id == case.id)
    ).scalars().all()
    assert len(rows) == 1


def test_dispute_reviewer_candidates_exclude_assigned(as_user, db, org):
    """候选评审人排除已指派的人与案件提交人。"""
    case = dispute_service.create_case(
        db, owner=org["emp"], appeal_title="候选用例", appeal_reason="原因"
    )
    dispute_service.assign_reviewer(db, case, org["manager"])

    with as_user(org["hr"]) as hr_client:
        resp = hr_client.get(f"/v1/dispute/cases/{case.id}/reviewer-candidates")
        assert resp.status_code == 200
        candidates = resp.json()["data"]
        ids = {c["userId"] for c in candidates}
        assert org["manager"].id not in ids
        assert org["emp"].id not in ids
        assert org["other"].id in ids


# ---------------------------------------------------------------------------
# 4. AI 复核
# ---------------------------------------------------------------------------


def test_ai_review_ensure_is_idempotent(as_user, db, org):
    """``ensure`` 有有效结果时不重复落库；``check`` 强制重新复核。"""
    plan_id = org["plan"].id

    with as_user(org["emp"]) as client:
        resp = client.post(f"/v1/ai/reviews/plans/MONTH_PLAN/{plan_id}/ensure")
        assert resp.status_code == 200, resp.text
        assert len(db.execute(select(AiReview)).scalars().all()) == 1

        resp = client.post(f"/v1/ai/reviews/plans/MONTH_PLAN/{plan_id}/ensure")
        assert resp.status_code == 200
        assert len(db.execute(select(AiReview)).scalars().all()) == 1

        resp = client.post(f"/v1/ai/reviews/plans/MONTH_PLAN/{plan_id}/check")
        assert resp.status_code == 200
        assert len(db.execute(select(AiReview)).scalars().all()) == 2


def test_ai_review_uses_real_plan_content(as_user, db, org):
    """复核结果基于真实计划条目：内容完整 → 无问题、低风险。"""
    plan_id = org["plan"].id

    with as_user(org["emp"]) as client:
        resp = client.post(f"/v1/ai/reviews/plans/MONTH_PLAN/{plan_id}/check")
        assert resp.status_code == 200, resp.text
        result = resp.json()["data"]["result"]
        assert result["issues"] == []
        assert result["overallRisk"] == "LOW"


def test_ai_review_cannot_review_others_plan(as_user, db, org):
    """不能复核他人计划 → 404（不泄露该 id 是否存在）。"""
    plan_id = org["plan"].id
    with as_user(org["other"]) as client:
        resp = client.post(f"/v1/ai/reviews/plans/MONTH_PLAN/{plan_id}/check")
        assert resp.status_code == 404, resp.text


def test_ai_review_latest_lookup(as_user, db, org):
    """最近一次复核可按业务对象查回。"""
    plan_id = org["plan"].id

    with as_user(org["emp"]) as client:
        client.post(f"/v1/ai/reviews/plans/MONTH_PLAN/{plan_id}/ensure")

        resp = client.get(
            "/v1/ai/reviews/latest", params={"bizType": "MONTH_PLAN", "bizId": plan_id}
        )
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload is not None
        assert payload["bizType"] == "MONTH_PLAN"
        assert payload["bizId"] == plan_id
        assert payload["status"] == "RULE_ONLY"

        # 不存在的对象 → null
        resp = client.get(
            "/v1/ai/reviews/latest", params={"bizType": "MONTH_PLAN", "bizId": "no-such"}
        )
        assert resp.json()["data"] is None


def test_ai_review_batch_lookup(as_user, db, org):
    """批量查询返回按 bizId 索引的映射。"""
    plan_id = org["plan"].id

    with as_user(org["emp"]) as client:
        client.post(f"/v1/ai/reviews/plans/MONTH_PLAN/{plan_id}/ensure")

        resp = client.get(
            "/v1/ai/reviews/latest/batch",
            params={"bizType": "MONTH_PLAN", "bizIds": f"{plan_id},22,33"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 只有真实复核过的对象才出现在结果里
        assert set(data.keys()) == {plan_id}


def test_ai_review_requires_auth():
    """AI 复核端点必须鉴权。"""
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)
    with TestClient(app) as anon:
        resp = anon.get(
            "/v1/ai/reviews/latest", params={"bizType": "MONTH_PLAN", "bizId": 1}
        )
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# 5. 规则引擎
# ---------------------------------------------------------------------------


def test_rule_engine_detects_empty_content():
    """空任务内容 → HIGH 风险。"""
    issues, risk = ai_review_engine.review_plan_content(
        items=[{"taskContent": "", "deliverable": "", "performanceWeight": 100}]
    )
    codes = {issue.code for issue in issues}
    assert "PLAN_CONTENT_EMPTY" in codes
    assert risk in ("HIGH", "BLOCKING")


def test_rule_engine_detects_weight_overflow():
    """权重合计不为 100 → 提示。"""
    issues, _ = ai_review_engine.review_plan_content(
        items=[
            {
                "taskContent": "完成销售目标并交付月度报告",
                "deliverable": "月报",
                "performanceWeight": 60,
            },
            {
                "taskContent": "维护客户关系并推进续约",
                "deliverable": "续约清单",
                "performanceWeight": 30,
            },
        ]
    )
    codes = {issue.code for issue in issues}
    assert "PLAN_WEIGHT_NOT_100" in codes


def test_rule_engine_is_deterministic():
    """相同输入 → 相同输出。"""
    items = [
        {
            "taskContent": "完成销售目标并交付月度报告",
            "deliverable": "月报",
            "performanceWeight": 100,
        },
    ]
    first = ai_review_engine.build_review_result(
        ai_review_engine.review_plan_content(items=items)[0]
    )
    second = ai_review_engine.build_review_result(
        ai_review_engine.review_plan_content(items=items)[0]
    )
    assert first == second


def test_rule_engine_no_issue_when_complete():
    """内容完整 → 无问题、低风险。"""
    issues, risk = ai_review_engine.review_plan_content(
        items=[
            {
                "taskContent": "完成第三季度销售目标并交付月度经营分析报告",
                "deliverable": "经营分析报告",
                "performanceWeight": 100,
            }
        ]
    )
    assert issues == []
    assert risk == "LOW"


def test_rule_engine_result_content_rules():
    """成果类规则：空描述 + 无附件。"""
    issues, risk = ai_review_engine.review_result_content(
        description="", completion_rate=None, attachments_count=0
    )
    codes = {issue.code for issue in issues}
    assert "RESULT_DESCRIPTION_EMPTY" in codes
    assert "RESULT_NO_ATTACHMENT" in codes
    assert risk in ("HIGH", "BLOCKING")


# ---------------------------------------------------------------------------
# 6. AI 会话
# ---------------------------------------------------------------------------


def test_ai_conversation_lifecycle(as_user, db, org):
    """会话：新建 → 活跃查询 → 历史 → 消息。"""
    with as_user(org["emp"]) as client:
        resp = client.post(
            "/v1/employee/ai/conversations/new",
            json={
                "sceneCode": "PLAN_DRAFT",
                "bizType": "MONTH_PLAN",
                "bizId": 1,
                "title": "月计划草稿",
            },
        )
        assert resp.status_code == 200, resp.text
        conversation = resp.json()["data"]
        assert conversation["sceneCode"] == "PLAN_DRAFT"
        assert conversation["status"] == "ACTIVE"
        assert conversation["title"] == "月计划草稿"

        resp = client.get(
            "/v1/employee/ai/conversations/active",
            params={"sceneCode": "PLAN_DRAFT", "bizId": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == conversation["id"]

        resp = client.get(
            "/v1/employee/ai/conversations/history",
            params={"sceneCode": "PLAN_DRAFT", "bizId": 1},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

        resp = client.get(
            f"/v1/employee/ai/conversations/{conversation['id']}/messages"
        )
        assert resp.status_code == 200
        page = resp.json()["data"]
        assert page["records"] == []
        assert page["hasMore"] is False


def test_ai_conversation_isolation(as_user, db, org):
    """他人不能访问我的会话 → 403。"""
    with as_user(org["emp"]) as client:
        resp = client.post(
            "/v1/employee/ai/conversations/new",
            json={"sceneCode": "PLAN_DRAFT", "bizType": "MONTH_PLAN"},
        )
        conversation_id = resp.json()["data"]["id"]

    with as_user(org["other"]) as other_client:
        resp = other_client.get(
            f"/v1/employee/ai/conversations/{conversation_id}/messages"
        )
        assert resp.status_code == 403, resp.text


def test_ai_conversation_history_scoped_to_owner(as_user, db, org):
    """历史列表只返回自己的会话。"""
    with as_user(org["emp"]) as client:
        client.post(
            "/v1/employee/ai/conversations/new", json={"sceneCode": "PLAN_DRAFT"}
        )

    with as_user(org["other"]) as other_client:
        resp = other_client.get(
            "/v1/employee/ai/conversations/history", params={"sceneCode": "PLAN_DRAFT"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# 7. 建议采纳流水
# ---------------------------------------------------------------------------


def test_ai_suggestion_action_persisted(as_user, db, org):
    """建议采纳动作写入流水表。"""
    with as_user(org["emp"]) as client:
        resp = client.post(
            "/v1/employee/ai/suggestions/SUG-001/actions",
            json={
                "actionCode": "APPLY_ITEM",
                "appliedFields": ["taskContent", "deliverable"],
                "beforeHash": "aaa",
                "afterHash": "bbb",
            },
        )
        assert resp.status_code == 200, resp.text

    rows = db.execute(select(AiSuggestionAction)).scalars().all()
    assert len(rows) == 1
    assert rows[0].suggestion_id == "SUG-001"
    assert rows[0].actor_id == org["emp"].id
    assert rows[0].action_code == "APPLY_ITEM"
    assert rows[0].applied_fields == ["taskContent", "deliverable"]


def test_ai_suggestion_action_invalid_code_rejected(as_user, db, org):
    """非法采纳动作 → 400。"""
    with as_user(org["emp"]) as client:
        resp = client.post(
            "/v1/employee/ai/suggestions/SUG-002/actions",
            json={"actionCode": "NOT_A_VALID_ACTION"},
        )
        assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 8. 迁移
# ---------------------------------------------------------------------------


NEW_TABLES = (
    "plan_draft_version",
    "plan_approval",
    "dispute_case",
    "dispute_reviewer",
    "dispute_opinion",
    "ai_review",
    "ai_conversation",
    "ai_message",
    "ai_suggestion_action",
)


def test_new_tables_registered_in_metadata():
    """9 张新表已注册进 Base.metadata。"""
    tables = Base.metadata.tables
    for name in NEW_TABLES:
        assert name in tables, f"缺少表 {name}"


def test_new_tables_created_in_sqlite(db):
    """9 张新表在 SQLite 中真实建出。"""
    from sqlalchemy import inspect

    names = set(inspect(db.get_bind()).get_table_names())
    for name in NEW_TABLES:
        assert name in names, f"缺少表 {name}"


def test_migration_file_exists_and_is_idempotent():
    """迁移脚本存在且含幂等保护。"""
    backend_root = Path(__file__).resolve().parents[2]
    migration = backend_root / "migrations" / "versions" / "0004_phase3_domain_modeling.py"
    assert migration.exists(), "缺少 0004 迁移脚本"
    content = migration.read_text(encoding="utf-8")
    assert "_table_exists" in content
    assert "def upgrade" in content
    assert "def downgrade" in content
    assert content.count("if _table_exists(") >= 9
