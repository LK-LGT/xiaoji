"""Step 6 计划助手工具层 —— 权限边界、脱敏、只读保证。

覆盖三条红线：
1. **越权不可能** —— 令牌决定身份，请求参数不能指定别人。
2. **部门可见 ≠ 可原样给模型** —— 脱敏必须真的生效。
3. **只读** —— 工具不改任何业务数据。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import (
    AiToolCall,
    Base,
    Department,
    Employee,
    MonthPlan,
    MonthPlanItem,
)
from backend.app.routes.plan_tools import router
from backend.app.services import plan_tool_service
from backend.app.services.agent_tool_token import issue_agent_tool_token

MY_TOOL = "get_my_plan_history"
DEPT_TOOL = "get_department_plan_history"


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_secret", "plan-tools-independent-test-secret")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Department(id="D1", name="研发部"))
        db.add(Department(id="D2", name="市场部"))
        db.flush()
        db.add(Employee(id="ME", name="我", department_id="D1",
                        employment_status="active", account_status="enabled"))
        db.add(Employee(id="PEER", name="张三", department_id="D1",
                        employment_status="active", account_status="enabled"))
        db.add(Employee(id="OUT", name="李四", department_id="D2",
                        employment_status="active", account_status="enabled"))
        db.add(Employee(id="LEFT", name="王五", department_id="D1",
                        employment_status="resigned", account_status="enabled"))
        db.flush()

        # ⚠️ **plan_month 必须用库内真实格式 ``YYYY-MM``**（带横杠）。
        #
        # ⚠️ **BUG（真机端到端验证时暴露）**：这里原来写 ``"202610"``（紧凑），
        #    与生产库的真实格式 ``"2026-10"`` 不一致 —— 于是
        #    **测试永远查不到数据但断言照过**，把一个真实的取数 bug 盖住了。
        #
        #    真机上的表现：工具恒返回 ``planCount: 0``，
        #    模型据此说「未查到历史计划」→ **参考材料静默失效**，
        #    而单测全绿。
        #
        #    所以测试数据必须**和真实写入的格式一模一样**。
        plan = MonthPlan(id="P1", owner_id="ME", plan_month="2026-10", title="我的计划")
        db.add(plan)
        db.flush()
        db.add(MonthPlanItem(id="MI1", plan_id="P1", task_name="我的任务",
                             task_content="我的任务内容", deliverable="交付物A"))

        peer_plan = MonthPlan(id="P2", owner_id="PEER", plan_month="2026-10",
                              title="同事计划", project_name="与王五合作的项目")
        db.add(peer_plan)
        db.flush()
        db.add(MonthPlanItem(id="PI1", plan_id="P2", task_name="张三负责的支付重构",
                             task_content="需要与李经理对接完成迁移",
                             deliverable="迁移脚本", performance_weight=30.0,
                             estimated_value=99999.0))
        db.commit()

        app = FastAPI()
        app.include_router(router)

        def dependency():
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

        app.dependency_overrides[get_db] = dependency
        with TestClient(app) as client:
            yield db, client
    engine.dispose()


def headers(scope="self", allow=None, employee_id="ME"):
    token = issue_agent_tool_token(
        employee_id=employee_id, task_id="plan", allow=allow or [MY_TOOL, DEPT_TOOL], scope=scope,
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. 越权边界
# ---------------------------------------------------------------------------


def test_my_plan_history_only_returns_own_plans(setup):
    """「我的历史计划」只能查到自己 —— 同事的计划不能出现。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history", json={"months": 12},
                           headers=headers())
    assert response.status_code == 200
    data = response.json()["data"]
    titles = [item["taskName"] for item in data["items"]]
    assert "我的任务" in titles
    assert not any("张三" in t for t in titles)


def test_my_plan_history_has_no_employee_id_parameter(setup):
    """请求体里没有能指定别人的字段 —— 参数越权在协议层就不可能。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history",
                           json={"months": 12, "employee_id": "PEER"},
                           headers=headers())
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 0. 取数格式（真机端到端验证时暴露的 bug）
# ---------------------------------------------------------------------------


def test_month_range_uses_stored_plan_month_format(setup):
    """``_month_range`` 必须产出**库内真实格式** ``YYYY-MM``。

    ⚠️ **BUG（真机端到端验证时暴露）**：原来返回 ``YYYYMM``（``202606``~``202611``），
    拿它和库里的 ``plan_month``（``2026-10``）做**字符串比较**：

        "2026-10" >= "202606"   →  False

    ``-`` 的 ASCII 码 45 < ``0`` 的 48 → **所有历史计划都查不到**。
    真机表现：工具恒返回 ``planCount: 0``，模型据此说「未查到历史计划」
    → **参考材料静默失效**，而单测因为造数格式也不一致，一直是绿的。

    这个断言锁住格式 —— 一旦有人把格式改回 ``YYYYMM``，这里立刻红。
    """
    db, client = setup
    start, end = plan_tool_service._month_range(12)
    assert "-" in start and "-" in end, f"月份范围必须是 YYYY-MM，实际 {start}~{end}"
    # 与库里真实写入的格式必须**可比较**
    assert "2026-10" >= start, f"库内格式 {start} 与 '2026-10' 不可比较"


def test_my_plan_history_finds_plans_with_real_stored_format(setup):
    """用**真实库内格式**的历史计划，工具必须查得到。

    ⚠️ 这条是上面那个 bug 的**行为级**断言：
    工具返回的 ``planCount`` 必须 > 0，否则说明取数静默失效了。
    """
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history", json={"months": 12},
                           headers=headers())
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True, f"应查到历史计划，实际 {data}"
    assert data["planCount"] >= 1, f"planCount 应 >=1，实际 {data['planCount']}"
    assert data["itemCount"] >= 1, f"itemCount 应 >=1，实际 {data['itemCount']}"


def test_department_history_requires_department_scope(setup):
    """只读令牌（scope=self）调不动部门工具。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers=headers(scope="self"))
    assert response.status_code == 403


def test_department_history_only_same_department(setup):
    """部门工具只看同部门在职同事。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers=headers(scope="department"))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["itemCount"] >= 1
    assert data.get("peerCount") == 1


def test_resigned_peer_excluded(setup):
    """离职同事不进部门参考。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers=headers(scope="department"))
    data = response.json()["data"]
    assert data["peerCount"] == 1


def test_token_allow_list_is_enforced(setup):
    """令牌白名单外的工具调不动。"""
    db, client = setup
    token = issue_agent_tool_token(
        employee_id="ME", task_id="plan", allow=[MY_TOOL], scope="department",
    )
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_missing_token_rejected(setup):
    """没令牌 -> 401。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history", json={"months": 12})
    assert response.status_code == 401


def test_inactive_employee_token_rejected(setup):
    """离职员工的令牌失效。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history", json={"months": 12},
                           headers=headers(employee_id="LEFT"))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 2. 脱敏
# ---------------------------------------------------------------------------


def test_department_history_strips_owner_identity(setup):
    """部门数据不返回 owner 标识 —— 只有不可回推的别名。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers=headers(scope="department"))
    data = response.json()["data"]
    blob = str(data)
    assert "张三" not in blob
    assert "李四" not in blob
    assert "PEER" not in blob
    assert "authorAlias" in data["items"][0]


def test_department_history_strips_person_names_in_text(setup):
    """计划文本里出现的人名要被替换掉。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers=headers(scope="department"))
    data = response.json()["data"]
    blob = str(data)
    assert "李经理" not in blob
    assert "王五" not in blob


def test_department_history_excludes_weight_and_value(setup):
    """绩效权重与预估价值属于考核数据，不进部门参考。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers=headers(scope="department"))
    data = response.json()["data"]
    assert "performanceWeight" not in str(data)
    assert "estimatedValue" not in str(data)
    assert "99999" not in str(data)


def test_department_history_excludes_approval_comment(setup):
    """审批意见是主管对个人的评语 —— 不能进部门参考。"""
    db, client = setup
    plan = db.get(MonthPlan, "P2")
    plan.approval_comment = "张三本月表现一般，需要改进"
    db.commit()
    response = client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                           headers=headers(scope="department"))
    assert "需要改进" not in str(response.json()["data"])


def test_own_plan_history_keeps_original_text(setup):
    """自己的计划不用洗 —— 那是我自己的内容。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history", json={"months": 12},
                           headers=headers())
    data = response.json()["data"]
    assert data["items"][0]["taskContent"] == "我的任务内容"


# ---------------------------------------------------------------------------
# 3. 只读
# ---------------------------------------------------------------------------


def test_tools_do_not_modify_data(setup):
    """调完工具，业务数据一个字节都不能变。"""
    db, client = setup
    stmt = select(MonthPlanItem.id, MonthPlanItem.task_name, MonthPlanItem.deliverable)
    snapshot_before = list(db.execute(stmt).all())
    plan_before = db.get(MonthPlan, "P1")
    title_before, status_before = plan_before.title, plan_before.status

    client.post("/v1/plan-tools/get_my_plan_history", json={"months": 12}, headers=headers())
    client.post("/v1/plan-tools/get_department_plan_history", json={"months": 12},
                headers=headers(scope="department"))

    assert list(db.execute(stmt).all()) == snapshot_before
    plan_after = db.get(MonthPlan, "P1")
    assert (plan_after.title, plan_after.status) == (title_before, status_before)


def test_audit_record_written(setup):
    """工具调用要留审计。"""
    db, client = setup
    client.post("/v1/plan-tools/get_my_plan_history", json={"months": 12}, headers=headers())
    calls = db.query(AiToolCall).filter_by(tool=MY_TOOL).all()
    assert len(calls) == 1
    assert calls[0].ok is True
    assert calls[0].agent == "plan"


# ---------------------------------------------------------------------------
# 4. 边界
# ---------------------------------------------------------------------------


def test_empty_history_returns_available_false(setup):
    """没有历史计划时 available=False —— 不假装有。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history", json={"months": 1},
                           headers=headers())
    data = response.json()["data"]
    assert data["available"] is False
    assert data["itemCount"] == 0
    assert data["note"]


def test_months_bounded(setup):
    """months 参数有上下限 —— 不能一次拉全库。"""
    db, client = setup
    response = client.post("/v1/plan-tools/get_my_plan_history", json={"months": 999},
                           headers=headers())
    assert response.status_code == 422


def test_scrubber_keeps_ordinary_text(setup):
    """脱敏不能误伤普通业务文本。"""
    assert plan_tool_service._scrub_person_names("优化支付链路的稳定性") == "优化支付链路的稳定性"
    assert plan_tool_service._scrub_person_names("完成季度述职") == "完成季度述职"
    assert "张三" not in plan_tool_service._scrub_person_names("张三负责")
    assert "李经理" not in plan_tool_service._scrub_person_names("与李经理对接")
