"""员工计划闭环 API 契约回归测试。

覆盖「批次 3 员工计划闭环」补齐的 24 个端点：

- ``employee/month-plans``  (9)
- ``employee/week-plans``   (10，含 2 个周结果提交端点)
- ``employee/day-plans``    (5)

断言内容：
1. 每个端点都真实注册到 OpenAPI（方法 + 路径双维度）。
2. 响应使用 ``{code: 0, data: ...}`` 信封，前端 ``http.ts`` 才能自动拆包。
3. 关键端点的 ``data`` 字段集合与前端 TypeScript 接口逐字段对齐。
4. 需要鉴权的端点在无 token 时返回 401。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def openapi_paths():
    """FastAPI 实际注册的 OpenAPI 路径 → 方法集合 映射。"""
    return {
        path: set(operations.keys())
        for path, operations in app.openapi().get("paths", {}).items()
    }


# ---------------------------------------------------------------------------
# 1. 端点注册断言
# ---------------------------------------------------------------------------

EMPLOYEE_PLAN_ENDPOINTS: tuple[tuple[str, str], ...] = (
    # ── 月计划（9）────────────────────────────────────────────────
    ("GET", "/v1/employee/month-plans/{plan_id}"),
    ("GET", "/v1/employee/month-plans/{plan_id}/template-preview"),
    ("GET", "/v1/employee/month-plans/{plan_id}/template-download"),
    ("POST", "/v1/employee/month-plans/draft"),
    ("POST", "/v1/employee/month-plans/{plan_id}/draft"),
    ("POST", "/v1/employee/month-plans/{plan_id}/draft/clear"),
    ("POST", "/v1/employee/month-plans/{plan_id}/submit"),
    ("POST", "/v1/employee/month-plans/{plan_id}/withdraw"),
    ("DELETE", "/v1/employee/month-plans/{plan_id}/items/{item_id}"),
    # ── 周计划（10）────────────────────────────────────────────────
    ("GET", "/v1/employee/week-plans"),
    ("GET", "/v1/employee/week-plans/parent-options"),
    ("GET", "/v1/employee/week-plans/{plan_id}"),
    ("POST", "/v1/employee/week-plans"),
    ("PUT", "/v1/employee/week-plans/{plan_id}"),
    ("POST", "/v1/employee/week-plans/{plan_id}/submit"),
    ("POST", "/v1/employee/week-plans/{plan_id}/withdraw"),
    ("DELETE", "/v1/employee/week-plans/{plan_id}"),
    ("GET", "/v1/employee/week-plans/{plan_id}/items/{item_id}/results/submit-context"),
    ("POST", "/v1/employee/week-plans/{plan_id}/items/{item_id}/results/submit"),
    # ── 日计划（5）─────────────────────────────────────────────────
    ("GET", "/v1/employee/day-plans/detail"),
    ("POST", "/v1/employee/day-plans/draft"),
    ("POST", "/v1/employee/day-plans/submit"),
    ("POST", "/v1/employee/day-plans/{plan_id}/withdraw"),
    ("DELETE", "/v1/employee/day-plans/{plan_id}"),
)


@pytest.mark.parametrize(("method", "path"), EMPLOYEE_PLAN_ENDPOINTS)
def test_employee_plan_endpoint_registered(openapi_paths, method, path):
    """员工计划闭环的每个端点都必须真实注册到 OpenAPI。"""
    assert path in openapi_paths, f"路径未注册: {method} {path}"
    assert method.lower() in openapi_paths[path], (
        f"方法未注册: {method} {path}（实际: {sorted(openapi_paths[path])}）"
    )


def test_employee_plan_endpoint_count():
    """端点总数锁定为 24 —— 新增/删除端点时须同步更新本断言。"""
    assert len(EMPLOYEE_PLAN_ENDPOINTS) == 24


# ---------------------------------------------------------------------------
# 2. 鉴权断言
# ---------------------------------------------------------------------------

AUTH_REQUIRED_GETS: tuple[str, ...] = (
    "/v1/employee/month-plans/1",
    "/v1/employee/month-plans/1/template-preview",
    "/v1/employee/week-plans",
    "/v1/employee/week-plans/parent-options",
    "/v1/employee/week-plans/1",
    "/v1/employee/week-plans/1/items/1/results/submit-context",
    "/v1/employee/day-plans/detail?date=2026-11-14",
)


@pytest.mark.parametrize("path", AUTH_REQUIRED_GETS)
def test_employee_plan_requires_auth(client, path):
    """员工计划端点必须要求鉴权 —— 不能泄露任何计划数据给匿名用户。"""
    resp = client.get(path)
    assert resp.status_code == 401, f"{path} 未鉴权即可访问: {resp.status_code}"


# ---------------------------------------------------------------------------
# 3. 响应字段与前端 TypeScript 接口对齐
# ---------------------------------------------------------------------------

# (路径, data 顶层字段集合, 是否为裸数组)
EMPLOYEE_PLAN_SHAPES: tuple[tuple[str, set[str], bool], ...] = (
    (
        "/v1/employee/month-plans/1",
        {
            "id",
            "planMonth",
            "employeeName",
            "departmentName",
            "status",
            "resultStatus",
            "updatedAt",
            "items",
            "deliverables",
            "resultSummary",
            "confirmRecords",
        },
        False,
    ),
    (
        "/v1/employee/month-plans/1/template-preview",
        {"planId", "planMonth", "items", "estimatedValueTotal"},
        False,
    ),
    ("/v1/employee/week-plans", set(), True),
    ("/v1/employee/week-plans/parent-options", set(), True),
    (
        "/v1/employee/week-plans/1",
        {"summary", "items", "siblingPlans", "dayPlanCount"},
        False,
    ),
    (
        "/v1/employee/week-plans/1/items/1/results/submit-context",
        {
            "weekPlanId",
            "weekPlanTitle",
            "weekStart",
            "weekEnd",
            "weekPlanStatus",
            "task",
            "latestResult",
            "acceptedFileTypes",
            "maxFileSizeMb",
        },
        False,
    ),
    (
        "/v1/employee/day-plans/detail?date=2026-11-14",
        {
            "id",
            "planDate",
            "orgName",
            "relatedWeekPlanItemIds",
            "content",
            "remark",
            "status",
            "calendarRule",
            "weekPlanItemOptions",
            "legacyMonthPlanItem",
        },
        False,
    ),
)


def _fake_employee():
    from backend.app.domain.models import Employee

    return Employee(
        id="E_TEST",
        name="测试员工",
        department_id="D_TEST",
        employment_status="active",
        account_status="enabled",
    )


@pytest.mark.parametrize(("path", "fields", "is_list"), EMPLOYEE_PLAN_SHAPES)
def test_employee_plan_response_shape(client, path, fields, is_list):
    """响应 data 的字段集合必须与前端 TS 接口一致。"""
    from backend.app.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = _fake_employee
    try:
        resp = client.get(path)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    body = resp.json()
    assert body.get("code") == 0, f"{path} code != 0"
    assert "data" in body, f"{path} 缺少 data 字段"

    data = body["data"]
    if is_list:
        assert isinstance(data, list), f"{path} 应返回裸数组，实际 {type(data)}"
        return

    assert isinstance(data, dict), f"{path} 应返回对象，实际 {type(data)}"
    actual = set(data.keys())
    assert actual == fields, (
        f"{path} 字段不匹配\n  期望: {sorted(fields)}\n  实际: {sorted(actual)}\n"
        f"  多余: {sorted(actual - fields)}\n  缺失: {sorted(fields - actual)}"
    )


def test_week_plan_summary_shape(client):
    """WeekPlanSummary 的字段必须与 frontend/src/api/weekPlan.ts 一致。

    注意：`deptId` / `submitAt` / `approveAt` / `approvalComment` 在前端接口里
    是可选字段（`?`），后端仍应返回这些键以保持响应结构稳定。
    """
    from backend.app.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = _fake_employee
    try:
        resp = client.get("/v1/employee/week-plans/1")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    body = resp.json()
    summary = body["data"]["summary"]
    expected = {
        "id",
        "title",
        "weekStart",
        "weekEnd",
        "status",
        "versionNo",
        "ownerUserId",
        "employeeName",
        "deptId",
        "departmentName",
        "itemCount",
        "submitAt",
        "approveAt",
        "approvalComment",
    }
    assert set(summary.keys()) == expected, (
        f"WeekPlanSummary 字段不匹配\n  期望: {sorted(expected)}\n  实际: {sorted(summary.keys())}"
    )


def test_month_plan_result_summary_shape(client):
    """EmployeeResultSummary 字段必须与 frontend/src/api/employee.ts 一致。"""
    from backend.app.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = _fake_employee
    try:
        resp = client.get("/v1/employee/month-plans/1")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    result_summary = resp.json()["data"]["resultSummary"]
    expected = {
        "submittedCount",
        "confirmedCount",
        "rejectedCount",
        "latestVersion",
        "overallCompletionRate",
    }
    assert set(result_summary.keys()) == expected, (
        f"EmployeeResultSummary 字段不匹配\n"
        f"  期望: {sorted(expected)}\n  实际: {sorted(result_summary.keys())}"
    )
