"""【板块3】pytest conftest - 共用 fixtures

为所有【板块3】测试提供：
  1. db - 测试数据库会话
  2. mock_employees - 测试员工数据
  3. mock_config - 冻结配置快照
"""

import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from backend.app.domain.models import Base
from backend.app.domain.models import (
    Employee,
    Department,
    Metric,
    WeightSet,
    ConfigSnapshot,
    AssessmentTask,
)


@pytest.fixture(scope="function")
def db():
    """创建内存测试数据库会话"""
    
    # 使用内存数据库，每个测试独立
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    yield session
    
    session.close()
    engine.dispose()


@pytest.fixture
def mock_employees(db: Session):
    """创建测试员工数据"""
    
    # 创建部门
    dept_sales = Department(id="DEPT_SALES", name="销售部", active=True)
    dept_finance = Department(id="DEPT_FINANCE", name="财务部", active=True)
    db.add_all([dept_sales, dept_finance])
    db.flush()
    
    # 创建无上级的员工（避免外键约束）
    manager2 = Employee(
        id="M002",
        name="张三（财务经理）",
        position="财务经理",
        department_id="DEPT_FINANCE",
        manager_id=None,
        employment_status="active"
    )
    
    manager1 = Employee(
        id="M001",
        name="王伟（销售经理）",
        position="销售经理",
        manager_id="M002",
        department_id="DEPT_SALES",
        employment_status="active"
    )
    
    hr = Employee(
        id="HR001",
        name="李红（HR总监）",
        position="HR总监",
        department_id="DEPT_SALES",
        manager_id="M002",
        employment_status="active"
    )
    
    db.add_all([manager2, manager1, hr])
    db.flush()
    
    # 创建普通员工（受 M001 管理）
    emp1 = Employee(
        id="E001",
        name="刘刚",
        position="销售代表",
        manager_id="M001",
        department_id="DEPT_SALES",
        employment_status="active"
    )
    
    emp2 = Employee(
        id="E002",
        name="陈美",
        position="销售代表",
        manager_id="M002",
        department_id="DEPT_FINANCE",
        employment_status="active"
    )
    
    emp3 = Employee(
        id="E003",
        name="王小明",
        position="销售代表",
        manager_id="M001",
        department_id="DEPT_SALES",
        employment_status="active"
    )
    
    db.add_all([emp1, emp2, emp3])
    db.commit()
    
    return {
        "M001": manager1,
        "M002": manager2,
        "HR001": hr,
        "E001": emp1,
        "E002": emp2,
        "E003": emp3,
    }


@pytest.fixture
def mock_config(db: Session):
    """创建冻结配置快照"""
    
    # 创建冻结配置（最少必要字段）
    config = ConfigSnapshot(
        task_id="TASK_MOCK",
        metrics_json='[{"code": "GMV", "name": "销售额"}, {"code": "retention_rate", "name": "留任率"}]',
        weights_json='[{"code": "GMV", "weight": 0.6}, {"code": "retention_rate", "weight": 0.4}]',
        rubric_json='{"1": "未达标", "2": "基本达标", "3": "达标", "4": "超标", "5": "大幅超标"}',
        frozen_at=datetime.now()
    )
    
    db.add(config)
    db.commit()
    
    return config
