"""BUG-R6-46：飞书同步游标接口分页。

## 问题

``GET /v1/feishu/sync-organization/cursors`` **一次性返回全部游标**，没有分页。

游标是**「每部门一条」**的粒度（``employee:<部门ID>``）——
部门数一涨，游标条数就跟着涨（真实库已有 32 条，部门更多时会到几百条）。
后果：

1. **响应体线性膨胀** —— 每次刷新管理页都要传全量游标；
2. **前端表格一次渲染全部行** —— 部门多时页面明显卡顿；
3. **没有 ``total``** —— 前端做不出分页控件。

## 本文件锁定的契约

1. ``/cursors`` 返回**分页信封** ``{ records, total, page, pageSize }``；
2. 支持 ``page`` / ``pageSize``（camelCase）与 ``page_size``（snake_case）两种写法；
3. 支持按 ``resourceType`` 精确过滤；
4. ``/status`` 概览接口**不再塞全量游标** —— 只保留全局粒度
   （``department`` / ``employee``）的条目，明细走分页端点；
5. 越界参数必须被 FastAPI 拒绝（422），而不是静默截断。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    OrganizationSyncCursor,
    SystemAdmin,
)
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    db = TestingSessionLocal()

    db.add(Department(id="D1", name="研发部", active=True))
    db.add(
        Employee(
            id="ADMIN001",
            name="管理员",
            login_account="admin001",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.add(SystemAdmin(employee_id="ADMIN001", active=True))
    db.commit()

    token, _refresh = _generate_token("ADMIN001", "admin001")

    def override_get_db():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        yield client, db, headers
    app.dependency_overrides.clear()


def _seed_cursors(db, count: int = 25) -> None:
    """造 ``count`` 条分部门粒度游标 + 2 条全局粒度游标。"""
    for index in range(count):
        db.add(
            OrganizationSyncCursor(
                resource_type=f"employee:DEPT{index:03d}",
                checkpoint_value=f"ckpt-{index}",
                cursor_value=f"cur-{index}",
                page_token=None,
                snapshot_complete=True,
            )
        )
    db.add(
        OrganizationSyncCursor(
            resource_type="department",
            checkpoint_value="ckpt-dept",
            cursor_value="cur-dept",
            page_token=None,
            snapshot_complete=True,
        )
    )
    db.add(
        OrganizationSyncCursor(
            resource_type="employee",
            checkpoint_value="ckpt-emp",
            cursor_value="cur-emp",
            page_token=None,
            snapshot_complete=False,
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# 1. 分页信封
# ---------------------------------------------------------------------------


def test_cursors_endpoint_returns_page_envelope(client_and_db):
    """响应必须是分页信封，而不是裸数组。"""
    client, _db, headers = client_and_db
    _seed_cursors(_db, count=5)

    response = client.get("/v1/feishu/sync-organization/cursors", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert isinstance(data, dict), "必须返回分页信封，不能是裸数组"
    assert set(("records", "total", "page", "pageSize")).issubset(data.keys())
    assert isinstance(data["records"], list)
    assert data["total"] == 7  # 5 条分部门 + 2 条全局
    assert data["page"] == 1


def test_cursors_endpoint_caps_page_size(client_and_db):
    """单页条数不得超过 pageSize —— 这正是分页的意义所在。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=25)

    response = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"page": 1, "pageSize": 10},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["total"] == 27
    assert data["pageSize"] == 10
    assert len(data["records"]) == 10, "单页必须被截断，不能全量返回"


def test_cursors_endpoint_pages_do_not_overlap(client_and_db):
    """翻页时各页记录不得重复。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=25)

    first = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"page": 1, "pageSize": 10},
        headers=headers,
    ).json()["data"]
    second = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"page": 2, "pageSize": 10},
        headers=headers,
    ).json()["data"]

    first_keys = {row["resourceType"] for row in first["records"]}
    second_keys = {row["resourceType"] for row in second["records"]}
    assert first_keys and second_keys
    assert not (first_keys & second_keys), "两页记录不得重复"


def test_cursors_endpoint_last_page_may_be_short(client_and_db):
    """最后一页可以不足 pageSize（正常分页语义）。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=5)  # 共 7 条

    response = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"page": 1, "pageSize": 5},
        headers=headers,
    )
    data = response.json()["data"]
    assert len(data["records"]) == 5

    response = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"page": 2, "pageSize": 5},
        headers=headers,
    )
    data = response.json()["data"]
    assert len(data["records"]) == 2
    assert data["total"] == 7


def test_cursors_endpoint_page_beyond_range_returns_empty(client_and_db):
    """越界页码返回空列表而不是报错。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=3)

    response = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"page": 99, "pageSize": 10},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["records"] == []
    assert data["total"] == 5


# ---------------------------------------------------------------------------
# 2. camelCase / snake_case 参数兼容
# ---------------------------------------------------------------------------


def test_cursors_endpoint_accepts_both_param_styles(client_and_db):
    """``pageSize`` 与 ``page_size`` 都要能用。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=25)

    camel = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"pageSize": 3},
        headers=headers,
    ).json()["data"]
    snake = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"page_size": 3},
        headers=headers,
    ).json()["data"]

    assert camel["pageSize"] == 3
    assert snake["pageSize"] == 3
    assert len(camel["records"]) == 3
    assert len(snake["records"]) == 3


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"pageSize": 0},
        {"pageSize": 100000},
    ],
)
def test_cursors_endpoint_rejects_invalid_params(client_and_db, params):
    """非法分页参数必须 422，而不是静默兜底。"""
    client, _db, headers = client_and_db
    response = client.get(
        "/v1/feishu/sync-organization/cursors",
        params=params,
        headers=headers,
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# 3. resourceType 过滤
# ---------------------------------------------------------------------------


def test_cursors_endpoint_filters_by_resource_type(client_and_db):
    """按资源类型过滤后，total 只算过滤后的条数。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=5)

    response = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"resourceType": "department"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["total"] == 1
    assert len(data["records"]) == 1
    assert data["records"][0]["resourceType"] == "department"


# ---------------------------------------------------------------------------
# 4. /status 概览不再塞全量游标
# ---------------------------------------------------------------------------


def test_status_endpoint_does_not_return_per_department_cursors(client_and_db):
    """概览接口只保留全局粒度游标，分部门条目进分页端点。

    以前 ``/status`` 会把**全部**游标（含每部门一条）塞进响应，
    部门一多响应体就线性膨胀。
    """
    client, db, headers = client_and_db
    _seed_cursors(db, count=30)

    response = client.get("/v1/feishu/sync-organization/status", headers=headers)
    assert response.status_code == 200, response.text
    cursors = response.json()["data"]["cursors"]

    resource_types = {cursor["resourceType"] for cursor in cursors}
    assert resource_types == {"department", "employee"}, (
        f"概览接口不应包含分部门粒度游标，实际：{resource_types}"
    )


def test_status_endpoint_still_reports_incomplete_snapshot(client_and_db):
    """概览接口仍要能反映「快照不完整」（离职判定被跳过的前提）。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=1)  # employee 那条 snapshot_complete=False

    response = client.get("/v1/feishu/sync-organization/status", headers=headers)
    cursors = response.json()["data"]["cursors"]
    incomplete = [c for c in cursors if not c["snapshotComplete"]]
    assert len(incomplete) == 1, "概览接口必须保留「快照不完整」的信号"


# ---------------------------------------------------------------------------
# 5. 权限边界不能因分页而放宽
# ---------------------------------------------------------------------------


def test_cursors_endpoint_requires_admin(client_and_db):
    """普通员工不得读取同步游标。"""
    client, db, headers = client_and_db
    db.add(
        Employee(
            id="EMP001",
            name="普通员工",
            login_account="emp001",
            employment_status="active",
            account_status="enabled",
            token_version=1,
        )
    )
    db.commit()
    emp_token, _ = _generate_token("EMP001", "emp001")

    response = client.get(
        "/v1/feishu/sync-organization/cursors",
        headers={"Authorization": f"Bearer {emp_token}"},
    )
    assert response.status_code == 403, response.text


def test_cursors_endpoint_requires_auth(client_and_db):
    """未认证不得读取同步游标。"""
    client, _db, _headers = client_and_db
    response = client.get("/v1/feishu/sync-organization/cursors")
    assert response.status_code in (401, 403), response.text


# ---------------------------------------------------------------------------
# 6. 游标字段口径与 /status 一致
# ---------------------------------------------------------------------------


def test_cursors_endpoint_and_status_share_field_contract(client_and_db):
    """两个端点的游标字段必须完全一致（同一套序列化口径）。"""
    client, db, headers = client_and_db
    _seed_cursors(db, count=1)

    paged = client.get(
        "/v1/feishu/sync-organization/cursors",
        params={"resourceType": "department"},
        headers=headers,
    ).json()["data"]["records"][0]
    overview = client.get(
        "/v1/feishu/sync-organization/status", headers=headers
    ).json()["data"]["cursors"]
    overview_dept = next(c for c in overview if c["resourceType"] == "department")

    assert set(paged.keys()) == set(overview_dept.keys()), (
        "分页端点与概览端点的游标字段必须一致，否则前端要做两套适配"
    )
    assert paged["checkpointValue"] == overview_dept["checkpointValue"]
    assert paged["snapshotComplete"] == overview_dept["snapshotComplete"]
