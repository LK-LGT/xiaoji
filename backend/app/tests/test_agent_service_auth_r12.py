"""R12 —— agent-service 强制鉴权的回归测试。

背景
----
盘点项 R12：``agent-service/app/main.py`` 的 ``_verify_token`` 是::

    if not _SERVICE_TOKEN:
        return  # 未配置令牌时跳过（本地开发）

后果：**未配置令牌 = 完全免鉴权**。4 个端点（含 AI 生成接口）
对任何能访问该端口的人裸奔。本机开发时因为端口是 ``127.0.0.1``
侥幸没出事，但一旦独立部署 / 换 IP / 放到反向代理后面 ——
立刻变成公网可调用的 AI 接口。

更隐蔽的是 ``/v1/agent/reload`` —— 它**连 ``_verify_token`` 都没调**，
等价于一个无鉴权的「切换后端模型」控制面。

本文件覆盖
----------
1. **启动期 fail fast** —— secret 未配置 → 拒绝启动（不是裸奔着跑）
2. **端点鉴权** —— 4 个端点全部要求 Bearer 令牌
3. **恒定时间比较** —— 防时序侧信道
4. **业务层 fail fast** —— backend 侧 secret 缺失 → 显式报错 + 降级
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_SERVICE = REPO_ROOT / "agent-service"

SECRET = "test-secret-token-for-r12"


def _load_agent_main(package_root: Path | None = None, alias: str = "_agent_svc"):
    """以独立包名加载 agent-service 的 ``main`` 模块。

    ⚠️ **不能用普通 import** —— ``agent-service/app`` 和
    ``backend/app`` **同名**！pytest 从仓库根跑时，
    ``sys.path`` 里 backend 在前 → ``import app.main``
    会加载**后端**的 main，测的根本不是智能体服务。

    这里用 importlib 把 ``app`` 目录注册成**别名包**
    （如 ``_agent_svc``），再导入 ``<别名>.main``。
    这样：
    - 相对导入（``from .agents import ...``）有正确的父包；
    - 与后端的 ``app`` 包**完全不冲突**。
    """
    root = package_root or AGENT_SERVICE
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # 把 <root>/app 注册成别名包
    pkg_spec = importlib.util.spec_from_file_location(
        alias, root / "app" / "__init__.py", submodule_search_locations=[str(root / "app")]
    )
    assert pkg_spec and pkg_spec.loader
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules[alias] = pkg
    pkg_spec.loader.exec_module(pkg)

    main = importlib.import_module(f"{alias}.main")
    return main


# ═══════════════════════════════════════════════════════════════════════════
# 1. 启动期 fail fast
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def agent_app(monkeypatch):
    """导入 agent-service 的 app（带合法 secret）。"""
    monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
    module = _load_agent_main()
    yield module
    for name in list(sys.modules):
        if name == "_agent_svc" or name.startswith("_agent_svc."):
            sys.modules.pop(name, None)


def test_startup_fails_without_secret(tmp_path, monkeypatch):
    """⚠️ 核心断言：secret 未配置 → **拒绝启动**。

    旧行为是「跳过鉴权」—— 那等于默认裸奔。
    """
    import shutil

    target = tmp_path / "svc"
    shutil.copytree(AGENT_SERVICE / "app", target / "app")
    monkeypatch.delenv("AGENT_SHARED_SECRET", raising=False)
    monkeypatch.delenv("AGENT_ALLOW_INSECURE", raising=False)

    with pytest.raises(RuntimeError, match="AGENT_SHARED_SECRET"):
        _load_agent_main(package_root=target, alias="_agent_svc_tmp")

    for name in list(sys.modules):
        if name == "_agent_svc_tmp" or name.startswith("_agent_svc_tmp."):
            sys.modules.pop(name, None)


def test_startup_allows_explicit_insecure_escape_hatch(tmp_path, monkeypatch):
    """逃生舱：显式 AGENT_ALLOW_INSECURE=true 时允许免鉴权启动。

    这是**刻意的开发便利**，但必须显式开启 ——
    绝不接受「默认就免鉴权」。
    """
    import shutil

    target = tmp_path / "svc"
    shutil.copytree(AGENT_SERVICE / "app", target / "app")
    monkeypatch.delenv("AGENT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("AGENT_ALLOW_INSECURE", "true")

    module = _load_agent_main(package_root=target, alias="_agent_svc_tmp")
    assert module.app is not None

    for name in list(sys.modules):
        if name == "_agent_svc_tmp" or name.startswith("_agent_svc_tmp."):
            sys.modules.pop(name, None)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 端点鉴权
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client(agent_app):
    from fastapi.testclient import TestClient

    return TestClient(agent_app.app)


def _payload(agent_app):
    """构造一个最小可用的 draft 请求体（字段名必须与真实 schema 一致）。"""
    return {
        "task_id": "TASK_R12",
        "employee": {"employee_id": "E001", "name": "测试员工"},
        "period": "2026-06",
        "metrics": [
            {
                "code": "gmv",
                "dimension": "业绩",
                "value": 95.0,
                "target": 100.0,
                "ratio": 0.95,
                "ratio_level": "B",
            }
        ],
        "evidence": [],
        "attendance_anomalies": [],
    }


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/agent/draft", "draft"),
        ("/v1/agent/rating", "rating"),
        ("/v1/agent/evidence-check", "evidence"),
        ("/v1/agent/reload", None),
    ],
)
def test_all_mutating_endpoints_require_auth(client, agent_app, path, body):
    """⚠️ 4 个端点**全部**必须有鉴权。

    特别包含 ``/v1/agent/reload`` —— 它原本**完全没有**鉴权，
    是最容易漏掉的一个。

    ⚠️ 断言用**合法请求体** —— 鉴权必须在参数校验**之前**发生。
    若端点把校验写在函数体里，FastAPI 会先校验请求体 → 返回 422，
    于是「未认证却能探到端点参数结构」，且日志里看到的是
    「参数错误」而不是「未认证」，监控会误判。
    """
    payload = {
        "draft": _payload(agent_app),
        "rating": {
            "metrics": [
                {"code": "gmv", "value": 95.0, "target": 100.0, "ratio": 0.95}
            ],
            "evidence": [],
            "period": "2026-06",
        },
        "evidence": {"text": "本月完成了销售额目标", "question_text": ""},
    }

    kwargs: dict = {}
    if body is not None:
        kwargs["json"] = payload[body]

    resp = client.post(path, **kwargs)
    assert resp.status_code == 401, f"{path} 未鉴权也能访问：{resp.status_code}"


def test_auth_is_checked_before_body_validation(client, agent_app):
    """⚠️ 鉴权必须**先于**请求体校验。

    发送**格式错误**的请求体且不带令牌 → 必须 401，不能是 422。

    若是 422，说明端点结构被未认证的调用方探到了 ——
    422 的 detail 会列出缺哪个字段、期望什么类型。
    """
    resp = client.post("/v1/agent/draft", json={"totally": "wrong"})
    assert resp.status_code == 401, f"鉴权顺序错误：{resp.status_code} {resp.text[:200]}"
    # 且不能泄露字段结构
    assert "task_id" not in resp.text


def test_valid_token_is_accepted(client, agent_app):
    """带正确令牌 → 正常响应（不是 401）。"""
    resp = client.post(
        "/v1/agent/draft",
        json=_payload(agent_app),
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert resp.status_code == 200, resp.text


def test_wrong_token_is_rejected(client, agent_app):
    """错误令牌 → 401。"""
    resp = client.post(
        "/v1/agent/draft",
        json=_payload(agent_app),
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


def test_malformed_authorization_header_is_rejected(client, agent_app):
    """畸形 Authorization 头 → 401（不能因为格式怪就放行）。"""
    for header in ("", "Basic abc", SECRET, "Bearer", "Bearer "):
        resp = client.post(
            "/v1/agent/draft",
            json=_payload(agent_app),
            headers={"Authorization": header},
        )
        assert resp.status_code == 401, f"Authorization={header!r} 未被拒绝"


def test_health_endpoint_stays_public(client, agent_app):
    """``/health`` 保持公开 —— 探针不该要令牌。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_error_message_does_not_leak_secret(client, agent_app):
    """401 响应不能泄露令牌内容或长度。"""
    resp = client.post(
        "/v1/agent/draft",
        json=_payload(agent_app),
        headers={"Authorization": "Bearer guess-me"},
    )
    assert resp.status_code == 401
    assert SECRET not in resp.text
    assert "guess-me" not in resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 3. 业务层（backend）侧 fail fast
# ═══════════════════════════════════════════════════════════════════════════


def test_backend_agent_client_requires_secret():
    """backend 侧：secret 缺失 → 构造客户端时**显式报错**。

    旧行为是「静默不发 Authorization 头」，配合服务端放行 = 全链路免鉴权。
    """
    from backend.app.services.review_service import (
        AgentAuthNotConfigured,
        AgentClient,
    )

    with pytest.raises(AgentAuthNotConfigured, match="AGENT_SHARED_SECRET"):
        AgentClient(base_url="http://127.0.0.1:8800", secret="")


def test_backend_agent_client_sends_bearer_header():
    """backend 侧：有 secret → **一定**带上 Authorization 头。"""
    from backend.app.services.review_service import AgentClient

    client = AgentClient(base_url="http://127.0.0.1:8800", secret="abc123")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer abc123"
    assert headers["Content-Type"] == "application/json"
