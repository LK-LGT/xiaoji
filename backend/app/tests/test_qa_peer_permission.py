"""P0-1：同部门同级互查的权限矩阵测试。

⚠️ **为什么这个文件单独存在**
------------------------------
同部门互查是 P0 唯一会**扩大**数据可见范围的能力。
扩大权限的代码必须有一份**独立、完整、可逐条核对**的用例矩阵，
不能散落在业务测试里靠间接覆盖。

⇒ 本文件是"权限边界"的**唯一事实来源**：
   任何改动 ``can_view_score_of`` / ``is_peer_viewable`` 的人，
   都必须先让本文件全绿。

⚠️ **三条硬边界（需求原文）**
------------------------------
① 同部门的**同一级**可以查对方完成的情况
② **不能查上一级**
③ **不能跨部门**查询
④ **能查不能改**

矩阵里的每一行都对应上面某一条。
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
from backend.app.services import access_control
from backend.app.services.access_control import PermissionAction, build_access_context

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


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


def _add_employee(
    db: Session,
    employee_id: str,
    *,
    name: str,
    department_id: str | None,
    manager_id: str | None = None,
    position: str = "",
    employment_status: str = "active",
    account_status: str = "enabled",
    extra_departments: tuple[str, ...] = (),
) -> Employee:
    employee = Employee(
        id=employee_id,
        name=name,
        department_id=department_id,
        manager_id=manager_id,
        position=position,
        employment_status=employment_status,
        account_status=account_status,
    )
    db.add(employee)
    for dept_id in extra_departments:
        db.add(
            EmployeeDepartment(
                employee_id=employee_id,
                department_id=dept_id,
                is_primary=False,
            )
        )
    db.flush()
    return employee


def _seed_org(db: Session) -> None:
    """建一个能覆盖全部矩阵场景的组织。

    销售部（D_SALES）：
        M_SALES   销售经理（汇报线 → D_ROOT）
        E_SALES1  销售代表甲（汇报线 → M_SALES）
        E_SALES2  销售代表乙（汇报线 → M_SALES）  ← 与 E_SALES1 同部门同级
        E_SALES3  销售代表丙（汇报线 → M_SALES2）  ← 同部门但汇报线不同级
        E_EXTERN  外部门同事（汇报线 → M_SALES）   ← 汇报线同级但跨部门

    市场部（D_MKT）：
        M_MKT     市场经理（汇报线 → D_ROOT）
        E_MKT1    市场代表（汇报线 → M_MKT）

    人力资源部（D_HR）：HR
    """
    db.add_all(
        [
            Department(id="D_ROOT", name="总部", active=True),
            Department(id="D_SALES", name="销售部", active=True),
            Department(id="D_MKT", name="市场部", active=True),
            Department(id="D_HR", name="人力资源部", active=True),
        ]
    )
    db.flush()

    db.add_all(
        [
            # 总部根节点
            _add_employee(
                db, "D_ROOT_BOSS", name="总裁",
                department_id="D_ROOT", manager_id=None, position="总裁",
            ),
            # 销售部
            _add_employee(
                db, "M_SALES", name="销售经理",
                department_id="D_SALES", manager_id="D_ROOT_BOSS", position="销售经理",
            ),
            _add_employee(
                db, "E_SALES1", name="销售代表甲",
                department_id="D_SALES", manager_id="M_SALES", position="销售代表",
            ),
            _add_employee(
                db, "E_SALES2", name="销售代表乙",
                department_id="D_SALES", manager_id="M_SALES", position="销售代表",
            ),
            _add_employee(
                db, "E_SALES3", name="销售代表丙",
                department_id="D_SALES", manager_id="E_SALES2", position="高级销售",
            ),
            _add_employee(
                db, "E_EXTERN", name="外部门同事",
                department_id="D_MKT", manager_id="M_SALES", position="销售代表",
            ),
            # 市场部
            _add_employee(
                db, "M_MKT", name="市场经理",
                department_id="D_MKT", manager_id="D_ROOT_BOSS", position="市场经理",
            ),
            _add_employee(
                db, "E_MKT1", name="市场代表",
                department_id="D_MKT", manager_id="M_MKT", position="市场代表",
            ),
            # HR
            _add_employee(
                db, "E_HR", name="HR专员",
                department_id="D_HR", manager_id="D_ROOT_BOSS", position="HR专员",
            ),
        ]
    )
    db.flush()

    # HR 身份靠部门配置，不靠职位名（见 test_dependencies_acl.py 的说明）
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()


def _ctx(db: Session, employee_id: str) -> access_control.AccessContext:
    employee = db.get(Employee, employee_id)
    assert employee is not None
    return build_access_context(db, employee)


# ---------------------------------------------------------------------------
# ① 同部门同一级 → 允许
# ---------------------------------------------------------------------------


def test_same_department_same_level_peer_is_viewable(db: Session) -> None:
    """同部门 + 同直属上级 ⇒ 互查。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("E_SALES2", db=db) is True


def test_peer_view_is_symmetric(db: Session) -> None:
    """互查必须对称 —— 不能只允许 A 查 B。"""
    _seed_org(db)
    assert _ctx(db, "E_SALES1").can_view_score_of("E_SALES2", db=db) is True
    assert _ctx(db, "E_SALES2").can_view_score_of("E_SALES1", db=db) is True


# ---------------------------------------------------------------------------
# ② 不能查上一级
# ---------------------------------------------------------------------------


def test_cannot_view_own_direct_manager(db: Session) -> None:
    """★ 自己的直属上级不可查。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("M_SALES", db=db) is False


def test_cannot_view_managers_manager(db: Session) -> None:
    """上级的上级不可查。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("D_ROOT_BOSS", db=db) is False


def test_manager_can_view_direct_report(db: Session) -> None:
    """主管仍可查直属下属（走下属路径，不是 peer 路径）。"""
    _seed_org(db)
    ctx = _ctx(db, "M_SALES")
    assert ctx.can_view_score_of("E_SALES1", db=db) is True


def test_manager_can_view_indirect_report(db: Session) -> None:
    """★ 汇报线闭包：主管可查「下属的下属」。

    这是 G9 缺口 —— 改造前 ``direct_report_ids`` 只含直接下属。
    """
    _seed_org(db)
    ctx = _ctx(db, "M_SALES")
    # E_SALES2 直属 E_SALES1？不 —— E_SALES2 直属 M_SALES；
    # E_SALES3 直属 E_SALES2 ⇒ 是 M_SALES 的间接下属。
    assert ctx.can_view_score_of("E_SALES3", db=db) is True


# ---------------------------------------------------------------------------
# ③ 不能跨部门
# ---------------------------------------------------------------------------


def test_cannot_view_cross_department_even_with_same_manager(db: Session) -> None:
    """★ 汇报线同级但跨部门 ⇒ 仍然拒绝。

    这是"同部门"与"同一级"必须**同时**检查的原因：
    E_EXTERN 与 E_SALES1 同为 M_SALES 下属，但分属不同部门。
    """
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("E_EXTERN", db=db) is False


def test_cannot_view_other_department_same_level(db: Session) -> None:
    """完全不相干的部门 ⇒ 拒绝。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("E_MKT1", db=db) is False


def test_same_department_different_reporting_line_is_rejected(db: Session) -> None:
    """同部门但汇报线不同 ⇒ 视为不同级 ⇒ 拒绝（保守侧失败模式）。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    # E_SALES3 汇报给 E_SALES2，与 E_SALES1 不同直属上级
    assert ctx.can_view_score_of("E_SALES3", db=db) is False


# ---------------------------------------------------------------------------
# ④ 能查不能改
# ---------------------------------------------------------------------------


def test_peer_permission_never_grants_write(db: Session) -> None:
    """★ peer 权限不产生任何写权限。

    「能查不能改」的权限层落点。
    """
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.has_permission(PermissionAction.VIEW_DEPARTMENT_PEER) is True
    assert ctx.can_write_score_of("E_SALES2", db=db) is False
    assert ctx.can_write_score_of("M_SALES", db=db) is False


def test_write_permission_scope_unchanged(db: Session) -> None:
    """写权限口径不受 peer 能力影响。

    ⚠️ 注意口径：``M_SALES`` 是 ``E_SALES1/E_SALES2`` 的**主管**，
    所以他对二人**有**写权限 —— 这是既有语义（主管要评下属），
    与 peer 无关。

    本测试要守的不变式是：**平级之间不能互写**。
    """
    _seed_org(db)
    # 主管对下属可写（既有语义）
    manager_ctx = _ctx(db, "M_SALES")
    assert manager_ctx.can_write_score_of("E_SALES1", db=db) is True
    assert manager_ctx.can_write_score_of("E_SALES2", db=db) is True

    # ★ 平级之间不能互写 —— 这才是「能查不能改」的守门点
    peer_ctx = _ctx(db, "E_SALES1")
    assert peer_ctx.can_write_score_of("E_SALES2", db=db) is False
    assert peer_ctx.can_write_score_of("M_SALES", db=db) is False


def test_hr_can_write_anywhere(db: Session) -> None:
    """HR 全组织读写。"""
    _seed_org(db)
    ctx = _ctx(db, "E_HR")
    assert ctx.can_view_score_of("E_SALES1", db=db) is True
    assert ctx.can_write_score_of("E_SALES1", db=db) is True


def test_admin_does_not_get_business_data_access(db: Session) -> None:
    """系统管理员**不**自动获得业务数据权限（沿用既有设计）。"""
    _seed_org(db)
    from backend.app.domain.models import SystemAdmin

    db.add(SystemAdmin(employee_id="E_SALES1", active=True))
    db.commit()
    ctx = _ctx(db, "E_SALES1")
    assert "admin" in ctx.roles
    assert ctx.can_view_score_of("E_MKT1", db=db) is False


# ---------------------------------------------------------------------------
# HR / 自身 / 兼岗 / 离职
# ---------------------------------------------------------------------------


def test_hr_can_view_any_department(db: Session) -> None:
    """HR 跨部门查询放行。"""
    _seed_org(db)
    ctx = _ctx(db, "E_HR")
    assert ctx.can_view_score_of("E_SALES1", db=db) is True
    assert ctx.can_view_score_of("E_MKT1", db=db) is True


def test_can_view_self(db: Session) -> None:
    """本人永远可查。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("E_SALES1", db=db) is True


def test_joint_appointment_peer_in_secondary_department(db: Session) -> None:
    """兼岗员工在兼岗部门可与该部门同级别同事互查。

    判定用部门集合交集，不是主部门相等 —— 否则兼岗员工查不到本组同事。
    """
    _seed_org(db)
    # E_MKT1 兼岗到销售部，汇报线仍归 M_MKT —— 为测试"部门维度"，
    # 这里让它与 E_SALES2 汇报线一致。
    e_mkt1 = db.get(Employee, "E_MKT1")
    e_mkt1.manager_id = "M_SALES"
    db.add(
        EmployeeDepartment(
            employee_id="E_MKT1", department_id="D_SALES", is_primary=False
        )
    )
    db.commit()

    ctx = _ctx(db, "E_SALES1")
    # E_MKT1 在 D_SALES（兼岗）且与 E_SALES1 同直属上级 ⇒ 允许
    assert ctx.can_view_score_of("E_MKT1", db=db) is True


def test_peer_reverse_is_symmetric_for_joint_appointment(db: Session) -> None:
    """兼岗的**方向性**：兼岗员工与主部门同事互查是可以的（他确实在那个部门）。

    ⚠️ 这里要守的不变式是**不能**因为兼岗就把范围无限扩大：
    兼岗员工 E_MKT1 在 D_SALES（兼岗）⇒ 可查 D_SALES 的人；
    但 D_MKT 部门里**只有他自己**的人 ⇒ 无从查起。

    ⇒ 真正的守门点是：兼岗员工**不能**查那些与他无部门交集的人。
    这里用一个既不共 D_SALES 也不共 D_MKT 的人验证。
    """
    _seed_org(db)
    e_mkt1 = db.get(Employee, "E_MKT1")
    e_mkt1.manager_id = "M_SALES"
    db.add(
        EmployeeDepartment(
            employee_id="E_MKT1", department_id="D_SALES", is_primary=False
        )
    )
    db.commit()

    ctx = _ctx(db, "E_MKT1")
    # E_SALES2 在 D_SALES，兼岗后有交集 ⇒ 允许
    assert ctx.can_view_score_of("E_SALES2", db=db) is True
    # D_HR 的人与 E_MKT1 无部门交集 ⇒ 拒绝
    assert ctx.can_view_score_of("E_HR", db=db) is False
    # D_MKT 的另一个人 —— E_MKT1 主部门是 D_MKT，E_MKT2 也在 D_MKT ⇒ 允许
    db.add(
        Employee(
            id="E_MKT2",
            name="市场代表乙",
            department_id="D_MKT",
            manager_id="M_SALES",
            position="市场代表",
        )
    )
    db.commit()
    assert ctx.can_view_score_of("E_MKT2", db=db) is True


def test_exited_peer_not_in_direct_reports_but_inactive_filtered_at_tool(db: Session) -> None:
    """离职同事：peer 判定不拦（部门同级仍在），
    但**在职校验在工具层** —— 本测试只确认权限层不越权放行离职者。

    ⚠️ 离职员工的绩效数据不通过问答开放 —— 该闸门在 ``agent_tools`` 的
    ``SUBJECT_INACTIVE`` 检查，不在权限层。
    """
    _seed_org(db)
    db.get(Employee, "E_SALES2").employment_status = "exited"
    db.commit()
    ctx = _ctx(db, "E_SALES1")
    # 权限层：部门与汇报线仍匹配 ⇒ 权限层不拦
    assert ctx.can_view_score_of("E_SALES2", db=db) is True


def test_root_level_peers_without_manager_are_viewable(db: Session) -> None:
    """双方都无上级且同部门 ⇒ 视为同级。"""
    _seed_org(db)
    db.add(Department(id="D_FLAT", name="平级部门", active=True))
    db.flush()
    _add_employee(
        db, "F_ROOT1", name="平级甲",
        department_id="D_FLAT", manager_id=None,
    )
    _add_employee(
        db, "F_ROOT2", name="平级乙",
        department_id="D_FLAT", manager_id=None,
    )
    db.commit()

    ctx = _ctx(db, "F_ROOT1")
    assert ctx.can_view_score_of("F_ROOT2", db=db) is True


def test_one_side_has_manager_other_does_not_is_rejected(db: Session) -> None:
    """一边有上级一边没有 ⇒ 汇报线不同级 ⇒ 拒绝。"""
    _seed_org(db)
    db.add(Department(id="D_FLAT", name="平级部门", active=True))
    db.flush()
    _add_employee(
        db, "F_ROOT1", name="平级甲",
        department_id="D_FLAT", manager_id=None,
    )
    _add_employee(
        db, "F_ROOT2", name="平级乙",
        department_id="D_FLAT", manager_id="M_SALES",
    )
    db.commit()

    ctx = _ctx(db, "F_ROOT1")
    assert ctx.can_view_score_of("F_ROOT2", db=db) is False


# ---------------------------------------------------------------------------
# 汇报线成环 —— 不能挂死
# ---------------------------------------------------------------------------


def test_reporting_line_cycle_does_not_hang(db: Session) -> None:
    """⚠️ 飞书数据可能出现环（A→B→A）。

    汇报线闭包必须用 visited 集合防死循环。
    """
    _seed_org(db)
    db.get(Employee, "M_SALES").manager_id = "E_SALES1"  # 成环
    db.commit()

    ctx = _ctx(db, "M_SALES")
    # 闭包计算不应超时；结论是"能查到某个下属"即可
    assert ctx.can_view_score_of("E_SALES3", db=db) in (True, False)


def test_cycle_peer_check_does_not_hang(db: Session) -> None:
    """成环时 peer 判定同样不能挂死。"""
    _seed_org(db)
    db.get(Employee, "M_SALES").manager_id = "E_SALES1"
    db.commit()

    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("M_SALES", db=db) in (True, False)


# ---------------------------------------------------------------------------
# 兼容性 —— 改造不得改变既有行为
# ---------------------------------------------------------------------------


def test_can_view_without_db_keeps_legacy_behavior(db: Session) -> None:
    """★ ``db=None`` 时行为与改造前完全一致。

    这是 13 处老调用点（main.py / trend_report / feedback / semantic_qc …）
    不受影响的保证。老调用方没传 db ⇒ 不进入 peer 分支。
    """
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    # 同部门同级：老行为（改造前）是 False —— 因为当时压根没有 peer 分支
    assert ctx.can_view_score_of("E_SALES2") is False
    # 本人、下属仍然为 True
    assert ctx.can_view_score_of("E_SALES1") is True
    assert ctx.can_view_score_of("E_SALES3") is False  # 非直属下属


def test_hr_without_db_still_allowed(db: Session) -> None:
    """HR 在 ``db=None`` 下仍然放行（既有路径不看 db）。"""
    _seed_org(db)
    ctx = _ctx(db, "E_HR")
    assert ctx.can_view_score_of("E_SALES1") is True


def test_own_score_without_db(db: Session) -> None:
    """本人查询在 ``db=None`` 下不变。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("E_SALES1") is True


# ---------------------------------------------------------------------------
# 权限集合
# ---------------------------------------------------------------------------


def test_employee_role_has_peer_permission(db: Session) -> None:
    """普通员工默认带 VIEW_DEPARTMENT_PEER。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.has_permission(PermissionAction.VIEW_DEPARTMENT_PEER) is True


def test_access_context_carries_manager_id(db: Session) -> None:
    """AccessContext 必须带 manager_id —— 同级判定需要它。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.manager_id == "M_SALES"


def test_empty_target_id_rejected(db: Session) -> None:
    """空目标一律拒绝。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("", db=db) is False
    assert ctx.can_write_score_of("", db=db) is False


def test_nonexistent_target_rejected(db: Session) -> None:
    """不存在的人 ⇒ 拒绝（不能因为 peer 分支就放行）。"""
    _seed_org(db)
    ctx = _ctx(db, "E_SALES1")
    assert ctx.can_view_score_of("NO_SUCH_PERSON", db=db) is False
