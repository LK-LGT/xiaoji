"""评分构成明细工具（``get_scoring_breakdown``）测试。

⚠️ **这个工具解决什么问题**
--------------------------
``get_metric_values`` 只给「实测值 / 目标值 / 达成率」，
AI 拿到这些**解释不了「分数是怎么算出来的」** ——
它看不到权重、等级分、贡献分。

本工具补上这一段，并且额外给出**最有价值**的部分：
**未参与算分的指标及原因**（缺数据 / 缺目标值 / rubric 未配置）。

⚠️ **锁定三条契约**
------------------
1. **不泄露全局评分标准** —— ``level_scores`` 是跨任务的默认配置，
   给了等于泄露别的任务的评分标准。
2. **不给「最终成绩」** —— 给的是**规则重算分**，
   与人工确认后的最终成绩不是一回事。
3. **不落库** —— 只读工具，绝不能写 Score / 改 record 状态。
"""

from __future__ import annotations

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
    AssessmentRecord,
    AssessmentTask,
    Base,
    ConfigSnapshot,
    Department,
    Employee,
    MetricValue,
    Score,
)
from backend.app.routes.agent_tools import router
from backend.app.services import agent_tool_service, review_service
from backend.app.services.agent_tool_token import issue_agent_tool_token


@pytest.fixture
def setup(monkeypatch):
    """两个指标：GMV（参与算分）+ NEW（缺数据 → 不参与）。"""
    monkeypatch.setattr(settings, "agent_tool_secret", "scoring-breakdown-test-secret")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Department(id="D", name="部门"))
        db.flush()
        db.add(Employee(id="E", name="员工", department_id="D", employment_status="active", account_status="enabled"))
        db.add(AssessmentTask(id="T", name="任务", period="202611"))
        db.flush()
        snapshot = ConfigSnapshot(
            id="S", task_id="T",
            metrics_json=[
                {"code": "GMV", "name": "销售额"},
                {"code": "NEW", "name": "新客数"},
            ],
            weights_json=[
                {"metric_code": "GMV", "weight": 60},
                {"metric_code": "NEW", "weight": 40},
            ],
            rubric_json=[
                {"metric_code": "GMV", "levels": [{"level": "A", "min_ratio": 1, "max_ratio": None}]},
                {"metric_code": "NEW", "levels": [{"level": "A", "min_ratio": 1, "max_ratio": None}]},
            ],
        )
        db.add(snapshot)
        db.flush()
        db.get(AssessmentTask, "T").snapshot_id = "S"
        db.add(AssessmentRecord(id="R", task_id="T", employee_id="E"))
        # ⚠️ 只有 GMV 有数据 —— NEW 缺数据 → **不参与算分**，
        #    GMV 的权重被重新归一为 100%。
        db.add(MetricValue(employee_id="E", metric_code="GMV", period="202611", value=120, target=100, source="manual"))
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


def headers(scope="self", allow=None, task_id="T"):
    token = issue_agent_tool_token(
        employee_id="E", task_id=task_id,
        allow=allow or ["get_scoring_breakdown"], scope=scope,
    )
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 核心：算式与贡献分
# ===========================================================================


def test_breakdown_matches_scoring_engine_exactly(setup):
    """⚠️ 工具返回的每一项必须与算分引擎**逐字段一致**。

    这是最重要的一条：如果工具自己算一遍，就可能出现
    「AI 说的贡献分」与「系统实际算的贡献分」不一致 ——
    那就变成了 AI 在编数据。
    """
    db, client = setup
    expected = review_service.compute_score(db, record_id="R")

    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["available"] is True
    assert data["total_score"] == expected.total
    assert len(data["items"]) == len(expected.per_metric)

    item = data["items"][0]
    ref = expected.per_metric[0]
    assert item["metric_code"] == ref["metric_code"]
    assert item["raw_weight"] == ref["raw_weight"]
    assert item["normalized_weight"] == ref["normalized_weight"]
    assert item["level"] == ref["level"]
    assert item["level_score"] == ref["level_score"]
    assert item["contribution"] == ref["contribution"]
    assert item["ratio"] == ref["ratio"]


def test_breakdown_exposes_formula_for_each_item(setup):
    """⚠️ 必须给现成的算式 —— 让 AI **直接引用**，不要自己重算。"""
    _, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    item = response.json()["data"]["items"][0]
    assert "formula" in item and item["formula"]
    # 算式里的数字必须与字段对得上
    assert str(item["normalized_weight"]) in item["formula"]
    assert str(item["level_score"]) in item["formula"]
    assert str(item["contribution"]) in item["formula"]


def test_breakdown_lists_excluded_metrics_with_reason(setup):
    """⚠️ **最有价值**的部分：未参与算分的指标及原因。

    用户问「我为什么少了分」时，答案往往在这里：
    某指标缺数据 → **不扣分** → 其余指标权重重新归一。
    不说清楚，用户会误以为「缺数据 = 扣分」。
    """
    _, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    data = response.json()["data"]

    excluded = data["excluded_items"]
    assert len(excluded) == 1
    assert excluded[0]["metric_code"] == "NEW"
    assert excluded[0]["metric_name"] == "新客数"
    assert excluded[0]["raw_weight"] == 40
    # 原因要说清是「缺数据」
    assert "缺少数据" in excluded[0]["reason"] or "缺少可用数据" in excluded[0]["reason"]
    assert excluded[0]["has_value"] is False


def test_weight_is_renormalized_when_metric_excluded(setup):
    """⚠️ 缺数据的指标**不扣分** —— 其余指标权重**重新归一**。

    NEW 权重 40 但无数据 → GMV 归一化权重应为 100%（而不是 60%）。
    """
    _, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    data = response.json()["data"]
    item = data["items"][0]
    assert item["raw_weight"] == 60
    assert item["normalized_weight"] == 1.0
    assert data["participating_weight"] == 60
    assert data["total_weight"] == 100
    assert data["warnings"]


# ===========================================================================
# 敏感度边界
# ===========================================================================


def test_breakdown_does_not_leak_global_level_scores(setup):
    """⚠️ **不能**泄露全局默认等级分映射表。

    ``level_scores``（A=100/B=80/C=60/D=40）是**跨任务**的默认配置，
    给了等于泄露别的任务的评分标准。
    """
    _, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    data = response.json()["data"]
    assert "level_scores" not in data
    # 整份响应里都不能出现这张表
    assert "level_scores" not in response.text


def test_breakdown_is_not_confirmed_final_score(setup):
    """⚠️ 给的是**规则重算分**，不是人工确认后的最终成绩。

    必须有 ``note`` 说清这一点 —— 否则 AI 会说成
    「你的最终成绩是 X 分」，而那可能已经被人工调整过。
    """
    db, client = setup
    # 人工确认了一个与规则分不同的等级
    db.get(AssessmentRecord, "R").confirmed_rating = "C"
    db.commit()

    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    data = response.json()["data"]
    assert "note" in data
    assert "最终成绩" in data["note"]
    # ⚠️ **不能**把人工确认的等级当成规则分返回
    assert data["items"][0]["level"] == "A"
    assert "confirmed_rating" not in response.text
    # ⚠️ 等级表里也不能混进人工确认的等级
    scale = data["items"][0].get("level_scale") or []
    assert all(entry.get("level") != "C" for entry in scale)


def test_breakdown_never_persists_score(setup):
    """⚠️ 只读工具 —— 绝不能写 Score、不能改 record 状态。"""
    db, client = setup
    client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    assert db.scalars(select(Score)).all() == []
    assert db.get(AssessmentRecord, "R").status == "not_started"
    assert db.scalars(select(AiToolCall)).one().ok


# ===========================================================================
# 权限与边界
# ===========================================================================


def test_breakdown_requires_token_allowance(setup):
    """⚠️ 令牌里没有这个工具 → 403，且**记录失败审计**。"""
    db, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"},
        headers=headers(allow=["get_metric_values"]),
    )
    assert response.status_code == 403
    audit = db.scalars(select(AiToolCall)).one()
    assert not audit.ok and audit.error == "TOOL_FORBIDDEN"


def test_breakdown_rejects_period_mismatch(setup):
    """⚠️ 周期必须与任务一致 —— 不能跨周期查。"""
    db, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202610"}, headers=headers(),
    )
    assert response.status_code == 400
    audit = db.scalars(select(AiToolCall)).one()
    assert not audit.ok and audit.error == "PERIOD_MISMATCH"


def test_breakdown_missing_snapshot_reports_unavailable(setup):
    """⚠️ 没有冻结配置 → 明确说不可用，**不编数据**。"""
    db, client = setup
    db.get(AssessmentTask, "T").snapshot_id = None
    db.commit()
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    data = response.json()["data"]
    assert data["available"] is False
    assert data["items"] == []
    assert "note" in data


def test_breakdown_missing_task_is_404(setup):
    _, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"},
        headers=headers(task_id="missing"),
    )
    assert response.status_code == 404


def test_breakdown_other_employee_record_is_404(setup):
    """⚠️ 没有自己的考核记录 → 404（查不到，不是空数据）。"""
    db, client = setup
    db.delete(db.get(AssessmentRecord, "R"))
    db.commit()
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown", json={"period": "202611"}, headers=headers(),
    )
    assert response.status_code == 404


def test_breakdown_rejects_extra_fields(setup):
    """⚠️ 不能借这个工具指定别的员工。"""
    _, client = setup
    response = client.post(
        "/v1/agent-tools/get_scoring_breakdown",
        json={"period": "202611", "employee_id": "OTHER"},
        headers=headers(),
    )
    assert response.status_code == 422


# ===========================================================================
# 服务层直调
# ===========================================================================


def test_load_breakdown_service_level(setup):
    """服务层直调也要一致（网页端预取走的是这条路径）。"""
    db, _ = setup
    task = db.get(AssessmentTask, "T")
    result = agent_tool_service.load_scoring_breakdown(db, employee_id="E", task=task)
    assert result["available"] is True
    assert result["total_score"] == 100.0
    assert len(result["items"]) == 1
    assert len(result["excluded_items"]) == 1
