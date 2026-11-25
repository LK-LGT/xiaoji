"""★ **动态板块菜单**（2026-11-24 用户诉求）。

⚠️ **用户原话**
------------
「在【部门功能板块】这个配置菜单里做配置：
  1. 新增板块：短视频部
  2. 在短视频板块下面，挂载子模块：**剪辑工作量**
  3. 侧边栏菜单**不是写死固定**，是**动态渲染**：
     - 系统读取当前登录人的部门；
     - 读取这个部门绑定了哪些功能子模块；
     - 只有这个部门的人员，侧边栏才会加载出【短视频 → 剪辑工作量】；
     - 其他部门的账号，侧边栏**根本不会出现这个入口**」

⚠️ **覆盖**
--------
1. 板块 → 子模块的目录结构
2. 岗位角色过滤（reporter / reviewer / none）
3. 权限过滤
4. 负责人兜底（管辖树里有部门开通 ⇒ reviewer）
5. HR / 管理员豁免
6. 空分组剔除
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Department, Employee, HrDepartmentConfig
from backend.app.domain.pan_workload_models import DepartmentModule, PanWorkloadPolicy


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
    """真实组织片段 —— 短视频挂在运营中心下面。"""
    d_center = Department(id="D_CENTER", name="运营中心", active=True)
    d_video = Department(id="D_VIDEO", name="短视频", active=True)
    d_admin = Department(id="D_ADMIN", name="总经办", active=True)
    d_hr = Department(id="D_HR", name="人事行政部", active=True)
    db.add_all([d_center, d_video, d_admin, d_hr])

    db.add_all([
        Employee(id="E_LCY", name="李婵雨", department_id="D_VIDEO", position="剪辑",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_ZJ", name="张洁", department_id="D_VIDEO", position="编导",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_LX", name="刘旭", department_id="D_VIDEO", position="运营",
                 employment_status="active", account_status="enabled"),
        # ⚠️ 主播 —— 业务口径是「不参与」
        Employee(id="E_ZWY", name="巫宜珊", department_id="D_VIDEO", position="主播",
                 employment_status="active", account_status="enabled"),
        # ⚠️ 上级部门负责人（杨飞的位置）—— 管着运营中心
        Employee(id="E_YF", name="杨飞", department_id="D_ADMIN", position="总经理",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_HR", name="HR", department_id="D_HR", position="人事主管",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_OTHER", name="外部门员工", department_id="D_CENTER",
                 position="员工", employment_status="active", account_status="enabled"),
    ])
    db.flush()

    d_video.parent_id = "D_CENTER"
    d_center.leader_id = "E_YF"
    d_video.leader_id = "E_LX"
    d_hr.leader_id = "E_HR"
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()


@pytest.fixture()
def module_grant(db):
    """★ **只有短视频开通了「剪辑工作量」板块** —— 与真实库一致。"""
    db.add(DepartmentModule(department_id="D_VIDEO", module_code="pan_workload",
                            active=True))
    db.commit()


@pytest.fixture()
def policies(db):
    """★ **部门 × 岗位 口径** —— 与真实库一致。

    剪辑 → reporter（要填报）
    编导 → reviewer（只审）
    运营 → reviewer（只审）
    ⚠️ **主播没配行** ⇒ 不参与
    """
    db.add_all([
        PanWorkloadPolicy(department_id="D_VIDEO", position="剪辑", role="reporter",
                          metric_codes="video_normal_count,video_ootd_count,video_agent_count",
                          active=True),
        PanWorkloadPolicy(department_id="D_VIDEO", position="编导", role="reviewer",
                          metric_codes="", active=True),
        PanWorkloadPolicy(department_id="D_VIDEO", position="运营", role="reviewer",
                          metric_codes="", active=True),
    ])
    db.commit()


class TestModuleMenuCatalog:
    def test_子模块注册表挂在板块下面(self):
        """⚠️ **核心结构断言** —— 板块 → 子模块两级。

        ⚠️ **2026-11-25：新增了「部门工作量」板块**
        ⇒ 断言改成**通用规则**（每个子模块都要挂在一个**已注册**的板块下），
           而不是写死「所有子模块都属于 pan_workload」。
        ⇒ 这样以后再加板块**不用改这个测试**。
        """
        from backend.app.services.department_modules import (
            KNOWN_MODULES,
            SUB_MODULES,
            sub_modules_for,
        )

        assert "pan_workload" in {code for code, _ in KNOWN_MODULES}

        specs = sub_modules_for("pan_workload")
        assert len(specs) == 1
        assert specs[0].code == "pan_workload"
        assert specs[0].path == "/performance/pan-workload"
        assert specs[0].permission == "pan:workload:view"

        # ⚠️ **通用规则** —— 每个子模块都必须挂在**已注册**的板块下。
        known = {code for code, _ in KNOWN_MODULES}
        for spec in SUB_MODULES:
            assert spec.module_code in known, (
                f"子模块 {spec.code} 挂在未注册板块 {spec.module_code} 上"
            )
            # ⚠️ **反向可查** —— ``sub_modules_for`` 必须能查到它。
            assert spec in sub_modules_for(spec.module_code)

    def test_未知板块没有子模块(self):
        from backend.app.services.department_modules import sub_modules_for

        assert sub_modules_for("not_a_real_module") == ()


class TestModuleMenuVisibility:
    def test_剪辑看到_reporter角色(self, db, org, module_grant, policies):
        """⚠️ **肯定式断言** —— 剪辑必须能看到入口。"""
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_LCY")
        menu = build_module_menu(db, emp, ["pan:workload:view"])
        assert len(menu) == 1
        assert menu[0]["code"] == "pan_workload"
        assert menu[0]["role"] == "reporter"
        assert menu[0]["children"][0]["path"] == "/performance/pan-workload"

    def test_编导与运营看到_reviewer角色(self, db, org, module_grant, policies):
        from backend.app.services.department_modules import build_module_menu

        for eid in ("E_ZJ", "E_LX"):
            emp = db.get(Employee, eid)
            menu = build_module_menu(db, emp, ["pan:workload:view"])
            assert len(menu) == 1, eid
            assert menu[0]["role"] == "reviewer", eid

    def test_主播看不到入口(self, db, org, module_grant, policies):
        """⚠️⚠️ **核心断言** —— 这就是「看得见点进去空数据」的解药。

        主播在**已开通板块的部门**，但岗位口径是「不参与」
        ⇒ 侧边栏**根本不出现**「剪辑工作量」。
        """
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_ZWY")
        menu = build_module_menu(db, emp, ["pan:workload:view"])
        assert menu == [], "主播（不参与）不该看到任何板块入口"

    def test_板块没开通则看不到(self, db, org, policies):
        """⚠️ **默认关闭** —— 没有配置行 = 谁都没有。"""
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_LCY")
        assert build_module_menu(db, emp, ["pan:workload:view"]) == []

    def test_外部门员工看不到(self, db, org, module_grant, policies):
        """⚠️ **隔离不能被破坏**。"""
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_OTHER")
        assert build_module_menu(db, emp, ["pan:workload:view"]) == []

    def test_没有权限则子模块被过滤(self, db, org, module_grant, policies):
        """⚠️ **权限维度** —— 板块开了但没权限 ⇒ 不显示子模块。

        ⇒ 分组因没有可见子模块而被**整组剔除**。
        """
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_LCY")
        assert build_module_menu(db, emp, []) == []


class TestLeaderAndHRExemption:
    def test_负责人兜底_管辖树开通则可见(self, db, org, module_grant, policies):
        """⚠️⚠️ **业务口径（2026-11-24）** —— 负责人必须能看到。

        杨飞坐在总经办，但管着运营中心（短视频的父部门）
        ⇒ 他在该板块里**没有岗位** ⇒ 必须由「负责人兜底」规则捞回来。
        """
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_YF")
        menu = build_module_menu(db, emp, ["pan:workload:view"])
        assert len(menu) == 1
        assert menu[0]["role"] == "reviewer"

    def test_负责人看不到未开通的板块(self, db, org, policies):
        """⚠️ **兜底规则也要受板块开关约束**。"""
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_YF")
        assert build_module_menu(db, emp, ["pan:workload:view"]) == []

    def test_HR豁免_恒可见(self, db, org, module_grant, policies):
        """⚠️ **HR / 管理员恒可见** —— 他们要配置板块、核对全局数据。

        ⚠️ **2026-11-25：断言改成「按板块查」而不是「数个数」**
        ⇒ 新增「部门工作量」板块后，HR 会看到**两个**入口（这是对的）
        ⇒ 数个数会随板块增加而**假失败** ⇒ 改成查具体板块。
        """
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_HR")
        menu = build_module_menu(db, emp, ["pan:workload:view"])
        by_code = {m["code"]: m for m in menu}
        assert "pan_workload" in by_code, "HR 必须能看到剪辑工作量"
        assert by_code["pan_workload"]["role"] == "reviewer"

    def test_HR即使没有岗位口径也能看到(self, db, org, module_grant):
        """⚠️ **HR 在人事行政部** —— 该部门没有任何岗位口径
        ⇒ 靠 HR 豁免规则保底，不会被「没有岗位口径」挡掉。
        """
        from backend.app.services.department_modules import build_module_menu

        emp = db.get(Employee, "E_HR")
        codes = {m["code"] for m in build_module_menu(db, emp, ["pan:workload:view"])}
        assert "pan_workload" in codes


class TestModuleRoleResolution:
    def test_角色解析(self, db, org, module_grant, policies):
        """⚠️ **角色是板块与岗位的交集**。"""
        from backend.app.services.department_modules import module_role_for_employee

        cases = {
            "E_LCY": "reporter",
            "E_ZJ": "reviewer",
            "E_LX": "reviewer",
            "E_ZWY": "none",
            "E_HR": "reviewer",
        }
        for eid, expected in cases.items():
            emp = db.get(Employee, eid)
            actual = module_role_for_employee(db, emp, "pan_workload")
            assert actual == expected, f"{eid} 期望 {expected}，实际 {actual}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
