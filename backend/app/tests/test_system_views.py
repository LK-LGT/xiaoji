"""系统管理真实数据视图测试。

覆盖：
- 部门树由 `department` 表构建（不再返回样例数据）；
- 员工列表由 `employee` 表构建；
- 角色按系统授权推导；
- 数据质量快照指标正确。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import Base, Department, Employee, SystemAdmin
from backend.app.services import system_views


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _seed(db) -> None:
    """构造一棵两级部门树与若干员工。"""
    db.add(Department(id="D_ROOT", name="总部", parent_id=None, active=True))
    db.add(
        Department(
            id="D_HR",
            name="人力资源部",
            parent_id="D_ROOT",
            leader_id="U_HR",
            active=True,
        )
    )
    db.add(
        Department(id="D_CHILD", name="销售一组", parent_id="D_ROOT", active=True)
    )
    db.add(
        Employee(
            id="U_HR",
            name="HR负责人",
            login_account="hr",
            email="hr@example.com",
            department_id="D_HR",
            manager_id="U_ROOT",
            employment_status="active",
            account_status="enabled",
        )
    )
    db.add(
        Employee(
            id="U_LEADER",
            name="部门负责人",
            login_account="leader",
            email="leader@example.com",
            department_id="D_CHILD",
            employment_status="active",
            account_status="enabled",
        )
    )
    db.add(
        Employee(
            id="U_EMP",
            name="多部门员工",
            login_account="emp",
            email="emp@example.com",
            department_id="D_CHILD",
            manager_id="U_LEADER",
            employment_status="active",
            account_status="enabled",
        )
    )
    db.add(
        Employee(
            id="U_EXITED",
            name="离职员工",
            login_account="exited",
            employment_status="exited",
            account_status="disabled",
            department_id="D_CHILD",
        )
    )
    db.add(SystemAdmin(employee_id="U_LEADER", active=True))
    db.commit()


def test_department_tree_is_built_from_db(db):
    """部门树应来自 department 表而不是样例数据。"""
    _seed(db)
    tree = system_views.list_department_tree(db)

    assert len(tree) == 1
    root = tree[0]
    assert root["id"] == "D_ROOT"
    assert root["name"] == "总部"
    assert len(root["children"]) == 2

    child_ids = {child["id"] for child in root["children"]}
    assert child_ids == {"D_HR", "D_CHILD"}

    hr = next(c for c in root["children"] if c["id"] == "D_HR")
    assert hr["leaderName"] == "HR负责人"
    assert hr["leaderUserId"] == "U_HR"


def test_department_tree_counts_employees(db):
    """部门员工数按主部门统计。

    统计口径包含已离职员工（离职员工仍占用部门编制），
    因此 D_CHILD 下有 U_LEADER、U_EMP、U_EXITED 共 3 人。
    """
    _seed(db)
    tree = system_views.list_department_tree(db)
    root = tree[0]
    child_map = {c["id"]: c for c in root["children"]}
    assert child_map["D_CHILD"]["employeeCount"] == 3  # U_LEADER + U_EMP + U_EXITED
    assert child_map["D_HR"]["employeeCount"] == 1


def test_empty_database_returns_empty_tree(db):
    """空库应返回空树而不是样例数据。"""
    tree = system_views.list_department_tree(db)
    assert tree == []


def test_user_list_is_built_from_db(db):
    """员工列表应来自 employee 表。"""
    _seed(db)
    result = system_views.list_user_items(db, page_no=1, page_size=50)

    assert result["total"] == 4
    ids = {item["id"] for item in result["records"]}
    assert ids == {"U_HR", "U_LEADER", "U_EMP", "U_EXITED"}

    emp = next(i for i in result["records"] if i["id"] == "U_EMP")
    assert emp["realName"] == "多部门员工"
    assert emp["username"] == "emp"
    assert emp["departmentName"] == "销售一组"
    assert emp["directLeaderName"] == "部门负责人"


def test_user_list_filters_by_keyword(db):
    """关键字过滤应生效。"""
    _seed(db)
    result = system_views.list_user_items(db, keyword="离职", page_no=1, page_size=50)
    assert result["total"] == 1
    assert result["records"][0]["id"] == "U_EXITED"


def test_user_list_filters_by_department(db):
    """按部门过滤应生效。"""
    _seed(db)
    result = system_views.list_user_items(db, dept_id="D_HR", page_no=1, page_size=50)
    assert result["total"] == 1
    assert result["records"][0]["id"] == "U_HR"


def test_user_item_exposes_dual_status(db):
    """在职状态与账号状态应分开暴露。"""
    _seed(db)
    result = system_views.list_user_items(db, keyword="离职", page_no=1, page_size=50)
    item = result["records"][0]
    assert item["employmentStatus"] == "exited"
    assert item["accountStatus"] == "disabled"
    assert item["status"] == 0


def test_role_names_are_derived(db):
    """角色应按系统授权推导。"""
    _seed(db)
    result = system_views.list_user_items(db, page_no=1, page_size=50)
    by_id = {item["id"]: item for item in result["records"]}

    # 系统管理员
    assert "admin" in by_id["U_LEADER"]["roleNames"]
    # 有下属 -> 直属主管
    assert "leader" in by_id["U_LEADER"]["roleNames"]
    # 普通员工
    assert by_id["U_EMP"]["roleNames"] == ["employee"]


def test_system_options_use_real_data(db):
    """系统选项应来自真实表。"""
    _seed(db)
    options = system_views.list_system_options(db)

    assert len(options["departments"]) == 1
    assert options["departments"][0]["id"] == "D_ROOT"

    leader_ids = {leader["id"] for leader in options["leaders"]}
    assert "U_HR" in leader_ids
    # 离职员工不应出现在负责人候选里
    assert "U_EXITED" not in leader_ids

    assert {role["id"] for role in options["roles"]} >= {"admin", "employee"}


def test_data_quality_snapshot_counts(db):
    """数据质量快照应统计缺失项。"""
    _seed(db)
    quality = system_views._data_quality_snapshot(db)

    assert quality["employeeCount"] == 4
    assert quality["departmentCount"] == 3
    # D_ROOT 与 D_CHILD 都没有负责人
    assert quality["department_without_leader_count"] == 2
    # 所有员工都归属了部门
    assert quality["no_department_count"] == 0
    # U_LEADER 未配置直属上级（U_HR / U_EMP 都配置了）
    assert quality["no_manager_count"] == 1
    assert quality["no_manager_ids"] == ["U_LEADER"]


def test_data_quality_detects_cyclic_manager(db):
    """循环上级关系应被检测出来。"""
    db.add(Department(id="D1", name="部门一", active=True))
    db.add(
        Employee(
            id="E1",
            name="甲",
            department_id="D1",
            manager_id="E2",
            employment_status="active",
        )
    )
    db.add(
        Employee(
            id="E2",
            name="乙",
            department_id="D1",
            manager_id="E1",
            employment_status="active",
        )
    )
    db.commit()

    quality = system_views._data_quality_snapshot(db)
    assert quality["cyclic_manager_count"] == 2
