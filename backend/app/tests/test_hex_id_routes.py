"""验证飞书 hex ID 走 FastAPI 路由时不再触发 422 / 500。

覆盖本次 int -> str 改造涉及的全部端点：
- 路径参数：user_id / dept_id / owner_id / group_id
- 查询参数：deptId / orgId / org_id / ownerId / scopeOrgId
- 响应模型：ProjectMonthPlanPackage.groupId / DepartmentEmployeeSummary.orgId

飞书 ID 是长十六进制字符串（例：912ecd8a7f82e638、ou_xxx），
转 int 会超出 Number.MAX_SAFE_INTEGER 并丢精度，故后端统一按 str 处理。

⚠️ R3 安全加固后这些端点都需要登录态，测试统一带 `auth_headers`。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

HEX_DEPT = "912ecd8a7f82e638"
HEX_USER = "ou_912ecd8a7f82e638"
ROOT_SENTINEL = "0"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers() -> dict:
    """生成一个合法的 HMAC Token（管理员视角）。

    这些端点需要登录态才能进入业务逻辑（否则 401/403 会掩盖
    「ID 格式是否被接受」这个真正的断言点）。

    ⚠️ CI 用的是**空库**（`_ci_local.db`），真实库里可能一个员工都没有。
    因此这里**不依赖真实库数据**：用 CI 自己的库建一个管理员账号，
    再签发 Token。

    端点若因为业务数据缺失返回 404/403 属可接受（说明已过鉴权层，
    真正要防的是 422 与 500）。
    """
    from backend.app.core.db import get_sessionmaker
    from backend.app.domain.models import Employee
    from backend.app.routes.auth import _generate_token

    db = get_sessionmaker()()
    try:
        admin = db.query(Employee).filter(Employee.id == "CI_ADMIN").first()
        if admin is None:
            admin = Employee(
                id="CI_ADMIN",
                name="CI 管理员",
                employment_status="active",
                account_status="enabled",
            )
            db.add(admin)
            db.commit()
        token, _ = _generate_token("CI_ADMIN", "ci_admin", 1)
        return {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


@pytest.mark.parametrize(
    "method,url,params",
    [
        # ── v1/system_users.py ────────────────────────────────────────
        ("put", f"/v1/system/users/{HEX_USER}", None),
        ("post", f"/v1/system/users/{HEX_USER}/status", None),
        ("post", f"/v1/system/users/{HEX_USER}/reset-password", None),
        # ── v1/system.py ─────────────────────────────────────────────
        ("put", f"/v1/system/departments/{HEX_DEPT}", None),
        ("delete", f"/v1/system/departments/{HEX_DEPT}", None),
        # ── department.py ────────────────────────────────────────────
        ("get", f"/v1/department/employees/{HEX_USER}/work-items", None),
        ("get", f"/v1/department/employees/{HEX_USER}/month-plans/1", None),
        ("get", f"/v1/department/project-month-plan-packages/{HEX_DEPT}", {"plan_month": "2026-01"}),
        ("get", f"/v1/department/project-result-packages/{HEX_DEPT}", None),
        # ── leader.py ────────────────────────────────────────────────
        ("get", f"/v1/leader/employees/{HEX_USER}/work-items", None),
        ("get", f"/v1/leader/employees/{HEX_USER}/month-plans/1", None),
        # ── leader_suggestion.py ──────────────────────────────────────
        ("get", f"/v1/leader/project-day-plan-packages/{HEX_DEPT}", None),
        ("get", f"/v1/leader/project-week-plan-packages/{HEX_DEPT}", None),
        ("get", f"/v1/leader/project-week-result-packages/{HEX_DEPT}", None),
    ],
)
def test_hex_id_not_rejected(
    client: TestClient,
    auth_headers: dict,
    method: str,
    url: str,
    params: dict | None,
) -> None:
    """hex ID 必须被接受：既不能 422（参数校验失败），也不能 500（响应模型不匹配）。"""
    kwargs: dict = {"headers": auth_headers}
    if params:
        kwargs["params"] = params
    if method in {"put", "post"}:
        kwargs["json"] = {}

    response = getattr(client, method)(url, **kwargs)

    # ⚠️ 断言的是「hex ID 没被当成非法输入」，不是「端点必须成功」。
    #
    #   422 = 参数校验层拒绝了 hex 字符串（如 int 类型转换失败）→ 必须防
    #   500 = 处理 hex ID 时崩了 → 必须防
    #
    # 404 / 403 属可接受：说明已经过了鉴权与参数校验层，
    # 只是该业务实体在当前库里不存在（CI 用空库，真实库也没有那个 ID）。
    # 端点自己的 docstring 与本文件的 fixture docstring 也都这么写。
    assert response.status_code != 422, f"{method.upper()} {url} -> 422 {response.text}"
    assert response.status_code != 500, f"{method.upper()} {url} -> 500 {response.text}"
    assert response.status_code < 500, f"{method.upper()} {url} -> {response.status_code} {response.text}"


@pytest.mark.parametrize(
    "url,params",
    [
        # deptId 查询参数
        ("/v1/system/users", {"deptId": HEX_DEPT}),
        # org_id / orgId / ownerId / scopeOrgId 查询参数
        ("/v1/department/employees", {"org_id": HEX_DEPT}),
        ("/v1/department/project-month-plan-packages", {"org_id": HEX_DEPT, "plan_month": "2026-01"}),
        ("/v1/department/project-result-packages", {"org_id": HEX_DEPT, "plan_month": "2026-01"}),
        ("/v1/leader/result-suggestions", {"orgId": HEX_DEPT, "ownerId": HEX_USER}),
        ("/v1/leader/project-day-plan-packages", {"scopeOrgId": HEX_DEPT}),
        ("/v1/leader/project-week-plan-packages", {"scopeOrgId": HEX_DEPT}),
        ("/v1/leader/project-week-result-packages", {"scopeOrgId": HEX_DEPT}),
    ],
)
def test_hex_id_query_not_rejected(
    client: TestClient, auth_headers: dict, url: str, params: dict
) -> None:
    """查询参数传 hex 字符串不能触发 422。"""
    response = client.get(url, params=params, headers=auth_headers)
    assert response.status_code != 422, f"GET {url} {params} -> 422 {response.text}"


def test_root_sentinel_is_string(client: TestClient) -> None:
    """根节点哨兵值是字符串 "0"，不能写成数字 0。"""
    from backend.app.core.db import get_sessionmaker
    from backend.app.services import system_views

    db = get_sessionmaker()()
    try:
        tree = system_views.list_department_tree(db)
    finally:
        db.close()

    for node in tree:
        assert isinstance(node["id"], str), node["id"]
        assert isinstance(node["parentId"], str), node["parentId"]

    # 根节点的 parentId 必须是字符串 "0"（飞书根部门 parent_id 的原值）。
    for node in tree:
        assert node["parentId"] == ROOT_SENTINEL, (
            f"根节点 parentId 应为字符串 '0'，实际为 {node['parentId']!r}"
        )


def test_empty_summary_uses_string_sentinel(
    client: TestClient, auth_headers: dict
) -> None:
    """员工摘要里的 orgId 必须是字符串 "0"（根节点哨兵）。"""
    response = client.get(
        f"/v1/department/employees/{HEX_USER}/work-items", headers=auth_headers
    )
    assert response.status_code == 200, response.text

    org_id = response.json()["data"]["employee"]["orgId"]
    assert org_id == ROOT_SENTINEL, f"orgId 应为字符串 '0'，实际为 {org_id!r}"
