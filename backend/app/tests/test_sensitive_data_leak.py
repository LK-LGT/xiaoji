"""敏感信息泄漏扫描测试。

覆盖：
- 日志中不出现 App Secret / Access Token / Authorization 头
- 同步响应中不出现密钥
- 失败记录与审计表中不出现密钥
- 脱敏工具函数按预期工作
"""

from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    OrganizationSyncAudit,
    OrganizationSyncBatch,
    OrganizationSyncItemError,
)
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.organization_sync import OrganizationSyncService

# 用于测试的假密钥，不使用真实值
FAKE_APP_SECRET = "TEST_ONLY_app_secret_ZZZZ_should_never_appear"
FAKE_ACCESS_TOKEN = "TEST_ONLY_tenant_access_token_YYYY_should_never_appear"


def _fresh_db():
    """创建一个干净的内存数据库会话（不依赖 pytest fixture）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Department(id="D1", name="研发部", active=True))
    session.add(
        Employee(
            id="EMP001",
            name="员工",
            login_account="emp001",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    session.commit()
    yield session
    session.close()
    engine.dispose()


class _BoomFeishu:
    """模拟飞书客户端：所有方法抛出含密钥的异常。"""

    def fetch_departments(self, *args, **kwargs):
        raise RuntimeError(
            f"request failed app_secret={FAKE_APP_SECRET} "
            f"tenant_access_token={FAKE_ACCESS_TOKEN} "
            f"Authorization: Bearer {FAKE_ACCESS_TOKEN}"
        )

    def fetch_employees(self, *args, **kwargs):
        raise RuntimeError(
            f"users failed app_secret={FAKE_APP_SECRET} "
            f"tenant_access_token={FAKE_ACCESS_TOKEN}"
        )

    def fetch_employee(self, *args, **kwargs):
        raise RuntimeError(f"user failed app_secret={FAKE_APP_SECRET}")

    def close(self):
        pass


# ──────────────────────────────────────────────────────────────
# 脱敏工具函数
# ──────────────────────────────────────────────────────────────


def test_safe_error_hides_sensitive_text():
    """含密钥的异常消息必须被整体隐藏。"""
    exc = RuntimeError(
        f"boom app_secret={FAKE_APP_SECRET} tenant_access_token={FAKE_ACCESS_TOKEN}"
    )
    message = OrganizationSyncService._safe_error(exc)
    assert FAKE_APP_SECRET not in message
    assert FAKE_ACCESS_TOKEN not in message
    assert "敏感信息已隐藏" in message


def test_safe_error_preserves_normal_message():
    """普通错误消息应保留（便于排查）。"""
    exc = RuntimeError("飞书接口返回 code=99991672")
    message = OrganizationSyncService._safe_error(exc)
    assert "99991672" in message


def test_sanitize_payload_masks_tokens():
    """payload 中的 token/secret 字段必须被剔除或掩码。"""
    payload = {
        "user_id": "u1",
        "name": "张三",
        "access_token": FAKE_ACCESS_TOKEN,
        "tenant_access_token": FAKE_ACCESS_TOKEN,
        "app_secret": FAKE_APP_SECRET,
        "authorization": f"Bearer {FAKE_ACCESS_TOKEN}",
    }
    sanitized = OrganizationSyncService._sanitize_payload(payload)
    dumped = json.dumps(sanitized, ensure_ascii=False)

    assert FAKE_ACCESS_TOKEN not in dumped
    assert FAKE_APP_SECRET not in dumped
    assert "authorization" not in {k.lower() for k in sanitized}
    # 非敏感字段保留
    assert sanitized["user_id"] == "u1"
    assert sanitized["name"] == "张三"


def test_sanitize_payload_handles_none():
    """None 输入返回 None。"""
    assert OrganizationSyncService._sanitize_payload(None) is None


# ──────────────────────────────────────────────────────────────
# 数据库中的失败记录与审计
# ──────────────────────────────────────────────────────────────


def test_error_message_sanitization_in_db(db):
    """同步失败后，批次错误信息中不得包含密钥。"""
    service = OrganizationSyncService(db, _BoomFeishu(), triggered_by="EMP001")
    result = service.sync_all()

    assert result["status"] == "failed"
    batch = db.get(OrganizationSyncBatch, result["batch_id"])
    assert batch is not None
    assert FAKE_APP_SECRET not in (batch.error_message or "")
    assert FAKE_ACCESS_TOKEN not in (batch.error_message or "")


def test_item_error_records_are_sanitized(db):
    """失败明细表中不得保存含密钥的消息。"""
    service = OrganizationSyncService(db, _BoomFeishu(), triggered_by="EMP001")
    service.sync_all()

    errors = db.query(OrganizationSyncItemError).all()
    for error in errors:
        assert FAKE_APP_SECRET not in (error.message or "")
        assert FAKE_ACCESS_TOKEN not in (error.message or "")
        if error.payload_json:
            dumped = json.dumps(error.payload_json, ensure_ascii=False)
            assert FAKE_APP_SECRET not in dumped
            assert FAKE_ACCESS_TOKEN not in dumped


def test_audit_detail_is_sanitized(db):
    """同步审计的 detail 中不得包含密钥。"""
    service = OrganizationSyncService(db, _BoomFeishu(), triggered_by="EMP001")
    service.sync_all()

    audits = db.query(OrganizationSyncAudit).all()
    for audit in audits:
        if audit.detail_json:
            dumped = json.dumps(audit.detail_json, ensure_ascii=False)
            assert FAKE_APP_SECRET not in dumped
            assert FAKE_ACCESS_TOKEN not in dumped


# ──────────────────────────────────────────────────────────────
# 日志
# ──────────────────────────────────────────────────────────────
#
# 注意：这些测试不能直接 logger.warning(secret) 再断言日志里没有 secret ——
# 那样断言的其实是"日志功能坏了"。正确做法是驱动真实代码路径，
# 然后断言该路径没有把密钥写进日志。


def test_sync_failure_does_not_log_secret(caplog):
    """同步失败时，日志中不得出现 App Secret / Access Token。"""
    caplog.set_level(logging.DEBUG)
    db = _fresh_db()
    try:
        service = OrganizationSyncService(db, _BoomFeishu(), triggered_by="EMP001")
        service.sync_all()
    finally:
        db.close()

    assert FAKE_APP_SECRET not in caplog.text
    assert FAKE_ACCESS_TOKEN not in caplog.text
    assert "Bearer " not in caplog.text


def test_feishu_client_does_not_log_credentials(caplog):
    """飞书客户端请求失败时不得把凭据写进日志。"""
    caplog.set_level(logging.DEBUG)
    # 用 MockTransport 让 Token 接口返回 5xx，触发错误路径。
    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [
                {"status_code": 500, "json": {"code": 500, "msg": "server error"}},
            ]
        }
    )
    client = FeishuClient(
        app_id="cli_test",
        app_secret=FAKE_APP_SECRET,
        tenant_id="tenant_test",
        transport=transport,
        max_retries=0,
    )
    try:
        with pytest.raises(Exception):
            client.fetch_departments()
    finally:
        client.close()

    assert FAKE_APP_SECRET not in caplog.text
    assert FAKE_ACCESS_TOKEN not in caplog.text


def test_real_app_secret_not_in_logs(caplog):
    """真实配置的 App Secret 不得出现在日志中。"""
    if not settings.feishu_app_secret:
        pytest.skip("未配置飞书 App Secret，跳过")

    caplog.set_level(logging.DEBUG)
    db = _fresh_db()
    try:
        # 使用真实密钥构造客户端，但让它在网络层失败。
        transport = MockTransport(
            {
                "POST /auth/v3/tenant_access_token/internal": [
                    {"status_code": 500, "json": {"code": 500, "msg": "server error"}},
                ]
            }
        )
        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
            transport=transport,
            max_retries=0,
        )
        try:
            with pytest.raises(Exception):
                client.fetch_departments()
        finally:
            client.close()
    finally:
        db.close()

    assert settings.feishu_app_secret not in caplog.text
    assert settings.feishu_app_secret[:8] not in caplog.text


def test_real_auth_token_secret_not_in_logs(caplog):
    """真实配置的认证密钥不得出现在日志中。"""
    caplog.set_level(logging.DEBUG)
    from fastapi.testclient import TestClient

    from backend.app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    client.get("/v1/health")

    assert settings.auth_token_secret not in caplog.text
    assert settings.auth_token_secret[:8] not in caplog.text


# ──────────────────────────────────────────────────────────────
# HTTP 响应
# ──────────────────────────────────────────────────────────────


def test_health_response_contains_no_secrets():
    """健康检查响应中不得包含任何密钥。"""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/v1/health")
    body = resp.text

    if settings.feishu_app_secret:
        assert settings.feishu_app_secret not in body
    if settings.auth_token_secret:
        assert settings.auth_token_secret not in body
    assert "tenant_access_token" not in body


def test_responses_do_not_echo_credential_headers():
    """响应不得回显凭据相关响应头。"""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/v1/health")

    for header in (
        "X-Feishu-Secret",
        "X-Auth-Token",
        "X-Authorization",
        "X-Tenant-Token",
    ):
        assert header not in resp.headers


def test_real_feishu_verification_token_not_hardcoded_in_tests():
    """测试源码不得写死真实的飞书 Verification Token。

    ⚠️ 2026-11-27：`test_feishu_event.py` 曾把本地 `.env` 的真实 Token
    抄进常量并进入 git 历史 ⇒ 轮换 Token 后，用这条护栏防止再次发生。
    """
    from pathlib import Path

    real_tokens = {
        item
        for item in (
            settings.feishu_approval_webhook_token,
            settings.feishu_contact_event_token,
        )
        if item
    }
    if not real_tokens:
        pytest.skip("未配置飞书 Verification Token，跳过（CI 无 .env）")

    tests_dir = Path(__file__).resolve().parent
    offenders: list[str] = []
    for path in tests_dir.glob("*.py"):
        body = path.read_text(encoding="utf-8", errors="ignore")
        if any(token in body for token in real_tokens):
            offenders.append(path.name)
    assert not offenders, f"测试文件中出现了真实飞书 Token：{offenders}"
