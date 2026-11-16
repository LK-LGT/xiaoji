"""飞书组织同步的事务边界回归测试（R6 / P1）。

背景
----
真实库里堆积了 92 条同步失败，消息全部是::

    (sqlite3.IntegrityError) FOREIGN KEY constraint failed
    [SQL: INSERT INTO employee (id, name, email, feishu_ope...

根因是**事务边界错误**，不是飞书接口问题：

1. ``_sync_departments`` 用 ``flush()`` 写部门 —— 不提交；
2. ``_sync_employees`` 在**同一个事务**里写员工；
3. 某个员工插入失败时，``self.db.rollback()`` 把**尚未提交的部门插入一并撤销**；
4. 后续所有员工的 ``department_id`` 外键目标消失 → 全部报 FK 失败。

于是「1 个员工失败」被放大成「全部员工失败」。

修复方式
--------
用 SQLAlchemy 的 SAVEPOINT（``begin_nested()``）把**每条记录**的写入
隔离在自己的子事务里：单条失败只回滚该 savepoint，
外层事务里已写入的部门 / 其它员工全部保留。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    OrganizationSyncBatch,
    OrganizationSyncItemError,
)
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.organization_sync import OrganizationSyncService


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _token_response(token: str = "token-1", expire: int = 7200):
    return {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": token, "expire": expire},
    }


def _employee(user_id: str, dept_id: str, name: str) -> dict:
    return {
        "user_id": user_id,
        "open_id": f"o_{user_id}",
        "union_id": f"un_{user_id}",
        "name": name,
        "department_ids": [dept_id],
        "leader_user_id": None,
        "email": f"{user_id}@example.com",
        "status": {"is_activated": True, "is_exited": False},
    }


def _client_with_employees(
    employees: list[dict],
    *,
    departments: list[dict] | None = None,
) -> FeishuClient:
    """构造一个飞书客户端：一次部门树 + 一次员工列表。"""
    dept_items = departments or [
        {"department_id": "d1", "name": "研发部", "parent_department_id": "0", "status": 1}
    ]
    recordings: dict[str, list] = {
        "POST /auth/v3/tenant_access_token/internal": [_token_response()],
        "GET /contact/v3/departments/0/children": [
            {
                "status_code": 200,
                "json": {"code": 0, "data": {"items": dept_items}},
            }
        ],
        "GET /contact/v3/users": [
            {"status_code": 200, "json": {"code": 0, "data": {"items": employees}}}
        ],
    }
    return FeishuClient(
        "app", "secret", transport=MockTransport(recordings), sleep=lambda _: None
    )


def _client_for_retry(employees: list[dict]) -> FeishuClient:
    """补偿重试用的客户端：部门树 + 员工列表正常，单用户详情接口失败。

    单用户接口失败是真实场景（限流 / 该用户已被删除），
    重试路径必须能回落到失败记录里保存的 payload。
    """
    recordings: dict[str, list] = {
        "POST /auth/v3/tenant_access_token/internal": [_token_response()],
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
        "GET /contact/v3/users": [
            {"status_code": 200, "json": {"code": 0, "data": {"items": employees}}}
        ],
    }
    return FeishuClient(
        "app", "secret", transport=MockTransport(recordings), sleep=lambda _: None
    )


# ──────────────────────────────────────────────────────────────
# 核心回归：单条失败不得连带撤销部门
# ──────────────────────────────────────────────────────────────


def test_single_employee_failure_does_not_wipe_departments(db):
    """单个员工失败时，部门写入必须保留（回归 92 条 FK 报错）。

    修复前：第一个员工失败 -> rollback 撤销全部部门插入
             -> 其余员工 department_id 外键失败 -> 全员报错。
    修复后：只有那 1 条失败，其余员工正常落库，部门也在。

    ⚠️ 唯一约束冲突是「先到先得」：这里让**后**处理的 ``u_bad`` 与
    ``u_good`` 抢同一个 open_id，于是 ``u_good`` 先落库成功，
    ``u_bad`` 才是失败的那条 —— 用来验证失败不会传染给已成功的记录。
    """
    good = _employee("u_good", "d1", "好员工")
    bad = _employee("u_bad", "d1", "坏员工")
    # 让 u_bad 的 open_id 与 u_good 相同 —— 触发 uq_employee_feishu_open_id。
    bad["open_id"] = good["open_id"]

    client = _client_with_employees([good, bad])
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()

    # 部门必须落库。
    assert db.get(Department, "d1") is not None, "部门写入被 rollback 连带撤销了"

    # 先落库的好员工必须保留 —— 失败不能传染。
    assert db.get(Employee, "u_good") is not None, "好员工被后一条失败连带撤销"

    # 坏员工确实失败了，但只有它自己。
    assert result["employees_failed"] == 1
    assert result["employees_synced"] >= 1


def test_department_rows_survive_when_employee_insert_fails(db):
    """即使有员工插入失败，已写入的部门也不能被回滚。"""
    # 两个员工共用同一个 open_id -> 后者触发唯一约束冲突。
    first = _employee("u1", "d1", "员工一")
    second = _employee("u2", "d1", "员工二")
    second["open_id"] = first["open_id"]

    client = _client_with_employees([first, second])
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()

    assert result["employees_failed"] >= 1
    dept = db.get(Department, "d1")
    assert dept is not None, "部门写入被员工失败的 rollback 连带撤销"
    assert dept.name == "研发部"
    # 先落库的员工不受影响。
    assert db.get(Employee, "u1") is not None


def test_successful_sync_leaves_no_errors(db):
    """正常同步不应产生任何失败记录。"""
    employees = [_employee(f"u{i}", "d1", f"员工{i}") for i in range(5)]
    client = _client_with_employees(employees)
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()

    assert result["employees_failed"] == 0
    assert result["employees_synced"] == 5
    assert db.query(OrganizationSyncItemError).count() == 0
    assert db.query(Employee).count() == 5
    assert db.query(Department).count() == 1


def test_manager_backfill_survives_individual_failure(db):
    """manager_id 回填阶段单条失败，也不得回滚其它员工。"""
    # u_lead 是上级；u_a 的 leader_user_id 指向 u_lead。
    lead = _employee("u_lead", "d1", "上级")
    a = _employee("u_a", "d1", "甲")
    a["leader_user_id"] = "u_lead"

    client = _client_with_employees([lead, a])
    service = OrganizationSyncService(db, client, triggered_by="admin")
    service.sync_all()

    employee = db.get(Employee, "u_a")
    assert employee is not None
    assert employee.manager_id == "u_lead"


def test_batch_is_marked_partial_failed_not_failed(db):
    """部分失败应记为 partial_failed，批次本身不算崩。"""
    good = _employee("u_good", "d1", "好员工")
    bad = _employee("u_bad", "d1", "坏员工")
    bad["open_id"] = good["open_id"]

    client = _client_with_employees([good, bad])
    service = OrganizationSyncService(db, client, triggered_by="admin")
    service.sync_all()

    batch = db.query(OrganizationSyncBatch).one()
    assert batch.status == "partial_failed"
    assert batch.departments_synced >= 1


def test_failed_item_is_recorded_with_category(db):
    """失败应被记录到失败表并完成分类。"""
    good = _employee("u_good", "d1", "好员工")
    bad = _employee("u_bad", "d1", "坏员工")
    bad["open_id"] = good["open_id"]

    client = _client_with_employees([good, bad])
    service = OrganizationSyncService(db, client, triggered_by="admin")
    service.sync_all()

    errors = db.query(OrganizationSyncItemError).all()
    assert errors, "失败应被记录"
    row = errors[0]
    assert row.resource_type == "employee"
    assert row.error_category is not None, "失败记录应完成错误分类"


def test_multiple_departments_all_persist(db):
    """多部门场景下，所有部门都应落库。"""
    departments = [
        {"department_id": "d1", "name": "研发部", "parent_department_id": "0", "status": 1},
        {"department_id": "d2", "name": "市场部", "parent_department_id": "0", "status": 1},
        {"department_id": "d3", "name": "财务部", "parent_department_id": "0", "status": 1},
    ]
    employees = [
        _employee("u1", "d1", "甲"),
        _employee("u2", "d2", "乙"),
        _employee("u3", "d3", "丙"),
    ]
    client = _client_with_employees(employees, departments=departments)
    service = OrganizationSyncService(db, client, triggered_by="admin")
    service.sync_all()

    rows = db.execute(select(Department.id)).scalars().all()
    assert set(rows) >= {"d1", "d2", "d3"}


# ──────────────────────────────────────────────────────────────
# 补偿重试路径：同样的事务隔离要求
# ──────────────────────────────────────────────────────────────


def test_retry_path_resolves_stale_errors(db):
    """历史失败记录（含 payload）应能被重试路径修复。

    这条对应真实库里堆积的 92 条 FK 失败记录 —— 它们带完整 payload、
    状态 pending、下次重试时间已过，重试端点应能捞出来修复。
    """
    db.add(Department(id="d1", name="研发部"))
    db.commit()

    batch = OrganizationSyncBatch(
        batch_no="ORG-STALE", status="partial_failed", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()

    stale_payload = _employee("u_stale", "d1", "遗留员工")
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u_stale",
            message="(sqlite3.IntegrityError) FOREIGN KEY constraint failed",
            error_code="SYNC_ITEM_FAILED",
            error_category=None,
            status="pending",
            retry_count=0,
            next_retry_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
            payload_json=stale_payload,
        )
    )
    db.commit()

    client = _client_for_retry([stale_payload])
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.retry_failed(actor_id="admin")

    assert result["retried"] == 1
    assert result["resolved"] == 1
    assert result["failed"] == 0
    assert db.get(Employee, "u_stale") is not None

    row = db.query(OrganizationSyncItemError).one()
    assert row.status == "resolved"
    assert row.resolved_at is not None


def test_retry_failure_does_not_abort_other_retries(db):
    """单条重试失败不得中断其余重试。"""
    batch = OrganizationSyncBatch(
        batch_no="ORG-RETRY", status="partial_failed", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()

    good_payload = _employee("u_ok", "d1", "可重试员工")
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u_ok",
            message="上次失败",
            error_code="SYNC_ITEM_FAILED",
            error_category="retryable",
            status="pending",
            retry_count=0,
            next_retry_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
            payload_json=good_payload,
        )
    )
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u_bad",
            message="上次失败",
            error_code="SYNC_ITEM_FAILED",
            error_category="retryable",
            status="pending",
            retry_count=0,
            next_retry_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
            # payload 缺 user_id -> 重试时必然失败
            payload_json={"name": "缺 user_id"},
        )
    )
    db.commit()

    client = _client_for_retry([good_payload])
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.retry_failed(actor_id="admin")

    assert result["retried"] == 2
    assert result["resolved"] == 1
    assert result["failed"] == 1
    # 成功的那条员工确实落库了。
    assert db.get(Employee, "u_ok") is not None
