"""Step 6 计划助手端点 —— 开关、契约、降级、字段收敛。

三条红线：
1. 默认关闭 —— 关闭时端点返回结构完整的空结果，前端不崩。
2. 模型产不出考核数字 —— 权重/预估价值固定为 0。
3. 智能体挂了就降级 —— 不抛异常、不白屏。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, settings
from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import Base, Department, Employee, MonthPlan, MonthPlanItem
from backend.app.routes.employee_ai import router
from backend.app.services import plan_ai_service


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "plan_ai_enabled", True)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Department(id="D1", name="研发部"))
        db.flush()
        db.add(Employee(id="ME", name="我", department_id="D1",
                        employment_status="active", account_status="enabled"))
        db.flush()
        # ⚠️ **plan_month 用库内真实格式 ``YYYY-MM``**（带横杠）。
        #
        # ⚠️ **BUG（真机端到端验证时暴露）**：这里原来写 ``"202610"``（紧凑），
        #    与生产库真实格式 ``"2026-10"`` 不一致 → 历史计划查不到 →
        #    ``historyPlanCount`` 恒为 0。测试因为造数格式相同而一直是绿的，
        #    把一个真实的取数 bug 盖住了。
        plan = MonthPlan(id="P1", owner_id="ME", plan_month="2026-10", title="上月计划")
        db.add(plan)
        db.flush()
        db.add(MonthPlanItem(id="MI1", plan_id="P1", task_name="历史任务",
                             task_content="历史任务内容", deliverable="历史交付物"))
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
        app.dependency_overrides[get_current_user] = lambda: db.get(Employee, "ME")
        with TestClient(app) as client:
            yield db, client
    engine.dispose()


def patch_agent(monkeypatch, result):
    """把智能体调用替换成固定返回。"""

    async def fake_call(db, **kwargs):
        return result

    monkeypatch.setattr(plan_ai_service, "call_plan_agent", fake_call)


def ok_result(**overrides):
    """构造一份标准的智能体返回。"""
    result = {
        "items": [], "findings": [], "warnings": [], "missingContext": [],
        "degraded": False, "toolCalls": [], "sources": [],
    }
    result.update(overrides)
    return result


# ---------------------------------------------------------------------------
# 1. 开关
# ---------------------------------------------------------------------------


def test_disabled_returns_empty_but_complete_shape(setup, monkeypatch):
    """关闭时返回结构完整的空结果 —— 前端不崩、不白屏。"""
    db, client = setup
    monkeypatch.setattr(settings, "plan_ai_enabled", False)
    response = client.post("/v1/employee/ai/month-plans/generate",
                           json={"requestId": "r1", "planMonth": "202611", "intentText": "生成"})
    assert response.status_code == 200
    data = response.json()["data"]
    for field in ("suggestionId", "items", "warnings", "missingContext",
                  "notice", "draftVersionId", "draftVersionNo", "effectiveOperationMode"):
        assert field in data, f"缺字段 {field}"
    assert data["items"] == []


def test_disabled_context_reports_not_enabled(setup, monkeypatch):
    """关闭时上下文如实说未启用。"""
    db, client = setup
    monkeypatch.setattr(settings, "plan_ai_enabled", False)
    response = client.get("/v1/employee/ai/month-plans/context", params={"planMonth": "202611"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is False
    assert "未启用" in data["notice"]


def test_default_is_disabled():
    """**代码默认值**必须关闭 —— 计划会变成考核指标，不能默认开。

    ⚠️ 这里断言的是**代码里的默认值**，不是「当前环境的实际值」。
    所以不能直接 ``Settings()`` —— 它会读 ``backend/.env``，
    而 ``.env`` 里可能已经配了 ``PLAN_AI_ENABLED=true``（生产启用），
    那会让这条断言随环境漂移。

    要断言代码默认值，得**显式传一个空环境**：
    ``_env_file=None`` 让 pydantic-settings 不加载任何 ``.env``。
    """
    fresh = Settings(
        agent_tool_secret="x" * 40,
        agent_shared_secret="y" * 40,
        # ⚠️ 不读 .env 就意味着**所有**环境变量都拿不到，
        #    包括必填的 ``auth_token_secret`` —— 只能显式补上。
        auth_token_secret="z" * 40,
        _env_file=None,
    )
    assert fresh.plan_ai_enabled is False


# ---------------------------------------------------------------------------
# 2. 上下文
# ---------------------------------------------------------------------------


def test_context_reports_real_history(setup):
    """上下文里的历史计划数是真实数据。"""
    db, client = setup
    response = client.get("/v1/employee/ai/month-plans/context", params={"planMonth": "202611"})
    data = response.json()["data"]
    assert data["enabled"] is True
    assert data["historyPlanCount"] >= 1
    assert "HISTORY_PLANS" in data["availableContext"]


def test_context_options_mark_unavailable_honestly(setup):
    """没有的上下文类型必须标 available=False —— 不假装有。"""
    db, client = setup
    response = client.get("/v1/employee/ai/month-plans/context", params={"planMonth": "202611"})
    options = {o["type"]: o for o in response.json()["data"]["contextOptions"]}
    assert options["JOB_DESCRIPTION"]["available"] is False
    assert options["ENTERPRISE_KNOWLEDGE"]["available"] is False


# ---------------------------------------------------------------------------
# 3. 生成 —— 字段收敛
# ---------------------------------------------------------------------------


def test_generated_items_have_fixed_weight_and_value(setup, monkeypatch):
    """权重与预估价值固定为 0 —— 模型不能决定考核权重。"""
    db, client = setup
    patch_agent(monkeypatch, ok_result(items=[{
        "taskName": "支付优化", "taskContent": "优化支付链路",
        "deliverable": "上线报告", "quality": "P0 为 0",
        "estimatedStartDate": "2026-11-01", "estimatedEndDate": "2026-11-30",
        "performanceWeight": 80, "estimatedValue": 999999,
    }]))
    response = client.post("/v1/employee/ai/month-plans/generate",
                           json={"requestId": "r1", "planMonth": "202611"})
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["taskName"] == "支付优化"
    assert item["performanceWeight"] == 0
    assert item["estimatedValue"] == 0


def test_generated_item_has_all_contract_fields(setup, monkeypatch):
    """条目字段必须完整 —— 前端按字段取值，缺一个就 undefined。"""
    db, client = setup
    patch_agent(monkeypatch, ok_result(items=[{"taskName": "T", "taskContent": "C"}]))
    response = client.post("/v1/employee/ai/month-plans/generate",
                           json={"requestId": "r1", "planMonth": "202611"})
    item = response.json()["data"]["items"][0]
    for field in ("workType", "taskName", "taskContent", "deliverable", "quality",
                  "estimatedStartDate", "estimatedEndDate",
                  "performanceWeight", "estimatedValue"):
        assert field in item, f"缺字段 {field}"


# ---------------------------------------------------------------------------
# 4. 检查 —— 只提示不阻断
# ---------------------------------------------------------------------------


def test_check_returns_issues_in_contract_shape(setup, monkeypatch):
    """体检结论按 AiCheckIssue 形状返回。"""
    db, client = setup
    patch_agent(monkeypatch, ok_result(findings=[
        {"kind": "duplicate", "severity": "warning", "targetIndex": 0,
         "message": "与上月重复", "suggestion": "换个角度"},
    ]))
    response = client.post("/v1/employee/ai/month-plans/check",
                           json={"requestId": "r1", "planMonth": "202611"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["issues"]) == 1
    issue = data["issues"][0]
    for field in ("code", "level", "fieldPath", "message", "suggestion"):
        assert field in issue, f"缺字段 {field}"
    assert issue["code"] == "duplicate"
    assert issue["level"] == "WARNING"


def test_check_never_uses_high_level(setup, monkeypatch):
    """体检不阻断 —— level 最高只能是 WARNING，不能用 HIGH 吓人。"""
    db, client = setup
    patch_agent(monkeypatch, ok_result(findings=[
        {"kind": "duplicate", "severity": "warning", "targetIndex": -1,
         "message": "重复", "suggestion": "改"},
    ]))
    response = client.post("/v1/employee/ai/month-plans/check",
                           json={"requestId": "r1", "planMonth": "202611"})
    assert response.json()["data"]["issues"][0]["level"] in ("INFO", "WARNING")


# ---------------------------------------------------------------------------
# 5. 周/日计划形状
# ---------------------------------------------------------------------------


def test_week_plan_items_use_week_shape(setup, monkeypatch):
    """周计划条目用 content 字段（不是 taskContent）。"""
    db, client = setup
    patch_agent(monkeypatch, ok_result(items=[{
        "taskName": "周任务", "taskContent": "周任务内容",
        "deliverable": "交付", "quality": "", "estimatedEndDate": "2026-11-30",
    }]))
    response = client.post("/v1/employee/ai/week-plans/generate",
                           json={"requestId": "r1", "weekStart": "2026-11-16"})
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["content"] == "周任务内容"
    assert item["plannedFinishDate"] == "2026-11-30"
    assert "monthPlanItemId" in item


def test_day_plan_content_is_text(setup, monkeypatch):
    """日计划是一段文本 —— 不是条目数组。"""
    db, client = setup
    patch_agent(monkeypatch, ok_result(items=[
        {"taskName": "任务A", "taskContent": "内容A"},
        {"taskName": "任务B", "taskContent": "内容B"},
    ]))
    response = client.post("/v1/employee/ai/day-plans/generate",
                           json={"requestId": "r1", "planDate": "2026-11-20"})
    data = response.json()["data"]
    assert isinstance(data["content"], str)
    assert "任务A" in data["content"]
    assert "任务B" in data["content"]
    assert data["relatedWeekPlanItemIds"] == []


# ---------------------------------------------------------------------------
# 6. 示例提取
# ---------------------------------------------------------------------------


def test_extract_example_reads_text_file(setup):
    """上传的文本文件要被真正读出来。"""
    db, client = setup
    response = client.post(
        "/v1/employee/ai/plan-examples/extract",
        files={"file": ("plan.txt", "完成支付链路优化\n交付上线报告".encode(), "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["fileName"] == "plan.txt"
    assert "支付链路优化" in data["content"]
    assert data["truncated"] is False


def test_extract_example_handles_gbk(setup):
    """GBK 编码的中文文件不能解成乱码。"""
    db, client = setup
    response = client.post(
        "/v1/employee/ai/plan-examples/extract",
        files={"file": ("plan.txt", "完成支付链路优化".encode("gbk"), "text/plain")},
    )
    assert response.status_code == 200
    assert "支付链路优化" in response.json()["data"]["content"]


def test_extract_example_truncates_oversized(setup):
    """超大文件要被截断并标记 —— 不能把服务拖垮。"""
    db, client = setup
    response = client.post(
        "/v1/employee/ai/plan-examples/extract",
        files={"file": ("big.txt", ("x" * (6 * 1024 * 1024)).encode(), "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["truncated"] is True
    assert data["content"] == ""


# ---------------------------------------------------------------------------
# 7. 工具令牌边界
# ---------------------------------------------------------------------------


def test_allowed_tools_filtered_by_config(monkeypatch):
    """配置里没开的工具不能出现在白名单里。"""
    monkeypatch.setattr(settings, "plan_ai_allowed_tools",
                        "get_my_plan_history,get_metric_values")
    assert plan_ai_service.allowed_tools() == ["get_my_plan_history"]


def test_department_context_can_be_disabled(setup, monkeypatch):
    """部门参考可以整体关掉 —— 关掉后 scope 只给 self。"""
    monkeypatch.setattr(settings, "plan_ai_department_context", False)
    db, client = setup
    employee = db.get(Employee, "ME")
    assert plan_ai_service._scope_for(employee) == "self"


def test_scope_is_department_when_enabled(setup):
    """默认有部门 + 开关打开 → scope=department。"""
    db, client = setup
    employee = db.get(Employee, "ME")
    assert plan_ai_service._scope_for(employee) == "department"
