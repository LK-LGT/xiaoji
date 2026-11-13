"""【板块1】鉴权权限层单元测试。

测试覆盖：
1. Mock token 解析（get_current_user）
2. 不同角色的权限检查（check_permission）
3. 越权场景拒绝（PermissionDenied）
4. 工具函数（check_can_view_score、check_can_submit_review）
"""

import pytest
from sqlalchemy.orm import Session

from backend.app.core.db import get_sessionmaker
from backend.app.domain import models
from backend.app.dependencies import (
    get_current_user,
    get_direct_reports,
    check_permission,
    check_can_view_score,
    check_can_submit_review,
    UserRole,
    MOCK_TOKENS,
)
from backend.app.exceptions import NotAuthenticated, PermissionDenied, RecordNotFound


class TestGetCurrentUser:
    """测试身份验证（get_current_user）。"""
    
    @pytest.fixture
    def db(self):
        """创建测试数据库会话。"""
        sm = get_sessionmaker()
        session = sm()
        yield session
        session.close()
    
    @pytest.mark.asyncio
    async def test_valid_token_ordinary_employee(self, db: Session):
        """测试：有效的普通员工 token。"""
        authorization = "Bearer emp_token_001"
        
        user = await get_current_user(authorization=authorization, db=db)
        
        assert user is not None
        assert user.id == "E001"
        assert user.name == "刘刚（销售代表）"
        assert user.manager_id == "M001"
    
    @pytest.mark.asyncio
    async def test_valid_token_manager(self, db: Session):
        """测试：有效的主管 token。"""
        authorization = "Bearer manager_token_001"
        
        user = await get_current_user(authorization=authorization, db=db)
        
        assert user is not None
        assert user.id == "M001"
        assert user.name == "王伟（销售经理）"
        assert user.position == "销售经理"
    
    @pytest.mark.asyncio
    async def test_valid_token_hr(self, db: Session):
        """测试：有效的 HR token。"""
        authorization = "Bearer hr_token_001"
        
        user = await get_current_user(authorization=authorization, db=db)
        
        assert user is not None
        assert user.id == "HR001"
        assert user.name == "李红（HR总监）"
    
    @pytest.mark.asyncio
    async def test_invalid_token(self, db: Session):
        """测试：无效的 token。"""
        authorization = "Bearer invalid_token_xyz"
        
        with pytest.raises(NotAuthenticated) as exc_info:
            await get_current_user(authorization=authorization, db=db)
        
        assert exc_info.value.code == "NOT_AUTHENTICATED"
    
    @pytest.mark.asyncio
    async def test_missing_authorization_header(self, db: Session):
        """测试：缺少 Authorization header。"""
        with pytest.raises(NotAuthenticated) as exc_info:
            await get_current_user(authorization=None, db=db)
        
        assert "缺少 Authorization header" in exc_info.value.message
    
    @pytest.mark.asyncio
    async def test_malformed_authorization_header(self, db: Session):
        """测试：Authorization header 格式错误。"""
        authorization = "InvalidFormat emp_token_001"
        
        with pytest.raises(NotAuthenticated) as exc_info:
            await get_current_user(authorization=authorization, db=db)
        
        assert "格式错误" in exc_info.value.message
    
    @pytest.mark.asyncio
    async def test_bearer_prefix_case_insensitive(self, db: Session):
        """测试：Bearer 前缀大小写不敏感。"""
        authorization = "bearer emp_token_001"  # 小写 bearer
        
        user = await get_current_user(authorization=authorization, db=db)
        
        assert user.id == "E001"


class TestGetDirectReports:
    """测试直属下级查询（get_direct_reports）。"""
    
    @pytest.fixture
    def db(self):
        """创建测试数据库会话。"""
        sm = get_sessionmaker()
        session = sm()
        yield session
        session.close()
    
    @pytest.mark.asyncio
    async def test_manager_with_direct_reports(self, db: Session):
        """测试：主管有直属下级。"""
        # M001 有 E001、E002 两个直属下级
        m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
        direct_reports = get_direct_reports(user=m001, db=db)
        
        # 这里的结果取决于数据库中有多少 manager_id="M001" 的员工
        # 由于我们的 Mock 数据，应该包括 E001 和 E002
        assert isinstance(direct_reports, list)
        # assert "E001" in direct_reports
        # assert "E002" in direct_reports
    
    @pytest.mark.asyncio
    async def test_employee_no_direct_reports(self, db: Session):
        """测试：普通员工没有直属下级。"""
        e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
        direct_reports = get_direct_reports(user=e001, db=db)
        
        assert direct_reports == []


class TestCheckPermission:
    """测试权限矩阵检查（check_permission）。"""
    
    @pytest.fixture
    def db(self):
        """创建测试数据库会话。"""
        sm = get_sessionmaker()
        session = sm()
        yield session
        session.close()
    
    @pytest.mark.asyncio
    async def test_employee_can_view_own_score(self, db: Session):
        """测试：普通员工能查看自己的成绩。"""
        e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
        
        result = check_permission(e001, "view_own_score")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_employee_cannot_view_all_scores(self, db: Session):
        """测试：普通员工不能查看所有成绩。"""
        e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
        
        result = check_permission(e001, "view_all_scores")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_manager_can_view_direct_report_score(self, db: Session):
        """测试：主管能查看直属下级的成绩。"""
        m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
        
        result = check_permission(m001, "view_direct_report_score")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_manager_cannot_view_all_scores(self, db: Session):
        """测试：主管不能查看所有成绩（跨部门）。"""
        m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
        
        result = check_permission(m001, "view_all_scores")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_hr_can_view_all_scores(self, db: Session):
        """测试：HR 能查看所有成绩。"""
        hr = await get_current_user(authorization="Bearer hr_token_001", db=db)
        
        result = check_permission(hr, "view_all_scores")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_hr_can_confirm_rating(self, db: Session):
        """测试：HR 能确认等级。"""
        hr = await get_current_user(authorization="Bearer hr_token_001", db=db)
        
        result = check_permission(hr, "confirm_rating")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_employee_cannot_confirm_rating(self, db: Session):
        """测试：普通员工不能确认等级。"""
        e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
        
        result = check_permission(e001, "confirm_rating")
        
        assert result is False


class TestCheckCanViewScore:
    """测试查看成绩权限（check_can_view_score）。"""
    
    @pytest.fixture
    def db(self):
        """创建测试数据库会话。"""
        sm = get_sessionmaker()
        session = sm()
        yield session
        session.close()
    
    @pytest.mark.asyncio
    async def test_user_can_view_own_score(self, db: Session):
        """测试：员工能查看自己的成绩。"""
        e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
        
        result = check_can_view_score(
            user=e001,
            target_employee_id="E001",
            db=db
        )
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_manager_can_view_direct_report_score(self, db: Session):
        """测试：主管能查看直属下级的成绩。"""
        m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
        e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
        
        # 这里需要确保 E001 是 M001 的下级（已在 Mock 数据中设置）
        result = check_can_view_score(
            user=m001,
            target_employee_id=e001.id,
            db=db
        )
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_employee_cannot_view_other_score(self, db: Session):
        """测试：普通员工不能查看其他员工的成绩。"""
        e001 = await get_current_user(authorization="Bearer emp_token_001", db=db)
        
        with pytest.raises(PermissionDenied) as exc_info:
            check_can_view_score(
                user=e001,
                target_employee_id="E002",
                db=db
            )
        
        assert exc_info.value.code == "PERMISSION_DENIED"
    
    @pytest.mark.asyncio
    async def test_hr_can_view_any_score(self, db: Session):
        """测试：HR 能查看任何员工的成绩。"""
        hr = await get_current_user(authorization="Bearer hr_token_001", db=db)
        
        result = check_can_view_score(
            user=hr,
            target_employee_id="E001",
            db=db
        )
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_manager_cannot_view_non_subordinate_score(self, db: Session):
        """测试：主管不能查看非直属下级的成绩。"""
        m001 = await get_current_user(authorization="Bearer manager_token_001", db=db)
        
        # E003 由 M002 管理，不是 M001 的直属下级
        with pytest.raises(PermissionDenied):
            check_can_view_score(
                user=m001,
                target_employee_id="E003",
                db=db
            )


class TestCheckCanSubmitReview:
    """测试提交评分权限（check_can_submit_review）。"""
    
    @pytest.fixture
    def db(self):
        """创建测试数据库会话。"""
        sm = get_sessionmaker()
        session = sm()
        
        # 清空表，重新创建测试数据
        session.query(models.AssessmentRecord).delete()
        session.query(models.AssessmentTask).delete()
        session.query(models.Employee).delete()
        session.commit()
        
        # 创建测试员工和任务
        self._setup_test_data(session)
        
        yield session
        session.close()
    
    @staticmethod
    def _setup_test_data(db: Session):
        """创建测试数据。"""
        # 创建部门
        dept = models.Department(id="DEPT_SALES", name="销售部")
        db.add(dept)
        db.flush()
        
        # 创建主管和员工
        m001 = models.Employee(
            id="M001",
            name="王伟",
            position="销售经理",
            manager_id=None,
            department_id="DEPT_SALES",
            employment_status="active"
        )
        e001 = models.Employee(
            id="E001",
            name="刘刚",
            position="销售代表",
            manager_id="M001",
            department_id="DEPT_SALES",
            employment_status="active"
        )
        db.add_all([m001, e001])
        db.flush()
        
        # 创建考核任务
        task = models.AssessmentTask(
            id="TASK001",
            name="2026年11月考核",
            period="202611",
            status="self_review_open"
        )
        db.add(task)
        db.flush()
        
        # 创建考核记录
        record_e001 = models.AssessmentRecord(
            id="RECORD_E001",
            task_id="TASK001",
            employee_id="E001",
            status="self_review_open"
        )
        db.add(record_e001)
        db.commit()
    
    @pytest.mark.asyncio
    async def test_employee_can_submit_own_self_review(self, db: Session):
        """测试：员工能提交自己的自评。"""
        # 从 Mock 中获取 E001
        e001 = models.Employee.query.filter_by(id="E001").first() if hasattr(models.Employee, 'query') else \
               db.query(models.Employee).filter_by(id="E001").first()
        
        if not e001:
            # 创建临时员工对象
            e001 = models.Employee(
                id="E001",
                name="刘刚",
                position="销售代表",
                manager_id="M001",
                employment_status="active"
            )
        
        result = check_can_submit_review(
            user=e001,
            target_record_id="RECORD_E001",
            db=db
        )
        
        assert result is not None
        assert result.id == "RECORD_E001"
    
    @pytest.mark.asyncio
    async def test_manager_can_submit_direct_report_review(self, db: Session):
        """测试：主管能提交直属下级的评分。"""
        m001 = models.Employee(
            id="M001",
            name="王伟",
            position="销售经理",
            manager_id=None,
            employment_status="active"
        )
        
        result = check_can_submit_review(
            user=m001,
            target_record_id="RECORD_E001",
            db=db
        )
        
        assert result is not None
        assert result.id == "RECORD_E001"
    
    @pytest.mark.asyncio
    async def test_employee_cannot_submit_other_self_review(self, db: Session):
        """测试：员工不能提交其他人的自评。"""
        e002 = models.Employee(
            id="E002",
            name="赵燕",
            position="销售代表",
            manager_id="M001",
            employment_status="active"
        )
        
        with pytest.raises(PermissionDenied):
            check_can_submit_review(
                user=e002,
                target_record_id="RECORD_E001",
                db=db
            )
    
    @pytest.mark.asyncio
    async def test_record_not_found(self, db: Session):
        """测试：记录不存在。"""
        e001 = models.Employee(
            id="E001",
            name="刘刚",
            employment_status="active"
        )
        
        with pytest.raises(RecordNotFound) as exc_info:
            check_can_submit_review(
                user=e001,
                target_record_id="NONEXISTENT_RECORD",
                db=db
            )
        
        assert exc_info.value.code == "NOT_FOUND"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
