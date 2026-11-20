"""P1-5 / P2 端点契约断言 —— 锁定本轮新增端点的注册与响应信封。

为什么要有这个文件：
前端 ``src/api/p2.ts`` 按固定路径调用这些端点。如果后端改了路径或
把 ``{"code":0,"data":...}`` 换成裸对象，前端会**静默**拿到 undefined，
运行时才炸。这里把契约钉死，让破坏性改动在测试阶段就暴露。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
)
from backend.app.main import app


@pytest.fixture(scope="module")
def openapi_paths():
    """FastAPI 实际注册的 OpenAPI 路径 → 方法集合 映射。"""
    return {
        path: set(operations.keys())
        for path, operations in app.openapi().get("paths", {}).items()
    }


#: 本轮新增端点清单（与 frontend/src/api/p2.ts 一一对应）
P1_P2_ENDPOINTS: tuple[tuple[str, str], ...] = (
    # P1-5 数据源白名单
    ("GET", "/v1/data-sources"),
    ("PUT", "/v1/data-sources"),
    # P2-1 语义质检 / 风险 / 面谈提纲
    ("GET", "/v1/semantic-qc/records/{record_id}"),
    # P2-2 趋势复盘报表
    ("GET", "/v1/trend-report/trend/{employee_id}"),
    ("GET", "/v1/trend-report/position"),
    ("GET", "/v1/trend-report/risk-roster"),
    ("GET", "/v1/trend-report/payroll-export"),
    # P2-3 反馈 / 灰度 / 案例库
    ("POST", "/v1/feedback"),
    ("GET", "/v1/feedback"),
    ("GET", "/v1/feedback/prompts"),
    ("PUT", "/v1/feedback/prompts"),
    ("GET", "/v1/feedback/prompts/resolve"),
    ("GET", "/v1/feedback/prompts/compare"),
    ("GET", "/v1/feedback/cases"),
    ("POST", "/v1/feedback/cases"),
    # P1-4 代理授权
    ("GET", "/v1/delegations"),
    ("GET", "/v1/delegations/my-scope"),
    ("POST", "/v1/delegations"),
    # P1-3 执行类指令（两阶段）
    ("POST", "/v1/exec-tools/exec_remind/prepare"),
    ("POST", "/v1/exec-tools/exec_remind/confirm"),
    ("POST", "/v1/exec-tools/exec_export/prepare"),
    ("POST", "/v1/exec-tools/exec_export/confirm"),
    ("POST", "/v1/exec-tools/exec_stats/prepare"),
    ("POST", "/v1/exec-tools/exec_stats/confirm"),
)


@pytest.mark.parametrize(("method", "path"), P1_P2_ENDPOINTS)
def test_endpoint_registered(openapi_paths, method, path):
    """本轮每个端点都必须真实注册到 OpenAPI。"""
    assert path in openapi_paths, f"路径未注册: {method} {path}"
    assert method.lower() in openapi_paths[path], (
        f"方法未注册: {method} {path}（实际: {sorted(openapi_paths[path])}）"
    )


# ---------------------------------------------------------------------------
# 响应信封结构断言
# ---------------------------------------------------------------------------

ENVELOPE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/data-sources"),
    ("GET", "/v1/semantic-qc/records/{record_id}"),
    ("GET", "/v1/trend-report/trend/{employee_id}"),
    ("GET", "/v1/trend-report/position"),
    ("GET", "/v1/trend-report/risk-roster"),
    ("GET", "/v1/trend-report/payroll-export"),
    ("GET", "/v1/feedback"),
    ("GET", "/v1/feedback/prompts"),
    ("GET", "/v1/feedback/prompts/compare"),
    ("GET", "/v1/feedback/cases"),
)


@pytest.fixture
def client():
    """一个以 HR 身份访问的客户端（HR 能看到全部本轮端点）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Department(id="HR", name="人事"))
    session.add(HrDepartmentConfig(department_id="HR", active=True))
    session.add(
        Employee(
            id="H", name="HR", department_id="HR",
            employment_status="active", account_status="enabled",
        )
    )
    session.add(
        Employee(
            id="E", name="员工E", department_id="HR",
            employment_status="active", account_status="enabled",
        )
    )
    session.add(AssessmentTask(id="T", name="任务", period="202611"))
    session.flush()
    session.add(AssessmentRecord(id="R", task_id="T", employee_id="E"))
    session.commit()

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: session.get(Employee, "H")
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


@pytest.mark.parametrize(("method", "path"), ENVELOPE_ENDPOINTS)
def test_response_uses_code_data_envelope(client, method, path):
    """★ 响应必须是 ``{"code":0,"data":...}`` 信封。

    前端 ``http`` 拦截器依赖 ``body.code === 0`` 才解包 ``body.data``。
    返回裸对象的话，前端拿到的是整个响应体 —— 字段全部对不上。
    """
    url = path.format(record_id="R", employee_id="E")
    if path.endswith("/position"):
        url += "?position=P"
    if path.endswith("/resolve"):
        url += "?agent=comment_draft&record_id=R"
    response = client.get(url)
    assert response.status_code == 200, f"{path} → {response.status_code}"
    body = response.json()
    assert isinstance(body, dict), f"{path} 顶层不是对象"
    assert body.get("code") == 0, f"{path} 缺少 code=0 信封: {body}"
    assert "data" in body, f"{path} 缺少 data 字段"


# ---------------------------------------------------------------------------
# 查询参数名断言
# ---------------------------------------------------------------------------

#: 前端 ``src/api/p2.ts`` 实际发送的查询参数名 → 后端必须**逐字**接受。
#:
#: ⚠️ 为什么要钉死参数名：
#: FastAPI 不会把 ``departmentId`` 自动映射到 ``department_id``。
#: 参数名写错时 FastAPI **不报错**，而是当成"没传这个参数"，
#: 于是筛选条件被静默丢弃 —— 页面显示"全部部门"，用户以为筛选生效了。
#: 这是比报错更危险的失效方式，所以必须有测试守住。
FRONTEND_QUERY_PARAMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/v1/trend-report/position", ("position", "period", "task_id")),
    ("/v1/trend-report/risk-roster", ("department_id", "period", "task_id")),
    ("/v1/trend-report/payroll-export", ("period", "task_id", "department_id", "include_names")),
    ("/v1/feedback", ("agent",)),
    ("/v1/feedback/prompts", ("agent",)),
    ("/v1/feedback/prompts/compare", ("agent", "versions")),
    ("/v1/feedback/prompts/resolve", ("agent", "record_id")),
    ("/v1/feedback/cases", ("scenario", "keyword", "limit")),
)


@pytest.mark.parametrize(("path", "expected"), FRONTEND_QUERY_PARAMS)
def test_frontend_query_param_names_are_accepted(openapi_paths, path, expected):
    """前端发的每个查询参数名，后端端点必须真的声明了这个名字。"""
    assert path in openapi_paths, f"路径未注册: {path}"

    spec = app.openapi()
    operation = spec["paths"][path]["get"]
    declared = {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "query"
    }
    missing = set(expected) - declared
    assert not missing, (
        f"{path} 未声明前端会发送的查询参数 {sorted(missing)}；"
        f"FastAPI 会静默忽略它们（实际声明: {sorted(declared)}）"
    )


def test_frontend_camelcase_params_are_not_sent(openapi_paths):
    """前端**不得**发送 camelCase 查询参数。

    写成 camelCase 的话后端不会 400，而是静默忽略 —— 筛选失效但不报错。
    """
    spec = app.openapi()
    for path, operation in spec["paths"].items():
        if not path.startswith("/v1/trend-report") and not path.startswith("/v1/feedback"):
            continue
        query_names = {
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter.get("in") == "query"
        }
        camel = {name for name in query_names if any(c.isupper() for c in name)}
        assert not camel, f"{path} 声明了 camelCase 查询参数 {sorted(camel)}"
