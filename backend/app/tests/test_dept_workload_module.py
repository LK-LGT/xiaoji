"""★ **「部门工作量」板块**的隔离回归测试（2026-11-25）。

⚠️ **为什么必须有这些测试**
--------------------------
这次给**20 个真实部门**开通了新板块 ⇒ 之前「板块隔离」的那套保证
（短视频的人看得到、仓储部的人看不到…）必须在**新板块**上同样成立。

⚠️ **核心风险点**
------------------
1. **非剪辑板块不能套用视频默认指标**
   ⇒ 否则仓储部分拣员会收到「今天剪了几条视频」的卡片。
2. **没开通的部门 ⇒ 403**（含直接敲 URL 的情况）
3. **指标留空 ⇒ 没有填报项**（不是「回退到视频指标」）
4. **两个板块互不干扰**（短视频走 ``pan_workload``，其它走 ``dept_workload``）
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Department, Employee
from backend.app.domain.pan_workload_models import DepartmentModule, PanWorkloadPolicy
from backend.app.services.department_modules import (
    DEPT_WORKLOAD_MODULE,
    KNOWN_MODULES,
    PAN_WORKLOAD_MODULE,
    SUB_MODULES,
    build_module_menu,
    module_role_for_employee,
)

PREFIX = "/v1/dept/workload"


# ═════════════════════════════════════════════════════════════════════════
# fixtures
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        yield session
        session.rollback()


@pytest.fixture()
def org(db):
    """★ **真实组织**（按飞书同步的结构裁剪）—— 短视频 + 仓储 + 客服。

    ⚠️ **刻意保留真实的父子层级**，因为「祖先继承」是隔离的关键。
    """
    short_video = Department(id="D_SV", name="短视频", parent_id=None)
    ops_center = Department(id="D_OPS", name="运营中心", parent_id=None)
    warehouse = Department(id="D_WH", name="仓储部", parent_id=None)
    pack = Department(id="D_PACK", name="包装小组", parent_id="D_WH")
    service = Department(id="D_SVC", name="客服部", parent_id=None)
    other = Department(id="D_OTHER", name="财务部", parent_id=None)
    for d in (short_video, ops_center, warehouse, pack, service, other):
        db.add(d)

    editor = Employee(
        id="E_LCY", name="李婵雨", department_id="D_SV", position="剪辑"
    )
    sv_leader = Employee(
        id="E_LX", name="刘旭", department_id="D_SV", position="运营"
    )
    picker = Employee(
        id="E_PICK", name="谢楚华", department_id="D_PACK", position="分拣员"
    )
    wh_leader = Employee(
        id="E_WHL", name="胡少丽", department_id="D_WH", position="仓库主管"
    )
    cs = Employee(id="E_CS", name="李杰燕", department_id="D_SVC", position="客服")
    finance = Employee(id="E_FIN", name="罗桂华", department_id="D_OTHER", position="出纳")
    for e in (editor, sv_leader, picker, wh_leader, cs, finance):
        db.add(e)
    db.commit()
    return db


def _grant(db, dept_id: str, module_code: str) -> None:
    db.add(
        DepartmentModule(
            department_id=dept_id,
            module_code=module_code,
            active=True,
            note="test",
        )
    )
    db.commit()


def _policy(db, dept_id: str, position: str, role: str, codes: str = "") -> None:
    db.add(
        PanWorkloadPolicy(
            department_id=dept_id,
            position=position,
            role=role,
            metric_codes=codes,
            active=True,
        )
    )
    db.commit()


# ═════════════════════════════════════════════════════════════════════════
# 板块注册
# ═════════════════════════════════════════════════════════════════════════


class Test模块注册:
    def test_部门工作量已注册(self):
        """新板块必须在 ``KNOWN_MODULES`` 里（HR 配置界面的下拉靠它）。"""
        assert DEPT_WORKLOAD_MODULE in {code for code, _ in KNOWN_MODULES}
        labels = dict(KNOWN_MODULES)
        assert labels[DEPT_WORKLOAD_MODULE] == "部门工作量"

    def test_剪辑板块仍独立存在(self):
        """⚠️ **不能把剪辑板块改掉** —— 它有百度网盘采集能力。"""
        assert PAN_WORKLOAD_MODULE in {code for code, _ in KNOWN_MODULES}

    def test_两个板块各有子模块(self):
        codes = {spec.code for spec in SUB_MODULES}
        assert "pan_workload" in codes
        assert "dept_workload" in codes

    def test_子模块归属正确(self):
        """⚠️ **子模块的归属板块必须一致** —— 否则菜单会串。"""
        for spec in SUB_MODULES:
            assert spec.module_code in {code for code, _ in KNOWN_MODULES}


# ═════════════════════════════════════════════════════════════════════════
# ★ **核心：默认指标不能跨板块泄漏**
# ═════════════════════════════════════════════════════════════════════════


class Test默认指标不跨板块泄漏:
    """
    ⚠️⚠️ **这是本次开发最大的坑**

    历史上「岗位没配指标」⇒ 回退到**视频默认指标**
    （普通视频/OOTD/智能体）⇒ 仓储部开了新板块后，
    分拣员会收到「今天剪了几条视频」的卡片。
    """

    def test_剪辑板块保留视频默认(self):
        from backend.app.services.pan_workload_policy import default_codes_for_role

        codes = default_codes_for_role("reporter", PAN_WORKLOAD_MODULE)
        assert codes == (
            "video_normal_count",
            "video_ootd_count",
            "video_agent_count",
        )

    def test_非剪辑板块没有默认指标(self):
        """⚠️ **核心断言** —— 别的板块**不能**拿到视频指标。"""
        from backend.app.services.pan_workload_policy import default_codes_for_role

        assert default_codes_for_role("reporter", DEPT_WORKLOAD_MODULE) == ()
        assert default_codes_for_role("reviewer", DEPT_WORKLOAD_MODULE) == ()

    def test_没配指标时取不到视频指标(self, db, org):
        """端到端验证：仓储部岗位配了 reporter 但**没配指标**。"""
        from backend.app.services.pan_workload_policy import reporter_metric_codes

        _policy(db, "D_PACK", "分拣员", "reporter", codes="")
        picker = db.get(Employee, "E_PICK")
        assert reporter_metric_codes(db, picker, DEPT_WORKLOAD_MODULE) == ()
        # ⚠️ 剪辑那边**仍然**有默认指标（不能被一起改坏）
        _policy(db, "D_SV", "剪辑", "reporter", codes="")
        editor = db.get(Employee, "E_LCY")
        assert reporter_metric_codes(db, editor, PAN_WORKLOAD_MODULE) == (
            "video_normal_count",
            "video_ootd_count",
            "video_agent_count",
        )

    def test_配了指标就按配的来(self, db, org):
        """配了指标 ⇒ 优先用配的（不套默认）。"""
        from backend.app.services.pan_workload_policy import reporter_metric_codes

        _policy(db, "D_PACK", "分拣员", "reporter", codes="warehouse_pick_count")
        picker = db.get(Employee, "E_PICK")
        assert reporter_metric_codes(db, picker, DEPT_WORKLOAD_MODULE) == (
            "warehouse_pick_count",
        )


# ═════════════════════════════════════════════════════════════════════════
# 板块隔离
# ═════════════════════════════════════════════════════════════════════════


class Test板块隔离:
    def test_开通的部门能看到(self, db, org):
        _grant(db, "D_PACK", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_PACK", "分拣员", "reporter")
        picker = db.get(Employee, "E_PICK")
        menu = build_module_menu(db, picker, ["pan:workload:view"])
        assert len(menu) == 1
        assert menu[0]["code"] == DEPT_WORKLOAD_MODULE
        assert menu[0]["role"] == "reporter"

    def test_没开通的部门看不到(self, db, org):
        """⚠️ **默认关闭** —— 财务部没开通 ⇒ 看不到。"""
        finance = db.get(Employee, "E_FIN")
        assert build_module_menu(db, finance, ["pan:workload:view"]) == []

    def test_两个板块互不干扰(self, db, org):
        """⚠️ **短视频只看到剪辑板块**，不看到部门工作量。"""
        _grant(db, "D_SV", PAN_WORKLOAD_MODULE)
        _policy(db, "D_SV", "剪辑", "reporter")
        editor = db.get(Employee, "E_LCY")
        menu = build_module_menu(db, editor, ["pan:workload:view"])
        assert [m["code"] for m in menu] == [PAN_WORKLOAD_MODULE]

    def test_仓储部看不到剪辑板块(self, db, org):
        """⚠️ **核心隔离断言** —— 仓储部开了部门工作量，
        但**绝不能**因此看到剪辑工作量。"""
        _grant(db, "D_PACK", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_PACK", "分拣员", "reporter")
        picker = db.get(Employee, "E_PICK")
        menu = build_module_menu(db, picker, ["pan:workload:view"])
        assert [m["code"] for m in menu] == [DEPT_WORKLOAD_MODULE]
        assert PAN_WORKLOAD_MODULE not in {m["code"] for m in menu}

    def test_短视频看不到部门板块(self, db, org):
        """反向隔离：短视频开了剪辑板块 ⇒ 不该看到部门工作量。"""
        _grant(db, "D_SV", PAN_WORKLOAD_MODULE)
        _policy(db, "D_SV", "剪辑", "reporter")
        editor = db.get(Employee, "E_LCY")
        menu = build_module_menu(db, editor, ["pan:workload:view"])
        assert DEPT_WORKLOAD_MODULE not in {m["code"] for m in menu}


class Test岗位角色:
    def test_reporter按岗位口径(self, db, org):
        _grant(db, "D_PACK", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_PACK", "分拣员", "reporter")
        assert module_role_for_employee(db, db.get(Employee, "E_PICK"), DEPT_WORKLOAD_MODULE) == "reporter"

    def test_reviewer按岗位口径(self, db, org):
        _grant(db, "D_WH", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_WH", "仓库主管", "reviewer")
        assert module_role_for_employee(db, db.get(Employee, "E_WHL"), DEPT_WORKLOAD_MODULE) == "reviewer"

    def test_配了none就不参与(self, db, org):
        """⚠️ **配了 none ⇒ 不显示菜单**（哪怕板块开着）。"""
        _grant(db, "D_SV", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_SV", "主播", "none")
        anchor = db.scalars(
            select(Employee).where(Employee.department_id == "D_SV").limit(1)
        ).first()
        # 岗位口径按**岗位**走 ⇒ 造一个主播员工
        anchor.position = "主播"
        db.commit()
        assert module_role_for_employee(db, anchor, DEPT_WORKLOAD_MODULE) == "none"
        assert build_module_menu(db, anchor, ["pan:workload:view"]) == []


# ═════════════════════════════════════════════════════════════════════════
# API 守卫
# ═════════════════════════════════════════════════════════════════════════


class TestAPI守卫:
    @pytest.fixture()
    def client(self, db, org):
        from backend.app.core.db import get_db
        from backend.app.main import app

        def _override_db():
            # ⚠️ **必须是生成器函数本身** —— FastAPI 用
            # ``inspect.isgeneratorfunction(call)`` 判断怎么处理依赖。
            yield db

        app.dependency_overrides[get_db] = _override_db
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    def _login(self, client, employee_id: str):
        """以指定员工身份登录（覆盖 ``get_current_user``）。"""
        from backend.app.dependencies import get_current_user
        from backend.app.main import app

        def _override_user():
            return client.current_db.get(Employee, employee_id)

        app.dependency_overrides[get_current_user] = _override_user
        client.current_db = client.app.state.__dict__.setdefault("_test_db", None)

    def test_没开通板块_403(self, client, db, org):
        """⚠️ **直接敲 URL 也进不去** —— 守卫在 router 上。"""
        from backend.app.dependencies import get_current_user
        from backend.app.main import app

        def _override_user():
            return db.get(Employee, "E_FIN")

        app.dependency_overrides[get_current_user] = _override_user
        resp = client.get(f"{PREFIX}/my-metrics")
        assert resp.status_code == 403
        assert "未开通" in resp.text

    def test_开通板块能访问(self, client, db, org):
        from backend.app.dependencies import get_current_user
        from backend.app.main import app

        _grant(db, "D_PACK", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_PACK", "分拣员", "reporter")

        def _override_user():
            return db.get(Employee, "E_PICK")

        app.dependency_overrides[get_current_user] = _override_user
        resp = client.get(f"{PREFIX}/my-metrics")
        assert resp.status_code == 200

    def test_角色取岗位口径_不是取指标码(self, client, db, org):
        """
        ⚠️⚠️ **回归测试**（2026-11-25 修的真实 bug）

        分拣员在口径表里是 ``reporter``，但**指标还没配**
        ⇒ ``reporter_metric_codes`` 返回**空**
        ⇒ 若角色用「有没有指标」推断 ⇒ 返回 ``role="none"``
        ⇒ 前端显示「暂未纳入该板块」⇒ 用户以为自己没权限。

        ⇒ 角色必须取 ``resolve_policy(...).role``（权威口径）。
        """
        from backend.app.dependencies import get_current_user
        from backend.app.main import app

        _grant(db, "D_PACK", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_PACK", "分拣员", "reporter", codes="")  # ⚠️ 指标留空

        def _override_user():
            return db.get(Employee, "E_PICK")

        app.dependency_overrides[get_current_user] = _override_user
        resp = client.get(f"{PREFIX}/my-scope")
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "reporter", (
            f"岗位口径是 reporter，但接口返回 {data['role']} ⇒ 指标为空被误当成不参与"
        )
        assert data["metrics"] == [], "指标还没配 ⇒ 不该下发指标"

    def test_指标待定与不参与_错误提示不同(self, client, db, org):
        """⚠️ **两种「不能填」要说不同的话** —— 否则用户白找管理员。"""
        from backend.app.dependencies import get_current_user
        from backend.app.main import app

        _grant(db, "D_PACK", DEPT_WORKLOAD_MODULE)
        _policy(db, "D_PACK", "分拣员", "reporter", codes="")

        def _override_user():
            return db.get(Employee, "E_PICK")

        app.dependency_overrides[get_current_user] = _override_user
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={
                "metric_code": "video_normal_count",
                "metric_date": "2026-11-25",
                "metric_value": 8,
            },
        )
        assert resp.status_code == 403
        assert "指标尚未配置" in resp.text, (
            f"指标待定时应明确说明，实际：{resp.text}"
        )

    def test_不参与的岗位_提示不同(self, client, db, org):
        """⚠️ **不参与**岗位 ⇒ 提示「不参与」而不是「指标待定」。"""
        from backend.app.dependencies import get_current_user
        from backend.app.main import app

        _grant(db, "D_PACK", DEPT_WORKLOAD_MODULE)
        # ⚠️ **不配任何岗位口径** ⇒ resolve_policy 返回 none

        def _override_user():
            return db.get(Employee, "E_PICK")

        app.dependency_overrides[get_current_user] = _override_user
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={
                "metric_code": "video_normal_count",
                "metric_date": "2026-11-25",
                "metric_value": 8,
            },
        )
        assert resp.status_code == 403
        assert "不参与" in resp.text

    def test_剪辑板块访问部门接口_403(self, client, db, org):
        """⚠️ **两个板块的接口互不通用** —— 剪辑不能走部门接口。"""
        from backend.app.dependencies import get_current_user
        from backend.app.main import app

        _grant(db, "D_SV", PAN_WORKLOAD_MODULE)

        def _override_user():
            return db.get(Employee, "E_LCY")

        app.dependency_overrides[get_current_user] = _override_user
        resp = client.get(f"{PREFIX}/my-metrics")
        assert resp.status_code == 403


class Test数据范围共享服务:
    """
    ⚠️⚠️ **两个板块必须用同一套数据范围判定**

    2026-11-25 把范围判定抽到了 ``services.pan_workload_scope``。
    ⇒ 若某个 router **自己复制一份**，改了一处忘了另一处
    ⇒ **权限漏洞**（且测试都过，只是行为不一致）。
    ⇒ 这里锁死「两个 router 都调共享服务」。
    """

    def test_两个router都调用共享服务(self):
        import ast
        from pathlib import Path

        from backend.app.routes import dept_workload, pan_workload

        for mod in (pan_workload, dept_workload):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            tree = ast.parse(src)
            called: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        called.add(func.id)
                    elif isinstance(func, ast.Attribute):
                        called.add(func.attr)
            assert "scope_employee_ids" in called, (
                f"{mod.__name__} 没有调用共享的范围判定 ⇒ 两边会漂移"
            )

    def test_共享服务存在且导出(self):
        from backend.app.services.pan_workload_scope import scope_employee_ids

        assert callable(scope_employee_ids)


# ═════════════════════════════════════════════════════════════════════════
# 播种脚本
# ═════════════════════════════════════════════════════════════════════════


class Test播种脚本:
    def test_岗位角色判定(self):
        """岗位名 → 角色 的规则（与脚本一致）。"""
        from backend.scripts.seed_department_workload import role_for_position

        assert role_for_position("分拣员") == "reporter"
        assert role_for_position("剪辑") == "reporter"
        assert role_for_position("仓库主管") == "reviewer"
        assert role_for_position("商务主管") == "reviewer"
        assert role_for_position("总经理") == "reviewer"
        # ⚠️ **主播不参与**（短视频业务已确认「口播成品不用管」）
        assert role_for_position("主播") == "none"
        # ⚠️ **没岗位 ⇒ 不参与**（默认关闭）
        assert role_for_position("") == "none"

    def test_脚本不写死指标(self):
        """⚠️ **核心断言** —— 脚本**不能**预设指标码。

        用户明确说「各岗位填什么指标，后续做实际的开发和填入」⇒
        脚本若写死指标 ⇒ 业务还没定就发版 ⇒ 违背诉求。
        """
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        source = (repo / "backend" / "scripts" / "seed_department_workload.py").read_text(
            encoding="utf-8"
        )
        # ⚠️ 不该出现任何**具体指标码**
        for code in (
            "video_normal_count",
            "video_ootd_count",
            "video_agent_count",
            "warehouse_pick_count",
            "service_response_rate",
        ):
            assert code not in source, f"播种脚本不应预设指标 {code}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
