"""工具凭据、网关拒绝路径、审计事务和脱敏回归。"""

import base64
import json
import runpy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, settings
from backend.app.core.db import get_db
from backend.app.domain.models import AiToolCall, Base, Employee
from backend.app.routes.agent_tools import agent_tool_ctx, router, write_tool_audit
from backend.app.services import agent_tool_token as tokens
from backend.app.services.privacy import redact_obj


@pytest.fixture(autouse=True)
def tool_secret(monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_secret", "tool-test-independent-secret-1234567890")


def issue(**overrides):
    return tokens.issue_agent_tool_token(**{
        "employee_id": "E1", "task_id": "T1", "allow": ["ping"], **overrides,
    })


def test_token_roundtrip_tampering_and_expiration(monkeypatch):
    monkeypatch.setattr(tokens.time, "time", lambda: 1000)
    token = issue(ttl=99999)
    payload = tokens.verify_agent_tool_token(token)
    assert payload.exp - payload.iat == 300
    with pytest.raises(tokens.AgentToolTokenError):
        tokens.verify_agent_tool_token(token + "x")
    monkeypatch.setattr(tokens.time, "time", lambda: 1300)
    with pytest.raises(tokens.AgentToolTokenExpired):
        tokens.verify_agent_tool_token(token)


@pytest.mark.parametrize("claim,value", [
    ("sub", ""), ("exp", "invalid"), ("iat", True), ("allow", "ping"),
    ("allow", [1]), ("scope", "all"), ("aud", "login"), ("exp", 999999999999),
])
def test_signed_malformed_claims_rejected(claim, value):
    encoded = issue().split(".")[0]
    raw = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    raw[claim] = value
    encoded = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")
    with pytest.raises(tokens.AgentToolTokenError):
        tokens.verify_agent_tool_token(f"{encoded}.{tokens._sign(encoded)}")


def test_secret_validation():
    with pytest.raises(ValueError):
        Settings(agent_tool_secret="secret")
    with pytest.raises(ValueError):
        Settings(agent_tool_secret=settings.auth_token_secret)
    with pytest.raises(ValueError):
        Settings(agent_tool_token_ttl_seconds=301)
    assert Settings(agent_tool_secret="").agent_tool_secret == ""


@pytest.fixture
def gateway():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Employee(id="E1", name="员工", employment_status="active", account_status="enabled"))
        db.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            yield client, db
    engine.dispose()


def test_gateway_auth_audit_and_revocation(gateway, monkeypatch):
    client, db = gateway
    assert client.post("/v1/agent-tools/ping").status_code == 401
    headers = {"Authorization": f"Bearer {issue()}"}
    assert client.post("/v1/agent-tools/ping", headers=headers).json()["data"]["ok"]
    audit = db.scalars(select(AiToolCall)).one()
    assert audit.ok and audit.args_keys == [] and len(audit.args_hash) == 64
    denied = {"Authorization": f"Bearer {issue(allow=['other'])}"}
    assert client.post("/v1/agent-tools/ping", headers=denied).status_code == 403
    assert db.scalars(select(AiToolCall).where(AiToolCall.ok.is_(False))).one().error == "TOOL_FORBIDDEN"
    employee = db.get(Employee, "E1")
    employee.account_status = "disabled"
    db.commit()
    assert client.post("/v1/agent-tools/ping", headers=headers).status_code == 403
    monkeypatch.setattr(settings, "agent_tool_secret", "")
    assert client.post("/v1/agent-tools/ping", headers=headers).status_code == 503


def test_audit_failure_preserves_outer_transaction(gateway):
    _, db = gateway
    ctx = agent_tool_ctx(authorization=f"Bearer {issue()}", db=db)
    db.add(Employee(id="E2", name="保留的业务变更"))
    db.flush()
    db.execute(text("DROP TABLE ai_tool_call"))
    write_tool_audit(db, ctx=ctx, tool="ping", args={}, ok=True, latency_ms=0)
    db.commit()
    assert db.get(Employee, "E2") is not None
    assert db.execute(text("SELECT 1")).scalar_one() == 1


def test_privacy_parity_with_l3():
    root = Path(__file__).resolve().parents[3]
    reference = runpy.run_path(str(root / "agent-service/app/grounding.py"))
    sample = {
        "contact_email": "person@example.com", "employeeNo": "E001",
        "rows": ["13812345678 person@example.com 110101199001011234 员工编号:E001"],
    }
    assert redact_obj(sample) == reference["redact_obj"](sample)
    assert redact_obj(sample)["contact_email"] == "***"
    assert "13812345678" not in str(redact_obj(sample))
