"""API 契约回归测试。

保证「批次 2 补齐的端点」不会因为后续重构而再次缺失或响应结构漂移。

覆盖内容：
1. 批次 2 的 38 个目标端点必须全部注册（方法 + 路径双维度）。
2. 关键端点的响应信封结构正确（``{code: 0, data: ...}``）。
3. 契约校验脚本能正确归一化模板字符串 / 三元表达式路径。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.dependencies import get_current_user
from backend.app.domain.models import Employee
from backend.app.main import app

# backend/app/tests/test_api_contract_batch2.py
#   parents[0]=tests  parents[1]=app  parents[2]=backend  parents[3]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_SCRIPT = REPO_ROOT / "backend" / "scripts" / "check_api_contract.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    """契约测试用的已登录客户端。

    ⚠️ 两点需要处理：

    1. 部分端点（如 ``/v1/dispute/dashboard``）已启用 token 鉴权，
       未登录会返回 401 —— 因此注入一个 HR 身份，
       让契约测试校验的是「响应信封结构」而不是「鉴权」。
    2. 应用默认连的是开发用的 ``data/_autogen_tmp.db``（表结构不完整），
       契约测试不应依赖它 —— 改用独立的内存库 + ``create_all``。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.app.core.db import get_db
    from backend.app.domain.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    hr_user = Employee(
        id="contract-test-hr",
        name="契约测试用户",
        department_id="DEPT_HR",
        employment_status="active",
        account_status="enabled",
    )
    session.add(hr_user)
    session.commit()

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: hr_user
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


@pytest.fixture(scope="module")
def openapi_paths():
    """FastAPI 实际注册的 OpenAPI 路径 → 方法集合 映射。"""
    return {
        path: set(operations.keys())
        for path, operations in app.openapi().get("paths", {}).items()
    }


# ---------------------------------------------------------------------------
# 1. 批次 2 端点注册断言
# ---------------------------------------------------------------------------

# 批次 2 清单：docs/05-API契约对齐清单.md 中「批次 2 — 同组补子路径」
BATCH2_ENDPOINTS: tuple[tuple[str, str], ...] = (
    # system/knowledge
    ("GET", "/v1/system/knowledge/documents"),
    ("GET", "/v1/system/knowledge/documents/{document_id}"),
    ("DELETE", "/v1/system/knowledge/documents/{document_id}"),
    ("POST", "/v1/system/knowledge/documents/{document_id}/enable"),
    ("POST", "/v1/system/knowledge/documents/{document_id}/disable"),
    ("GET", "/v1/system/knowledge/documents/{document_id}/history"),
    ("POST", "/v1/system/knowledge/documents/{document_id}/versions"),
    ("GET", "/v1/system/knowledge/preview"),
    ("GET", "/v1/system/knowledge/status"),
    ("POST", "/v1/system/knowledge/sync"),
    # leader/knowledge
    ("POST", "/v1/leader/knowledge/upload"),
    ("GET", "/v1/leader/knowledge/uploads"),
    # department/export-tasks
    ("GET", "/v1/department/export-tasks/{task_id}"),
    ("POST", "/v1/department/export-tasks/{task_id}/check"),
    ("GET", "/v1/department/export-tasks/{task_id}/download"),
    ("GET", "/v1/department/export-tasks/{task_id}/download-info"),
    ("POST", "/v1/department/export-tasks/{task_id}/retry"),
    # leader/export-tasks
    ("GET", "/v1/leader/export-tasks/{task_id}/download"),
    ("GET", "/v1/leader/export-tasks/{task_id}/download-info"),
    # employee/export-tasks
    ("GET", "/v1/employee/export-tasks/{task_id}/download"),
    # leader/employees
    ("GET", "/v1/leader/employees"),
    ("GET", "/v1/leader/employees/{owner_id}/work-items"),
    ("GET", "/v1/leader/employees/{owner_id}/month-plans/{plan_id}"),
    # department/employees
    ("GET", "/v1/department/employees/{owner_id}/work-items"),
    ("GET", "/v1/department/employees/{owner_id}/month-plans/{plan_id}"),
    # system/roles
    ("GET", "/v1/system/roles/{role_id}/permissions"),
    ("PUT", "/v1/system/roles/{role_id}/permissions"),
    # system/workday-rules
    ("POST", "/v1/system/workday-rules/{rule_id}/status"),
    # system/audits
    ("GET", "/v1/system/audits/options"),
    # department/project-result-packages
    ("GET", "/v1/department/project-result-packages/{group_id}"),
    # dashboard
    ("GET", "/v1/employee/dashboard"),
    ("GET", "/v1/dispute/dashboard"),
    # org-tree
    ("GET", "/v1/department/org-tree"),
    ("GET", "/v1/leader/org-tree"),
    # system/week-plan-standard-supplements
    ("GET", "/v1/system/week-plan-standard-supplements/{supplement_id}"),
    # messages
    ("GET", "/v1/messages"),
    # leader/workbench
    ("GET", "/v1/leader/workbench"),
    # planning/stats
    ("GET", "/v1/planning/stats"),
)


@pytest.mark.parametrize(("method", "path"), BATCH2_ENDPOINTS)
def test_batch2_endpoint_registered(openapi_paths, method, path):
    """批次 2 的每个端点都必须真实注册到 OpenAPI。"""
    assert path in openapi_paths, f"路径未注册: {method} {path}"
    assert method.lower() in openapi_paths[path], (
        f"方法未注册: {method} {path}（实际: {sorted(openapi_paths[path])}）"
    )


# ---------------------------------------------------------------------------
# 2. 响应信封结构断言
# ---------------------------------------------------------------------------

ENVELOPE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/leader/org-tree"),
    ("GET", "/v1/leader/workbench"),
    ("GET", "/v1/leader/employees"),
    ("GET", "/v1/department/org-tree"),
    ("GET", "/v1/employee/dashboard"),
    ("GET", "/v1/dispute/dashboard"),
    ("GET", "/v1/planning/stats"),
    ("GET", "/v1/system/roles/1/permissions"),
)


@pytest.mark.parametrize(("method", "path"), ENVELOPE_ENDPOINTS)
def test_response_envelope_shape(client, method, path):
    """响应必须使用 `{code: 0, data: ...}` 信封，前端 http.ts 才能自动拆包。"""
    resp = client.request(method, path)
    assert resp.status_code == 200, f"{method} {path} -> {resp.status_code}"
    body = resp.json()
    assert isinstance(body, dict), f"{method} {path} 未返回对象"
    assert body.get("code") == 0, f"{method} {path} code != 0"
    assert "data" in body, f"{method} {path} 缺少 data 字段"


# ---------------------------------------------------------------------------
# 3. 契约脚本路径归一化
# ---------------------------------------------------------------------------


def _load_contract_module():
    spec = importlib.util.spec_from_file_location("check_api_contract", CONTRACT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 普通模板字符串
        ("/leader/employees/${ownerId}/work-items", "/leader/employees/{id}/work-items"),
        # 三元表达式（内部含 `}`，朴素正则会截断）
        (
            "/department/templates/${id}/${enabled ? 'enable' : 'disable'}",
            "/department/templates/{id}/{id}",
        ),
        # 三元表达式 + 嵌套调用
        (
            "/ai/requests/${encodeURIComponent(requestId)}/cancel",
            "/ai/requests/{id}/cancel",
        ),
    ],
)
def test_contract_script_normalize_path(raw, expected):
    """契约脚本必须能把模板字符串 / 三元表达式路径归一化。"""
    module = _load_contract_module()
    assert module.normalize_path(raw) == expected
