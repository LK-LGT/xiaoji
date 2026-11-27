"""
创建示例数据
"""
import sys
import uuid
from datetime import date
from pathlib import Path

# ⚠️ **引导到仓库根** —— 让 `backend.app.*` 可导入。
# （顶层 `app` 会被 agent-service 的 `app` 包抢占，所以统一用全路径。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from backend.app.core.db import get_engine, get_sessionmaker
from backend.app.domain.models import Base, Department, Employee


def seed_data():
    """创建示例数据"""
    # 创建所有表
    engine = get_engine()
    Base.metadata.create_all(bind=engine)

    # 获取 session
    SessionLocal = get_sessionmaker()
    db = SessionLocal()

    try:
        # 检查是否已经有数据
        existing = db.query(Department).first()
        if existing:
            print("✓ 数据库已有示例数据，跳过初始化")
            return

        # 创建部门
        depts = [
            Department(id="DEPT_HR", name="人力资源部", parent_id=None, leader_id=None, active=True),
            Department(id="DEPT_SALES", name="销售部", parent_id=None, leader_id=None, active=True),
            Department(id="DEPT_TECH", name="技术部", parent_id=None, leader_id=None, active=True),
            Department(id="DEPT_FINANCE", name="财务部", parent_id=None, leader_id=None, active=True),
            Department(id="DEPT_OPS", name="运营部", parent_id=None, leader_id=None, active=True),
        ]
        db.add_all(depts)
        db.commit()
        print("✓ 部门创建成功")

        # 创建员工
        admin_id = str(uuid.uuid4())
        manager_id = str(uuid.uuid4())
        employee_id = str(uuid.uuid4())
        hr_staff_id = str(uuid.uuid4())
        tech_lead_id = str(uuid.uuid4())

        employees = [
            Employee(
                id=admin_id,
                name="系统管理员",
                email="admin@company.com",
                department_id="DEPT_HR",
                position="系统管理员",
                manager_id=None,
                employment_status="active",
                joined_at=date(2020, 1, 1),
            ),
            Employee(
                id=manager_id,
                name="王伟（销售经理）",
                email="wang@company.com",
                department_id="DEPT_SALES",
                position="销售经理",
                manager_id=admin_id,
                employment_status="active",
                joined_at=date(2021, 6, 15),
            ),
            Employee(
                id=employee_id,
                name="刘刚（销售代表）",
                email="liu@company.com",
                department_id="DEPT_SALES",
                position="销售代表",
                manager_id=manager_id,
                employment_status="active",
                joined_at=date(2022, 3, 20),
            ),
            Employee(
                id=hr_staff_id,
                name="李明（HR专员）",
                email="li@company.com",
                department_id="DEPT_HR",
                position="HR专员",
                manager_id=admin_id,
                employment_status="active",
                joined_at=date(2021, 9, 1),
            ),
            Employee(
                id=tech_lead_id,
                name="张三（技术负责人）",
                email="zhang@company.com",
                department_id="DEPT_TECH",
                position="技术负责人",
                manager_id=admin_id,
                employment_status="active",
                joined_at=date(2020, 11, 1),
            ),
        ]
        db.add_all(employees)
        db.commit()
        print("✓ 员工创建成功")

        # 更新部门负责人
        db.query(Department).filter(Department.id == "DEPT_HR").update({"leader_id": admin_id})
        db.query(Department).filter(Department.id == "DEPT_SALES").update({"leader_id": manager_id})
        db.query(Department).filter(Department.id == "DEPT_TECH").update({"leader_id": tech_lead_id})
        db.commit()
        print("✓ 部门负责人分配成功")

        print("\n✅ 示例数据创建完成！")

    finally:
        db.close()

if __name__ == "__main__":
    seed_data()
