"""飞书事件解密 / 验签 / 归一化测试（A8 补强）。

覆盖三条真实链路：

1. **加密模式** —— 飞书推 ``{"encrypt": "..."}``，需验签 + 解密
2. **明文模式** —— 飞书推 v2.0 信封，比对 Verification Token
3. **握手** —— ``url_verification`` 需在 1 秒内回显 challenge

外加事件归一化的版本兼容（v2.0 / v1.0 / 扁平）。
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import PlanStatus
from backend.app.domain.models import Base, Department, Employee, MonthPlan, SystemAdmin
from backend.app.main import app
from backend.app.services import feishu_binding, feishu_event

WEBHOOK_URL = "/v1/feishu/approval/events"

# 官方回归向量（来自飞书文档的 Python 示例，逐字照抄）
OFFICIAL_VECTOR_KEY = "test key"
OFFICIAL_VECTOR_CIPHER = "P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk="
OFFICIAL_VECTOR_PLAIN = "hello world"

TEST_ENCRYPT_KEY = "0uBLCvvZA0gBTMYeM6HbZlbOg1HVFWxR"
# ⚠️ 2026-11-27：这里**绝不能**写真实 Verification Token。
#    历史版本曾把本地 `.env` 的真实 Token 抄进测试（并进入 git 历史）
#    ⇒ 轮换 Token 是主修复；本文件只保留**明显是假的**测试值。
TEST_VERIFICATION_TOKEN = "test-verification-token-not-real"


# ---------------------------------------------------------------------------
# 工具：加密一个事件（模拟飞书推送）
# ---------------------------------------------------------------------------


def encrypt_payload(plain_obj: dict, encrypt_key: str = TEST_ENCRYPT_KEY) -> str:
    """按飞书官方算法加密事件体。

    ``key = sha256(encrypt_key)``；``iv`` 为 16 字节随机数；
    明文 PKCS7 填充后 AES-256-CBC 加密；最终 ``base64(iv + 密文)``。
    """
    from Crypto.Cipher import AES

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    # 固定 IV 让测试可复现（生产环境飞书用随机 IV）
    iv = b"\x00" * 16
    data = json.dumps(plain_obj, ensure_ascii=False).encode("utf-8")
    pad_len = 16 - len(data) % 16
    data += bytes([pad_len]) * pad_len
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(iv + cipher.encrypt(data)).decode("utf-8")


def sign_request(encrypt_key: str, raw_body: bytes, timestamp: str, nonce: str) -> str:
    """按飞书官方算法算签名。"""
    b1 = f"{timestamp}{nonce}{encrypt_key}".encode()
    return hashlib.sha256(b1 + raw_body).hexdigest()


def real_feishu_v2_event(
    *,
    token: str = TEST_VERIFICATION_TOKEN,
    instance_id: str = "74333067777841234",
    status: str = "APPROVED",
) -> dict:
    """飞书真实 v2.0 审批事件（结构取自官方文档逐字模板）。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": "b53e80a132ec090ba249a8bb74da0123",
            "token": token,
            "event_type": "approval.approval.updated_v4",
            "app_id": "cli_xxx",
            "tenant_key": "d6ae59e175fxxx",
            "event_create_time": "1502199207.7171419",
        },
        "event": {
            "object": {
                "approval_id": instance_id,
                "approval_code": "5B43240B-848C-49BD-9D27-11EF0EE11234",
                "version_id": "7459343285667611234",
                "widget_group_type": 0,
                "form_definition_id": "7459341670005371234",
                "timestamp": "1736763698",
                "status": status,
                "extra": "",
            }
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
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
    dept = Department(id="D_1", name="业务部", active=True)
    db.add(dept)
    db.flush()
    emp = Employee(
        id="E_A",
        name="员工甲",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
        feishu_open_id="ou_emp_a",
        feishu_user_id="u_emp_a",
    )
    admin = Employee(
        id="E_C",
        name="总监丙",
        department_id="D_1",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([emp, admin])
    db.flush()
    db.add(SystemAdmin(employee_id="E_C", active=True))
    db.flush()
    return {"a": emp, "c": admin}


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def feishu_env():
    """配置飞书 webhook 凭据（加密模式）。"""
    old_token = settings.feishu_approval_webhook_token
    old_key = settings.feishu_encrypt_key
    settings.feishu_approval_webhook_token = TEST_VERIFICATION_TOKEN
    settings.feishu_encrypt_key = TEST_ENCRYPT_KEY
    yield
    settings.feishu_approval_webhook_token = old_token
    settings.feishu_encrypt_key = old_key


@pytest.fixture()
def plaintext_env():
    """配置飞书 webhook 凭据（明文模式 —— 不配 Encrypt Key）。"""
    old_token = settings.feishu_approval_webhook_token
    old_key = settings.feishu_encrypt_key
    settings.feishu_approval_webhook_token = TEST_VERIFICATION_TOKEN
    settings.feishu_encrypt_key = ""
    yield
    settings.feishu_approval_webhook_token = old_token
    settings.feishu_encrypt_key = old_key


def _month_plan(db, owner_id="E_A"):
    plan = MonthPlan(
        owner_id=owner_id, plan_month="2026-11", title="十一月计划",
        status=PlanStatus.SUBMITTED.value,
    )
    db.add(plan)
    db.flush()
    return plan


# ---------------------------------------------------------------------------
# 1. 解密：官方回归向量
# ---------------------------------------------------------------------------


def test_official_regression_vector():
    """官方文档给的回归向量必须能解出原文。"""
    plain = feishu_event.decrypt_event(OFFICIAL_VECTOR_KEY, OFFICIAL_VECTOR_CIPHER)
    assert plain == OFFICIAL_VECTOR_PLAIN


def test_decrypt_real_event():
    """解密自己加密的真实 v2.0 事件。"""
    payload = real_feishu_v2_event()
    encrypted = encrypt_payload(payload)
    plain = feishu_event.decrypt_event(TEST_ENCRYPT_KEY, encrypted)
    assert json.loads(plain)["header"]["event_type"] == "approval.approval.updated_v4"


def test_decrypt_with_wrong_key_raises():
    """密钥不对 → 报错，不返回垃圾明文。"""
    encrypted = encrypt_payload(real_feishu_v2_event())
    with pytest.raises(feishu_event.FeishuEventError):
        feishu_event.decrypt_event("wrong-key", encrypted)


def test_decrypt_with_invalid_base64_raises():
    """base64 非法 → 报错。"""
    with pytest.raises(feishu_event.FeishuEventError):
        feishu_event.decrypt_event(TEST_ENCRYPT_KEY, "!!!not-base64!!!")


def test_decrypt_with_short_cipher_raises():
    """密文过短 → 报错。"""
    with pytest.raises(feishu_event.FeishuEventError):
        feishu_event.decrypt_event(TEST_ENCRYPT_KEY, base64.b64encode(b"short").decode())


def test_decrypt_without_key_raises():
    """没配 Encrypt Key → 报错（不能猜）。"""
    with pytest.raises(feishu_event.FeishuEventError):
        feishu_event.decrypt_event("", "anything")


# ---------------------------------------------------------------------------
# 2. 验签
# ---------------------------------------------------------------------------


def test_verify_signature_valid():
    """正确签名 → 通过。"""
    raw = b'{"hello":"world"}'
    sig = sign_request(TEST_ENCRYPT_KEY, raw, "1700000000", "nonce123")
    assert feishu_event.verify_signature(
        encrypt_key=TEST_ENCRYPT_KEY,
        timestamp="1700000000",
        nonce="nonce123",
        signature=sig,
        raw_body=raw,
    )


def test_verify_signature_invalid():
    """错误签名 → 不通过。"""
    assert not feishu_event.verify_signature(
        encrypt_key=TEST_ENCRYPT_KEY,
        timestamp="1700000000",
        nonce="nonce123",
        signature="deadbeef",
        raw_body=b'{"hello":"world"}',
    )


def test_verify_signature_tampered_body_fails():
    """body 被篡改 → 签名不匹配（防重放/篡改）。"""
    raw = b'{"hello":"world"}'
    sig = sign_request(TEST_ENCRYPT_KEY, raw, "1700000000", "nonce123")
    assert not feishu_event.verify_signature(
        encrypt_key=TEST_ENCRYPT_KEY,
        timestamp="1700000000",
        nonce="nonce123",
        signature=sig,
        raw_body=b'{"hello":"evil"}',
    )


def test_verify_signature_missing_headers_fails():
    """缺请求头 → 不通过。"""
    assert not feishu_event.verify_signature(
        encrypt_key=TEST_ENCRYPT_KEY,
        timestamp="",
        nonce="",
        signature="x",
        raw_body=b"{}",
    )


# ---------------------------------------------------------------------------
# 3. 事件归一化
# ---------------------------------------------------------------------------


def test_parse_v2_event():
    """v2.0 信封 → 正确抽出实例 ID / 状态 / token。"""
    event = feishu_event.parse_event(real_feishu_v2_event())
    assert event.token == TEST_VERIFICATION_TOKEN
    assert event.instance_code == "74333067777841234"
    assert event.instance_status == "APPROVED"
    assert event.approval_code == "5B43240B-848C-49BD-9D27-11EF0EE11234"
    assert event.event_type == "approval.approval.updated_v4"
    assert event.schema_version == "2.0"


def test_parse_v2_event_status_from_process_obj():
    """顶层没有 status 时，从 process_obj（JSON 字符串）里取。"""
    payload = real_feishu_v2_event()
    payload["event"]["object"].pop("status")
    payload["event"]["object"]["process_obj"] = json.dumps({"status": "REJECTED"})
    event = feishu_event.parse_event(payload)
    assert event.instance_status == "REJECTED"


def test_parse_v1_event():
    """v1.0 信封（顶层 token + event 内实例）→ 也能解析。"""
    payload = {
        "ts": "1502199207.7171419",
        "uuid": "bc447199585340d1f3728d26b1c0297a",
        "token": TEST_VERIFICATION_TOKEN,
        "type": "event_callback",
        "event": {
            "type": "approval.approval.updated_v4",
            "app_id": "cli_9c8609450f78d102",
            "instance_code": "inst_legacy_1",
            "instance_status": "APPROVED",
        },
    }
    event = feishu_event.parse_event(payload)
    assert event.token == TEST_VERIFICATION_TOKEN
    assert event.instance_code == "inst_legacy_1"
    assert event.instance_status == "APPROVED"


def test_parse_flat_format():
    """扁平格式（早期测试用）→ 仍兼容。"""
    payload = {
        "token": TEST_VERIFICATION_TOKEN,
        "event_id": "evt_1",
        "instance_code": "inst_flat",
        "instance_status": "approved",
        "plan_type": "month",
        "plan_id": "PLAN_1",
        "correlation_key": "PPAB12CD34",
    }
    event = feishu_event.parse_event(payload)
    assert event.instance_code == "inst_flat"
    assert event.instance_status == "APPROVED"
    assert event.plan_type == "month"
    assert event.plan_id == "PLAN_1"
    assert event.correlation_key == "PPAB12CD34"


def test_parse_empty_body_raises():
    """非 dict → 报错。"""
    with pytest.raises(feishu_event.FeishuEventError):
        feishu_event.parse_event(["not", "a", "dict"])


def test_is_url_verification():
    """识别握手请求。"""
    assert feishu_event.is_url_verification(
        {"challenge": "abc", "type": "url_verification", "token": "x"}
    )
    assert feishu_event.is_url_verification({"challenge": "abc"})
    assert not feishu_event.is_url_verification(
        {"schema": "2.0", "header": {"event_id": "x"}}
    )


# ---------------------------------------------------------------------------
# 4. Webhook：加密模式端到端
# ---------------------------------------------------------------------------


def test_webhook_accepts_encrypted_event(db, client, feishu_env):
    """加密事件 → 验签 + 解密 → 正常处理。"""
    plan = _month_plan(db)
    # 飞书实例 ID 与本系统计划 ID 不同 —— 靠绑定表关联
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_feishu_001"
    )
    payload = real_feishu_v2_event(instance_id="inst_feishu_001")
    encrypted = encrypt_payload(payload)
    raw = json.dumps({"encrypt": encrypted}).encode("utf-8")

    ts, nonce = "1700000000", "n1"
    sig = sign_request(TEST_ENCRYPT_KEY, raw, ts, nonce)

    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={
            "Content-Type": "application/json",
            feishu_event.HEADER_TIMESTAMP: ts,
            feishu_event.HEADER_NONCE: nonce,
            feishu_event.HEADER_SIGNATURE: sig,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["result"] == "applied"
    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.APPROVED.value


def test_webhook_rejects_bad_signature(db, client, feishu_env):
    """签名错误 → 401，绝不放行。"""
    plan = _month_plan(db)
    payload = real_feishu_v2_event(instance_id=plan.id)
    raw = json.dumps({"encrypt": encrypt_payload(payload)}).encode("utf-8")

    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={
            "Content-Type": "application/json",
            feishu_event.HEADER_TIMESTAMP: "1700000000",
            feishu_event.HEADER_NONCE: "n1",
            feishu_event.HEADER_SIGNATURE: "invalid-signature",
        },
    )
    assert r.status_code == 401
    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.SUBMITTED.value


def test_webhook_rejects_encrypted_without_encrypt_key(db, client, plaintext_env):
    """收到密文但没配 Encrypt Key → 503，不猜测解密。"""
    raw = json.dumps({"encrypt": encrypt_payload(real_feishu_v2_event())}).encode("utf-8")
    r = client.post(WEBHOOK_URL, content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 503


def test_webhook_rejects_encrypted_with_bad_ciphertext(db, client, feishu_env):
    """密文解不开 → 400。"""
    raw = json.dumps({"encrypt": "!!!bad-base64!!!"}).encode("utf-8")
    ts, nonce = "1700000000", "n1"
    sig = sign_request(TEST_ENCRYPT_KEY, raw, ts, nonce)
    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={
            "Content-Type": "application/json",
            feishu_event.HEADER_TIMESTAMP: ts,
            feishu_event.HEADER_NONCE: nonce,
            feishu_event.HEADER_SIGNATURE: sig,
        },
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 5. Webhook：明文模式
# ---------------------------------------------------------------------------


def test_webhook_accepts_plain_v2_event(db, client, plaintext_env):
    """明文 v2.0 信封 → 能正常处理（这就是修复前 401 的场景）。"""
    plan = _month_plan(db)
    feishu_binding.bind_plan_to_instance(
        db, plan_type="month", plan_id=plan.id, instance_code="inst_feishu_002"
    )
    payload = real_feishu_v2_event(instance_id="inst_feishu_002")

    r = client.post(WEBHOOK_URL, json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["result"] == "applied"
    db.expire_all()
    assert db.get(MonthPlan, plan.id).status == PlanStatus.APPROVED.value


def test_webhook_plain_rejects_wrong_token(db, client, plaintext_env):
    """明文模式 token 不对 → 401。"""
    r = client.post(
        WEBHOOK_URL, json=real_feishu_v2_event(token="wrong-token")
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 6. Webhook：握手
# ---------------------------------------------------------------------------


def test_webhook_handles_url_verification(client, plaintext_env):
    """握手请求 → 原样返回 challenge（飞书保存地址的前提）。"""
    r = client.post(
        WEBHOOK_URL,
        json={
            "challenge": "ajls384kdj1234",
            "type": "url_verification",
            "token": TEST_VERIFICATION_TOKEN,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"challenge": "ajls384kdj1234"}


def test_webhook_handles_encrypted_url_verification(client, feishu_env):
    """加密模式下的握手 → 先解密再取 challenge。"""
    payload = {
        "challenge": "ajls384kdj1234",
        "type": "url_verification",
        "token": TEST_VERIFICATION_TOKEN,
    }
    raw = json.dumps({"encrypt": encrypt_payload(payload)}).encode("utf-8")
    ts, nonce = "1700000000", "n1"
    sig = sign_request(TEST_ENCRYPT_KEY, raw, ts, nonce)

    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={
            "Content-Type": "application/json",
            feishu_event.HEADER_TIMESTAMP: ts,
            feishu_event.HEADER_NONCE: nonce,
            feishu_event.HEADER_SIGNATURE: sig,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"challenge": "ajls384kdj1234"}


def test_webhook_still_503_when_token_not_configured(client):
    """未配置 Verification Token → 503（不放行）。"""
    old = settings.feishu_approval_webhook_token
    settings.feishu_approval_webhook_token = ""
    try:
        r = client.post(WEBHOOK_URL, json={"token": "x"})
        assert r.status_code == 503
    finally:
        settings.feishu_approval_webhook_token = old
