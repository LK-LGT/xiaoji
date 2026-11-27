"""运行时模型配置端点（2026-11-27）。

业务层启用 AI 配置后，通过本端点把模型切过去，无需重启 agent-service。
⚠️ 响应/日志绝不包含 API Key。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

AGENT = Path(__file__).resolve().parents[1] / "agent-service"
sys.path.insert(0, str(AGENT))

# isort: split

from tests._test_secret import AUTH_HEADERS as AUTH  # noqa: E402

# isort: split

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_to_echo():
    yield
    client.post(
        "/v1/agent/runtime-config",
        json={"mode": "echo"},
        headers=AUTH,
    )


def test_未带令牌被拒():
    assert client.get("/v1/agent/runtime-config").status_code == 401


def test_切换到echo并返回状态():
    resp = client.post(
        "/v1/agent/runtime-config",
        json={"mode": "echo"},
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "echo"
    assert data["model"] == "echo-deterministic"
    assert "api_key" not in data

    current = client.get("/v1/agent/runtime-config", headers=AUTH).json()
    assert current["mode"] == "echo"


def test_openai缺字段返回422():
    resp = client.post(
        "/v1/agent/runtime-config",
        json={"mode": "openai", "base_url": "https://example.com/v1"},
        headers=AUTH,
    )
    assert resp.status_code == 422, resp.text


def test_openai配置立即生效但不回传Key():
    resp = client.post(
        "/v1/agent/runtime-config",
        json={
            "mode": "openai",
            "base_url": "https://example.com/v1",
            "model": "test-model",
            "api_key": "sk-secret-not-returned",
        },
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "sk-secret-not-returned" not in body
    assert resp.json()["model"] == "test-model"

    health = client.get("/health").json()
    assert health["model"] == "test-model"
