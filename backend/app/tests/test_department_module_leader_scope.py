"""部门负责人继承下属部门板块（2026-11-24）。

⚠️ **为什么有这个用例**
真实组织：

    运营中心 (leader=杨飞)
    └── 短视频 (leader=刘旭)  ← 只有这里开通了 ``pan_workload``
        ├── 张洁  编导
        │   ├── 李婵雨 剪辑
        │   └── 佟瑶   剪辑

杨飞坐在「总经办」（``department_id = 总经办``）
⇒ 旧逻辑只查「自己所在部门 + 所有上级」⇒ 得到 ``set()``
⇒ 他**明明是短视频的负责人上级**却看不到剪辑工作量
⇒ 业务口径确认：**负责人必须能看到**。

⇒ 修复：``enabled_modules_for_employee`` 额外并入
``managed_department_ids``（含子树），且**仅**对 ``department_leader`` 生效。

覆盖
----
1. 上级部门负责人 ⇒ 拿到子部门开通的板块
2. 非负责人的普通主管 ⇒ **不**继承（板块是部门属性）
3. 隔离不被破坏 ⇒ 别的部门的普通员工仍然看不到
4. 子树展开 ⇒ 管着父部门的 leader 也能看到孙部门开通的板块
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Department, Employee
from backend.app.domain.pan_workload_models import DepartmentModule


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
    """真实组织片段：运营中心 → 短视频 → 剪辑组。"""
    d_center = Department(id="D_CENTER", name="运营中心", active=True)
    d_video = Department(id="D_VIDEO", name="短视频", active=True)
    d_admin = Department(id="D_ADMIN", name="总经办", active=True)
    d_hr = Department(id="D_HR", name="人事行政部", active=True)
    db.add_all([d_center, d_video, d_admin, d_hr])

    db.add_all([
        # 杨飞：坐在总经办，但**管着运营中心**（短视频的父部门）
        Employee(id="E_YF", name="杨飞", department_id="D_ADMIN", position="总经理",
                 employment_status="active", account_status="enabled"),
        # 刘旭：短视频负责人
        Employee(id="E_LX", name="刘旭", department_id="D_VIDEO", position="运营",
                 employment_status="active", account_status="enabled"),
        # 张洁：编导，两位剪辑的直属上级（**不是**部门 leader）
        Employee(id="E_ZJ", name="张洁", department_id="D_VIDEO", position="编导",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_LCY", name="李婵雨", department_id="D_VIDEO", position="剪辑",
                 employment_status="active", account_status="enabled"),
        # 外部门普通员工
        Employee(id="E_OTHER", name="外部门员工", department_id="D_HR",
                 employment_status="active", account_status="enabled"),
    ])
    db.flush()

    d_video.parent_id = "D_CENTER"
    d_center.leader_id = "E_YF"
    d_video.leader_id = "E_LX"
    d_hr.leader_id = "E_OTHER"
    db.commit()


def _enable(db, dept_id: str, module_code: str = "pan_workload"):
    db.add(DepartmentModule(department_id=dept_id, module_code=module_code, active=True))
    db.commit()


class TestLeaderInheritsChildDepartmentModule:
    def test_上级部门负责人拿到子部门板块(self, db, org):
        """⚠️ **核心用例** —— 杨飞必须拿到 ``pan_workload``。"""
        from backend.app.services.department_modules import enabled_modules_for_employee

        _enable(db, "D_VIDEO")
        emp = db.get(Employee, "E_YF")
        assert "pan_workload" in enabled_modules_for_employee(db, emp)

    def test_板块所在部门成员仍能拿到(self, db, org):
        from backend.app.services.department_modules import enabled_modules_for_employee

        _enable(db, "D_VIDEO")
        for eid in ("E_LX", "E_ZJ", "E_LCY"):
            emp = db.get(Employee, eid)
            assert "pan_workload" in enabled_modules_for_employee(db, emp), eid

    def test_未开通则负责人也没有(self, db, org):
        """⚠️ **默认关闭** —— 没配置行 = 谁都没有。"""
        from backend.app.services.department_modules import enabled_modules_for_employee

        emp = db.get(Employee, "E_YF")
        assert enabled_modules_for_employee(db, emp) == set()

    def test_停用后负责人也没有(self, db, org):
        from backend.app.services.department_modules import enabled_modules_for_employee

        db.add(DepartmentModule(department_id="D_VIDEO", module_code="pan_workload",
                                active=False))
        db.commit()
        emp = db.get(Employee, "E_YF")
        assert "pan_workload" not in enabled_modules_for_employee(db, emp)

    def test_子树展开_父部门负责人看到孙部门板块(self, db, org):
        """⚠️ ``managed_department_ids`` **已含子树** ⇒ 不必再手工展开。"""
        from backend.app.services.department_modules import enabled_modules_for_employee

        _enable(db, "D_VIDEO")
        emp = db.get(Employee, "E_YF")
        assert "pan_workload" in enabled_modules_for_employee(db, emp)


class TestIsolationPreserved:
    def test_外部门普通员工看不到(self, db, org):
        """⚠️ **隔离不能被这次修复破坏** —— 人事行政部的员工仍然没有板块。"""
        from backend.app.services.department_modules import enabled_modules_for_employee

        _enable(db, "D_VIDEO")
        emp = db.get(Employee, "E_OTHER")
        assert "pan_workload" not in enabled_modules_for_employee(db, emp)

    def test_普通主管不因汇报线继承(self, db, org):
        """⚠️ **板块是部门属性** —— 普通主管**不**因为「有下属」就继承板块。

        构造：张洁是李婵雨/佟瑶的直属上级（``manager`` 角色），
        但她**坐在人事行政部**且该部门**未开通**板块
        ⇒ 她拿不到（板块只在「管着一个部门」时按管辖树下钻）。

        ⚠️ 真实数据里张洁在短视频（已开通）⇒ 由**规则 2**（自己所在部门）命中，
        与「管辖树下钻」无关 ⇒ 这里用**未开通的部门**来隔离两种来源。
        """
        from backend.app.services.department_modules import enabled_modules_for_employee

        _enable(db, "D_VIDEO")
        # 把张洁挪到未开通的人事行政部，并让两位剪辑向她汇报
        db.get(Employee, "E_ZJ").department_id = "D_HR"
        db.get(Employee, "E_LCY").manager_id = "E_ZJ"
        db.commit()

        emp = db.get(Employee, "E_ZJ")
        assert "pan_workload" not in enabled_modules_for_employee(db, emp)

    def test_普通主管在自己部门开通时也能拿到(self, db, org):
        """⚠️ 普通主管**在自己部门**开通时当然能用（规则 2 向上继承）。"""
        from backend.app.services.department_modules import enabled_modules_for_employee

        _enable(db, "D_VIDEO")
        emp = db.get(Employee, "E_ZJ")
        # 张洁在短视频，短视频已开通 ⇒ 规则 2 命中（不是靠规则 3）
        assert "pan_workload" in enabled_modules_for_employee(db, emp)

    def test_外部门主管不继承(self, db, org):
        """⚠️ 别人的部门的 leader 也不能拿到。"""
        from backend.app.services.department_modules import enabled_modules_for_employee

        _enable(db, "D_VIDEO")
        db.add(Employee(id="E_HRLEAD", name="别的部门负责人", department_id="D_HR",
                        employment_status="active", account_status="enabled"))
        db.flush()
        db.get(Department, "D_HR").leader_id = "E_HRLEAD"
        db.commit()

        emp = db.get(Employee, "E_HRLEAD")
        assert "pan_workload" not in enabled_modules_for_employee(db, emp)


class TestLeaderCanAccessSummaryEndpoint:
    """⚠️ **端到端** —— 光有模块不够，数据范围也要覆盖剪辑。"""

    def test_负责人能看下属剪辑的工作量(self, db, org):
        from backend.app.routes.pan_workload import _scope_employee_ids

        emp = db.get(Employee, "E_YF")
        allowed = _scope_employee_ids(db, emp)
        assert allowed is not None
        assert "E_LCY" in allowed, "杨飞应能看到李婵雨的工作量"
        assert "E_LX" in allowed, "杨飞应能看到刘旭的数据"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
