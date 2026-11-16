#!/usr/bin/env python
"""【板块1】快速验证脚本 - 不需要完整的 FastAPI 应用即可运行。

用途：快速演示核心功能，验证实现是否正确。
"""

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.dependencies import (
    MOCK_TOKENS,
    check_can_submit_review,
    check_can_view_score,
    check_permission,
    get_current_user,
    get_direct_reports,
)
from backend.app.domain.models import AssessmentRecord, AssessmentTask, Base, Department, Employee
from backend.app.exceptions import NotAuthenticated, PermissionDenied, RecordNotFound

# ┌─────────────────────────────────────────────────────┐
# │ 初始化测试数据库                                    │
# └─────────────────────────────────────────────────────┘

def setup_test_db():
    """创建内存 SQLite 数据库并初始化。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal


def populate_test_data(SessionLocal):
    """填充测试数据。"""
    db = SessionLocal()

    try:
        # 创建部门
        dept_sales = Department(id="DEPT_SALES", name="销售部")
        dept_hr = Department(id="DEPT_HR", name="HR部")
        db.add_all([dept_sales, dept_hr])
        db.flush()

        # 创建员工（对应 MOCK_TOKENS）
        employees = [
            Employee(
                id="HR001",
                name="李红（HR总监）",
                position="HR总监",
                manager_id=None,
                department_id="DEPT_HR",
                employment_status="active"
            ),
            Employee(
                id="M001",
                name="王伟（销售经理）",
                position="销售经理",
                manager_id="M002",
                department_id="DEPT_SALES",
                employment_status="active"
            ),
            Employee(
                id="M002",
                name="张文（销售总监）",
                position="销售总监",
                manager_id=None,
                department_id="DEPT_SALES",
                employment_status="active"
            ),
            Employee(
                id="E001",
                name="刘刚（销售代表）",
                position="销售代表",
                manager_id="M001",
                department_id="DEPT_SALES",
                employment_status="active"
            ),
            Employee(
                id="E002",
                name="赵燕（销售代表）",
                position="销售代表",
                manager_id="M001",
                department_id="DEPT_SALES",
                employment_status="active"
            ),
            Employee(
                id="E003",
                name="陈晶（财务主管）",
                position="财务主管",
                manager_id="M002",
                department_id="DEPT_SALES",
                employment_status="active"
            ),
        ]
        db.add_all(employees)
        db.flush()

        # 创建考核任务
        task = AssessmentTask(
            id="TASK001",
            name="2026年11月考核",
            period="202611",
            status="self_review_open"
        )
        db.add(task)
        db.flush()

        # 创建考核记录
        records = [
            AssessmentRecord(
                id="RECORD_E001",
                task_id="TASK001",
                employee_id="E001",
                status="self_review_open"
            ),
            AssessmentRecord(
                id="RECORD_E002",
                task_id="TASK001",
                employee_id="E002",
                status="self_review_open"
            ),
        ]
        db.add_all(records)
        db.commit()

        return db

    except Exception:
        db.rollback()
        raise


# ┌─────────────────────────────────────────────────────┐
# │ 测试用例                                            │
# └─────────────────────────────────────────────────────┘

async def test_get_current_user():
    """测试：获取当前用户。"""
    print("\n📋 测试 1: get_current_user")
    print("=" * 60)

    SessionLocal = setup_test_db()
    db = populate_test_data(SessionLocal)

    test_cases = [
        ("emp_token_001", "E001", True),
        ("manager_token_001", "M001", True),
        ("hr_token_001", "HR001", True),
        ("invalid_token", None, False),
    ]

    for token, expected_id, should_pass in test_cases:
        try:
            user = await get_current_user(
                authorization=f"Bearer {token}",
                db=db
            )

            if should_pass:
                print(f"✅ Token '{token}' → {user.name} (ID: {user.id})")
                assert user.id == expected_id
            else:
                print(f"❌ Token '{token}' 应该失败，但成功了")

        except NotAuthenticated as e:
            if not should_pass:
                print(f"✅ Token '{token}' 正确拒绝: {e.message}")
            else:
                print(f"❌ Token '{token}' 不应该失败: {e.message}")

        except Exception as e:
            print(f"❌ 未预期的异常: {e}")

    db.close()


async def test_check_permission():
    """测试：权限矩阵检查。"""
    print("\n📋 测试 2: check_permission")
    print("=" * 60)

    SessionLocal = setup_test_db()
    db = populate_test_data(SessionLocal)

    # 获取各角色的用户
    e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
    m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
    hr = await get_current_user(authorization="Bearer hr_token_001", db=db)

    test_cases = [
        (e001, "view_own_score", True),
        (e001, "view_all_scores", False),
        (e001, "confirm_rating", False),

        (m001, "view_direct_report_score", True),
        (m001, "view_all_scores", False),
        (m001, "confirm_rating", False),
        (m001, "add_metric_to_record", True),

        (hr, "view_all_scores", True),
        (hr, "confirm_rating", True),
        (hr, "view_audit_log", True),
    ]

    for user, action, expected in test_cases:
        result = check_permission(user, action)
        status = "✅" if result == expected else "❌"
        print(f"{status} {user.name[:15]:15} 权限 '{action}': {result} (期望: {expected})")

    db.close()


async def test_check_can_view_score():
    """测试：查看成绩权限。"""
    print("\n📋 测试 3: check_can_view_score")
    print("=" * 60)

    SessionLocal = setup_test_db()
    db = populate_test_data(SessionLocal)

    e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
    m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
    hr = await get_current_user(authorization="Bearer hr_token_001", db=db)

    test_cases = [
        (e001.id, "E001", True, "员工查看自己"),  # ← 改为 e001.id
        (e001.id, "E002", False, "员工查看他人"),
        (m001.id, "E001", True, "主管查看直属下级"),
        (m001.id, "E003", False, "主管查看非下级"),
        (hr.id, "E001", True, "HR 查看任何人"),
        (hr.id, "E002", True, "HR 查看任何人"),
    ]

    for user_id, target_id, should_pass, desc in test_cases:
        try:
            # 返回值不用看 —— 这里验证的是「放行 / 抛 PermissionDenied」
            check_can_view_score(user_id, target_id, db)  # ← 改为 user_id
            if should_pass:
                print(f"✅ {desc}: 允许")
            else:
                print(f"❌ {desc}: 应该拒绝，但允许了")

        except PermissionDenied:
            if not should_pass:
                print(f"✅ {desc}: 正确拒绝")
            else:
                print(f"❌ {desc}: 不应该拒绝")

    db.close()


async def test_check_can_submit_review():
    """测试：提交评分权限。"""
    print("\n📋 测试 4: check_can_submit_review")
    print("=" * 60)

    SessionLocal = setup_test_db()
    db = populate_test_data(SessionLocal)

    e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
    e002 = await get_current_user(authorization="Bearer emp_token_002", db=db)
    m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)

    test_cases = [
        (e001.id, "RECORD_E001", True, "员工提交自己的自评"),  # ← 改为 e001.id
        (e001.id, "RECORD_E002", False, "员工提交他人的自评"),
        (m001.id, "RECORD_E001", True, "主管提交下级的评分"),
        (e002.id, "RECORD_E001", False, "非管理者提交他人的自评"),
    ]

    for user_id, record_id, should_pass, desc in test_cases:
        try:
            # 返回值不用看 —— 这里验证的是「放行 / 抛异常」
            check_can_submit_review(user_id, record_id, db)  # ← 改为 user_id
            if should_pass:
                print(f"✅ {desc}: 允许")
            else:
                print(f"❌ {desc}: 应该拒绝，但允许了")

        except (PermissionDenied, RecordNotFound):
            if not should_pass:
                print(f"✅ {desc}: 正确拒绝")
            else:
                print(f"❌ {desc}: 不应该拒绝")

    db.close()


async def test_get_direct_reports():
    """测试：获取直属下级。"""
    print("\n📋 测试 5: get_direct_reports")
    print("=" * 60)

    SessionLocal = setup_test_db()
    db = populate_test_data(SessionLocal)

    m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
    e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)

    # M001 的直属下级应该是 E001 和 E002
    reports = get_direct_reports(m001, db)
    print(f"✅ M001 的直属下级: {reports}")

    # E001 没有直属下级
    reports = get_direct_reports(e001, db)
    print(f"✅ E001 的直属下级: {reports if reports else '（无）'}")

    db.close()


# ┌─────────────────────────────────────────────────────┐
# │ 主函数                                              │
# └─────────────────────────────────────────────────────┘

async def main():
    """运行所有测试。"""
    print("\n" + "=" * 60)
    print("【板块1】鉴权权限层 - 快速验证")
    print("=" * 60)

    print("\n📊 Mock Token 库")
    print("-" * 60)
    for token, data in MOCK_TOKENS.items():
        print(f"  {token:25} → {data['name']:20} ({data['role']})")

    print("\n📊 权限矩阵")
    print("-" * 60)
    from backend.app.dependencies import PERMISSION_MATRIX
    for role, perms in PERMISSION_MATRIX.items():
        print(f"\n  {role.value}:")
        for action, allowed in perms.items():
            status = "✓" if allowed else "✗"
            print(f"    [{status}] {action}")

    # 运行测试
    await test_get_current_user()
    await test_check_permission()
    await test_check_can_view_score()
    await test_check_can_submit_review()
    await test_get_direct_reports()

    print("\n" + "=" * 60)
    print("✅ 快速验证完成！")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
