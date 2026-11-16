"""R6 完整业务链路的**回归测试**（计划 → 审批 → 成果 → 算分）。

为什么要有这个测试
------------------
盘点项 R6：功能代码都在，但真实闭环**从没跑通过一次**。
本文件把「计划→审批→成果→算分」压进一条**可重复执行**的测试，
让 CI 每次都守着这条链路 —— 以后谁改坏了审批状态机或算分口径，
这里立刻红。

与 ``backend/scripts/verify_r6_full_chain_e2e.py`` 的关系
------------------------------------------------------
- 脚本：跑**真实库**，人工/发布前验证用；
- 本文件：跑**内存库**，CI 每次都跑。

两者验证同一套断言口径，避免「脚本过了但 CI 没守」的割裂。

⚠️ SQLite 同线程限制
--------------------
``TestClient`` 在**另一个线程**里跑端点，SQLite 默认不允许跨线程使用
连接 → 必须开 ``check_same_thread=False`` 并用 ``StaticPool``
（所有连接共享同一个内存库），否则报
「SQLite objects created in a thread can only be used in that same thread」。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import PlanStatus
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
    Department,
    Employee,
    MetricValue,
    MonthPlan,
    MonthPlanItem,
    Score,
)
from backend.app.domain.result_models import Result
from backend.app.main import app
from backend.app.services import review_service
from backend.app.services.access_control import build_access_context

PERIOD = "209906"
PLAN_MONTH = "2099-06"

METRICS_JSON = [
    {"code": "gmv", "name": "销售额", "unit": "元"},
    {"code": "conv", "name": "转化率", "unit": "%"},
]
WEIGHTS_JSON = [
    {"metric_code": "gmv", "weight": 70},
    {"metric_code": "conv", "weight": 30},
]
RUBRIC_JSON = [
    {
        "metric_code": code,
        "levels_json": [
            {"level": "A", "min_ratio": 1.0, "max_ratio": None, "score": 100},
            {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "score": 80},
            {"level": "C", "min_ratio": 0.0, "max_ratio": 0.8, "score": 60},
        ],
    }
    for code in ("gmv", "conv")
]


@pytest.fixture
def chain_env():
    """一套完整的 R6 环境：部门 → 经理 → 员工 + 冻结快照 + 考核任务 + 指标值。

    Yields:
        ``(client, db, ctx)`` —— ctx 里是各角色的 employee id。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from backend.app.domain.models import Base

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    db.add(Department(id="D_R6", name="R6 测试部", parent_id=None, active=True))
    db.add(
        Employee(
            id="R6_MANAGER",
            name="R6 经理",
            login_account="r6_manager",
            department_id="D_R6",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.add(
        Employee(
            id="R6_EMP",
            name="R6 员工",
            login_account="r6_emp",
            department_id="D_R6",
            manager_id="R6_MANAGER",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.flush()

    snap = ConfigSnapshot(
        task_id="R6_TASK",
        metrics_json=METRICS_JSON,
        weights_json=WEIGHTS_JSON,
        rubric_json=RUBRIC_JSON,
        config_version="r6-v1",
    )
    db.add(snap)
    db.flush()

    task = AssessmentTask(
        id="R6_TASK",
        name="R6 考核任务",
        period=PERIOD,
        status="in_progress",
        snapshot_id=snap.id,
        owner_id="R6_MANAGER",
    )
    db.add(task)
    db.flush()

    db.add(
        AssessmentRecord(
            id="R6_REC",
            task_id="R6_TASK",
            employee_id="R6_EMP",
            status="in_progress",
        )
    )
    for code, value, target in (("gmv", 95.0, 100.0), ("conv", 85.0, 100.0)):
        db.add(
            MetricValue(
                employee_id="R6_EMP",
                metric_code=code,
                period=PERIOD,
                value=value,
                target=target,
                source="r6_test",
            )
        )

    plan = MonthPlan(
        id="R6_PLAN",
        owner_id="R6_EMP",
        plan_month=PLAN_MONTH,
        title="R6 月计划",
        status=PlanStatus.DRAFT.value,
    )
    db.add(plan)
    db.flush()
    db.add(
        MonthPlanItem(
            id="R6_ITEM",
            plan_id=plan.id,
            task_type="REGULAR",
            task_name="R6 任务",
            task_content="跑通完整链路",
            deliverable="可交付",
            quality="高",
            performance_weight=100.0,
            estimated_value=100.0,
            status="draft",
            sort_no=0,
        )
    )
    db.commit()

    current = {"id": "R6_EMP"}

    def db_override():
        s = get_sessionmaker_local()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def user_override():
        s = get_sessionmaker_local()
        try:
            yield s.get(Employee, current["id"])
        finally:
            s.close()

    def get_sessionmaker_local():
        return sessionmaker(bind=engine)()

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    try:
        yield TestClient(app), db, current
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()
        engine.dispose()


def _manager_can_approve(db) -> bool:
    """确认经理对员工有审批权（避免测试因数据范围问题假失败）。"""
    manager = db.get(Employee, "R6_MANAGER")
    employee = db.get(Employee, "R6_EMP")
    ctx = build_access_context(db, manager)
    from backend.app.services.access_control import can_access_employee

    return can_access_employee(ctx, employee, db, require_active_target=True)


def test_r6_chain_plan_to_score(chain_env):
    """R6 主用例：计划 → 审批 → 成果 → 算分，一条龙走通。

    每一步都做**库内落库断言** —— 只看接口 200 是不够的
    （历史上就踩过「接口说成功、库里没变化」的坑）。
    """
    client, db, current = chain_env

    assert _manager_can_approve(db), "测试数据本身不满足审批权前提"

    # ── ① 计划：把计划推进到「已提交」态 ─────────────────────────────────
    #    计划本体已在 fixture 里建好（含条目），
    #    这里只推进状态 —— 计划创建端点的契约由
    #    ``test_plan_approval_api.py`` 等既有测试负责，
    #    本文件聚焦「审批 → 成果 → 算分」的闭环。
    current["id"] = "R6_EMP"
    plan = db.get(MonthPlan, "R6_PLAN")
    plan.status = PlanStatus.SUBMITTED.value
    db.commit()

    # ── ② 审批：主管能在待批列表里看到并通过 ────────────────────────────
    current["id"] = "R6_MANAGER"

    r = client.get("/v1/leader/month-plan-approvals")
    assert r.status_code == 200, r.text
    rows = r.json().get("data", [])
    hit = [x for x in rows if x.get("id") == "R6_PLAN"]
    assert hit, f"待批列表里找不到本次计划（可见范围问题）：{rows}"
    assert hit[0]["ownerId"] == "R6_EMP"
    assert hit[0]["status"] == "PENDING_APPROVAL"

    r = client.post(
        "/v1/leader/month-plan-approvals/R6_PLAN/approve",
        json={"comment": "R6 验证通过", "versionNo": 1},
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    plan = db.get(MonthPlan, "R6_PLAN")
    assert plan.status == PlanStatus.APPROVED.value
    assert plan.approved_at is not None

    # 重复审批 → 409（状态机幂等）
    r = client.post(
        "/v1/leader/month-plan-approvals/R6_PLAN/approve", json={"comment": "再来"}
    )
    assert r.status_code == 409

    # ── ③ 成果：员工提交月成果 ──────────────────────────────────────────
    current["id"] = "R6_EMP"
    r = client.post(
        "/v1/employee/results/submit",
        data={
            "monthPlanId": "R6_PLAN",
            "completionRate": "95",
            "description": "完成 R6 验证",
            "completionStatus": "COMPLETED",
            "remark": "R6",
            "personalPerformance": "95",
        },
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    results = db.execute(select(Result)).scalars().all()
    assert len(results) == 1
    assert results[0].owner_id == "R6_EMP"
    assert results[0].status == "submitted"

    # ── ④ 算分：规则引擎算分并落 Score ──────────────────────────────────
    current["id"] = "R6_MANAGER"
    r = client.post(
        "/v1/assessment/records/R6_REC/execute-rules-engine",
        json={"force_execute": True},
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    scores = db.execute(select(Score).where(Score.record_id == "R6_REC")).scalars().all()
    assert len(scores) == 1, "Score 表没有落库"

    # 期望值**按 rubric 独立推导**：
    #   gmv : 95/100 = 0.95 → [0.8, 1.0) → B(80)，权重 70
    #   conv: 85/100 = 0.85 → [0.8, 1.0) → B(80)，权重 30
    #   总分 = (70×80 + 30×80) / 100 = 80.0
    expected = (70 * 80 + 30 * 80) / 100
    assert abs(float(scores[0].total) - expected) < 0.01

    # 两个指标都必须参与 —— 少一个说明「缺数据静默不扣分」的行为出现了。
    assert len(scores[0].per_metric_json) == 2

    # 算分可复算：跑两次结果必须完全一致（模型不参与算分）。
    r1 = review_service.compute_score(db, record_id="R6_REC")
    r2 = review_service.compute_score(db, record_id="R6_REC")
    assert abs(r1.total - r2.total) < 1e-9


def test_r6_score_requires_snapshot(chain_env):
    """没有冻结快照 → 算分必须**报错**，不能出假分。

    这是决策③的底线：配置错误必须阻断，不能降级。
    """
    client, db, current = chain_env

    task = db.get(AssessmentTask, "R6_TASK")
    task.snapshot_id = None
    db.commit()

    current["id"] = "R6_MANAGER"
    r = client.post(
        "/v1/assessment/records/R6_REC/execute-rules-engine",
        json={"force_execute": True},
    )
    assert r.status_code != 200, "缺配置时竟然算出了分数"


def test_r6_missing_metric_is_excluded_not_penalized(chain_env):
    """缺数据/缺目标的指标**不参与算分**，权重按参与项重新归一。

    ⚠️ 不是「记 0 分」—— 配置瑕疵不该伪装成业务低绩效。
    """
    client, db, current = chain_env

    # 删掉 conv 的指标值 → 它不参与，gmv 独占 100% 权重
    conv = db.execute(
        select(MetricValue).where(
            MetricValue.employee_id == "R6_EMP", MetricValue.metric_code == "conv"
        )
    ).scalar_one_or_none()
    db.delete(conv)
    db.commit()

    result = review_service.compute_score(db, record_id="R6_REC")
    # gmv 达成率 0.95 → B(80)，独占全部权重 → 总分 80
    assert abs(result.total - 80.0) < 0.01
    assert len(result.per_metric) == 1
    assert any("conv" in w for w in result.warnings), f"缺数据必须留告警：{result.warnings}"


def test_r6_weight_must_sum_to_100(chain_env):
    """权重合计 ≠ 100 → 算分必须阻断。"""
    client, db, current = chain_env

    snap = db.get(ConfigSnapshot, db.get(AssessmentTask, "R6_TASK").snapshot_id)
    snap.weights_json = [{"metric_code": "gmv", "weight": 70}]
    db.commit()

    from backend.app.services.scoring import ScoringError

    with pytest.raises(ScoringError):
        review_service.compute_score(db, record_id="R6_REC")
