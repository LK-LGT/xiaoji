"""飞书组织同步接口的 API 级测试。

覆盖异步触发、进度查询、状态查询与并发互斥。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    OrganizationSyncBatch,
    SystemAdmin,
)
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    db = TestingSessionLocal()

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

    token, _refresh = _generate_token("ADMIN001", "admin001")
    emp_token, _emp_refresh = _generate_token("EMP001", "emp001")

    def override_get_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        yield client, db, headers, {"Authorization": f"Bearer {emp_token}"}
    app.dependency_overrides.clear()


def test_trigger_sync_requires_admin(client_and_db):
    """非管理员不得触发同步。"""
    client, _db, _headers, emp_headers = client_and_db
    response = client.post("/v1/feishu/sync-organization", json={"mode": "full"}, headers=emp_headers)
    assert response.status_code == 403


def test_trigger_sync_requires_auth(client_and_db):
    """未认证不得触发同步。"""
    client, _db, _headers, _emp = client_and_db
    response = client.post("/v1/feishu/sync-organization", json={"mode": "full"})
    assert response.status_code == 401


def test_trigger_sync_returns_batch_id(client_and_db, monkeypatch):
    """触发同步应立即返回批次 ID（异步执行）。"""
    client, db, headers, _emp = client_and_db

    # 避免真实调用飞书：替换任务启动入口，只验证契约。
    import backend.app.routes.feishu as feishu_route

    monkeypatch.setattr(
        feishu_route,
        "start_async_sync",
        lambda _db, **kwargs: (
            OrganizationSyncBatch(
                batch_no="ORG-TEST",
                status="pending",
                mode=kwargs.get("mode", "full"),
                trigger_type=kwargs.get("trigger_type", "manual"),
                triggered_by=kwargs.get("actor_id"),
            ),
            True,
        ),
    )

    response = client.post("/v1/feishu/sync-organization", json={"mode": "full"}, headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["mode"] == "full"
    assert payload["data"]["created"] is True
    assert "batchId" in payload["data"]


def test_get_sync_status_endpoint(client_and_db):
    """状态接口应返回链路状态。"""
    client, _db, headers, _emp = client_and_db
    response = client.get("/v1/feishu/sync-organization/status", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert "configured" in data
    assert "scheduledEnabled" in data
    assert "cursors" in data


def test_get_batch_detail_returns_404_for_unknown(client_and_db):
    """查询不存在的批次应返回 404。"""
    client, _db, headers, _emp = client_and_db
    response = client.get("/v1/feishu/sync-organization/batches/nope", headers=headers)
    assert response.status_code == 404


def test_get_batch_detail_returns_camel_case(client_and_db):
    """批次详情应使用 camelCase 契约。"""
    client, db, headers, _emp = client_and_db
    batch = OrganizationSyncBatch(
        batch_no="ORG-CAMEL",
        status="ok",
        mode="full",
        trigger_type="manual",
        departments_synced=3,
        employees_synced=5,
    )
    db.add(batch)
    db.commit()

    response = client.get(f"/v1/feishu/sync-organization/batches/{batch.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["batchNo"] == "ORG-CAMEL"
    assert data["departmentsSynced"] == 3
    assert data["employeesSynced"] == 5
    assert data["errorCategoryInfo"]["autoRetryable"] is False


def test_list_batches_supports_filter_and_pagination(client_and_db):
    """批次列表应支持过滤与分页。"""
    client, db, headers, _emp = client_and_db
    db.add(OrganizationSyncBatch(batch_no="ORG-A", status="ok", mode="full", trigger_type="manual"))
    db.add(OrganizationSyncBatch(batch_no="ORG-B", status="failed", mode="full", trigger_type="manual"))
    db.commit()

    response = client.get(
        "/v1/feishu/sync-organization/batches",
        params={"status": "failed", "pageNo": 1, "pageSize": 10},
        headers=headers,
    )
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["batchNo"] == "ORG-B"
    assert data["pageSize"] == 10


def test_list_errors_supports_category_filter(client_and_db):
    """失败记录应支持按分类过滤。"""
    from backend.app.domain.models import OrganizationSyncItemError

    client, db, headers, _emp = client_and_db
    batch = OrganizationSyncBatch(batch_no="ORG-ERR", status="partial_failed", mode="full", trigger_type="manual")
    db.add(batch)
    db.commit()
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u1",
            message="限流",
            error_category="retryable",
            status="pending",
        )
    )
    db.add(
        OrganizationSyncItemError(
            batch_id=batch.id,
            resource_type="employee",
            resource_id="u2",
            message="权限不足",
            error_category="config",
            status="dead_letter",
        )
    )
    db.commit()

    response = client.get(
        "/v1/feishu/sync-organization/errors",
        params={"errorCategory": "retryable"},
        headers=headers,
    )
    data = response.json()["data"]
    assert data["total"] == 1
    item = data["records"][0]
    assert item["resourceId"] == "u1"
    assert item["errorCategoryInfo"]["autoRetryable"] is True


def test_list_cursors_endpoint(client_and_db):
    """游标接口应返回游标列表。"""
    client, _db, headers, _emp = client_and_db
    response = client.get("/v1/feishu/sync-organization/cursors", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


def test_latest_reconciliation_returns_null_when_absent(client_and_db):
    """没有对账记录时应返回 null 而不是报错。"""
    client, _db, headers, _emp = client_and_db
    response = client.get("/v1/feishu/sync-organization/reconciliation/latest", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] is None


def test_latest_reconciliation_returns_payload(client_and_db):
    """存在对账记录时应返回对账内容，供前端渲染数据质量卡片。"""
    client, db, headers, _emp = client_and_db
    batch = OrganizationSyncBatch(
        batch_no="ORG-RECON",
        status="ok",
        mode="full",
        trigger_type="manual",
        reconciliation_json={
            "remote_departments": 3,
            "local_departments": 3,
            "missing_local_departments": [],
            "stale_local_departments": [],
            "remote_employees": 10,
            "local_employees": 10,
            "missing_local_employees": [],
            "stale_local_employees": [],
            "active_remote_employees": 9,
            "active_local_employees": 9,
            "active_count_mismatch": False,
            "unbound_count": 1,
            "conflict_count": 0,
            "conflicts": [],
            "snapshot_complete": True,
            "failed_departments": [],
            "data_quality": {
                "no_department_count": 1,
                "no_manager_count": 0,
                "no_login_account_count": 2,
                "cyclic_manager_count": 0,
            },
        },
    )
    db.add(batch)
    db.commit()

    response = client.get("/v1/feishu/sync-organization/reconciliation/latest", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["batchNo"] == "ORG-RECON"
    recon = data["reconciliation"]
    assert recon["snapshot_complete"] is True
    assert recon["data_quality"]["no_department_count"] == 1


def test_list_audits_supports_camel_case_params(client_and_db):
    """审计接口应同时接受 camelCase 与 snake_case 查询参数。"""
    from backend.app.domain.models import OrganizationSyncAudit

    client, db, headers, _emp = client_and_db
    batch = OrganizationSyncBatch(batch_no="ORG-AUDIT", status="ok", mode="full", trigger_type="manual")
    db.add(batch)
    db.commit()
    db.add(
        OrganizationSyncAudit(
            batch_id=batch.id,
            actor_id="ADMIN001",
            action="trigger_sync",
            resource_type="batch",
            resource_id=batch.id,
            result="SUCCESS",
            detail_json={"mode": "full"},
        )
    )
    db.commit()

    camel = client.get(
        "/v1/feishu/sync-organization/audits",
        params={"batchId": batch.id, "actorId": "ADMIN001", "pageSize": 5},
        headers=headers,
    )
    assert camel.status_code == 200
    camel_data = camel.json()["data"]
    assert camel_data["total"] == 1
    assert camel_data["pageSize"] == 5
    assert camel_data["records"][0]["batchId"] == batch.id
    assert camel_data["records"][0]["actorId"] == "ADMIN001"

    snake = client.get(
        "/v1/feishu/sync-organization/audits",
        params={"batch_id": batch.id, "actor_id": "ADMIN001", "page_size": 5},
        headers=headers,
    )
    assert snake.status_code == 200
    assert snake.json()["data"]["total"] == 1


def test_list_batches_supports_camel_case_page_size(client_and_db):
    """批次列表的 pageSize 应同时支持 camelCase 与 snake_case。"""
    client, db, headers, _emp = client_and_db
    for index in range(5):
        db.add(OrganizationSyncBatch(batch_no=f"ORG-C{index}", status="ok", mode="full", trigger_type="manual"))
    db.commit()

    camel = client.get(
        "/v1/feishu/sync-organization/batches",
        params={"pageSize": 2},
        headers=headers,
    )
    assert camel.json()["data"]["pageSize"] == 2
    assert len(camel.json()["data"]["records"]) == 2

    snake = client.get(
        "/v1/feishu/sync-organization/batches",
        params={"page_size": 3},
        headers=headers,
    )
    assert snake.json()["data"]["pageSize"] == 3


def test_admin_login_exposes_feishu_sync_permission(client_and_db):
    """管理员登录后应拿到飞书同步管理权限，前端菜单才能显示。"""
    client, _db, _headers, _emp = client_and_db
    response = client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "system:feishu-sync:manage" in data["permissions"]
