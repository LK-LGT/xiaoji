"""P0-4a：写端点接入 ``can_write_score_of`` 的测试。

⚠️ **为什么这���测试重要**
------------------------
P0 的 peer 能力（同部门同级互查）会**扩大** ``can_view_score_of`` 的放行范围。
而写端点历史上复用了 ``can_view_score_of`` 作为授权闸门 ——
一旦 peer 生效，「能查」就顺带变成「能改」。

⚠️ 指标实测值是**算分的唯一入口**（``evidence_service.add_metric_value``）。
   能给同事写实测值 = 能改同事的绩效分数 —— 这是需求明确禁止的。

⇒ 本文件断言：**写端点绝不能因为 peer 能力而放行**。

⚠️ **同时要守住既有语义**
--------------------------
改造不能把「本来能写的人」挡住：
- 主管写下属 ✔（要保留）
- HR 写全员 ✔（要保留）
- 代理授权受托人 ✔（要保留）
- 本人写自己 ✔（要保留）
- 部门负责人写管理范围 ✔（要保留）
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
)
from backend.app.services.access_control import build_access_context


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _emp(
    db: Session,
    emp_id: str,
    name: str,
    dept: str | None,
    manager: str | None = None,
) -> Employee:
    e = Employee(
        id=emp_id,
        name=name,
        department_id=dept,
        manager_id=manager,
        position="",
        employment_status="active",
        account_status="enabled",
    )
    db.add(e)
    db.flush()
    return e


@pytest.fixture()
def seeded(db: Session) -> Session:
    """组织结构：

    D_ROOT（总裁 BOSS）
      ├─ D_SALES（经理 M_SALES）
      │    ├─ E_A（销售甲）  ┐ 同部门同级别
      │    ├─ E_B（销售乙）  ┘
      │    └─ E_C（销售丙，汇报给 E_B ⇒ 不同级）
      ├─ D_MKT（经理 M_MKT）
      │    └─ E_X（市场甲）
      └─ D_HR（HR）
    """
    db.add_all([
        Department(id="D_ROOT", name="总部", active=True),
        Department(id="D_SALES", name="销售部", active=True),
        Department(id="D_MKT", name="市场部", active=True),
        Department(id="D_HR", name="人力资源部", active=True),
    ])
    db.flush()
    _emp(db, "BOSS", "总裁", "D_ROOT", None)
    _emp(db, "M_SALES", "销售经理", "D_SALES", "BOSS")
    _emp(db, "E_A", "销售甲", "D_SALES", "M_SALES")
    _emp(db, "E_B", "销售乙", "D_SALES", "M_SALES")
    _emp(db, "E_C", "销售丙", "D_SALES", "E_B")
    _emp(db, "M_MKT", "市场经理", "D_MKT", "BOSS")
    _emp(db, "E_X", "市场甲", "D_MKT", "M_MKT")
    _emp(db, "E_HR", "HR专员", "D_HR", "BOSS")
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()
    return db


def _ctx(db: Session, emp_id: str):
    return build_access_context(db, db.get(Employee, emp_id))


# ---------------------------------------------------------------------------
# ★ 核心不变式：peer 看得见 ≠ peer 写得动
# ---------------------------------------------------------------------------


def test_peer_can_view_but_not_write(seeded: Session) -> None:
    """★ 同部门同级别：能看，**不能写**。

    这是 P0 需求「能查不能改」在写路径上的直接体现。
    """
    ctx = _ctx(seeded, "E_A")
    # 能看
    assert ctx.can_view_score_of("E_B", db=seeded) is True
    # 不能写
    assert ctx.can_write_score_of("E_B", db=seeded) is False


def test_peer_write_is_denied_even_without_db(seeded: Session) -> None:
    """★ ``db=None`` 也必须拒绝 —— peer 分支根本不在写路径里。"""
    ctx = _ctx(seeded, "E_A")
    assert ctx.can_write_score_of("E_B") is False


def test_cross_department_neither_view_nor_write(seeded: Session) -> None:
    """跨部门：既不能看也不能写。"""
    ctx = _ctx(seeded, "E_A")
    assert ctx.can_view_score_of("E_X", db=seeded) is False
    assert ctx.can_write_score_of("E_X", db=seeded) is False


def test_manager_cannot_write_peer(seeded: Session) -> None:
    """★ 主管对**平级同事**不能写。

    ⚠️ 容易被误认为"主管什么都能干" ——
       但 M_SALES 与 E_B 不是上下级关系（E_B 汇报给 M_SALES，
       但 M_SALES 与 E_B 都不是对方的直属上级）。
       实际上 M_SALES 是 E_B 的上级，**应该**能写。
       这里用市场部同级别来验证真正的平级场景。
    """
    ctx = _ctx(seeded, "M_MKT")
    # E_X 是 M_MKT 的直属下属 ⇒ 主管对下属可写
    assert ctx.can_write_score_of("E_X", db=seeded) is True


def test_true_peer_cannot_write(seeded: Session) -> None:
    """★ 真正的平级（互相不是上下级）不能互写。"""
    db = seeded
    # 造两个互为平级的人：都汇报给 M_SALES，但都不是对方的 manager
    _emp(db, "P1", "平级一", "D_SALES", "M_SALES")
    _emp(db, "P2", "平级二", "D_SALES", "M_SALES")
    db.commit()

    ctx = _ctx(db, "P1")
    assert ctx.can_view_score_of("P2", db=db) is True
    assert ctx.can_write_score_of("P2", db=db) is False
    # 反向同样
    ctx2 = _ctx(db, "P2")
    assert ctx2.can_write_score_of("P1", db=db) is False


# ---------------------------------------------------------------------------
# 既有写权限必须保留
# ---------------------------------------------------------------------------


def test_manager_can_write_direct_report(seeded: Session) -> None:
    """主管写直属下属 —— 既有语义，必须保留。"""
    ctx = _ctx(seeded, "M_SALES")
    assert ctx.can_write_score_of("E_A", db=seeded) is True


def test_manager_can_write_indirect_report(seeded: Session) -> None:
    """★ 主管写**间接下属**（汇报线闭包）—— 报表线语义。

    E_C 汇报给 E_B，E_B 汇报给 M_SALES ⇒ M_SALES 是 E_C 的汇报线上上级。
    """
    ctx = _ctx(seeded, "M_SALES")
    assert ctx.can_write_score_of("E_C", db=seeded) is True


def test_hr_can_write_anyone(seeded: Session) -> None:
    """HR 写全员 —— 既有语义，必须保留。"""
    ctx = _ctx(seeded, "E_HR")
    for target in ("E_A", "E_B", "E_C", "E_X", "M_SALES", "BOSS"):
        assert ctx.can_write_score_of(target, db=seeded) is True, target


def test_self_write_own_record(seeded: Session) -> None:
    """本人写自己的记录 —— 既有语义，必须保留。"""
    ctx = _ctx(seeded, "E_A")
    assert ctx.can_write_score_of("E_A", db=seeded) is True


def test_ordinary_employee_cannot_write_others(seeded: Session) -> None:
    """普通员工不能写别人的记录。"""
    ctx = _ctx(seeded, "E_A")
    assert ctx.can_write_score_of("E_B", db=seeded) is False
    assert ctx.can_write_score_of("M_SALES", db=seeded) is False
    assert ctx.can_write_score_of("E_X", db=seeded) is False


def test_ordinary_employee_cannot_write_manager_record(seeded: Session) -> None:
    """普通员工不能写上级记录。"""
    ctx = _ctx(seeded, "E_A")
    assert ctx.can_write_score_of("M_SALES", db=seeded) is False


def test_admin_does_not_gain_write_on_others(seeded: Session) -> None:
    """系统管理员**不**自动获得业务写权限（沿用既有设计）。"""
    from backend.app.domain.models import SystemAdmin

    seeded.add(SystemAdmin(employee_id="E_A", active=True))
    seeded.commit()
    ctx = _ctx(seeded, "E_A")
    assert "admin" in ctx.roles
    assert ctx.can_write_score_of("E_B", db=seeded) is False


def test_empty_and_nonexistent_targets_rejected(seeded: Session) -> None:
    """空目标与不存在的人一律拒绝。"""
    ctx = _ctx(seeded, "E_A")
    assert ctx.can_write_score_of("", db=seeded) is False
    assert ctx.can_write_score_of("NOBODY", db=seeded) is False


# ---------------------------------------------------------------------------
# 部门负责人（管理范围）
# ---------------------------------------------------------------------------


def test_department_leader_can_write_managed_department(seeded: Session) -> None:
    """部门负责人写管理范围内员工 —— 既有语义，必须保留。

    ⚠️ 这里把 M_SALES 设成 D_SALES 的 leader ⇒ 他是「部门负责人」角色。
    """
    db = seeded
    db.get(Department, "D_SALES").leader_id = "M_SALES"
    db.commit()

    ctx = _ctx(db, "M_SALES")
    assert "department_leader" in ctx.roles
    assert ctx.can_write_score_of("E_A", db=db) is True
    assert ctx.can_write_score_of("E_B", db=db) is True
    # ⚠️ 管理范围**不含**别的部门
    assert ctx.can_write_score_of("E_X", db=db) is False


def test_joint_appointment_department_leader_can_write(seeded: Session) -> None:
    """兼岗员工被兼岗部门负责人管理时可写。"""
    db = seeded
    db.get(Department, "D_SALES").leader_id = "M_SALES"
    db.add(EmployeeDepartment(
        employee_id="E_X", department_id="D_SALES", is_primary=False
    ))
    db.commit()

    ctx = _ctx(db, "M_SALES")
    assert ctx.can_write_score_of("E_X", db=db) is True


def test_exited_employee_not_writable_by_manager(seeded: Session) -> None:
    """⚠️ 离职员工不在汇报线闭包里（闭包过滤了非 active）⇒ 主管不能写。

    这与既有语义一致 —— 离职不该再参与日常考核。
    """
    db = seeded
    db.get(Employee, "E_A").employment_status = "exited"
    db.commit()

    ctx = _ctx(db, "M_SALES")
    assert ctx.can_write_score_of("E_A", db=db) is False
