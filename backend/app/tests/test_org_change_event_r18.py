"""飞书通讯录事件驱动增量（R18）测试。

覆盖：

===================== ====================================================
场景                   测试函数
===================== ====================================================
事件解析               ``test_parse_*``
入队与去重             ``test_enqueue_*`` / ``test_dedup_*``
事件消费               ``test_consume_*``
离职/删除处理          ``test_handle_*``
失败重试与死信         ``test_retry_*`` / ``test_dead_letter_*``
Webhook 安全基线       ``test_webhook_*``
兜底机制               ``test_fallback_*``
===================== ====================================================

设计原则
--------
1. **不打真实网络** —— 用 ``MockTransport`` 录制响应；
2. **不依赖 sleep** —— 退避时间只断言「排到多久之后」，不真的等；
3. **每个测试独立建库** —— 内存 SQLite。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    FeishuIdentity,
    OrgChangeQueue,
)
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.org_change_consumer import OrgChangeConsumer
from backend.app.services.org_change_queue import (
    DEDUP_WINDOW_SECONDS,
    MAX_RETRY_COUNT,
    STATUS_DEAD_LETTER,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSED,
    build_dedup_key,
    claim_pending_events,
    enqueue_event,
    parse_contact_event,
    queue_stats,
    release_stale_processing,
)

# ═══════════════════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def token_response(token: str = "token-1", expire: int = 7200):
    return {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": token, "expire": expire},
    }


def make_client(recordings):
    return FeishuClient(
        app_id="app-1",
        app_secret="secret-1",
        transport=MockTransport(recordings),
        sleep=lambda _: None,
    )


def user_payload(
    user_id: str = "u1",
    name: str = "张三",
    *,
    open_id: str | None = None,
    union_id: str | None = None,
    is_exited: bool = False,
    is_activated: bool = True,
    departments: list[str] | None = None,
):
    """飞书用户（``fetch_employee`` 的返回结构）。"""
    return {
        "user_id": user_id,
        "open_id": open_id or f"ou_{user_id}",
        "union_id": union_id or f"on_{user_id}",
        "name": name,
        "email": f"{user_id}@example.com",
        "department_ids": departments or ["d1"],
        "leader_user_id": None,
        "status": {
            "is_activated": is_activated,
            "is_exited": is_exited,
            "is_frozen": False,
            "is_resigned": False,
        },
    }


def dept_payload(department_id: str = "d1", name: str = "研发部", parent: str = "0"):
    """飞书部门（``fetch_department`` 的返回结构）。"""
    return {
        "department_id": department_id,
        "name": name,
        "parent_department_id": parent,
    }


def contact_event(
    event_type: str = "contact.user.updated_v3",
    *,
    event_id: str = "evt-1",
    token: str = "secret-token",
    user: dict[str, Any] | None = None,
    department: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """飞书通讯录事件（v2 结构）。"""
    event: dict[str, Any] = {}
    if user is not None:
        event["user"] = user
    if department is not None:
        event["department"] = department
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": event_type,
            "create_time": "1700000000000",
            "token": token,
            "app_id": "app-1",
            "tenant_key": "tk",
        },
        "event": event,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 一、事件解析
# ═══════════════════════════════════════════════════════════════════════════


def test_parse_user_updated_event():
    """⚠️ 用户变更事件应解析出 (事件类型, 资源类型, 资源ID, 载荷)。"""
    body = contact_event(
        "contact.user.updated_v3",
        user={"user_id": "u123", "name": "李四"},
    )
    result = parse_contact_event(body)
    assert result is not None
    event_type, resource_type, resource_id, payload = result
    assert event_type == "user_updated"
    assert resource_type == "user"
    assert resource_id == "u123"
    assert payload["user"]["name"] == "李四"


def test_parse_user_deleted_event():
    body = contact_event("contact.user.deleted_v3", user={"user_id": "u9"})
    result = parse_contact_event(body)
    assert result is not None
    assert result[0] == "user_deleted"
    assert result[2] == "u9"


def test_parse_department_event():
    body = contact_event(
        "contact.department.updated_v3",
        department={"department_id": "d1", "name": "研发部"},
    )
    result = parse_contact_event(body)
    assert result is not None
    assert result[0] == "department_updated"
    assert result[1] == "department"
    assert result[2] == "d1"


def test_parse_unknown_event_returns_none():
    """⚠️ 不认识的事件类型 → 返回 None（调用方返回 200，不让飞书重推）。"""
    body = contact_event("contact.approval.created_v3", user={"user_id": "u1"})
    assert parse_contact_event(body) is None


def test_parse_event_without_resource_id_returns_none():
    """⚠️ 缺少资源 ID → 无法定位实体，返回 None。"""
    body = contact_event("contact.user.updated_v3", user={"name": "没有ID"})
    assert parse_contact_event(body) is None


def test_parse_event_without_type_returns_none():
    assert parse_contact_event({}) is None


def test_parse_v1_style_event():
    """⚠️ 兼容旧版事件（没有 header，事件名在 type 字段）。"""
    body = {
        "type": "contact.user.updated_v3",
        "token": "t",
        "event": {"user": {"user_id": "u7"}},
    }
    result = parse_contact_event(body)
    assert result is not None
    assert result[2] == "u7"


def test_parse_event_falls_back_to_open_id():
    """⚠️ 载荷里只有 open_id 时也应能定位资源。"""
    body = contact_event(
        "contact.user.updated_v3",
        user={"open_id": "ou_xxx", "name": "只有open_id"},
    )
    result = parse_contact_event(body)
    assert result is not None
    assert result[2] == "ou_xxx"


# ═══════════════════════════════════════════════════════════════════════════
# 二、入队与去重
# ═══════════════════════════════════════════════════════════════════════════


def test_enqueue_event_creates_row(db):
    """入队应生成一条 pending 记录。"""
    row, created = enqueue_event(
        db, "user_updated", "user", "u1", {"user": {"user_id": "u1"}}
    )
    assert created is True
    assert row is not None
    assert row.status == STATUS_PENDING
    assert row.resource_id == "u1"
    assert row.event_type == "user_updated"


def test_enqueue_event_with_same_event_id_is_deduplicated(db):
    """⚠️ 飞书重推同一 event_id → 只留一条。"""
    key = build_dedup_key("user_updated", "user", "u1", event_id="evt-1")
    row1, created1 = enqueue_event(
        db, "user_updated", "user", "u1", {}, dedup_key=key
    )
    row2, created2 = enqueue_event(
        db, "user_updated", "user", "u1", {}, dedup_key=key
    )
    assert created1 is True
    assert created2 is False
    assert row2 is None
    assert row1 is not None


def test_enqueue_event_without_event_id_uses_time_bucket(db):
    """⚠️ 没有 event_id 时按时间片去重。

    同一资源在同一时间片内的重复推送只处理一次。
    """
    key1 = build_dedup_key("user_updated", "user", "u1")
    key2 = build_dedup_key("user_updated", "user", "u1")
    assert key1 == key2
    assert key1.startswith("w:user:u1:user_updated:")


def test_enqueue_different_resource_not_deduplicated(db):
    """不同资源不应互相去重。"""
    _, c1 = enqueue_event(db, "user_updated", "user", "u1", {})
    _, c2 = enqueue_event(db, "user_updated", "user", "u2", {})
    assert c1 is True
    assert c2 is True


def test_enqueue_processed_event_allows_new_one(db):
    """⚠️ 已处理完的历史事件不应阻止同一资源的**新**变更。

    否则同一资源第二次变更会被永久丢弃。
    """
    key = build_dedup_key("user_updated", "user", "u1", event_id="evt-1")
    row, _ = enqueue_event(db, "user_updated", "user", "u1", {}, dedup_key=key)
    row.status = STATUS_PROCESSED
    db.commit()

    _, created = enqueue_event(
        db, "user_updated", "user", "u1", {}, dedup_key=key
    )
    assert created is True, "已处理的事件不应阻止新事件"


def test_queue_stats(db):
    """队列统计应按状态分组。"""
    enqueue_event(db, "user_updated", "user", "u1", {})
    enqueue_event(db, "user_updated", "user", "u2", {})
    stats = queue_stats(db)
    assert stats[STATUS_PENDING] == 2
    assert stats["total"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# 三、领取与消费
# ═══════════════════════════════════════════════════════════════════════════


def test_claim_pending_events_marks_processing(db):
    """领取后状态应变为 processing。"""
    enqueue_event(db, "user_updated", "user", "u1", {})
    rows = claim_pending_events(db)
    assert len(rows) == 1
    assert rows[0].status == "processing"


def test_claim_respects_retry_backoff(db):
    """⚠️ 未到重试时间的记录不应被领取。"""
    enqueue_event(db, "user_updated", "user", "u1", {})
    row = db.execute(select(OrgChangeQueue)).scalar_one()
    row.next_retry_at = row.created_at + timedelta(hours=1)
    db.commit()

    assert claim_pending_events(db) == []


def test_release_stale_processing(db):
    """⚠️ 僵死的 processing 记录应被重置 —— 否则事件静默丢失。"""
    enqueue_event(db, "user_updated", "user", "u1", {})
    row = db.execute(select(OrgChangeQueue)).scalar_one()
    row.status = "processing"
    # 伪装成很久以前的
    row.created_at = row.created_at - timedelta(hours=2)
    db.commit()

    released = release_stale_processing(db, stale_seconds=900)
    assert released == 1
    db.refresh(row)
    assert row.status == STATUS_PENDING


def test_consumer_processes_user_event(db):
    """⚠️ 用户变更事件 → 回查飞书 → upsert 员工。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/users/u1": [
            {"status_code": 200, "json": {"code": 0, "data": {"user": user_payload("u1")}}}
        ],
    }
    enqueue_event(db, "user_updated", "user", "u1", {})

    consumer = OrgChangeConsumer(db, make_client(recordings))
    result = consumer.process_pending()

    assert result["processed"] == 1
    employee = db.get(Employee, "u1")
    assert employee is not None
    assert employee.name == "张三"


def test_consumer_creates_batch_for_audit(db):
    """⚠️ 事件处理必须产生批次 —— 否则改动无法审计追溯。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/users/u1": [
            {"status_code": 200, "json": {"code": 0, "data": {"user": user_payload("u1")}}}
        ],
    }
    enqueue_event(db, "user_updated", "user", "u1", {})

    consumer = OrgChangeConsumer(db, make_client(recordings))
    consumer.process_pending()

    row = db.execute(select(OrgChangeQueue)).scalar_one()
    assert row.batch_id is not None
    assert row.status == STATUS_PROCESSED

    from backend.app.domain.models import OrganizationSyncBatch

    batch = db.get(OrganizationSyncBatch, row.batch_id)
    assert batch is not None
    assert batch.trigger_type == "event"


def test_consumer_upserts_identity_bindings(db):
    """⚠️ 回查飞书拿到完整数据后，身份绑定应建立。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/users/u1": [
            {
                "status_code": 200,
                "json": {
                    "code": 0,
                    "data": {
                        "user": user_payload("u1", open_id="ou_1", union_id="on_1")
                    },
                },
            }
        ],
    }
    enqueue_event(db, "user_created", "user", "u1", {})

    consumer = OrgChangeConsumer(db, make_client(recordings))
    consumer.process_pending()

    identities = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all()
    values = {i.identity_type: i.identity_value for i in identities}
    assert values.get("user_id") == "u1"
    assert values.get("open_id") == "ou_1"
    assert values.get("union_id") == "on_1"


def test_consumer_handles_department_event(db):
    """部门变更事件 → upsert 部门。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/d1": [
            {
                "status_code": 200,
                "json": {
                    "code": 0,
                    "data": {"department": dept_payload("d1", "研发部", "d0")},
                },
            }
        ],
    }
    enqueue_event(db, "department_updated", "department", "d1", {})

    consumer = OrgChangeConsumer(db, make_client(recordings))
    result = consumer.process_pending()

    assert result["processed"] == 1
    dept = db.get(Department, "d1")
    assert dept is not None
    assert dept.name == "研发部"
    assert dept.parent_id == "d0"


# ═══════════════════════════════════════════════════════════════════════════
# 四、离职 / 删除处理
# ═══════════════════════════════════════════════════════════════════════════


def test_consumer_marks_user_deleted_as_exited(db):
    """⚠️ 用户删除事件 → 离职 + 禁用账号。"""
    employee = Employee(
        id="u1",
        name="张三",
        employment_status="active",
        account_status="enabled",
        feishu_user_id="u1",
    )
    db.add(employee)
    db.commit()

    enqueue_event(db, "user_deleted", "user", "u1", {})
    consumer = OrgChangeConsumer(db, make_client({}))
    result = consumer.process_pending()

    assert result["processed"] == 1
    db.refresh(employee)
    assert employee.employment_status == "exited"
    assert employee.account_status == "disabled"
    assert employee.token_version and employee.token_version >= 2


def test_consumer_deactivates_identities_on_deletion(db):
    """⚠️ 离职必须停用身份绑定 —— 否则能匹配到已离职员工（安全风险）。"""
    employee = Employee(
        id="u1",
        name="张三",
        employment_status="active",
        account_status="enabled",
        feishu_user_id="u1",
    )
    db.add(employee)
    db.add(
        FeishuIdentity(
            employee_id="u1",
            identity_type="user_id",
            identity_value="u1",
            active=True,
        )
    )
    db.commit()

    enqueue_event(db, "user_deleted", "user", "u1", {})
    consumer = OrgChangeConsumer(db, make_client({}))
    consumer.process_pending()

    identity = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalar_one()
    assert not identity.active, "离职员工的身份绑定必须停用"


def test_consumer_marks_department_deleted_inactive(db):
    """部门删除 → 停用部门（不删员工）。"""
    db.add(Department(id="d1", name="研发部"))
    db.commit()

    enqueue_event(db, "department_deleted", "department", "d1", {})
    consumer = OrgChangeConsumer(db, make_client({}))
    result = consumer.process_pending()

    assert result["processed"] == 1
    dept = db.get(Department, "d1")
    assert dept is not None
    assert dept.active is False
    assert dept.feishu_deleted_at is not None


def test_consumer_treats_empty_refetch_as_deleted(db):
    """⚠️ 回查返回空（事件乱序：先删后更新）→ 按删除处理，不静默跳过。"""
    employee = Employee(
        id="u1",
        name="张三",
        employment_status="active",
        account_status="enabled",
        feishu_user_id="u1",
    )
    db.add(employee)
    db.commit()

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/users/u1": [
            {"status_code": 200, "json": {"code": 0, "data": {"user": {}}}}
        ],
    }
    enqueue_event(db, "user_updated", "user", "u1", {})

    consumer = OrgChangeConsumer(db, make_client(recordings))
    consumer.process_pending()

    db.refresh(employee)
    assert employee.employment_status == "exited"


def test_deleted_user_cannot_be_resolved(db):
    """⚠️ 端到端：离职后 resolve_employee 解析不到。"""
    from backend.app.services.feishu_identity_service import FeishuIdentityService

    employee = Employee(
        id="u1",
        name="张三",
        employment_status="active",
        account_status="enabled",
        feishu_user_id="u1",
    )
    db.add(employee)
    db.add(
        FeishuIdentity(
            employee_id="u1",
            identity_type="user_id",
            identity_value="u1",
            active=True,
        )
    )
    db.commit()

    enqueue_event(db, "user_deleted", "user", "u1", {})
    consumer = OrgChangeConsumer(db, make_client({}))
    consumer.process_pending()

    assert FeishuIdentityService(db).resolve_employee(user_id="u1") is None


# ═══════════════════════════════════════════════════════════════════════════
# 五、失败重试与死信
# ═══════════════════════════════════════════════════════════════════════════


def test_consumer_failure_is_recorded_and_retried(db):
    """⚠️ 处理失败 → 标记 failed 并按退避排期，不丢事件。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/users/u1": [
            {
                "status_code": 400,
                "json": {"code": 99992402, "msg": "field validation failed"},
            }
        ],
    }
    enqueue_event(db, "user_updated", "user", "u1", {})

    consumer = OrgChangeConsumer(db, make_client(recordings))
    result = consumer.process_pending()

    assert result["processed"] == 0
    assert result["failed"] == 1

    row = db.execute(select(OrgChangeQueue)).scalar_one()
    assert row.status == STATUS_FAILED
    assert row.retry_count == 1
    assert row.next_retry_at is not None
    assert row.error_message


def test_consumer_failure_becomes_dead_letter_after_max_retries(db):
    """⚠️ 超过最大重试次数 → 进死信，等人工处理。"""
    row = OrgChangeQueue(
        event_type="user_updated",
        resource_type="user",
        resource_id="u1",
        status=STATUS_FAILED,
        retry_count=MAX_RETRY_COUNT - 1,
        dedup_key="k1",
    )
    db.add(row)
    db.commit()

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/users/u1": [
            {"status_code": 400, "json": {"code": 99992402, "msg": "bad"}}
        ],
    }
    consumer = OrgChangeConsumer(db, make_client(recordings))
    result = consumer.process_pending()

    assert result["dead_letter"] == 1
    db.refresh(row)
    assert row.status == STATUS_DEAD_LETTER
    assert row.next_retry_at is None


def test_dead_letter_event_is_not_claimed_again(db):
    """死信不应被自动领取 —— 等人工介入。"""
    row = OrgChangeQueue(
        event_type="user_updated",
        resource_type="user",
        resource_id="u1",
        status=STATUS_DEAD_LETTER,
        dedup_key="k1",
    )
    db.add(row)
    db.commit()

    assert claim_pending_events(db) == []


# ═══════════════════════════════════════════════════════════════════════════
# 六、Webhook 安全基线
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def contact_client(tmp_path):
    """挂载 contact 路由的测试客户端（**独立文件库**，跨线程安全）。

    ⚠️ **为什么要单独建库**，不能复用 ``db`` 夹具：

    FastAPI 的 ``TestClient`` 会在**另一个线程**里跑请求处理函数。
    内存 SQLite（``SingletonThreadPool``）每个线程一条连接，
    于是测试线程写入的数据在请求线程**根本看不到** ——
    表现就是「入队了但查不到」。

    解决：用一个**文件库** + ``check_same_thread=False``，
    所有线程共用同一个文件，天然可见。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.db import get_db
    from backend.app.routes import feishu_contact

    db_path = tmp_path / "contact_events.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    app = FastAPI()
    app.include_router(feishu_contact.router)
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    client.db_session = session  # type: ignore[attr-defined]
    client.db_engine = engine  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        session.close()
        engine.dispose()


def test_webhook_rejects_when_token_not_configured(contact_client, monkeypatch):
    """⚠️ 未配置 Verification Token → 503 不放行。

    否则任何人都能伪造通讯录变更把员工改成离职。
    """
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "feishu_contact_event_token", "")
    response = contact_client.post(
        "/v1/feishu/contact/events", json=contact_event(token="anything")
    )
    assert response.status_code == 503


def test_webhook_rejects_wrong_token(contact_client, monkeypatch):
    """⚠️ Token 不匹配 → 401。"""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "feishu_contact_event_token", "right-token")
    response = contact_client.post(
        "/v1/feishu/contact/events", json=contact_event(token="wrong-token")
    )
    assert response.status_code == 401


def test_webhook_accepts_valid_token_and_enqueues(contact_client, monkeypatch):
    """⚠️ 正确 Token → 入队并返回 200。"""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "feishu_contact_event_token", "secret-token")
    response = contact_client.post(
        "/v1/feishu/contact/events",
        json=contact_event(
            "contact.user.updated_v3",
            event_id="evt-99",
            token="secret-token",
            user={"user_id": "u1", "name": "李四"},
        ),
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0

    rows = contact_client.db_session.execute(select(OrgChangeQueue)).scalars().all()
    assert len(rows) == 1
    assert rows[0].resource_id == "u1"
    assert rows[0].status == STATUS_PENDING


def test_webhook_deduplicates_repeated_push(contact_client, monkeypatch):
    """⚠️ 飞书重推同一 event → 只入队一条。"""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "feishu_contact_event_token", "secret-token")
    body = contact_event(
        "contact.user.updated_v3",
        event_id="evt-dup",
        token="secret-token",
        user={"user_id": "u1"},
    )
    contact_client.post("/v1/feishu/contact/events", json=body)
    contact_client.post("/v1/feishu/contact/events", json=body)

    rows = contact_client.db_session.execute(select(OrgChangeQueue)).scalars().all()
    assert len(rows) == 1


def test_webhook_ignores_unknown_event_type(contact_client, monkeypatch):
    """⚠️ 不认识的事件 → 返回 200（不让飞书反复重推），且不入队。"""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "feishu_contact_event_token", "secret-token")
    response = contact_client.post(
        "/v1/feishu/contact/events",
        json=contact_event(
            "contact.unknown.thing_v3",
            token="secret-token",
            user={"user_id": "u1"},
        ),
    )
    assert response.status_code == 200
    rows = contact_client.db_session.execute(select(OrgChangeQueue)).scalars().all()
    assert rows == []


def test_webhook_rejects_non_json_body(contact_client, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "feishu_contact_event_token", "secret-token")
    response = contact_client.post(
        "/v1/feishu/contact/events",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# 七、兜底机制
# ═══════════════════════════════════════════════════════════════════════════


def test_event_queue_is_not_the_only_source_of_truth(db):
    """⚠️ **兜底保证**：分页游标全量对账仍然存在，事件只是快路径。

    飞书事件可能丢失 —— 所以绝不能只靠事件。
    这里断言「全量对账能力仍然可用」：
    跑一次全量同步后，缺失员工会被判定离职。
    """
    from backend.app.services.organization_sync import OrganizationSyncService

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            {"status_code": 200, "json": {"code": 0, "data": {"items": []}}}
        ],
        "GET /contact/v3/users": [
            {"status_code": 200, "json": {"code": 0, "data": {"items": []}}}
        ],
    }
    # 先造一个本地员工
    db.add(
        Employee(
            id="u_old",
            name="老员工",
            employment_status="active",
            account_status="enabled",
            feishu_user_id="u_old",
        )
    )
    db.commit()

    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    service.sync_all()

    # 全量快照完整且远端没有这个人 → 判定离职
    employee = db.get(Employee, "u_old")
    assert employee.employment_status == "exited", "全量对账兜底必须仍然生效"


def test_consumer_does_not_break_full_sync(db):
    """⚠️ 事件消费与全量同步共存，互不干扰。

    全量同步负责兜底，事件负责快路径 —— 两者并存才安全。
    """
    from backend.app.services.organization_sync import OrganizationSyncService

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/users/u1": [
            {"status_code": 200, "json": {"code": 0, "data": {"user": user_payload("u1")}}}
        ],
    }
    enqueue_event(db, "user_updated", "user", "u1", {})

    consumer = OrgChangeConsumer(db, make_client(recordings))
    consumer.process_pending()

    # 全量同步仍然可跑
    full_recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            {"status_code": 200, "json": {"code": 0, "data": {"items": []}}}
        ],
        "GET /contact/v3/users": [
            {"status_code": 200, "json": {"code": 0, "data": {"items": []}}}
        ],
    }
    service = OrganizationSyncService(db, make_client(full_recordings), mode="full")
    result = service.sync_all()
    assert result["status"] in ("ok", "partial_failed")


def test_event_scheduler_uses_separate_lock():
    """⚠️ 事件消费锁必须与全量同步锁**不同**。

    共用会让两个任务互相排队 —— 事件变更迟迟不生效。
    """
    from backend.app.services.feishu_sync_tasks import JOB_NAME as SYNC_JOB
    from backend.app.services.org_event_scheduler import JOB_NAME as EVENT_JOB

    assert EVENT_JOB != SYNC_JOB
