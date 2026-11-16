"""规则引擎端点 —— 集成测试（A8：修存量缺陷 + 切到权威口径）。

背景：
    `backend/app/routes/v1/rules_engine.py` 原本有 7 处缺陷，且**从未注册进
    `main.py`**（端点实际返回 404，不是 500）。本次修复：

    1. 算分改走 `services/scoring.py` + `services/review_service.py`
       （唯一真值：A/B/C/D 等级 + 冻结快照 + MetricValue 目标值）。
    2. `MetricValue` 按 `(employee_id, period)` 查询 —— 不再查不存在的
       `record_id` 列，也不再用 `source="manager"` 过滤掉全部数据。
    3. ACL 走 `access_control.build_access_context` + `can_access_employee`
       —— 不再读 `Employee.role`（该列不存在）。
    4. 结果落库到 `Score` 表 —— 不再往 `AssessmentRecord` 写不存在的
       `final_score` / `rating` / `calculation_detail` 列。
    5. 快照三段配置是 `list[dict]`，不再 `json.loads`、不再 `.items()`。
    6. 考勤/奖惩**不参与算分**（决策②），只回显不入算。
    7. 端点注册进 `main.py`。

覆盖：
    A. 算分正确性：达成率 → 等级 → 加权总分。
    B. 落库：写 `Score` 表，不写 `AssessmentRecord` 的不存在列。
    C. 数据范围：HR 全公司、部门负责人管理范围、主管直属下属、本人、越权拒绝。
    D. 考勤不进算分：`absent_days` 传多少都不改变分数。
    E. 幂等：重复执行不产生第二行，数值一致。
    F. 错误路径：记录不存在、无数据、配置非法、状态强制。
    G. 鉴权与注册。
"""

from __future__ import annotations

import contextvars
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import RecordStatus
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    ConfigSnapshot,
    Department,
    Employee,
    HrDepartmentConfig,
    MetricValue,
    Score,
    SystemAdmin,
)
from backend.app.main import app

ENDPOINT = "/v1/assessment/records/{record_id}/execute-rules-engine"

PERIOD = "2026-11"

#: A/B/C/D 等级区间（与 `db/seed.py` 的 GMV_LEVELS 同构）
GMV_LEVELS = [
    {"level": "A", "min_ratio": 1.05, "max_ratio": None,
     "desc": "超额完成目标 5% 以上", "score": 100},
    {"level": "B", "min_ratio": 0.95, "max_ratio": 1.05,
     "desc": "达成目标", "score": 80},
    {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95,
     "desc": "未达成目标", "score": 60},
]

CONV_LEVELS = [
    {"level": "A", "min_ratio": 1.10, "max_ratio": None,
     "desc": "转化率超基准 10% 以上", "score": 100},
    {"level": "B", "min_ratio": 0.95, "max_ratio": 1.10,
     "desc": "转化率接近基准", "score": 80},
    {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95,
     "desc": "转化率明显低于基准", "score": 60},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    # ⚠️ 必须用 StaticPool —— TestClient 在工作线程发请求，
    #    SQLite `:memory:` 默认每连接一个独立库（commit 后 refresh 会拿到空库）。
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def org(db):
    """组织结构。

    - `D_OPS`：运营部，`E_LEAD` 是部门负责人（leader_id）
    - `D_LEAD` 直管 `E_A` / `E_B`
    - `D_HR`：HR 部门（登记在 `HrDepartmentConfig` 里）—— 成员自动获得 HR 角色
    - `E_ADMIN` 是系统管理员（`SystemAdmin`）—— **只管配置，无业务数据范围**
    - `E_OUT` 是不相干的第三方（用于越权测试）

    ⚠️ 注意：`D_OPS` **不能**是 HR 部门，否则该部门所有成员都会拿到 `hr` 角色
    （`build_access_context` 的既定语义：所属部门登记为 HR 部门 → 是 HR）。
    """
    dept = Department(id="D_OPS", name="运营部", active=True)
    hr_dept = Department(id="D_HR", name="人力资源部", active=True)
    other = Department(id="D_OTHER", name="其他部", active=True)
    db.add_all([dept, hr_dept, other])
    db.flush()

    lead = Employee(id="E_LEAD", name="主管", department_id="D_OPS",
                    employment_status="active", account_status="enabled")
    emp_a = Employee(id="E_A", name="员工甲", department_id="D_OPS",
                     employment_status="active", account_status="enabled")
    emp_b = Employee(id="E_B", name="员工乙", department_id="D_OPS",
                     employment_status="active", account_status="enabled")
    hr = Employee(id="E_HR", name="HR", department_id="D_HR",
                  employment_status="active", account_status="enabled")
    admin = Employee(id="E_ADMIN", name="管理员", department_id="D_OPS",
                     employment_status="active", account_status="enabled")
    outsider = Employee(id="E_OUT", name="外部员工", department_id="D_OTHER",
                        employment_status="active", account_status="enabled")
    db.add_all([lead, emp_a, emp_b, hr, admin, outsider])
    db.flush()

    emp_a.manager_id = "E_LEAD"
    emp_b.manager_id = "E_LEAD"
    dept.leader_id = "E_LEAD"
    db.flush()

    # HR 身份 = 所属部门在 HrDepartmentConfig 里
    db.add(HrDepartmentConfig(id="HDC_HR", department_id="D_HR", active=True))
    # 系统管理员
    db.add(SystemAdmin(id="SA_1", employee_id="E_ADMIN", active=True))
    db.flush()

    return {
        "lead": lead, "a": emp_a, "b": emp_b, "hr": hr,
        "admin": admin, "out": outsider, "dept": dept,
    }


_CURRENT_USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "test_current_user_id", default=""
)
_CURRENT_USER: contextvars.ContextVar[Employee] = contextvars.ContextVar("test_current_user")


def _current_user() -> Employee:
    if not _CURRENT_USER_ID.get():
        raise AssertionError("测试请求未指定用户身份")
    return _CURRENT_USER.get()


@pytest.fixture()
def login(db):
    """切换身份的工厂：``login(org["lead"])`` 返回一个客户端。"""

    def _make(user: Employee) -> TestClient:
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = _current_user

        class _Client(TestClient):
            def request(self, method, url, **kwargs):  # type: ignore[override]
                headers = dict(kwargs.pop("headers", None) or {})
                headers["X-Test-User"] = user.id
                kwargs["headers"] = headers
                token = _CURRENT_USER_ID.set(user.id)
                obj = _CURRENT_USER.set(user)
                try:
                    return super().request(method, url, **kwargs)
                finally:
                    _CURRENT_USER_ID.reset(token)
                    _CURRENT_USER.reset(obj)

        return _Client(app)

    yield _make
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_task(db, employee_id: str, *, period: str = PERIOD) -> tuple[AssessmentTask, AssessmentRecord]:
    """建考核任务 + 冻结快照 + 考核记录。

    快照口径（与 `db/seed.py` 同构）：
      - gmv 权重 70，目标 1_200_000
      - conversion_rate 权重 30，目标 100

    ⚠️ 权重合计必须 = 100（决策③第一道保险：`config_service.validate_config`），
    否则算分被阻断（CONFIG_ERROR），不会产出假分。
    """
    task = AssessmentTask(
        id="T_1",
        name="11月考核",
        period=period,
        department_id="D_OPS",
        position=None,
        status="self_review_open",
        rating_policy="reference_only",
    )
    db.add(task)
    db.flush()

    snapshot = ConfigSnapshot(
        id="CS_1",
        task_id="T_1",
        metrics_json=[
            {"code": "gmv", "name": "GMV", "unit": "元",
             "calculation": "订单金额求和", "data_source": "api:ecommerce"},
            {"code": "conversion_rate", "name": "转化率", "unit": "%",
             "calculation": "支付人数/访客数", "data_source": "api:ecommerce"},
        ],
        weights_json=[
            {"metric_code": "gmv", "weight": 70},
            {"metric_code": "conversion_rate", "weight": 30},
        ],
        rubric_json=[
            {"metric_code": "gmv", "levels_json": GMV_LEVELS},
            {"metric_code": "conversion_rate", "levels_json": CONV_LEVELS},
        ],
    )
    db.add(snapshot)
    db.flush()
    task.snapshot_id = snapshot.id

    record = AssessmentRecord(
        id="R_1",
        task_id="T_1",
        employee_id=employee_id,
        status=RecordStatus.SELF_REVIEW_SUBMITTED.value,
    )
    db.add(record)
    db.flush()
    return task, record


def _add_metric_values(db, employee_id: str, *, gmv: float, conversion: float,
                       period: str = PERIOD) -> None:
    """写入算分集（`MetricValue`）。

    注意：**不带 `record_id`**，也不带 `source="manager"` ——
    这正是原实现查不到数据的原因。
    """
    db.add_all([
        MetricValue(
            id=f"MV_GMV_{employee_id}",
            employee_id=employee_id,
            metric_code="gmv",
            period=period,
            value=gmv,
            target=1_200_000.0,
            unit="元",
            source="excel_import",
            source_ref=f"{employee_id}|{period}|gmv",
            as_of=datetime(2026, 11, 30),
        ),
        MetricValue(
            id=f"MV_CONV_{employee_id}",
            employee_id=employee_id,
            metric_code="conversion_rate",
            period=period,
            value=conversion,
            target=100.0,
            unit="%",
            source="excel_import",
            source_ref=f"{employee_id}|{period}|conversion_rate",
            as_of=datetime(2026, 11, 30),
        ),
    ])
    db.flush()


def _scores(db) -> list[Score]:
    return list(db.execute(select(Score)).scalars().all())


# ---------------------------------------------------------------------------
# A. 算分正确性
# ---------------------------------------------------------------------------


def test_score_uses_canonical_achievement_ratio_and_rubric(db, org, login):
    """达成率 → Rubric 查表定级 → 权重乘等级分。

    gmv  = 1_200_000 / 1_200_000 → ratio 1.0 → 落在 [0.95, 1.05) → B(80)
    conv = 96 / 100              → ratio 0.96 → 落在 [0.95, 1.10) → B(80)

    权重合计 = 100（决策③）→ 归一后 gmv 70%、conv 30%
    总分 = 70% * 80 + 30% * 80 = 80.0
    """
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["final_score"] == pytest.approx(80.0)
    assert body["status"] == "success"

    detail = body["calculation_detail"]
    # 权重按参与项归一（缺数据不连带扣其他指标的权）
    assert detail["metric_weights"]["gmv"] == pytest.approx(0.7, abs=1e-4)
    assert detail["metric_weights"]["conversion_rate"] == pytest.approx(0.3, abs=1e-4)
    assert len(detail["normalized_metrics"]) == 2


def test_low_performance_maps_to_lower_level(db, org, login):
    """未达标 → C 档（60 分），等级由 Rubric 区间决定，不靠硬编码 1~5。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=600_000.0, conversion=50.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["final_score"] == pytest.approx(60.0)
    # C → 3 档
    assert body["rating_level"] == 3
    assert body["rating_label"] == "C"


def test_missing_metric_does_not_penalize_others(db, org, login):
    """缺数据 ≠ 扣分：另一个指标的权重重新归一。"""
    _make_task(db, "E_A")
    # 只写 gmv，conv 不写
    db.add(MetricValue(
        id="MV_GMV_E_A", employee_id="E_A", metric_code="gmv", period=PERIOD,
        value=1_200_000.0, target=1_200_000.0, unit="元", source="excel_import",
        source_ref="E_A|2026-11|gmv", as_of=datetime(2026, 11, 30),
    ))
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # gmv 参与，归一权重 100%，ratio 1.0 → B(80)
    assert body["final_score"] == pytest.approx(80.0)
    detail = body["calculation_detail"]
    assert detail["metric_weights"]["gmv"] == pytest.approx(1.0, abs=1e-4)
    assert "conversion_rate" not in detail["metric_weights"]


# ---------------------------------------------------------------------------
# B. 落库位置
# ---------------------------------------------------------------------------


def test_result_persisted_to_score_table(db, org, login):
    """分数落在 `Score` 表（唯一真值表）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    db.expire_all()
    rows = _scores(db)
    assert len(rows) == 1
    assert rows[0].record_id == "R_1"
    assert rows[0].total == pytest.approx(80.0)
    # 明细可追溯
    assert len(rows[0].per_metric_json) == 2
    # trace 记录算分口径（规则版本、参与指标数、权重合计）
    assert rows[0].trace_json["rule"] == "level_match_v1"
    assert rows[0].trace_json["participating_count"] == 2
    assert rows[0].trace_json["total_weight"] == pytest.approx(100.0)


def test_record_has_no_nonexistent_score_columns(db, org, login):
    """`AssessmentRecord` 上没有 final_score/rating/calculation_detail 列 —— 不写。

    算分**不推进** `record.status` —— 状态机由业务流程推进，
    与权威流程 `review_service.confirm_draft` 的做法一致。
    """
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    cols = {c.name for c in AssessmentRecord.__table__.columns}
    assert "final_score" not in cols
    assert "calculation_detail" not in cols
    assert "rating" not in cols

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    db.expire_all()
    rec = db.get(AssessmentRecord, "R_1")
    assert rec is not None
    # 状态不被算分端点改动
    assert rec.status == RecordStatus.SELF_REVIEW_SUBMITTED.value


# ---------------------------------------------------------------------------
# C. 数据范围
# ---------------------------------------------------------------------------


def test_hr_can_execute_for_anyone(db, org, login):
    """HR 全公司可见 —— 可以给任意员工算分。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["hr"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text


def test_manager_can_execute_for_direct_report(db, org, login):
    """主管可以给直属下属算分。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text


def test_department_leader_can_execute_for_department_member(db, org, login):
    """部门负责人可以给本部门员工算分（`E_LEAD` 是 D_OPS 的 leader）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text


def test_unrelated_employee_is_denied(db, org, login):
    """无关员工被拒 —— 不再靠不存在的 `Employee.role` 字段判断。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["out"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error_code"] == "PERMISSION_DENIED"


def test_system_admin_has_no_business_data_scope(db, org, login):
    """系统管理员只管配置，**不获得业务数据范围**（`SystemAdmin` 的既定语义）。

    这与 `access_control.can_access_employee` 一致：
    只有本人 / HR / 直属下属 / 部门负责人管理范围可以算分。
    """
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["admin"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error_code"] == "PERMISSION_DENIED"


def test_employee_can_execute_for_self(db, org, login):
    """员工本人可以给自己算分（`can_access_employee` 含本人）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["a"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# D. 考勤不进算分（决策②）
# ---------------------------------------------------------------------------


def test_attendance_never_affects_score(db, org, login):
    """`absent_days` 传多少都不改变分数 —— 考勤是 reference_only。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        baseline = c.post(ENDPOINT.format(record_id="R_1"), json={})
        heavy = c.post(
            ENDPOINT.format(record_id="R_1"),
            json={"absent_days": 30, "praise_count": 10, "complaint_count": 5,
                  "violation_count": 3},
        )

    assert baseline.status_code == 200, baseline.text
    assert heavy.status_code == 200, heavy.text
    assert heavy.json()["final_score"] == pytest.approx(baseline.json()["final_score"])

    detail = heavy.json()["calculation_detail"]
    assert detail["attendance_adjustment"] == 0.0
    assert detail["reward_adjustment"] == 0.0
    assert detail["total_adjustment"] == 0.0


def test_attendance_evidence_not_in_metric_value(db, org, login):
    """`MetricValue` 里根本没有考勤数据 —— 物理上无法影响分数。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    codes = {mv.metric_code for mv in db.execute(select(MetricValue)).scalars().all()}
    assert "attendance" not in codes
    assert "absent_days" not in codes


# ---------------------------------------------------------------------------
# E. 幂等
# ---------------------------------------------------------------------------


def test_repeated_execution_is_idempotent(db, org, login):
    """重复执行不产生第二行 `Score`，数值一致。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        first = c.post(ENDPOINT.format(record_id="R_1"), json={})
        second = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["final_score"] == pytest.approx(second.json()["final_score"])

    db.expire_all()
    assert len(_scores(db)) == 1


# ---------------------------------------------------------------------------
# F. 错误路径
# ---------------------------------------------------------------------------


def test_record_not_found(db, org, login):
    """记录不存在 → 404。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_NOPE"), json={})

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error_code"] == "NOT_FOUND"


def test_no_metric_value_raises_configuration_error(db, org, login):
    """无算分数据 → 配置类错误（阻断，不降级出假分）。"""
    _make_task(db, "E_A")

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error_code"] == "CONFIG_ERROR"


def test_invalid_snapshot_blocks_scoring(db, org, login):
    """快照权重引用未定义指标 → 阻断算分，不写出假分。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    snapshot = db.get(ConfigSnapshot, "CS_1")
    snapshot.weights_json = [{"metric_code": "not_defined", "weight": 100}]
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error_code"] == "CONFIG_ERROR"
    db.expire_all()
    assert _scores(db) == []


def test_force_execute_allows_non_finalized_state(db, org, login):
    """非可算分状态 + force_execute → 放行。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    rec = db.get(AssessmentRecord, "R_1")
    rec.status = "appealing"
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(
            ENDPOINT.format(record_id="R_1"), json={"force_execute": True}
        )

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# G. 鉴权与注册
# ---------------------------------------------------------------------------


def test_endpoint_requires_auth(db, org):
    """未鉴权 → 401/403（不覆盖 get_current_user，走真实鉴权依赖）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            resp = c.post(ENDPOINT.format(record_id="R_1"), json={})
        assert resp.status_code in (401, 403), resp.text
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_endpoint_is_registered():
    """端点必须真的注册进 app —— 原实现从未注册，实际是 404。"""
    schema = app.openapi()
    assert ENDPOINT in schema["paths"], "规则引擎端点未注册进 main.py"
    assert "post" in schema["paths"][ENDPOINT]
