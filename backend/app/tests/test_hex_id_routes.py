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

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app

HEX_DEPT = "912ecd8a7f82e638"
HEX_USER = "ou_912ecd8a7f82e638"
ROOT_SENTINEL = "0"


@pytest.fixture(scope="module")
def isolated_db(tmp_path_factory):
    """把整个模块切到**独立数据库**，不碰共享的 ``perf.db``。

    ⚠️ **为什么必须隔离**：
    这个模块的 ``auth_headers`` fixture 会在库里建 ``CI_ADMIN`` 员工，
    ``test_root_sentinel_is_string`` 还要读部门树。
    直接用 ``get_sessionmaker()`` 就会把测试数据写进**真实库** ——
    不可逆，而且会污染线上对账数据（之前 ``CI_ADMIN`` 就是这么混进去的）。

    ⚠️ 这里**用 Alembic 建表**而不是 ``Base.metadata.create_all()``：
    create_all 只建表、不写 ``alembic_version``，
    之后 CI 的「Alembic 升级到 head」会再 CREATE 一遍同样的表 →
    `table department already exists` → 迁移检查整条挂掉。

    ⚠️ 注意 ``tmp_path_factory`` 而非 ``tmp_path``：
    模块级 fixture 的作用域比函数级宽，函数级 fixture 不能被它依赖。

    ⚠️ **为什么额外产出自己的 sessionmaker**：
    ``core.db`` 里的 ``_engine`` / ``_SessionLocal`` 是**模块级全局**。
    别的测试模块会把 ``database_url`` monkeypatch 掉并把 ``_engine``
    重置为 ``None`` —— 一旦发生在这个模块的 fixture 存续期间，
    本模块再调 ``get_sessionmaker()`` 就会拿到**另一个**引擎
    （可能指向别的库，甚至指向真实 perf.db）。

    这正是本模块之前**偶发**失败的根因：
    ``test_root_sentinel_is_string`` 往错误的库里插部门，
    撞 ``UNIQUE constraint failed: department.id``；
    单独跑必过、全量跑偶尔挂 —— 典型的顺序依赖型 flaky。

    所以这里**自己造一个绑定到本模块引擎的 sessionmaker** 并产出，
    本模块的所有测试都用它，不再依赖那个全局单例。
    """
    import os
    import subprocess

    from backend.app.core import config as config_mod
    from backend.app.domain.models import Base

    db_path = tmp_path_factory.mktemp("hex_routes") / "hex_routes.db"
    url = f"sqlite:///{db_path.as_posix()}"

    import backend.app.core.db as db_mod

    original_url = config_mod.settings.database_url
    config_mod.settings.database_url = url
    db_mod._engine = None
    db_mod._SessionLocal = None

    # 用 Alembic 把这个新库升到 head（并写入 alembic_version）。
    repo_root = Path(__file__).resolve().parents[3]
    env = {**os.environ}
    env["DATABASE_URL"] = url
    env.setdefault("AUTO_CREATE_TABLES", "false")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(repo_root / "backend"),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # ⚠️ 保险：万一 Alembic 没建全，用 Base 补齐（不写 alembic_version，
    #    但这库是临时的，不会影响 CI 的迁移检查）。
    engine = create_engine(url)
    Base.metadata.create_all(engine)

    # 本模块自有的 sessionmaker —— 绑定到上面那个引擎，
    # **不经过** core.db 的全局单例，因此不受其它模块干扰。
    module_sessionmaker = sessionmaker(bind=engine, autoflush=False, future=True)

    # ⚠️ 把 URL 一并交给调用方：`_upgrade_schema_to_head()` 需要它，
    #    否则子进程会读 `backend/.env` 里的**真实库**路径（见该函数说明）。
    module_sessionmaker.db_url = url  # type: ignore[attr-defined]

    yield module_sessionmaker

    engine.dispose()
    config_mod.settings.database_url = original_url
    db_mod._engine = None
    db_mod._SessionLocal = None


@pytest.fixture(scope="module")
def client(isolated_db) -> TestClient:
    """本模块的 TestClient。

    ⚠️ **必须覆盖 ``get_db`` 依赖**：
    应用默认走 ``core.db.get_db`` → 全局 ``get_sessionmaker()``。
    那个全局单例可能被别的测试模块重置成**别的引擎**，
    于是请求会落到别的库（甚至真实 perf.db）——
    这会让本模块的断言变得毫无意义，也污染真实数据。

    这里显式覆盖成**本模块的 sessionmaker**，
    保证「请求读的库」与「测试写的库」永远是同一个。
    """
    from backend.app.core.db import get_db

    def _isolated_get_db():
        db = isolated_db()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = _isolated_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _upgrade_schema_to_head(url: str) -> None:
    """用 Alembic 把**指定**的库升到 head。

    ⚠️ 必须走 Alembic 而不是 `Base.metadata.create_all()`：
    后者只建表、不写 `alembic_version`，会让 CI 后续的迁移检查
    撞上 `table xxx already exists`。

    ⚠️⚠️ **`url` 必须显式传进来，不能靠继承环境变量**（2026-11-16 实测）：
    子进程是**全新**的 Python 进程，它会重新读 `backend/.env` ——
    那里写的是**真实库**的绝对路径（``.../backend/data/perf.db``）。
    于是「给隔离库建表」实际建到了**真实库**上，
    真实库会被塞进 `CI_ADMIN` 测试账号（之前就是这么混进去的）。

    `env["DATABASE_URL"]` 的优先级高于 `.env`（pydantic-settings：
    环境变量 > env_file），所以这里**必须覆盖**而不是 `setdefault`。
    """
    import os
    import subprocess

    repo_root = Path(__file__).resolve().parents[3]
    env = {**os.environ}
    # ⚠️ 覆盖而非 setdefault —— 继承来的值可能指向真实库。
    env["DATABASE_URL"] = url
    env["AUTO_CREATE_TABLES"] = "false"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(repo_root / "backend"),
        env=env,
        capture_output=True,
        text=True,
        # Windows 默认 GBK 解码，Alembic 输出含非 ASCII → UnicodeDecodeError。
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def auth_headers(isolated_db) -> dict:
    """生成一个合法的 HMAC Token（管理员视角）。

    这些端点需要登录态才能进入业务逻辑（否则 401/403 会掩盖
    「ID 格式是否被接受」这个真正的断言点）。

    ⚠️ CI 用的是**空库**（`_ci_local.db`），真实库里可能一个员工都没有。
    因此这里**不依赖真实库数据**：用**本模块的隔离库**建一个管理员账号，
    再签发 Token。

    端点若因为业务数据缺失返回 404/403 属可接受（说明已过鉴权层，
    真正要防的是 422 与 500）。
    """
    from backend.app.domain.models import Employee
    from backend.app.routes.auth import _generate_token

    # ⚠️ 用**本模块自己的** sessionmaker（由 isolated_db 产出），
    #    不用全局 get_sessionmaker() —— 全局那个可能被别的测试模块
    #    重置成别的引擎，导致这里写到错误的库里。
    db = isolated_db()
    try:
        # ⚠️ CI 的执行顺序是「先跑测试、后跑迁移」，且 `AUTO_CREATE_TABLES=false`，
        #    所以在**全新**的库里跑测试时，`employee` 表根本还不存在
        #    → 直接查会报 `no such table: employee`（23 个用例 setup ERROR）。
        #
        #    ⚠️ 这里**必须用 Alembic 建表，不能用 `Base.metadata.create_all()`**：
        #    create_all 只建表、不写 `alembic_version`，
        #    于是 CI 后续的「Alembic 升级到 head」会再 CREATE 一遍同样的表 →
        #    `table department already exists` → 迁移检查整条挂掉。
        try:
            db.query(Employee).filter(Employee.id == "CI_ADMIN").first()
        except Exception:
            db.rollback()
            # ⚠️ 必须传隔离库的 URL —— 不传的话子进程会读 `.env`
            #    里的真实库路径，把迁移打到真实 perf.db 上。
            _upgrade_schema_to_head(isolated_db.db_url)  # type: ignore[attr-defined]

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
    # 端点自己的 docstring 也这么写。
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


def test_root_sentinel_is_string(client: TestClient, isolated_db) -> None:
    """根节点哨兵值是字符串 "0"，不能写成数字 0。

    ⚠️ **必须自己造一棵部门树**：
    隔离库是空的，直接读部门树会得到空列表，
    断言就成了「永远通过」的空测试 —— 等于没测。
    """
    from backend.app.domain.models import Department
    from backend.app.services import system_views

    # ⚠️ 用本模块自己的 sessionmaker（见 isolated_db 的说明）：
    #    走全局 get_sessionmaker() 会在别的模块重置过全局引擎时
    #    写到错误的库 —— 这正是本用例之前偶发失败的原因。
    db = isolated_db()
    try:
        # 造一棵有两层的树：根 → 子部门
        db.add(
            Department(
                id="root-dept-001",
                name="根部门",
                parent_id="0",
            )
        )
        db.add(
            Department(
                id=HEX_DEPT,
                name="子部门",
                parent_id="root-dept-001",
            )
        )
        db.commit()

        tree = system_views.list_department_tree(db)
    finally:
        db.close()

    assert tree, "部门树不应为空 —— 造的数据没生效，测试会失去意义"
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
