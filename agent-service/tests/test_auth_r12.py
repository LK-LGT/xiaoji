"""R12 —— 智能体服务自己的鉴权回归测试。

放在 agent-service 侧（而不是只在 backend 侧）——
**服务的归属方应该守自己的门**。
backend 侧那份是「跨服务契约」视角，这里是「服务自身」视角。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

AGENT = Path(__file__).resolve().parents[1] / "agent-service"
sys.path.insert(0, str(AGENT))

# ⚠️ secret 收敛到一处，避免「谁先导入谁定值」导致莫名 401。
#
# ⚠️⚠️ **导入顺序是硬约束，别让 ruff 的 I001 自动排序改掉！**
#
#     ``app/main.py`` 在**模块导入时**读取并校验环境变量：
#         _SERVICE_TOKEN = os.getenv("AGENT_SHARED_SECRET", "")   ← 导入即读
#         _require_startup_secret()                              ← 导入即校验
#
#     而 ``tests/_test_secret.py`` 负责 ``os.environ.setdefault(...)``。
#     所以**必须先导入 _test_secret，再导入 app.main**。
#     本文件按字母序**排在 test_draft_api.py 之前**，也就是说
#     **本文件是第一个导入 app.main 的** —— 顺序反了就会把 secret 定错。
#
#     下面的 ``# isort: split`` 给 ruff I001 立界碑：排序器不得跨越它重排。
# isort: split

from tests._test_secret import AUTH_HEADERS as AUTH  # noqa: E402

# isort: split

from app.main import app  # noqa: E402

client = TestClient(app)


def _draft_body() -> dict:
    return {
        "task_id": "T-2026-10-运营部",
        "employee": {"alias": "该员工", "role_title": "运营专员"},
        "period": "2026-10",
        "metrics": [
            {"code": "gmv", "dimension": "GMV", "value": 100.0, "target": 100.0}
        ],
        "evidence": [],
        "attendance_anomalies": [],
        "rubric": [],
    }


def test_draft_requires_token():
    """未带令牌 → 401。"""
    resp = client.post("/v1/agent/draft", json=_draft_body())
    assert resp.status_code == 401


def test_draft_with_token_succeeds():
    """带正确令牌 → 200。"""
    resp = client.post("/v1/agent/draft", json=_draft_body(), headers=AUTH)
    assert resp.status_code == 200, resp.text


def test_reload_requires_token():
    """⚠️ ``/v1/agent/reload`` 原本**完全没有鉴权** —— 现在必须有。

    它能切换后端模型，等价于控制面权限。
    """
    resp = client.post("/v1/agent/reload")
    assert resp.status_code == 401


def test_reload_with_token_succeeds():
    resp = client.post("/v1/agent/reload", headers=AUTH)
    assert resp.status_code == 200, resp.text


def test_health_is_public():
    """``/health`` 保持公开（探针不该要令牌）。"""
    resp = client.get("/health")
    assert resp.status_code == 200


def test_wrong_token_rejected():
    resp = client.post(
        "/v1/agent/draft", json=_draft_body(), headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401


def test_auth_precedes_body_validation():
    """鉴权先于请求体校验 —— 未认证方探不到端点结构。"""
    resp = client.post("/v1/agent/draft", json={"bogus": True})
    assert resp.status_code == 401
    assert "task_id" not in resp.text
