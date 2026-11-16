"""系统管理端点响应形态回归测试（R2 修复）。

背景
----
`GET /v1/system/orgs` 曾把返回注解写成 `-> dict`，而函数实际返回
**列表**（部门树）。FastAPI 会按注解做响应模型校验，标错直接抛
`ResponseValidationError` → HTTP 500，前端表现为「组织架构页面加载失败」。

本测试锁定三件事：
1. `/orgs` 与别名 `/departments` 都返回 200，且响应体是**裸数组**；
2. 数组元素满足前端 `api/system.ts` 的 `DeptNode` 契约（11 个字段齐全）；
3. 不再出现「注解类型与实际返回不符」导致的 500。

⚠️ 注意：断言 `-> dict` 这类错误注解会**在导入期就变成 500**，
因此本用例是端点级的真实回归网，而不只是服务层单测。

**测试隔离**：本用例自己造一棵部门树（内存库 + 依赖覆盖），
不依赖真实库种子数据 —— CI 用的是空库，必须自给自足。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, Department, Employee
from backend.app.main import app

# 前端 `DeptNode` 契约（见 frontend/src/api/system.ts）。
DEPT_NODE_KEYS = {
    "id",
    "parentId",
    "name",
    "code",
    "orgType",
    "leaderUserId",
    "leaderName",
    "sortNo",
    "status",
    "employeeCount",
    "children",
}


@pytest.fixture()
def client_with_departments():
    """构造带部门树的独立测试实例（不依赖真实库）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(Department(id="D_ROOT", name="总部", parent_id=None, active=True))
    session.add(
        Department(id="D_HR", name="人力资源部", parent_id="D_ROOT", active=True)
    )
    session.add(Employee(id="U_HR", name="张三", department_id="D_HR"))
    session.commit()

    def override_get_db():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    # raise_server_exceptions=False：让 500 以响应体形式返回，便于断言状态码。
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


@pytest.mark.parametrize("path", ["/v1/system/orgs", "/v1/system/departments"])
def test_org_tree_endpoint_returns_200(
    client_with_departments: TestClient, path: str
) -> None:
    """部门树端点必须返回 200，而不是 500。

    回归点：注解写成 `-> dict` 但实际返回 list → ResponseValidationError。
    """
    resp = client_with_departments.get(path)

    assert resp.status_code == 200, (
        f"{path} 返回 {resp.status_code}，期望 200。"
        f"若为 500，多半是返回注解与实际返回类型不符。"
    )


@pytest.mark.parametrize("path", ["/v1/system/orgs", "/v1/system/departments"])
def test_org_tree_returns_bare_array(
    client_with_departments: TestClient, path: str
) -> None:
    """响应体必须是裸数组（不是 `{code, data}` 信封）。

    前端 `getDepartments(): DeptNode[]` 直接把结果当数组用，
    套信封会导致 `.map()` / `flattenDepartments` 直接抛错。
    """
    resp = client_with_departments.get(path)
    body = resp.json()

    assert isinstance(body, list), f"{path} 应返回数组，实际返回 {type(body).__name__}"


@pytest.mark.parametrize("path", ["/v1/system/orgs", "/v1/system/departments"])
def test_org_tree_node_matches_frontend_contract(
    client_with_departments: TestClient, path: str
) -> None:
    """每个部门节点必须满足前端 `DeptNode` 契约。

    缺字段在前端不会立刻报错，而是渲染成空列 —— 属于难查的静默 bug。
    """
    resp = client_with_departments.get(path)
    nodes = resp.json()

    assert nodes, "部门树为空，无法校验节点契约"

    for node in nodes:
        missing = DEPT_NODE_KEYS - set(node)
        assert not missing, f"部门节点缺字段 {sorted(missing)}：{node}"
        assert isinstance(node["children"], list)
        assert node["orgType"] in {"DEPARTMENT", "GROUP"}
