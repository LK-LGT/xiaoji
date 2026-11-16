"""R18 事件驱动增量 —— 单元与 API 级测试。

覆盖三层：

1. **队列层**（``org_change_queue``）
   —— 事件解析、去重键、入队去重、领取、退避重试、死信、僵死回收；
2. **消费层**（``org_change_consumer``）
   —— 回查飞书后 upsert、删除事件置离职/停用、身份绑定同时停用；
3. **API 层**（``routes/feishu_contact``）
   —— Verification Token 校验、未配置不放行、去重响应、管理端点鉴权。

⚠️ 为什么这些用例必须存在
------------------------
事件驱动增量是**快路径**，但它一旦漏事件就会**永久丢数据**
（飞书事件推送可能丢失）。所以：

- 队列必须保证「不丢、不重、乱序不错」；
- 消费者必须保证「回查飞书拿完整值」，不能直接用残缺的事件载荷；
- webhook 必须校验 Token，未配置时**绝不能放行**
  —— 否则任何人都能伪造通讯录变更把员工改成离职。

⚠️ 事件**不是唯一数据源**：分页游标全量对账仍是兜底。
本文件只验证事件路径自身的行为，不替代 R17 的游标测试。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    FeishuIdentity,
    OrgChangeQueue,
    SystemAdmin,
)
from backend.app.main import app
from backend.app.routes.auth import _generate_token
from backend.app.services import org_change_queue as queue_mod
from backend.app.services.org_change_queue import (
    EVENT_DEPARTMENT_DELETED,
    EVENT_DEPARTMENT_UPDATED,
    EVENT_USER_DELETED,
    EVENT_USER_UPDATED,
    MAX_RETRY_COUNT,
    STATUS_DEAD_LETTER,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSED,
    build_dedup_key,
    claim_pending_events,
    enqueue_event,
    mark_failed,
    mark_processed,
    parse_contact_event,
    queue_stats,
    release_stale_processing,
)

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def db():
    """每个测试一个独立的内存库。

    ⚠️ 必须隔离 —— 消费者会写员工 / 部门 / 身份绑定，
    共享真实 perf.db 会被测试数据污染。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client_and_db(db, monkeypatch):
    """带管理员登录态的 TestClient。"""
    db.add(Department(id="D1", name="研发部", active=True))
    db.add(
        Employee(
            id="ADMIN001",
            name="管理员",
            login_account="admin001",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.add(
        Employee(
            id="EMP001",
            name="普通员工",
            login_account="emp001",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.add(SystemAdmin(employee_id="ADMIN001", active=True))
    db.commit()

    token, _ = _generate_token("ADMIN001", "admin001")
    emp_token, _ = _generate_token("EMP001", "emp001")

    def override_get_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, db, {"Authorization": f"Bearer {token}"}, {
            "Authorization": f"Bearer {emp_token}"
        }
    app.dependency_overrides.clear()


def _user_event(event_id="EVT001", user_id="u_001", name="张三"):
    """构造一个飞书 v2 格式的用户变更事件。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "contact.user.updated_v3",
            "create_time": "1700000000000",
            "token": "test-token",
        },
        "event": {"user": {"user_id": user_id, "name": name}},
    }


def _dept_event(event_id="EVT002", dept_id="d_001"):
    """构造一个飞书 v2 格式的部门变更事件。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "contact.department.updated_v3",
            "create_time": "1700000000000",
            "token": "test-token",
        },
        "event": {"department": {"department_id": dept_id, "name": "研发部"}},
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. 事件解析
# ═══════════════════════════════════════════════════════════════════════════


def test_parse_user_event_v2():
    """v2 结构：事件名在 header，载荷在 event.user。"""
    parsed = parse_contact_event(_user_event())
    assert parsed is not None
    event_type, resource_type, resource_id, payload = parsed
    assert event_type == EVENT_USER_UPDATED
    assert resource_type == "user"
    assert resource_id == "u_001"
    assert payload["user"]["name"] == "张三"


def test_parse_department_event_v2():
    """部门事件解析。"""
    parsed = parse_contact_event(_dept_event())
    assert parsed is not None
    event_type, resource_type, resource_id, _payload = parsed
    assert event_type == EVENT_DEPARTMENT_UPDATED
    assert resource_type == "department"
    assert resource_id == "d_001"


def test_parse_event_v1_legacy_shape():
    """v1 结构：事件名在顶层 type，载荷直接在顶层。"""
    body = {
        "type": "contact.user.deleted_v3",
        "user": {"user_id": "u_777"},
    }
    parsed = parse_contact_event(body)
    assert parsed is not None
    event_type, _resource_type, resource_id, _payload = parsed
    assert event_type == EVENT_USER_DELETED
    assert resource_id == "u_777"


def test_parse_unknown_event_returns_none():
    """不认识的事件类型 → None（调用方记日志后忽略，不让飞书反复重推）。"""
    body = {
        "header": {"event_type": "contact.unknown.thing_v3"},
        "event": {},
    }
    assert parse_contact_event(body) is None


def test_parse_event_without_resource_id_returns_none():
    """缺少资源 ID → None（无法定位目标，不能入队）。"""
    body = {
        "header": {"event_type": "contact.user.updated_v3"},
        "event": {"user": {}},
    }
    assert parse_contact_event(body) is None


def test_parse_user_event_falls_back_to_open_id():
    """user_id 缺失时退化用 open_id（不同版本事件字段名不一致）。"""
    body = {
        "header": {"event_type": "contact.user.created_v3"},
        "event": {"user": {"open_id": "ou_123"}},
    }
    parsed = parse_contact_event(body)
    assert parsed is not None
    assert parsed[2] == "ou_123"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 去重键
# ═══════════════════════════════════════════════════════════════════════════


def test_dedup_key_prefers_event_id():
    """有 event_id 时用它 —— 飞书重推时它保持不变，判据最精准。"""
    key = build_dedup_key(EVENT_USER_UPDATED, "user", "u_001", event_id="E1")
    assert key == "eid:E1"


def test_dedup_key_without_event_id_uses_time_bucket():
    """无 event_id 时退化为「资源+类型+时间片」。"""
    from datetime import datetime

    moment = datetime(2026, 1, 1, 12, 0, 0)
    key = build_dedup_key(
        EVENT_USER_UPDATED, "user", "u_001", created_at=moment
    )
    assert key.startswith("w:user:u_001:user_updated:")


def test_dedup_key_same_bucket_is_stable():
    """同一时间片内相同事件 → 同一个去重键。"""
    from datetime import datetime

    k1 = build_dedup_key(
        EVENT_USER_UPDATED,
        "user",
        "u_001",
        created_at=datetime(2026, 1, 1, 12, 0, 1),
    )
    k2 = build_dedup_key(
        EVENT_USER_UPDATED,
        "user",
        "u_001",
        created_at=datetime(2026, 1, 1, 12, 0, 2),
    )
    assert k1 == k2


# ═══════════════════════════════════════════════════════════════════════════
# 3. 入队与去重
# ═══════════════════════════════════════════════════════════════════════════


def test_enqueue_event_creates_row(db):
    """入队应创建一条 pending 记录。"""
    row, created = enqueue_event(
        db, EVENT_USER_UPDATED, "user", "u_001", {"user": {"name": "张三"}}
    )
    assert created is True
    assert row is not None
    assert row.status == STATUS_PENDING
    assert row.event_type == EVENT_USER_UPDATED
    assert row.resource_id == "u_001"


def test_enqueue_event_dedups_same_key(db):
    """相同去重键重复入队 → 第二次被去重。"""
    key = build_dedup_key(EVENT_USER_UPDATED, "user", "u_001", event_id="E1")
    _row1, created1 = enqueue_event(
        db, EVENT_USER_UPDATED, "user", "u_001", {}, dedup_key=key
    )
    row2, created2 = enqueue_event(
        db, EVENT_USER_UPDATED, "user", "u_001", {}, dedup_key=key
    )
    assert created1 is True
    assert created2 is False
    assert row2 is None


def test_enqueue_event_allows_new_event_after_processed(db):
    """⚠️ 已处理完的记录**不再参与去重**。

    否则同一资源的第二次变更会被永久丢弃 ——
    这是「去重」最常见的坑：去重窗口必须是「未完成」范围，
    而不是「历史全部」。
    """
    key = build_dedup_key(EVENT_USER_UPDATED, "user", "u_001", event_id="E1")
    row1, _ = enqueue_event(
        db, EVENT_USER_UPDATED, "user", "u_001", {}, dedup_key=key
    )
    mark_processed(db, row1)

    # 同 key 再来一次 → 应当**新建**（不是被去重）
    row2, created2 = enqueue_event(
        db, EVENT_USER_UPDATED, "user", "u_001", {}, dedup_key=key
    )
    assert created2 is True
    assert row2 is not None
    assert row2.id != row1.id


# ═══════════════════════════════════════════════════════════════════════════
# 4. 领取与状态流转
# ═══════════════════════════════════════════════════════════════════════════


def test_claim_pending_events_marks_processing(db):
    """领取待处理事件应把它们置为 processing。"""
    row, _ = enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})
    claimed = claim_pending_events(db)
    assert len(claimed) == 1
    assert claimed[0].id == row.id
    db.refresh(row)
    assert row.status == queue_mod.STATUS_PROCESSING


def test_claim_pending_events_orders_by_created_at(db):
    """领取顺序按创建时间升序 —— 事件必须按真实先后处理。"""
    first, _ = enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})
    first.created_at = first.created_at - timedelta(seconds=10)
    db.commit()
    enqueue_event(db, EVENT_USER_UPDATED, "user", "u_002", {})

    claimed = claim_pending_events(db)
    assert [c.resource_id for c in claimed] == ["u_001", "u_002"]


def test_claim_pending_respects_backoff(db):
    """失败后未到退避时间的事件不该被领取。"""
    row, _ = enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})
    mark_failed(db, row, "boom")
    db.refresh(row)
    assert row.status == STATUS_FAILED
    assert row.next_retry_at is not None

    # next_retry_at 在未来 → 本轮不领取
    assert claim_pending_events(db) == []


def test_mark_failed_retries_then_dead_letter(db):
    """连续失败达到上限 → 进死信。"""
    row, _ = enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})
    for _ in range(MAX_RETRY_COUNT - 1):
        new_status = mark_failed(db, row, "boom")
        assert new_status == STATUS_FAILED

    final_status = mark_failed(db, row, "boom")
    assert final_status == STATUS_DEAD_LETTER
    db.refresh(row)
    assert row.status == STATUS_DEAD_LETTER
    assert row.retry_count == MAX_RETRY_COUNT


def test_release_stale_processing(db):
    """僵死的 processing 记录应被重置为 pending（消费者崩溃兜底）。"""
    row, _ = enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})
    claim_pending_events(db)
    db.refresh(row)
    assert row.status == queue_mod.STATUS_PROCESSING

    # 把 created_at 改老，模拟「处理中途崩溃」
    row.created_at = row.created_at - timedelta(hours=1)
    db.commit()

    released = release_stale_processing(db)
    assert released == 1
    db.refresh(row)
    assert row.status == STATUS_PENDING


def test_queue_stats_counts_by_status(db):
    """队列统计按状态分组。"""
    enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})
    enqueue_event(db, EVENT_USER_UPDATED, "user", "u_002", {})
    stats = queue_stats(db)
    assert stats[STATUS_PENDING] == 2
    assert stats["total"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# 5. 消费者
# ═══════════════════════════════════════════════════════════════════════════


class _FakeFeishuClient:
    """最小飞书客户端替身。"""

    def __init__(self, employee=None, department=None):
        self._employee = employee
        self._department = department
        self.fetched_employee_ids: list[str] = []
        self.fetched_department_ids: list[str] = []

    def fetch_employee(self, user_id):
        self.fetched_employee_ids.append(user_id)
        return self._employee

    def fetch_department(self, dept_id):
        self.fetched_department_ids.append(dept_id)
        return self._department


def test_consumer_upserts_employee_by_requerying_feishu(db):
    """⚠️ 消费者必须**回查飞书**拿完整数据，而不是直接用事件载荷。

    事件载荷可能缺 open_id / union_id —— 直接用会导致身份绑定丢失，
    员工登录不了。
    """
    from backend.app.services.org_change_consumer import OrgChangeConsumer

    db.add(
        Employee(
            id="E_LOCAL",
            name="旧名字",
            login_account="old",
            employment_status="active",
            account_status="enabled",
        )
    )
    db.commit()

    row, _ = enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})
    client = _FakeFeishuClient(
        employee={
            "user_id": "u_001",
            "name": "新名字",
            "open_id": "ou_001",
            "union_id": "on_001",
        }
    )
    consumer = OrgChangeConsumer(db, client)
    result = consumer.process_pending()

    assert result["processed"] == 1
    # 回查确实发生了
    assert client.fetched_employee_ids == ["u_001"]
    # 数据落库了
    emp = db.query(Employee).filter(Employee.feishu_user_id == "u_001").first()
    assert emp is not None
    assert emp.name == "新名字"


def test_consumer_marks_deleted_user_exited_and_disables(db):
    """删除事件 → 离职 + 禁用账号。"""
    from backend.app.services.org_change_consumer import OrgChangeConsumer

    db.add(
        Employee(
            id="E_DEL",
            name="要离职的人",
            login_account="del",
            feishu_user_id="u_del",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.commit()

    row, _ = enqueue_event(db, EVENT_USER_DELETED, "user", "u_del", {})
    consumer = OrgChangeConsumer(db, _FakeFeishuClient())
    result = consumer.process_pending()

    assert result["processed"] == 1
    emp = db.get(Employee, "E_DEL")
    assert emp.employment_status == "exited"
    assert emp.account_status == "disabled"
    assert emp.token_version == 2  # 已签发 token 全部失效


def test_consumer_disables_identity_bindings_on_delete(db):
    """⚠️ 删除事件必须**同时停用身份绑定**。

    否则 ``resolve_employee()`` 还能把已离职员工解析出来 —— 安全风险。
    """
    from backend.app.services.org_change_consumer import OrgChangeConsumer

    db.add(
        Employee(
            id="E_DEL2",
            name="离职者",
            login_account="del2",
            feishu_user_id="u_del2",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.add(
        FeishuIdentity(
            employee_id="E_DEL2",
            identity_type="open_id",
            identity_value="ou_del2",
            active=True,
        )
    )
    db.commit()

    enqueue_event(db, EVENT_USER_DELETED, "user", "u_del2", {})
    consumer = OrgChangeConsumer(db, _FakeFeishuClient())
    consumer.process_pending()

    identity = db.query(FeishuIdentity).filter_by(employee_id="E_DEL2").first()
    assert identity.active is False
    assert identity.unbound_at is not None


def test_consumer_deactivates_deleted_department_but_keeps_employees(db):
    """部门删除 → 停用部门；**不删员工**（员工可能还在别的部门）。"""
    from backend.app.services.org_change_consumer import OrgChangeConsumer

    db.add(Department(id="D_DEL", name="要删的部门", active=True))
    db.add(
        Employee(
            id="E_IN_DEPT",
            name="部门里的员工",
            login_account="ind",
            department_id="D_DEL",
            employment_status="active",
            account_status="enabled",
        )
    )
    db.commit()

    enqueue_event(db, EVENT_DEPARTMENT_DELETED, "department", "D_DEL", {})
    consumer = OrgChangeConsumer(db, _FakeFeishuClient())
    result = consumer.process_pending()

    assert result["processed"] == 1
    dept = db.get(Department, "D_DEL")
    assert dept.active is False
    # 员工**保留**
    assert db.get(Employee, "E_IN_DEPT") is not None


def test_consumer_marks_failure_and_retries(db):
    """处理失败 → 记 failed 并安排重试，不丢事件。"""
    from backend.app.services.org_change_consumer import OrgChangeConsumer

    enqueue_event(db, EVENT_USER_UPDATED, "user", "u_001", {})

    class _BoomClient(_FakeFeishuClient):
        def fetch_employee(self, user_id):
            raise RuntimeError("飞书不可用")

    consumer = OrgChangeConsumer(db, _BoomClient())
    result = consumer.process_pending()

    assert result["failed"] == 1
    row = db.query(OrgChangeQueue).first()
    assert row.status == STATUS_FAILED
    assert row.retry_count == 1
    assert "飞书不可用" in row.error_message


# ═══════════════════════════════════════════════════════════════════════════
# 6. Webhook API
# ═══════════════════════════════════════════════════════════════════════════


def test_webhook_requires_token(client_and_db, monkeypatch):
    """⚠️ Verification Token 不匹配 → 401。

    这是最关键的安全闸门：任何人都能伪造通讯录变更把员工改成离职。
    """
    client, _db, _headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "right-token")

    response = client.post(
        "/v1/feishu/contact/events",
        json=_user_event(),
        headers={"X-Test": "1"},
    )
    assert response.status_code == 401


def test_webhook_without_configured_token_returns_503(client_and_db, monkeypatch):
    """⚠️ **未配置** Verification Token → 503，**绝不能放行**。"""
    client, db, _headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "")

    response = client.post(
        "/v1/feishu/contact/events",
        json=_user_event(),
    )
    assert response.status_code == 503
    # 且**没有**入队
    assert db.query(OrgChangeQueue).count() == 0


def test_webhook_accepts_valid_event_and_enqueues(client_and_db, monkeypatch):
    """Token 正确 → 入队并返回飞书约定的 {"code": 0}。"""
    client, db, _headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "test-token")

    response = client.post("/v1/feishu/contact/events", json=_user_event())
    assert response.status_code == 200
    assert response.json()["code"] == 0

    row = db.query(OrgChangeQueue).first()
    assert row is not None
    assert row.event_type == EVENT_USER_UPDATED
    assert row.resource_id == "u_001"


def test_webhook_deduplicates_repeated_event(client_and_db, monkeypatch):
    """同一 event_id 重复推送 → 只入队一条（飞书重推场景）。"""
    client, db, _headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "test-token")

    client.post("/v1/feishu/contact/events", json=_user_event(event_id="E_DUP"))
    client.post("/v1/feishu/contact/events", json=_user_event(event_id="E_DUP"))

    assert db.query(OrgChangeQueue).count() == 1


def test_webhook_ignores_unknown_event_without_error(client_and_db, monkeypatch):
    """不认识的事件 → 返回 200（让飞书别再重推），且不入队。"""
    client, db, _headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "test-token")

    body = {
        "header": {"event_type": "contact.something.unknown_v3", "token": "test-token"},
        "event": {},
    }
    response = client.post("/v1/feishu/contact/events", json=body)
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert db.query(OrgChangeQueue).count() == 0


def test_webhook_rejects_invalid_json(client_and_db, monkeypatch):
    """非法 JSON → 400。"""
    client, _db, _headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "test-token")

    response = client.post(
        "/v1/feishu/contact/events",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_webhook_does_not_require_login(client_and_db, monkeypatch):
    """Webhook **不要求登录态**（调用方是飞书服务器），只靠 Token 校验。"""
    client, db, _headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "test-token")

    response = client.post("/v1/feishu/contact/events", json=_user_event())
    assert response.status_code == 200
    assert db.query(OrgChangeQueue).count() == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. 管理端点
# ═══════════════════════════════════════════════════════════════════════════


def test_admin_can_view_queue_stats(client_and_db):
    """管理员可查队列统计。"""
    client, _db, headers, _emp = client_and_db
    response = client.get("/v1/feishu/contact/events/stats", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


def test_non_admin_cannot_view_queue_stats(client_and_db):
    """⚠️ 非管理员不得查看队列（含载荷里的资源 ID）。"""
    client, _db, _headers, emp_headers = client_and_db
    response = client.get("/v1/feishu/contact/events/stats", headers=emp_headers)
    assert response.status_code == 403


def test_unauthenticated_cannot_view_queue_stats(client_and_db):
    """未认证不得查看队列。"""
    client, _db, _headers, _emp = client_and_db
    response = client.get("/v1/feishu/contact/events/stats")
    assert response.status_code == 401


def test_admin_can_list_queue_events(client_and_db, monkeypatch):
    """管理员可分页查队列。"""
    client, db, headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "test-token")
    client.post("/v1/feishu/contact/events", json=_user_event())

    response = client.get("/v1/feishu/contact/events", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["resourceId"] == "u_001"


def test_admin_can_retry_dead_letter(client_and_db):
    """管理员可把死信排回 pending。"""
    client, db, headers, _emp = client_and_db
    row = OrgChangeQueue(
        event_type=EVENT_USER_UPDATED,
        resource_type="user",
        resource_id="u_dl",
        status=STATUS_DEAD_LETTER,
        dedup_key="dl-1",
        retry_count=MAX_RETRY_COUNT,
    )
    db.add(row)
    db.commit()

    response = client.post(
        f"/v1/feishu/contact/events/{row.id}/retry", headers=headers
    )
    assert response.status_code == 200
    db.refresh(row)
    assert row.status == STATUS_PENDING
    assert row.retry_count == 0


def test_retry_unknown_event_returns_404(client_and_db):
    """重试不存在的事件 → 404。"""
    client, _db, headers, _emp = client_and_db
    response = client.post(
        "/v1/feishu/contact/events/NOPE/retry", headers=headers
    )
    assert response.status_code == 404


def test_consume_endpoint_requires_admin(client_and_db):
    """⚠️ 手动触发消费必须管理员 —— 它会真的改员工数据。"""
    client, _db, _headers, emp_headers = client_and_db
    response = client.post("/v1/feishu/contact/events/consume", headers=emp_headers)
    assert response.status_code == 403


def test_consume_endpoint_processes_pending(client_and_db, monkeypatch):
    """管理员手动触发消费 → 处理队列。"""
    client, db, headers, _emp = client_and_db
    monkeypatch.setattr(settings, "feishu_contact_event_token", "test-token")
    client.post("/v1/feishu/contact/events", json=_user_event())

    # 避免真实调用飞书：替换客户端工厂。
    import backend.app.routes.feishu_contact as contact_route

    monkeypatch.setattr(
        contact_route, "_client", lambda: _FakeFeishuClient(employee={"user_id": "u_001"})
    )

    response = client.post("/v1/feishu/contact/events/consume", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["processed"] == 1
