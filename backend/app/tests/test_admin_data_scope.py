"""🔴 **2026-11-24 规则修订：数据范围按「角色来源」区分**。

业务规则（用户明确确认）
----------------------
1. HR **允许**跨部门看绩效数据。
2. 系统管理员 = 公司内**真实员工账号**，只是被分配了【绩效配置管理员】权限。
   ⇒ **不能**因为是管理员就自动获得跨部门业务数据范围
   （否则管理员账号就变成了「万能模拟账号」，可伪装成任意人看页面）。
3. 可见范围全部以**当前登录的真实员工身份**（部门、岗位、汇报线）动态判定。

实测到的越权（修复前）
----------------------
管理员账号登录后调 ``/v1/baidu/workload/summary``，
返回**全公司所有剪辑**的数据 —— 而该管理员在人事行政部，
与短视频部**无任何汇报线关系**。

⚠️ **根因不是「模拟账号」，而是「角色名当口径用」**
``pan_workload._scope_employee_ids`` 写的是 ``{"hr", "admin"} & roles``
⇒ 把「HR（来自 hr_department_config）」与「系统管理员（来自 system_admin 表）」
当成同一个口径 ⇒ 管理员被白送了跨部门数据范围。

⇒ **权限层本来就是对的**：``VIEW_ALL_SCORES`` 只授予 ``hr``，
``access_control.can_view_score_of`` 的注释也早就写明
「管理员**不**自动拥有查看全员成绩的权限」。
本文件锁住的是「路由层不要退回角色名判断」。

⚠️ **断言要能抓到越权**
只断言「无权时 403」是**否定式断言** ——
把整个范围检查删掉也照样通过 ⇒ 空转。
⇒ 每条都**同时**断言「有权时能通过」，确保测的是**差异**而非端点坏了。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    SystemAdmin,
)
from backend.app.domain.pan_workload_models import (
    DepartmentModule,
    PanWorkloadDaily,
)

PREFIX = "/v1/baidu/workload"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def org(db):
    """照抄真实组织结构（与 ``test_pan_workload_api`` 一致）。

    ⚠️ **角色靠机制产生，不靠名字**：
    - HR  ← ``HrDepartmentConfig``（D_HR）
    - 系统管理员 ← ``SystemAdmin`` 表
    - 部门 leader ← ``Department.leader_id``
    - 主管 ← 「有直属下属」（``manager_id`` 指向自己）

    关键角色：
    - ``E_HR``      人事行政部（**HR**）⇒ 允许跨部门
    - ``E_ADMIN``  人事行政部（**系统管理员**，非 HR）⇒ **不允许**跨部门
    - ``E_ZJ``     短视频部 编导（两位剪辑的直属上级）
    """
    d_center = Department(id="D_CENTER", name="运营中心", active=True)
    d_video = Department(id="D_VIDEO", name="短视频", active=True)
    d_hr = Department(id="D_HR", name="人事行政部", active=True)
    d_other = Department(id="D_OTHER", name="其它部门", active=True)
    db.add_all([d_center, d_video, d_hr, d_other])

    db.add_all([
        Employee(id="E_LCY", name="李婵雨", department_id="D_VIDEO",
                 position="剪辑", manager_id="E_ZJ",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_TY", name="佟瑶", department_id="D_VIDEO",
                 position="剪辑", manager_id="E_ZJ",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_ZJ", name="张洁", department_id="D_VIDEO",
                 position="编导", manager_id="E_LX",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_LX", name="刘旭", department_id="D_VIDEO",
                 position="运营", manager_id="E_TOP",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_TOP", name="上级", department_id="D_CENTER",
                 employment_status="active", account_status="enabled"),
        # ★ HR —— 所属部门被配为 HR 部门
        Employee(id="E_HR", name="HR", department_id="D_HR",
                 employment_status="active", account_status="enabled"),
        # ★ 系统管理员 —— 放在 D_CENTER（运营中心），
        #   这样他**只有**「系统管理员」这一个身份（不是 HR 部门成员），
        #   才能与 E_HR 区分开。
        Employee(id="E_ADMIN", name="系统管理员", department_id="D_CENTER",
                 employment_status="active", account_status="enabled"),
        # ⚠️ **外部门主管** —— 另一个部门（D_OTHER）的 leader，
        #   与短视频部（D_VIDEO）**无任何汇报线/部门关系**。
        #   ⇒ 他是主管（能过 ``_require_leader``），但管不到短视频部 ⇒
        #     正好用来测「是主管 ≠ 是这单的主管」。
        #   ⚠️ **不能复用 E_TOP** —— E_TOP 是 D_CENTER 的 leader，
        #   而 D_VIDEO 是 D_CENTER 的**子部门** ⇒ E_TOP 合法管辖短视频部
        #   （部门树向下展开）⇒ 用它测不出串门。
        Employee(id="E_LEAD_OTHER", name="外部门主管", department_id="D_OTHER",
                 position="主管", manager_id=None,
                 employment_status="active", account_status="enabled"),
    ])
    db.flush()
    d_video.parent_id = "D_CENTER"
    d_video.leader_id = "E_LX"
    d_center.leader_id = "E_TOP"
    d_hr.leader_id = "E_HR"
    d_other.leader_id = "E_LEAD_OTHER"
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()


@pytest.fixture()
def modules(db):
    """短视频部开通板块。

    ⚠️ **为什么管理员也要能进这个端点**
    板块隔离管的是「这个部门有没有这个业务」，
    与「能看谁的数据」是**两个维度** ⇒ 管理员要**全部板块**
    （否则开不了新板块），但**不该**自动获得全部数据。
    ⇒ 管理员放在 D_CENTER，这里也给 D_CENTER 开通板块，
    保证测试聚焦在**数据范围**这一个维度上。
    """
    db.add_all([
        DepartmentModule(department_id="D_VIDEO", module_code="pan_workload",
                        active=True),
        DepartmentModule(department_id="D_CENTER", module_code="pan_workload",
                        active=True),
        DepartmentModule(department_id="D_OTHER", module_code="pan_workload",
                        active=True),
    ])
    db.commit()


@pytest.fixture()
def admin_grant(db):
    """把 E_ADMIN 授予系统管理员（**只有**系统管理员，不是 HR）。"""
    db.add(SystemAdmin(employee_id="E_ADMIN", active=True))
    db.commit()


@pytest.fixture()
def client(db, monkeypatch):
    from backend.app.main import app
    from backend.app.routes import pan_workload as routes

    current = {"user": None}

    def _override_user():
        return current["user"]

    app.dependency_overrides[routes.get_current_user] = _override_user

    def _override_db():
        yield db

    app.dependency_overrides[routes.get_db] = _override_db

    with TestClient(app) as c:
        c.current = current  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def as_user(client, db):
    def _set(user_id: str):
        emp = db.get(Employee, user_id)
        assert emp is not None
        client.current["user"] = emp  # type: ignore[attr-defined]
        return emp

    return _set


def _daily(db, emp_id: str, day: str, total: int = 6):
    from datetime import date

    db.add(
        PanWorkloadDaily(
            employee_id=emp_id,
            business_date=date.fromisoformat(day),
            total_count=total,
            file_count=total,
            scanned_count=total,
            excluded_count=0,
            marker_breakdown={},
            anomaly_count={},
            adjusted_count=None,
            status="auto",
        )
    )
    db.commit()


def _review(db, emp_id: str, day: str = "2026-11-19"):
    """造一条复核工单。"""
    from datetime import date

    from backend.app.domain.pan_workload_models import PanWorkloadReview

    row = PanWorkloadReview(
        id=f"REV_{emp_id}_{day.replace('-', '')}",
        employee_id=emp_id,
        business_date=date.fromisoformat(day),
        scope="day",
        claimed_count=9,
        system_count=6,
        reason="数不对",
        status="pending",
        created_by=emp_id,
    )
    db.add(row)
    db.commit()
    return row


class TestSystemAdminDataScope:
    """🔴 核心回归：系统管理员**不**自动获得跨部门数据范围。"""

    def test_管理员看不到外部门员工(self, client, db, org, modules,
                                admin_grant, as_user):
        """🔴 **修复前会挂**：管理员能看到全公司剪辑数据。

        ⚠️ 管理员在运营中心（D_CENTER），两位剪辑在短视频部（D_VIDEO）
        ⇒ 二者**无任何汇报线关系** ⇒ 不该看到。
        """
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_TY", "2026-11-19", 9)
        as_user("E_ADMIN")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        ids = {e["employeeId"] for e in resp.json()["employees"]}
        assert "E_LCY" not in ids, "管理员看到了外部门员工的条数 —— 越权！"
        assert "E_TY" not in ids, "管理员看到了外部门员工的条数 —— 越权！"

    def test_管理员看不到外部门明细(self, client, db, org, modules,
                                  admin_grant, as_user):
        """⚠️ **配对**：summary 是聚合端点，明细端点也要拦。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_ADMIN")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_LCY&business_date=2026-11-19"
        )
        assert resp.status_code == 403, resp.text

    def test_管理员仍能管理配置(self, client, db, org, modules,
                              admin_grant, as_user):
        """⚠️ **配对** —— 证明「收窄数据范围」没有把管理员**废掉**。

        管理员的真实价值在**配置动作**（维护绑定、配置板块）
        ⇒ 那条线必须仍然通畅，否则会「修出死锁」。
        """
        as_user("E_ADMIN")
        # 绑定维护：仅 HR / 系统管理员
        resp = client.post(f"{PREFIX}/bindings", json={})
        assert resp.status_code != 403, (
            f"管理员被配置动作挡住了 ⇒ 会死锁：{resp.text}"
        )


class TestHrDataScope:
    """✅ HR 保留跨部门看数据的权限（业务口径确认）。"""

    def test_HR能看全员(self, client, db, org, modules, as_user):
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_TY", "2026-11-19", 9)
        as_user("E_HR")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        ids = {e["employeeId"] for e in resp.json()["employees"]}
        assert {"E_LCY", "E_TY"} <= ids, f"HR 看不到全员：{ids}"

    def test_HR能看外部门明细(self, client, db, org, modules, as_user):
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_HR")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_LCY&business_date=2026-11-19"
        )
        assert resp.status_code == 200, resp.text


class TestRoleSourceSeparation:
    """⚠️ 角色**来源**不同 ⇒ 数据范围**不同**。"""

    def test_管理员与HR是不同角色(self, db, org, admin_grant):
        """⚠️ **前提断言** —— 后面所有测试都建立在「两个角色可区分」上。

        ⇒ 若哪天管理员也被配成 HR 部门成员，本文件的前提就失效了
        ⇒ 必须**显式断言**，否则会静默失去区分度。
        """
        from backend.app.services.access_control import build_access_context
        from backend.app.services.hr_admin_roles import is_hr, is_system_admin

        admin = db.get(Employee, "E_ADMIN")
        hr = db.get(Employee, "E_HR")
        assert is_system_admin(db, admin) is True
        assert is_hr(db, admin) is False, "管理员被算成 HR 了"
        assert is_hr(db, hr) is True
        assert is_system_admin(db, hr) is False

        admin_ctx = build_access_context(db, admin)
        assert "admin" in admin_ctx.roles
        assert "hr" not in admin_ctx.roles

    def test_管理员没有全员查看权限位(self, db, org, admin_grant):
        """⚠️ **权限层本来是对的** —— 这里锁住它，防止有人顺手给 admin 加。

        ⇒ ``VIEW_ALL_SCORES`` 只授予 ``hr``。
        ⇒ 路由层一旦改用这个权限位判定，就不会越权。
        """
        from backend.app.services.access_control import (
            PermissionAction,
            build_access_context,
        )

        ctx = build_access_context(db, db.get(Employee, "E_ADMIN"))
        assert ctx.has_permission(PermissionAction.VIEW_ALL_SCORES) is False

        hr_ctx = build_access_context(db, db.get(Employee, "E_HR"))
        assert hr_ctx.has_permission(PermissionAction.VIEW_ALL_SCORES) is True

    def test_hr判定与access_context口径一致(self, db, org, admin_grant):
        """🔴 **防止两处口径漂移**。

        ⚠️ ``hr_admin_roles.is_hr``（服务层）
        与 ``access_control.build_access_context`` 里的 ``is_hr``
        **必须永远给出同一个答案** —— 否则会出现
        「路由说你是 HR，后端说不是」的分裂。

        ⇒ 本测试对**所有**员工断言两者一致（不只是 HR 本人）。
        """
        from backend.app.services.access_control import build_access_context
        from backend.app.services.hr_admin_roles import is_hr

        for emp in db.query(Employee).all():
            ctx = build_access_context(db, emp)
            assert is_hr(db, emp) == ("hr" in ctx.roles), (
                f"口径漂移：{emp.name} 服务层={is_hr(db, emp)} "
                f"而 ctx.roles={sorted(ctx.roles)}"
            )


class TestScopeFunction:
    """⚠️ 直接测 ``_scope_employee_ids`` —— 口径的**单一事实源**。"""

    def test_管理员返回受限列表而非None(self, db, org, admin_grant):
        """🔴 **核心不变量**。

        ⚠️ ``None`` = 不限 ⇒ 管理员**绝不能**返回 ``None``。
        这是整个修复的落点 —— 其它端点都走这个函数。
        """
        from backend.app.routes.pan_workload import _scope_employee_ids

        emp = db.get(Employee, "E_ADMIN")
        scope = _scope_employee_ids(db, emp)
        assert scope is not None, "管理员被判定为「不限」⇒ 越权"
        assert scope == ["E_ADMIN"], f"范围不对：{scope}"

    def test_HR返回None(self, db, org):
        from backend.app.routes.pan_workload import _scope_employee_ids

        emp = db.get(Employee, "E_HR")
        assert _scope_employee_ids(db, emp) is None

    def test_员工只看到自己(self, db, org):
        from backend.app.routes.pan_workload import _scope_employee_ids

        emp = db.get(Employee, "E_LCY")
        assert _scope_employee_ids(db, emp) == ["E_LCY"]

    def test_主管看到下属(self, db, org):
        from backend.app.routes.pan_workload import _scope_employee_ids

        emp = db.get(Employee, "E_ZJ")
        scope = _scope_employee_ids(db, emp)
        assert scope is not None
        assert {"E_LCY", "E_TY"} <= set(scope), f"主管看不到下属：{scope}"


class TestCrossDepartmentReviewResolve:
    """🔴 **2026-11-24 实测复现的第二个漏洞**：跨部门串门裁定。

    实测（修复前）：部门 A 主管调 ``POST /reviews/REV_B/resolve``
    ⇒ HTTP 200，部门 B 员工的工单 ``final_count`` 被改成 **99**（原本 6）
    ⇒ **跨部门篡改他人绩效工作量数据**。

    ⚠️ **根因**：``_require_leader`` 只判定「你是不是**某个**主管」
    ⇒ 部门 A 的主管对部门 B 的工单**同样通过**。
    ⇒ 缺的那一问是：「这单的**当事人**在你的管辖范围内吗？」
    """

    def test_外部门主管不能裁定工单(self, client, db, org, modules, as_user):
        """🔴 核心回归。"""
        from backend.app.domain.pan_workload_models import PanWorkloadReview

        review = _review(db, "E_LCY")  # 李婵雨（短视频部）的工单
        as_user("E_LEAD_OTHER")  # 其它部门的 leader，与短视频部无任何关系

        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 99},
        )
        assert resp.status_code == 403, resp.text

        # ⚠️ **数据必须没被动过**
        db.expire_all()
        row = db.get(PanWorkloadReview, review.id)
        assert row.status != "approved", "跨部门串门成功了 —— 漏洞没修好"
        assert row.final_count != 99, "工单被改了 —— 漏洞没修好"

    def test_本部门主管仍能裁定(self, client, db, org, modules, as_user):
        """⚠️ **配对** —— 证明上一条不是「端点全坏了」。

        ⚠️ **用 E_ZJ（编导，短视频部）** —— 她是两位剪辑的直属上级
        ⇒ 走的是「汇报线」路径，不是「部门 leader」路径。
        """
        review = _review(db, "E_LCY")
        as_user("E_ZJ")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 8},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["finalCount"] == 8

    def test_HR能裁定任何工单(self, client, db, org, modules, as_user):
        """⚠️ **配对** —— HR 允许跨部门看数据（业务口径确认）
        ⇒ 范围校验 (``None``) 不应挡住 HR 裁定。
        """
        review = _review(db, "E_LCY")
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 7},
        )
        assert resp.status_code == 200, resp.text


class TestNoRoleNameShortcut:
    """🔴 防止有人把 ``{"hr", "admin"}`` 写回**数据范围**判定。"""

    def test_数据范围判定不再有管理员全放行(self):
        """⚠️ **静态检查**：**数据范围**函数里
        不得再出现 ``{"hr", "admin"}`` 这种**合并口径**。

        ⇒ 目标端点若需要「不限」，必须写 ``"hr" in roles``。

        ⚠️ **为什么只查这一处、而不是整个文件**
        ``_is_admin`` / ``_is_leader`` 里的 ``{"hr", "admin"}`` 是**动作权限**
        （谁能配置、谁能裁定）⇒ 那里**应该**保留 ——
        管理员需要配置动作，但**不需要**全部业务数据。
        ⇒ 把两者混为一谈正是本次越权的根因。

        ⚠️ **为什么用 ``ast`` 而不是字符串搜索**
        注释里会**引用**这段旧代码作为反例（说明「过去是这么写的」）
        ⇒ 直接搜原文会误报；
        而按 ``def `` 切字符串又会被 docstring 里的换行骗到。
        ⇒ 用 ``ast`` 精确取到函数体，再看它**真正用到的名字**。

        ⚠️⚠️ **2026-11-25：判定已抽到共享服务** ``services.pan_workload_scope``
        ⇒ **真正含判定逻辑的**是 ``scope_employee_ids``
        ⇒ 两个 router（剪辑板块 / 部门板块）都调它
        ⇒ 这里检查**那个函数**（唯一的真相源）。
        """
        import ast
        from pathlib import Path

        from backend.app.services import pan_workload_scope

        src = Path(pan_workload_scope.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)

        func = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and n.name == "scope_employee_ids"
        )

        # ⚠️ **只取「真正用到的名字」** —— 注释与 docstring 不参与。
        names: set[str] = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)

        # ⚠️ ``ctx.roles`` 会被当成 ``roles``（Attribute 的 attr）。
        assert "hr" in names, (
            "scope_employee_ids 应当只对 HR 放行「不限」"
        )
        assert "roles" in names, (
            "scope_employee_ids 应当基于 ctx.roles 判定"
        )
        # 🔴 **核心不变量** —— 管理员**不得**出现在数据范围的角色判定里。
        assert "admin" not in names, (
            "scope_employee_ids 里出现了 admin ⇒ 管理员被白送了跨部门数据范围"
        )


# ═══════════════════════════════════════════════════════════════════════════
# B3（2026-11-24）—— 目录绑定列表的读权限收紧
# ═══════════════════════════════════════════════════════════════════════════


class TestBindingsReadScope:
    """🔴 ``GET /bindings`` 的读权限必须与写权限**同口径**。

    ⚠️ **背景**
    修复前：``GET /bindings`` 用 ``_require_leader``（主管即可），
    而 ``POST /bindings`` 用 ``_require_admin``（HR/管理员）
    ⇒ 出现「**能读不能写**」的不对称，
    且主管能读到**全公司**的「目录 → 人员」对照表（无范围过滤）。

    ⚠️ **为什么绑定表是配置而非业务数据**
    ``PanAccountBinding`` 是「网盘目录 → 员工」的映射，
    由 HR/管理员维护；**不属于**「谁的工作量」这类业务数据。
    ⇒ 一个目录绑给谁是**公司级决策**，主管看自己范围内的那几条没有业务价值。
    """

    @pytest.fixture()
    def binding(self, db):
        """造一条绑定，验证「读到的内容确实越权」。"""
        from backend.app.domain.pan_workload_models import PanAccountBinding

        row = PanAccountBinding(
            folder_path="/成品-李婵雨",
            folder_name="李婵雨",
            employee_id="E_LCY",
            active=True,
        )
        db.add(row)
        db.commit()
        return row

    def test_主管不能读绑定列表(self, client, db, org, modules, binding, as_user):
        """🔴 **核心** —— 主管读绑定列表必须被拒。

        ⚠️ ``E_ZJ``（张洁，编导）是两位剪辑的**直属上级**
        ⇒ 有 ``manager`` 角色 ⇒ **能过**旧的 ``_require_leader``
        ⇒ 这正是修复前的越权入口。
        """
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/bindings")
        assert resp.status_code == 403, (
            f"主管能读全公司绑定列表 ⇒ 越权：{resp.text[:200]}"
        )

    def test_外部门主管不能读绑定列表(
        self, client, db, org, modules, binding, as_user
    ):
        """⚠️ **配对** —— 换个部门的主管也一样。

        ⇒ 防止有人改成「按管辖范围过滤」而不是「要管理员」——
          那样外部门主管仍会拿到自己那几条（虽然不是全量，
          但**读写口径仍不一致**）。
        """
        as_user("E_LEAD_OTHER")
        resp = client.get(f"{PREFIX}/bindings")
        assert resp.status_code == 403, resp.text

    def test_员工不能读绑定列表(self, client, db, org, modules, binding, as_user):
        """⚠️ 员工本来就过不了 ``_require_leader`` —— 但要锁住，防回退。"""
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/bindings")
        assert resp.status_code == 403, resp.text

    def test_HR能读绑定列表(self, client, db, org, modules, binding, as_user):
        """⚠️ **配对** —— 证明上一条不是「端点坏了」。

        ⇒ HR 是配置的实际维护者，必须能读。
        """
        as_user("E_HR")
        resp = client.get(f"{PREFIX}/bindings")
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert any(i["folderPath"] == "/成品-李婵雨" for i in items), (
            f"HR 读不到绑定：{items}"
        )

    def test_管理员能读绑定列表(
        self, client, db, org, modules, binding, admin_grant, as_user
    ):
        """⚠️ **配对** —— 管理员是配置维护者，必须能读。

        ⇒ 这条与 ``test_管理员仍能管理配置``（写）配成一对
        ⇒ 证明「收窄读权限」没有把管理员**废掉**（不会死锁）。
        """
        as_user("E_ADMIN")
        resp = client.get(f"{PREFIX}/bindings")
        assert resp.status_code == 200, resp.text

    def test_读写权限口径一致(self):
        """🔴 **核心不变量** —— 同一资源的 GET 与 POST 必须同口径。

        ⚠️ **为什么用静态检查**
        「读比写宽」是**结构性**问题：
        逐个端点测只能覆盖已知的那些；
        ⇒ 用 ``ast`` 扫全模块，锁住「GET 的鉴权函数 == POST 的鉴权函数」。
        """
        import ast
        from pathlib import Path

        import backend.app.routes.pan_workload as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)

        # ⚠️ 取「每个端点函数调用的鉴权函数名」。
        guards: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id in ("_require_admin", "_require_leader")
                ):
                    guards[node.name] = inner.func.id

        # ⚠️ 端点函数名 → 路由 path（**含 router 的 prefix**）。
        prefix = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Name)
                        and tgt.id == "router"
                        and isinstance(node.value, ast.Call)
                    ):
                        for kw in node.value.keywords:
                            if kw.arg == "prefix" and isinstance(
                                kw.value, ast.Constant
                            ):
                                prefix = kw.value.value

        by_path: dict[tuple[str, str], str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                ):
                    continue
                # ⚠️ path 可能是**位置参数**（``@router.get("/bindings")``），
                #    也可能是关键字（``@router.get(path="...")``）⇒ 两种都要认。
                path = None
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    path = dec.args[0].value
                for kw in dec.keywords:
                    if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                        path = kw.value.value
                if path:
                    # ⚠️ **必须带 method** —— GET 与 POST 同路径
                    #    （``/bindings`` 两个都有）⇒ 只按 path 建 dict
                    #    会互相覆盖 ⇒ 只能留下最后一个。
                    by_path[(dec.func.attr, f"{prefix}{path}")] = node.name

        bindings_get = by_path.get(("get", "/v1/baidu/workload/bindings"))
        assert bindings_get == "list_bindings", (
            f"找不到 GET /bindings 的处理函数（找到 {bindings_get!r}）；"
            f"本模块的路由表：{sorted(by_path)}"
        )
        assert guards.get(bindings_get) == "_require_admin", (
            f"GET /bindings 用的是 {guards.get(bindings_get)!r}，"
            f"应与 POST /bindings 一致用 '_require_admin'"
        )

        post_fn = by_path.get(("post", "/v1/baidu/workload/bindings"))
        assert post_fn == "create_binding", f"POST /bindings 的函数是 {post_fn!r}"
        assert guards.get(post_fn) == "_require_admin"

    def test_其他配置端点仍是管理员(self):
        """⚠️ **配对** —— 确认没有把别的端点误改。

        ⇒ ``POST /collect`` / ``POST /aggregate`` 一直是 ``_require_admin``
        ⇒ B3 只动 ``GET /bindings``，不应波及其他。
        """
        import ast
        from pathlib import Path

        import backend.app.routes.pan_workload as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)

        def _guard_of(func_name: str) -> str | None:
            node = next(
                (
                    n
                    for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == func_name
                ),
                None,
            )
            if node is None:
                return None
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id in ("_require_admin", "_require_leader")
                ):
                    return inner.func.id
            return None

        for fn in ("create_binding", "trigger_collect", "aggregate"):
            assert _guard_of(fn) == "_require_admin", (
                f"{fn} 的鉴权变成了 {_guard_of(fn)!r} ⇒ 配置动作被放宽了"
            )
