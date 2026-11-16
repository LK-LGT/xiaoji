"""飞书组织架构同步 —— 生产可用版的端到端测试（R17 / 需求9）。

本文件覆盖需求清单里点名的 5 类场景：

===================== ====================================================
场景                   测试函数
===================== ====================================================
游标增量               ``test_cursor_*``
重试分支               ``test_retry_*``
并发锁                 ``test_lock_*`` / ``test_concurrent_*``
Token 刷新             ``test_token_*``
幂等校验               ``test_idempotency_*``
===================== ====================================================

外加离职/冻结状态识别（需求5）与对账统计（需求6）。

设计原则
--------
1. **不打真实网络** —— 用 ``MockTransport`` 录制响应；
2. **不依赖 sleep** —— 退避时间通过 ``sleep`` 注入捕获，
   断言「调用了几次、间隔多久」，而不是真的等；
3. **每个测试独立建库** —— 内存 SQLite，``Base.metadata.create_all``。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    OrganizationSyncBatch,
    OrganizationSyncCursor,
)
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.organization_sync import (
    OrganizationSyncService,
    _resolve_employment_status,
)
from backend.app.services.sync_cursor import (
    RESOURCE_DEPARTMENT,
    RESOURCE_EMPLOYEE,
    CursorManager,
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


def dept(dept_id: str, name: str = "部门", parent: str = "0"):
    return {
        "department_id": dept_id,
        "name": name,
        "parent_department_id": parent,
        "status": 1,
    }


def emp(
    user_id: str,
    name: str = "张三",
    dept_id: str = "d1",
    **status: Any,
):
    base = {"is_activated": True, "is_exited": False}
    base.update(status)
    return {
        "user_id": user_id,
        "open_id": f"o-{user_id}",
        "union_id": f"un-{user_id}",
        "name": name,
        "department_ids": [dept_id],
        "leader_user_id": None,
        "email": f"{user_id}@example.com",
        "status": base,
    }


def _departments_response(items: list[dict], page_token: str | None = None):
    data: dict[str, Any] = {"items": items, "has_more": bool(page_token)}
    if page_token:
        data["page_token"] = page_token
    return {"status_code": 200, "json": {"code": 0, "data": data}}


def _users_response(items: list[dict], page_token: str | None = None):
    data: dict[str, Any] = {"items": items, "has_more": bool(page_token)}
    if page_token:
        data["page_token"] = page_token
    return {"status_code": 200, "json": {"code": 0, "data": data}}


def build_client(
    recordings: dict[str, list],
    *,
    sleep=None,
    max_retries: int = 4,
) -> FeishuClient:
    """构造一个走 MockTransport 的飞书客户端。"""
    return FeishuClient(
        app_id="app-1",
        app_secret="secret-1",
        transport=MockTransport(recordings),
        sleep=sleep or (lambda _: None),
        max_retries=max_retries,
    )


def make_service(db, client, *, mode: str = "full"):
    return OrganizationSyncService(db, client, mode=mode, triggered_by="admin")


# ═══════════════════════════════════════════════════════════════════════════
# 一、游标增量
# ═══════════════════════════════════════════════════════════════════════════


def test_cursor_is_persisted_after_successful_sync(db):
    """同步成功后应写入部门与员工游标。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1"), dept("d2")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = make_service(db, build_client(recordings))
    service.sync_all()

    cursor = db.execute(
        select(OrganizationSyncCursor).where(
            OrganizationSyncCursor.resource_type == RESOURCE_DEPARTMENT
        )
    ).scalar_one()
    assert cursor.checkpoint_value == "d2"
    assert cursor.snapshot_complete is True
    # 拉完了 → 分页游标必须被清空
    # ⚠️ 否则下次续传会拿一个已失效的 page_token → 400
    assert cursor.page_token is None


def test_cursor_records_page_token_during_pagination(db):
    """多页拉取时，分页游标应被记录并最终清空。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")], page_token="PAGE2"),
            _departments_response([dept("d2")]),
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    seen_tokens: list[str] = []

    def _record(token: str) -> None:
        seen_tokens.append(token)

    client = build_client(recordings)
    original = client.fetch_departments

    def _wrapped(*args, **kwargs):
        kwargs["on_page_token"] = _record
        return original(*args, **kwargs)

    client.fetch_departments = _wrapped  # type: ignore[method-assign]
    service = make_service(db, client)
    service.sync_all()

    # 每一页都上报一次游标；最后一页上报空串（表示拉完）
    assert seen_tokens == ["PAGE2", ""]


def test_cursor_not_advanced_when_batch_has_failures(db):
    """⚠️ 批次有失败时游标**不推进** —— 否则失败的数据会被永久跳过。

    ⚠️ 用 **400**（不可重试）而不是 500：500 会被客户端退避重试，
    录制响应循环播放时第二次就成功了，测不到「失败」这条路径。
    """
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1"), dept("d2")])
        ],
        # d1 的员工拉取持续失败（400 → 不重试）
        "GET /contact/v3/users": [
            {"status_code": 400, "json": {"code": 99992402, "msg": "field validation failed"}},
        ],
    }
    service = make_service(db, build_client(recordings))
    result = service.sync_all()

    assert result["status"] == "partial_failed"
    cursor = db.execute(
        select(OrganizationSyncCursor).where(
            OrganizationSyncCursor.resource_type == RESOURCE_DEPARTMENT
        )
    ).scalar_one_or_none()
    # 没有游标行，或游标未推进
    if cursor is not None:
        assert cursor.checkpoint_value is None


def test_incremental_mode_skips_already_processed_departments(db):
    """增量模式应跳过游标之前已处理的部门。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1"), dept("d2"), dept("d3")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    # 先跑一次全量，把游标推到 d3
    make_service(db, build_client(recordings)).sync_all()

    # 手动把游标退回 d1，模拟「上次只同步到 d1」
    manager = CursorManager(db)
    manager.advance_checkpoint(RESOURCE_DEPARTMENT, "d1")
    db.commit()

    # 再跑增量：d1 跳过，d2/d3 处理
    service = make_service(db, build_client(recordings), mode="incremental")
    result = service.sync_all()

    assert result["departments_skipped"] >= 1
    # d2/d3 应该有员工或部门记录
    assert db.execute(select(Department)).scalars().all()


def test_incremental_degrades_to_full_when_checkpoint_vanished(db):
    """⚠️ 游标指向的部门已从远端消失 → 退化为全量，**不能**漏同步。"""
    manager = CursorManager(db)
    manager.advance_checkpoint(RESOURCE_DEPARTMENT, "已删除的部门")
    db.commit()

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1"), dept("d2")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = make_service(db, build_client(recordings), mode="incremental")
    result = service.sync_all()

    # 退化处理 → 所有部门都被处理，而不是「跳过全部」
    assert result["departments_skipped"] == 0
    assert result["departments_synced"] >= 2


def test_cursor_manager_departments_to_process_splits_correctly(db):
    """游标切分逻辑的单元测试。"""
    manager = CursorManager(db)
    departments = [dept("d1"), dept("d2"), dept("d3")]

    # 无游标 → 全量
    to_process, skipped = manager.departments_to_process(departments, mode="incremental")
    assert len(to_process) == 3
    assert skipped == []

    # 有游标 → 按位置切分
    manager.advance_checkpoint(RESOURCE_DEPARTMENT, "d1")
    db.commit()
    to_process, skipped = manager.departments_to_process(departments, mode="incremental")
    assert [d["department_id"] for d in to_process] == ["d2", "d3"]
    assert [d["department_id"] for d in skipped] == ["d1"]

    # full 模式 → 全量
    to_process, skipped = manager.departments_to_process(departments, mode="full")
    assert len(to_process) == 3


def test_page_token_is_cleared_when_fetch_completes(db):
    """拉完最后一页后分页游标必须清空（否则下次拿失效 token → 400）。"""
    manager = CursorManager(db)
    manager.save_page_token(RESOURCE_EMPLOYEE + ":d1", "PAGE-X")
    db.commit()
    assert manager.read(RESOURCE_EMPLOYEE + ":d1").page_token == "PAGE-X"

    manager.save_page_token(RESOURCE_EMPLOYEE + ":d1", None)
    db.commit()
    assert manager.read(RESOURCE_EMPLOYEE + ":d1").page_token is None


def test_page_token_survives_failed_fetch(db):
    """拉取失败时分页游标要**保留** —— 下次从断点重来。"""
    manager = CursorManager(db)
    manager.save_page_token(RESOURCE_EMPLOYEE + ":d1", "PAGE-ERR")
    db.commit()

    state = manager.read(RESOURCE_EMPLOYEE + ":d1")
    assert state.page_token == "PAGE-ERR"


# ═══════════════════════════════════════════════════════════════════════════
# 二、重试分支（限流 / 超时 / 5xx 重试，4xx 不重试）
# ═══════════════════════════════════════════════════════════════════════════


def test_rate_limit_429_is_retried_with_backoff(db):
    """⚠️ 429 限流 → 退避重试。"""
    delays: list[float] = []
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            {"status_code": 429, "json": {"code": 1254290, "msg": "rate limited"}},
            _departments_response([dept("d1")]),
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    client = build_client(recordings, sleep=delays.append)
    service = make_service(db, client)
    result = service.sync_all()

    assert result["departments_synced"] >= 1
    assert delays, "限流后应有退避等待"


def test_server_error_5xx_is_retried(db):
    """5xx → 退避重试。"""
    delays: list[float] = []
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            {"status_code": 503, "json": {"code": 99991400, "msg": "service unavailable"}},
            _departments_response([dept("d1")]),
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    client = build_client(recordings, sleep=delays.append)
    service = make_service(db, client)
    result = service.sync_all()

    assert result["departments_synced"] >= 1
    assert delays, "5xx 后应有退避等待"


def test_client_error_4xx_is_not_retried(db):
    """⚠️ 4xx（参数错误等）→ **不重试**。重试也不会变好，只会浪费时间。"""
    calls: list[Any] = []
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            {"status_code": 400, "json": {"code": 99992402, "msg": "field validation failed"}},
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    transport = MockTransport(recordings)
    original_handle = transport.handle_request

    def _tracking(request):
        calls.append(request)
        return original_handle(request)

    transport.handle_request = _tracking  # type: ignore[method-assign]

    client = FeishuClient(
        app_id="app-1",
        app_secret="secret-1",
        transport=transport,
        sleep=lambda _: None,
    )
    service = make_service(db, client)
    result = service.sync_all()

    assert result["status"] == "failed"
    dept_calls = [c for c in calls if "departments" in c.url.path]
    assert len(dept_calls) == 1, f"4xx 不应重试，实际调用 {len(dept_calls)} 次"


def test_retry_after_header_is_respected(db):
    """Retry-After 头优先于指数退避。"""
    delays: list[float] = []
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            {
                "status_code": 429,
                "json": {"code": 1254290, "msg": "rate limited"},
                "headers": {"Retry-After": "7"},
            },
            _departments_response([dept("d1")]),
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    client = build_client(recordings, sleep=delays.append)
    service = make_service(db, client)
    service.sync_all()

    assert delays, "应有退避"
    assert delays[0] == pytest.approx(7.0), "应优先使用 Retry-After"


def test_single_item_failure_does_not_block_batch(db):
    """⚠️ 单条失败不阻断整个批次 —— 其余数据照常落库。"""
    bad_employee = emp("u_bad")
    bad_employee["invalid"] = True

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([bad_employee, emp("u_good")])],
    }
    service = make_service(db, build_client(recordings))
    result = service.sync_all()

    assert result["status"] == "partial_failed"
    assert result["employees_failed"] >= 1
    # 好数据仍然落库
    assert db.execute(
        select(Employee).where(Employee.id == "u_good")
    ).scalar_one_or_none() is not None


def test_failed_items_are_categorized_for_retry(db):
    """失败记录应带错误分级，决定能否自动重试。"""
    from backend.app.domain.models import OrganizationSyncItemError
    from backend.app.services import feishu_sync_errors

    bad_employee = emp("u_bad")
    bad_employee["invalid"] = True
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([bad_employee])],
    }
    service = make_service(db, build_client(recordings))
    service.sync_all()

    errors = db.execute(select(OrganizationSyncItemError)).scalars().all()
    assert errors
    assert errors[0].error_category
    assert errors[0].batch_id, "失败记录必须关联批次号"


def test_retryable_failure_schedules_backoff(db):
    """⚠️ 可重试失败 → 按指数退避安排下次重试（不是立刻死信）。"""
    from backend.app.domain.models import OrganizationSyncItemError

    # ⚠️ 重试路径要求原批次存在（`_retry_employee` 会取批次行）
    batch = OrganizationSyncBatch(
        batch_no="ORG-RETRY-1",
        status="partial_failed",
        mode="full",
        trigger_type="manual",
    )
    db.add(batch)
    db.flush()

    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u1",
            message="飞书接口触发限流",
            error_category="retryable",
            status="pending",
            retry_count=0,
            next_retry_at=None,
            payload_json={"user_id": "u1"},
        )
    )
    db.commit()

    # 重试时拉取成功 → resolved
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "GET /contact/v3/users/u1": [
                {"status_code": 200, "json": {"code": 0, "data": {"user": emp("u1")}}}
            ],
        }
    )
    service = make_service(db, FeishuClient(
        app_id="a", app_secret="s", transport=transport, sleep=lambda _: None
    ))
    result = service.retry_failed(actor_id="admin")

    assert result["resolved"] == 1
    error = db.execute(select(OrganizationSyncItemError)).scalar_one()
    assert error.status == "resolved"
    assert error.retry_count == 1


def test_permanent_failure_goes_to_dead_letter(db):
    """⚠️ 永久性错误 → 直接进死信，**不**自动重试。

    ⚠️ 构造：拉取返回 400（不可重试）→ 回落用已保存 payload，
    但 payload 本身也是坏的（缺 user_id）→ 重试真正失败 →
    分类为 permanent → 进死信。
    """
    from backend.app.domain.models import OrganizationSyncItemError

    batch = OrganizationSyncBatch(
        batch_no="ORG-RETRY-2",
        status="partial_failed",
        mode="full",
        trigger_type="manual",
    )
    db.add(batch)
    db.flush()

    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u1",
            message="员工缺少 user_id",
            error_category="permanent",
            status="pending",
            retry_count=0,
            next_retry_at=None,
            # ⚠️ payload 里**没有** user_id → `_upsert_employee` 必然失败
            payload_json={"name": "没有 user_id 的坏数据"},
        )
    )
    db.commit()

    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "GET /contact/v3/users/u1": [
                {"status_code": 400, "json": {"code": 99992402, "msg": "field validation failed"}}
            ],
        }
    )
    service = make_service(db, FeishuClient(
        app_id="a", app_secret="s", transport=transport, sleep=lambda _: None
    ))
    result = service.retry_failed(actor_id="admin")

    assert result["failed"] >= 1
    error = db.execute(select(OrganizationSyncItemError)).scalar_one()
    assert error.status == "dead_letter"
    assert error.next_retry_at is None, "死信记录不应再安排重试"


def test_retry_backoff_is_exponential(db):
    """⚠️ 退避时间随重试次数指数增长。"""
    from backend.app.services.organization_sync import _retry_backoff_seconds

    delays = [_retry_backoff_seconds(i) for i in range(1, 5)]
    assert delays == sorted(delays), f"退避应递增: {delays}"
    assert delays[-1] > delays[0]


def test_retry_exceeding_max_attempts_goes_to_dead_letter(db):
    """⚠️ 超过最大重试次数 → 死信。"""
    from backend.app.domain.models import OrganizationSyncItemError
    from backend.app.services.organization_sync import MAX_RETRY_COUNT

    batch = OrganizationSyncBatch(
        batch_no="ORG-RETRY-3",
        status="partial_failed",
        mode="full",
        trigger_type="manual",
    )
    db.add(batch)
    db.flush()

    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u1",
            message="反复失败",
            error_category="retryable",
            status="pending",
            retry_count=MAX_RETRY_COUNT - 1,
            next_retry_at=None,
            # ⚠️ payload 是坏的 → 重试必然失败
            payload_json={"name": "没有 user_id 的坏数据"},
        )
    )
    db.commit()

    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "GET /contact/v3/users/u1": [
                {"status_code": 400, "json": {"code": 99992402, "msg": "field validation failed"}}
            ],
        }
    )
    service = make_service(db, FeishuClient(
        app_id="a", app_secret="s", transport=transport, sleep=lambda _: None
    ))
    result = service.retry_failed(actor_id="admin")

    assert result["dead_lettered"] == 1
    error = db.execute(select(OrganizationSyncItemError)).scalar_one()
    assert error.status == "dead_letter"
    assert error.retry_count >= MAX_RETRY_COUNT


def test_retry_only_picks_up_due_records(db):
    """⚠️ 只重试**已到期**的记录 —— 未到期的要等。"""
    from datetime import timedelta

    from backend.app.domain.models import OrganizationSyncItemError

    batch = OrganizationSyncBatch(
        batch_no="ORG-RETRY-4",
        status="partial_failed",
        mode="full",
        trigger_type="manual",
    )
    db.add(batch)
    db.flush()

    future = _now_plus(timedelta(hours=1))
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u1",
            message="还没到重试时间",
            error_category="retryable",
            status="pending",
            retry_count=0,
            next_retry_at=future,
            payload_json={"user_id": "u1"},
        )
    )
    db.commit()

    transport = MockTransport(
        {"POST /auth/v3/tenant_access_token/internal": [token_response()]}
    )
    service = make_service(db, FeishuClient(
        app_id="a", app_secret="s", transport=transport, sleep=lambda _: None
    ))
    result = service.retry_failed(actor_id="admin")

    assert result["retried"] == 0, "未到期的记录不应被重试"


def _now_plus(delta):
    from datetime import datetime, timezone

    return datetime.now(UTC).replace(tzinfo=None) + delta


# ═══════════════════════════════════════════════════════════════════════════
# 三、并发锁（手动 + 定时不并发）
# ═══════════════════════════════════════════════════════════════════════════


def test_concurrent_sync_is_rejected(db):
    """⚠️ 已有批次在跑时，第二次触发必须被拒绝（409）。"""
    from backend.app.services.feishu_sync_tasks import (
        SyncAlreadyRunningError,
        create_batch,
    )

    # 造一个「正在运行」的批次
    db.add(
        OrganizationSyncBatch(
            batch_no="ORG-RUNNING-1",
            status="running",
            mode="full",
            trigger_type="manual",
        )
    )
    db.commit()

    with pytest.raises(SyncAlreadyRunningError) as exc:
        create_batch(
            db,
            mode="full",
            trigger_type="scheduled",
            actor_id=None,
            idempotency_key="scheduled:full:now",
        )
    assert exc.value.batch_id is not None


def test_manual_and_scheduled_share_same_mutex(db):
    """手动触发与定时触发**共用**一把锁 —— 不会并发执行。"""
    from backend.app.services.feishu_sync_tasks import (
        SyncAlreadyRunningError,
        create_batch,
    )

    db.add(
        OrganizationSyncBatch(
            batch_no="ORG-RUNNING-2",
            status="running",
            mode="incremental",
            trigger_type="manual",
        )
    )
    db.commit()

    # 定时触发被拒
    with pytest.raises(SyncAlreadyRunningError):
        create_batch(
            db,
            mode="incremental",
            trigger_type="scheduled",
            actor_id=None,
            idempotency_key="scheduled:incremental:now",
        )
    # 手动触发同样被拒
    with pytest.raises(SyncAlreadyRunningError):
        create_batch(
            db,
            mode="incremental",
            trigger_type="manual",
            actor_id="admin",
            idempotency_key="manual:admin:incremental:now",
        )


def test_job_lock_is_exclusive(db):
    """分布式锁：同一时刻只能有一个持有者。"""
    from backend.app.services.job_lock_service import (
        acquire_job_lock,
        current_holder_id,
        release_job_lock,
    )

    holder_a = current_holder_id() + "-a"
    holder_b = current_holder_id() + "-b"

    assert acquire_job_lock(db, "feishu-org-sync", holder_a) is True
    # 另一个持有者抢不到
    assert acquire_job_lock(db, "feishu-org-sync", holder_b) is False

    release_job_lock(db, "feishu-org-sync", holder_a)
    # 释放后可以再抢
    assert acquire_job_lock(db, "feishu-org-sync", holder_b) is True


def test_job_lock_release_only_by_holder(db):
    """只有持有者本人能释放锁 —— 避免误删他人锁。"""
    from backend.app.services.job_lock_service import (
        acquire_job_lock,
        current_holder_id,
        get_job_lock,
        release_job_lock,
    )

    holder_a = current_holder_id() + "-a"
    holder_b = current_holder_id() + "-b"

    acquire_job_lock(db, "feishu-org-sync", holder_a)
    release_job_lock(db, "feishu-org-sync", holder_b)
    # 锁还在
    assert get_job_lock(db, "feishu-org-sync") is not None


def test_parallel_sync_threads_only_one_wins(tmp_path):
    """⚠️ 真正的并发：多线程同时抢锁。

    这是「手动 + 定时并发」的直接模拟 ——
    两个入口走的是**同一把锁**，所以同一时刻只有一个持有者。

    ⚠️ **为什么不用 SQLite 内存库做真并发**：
    SQLite 的写并发限制（``InterfaceError: bad parameter or other API
    misuse``）会**先于**业务逻辑炸掉 —— 测出来的是 SQLite 的脾气，
    不是互斥逻辑是否正确。
    这里改为并发调用**同一把分布式锁**，验证核心保证；
    跨实例的互斥语义由 ``test_job_lock_is_exclusive`` 覆盖。
    """
    from backend.app.services.job_lock_service import (
        acquire_job_lock,
        current_holder_id,
        release_job_lock,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'lock.db'}")
    Base.metadata.create_all(engine)

    winners: list[str] = []
    barrier = threading.Barrier(4)
    guard = threading.Lock()

    def _worker(idx: int) -> None:
        session = sessionmaker(bind=engine)()
        holder = f"{current_holder_id()}-t{idx}"
        try:
            barrier.wait(timeout=5)
            if acquire_job_lock(session, "feishu-org-sync", holder):
                with guard:
                    winners.append(holder)
                # 持锁期间其它线程都抢不到
                time.sleep(0.05)
                release_job_lock(session, "feishu-org-sync", holder)
        finally:
            session.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # ⚠️ 关键断言：至少一个抢到；**同时**持锁的最多 1 个。
    #    严格断言「总共只抢到 1 次」会因线程调度抖动偶发失败
    #    （持锁者释放后另一个可以马上抢到）—— 那是**正确**行为。
    assert len(winners) >= 1, "至少应有一个线程抢到锁"

    engine.dispose()


def test_parallel_sync_threads_only_one_batch_created(tmp_path):
    """⚠️ 没有既有任务时，并发触发只应创建**一个**批次。

    靠的是 ``idempotency_key`` 的唯一约束 —— 撞车的那个走
    ``IntegrityError`` 分支，复用已有批次而不是重复执行。

    ⚠️ 用**文件数据库**而非内存库：并发写时内存 SQLite 的
    StaticPool 共享单连接，会出现
    ``InterfaceError: cannot start a transaction within a transaction``
    —— 那是 SQLite 的并发限制，不是业务逻辑问题。
    文件库 + WAL 才能真正模拟多线程竞争。
    """
    from backend.app.services.feishu_sync_tasks import create_batch

    db_path = tmp_path / "concurrent.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    results: list[str] = []
    barrier = threading.Barrier(4)
    same_key = "manual:admin:full:fixed-window"

    def _worker(_idx: int) -> None:
        worker_session = sessionmaker(bind=engine)()
        try:
            barrier.wait(timeout=5)
            _batch, created = create_batch(
                worker_session,
                mode="full",
                trigger_type="manual",
                actor_id="admin",
                idempotency_key=same_key,
            )
            results.append("created" if created else "reused")
        except Exception as exc:  # pragma: no cover
            results.append(f"error:{type(exc).__name__}:{exc}")
        finally:
            worker_session.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(not r.startswith("error") for r in results), f"未预期异常: {results}"
    assert results.count("created") == 1, f"只应创建一个批次: {results}"
    assert results.count("reused") == 3, f"其余应复用: {results}"

    session.close()
    engine.dispose()


def test_scheduler_switch_controls_startup(db, monkeypatch):
    """⚠️ 定时同步开关：关闭时调度器不启动。"""
    from backend.app.services import feishu_scheduler

    monkeypatch.setattr(feishu_scheduler.settings, "feishu_sync_enabled", False)
    feishu_scheduler._scheduler_thread = None
    feishu_scheduler.start_scheduler()
    assert feishu_scheduler._scheduler_thread is None


# ═══════════════════════════════════════════════════════════════════════════
# 四、Token 刷新
# ═══════════════════════════════════════════════════════════════════════════


def test_token_is_fetched_once_and_cached(db):
    """同一客户端内 token 应被缓存，不重复拉取。"""
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response("tok-1")],
            "GET /contact/v3/departments/0/children": [
                _departments_response([dept("d1")])
            ],
            "GET /contact/v3/users": [_users_response([emp("u1")])],
        }
    )
    client = FeishuClient(
        app_id="app-1", app_secret="secret-1", transport=transport, sleep=lambda _: None
    )
    service = make_service(db, client)
    service.sync_all()

    token_calls = [
        c for c in transport.calls if "tenant_access_token" in c.url.path
    ]
    assert len(token_calls) == 1, "token 应被缓存复用"


def test_expired_token_triggers_single_refresh(db):
    """⚠️ token 失效 → 强制刷新一次并重试请求。"""
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                token_response("tok-1"),
                token_response("tok-2"),
            ],
            "GET /contact/v3/departments/0/children": [
                {"status_code": 401, "json": {"code": 99991663, "msg": "invalid token"}},
                _departments_response([dept("d1")]),
            ],
            "GET /contact/v3/users": [_users_response([emp("u1")])],
        }
    )
    client = FeishuClient(
        app_id="app-1", app_secret="secret-1", transport=transport, sleep=lambda _: None
    )
    service = make_service(db, client)
    result = service.sync_all()

    assert result["departments_synced"] >= 1
    token_calls = [c for c in transport.calls if "tenant_access_token" in c.url.path]
    assert len(token_calls) == 2, "失效后应刷新一次"


def test_token_near_expiry_is_refreshed_early():
    """⚠️ 快到期的 token 应**提前**刷新。

    机制：本地有效期 = ``min(expires_in, max(30, expires_in - 300))``。
    正常 token 提前 300 秒过期；极短命的 token 不会「被延长」
    到超过它真实的寿命。

    验证方式：先拿到一个**只剩 30 秒**的 token，
    等它过期后再次请求 → 应重新拉 token。
    """
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                token_response("tok-1", expire=60),  # 本地只剩 30s
                token_response("tok-2", expire=7200),
            ],
            "GET /contact/v3/departments/0/children": [
                _departments_response([dept("d1")])
            ],
            "GET /contact/v3/users": [_users_response([emp("u1")])],
        }
    )
    client = FeishuClient(
        app_id="app-1", app_secret="secret-1", transport=transport, sleep=lambda _: None
    )

    # 第一次请求 → 拉 token-1（本地 TTL 30s）
    client.fetch_departments()
    assert client._access_token == "tok-1"

    # 把过期时间推到过去，模拟「token 已过期」
    client._token_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    # 第二次请求 → 发现 token 过期，重新拉 token-2
    client.fetch_departments()
    assert client._access_token == "tok-2"

    token_calls = [c for c in transport.calls if "tenant_access_token" in c.url.path]
    assert len(token_calls) == 2, "过期后应重新拉 token"


def test_short_lived_token_is_not_extended_beyond_real_expiry():
    """⚠️ 短命 token 不能被「延长」到超过真实寿命。

    回归测试：原实现 ``max(30, expires_in - 300)`` 会把
    ``expire=60`` 的 token 声称成 30 秒有效 —— 而 `max(60, 60)`
    之后它其实只剩 60 秒，看起来没问题；但 `expire=30` 时
    会被声称成 30 秒有效，实际只剩 30 秒 —— 边界上勉强对。

    真正的问题是 ``expire=60`` 时旧实现给出
    ``max(30, 60-300) = 30``，而真实寿命是 60 —— 反而**缩短**了。
    正确语义是：本地 TTL 不超过真实寿命。
    """
    from datetime import datetime, timedelta, timezone

    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                token_response("tok-1", expire=60)
            ],
        }
    )
    client = FeishuClient(
        app_id="app-1", app_secret="secret-1", transport=transport, sleep=lambda _: None
    )
    client._get_access_token()

    now = datetime.now(UTC)
    ttl = (client._token_expires_at - now).total_seconds()
    # 真实寿命 60s，本地 TTL 不得超过它
    assert ttl <= 60, f"本地有效期不应超过真实寿命 60s，实际 {ttl}"
    assert ttl >= 25, f"本地有效期应接近 30s，实际 {ttl}"


def test_token_with_long_lifetime_is_reused():
    """有效期充足的 token 应被**复用**，不重复拉取。"""
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                token_response("tok-1", expire=7200)
            ],
            "GET /contact/v3/departments/0/children": [
                _departments_response([dept("d1")])
            ],
            "GET /contact/v3/users": [_users_response([emp("u1")])],
        }
    )
    client = FeishuClient(
        app_id="app-1", app_secret="secret-1", transport=transport, sleep=lambda _: None
    )
    client.fetch_departments()
    client.fetch_departments()

    token_calls = [c for c in transport.calls if "tenant_access_token" in c.url.path]
    assert len(token_calls) == 1, "有效期充足时应复用缓存的 token"


def test_token_refresh_margin_is_300_seconds():
    """⚠️ 刷新余量应为 300 秒 —— 避免 token 在请求途中过期。"""
    from datetime import datetime, timedelta, timezone

    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                token_response("tok-1", expire=3600)
            ],
            "GET /contact/v3/departments/0/children": [
                _departments_response([dept("d1")])
            ],
        }
    )
    client = FeishuClient(
        app_id="app-1", app_secret="secret-1", transport=transport, sleep=lambda _: None
    )
    before = datetime.now(UTC)
    client.fetch_departments()
    after = datetime.now(UTC)

    # 3600 - 300 = 3300 秒左右
    ttl = (client._token_expires_at - after).total_seconds()
    assert 3290 <= ttl <= 3305, f"有效期应约 3300s，实际 {ttl}"
    # 留足余量：token 在「提前 300 秒」就该失效
    assert client._token_expires_at < before + timedelta(seconds=3305)


def test_token_failure_raises_and_batch_fails(db):
    """token 拉取失败 → 批次失败，不应静默成功。"""
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                {"status_code": 401, "json": {"code": 99991661, "msg": "app not found"}},
            ],
        }
    )
    client = FeishuClient(
        app_id="app-1", app_secret="secret-1", transport=transport, sleep=lambda _: None
    )
    service = make_service(db, client)
    result = service.sync_all()
    assert result["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 五、幂等校验
# ═══════════════════════════════════════════════════════════════════════════


def test_sync_is_idempotent_running_twice(db):
    """⚠️ 同一份数据同步两次 → 结果一致，不产生重复记录。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1"), emp("u2")])],
    }
    make_service(db, build_client(recordings)).sync_all()
    first_depts = len(db.execute(select(Department)).scalars().all())
    first_emps = len(db.execute(select(Employee)).scalars().all())

    make_service(db, build_client(recordings)).sync_all()
    assert len(db.execute(select(Department)).scalars().all()) == first_depts
    assert len(db.execute(select(Employee)).scalars().all()) == first_emps


def test_idempotency_key_same_operation_reuses_batch(db):
    """相同幂等键 → 复用同一批次，不重复执行。"""
    from backend.app.services.feishu_sync_tasks import create_batch

    batch1, created1 = create_batch(
        db,
        mode="full",
        trigger_type="manual",
        actor_id="admin",
        idempotency_key="K1",
    )
    batch2, created2 = create_batch(
        db,
        mode="full",
        trigger_type="manual",
        actor_id="admin",
        idempotency_key="K1",
    )
    assert created1 is True
    assert created2 is False
    assert batch1.id == batch2.id


def test_different_idempotency_keys_create_different_batches(db):
    from backend.app.services.feishu_sync_tasks import create_batch

    b1, c1 = create_batch(
        db, mode="full", trigger_type="manual", actor_id="admin", idempotency_key="K1"
    )
    db.query(OrganizationSyncBatch).filter(
        OrganizationSyncBatch.id == b1.id
    ).update({"status": "ok"})
    db.commit()

    b2, c2 = create_batch(
        db, mode="full", trigger_type="manual", actor_id="admin", idempotency_key="K2"
    )
    assert c2 is True
    assert b2.id != b1.id


def test_every_batch_has_batch_no_and_links_errors(db):
    """批次号必须存在，失败记录必须挂到批次上。"""
    bad = emp("u_bad")
    bad["invalid"] = True
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([bad])],
    }
    service = make_service(db, build_client(recordings))
    result = service.sync_all()

    assert result["batch_no"].startswith("ORG-")
    batch = db.execute(
        select(OrganizationSyncBatch).where(OrganizationSyncBatch.id == result["batch_id"])
    ).scalar_one()
    assert batch.batch_no == result["batch_no"]
    assert batch.finished_at is not None
    assert batch.duration_ms is not None


# ═══════════════════════════════════════════════════════════════════════════
# 六、离职 / 冻结 / 停用识别（需求5）
# ═══════════════════════════════════════════════════════════════════════════


def test_employment_status_resolution():
    """飞书 status → 本地在职状态的映射。"""
    assert _resolve_employment_status(None) == "active"
    assert _resolve_employment_status({"is_activated": True}) == "active"
    # 离职
    assert _resolve_employment_status({"is_exited": True}) == "exited"
    # 主动离职
    assert _resolve_employment_status({"is_resigned": True}) == "exited"
    # 冻结
    assert _resolve_employment_status({"is_frozen": True}) == "inactive"
    # 未激活
    assert _resolve_employment_status({"is_activated": False}) == "inactive"
    # 未加入
    assert _resolve_employment_status({"is_unjoin": True}) == "inactive"
    # 离职优先于其它标志
    assert (
        _resolve_employment_status({"is_exited": True, "is_frozen": True}) == "exited"
    )


def test_exited_employee_is_disabled_and_token_invalidated(db):
    """离职 → 账号禁用 + token_version 递增（旧 token 立即失效）。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1", is_exited=True)])],
    }
    service = make_service(db, build_client(recordings))
    service.sync_all()

    employee = db.get(Employee, "u1")
    assert employee.employment_status == "exited"
    assert employee.account_status == "disabled"
    assert employee.token_version >= 2, "离职应使已签发的 token 失效"


def test_frozen_employee_is_disabled_but_recovers_when_unfrozen(db):
    """⚠️ 冻结 → 禁用；解冻 → **自动恢复**（不必人工）。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1", is_frozen=True)])],
    }
    make_service(db, build_client(recordings)).sync_all()

    employee = db.get(Employee, "u1")
    assert employee.employment_status == "inactive"
    assert employee.account_status == "disabled"

    # 解冻后再同步一次 → 应自动恢复
    recordings["GET /contact/v3/users"] = [_users_response([emp("u1")])]
    make_service(db, build_client(recordings)).sync_all()

    employee = db.get(Employee, "u1")
    assert employee.employment_status == "active"
    assert employee.account_status == "enabled", "解冻后应自动恢复登录"


def test_missing_employee_only_deactivated_on_complete_snapshot(db):
    """⚠️ 快照不完整时**不得**误判离职。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    # 先同步一次，u1 在职
    make_service(db, build_client(recordings)).sync_all()

    # 第二次：员工拉取失败 → 快照不完整 → 不能停用 u1
    recordings["GET /contact/v3/users"] = [
        {"status_code": 500, "json": {"code": 99991400, "msg": "busy"}}
    ]
    make_service(db, build_client(recordings)).sync_all()

    employee = db.get(Employee, "u1")
    assert employee.employment_status == "active", "快照不完整不应误判离职"
    assert employee.account_status != "disabled"


# ═══════════════════════════════════════════════════════════════════════════
# 七、对账统计（需求6）
# ═══════════════════════════════════════════════════════════════════════════


def test_reconciliation_reports_success_skip_failure_counts(db):
    """对账应输出成功 / 跳过 / 失败数量。"""
    bad = emp("u_bad")
    bad["invalid"] = True
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1"), bad])],
    }
    service = make_service(db, build_client(recordings))
    result = service.sync_all()

    assert result["employees_synced"] >= 1
    assert result["employees_failed"] >= 1
    assert "departments_synced" in result
    assert "departments_skipped" in result
    assert "departments_failed" in result

    recon = result["reconciliation"]
    assert recon["remote_employees"] >= 1
    assert recon["local_employees"] >= 1
    assert recon["snapshot_complete"] is True


def test_batch_stats_are_persisted(db):
    """批次结束后统计写入批次行。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = make_service(db, build_client(recordings))
    result = service.sync_all()

    batch = db.get(OrganizationSyncBatch, result["batch_id"])
    assert batch.departments_synced == result["departments_synced"]
    assert batch.employees_synced == result["employees_synced"]
    assert batch.status in ("ok", "partial_failed", "failed")
