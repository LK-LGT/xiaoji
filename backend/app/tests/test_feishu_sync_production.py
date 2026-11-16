"""飞书组织同步的生产可用性增强测试。

覆盖：
- 错误分级与自动重试策略（退避、死信）；
- 完整快照 vs 不完整快照下的离职判定差异；
- 异步同步的幂等键与互斥；
- 同步状态与游标接口；
- 新增字段的 schema 兼容。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    OrganizationSyncBatch,
    OrganizationSyncCursor,
    OrganizationSyncItemError,
)
from backend.app.services import feishu_sync_errors, feishu_sync_tasks
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.organization_sync import (
    MAX_RETRY_COUNT,
    OrganizationSyncService,
)


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


# ──────────────────────────────────────────────────────────────
# 错误分级
# ──────────────────────────────────────────────────────────────


def test_error_categories_for_feishu_codes():
    """飞书错误码应映射到正确的分级。"""
    assert feishu_sync_errors.FEISHU_CODE_CATEGORY[99991663] == (
        feishu_sync_errors.CATEGORY_RETRYABLE
    )
    assert feishu_sync_errors.FEISHU_CODE_CATEGORY[99991672] == (
        feishu_sync_errors.CATEGORY_CONFIG
    )
    assert feishu_sync_errors.FEISHU_CODE_CATEGORY[1254290] == (
        feishu_sync_errors.CATEGORY_RETRYABLE
    )


def test_classify_message_by_keyword():
    """消息关键词应能推断分级。"""
    assert feishu_sync_errors.classify_message("飞书接口触发限流") == (
        feishu_sync_errors.CATEGORY_RETRYABLE
    )
    assert feishu_sync_errors.classify_message("应用未获得通讯录权限") == (
        feishu_sync_errors.CATEGORY_CONFIG
    )
    assert feishu_sync_errors.classify_message("飞书身份 user_id 已绑定到其他员工") == (
        feishu_sync_errors.CATEGORY_MANUAL
    )
    assert feishu_sync_errors.classify_message("员工缺少 user_id") == (
        feishu_sync_errors.CATEGORY_PERMANENT
    )
    assert feishu_sync_errors.classify_message("某种没见过的错误") == (
        feishu_sync_errors.CATEGORY_UNKNOWN
    )


def test_classify_item_failure_with_error_code():
    """带数字错误码的记录应按错误码分级。"""
    assert feishu_sync_errors.classify_item_failure(
        "随便什么消息", error_code="FEISHU_99991672"
    ) == feishu_sync_errors.CATEGORY_CONFIG


def test_is_auto_retryable_only_for_retryable():
    """只有瞬时故障允许自动重试。"""
    assert feishu_sync_errors.is_auto_retryable(feishu_sync_errors.CATEGORY_RETRYABLE)
    assert not feishu_sync_errors.is_auto_retryable(feishu_sync_errors.CATEGORY_CONFIG)
    assert not feishu_sync_errors.is_auto_retryable(feishu_sync_errors.CATEGORY_MANUAL)
    assert not feishu_sync_errors.is_auto_retryable(feishu_sync_errors.CATEGORY_PERMANENT)


def test_category_payload_shape():
    """前端消费的分类信息应包含标签与自动重试标记。"""
    payload = feishu_sync_errors.category_payload(feishu_sync_errors.CATEGORY_RETRYABLE)
    assert payload["category"] == "retryable"
    assert payload["autoRetryable"] is True
    assert payload["label"]


# ──────────────────────────────────────────────────────────────
# 失败记录的错误分级与重试策略
# ──────────────────────────────────────────────────────────────


def test_record_error_classifies_and_schedules_retry(db):
    """可重试失败应被分类，并安排退避时间。"""
    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    batch = OrganizationSyncBatch(
        batch_no="ORG-TEST-1", status="running", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()

    service._record_error(batch, "employee", "u1", "飞书接口触发限流，请稍后重试")
    row = db.query(OrganizationSyncItemError).one()
    assert row.error_category == feishu_sync_errors.CATEGORY_RETRYABLE
    assert row.next_retry_at is not None
    assert row.status == "pending"


def test_record_error_manual_conflict_has_no_auto_retry(db):
    """需人工处理的冲突不应安排自动重试。"""
    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    batch = OrganizationSyncBatch(
        batch_no="ORG-TEST-2", status="running", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()

    service._record_error(batch, "employee", "u1", "飞书身份 user_id 已绑定到其他员工")
    row = db.query(OrganizationSyncItemError).one()
    assert row.error_category == feishu_sync_errors.CATEGORY_MANUAL
    assert row.next_retry_at is None


def test_retry_failed_moves_permanent_error_to_dead_letter(db):
    """永久性错误重试后应进入死信，而不是无限重试。"""
    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    batch = OrganizationSyncBatch(
        batch_no="ORG-TEST-3", status="running", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u_missing",
            message="员工缺少 user_id",
            error_category=feishu_sync_errors.CATEGORY_PERMANENT,
            status="pending",
            next_retry_at=None,
        )
    )
    db.commit()

    result = service.retry_failed(actor_id="admin")
    assert result["failed"] == 1
    assert result["dead_lettered"] == 1
    row = db.query(OrganizationSyncItemError).one()
    assert row.status == "dead_letter"
    assert row.next_retry_at is None


def test_retry_failed_schedules_backoff_for_retryable(db):
    """瞬时故障重试失败后应安排指数退避的下次重试。"""
    client = _mock_client(failing_employee=True)
    service = OrganizationSyncService(db, client, triggered_by="admin")
    batch = OrganizationSyncBatch(
        batch_no="ORG-TEST-4", status="running", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u_missing",
            message="飞书接口触发限流",
            error_category=feishu_sync_errors.CATEGORY_RETRYABLE,
            status="pending",
            next_retry_at=None,
        )
    )
    db.commit()

    service.retry_failed(actor_id="admin")
    row = db.query(OrganizationSyncItemError).one()
    assert row.status == "pending"
    assert row.next_retry_at is not None
    assert row.next_retry_at > datetime.now(timezone.utc).replace(tzinfo=None)


def test_retry_count_beyond_max_goes_to_dead_letter(db):
    """超过最大重试次数的记录应进入死信。"""
    client = _mock_client(failing_employee=True)
    service = OrganizationSyncService(db, client, triggered_by="admin")
    batch = OrganizationSyncBatch(
        batch_no="ORG-TEST-5", status="running", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u_missing",
            message="飞书接口触发限流",
            error_category=feishu_sync_errors.CATEGORY_RETRYABLE,
            status="pending",
            retry_count=MAX_RETRY_COUNT - 1,
            next_retry_at=None,
        )
    )
    db.commit()

    service.retry_failed(actor_id="admin")
    row = db.query(OrganizationSyncItemError).one()
    assert row.status == "dead_letter"


# ──────────────────────────────────────────────────────────────
# 离职判定：完整快照 vs 不完整快照
# ──────────────────────────────────────────────────────────────


def test_complete_snapshot_marks_missing_employee_as_exited(db):
    """完整快照下，远端消失的员工应被判定离职并禁用账号。"""
    db.add(Department(id="d1", name="研发部"))
    db.add(
        Employee(
            id="u_gone",
            name="已离职员工",
            feishu_user_id="u_gone",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.commit()

    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()

    assert result["snapshot_complete"] is True
    gone = db.get(Employee, "u_gone")
    assert gone.employment_status == "exited"
    assert gone.account_status == "disabled"
    assert gone.token_version == 2


def test_incomplete_snapshot_does_not_disable_employees(db):
    """快照不完整（有部门拉取失败）时，不得停用任何员工。"""
    db.add(Department(id="d1", name="研发部"))
    db.add(
        Employee(
            id="u_gone",
            name="在职员工",
            feishu_user_id="u_gone",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.commit()

    client = _mock_client(failing_department=True)
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()

    assert result["snapshot_complete"] is False
    employee = db.get(Employee, "u_gone")
    assert employee.employment_status == "active"
    assert employee.account_status == "enabled"
    assert employee.token_version == 1


def test_incremental_mode_never_disables_missing_employees(db):
    """增量模式下不得把「本次没拉到」判定为离职。"""
    db.add(Department(id="d1", name="研发部"))
    db.add(
        Employee(
            id="u_gone",
            name="在职员工",
            feishu_user_id="u_gone",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.commit()

    client = _mock_client()
    service = OrganizationSyncService(db, client, mode="incremental", triggered_by="admin")
    result = service.sync_all()

    assert result["snapshot_complete"] is True
    employee = db.get(Employee, "u_gone")
    assert employee.employment_status == "active"
    assert employee.account_status == "enabled"


# ──────────────────────────────────────────────────────────────
# 对账与数据质量
# ──────────────────────────────────────────────────────────────


def test_reconciliation_includes_data_quality(db):
    """对账结果应包含数据质量指标。"""
    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()

    quality = result["reconciliation"]["data_quality"]
    assert "no_department_count" in quality
    assert "no_manager_count" in quality
    assert "cyclic_manager_count" in quality
    assert "no_login_account_count" in quality


def test_reconciliation_reports_snapshot_completeness(db):
    """对账结果应标明快照是否完整。"""
    client = _mock_client(failing_department=True)
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()

    recon = result["reconciliation"]
    assert recon["snapshot_complete"] is False
    assert recon["failed_departments"] == ["d1"]


# ──────────────────────────────────────────────────────────────
# 游标与批次幂等
# ──────────────────────────────────────────────────────────────


def test_cursor_is_created_and_persisted(db):
    """同步应写入游标记录。"""
    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    service.sync_all()

    cursors = db.execute(select(OrganizationSyncCursor)).scalars().all()
    assert len(cursors) >= 1


def test_create_batch_is_idempotent(db):
    """相同幂等键应复用同一批次。"""
    first, created_first = feishu_sync_tasks.create_batch(
        db,
        mode="full",
        trigger_type="manual",
        actor_id="admin",
        idempotency_key="manual:admin:full:fixed",
    )
    second, created_second = feishu_sync_tasks.create_batch(
        db,
        mode="full",
        trigger_type="manual",
        actor_id="admin",
        idempotency_key="manual:admin:full:fixed",
    )
    assert created_first is True
    assert created_second is False
    assert first.id == second.id


def test_create_batch_rejects_concurrent_running(db):
    """已有运行中批次时，新的触发应被拒绝。"""
    db.add(
        OrganizationSyncBatch(
            batch_no="ORG-RUNNING",
            status="running",
            mode="full",
            trigger_type="manual",
        )
    )
    db.commit()

    with pytest.raises(feishu_sync_tasks.SyncAlreadyRunningError) as exc_info:
        feishu_sync_tasks.create_batch(
            db,
            mode="full",
            trigger_type="manual",
            actor_id="admin",
            idempotency_key="manual:admin:full:other",
        )
    assert exc_info.value.batch_id is not None


def test_build_idempotency_key_is_stable_within_window():
    """同一窗口内生成的幂等键应一致。"""
    key_a = feishu_sync_tasks.build_idempotency_key(
        mode="full", trigger_type="manual", actor_id="admin"
    )
    key_b = feishu_sync_tasks.build_idempotency_key(
        mode="full", trigger_type="manual", actor_id="admin"
    )
    assert key_a == key_b
    assert key_a.startswith("manual:admin:full:")


def test_build_idempotency_key_differs_by_actor_and_mode():
    """不同操作人或模式应生成不同幂等键。"""
    key_a = feishu_sync_tasks.build_idempotency_key(
        mode="full", trigger_type="manual", actor_id="admin"
    )
    key_b = feishu_sync_tasks.build_idempotency_key(
        mode="full", trigger_type="manual", actor_id="other"
    )
    key_c = feishu_sync_tasks.build_idempotency_key(
        mode="incremental", trigger_type="manual", actor_id="admin"
    )
    assert key_a != key_b
    assert key_a != key_c


def test_get_sync_status_reports_configuration(db):
    """状态接口应报告配置与批次信息。"""
    status = feishu_sync_tasks.get_sync_status(db)
    assert "configured" in status
    assert "scheduledEnabled" in status
    assert "cursors" in status
    assert isinstance(status["cursors"], list)


# ──────────────────────────────────────────────────────────────
# 超时批次恢复
# ──────────────────────────────────────────────────────────────


def test_recover_stale_batches_marks_failed(db):
    """超时的 running 批次应被标记为 failed。"""
    stale_started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    db.add(
        OrganizationSyncBatch(
            batch_no="ORG-STALE",
            status="running",
            mode="full",
            trigger_type="scheduled",
            started_at=stale_started,
        )
    )
    db.commit()

    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    recovered = service.recover_stale_batches()

    assert recovered == 1
    row = db.query(OrganizationSyncBatch).one()
    assert row.status == "failed"
    assert row.error_message


# ──────────────────────────────────────────────────────────────
# Mock 客户端
# ──────────────────────────────────────────────────────────────


def _mock_client(
    *,
    failing_department: bool = False,
    failing_employee: bool = False,
) -> FeishuClient:
    employee = {
        "user_id": "u1",
        "open_id": "o1",
        "union_id": "un1",
        "name": "张三",
        "department_ids": ["d1"],
        "leader_user_id": None,
        "email": "zhang@example.com",
        "status": {"is_activated": True, "is_exited": False},
    }
    recordings: dict[str, list] = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            {
                "status_code": 200,
                "json": {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "department_id": "d1",
                                "name": "研发部",
                                "parent_department_id": "0",
                                "status": 1,
                            }
                        ]
                    },
                },
            }
        ],
    }
    if failing_department:
        # 部门员工拉取持续失败：用于验证「快照不完整」。
        recordings["GET /contact/v3/users"] = [
            {"status_code": 500, "json": {"code": 99991400, "msg": "busy"}}
        ]
    else:
        recordings["GET /contact/v3/users"] = [
            {"status_code": 200, "json": {"code": 0, "data": {"items": [employee]}}}
        ]
    if failing_employee:
        recordings["GET /contact/v3/users/u_missing"] = [
            {"status_code": 500, "json": {"code": 99991400, "msg": "busy"}}
        ]
    return FeishuClient("app", "secret", transport=MockTransport(recordings), sleep=lambda _: None)
