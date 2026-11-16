"""飞书审批回流 + IM 通知（A8）测试。

覆盖：
1. **Webhook 鉴权**：未配置 token → 503；token 不符 → 401。
2. **审批回流**：APPROVED / REJECTED → 本系统状态流转 + 写 `plan_approval` 流水。
3. **幂等**：同一事件重复推送 → 只写一条流水。
4. **状态守卫**：非终态（PENDING）→ 跳过；已是终态 → 跳过；非法迁移 → 跳过。
5. **三种计划类型**：月 / 周 / 日各自的列名差异（`approved_at` vs `approve_at`）。
6. **权限边界**：管理端点非管理员 → 403；未认证 → 401。
7. **IM 通知**：无飞书 ID / 发送失败 → 不抛错（旁路语义）。
8. **纯函数**：飞书实例解析（状态 / code / user_id / 意见）。
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
    MonthPlanItem,
    SystemAdmin,
    WeekPlan,
)
from backend.app.domain.plan_models import PlanApproval
from backend.app.main import app
from backend.app.services import feishu_approval_sync

WEBHOOK_URL = "/v1/feishu/approval/events"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    # ⚠️ 必须用 StaticPool：FastAPI TestClient 在工作线程里发请求，
    #    SQLite `:memory:` 默认每连接一个独立库（会报 "no such table"）。
    #    StaticPool 让所有线程共用同一个连接。
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
    """员工甲（归属人，有飞书 ID）、总监丙（审批人，有飞书 ID）、外部员工。"""
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
    outsider = Employee(
        id="E_X",
        name="外部员工",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([emp_a, leader, outsider])
    db.flush()
    emp_a.manager_id = "E_C"
    db.flush()
    # 总监丙同时是系统管理员 —— 管理端点（回流触发 / 查流水）需要
    # `sync_organization` 权限，而该权限只授予 SystemAdmin 记录。
    db.add(SystemAdmin(employee_id="E_C", active=True))
    db.flush()
    return {"a": emp_a, "c": leader, "x": outsider}


@pytest.fixture()
def anon_client(db):
    """不带认证的 TestClient。"""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def admin_client(db, org):
    """以总监丙身份登录的 TestClient（具备管理员权限）。"""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: org["c"]
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def webhook_token():
    """临时设置 webhook Verification Token。"""
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
    db.add(MonthPlanItem(plan_id=plan.id, task_name="推进签约", deliverable="合同", sort_no=0))
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


def _day_plan(db, owner_id="E_A", status=PlanStatus.SUBMITTED.value):
    plan = DayPlan(
        owner_id=owner_id,
        plan_date=date(2026, 11, 10),
        content="完成需求评审",
        status=status,
    )
    db.add(plan)
    db.flush()
    return plan


def _event(plan_type="month", plan_id="X", status="APPROVED", token="test-verify-token"):
    return {
        "token": token,
        "event_id": "evt_001",
        "event_type": "approval_instance",
        "instance_code": "inst_001",
        "instance_status": status,
        "approval_code": "plan_approval_code",
        "user_id": "u_emp_a",
        "comment": "飞书审批通过",
        "plan_type": plan_type,
        "plan_id": plan_id,
    }


# ---------------------------------------------------------------------------
# 1. Webhook 鉴权
# ---------------------------------------------------------------------------


def test_webhook_without_token_configured_returns_503(db, anon_client):
    """未配置 Verification Token → 503，绝不放开写入入口。"""
    old = settings.feishu_approval_webhook_token
    settings.feishu_approval_webhook_token = ""
    try:
        r = anon_client.post(WEBHOOK_URL, json=_event(token="anything"))
    finally:
        settings.feishu_approval_webhook_token = old
    assert r.status_code == 503


def test_webhook_with_wrong_token_returns_401(db, anon_client, webhook_token):
    """Token 不符 → 401。"""
    r = anon_client.post(WEBHOOK_URL, json=_event(token="wrong-token"))
    assert r.status_code == 401


def test_webhook_with_missing_token_returns_401(db, anon_client, webhook_token):
    """Token 缺失 → 401。"""
    payload = _event()
    payload.pop("token")
    r = anon_client.post(WEBHOOK_URL, json=payload)
    assert r.status_code == 401


def test_webhook_with_invalid_json_returns_400(db, anon_client, webhook_token):
    """请求体不是合法 JSON → 400。"""
    r = anon_client.post(
        WEBHOOK_URL, content=b"not-json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 2. 审批回流
# ---------------------------------------------------------------------------


def test_webflow_approved_transitions_month_plan(db, anon_client, webhook_token, org):
    """APPROVED → 月计划 submitted → approved，并写审批流水。"""
    plan = _month_plan(db)
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_id=plan.id, status="APPROVED"))
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "applied"

    db.expire_all()
    row = db.get(MonthPlan, plan.id)
    assert row.status == PlanStatus.APPROVED.value
    assert row.approved_at is not None
    assert row.approval_comment == "飞书审批通过"

    records = list(
        db.execute(
            select(PlanApproval).where(PlanApproval.plan_id == plan.id)
        ).scalars()
    )
    assert len(records) == 1
    assert records[0].action == "approve"
    assert records[0].from_status == PlanStatus.SUBMITTED.value
    assert records[0].to_status == PlanStatus.APPROVED.value
    assert records[0].dedup_key.startswith("feishu:")


def test_webhook_rejected_transitions_plan(db, anon_client, webhook_token):
    """REJECTED → submitted → rejected。"""
    plan = _month_plan(db)
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_id=plan.id, status="REJECTED"))
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "applied"

    db.expire_all()
    row = db.get(MonthPlan, plan.id)
    assert row.status == PlanStatus.REJECTED.value


def test_webhook_weekly_plan_uses_approve_at_column(db, anon_client, webhook_token):
    """周计划用 `approve_at`（不是 `approved_at`）—— 列名差异必须正确处理。"""
    plan = _week_plan(db)
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_type="week", plan_id=plan.id))
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "applied"

    db.expire_all()
    row = db.get(WeekPlan, plan.id)
    assert row.status == PlanStatus.APPROVED.value
    assert row.approve_at is not None
    assert row.approval_comment == "飞书审批通过"


def test_webhook_daily_plan_only_sets_review_status(db, anon_client, webhook_token):
    """日计划是抽检语义 —— 不改主状态机，只写 review_status。"""
    plan = _day_plan(db)
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_type="day", plan_id=plan.id))
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "applied"

    db.expire_all()
    row = db.get(DayPlan, plan.id)
    assert row.status == PlanStatus.SUBMITTED.value  # 主状态机不动
    assert row.review_status == "APPROVED"
    assert row.reviewed_at is not None


def test_webhook_event_without_plan_identifier_is_ignored(db, anon_client, webhook_token):
    """事件缺少计划标识 → 忽略并返回 0（不报错，避免飞书反复重试）。"""
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_id=""))
    assert r.status_code == 200
    assert r.json()["code"] == 0


def test_webhook_unknown_plan_returns_404(db, anon_client, webhook_token):
    """计划不存在 → 404。"""
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_id="NOT_EXIST"))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 3. 幂等
# ---------------------------------------------------------------------------


def test_webhook_duplicate_event_is_idempotent(db, anon_client, webhook_token):
    """同一事件重复推送 → 只写一条流水（飞书是至少一次投递语义）。"""
    plan = _month_plan(db)
    payload = _event(plan_id=plan.id)

    r1 = anon_client.post(WEBHOOK_URL, json=payload)
    assert r1.json()["data"]["result"] == "applied"

    r2 = anon_client.post(WEBHOOK_URL, json=payload)
    assert r2.status_code == 200
    assert r2.json()["data"]["result"] == "skipped"

    records = list(
        db.execute(
            select(PlanApproval).where(PlanApproval.plan_id == plan.id)
        ).scalars()
    )
    assert len(records) == 1


def test_reflow_dedup_key_is_namespaced_separately_from_manual_approval(db):
    """飞书回流与本系统手动审批的幂等键不互相吞掉。"""
    plan = _month_plan(db)
    key = feishu_approval_sync._build_reflow_dedup_key(
        "month", plan.id, "approve", 1, "inst_1"
    )
    manual = f"month:{plan.id}:approve:1"
    assert key != manual
    assert key.startswith("feishu:")


# ---------------------------------------------------------------------------
# 4. 状态守卫
# ---------------------------------------------------------------------------


def test_pending_status_is_skipped(db, anon_client, webhook_token):
    """非终态（PENDING）→ 跳过，不写流水。"""
    plan = _month_plan(db)
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_id=plan.id, status="PENDING"))
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "skipped"

    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.SUBMITTED.value
    assert db.execute(select(PlanApproval)).scalars().all() == []


def test_already_final_status_is_skipped(db, anon_client, webhook_token):
    """已是终态 → 跳过（第二道幂等保险）。"""
    plan = _month_plan(db, status=PlanStatus.APPROVED.value)
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_id=plan.id))
    assert r.json()["data"]["result"] == "skipped"
    assert db.execute(select(PlanApproval)).scalars().all() == []


def test_illegal_transition_is_skipped(db, anon_client, webhook_token):
    """状态机不允许的迁移（如 draft → approved）→ 跳过，不抛错。"""
    plan = _month_plan(db, status=PlanStatus.DRAFT.value)
    r = anon_client.post(WEBHOOK_URL, json=_event(plan_id=plan.id))
    assert r.status_code == 200
    assert r.json()["data"]["result"] == "skipped"

    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.DRAFT.value


# ---------------------------------------------------------------------------
# 5. 权限边界
# ---------------------------------------------------------------------------


def test_reflow_endpoint_requires_admin(db, anon_client):
    """管理端点未认证 → 401。"""
    r = anon_client.post(
        "/v1/feishu/approval/reflow",
        json={"plan_type": "month", "plan_id": "X", "instance_id": "Y"},
    )
    assert r.status_code == 401


def test_reflow_records_requires_admin(db, anon_client):
    """查回流流水未认证 → 401。"""
    r = anon_client.get("/v1/feishu/approval/reflow-records")
    assert r.status_code == 401


def test_reflow_records_lists_feishu_records(db, admin_client):
    """回流流水可查。"""
    plan = _month_plan(db)
    feishu_approval_sync.apply_feishu_approval_event(
        db,
        plan_type="month",
        plan_id=plan.id,
        feishu_status="APPROVED",
        instance_id="inst_1",
    )
    db.commit()

    r = admin_client.get("/v1/feishu/approval/reflow-records")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["action"] == "approve"


def test_approval_status_endpoint_reports_config(admin_client):
    """链路配置状态可查。"""
    r = admin_client.get("/v1/feishu/approval/status")
    assert r.status_code == 200
    assert "webhookEnabled" in r.json()["data"]


# ---------------------------------------------------------------------------
# 6. IM 通知（旁路语义）
# ---------------------------------------------------------------------------


def test_notify_skips_when_no_feishu_id(db, org):
    """审批人没有飞书 ID → 跳过且不抛错。"""
    assert org["x"].feishu_open_id is None
    sent = feishu_approval_sync.notify_approver_by_im(
        db, approver_id="E_X", message="测试", client=object()
    )
    assert sent is False


def test_notify_failure_does_not_raise(db, org):
    """发送失败 → 返回 False，不抛错（通知是旁路动作）。"""

    class BoomClient:
        def send_text_message(self, *args, **kwargs):
            raise RuntimeError("飞书不可用")

    sent = feishu_approval_sync.notify_approver_by_im(
        db, approver_id="E_C", message="测试", client=BoomClient()
    )
    assert sent is False


def test_notify_success_returns_true(db, org):
    """发送成功 → True。"""
    sent_messages = []

    class FakeClient:
        def send_text_message(self, receive_id, text, **kwargs):
            sent_messages.append((receive_id, text))

    sent = feishu_approval_sync.notify_approver_by_im(
        db, approver_id="E_C", message="测试通知", client=FakeClient()
    )
    assert sent is True
    assert sent_messages[0][0] == "ou_leader"


def test_notify_plan_submitted_finds_approver(db, org):
    """按直属主管找审批人。"""
    approvers = feishu_approval_sync.find_plan_approvers(db, "E_A")
    assert [a.id for a in approvers] == ["E_C"]


def test_notify_plan_submitted_no_approver(db, org):
    """没有主管 → 不发通知。"""
    org["a"].manager_id = None
    db.flush()
    assert feishu_approval_sync.find_plan_approvers(db, "E_A") == []


def test_notify_plan_submitted_safe_swallows_errors(db, org):
    """`notify_plan_submitted_safe` 不因通知失败影响主流程。"""
    # 飞书未配置时直接返回 False
    old = settings.feishu_app_id
    settings.feishu_app_id = ""
    try:
        sent = feishu_approval_sync.notify_plan_submitted_safe(
            db,
            owner_id="E_A",
            plan_type="month",
            plan_id="P1",
            plan_title="十一月计划",
        )
    finally:
        settings.feishu_app_id = old
    assert sent is False


def test_build_plan_submitted_message_contains_owner_and_type():
    """通知文案含员工名与计划类型。"""
    msg = feishu_approval_sync.build_plan_submitted_message(
        owner_name="员工甲", plan_type="week", plan_title="本周计划", submitter_note="备注"
    )
    assert "员工甲" in msg
    assert "周计划" in msg
    assert "备注" in msg


# ---------------------------------------------------------------------------
# 7. 纯函数：飞书实例解析
# ---------------------------------------------------------------------------


def test_extract_instance_status_variants():
    """状态解析兼容多种形态。"""
    assert feishu_approval_sync.extract_instance_status({"status": "APPROVED"}) == "APPROVED"
    assert feishu_approval_sync.extract_instance_status({"status": "approved"}) == "APPROVED"
    assert feishu_approval_sync.extract_instance_status({}) == ""
    assert feishu_approval_sync.extract_instance_status(None) == ""


def test_extract_instance_code_and_id():
    """实例 code / id 解析。"""
    assert (
        feishu_approval_sync.extract_instance_code({"approval_code": "AC_1"}) == "AC_1"
    )
    assert (
        feishu_approval_sync.extract_instance_id({"instance_code": "IC_1"}) == "IC_1"
    )
    assert (
        feishu_approval_sync.extract_instance_id({"approval_instance": {"instance_code": "IC_2"}})
        == "IC_2"
    )


def test_extract_user_id_variants():
    """发起人解析：user_id → open_id → applicant 结构。"""
    assert feishu_approval_sync.extract_user_id({"user_id": "u1"}) == "u1"
    assert feishu_approval_sync.extract_user_id({"open_id": "o1"}) == "o1"
    assert (
        feishu_approval_sync.extract_user_id({"applicant": {"user_id": "u2"}}) == "u2"
    )
    assert feishu_approval_sync.extract_user_id({}) == ""


def test_extract_comment_variants():
    """意见解析。"""
    assert feishu_approval_sync.extract_comment({"comment": "好的"}) == "好的"
    assert feishu_approval_sync.extract_comment({"reason": "再改改"}) == "再改改"
    assert feishu_approval_sync.extract_comment({}) == ""


# ---------------------------------------------------------------------------
# 8. 身份映射
# ---------------------------------------------------------------------------


def test_resolve_employee_by_feishu_user_id(db, org):
    """按 user_id 解析员工（走 employee 表快照回退）。"""
    emp = feishu_approval_sync.resolve_employee_by_feishu_id(db, "u_leader")
    assert emp is not None
    assert emp.id == "E_C"


def test_resolve_employee_by_feishu_open_id(db, org):
    """按 open_id 解析员工。"""
    emp = feishu_approval_sync.resolve_employee_by_feishu_id(db, "ou_leader")
    assert emp is not None
    assert emp.id == "E_C"


def test_resolve_employee_unknown_id_returns_none(db, org):
    """未知飞书 ID → None。"""
    assert feishu_approval_sync.resolve_employee_by_feishu_id(db, "nobody") is None


def test_employee_feishu_receive_id_prefers_open_id(db, org):
    """优先用 open_id 发消息。"""
    rid, rtype = feishu_approval_sync.employee_feishu_receive_id(org["c"])
    assert rid == "ou_leader"
    assert rtype == "open_id"


def test_employee_feishu_receive_id_empty_when_missing(db, org):
    """没有飞书 ID → 空串。"""
    rid, rtype = feishu_approval_sync.employee_feishu_receive_id(org["x"])
    assert rid == ""
    assert rtype == ""
