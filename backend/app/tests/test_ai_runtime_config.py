"""AI 模型配置「真实生效」回归（2026-11-27）。

- test_config 必须真的发 HTTP 请求；
- enable_config 必须把配置推给 agent-service，成功后才改数据库状态；
- 失败时不能出现「页面已启用、实际没切」的假成功。
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.domain.models import AiModelConfigRow, Base
from backend.app.services import ai_model_config_service, secret_box


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def config_row(db):
    row = AiModelConfigRow(
        config_name="test",
        provider_code="OPENAI_COMPATIBLE",
        base_url="https://example.com/v1",
        model_name="test-model",
        api_key_enc=secret_box.encrypt_secret("sk-test-secret"),
        status="DISABLED",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_测试连接真的发请求(db, config_row, monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = ai_model_config_service.test_config(db, config_row.id)
    assert result["success"] is True
    assert result["latencyMs"] >= 0
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["json"]["model"] == "test-model"


def test_启用会推送到agent_service(db, config_row, monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return httpx.Response(200, json={"status": "applied", "mode": "openai"})

    monkeypatch.setattr(settings, "agent_service_url", "http://agent.test:8800")
    monkeypatch.setattr(settings, "agent_shared_secret", "test-secret")
    monkeypatch.setattr(httpx, "post", fake_post)

    result = ai_model_config_service.enable_config(db, config_row.id)
    assert result is not None
    assert captured["url"] == "http://agent.test:8800/v1/agent/runtime-config"
    assert captured["json"]["mode"] == "openai"
    assert captured["json"]["api_key"] == "sk-test-secret"
    assert captured["json"]["model"] == "test-model"
    assert db.get(AiModelConfigRow, config_row.id).status == "ENABLED"


def test_agent_service拒绝时状态不变(db, config_row, monkeypatch):
    monkeypatch.setattr(settings, "agent_service_url", "http://agent.test:8800")
    monkeypatch.setattr(settings, "agent_shared_secret", "test-secret")
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: httpx.Response(422, text="bad config")
    )

    with pytest.raises(ai_model_config_service.AgentRuntimeError):
        ai_model_config_service.enable_config(db, config_row.id)
    assert db.get(AiModelConfigRow, config_row.id).status == "DISABLED"
