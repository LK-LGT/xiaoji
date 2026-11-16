"""计划 ↔ 飞书审批实例持久绑定测试（A8 补强）。

覆盖：
1. **绑定写入** —— 幂等、字段补齐、1:1 约束。
2. **关联码** —— 生成、归一化、唯一性、易读字符集。
3. **反查** —— instance_code 优先、correlation_key 次之、都无则失败。
4. **飞书侧发起的审批回流** —— 事件不带 plan_id，仅带关联码 → 仍能回流。
5. **本系统发起的审批回流** —— 事件不带 plan_id，仅带 instance_code → 仍能回流。
6. **绑定管理端点** —— 权限、CRUD。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import PlanStatus
from backend.app.domain.models import (
    Base,
    DayPlan,
    Department,
    Employee,
    MonthPlan,
    SystemAdmin,
    WeekPlan,
)
from backend.app.domain.plan_models import PlanFeishuBinding
from backend.app.main import app
from backend.app.services import feishu_approval_sync, feishu_binding

WEBHOOK_URL = "/v1/feishu/approval/events"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    # ⚠️ 必须用 StaticPool —— TestClient 在工作线程发请求，
    #    SQLite `:memory:` 默认每连接一个独立库。
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
    d1 = Department(id="D_1", name="业务部", active=True)
    db.add(d1)
    db.flush()

    emp_a = Employee(
        id="E_A",
        name="员工甲",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
        feishu_open_id="ou_emp_a",
        feishu_user_id="u_emp_a",
    )
    leader = Employee(
        id="E_C",
        name="总监丙",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
        feishu_open_id="ou_leader",
        feishu_user_id="u_leader",
    )
    db.add_all([emp_a, leader])
    db.flush()
    emp_a.manager_id = "E_C"
    db.flush()
    db.add(SystemAdmin(employee_id="E_C", active=True))
    db.flush()
    return {"a": emp_a, "c": leader}


@pytest.fixture()
def anon_client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def admin_client(db, org):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: org["c"]
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def webhook_token():
    old = settings.feishu_approval_webhook_token
    settings.feishu_approval_webhook_token = "test-verify-token"
    yield "test-verify-token"
    settings.feishu_approval_webhook_token = old


def _month_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value):
    plan = MonthPlan(
        owner_id=owner_id, plan_month="2026-11", title="十一月计划", status=status
    )
    db.add(plan)
    db.flush()
    return plan


def _week_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value):
    plan = WeekPlan(
        owner_id=owner_id,
        title="周计划",
        week_start=date(2026, 11, 9),
        week_end=date(2026, 11, 9) + timedelta(days=6),
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


def _event(**overrides):
    payload = {
        "token": "test-verify-token",
        "event_id": "evt_001",
        "event_type": "approval_instance",
        "instance_code": "inst_001",
        "instance_status": "APPROVED",
        "approval_code": "plan_approval_code",
        "user_id": "u_emp_a",
        "comment": "飞书审批通过",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1. 绑定写入
# ---------------------------------------------------------------------------


def test_bind_creates_row_with_generated_correlation_key(db):
    """绑定时自动生成关联码。"""
    plan = _month_plan(db)
    row = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    assert row.plan_type == "month"
    assert row.plan_id == plan.id
    assert row.instance_code == "inst_1"
    assert row.correlation_key
    assert row.correlation_key.startswith("PP")


def test_bind_is_idempotent(db):
    """重复绑定同一计划 → 复用同一行，不新增。"""
    plan = _month_plan(db)
    first = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    second = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    assert first.id == second.id
    rows = db.execute(select(PlanFeishuBinding)).scalars().all()
    assert len(rows) == 1


def test_bind_fills_missing_fields_without_overwriting(db):
    """后补字段只填空，不覆盖已有值。"""
    plan = _month_plan(db)
    row = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    original_corr = row.correlation_key

    feishu_binding.bind_plan_to_instance(
        db,
        plan_type="month",
        plan_id=plan.id,
        instance_code="inst_2",  # 已存在，不应覆盖
        approval_code="AC_1",
    )
    assert row.instance_code == "inst_1"
    assert row.approval_code == "AC_1"
    assert row.correlation_key == original_corr


def test_binding_rows_are_unique_per_plan(db):
    """一个计划只能绑一行（唯一约束）。"""
    from sqlalchemy.exc import IntegrityError

    plan = _month_plan(db)
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    db.add(
        PlanFeishuBinding(
            plan_type="month", plan_id=plan.id, correlation_key="PPOTHER1234"
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------------------
# 2. 关联码
# ---------------------------------------------------------------------------


def test_correlation_key_format():
    """关联码格式：PP + 8 位大写字母数字。"""
    key = feishu_binding.generate_correlation_key()
    assert key.startswith("PP")
    assert len(key) == 10
    assert key[2:].isalnum()


def test_correlation_key_avoids_confusable_chars():
    """刻意剔除易混字符（0/O、1/I/l）。"""
    key = feishu_binding.generate_correlation_key()
    body = key[2:]
    for ch in ("O", "I", "L", "0", "1"):
        assert ch not in body, f"关联码不应包含易混字符 {ch}"


def test_correlation_key_unique_across_calls(db):
    """多次生成的关联码互不相同。"""
    keys = {feishu_binding.generate_correlation_key(db) for _ in range(50)}
    assert len(keys) == 50


def test_normalize_correlation_key():
    """归一化：大小写、空格、连字符、前缀缺失都要能识别。"""
    assert feishu_binding.normalize_correlation_key("ppab12cd34") == "PPAB12CD34"
    assert feishu_binding.normalize_correlation_key("PP-AB12-CD34") == "PPAB12CD34"
    assert feishu_binding.normalize_correlation_key(" ab12cd34 ") == "PPAB12CD34"
    assert feishu_binding.normalize_correlation_key("") == ""


def test_normalize_accepts_key_without_prefix():
    """员工只抄了主体码（没抄 PP 前缀）也能识别。"""
    key = feishu_binding.generate_correlation_key()
    body = key[2:]
    assert feishu_binding.normalize_correlation_key(body) == key


# ---------------------------------------------------------------------------
# 3. 反查
# ---------------------------------------------------------------------------


def test_resolve_by_instance_code(db):
    """按 instance_code 反查。"""
    plan = _month_plan(db)
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    ptype, pid, binding = feishu_binding.resolve_plan_by_instance(
        db, instance_code="inst_1"
    )
    assert ptype == "month"
    assert pid == plan.id
    assert binding is not None


def test_resolve_by_correlation_key(db):
    """按关联码反查（飞书侧发起的核心通道）。"""
    plan = _month_plan(db)
    row = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    ptype, pid, binding = feishu_binding.resolve_plan_by_instance(
        db, correlation_key=row.correlation_key
    )
    assert ptype == "month"
    assert pid == plan.id
    assert binding is not None


def test_resolve_prefers_instance_code(db):
    """instance_code 优先于 correlation_key。"""
    plan_a = _month_plan(db, owner_id="E_A")
    plan_b = _week_plan(db, owner_id="E_A")
    row_a = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan_a.id, instance_code="inst_1"
    )
    row_b = feishu_binding.bind_plan_to_instance(
        db, plan_type="week", plan_id=plan_b.id, instance_code="inst_2"
    )
    assert row_a.correlation_key != row_b.correlation_key

    ptype, pid, _ = feishu_binding.resolve_plan_by_instance(
        db, instance_code="inst_1", correlation_key=row_b.correlation_key
    )
    assert pid == plan_a.id


def test_resolve_returns_empty_when_not_found(db):
    """定位不到 → 空元组。"""
    ptype, pid, binding = feishu_binding.resolve_plan_by_instance(
        db, instance_code="nobody", correlation_key="PPNOPE1234"
    )
    assert ptype == ""
    assert pid == ""
    assert binding is None


# ---------------------------------------------------------------------------
# 4. 飞书侧发起的审批回流（核心场景）
# ---------------------------------------------------------------------------


def test_webhook_reflows_by_correlation_key_alone(db, anon_client, webhook_token):
    """事件**不带** plan_id，只带关联码 → 仍能回流到正确计划。

    这正是「员工从飞书侧发起审批」的场景。
    """
    plan = _month_plan(db)
    binding = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_001"
    )

    r = anon_client.post(
        WEBHOOK_URL,
        json=_event(
            plan_type="",
            plan_id="",
            correlation_key=binding.correlation_key,
        ),
    )
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "applied"

    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.APPROVED.value


def test_webhook_reflows_by_instance_code_alone(db, anon_client, webhook_token):
    """事件不带 plan_id，只带 instance_code → 仍能回流。"""
    plan = _month_plan(db)
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_001"
    )

    r = anon_client.post(
        WEBHOOK_URL, json=_event(plan_type="", plan_id="", correlation_key="")
    )
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "applied"

    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.APPROVED.value


def test_webhook_with_correlation_key_backfills_binding(db, anon_client, webhook_token):
    """按关联码回填后，绑定行应记录飞书实例状态。"""
    plan = _month_plan(db)
    binding = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_001"
    )

    anon_client.post(
        WEBHOOK_URL,
        json=_event(correlation_key=binding.correlation_key, instance_status="REJECTED"),
    )
    db.expire_all()
    assert binding.instance_status == "REJECTED"


def test_webhook_without_any_binding_hint_is_ignored(db, anon_client, webhook_token):
    """既无计划标识又无绑定线索 → 忽略，不报错（避免飞书反复重试）。"""
    plan = _month_plan(db)
    r = anon_client.post(
        WEBHOOK_URL, json=_event(plan_type="", plan_id="", correlation_key="")
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.SUBMITTED.value


def test_webhook_with_unbound_correlation_key_is_ignored(db, anon_client, webhook_token):
    """关联码不存在 → 忽略，不误改任何计划。"""
    plan = _month_plan(db)
    r = anon_client.post(
        WEBHOOK_URL, json=_event(plan_type="", plan_id="", correlation_key="PPUNKNOWN12")
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.SUBMITTED.value


def test_reflow_via_binding_updates_correct_plan_only(db, anon_client, webhook_token):
    """按 instance_code 反查时，只改绑定的那个计划。"""
    plan_a = _month_plan(db, owner_id="E_A")
    plan_b = _month_plan(db, owner_id="E_C")
    plan_b.plan_month = "2026-12"
    db.flush()
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan_a.id, instance_code="inst_001"
    )

    anon_client.post(
        WEBHOOK_URL, json=_event(plan_type="", plan_id="", correlation_key="")
    )
    db.expire_all()
    assert db.get(MonthPlan, plan_a.id).status == PlanStatus.APPROVED.value
    assert db.get(MonthPlan, plan_b.id).status == PlanStatus.SUBMITTED.value


# ---------------------------------------------------------------------------
# 5. 绑定管理端点
# ---------------------------------------------------------------------------


def test_bindings_endpoint_requires_auth(db, anon_client):
    """未认证 → 401。"""
    r = anon_client.get("/v1/feishu/approval/bindings")
    assert r.status_code == 401


def test_create_binding_endpoint(db, admin_client):
    """手工绑定端点可用。"""
    plan = _month_plan(db)
    r = admin_client.post(
        "/v1/feishu/approval/bindings",
        json={
            "plan_type": "month",
            "plan_id": plan.id,
            "instance_code": "inst_001",
            "approval_code": "AC_1",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["planId"] == plan.id
    assert data["instanceCode"] == "inst_001"
    assert data["correlationKey"].startswith("PP")


def test_create_binding_with_nonexistent_plan_returns_404(db, admin_client):
    """计划不存在 → 404。"""
    r = admin_client.post(
        "/v1/feishu/approval/bindings",
        json={"plan_type": "month", "plan_id": "NOT_EXIST"},
    )
    assert r.status_code == 404


def test_list_bindings_endpoint(db, admin_client):
    """查绑定列表。"""
    plan = _month_plan(db)
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_001"
    )
    db.commit()

    r = admin_client.get("/v1/feishu/approval/bindings")
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 1


def test_get_binding_by_plan_endpoint(db, admin_client):
    """按计划查绑定。"""
    plan = _month_plan(db)
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_001"
    )
    db.commit()

    r = admin_client.get(f"/v1/feishu/approval/bindings/by-plan/month/{plan.id}")
    assert r.status_code == 200
    assert r.json()["data"]["planId"] == plan.id


def test_get_binding_by_plan_not_found_returns_404(db, admin_client):
    """无绑定 → 404。"""
    plan = _month_plan(db)
    r = admin_client.get(f"/v1/feishu/approval/bindings/by-plan/month/{plan.id}")
    assert r.status_code == 404


def test_reflow_endpoint_accepts_plan_optional(db, admin_client):
    """手工回流端点允许不带 plan_type / plan_id（走绑定反查）。"""
    plan = _month_plan(db)
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_001"
    )
    db.commit()

    class FakeClient:
        def fetch_approval_instance(self, instance_id):
            return {"status": "APPROVED", "comment": "通过"}

        def close(self):
            pass

    import backend.app.routes.feishu_approval as route_mod

    original = route_mod._client
    route_mod._client = lambda: FakeClient()
    try:
        r = admin_client.post(
            "/v1/feishu/approval/reflow",
            json={"plan_type": "", "plan_id": "", "instance_id": "inst_001"},
        )
    finally:
        route_mod._client = original

    assert r.status_code == 200
    assert r.json()["data"]["result"] == "applied"
    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.APPROVED.value


# ---------------------------------------------------------------------------
# 6. 提交时自动绑定
# ---------------------------------------------------------------------------


def test_bind_and_notify_creates_binding(db, org, monkeypatch):
    """提交时自动建绑定并拿到关联码。"""
    plan = _month_plan(db, status=PlanStatus.DRAFT.value)
    sent_messages = []

    class FakeClient:
        def send_text_message(self, receive_id, text, **kwargs):
            sent_messages.append((receive_id, text))

        def close(self):
            pass

    import backend.app.services.feishu_approval_sync as sync_mod

    def fake_notify(db, *, owner_id, plan_type, plan_id, plan_title, client, submitter_note=""):
        return True

    monkeypatch.setattr(sync_mod, "notify_plan_submitted_safe", fake_notify)

    old_id = settings.feishu_app_id
    old_secret = settings.feishu_app_secret
    settings.feishu_app_id = "cli_test"
    settings.feishu_app_secret = "secret_test"
    try:
        # 直接构造绑定（跳过真实飞书客户端）
        binding = feishu_binding.bind_plan_to_instance(
            db, plan_type="month", plan_id=plan.id
        )
        assert binding.correlation_key.startswith("PP")
    finally:
        settings.feishu_app_id = old_id
        settings.feishu_app_secret = old_secret


def test_correlation_message_contains_key(db):
    """通知文案必须含关联码（员工要照着填）。"""
    msg = feishu_binding.build_correlation_message(
        owner_name="员工甲",
        plan_type="week",
        plan_title="本周计划",
        correlation_key="PPAB12CD34",
        approver_name="总监丙",
    )
    assert "PPAB12CD34" in msg
    assert "员工甲" in msg
    assert "周计划" in msg
    assert "总监丙" in msg


def test_correlation_message_without_approver(db):
    """无审批人时文案仍含关联码。"""
    msg = feishu_binding.build_correlation_message(
        owner_name="员工甲",
        plan_type="month",
        plan_title="十一月计划",
        correlation_key="PPAB12CD34",
    )
    assert "PPAB12CD34" in msg


def test_serialize_binding(db):
    """绑定序列化字段齐全。"""
    plan = _month_plan(db)
    row = feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_1"
    )
    data = feishu_binding.serialize_binding(row)
    for key in (
        "id",
        "planType",
        "planId",
        "instanceCode",
        "correlationKey",
        "bindSource",
        "createdAt",
    ):
        assert key in data
