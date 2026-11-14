"""第 2 阶段飞书组织同步与身份映射测试。

覆盖：限流重试、Token 失效刷新、单条失败不阻断批次、幂等、离职禁用、
身份唯一性、冲突检测、审计和敏感信息不泄露。
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    FeishuIdentity,
    FeishuIdentityAudit,
    OrganizationSyncItemError,
)
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.feishu_identity_service import FeishuIdentityService, IdentityConflictError
from backend.app.services.organization_sync import OrganizationSyncService


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


def test_rate_limit_is_retried(db):
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "GET /contact/v3/departments/0/children": [
                {"status_code": 429, "headers": {"Retry-After": "0"}},
                {"status_code": 200, "json": {"code": 0, "data": {"items": []}}},
            ],
        }
    )
    delays: list[float] = []
    client = FeishuClient(
        "app", "secret", transport=transport, sleep=delays.append
    )
    assert client.fetch_departments() == []
    assert delays == [0.0]
    assert len(transport.calls) == 3


def test_invalid_token_refreshes_once(db):
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                token_response("old"),
                token_response("new"),
            ],
            "GET /contact/v3/departments/0/children": [
                {
                    "status_code": 200,
                    "json": {"code": 99991663, "msg": "invalid token"},
                },
                {
                    "status_code": 200,
                    "json": {"code": 0, "data": {"items": [{"department_id": "d1"}]}},
                },
            ],
        }
    )
    client = FeishuClient("app", "secret", transport=transport, sleep=lambda _: None)
    assert client.fetch_departments()[0]["department_id"] == "d1"
    token_calls = [
        call for call in transport.calls
        if call.url.path.endswith("/auth/v3/tenant_access_token/internal")
    ]
    assert len(token_calls) == 2


def test_sync_is_idempotent_and_counts_items(db):
    client = _mock_client()
    service = OrganizationSyncService(db, client, triggered_by="admin")
    first = service.sync_all()
    second = OrganizationSyncService(db, client, triggered_by="admin").sync_all()
    assert first["departments_synced"] == 1
    assert first["employees_synced"] == 1
    assert second["departments_synced"] == 0
    assert second["employees_synced"] == 0
    assert db.query(Employee).count() == 1
    assert db.query(FeishuIdentity).count() == 3


def test_single_item_failure_does_not_break_batch(db):
    client = _mock_client(employee_name=None)
    service = OrganizationSyncService(db, client, triggered_by="admin")
    result = service.sync_all()
    assert result["employees_failed"] == 1
    assert result["departments_synced"] == 1
    assert db.query(OrganizationSyncItemError).count() == 1


def test_exited_employee_disables_account(db):
    client = _mock_client(exited=True)
    result = OrganizationSyncService(db, client, triggered_by="admin").sync_all()
    employee = db.query(Employee).one()
    assert result["employees_synced"] == 1
    assert employee.employment_status == "exited"
    assert employee.account_status == "disabled"
    assert employee.token_version == 2


def test_identity_unique_and_conflict_detection(db):
    employee = Employee(id="E1", name="张三")
    db.add(employee)
    db.add(Employee(id="E2", name="李四"))
    db.commit()
    service = FeishuIdentityService(db)
    service.bind(
        employee_id="E1",
        identity_type="user_id",
        identity_value="u1",
        actor_id="admin",
        reason="人工绑定",
    )
    db.commit()
    with pytest.raises(IdentityConflictError):
        service.bind(
            employee_id="E2",
            identity_type="user_id",
            identity_value="u1",
            actor_id="admin",
            reason="重复绑定",
        )
    assert len(service.detect_conflicts()) == 0
    assert db.query(FeishuIdentityAudit).count() == 1


def test_unbound_employee_list_and_disable_account(db):
    employee = Employee(
        id="E1",
        name="张三",
        feishu_user_id="u1",
        feishu_open_id="o1",
        feishu_union_id="un1",
    )
    db.add(employee)
    db.commit()
    service = FeishuIdentityService(db)
    assert len(service.list_unbound()) == 1
    service.disable_account(employee_id="E1", actor_id="admin", reason="离职")
    db.commit()
    assert employee.account_status == "disabled"
    assert employee.token_version == 2


# ──────────────────────────────────────────────────────────────
# 飞书错误码语义化
# ──────────────────────────────────────────────────────────────


def test_describe_feishu_error_known_codes():
    """已知错误码应转换为可读语义。"""
    from backend.app.services.feishu_client import describe_feishu_error

    assert "版本未发布" in describe_feishu_error(99991672)
    assert "限流" in describe_feishu_error(1254290)
    assert "Token 无效" in describe_feishu_error(99991663)
    assert "可见范围" in describe_feishu_error(99991679)


def test_describe_feishu_error_unknown_code_keeps_code():
    """未知错误码应保留原始码，便于排查。"""
    from backend.app.services.feishu_client import describe_feishu_error

    message = describe_feishu_error(123456)
    assert "123456" in message


def test_describe_feishu_error_none_code():
    """无错误码时返回兜底消息。"""
    from backend.app.services.feishu_client import describe_feishu_error

    assert describe_feishu_error(None, "兜底消息") == "兜底消息"


def test_error_code_99991672_is_descriptive(db):
    """权限不足错误应给出可执行提示。"""
    from backend.app.services.feishu_client import FeishuError

    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "GET /contact/v3/departments/0/children": [
                {
                    "status_code": 200,
                    "json": {"code": 99991672, "msg": "permission denied"},
                },
            ],
        }
    )
    client = FeishuClient("app", "secret", transport=transport, sleep=lambda _: None)
    with pytest.raises(FeishuError) as exc_info:
        client.fetch_departments()
    assert "版本未发布" in str(exc_info.value)
    assert exc_info.value.code == 99991672


def test_rate_limit_error_is_retryable(db):
    """限流错误应标记为可重试。"""
    from backend.app.services.feishu_client import FeishuError

    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [token_response()],
            "GET /contact/v3/departments/0/children": [
                {
                    "status_code": 200,
                    "json": {"code": 1254290, "msg": "rate limited"},
                },
            ],
        }
    )
    client = FeishuClient("app", "secret", transport=transport, sleep=lambda _: None)
    with pytest.raises(FeishuError) as exc_info:
        client.fetch_departments()
    assert exc_info.value.retryable is True
    assert "限流" in str(exc_info.value)


def test_token_invalid_error_triggers_refresh(db):
    """Token 失效错误应触发一次刷新后重放。"""
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                token_response("old"),
                token_response("new"),
            ],
            "GET /contact/v3/departments/0/children": [
                {
                    "status_code": 200,
                    "json": {"code": 99991663, "msg": "invalid token"},
                },
                {
                    "status_code": 200,
                    "json": {"code": 0, "data": {"items": []}},
                },
            ],
        }
    )
    client = FeishuClient("app", "secret", transport=transport, sleep=lambda _: None)
    assert client.fetch_departments() == []
    # 两次 Token 获取 + 两次业务请求
    assert len(transport.calls) == 4


def _mock_client(
    *, employee_name: str | None = "张三", exited: bool = False
) -> FeishuClient:
    employee = {
        "user_id": "u1",
        "open_id": "o1",
        "union_id": "un1",
        "name": employee_name,
        "department_ids": ["d1"],
        "leader_user_id": None,
        "email": "zhang@example.com",
        "status": {"is_activated": True, "is_exited": exited},
    }
    if employee_name is None:
        # 缺失必填字段用于验证单条失败隔离。
        employee.pop("name")
        employee["invalid"] = True
    transport = MockTransport(
        {
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
            "GET /contact/v3/users": [
                {
                    "status_code": 200,
                    "json": {"code": 0, "data": {"items": [employee]}},
                }
            ],
        }
    )
    return FeishuClient(
        "app", "secret", transport=transport, sleep=lambda _: None
    )
