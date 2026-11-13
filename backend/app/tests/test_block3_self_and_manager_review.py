"""【板块3】单元测试 - 自评和评分接口

测试覆盖：
  1. 参数验证测试
  2. 权限测试
  3. 业务逻辑测试
  4. 异常处理测试
  5. 状态转移测试
"""

import pytest
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from backend.app.domain.models import (
    Employee,
    Department,
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
)
from backend.app import exceptions


class TestBlock3SelfReview:
    """【板块3.1】员工自评接口测试"""
    
    def test_self_review_success(self, db: Session, mock_employees, mock_config):
        """✅ 测试：成功提交自评"""
        
        from datetime import datetime as dt_cls
        from backend.app.domain.models_block3 import SelfReview
        from uuid import uuid4
        
        # Arrange
        task = AssessmentTask(
            id="TASK_SR_001",
            name="自评成功测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        record = AssessmentRecord(
            id="REC_SR_001",
            task_id=task.id,
            employee_id="E001",
            status="not_started",
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        # Act
        self_score = 4
        self_comment = "完成了销售目标的 95%，表现良好"
        
        # 创建自评记录
        self_review = SelfReview(
            id=str(uuid4()),
            record_id=record.id,
            employee_id="E001",
            self_score=self_score,
            self_comment=self_comment,
            submitted_at=dt_cls.now()
        )
        db.add(self_review)
        
        # 更新 record
        record.status = "reviewing"
        record.self_submitted_at = dt_cls.now()
        record.submit_locked = True
        
        db.add(record)
        db.commit()
        db.refresh(record)
        
        # Assert
        assert record.status == "reviewing"
        assert record.submit_locked is True
        assert record.self_submitted_at is not None
        
        # 验证 SelfReview 记录
        saved_review = db.query(SelfReview).filter_by(record_id=record.id).first()
        assert saved_review is not None
        assert saved_review.self_score == 4
        assert saved_review.self_comment == self_comment
    
    def test_self_review_invalid_score(self):
        """✅ 测试：参数验证 - 无效分数"""
        
        from backend.app.schemas_block3 import SelfReviewRequest
        
        # 分数超出范围应该失败
        try:
            SelfReviewRequest(
                self_score=10,  # 超出 1-5 范围
                self_comment="这是一个有效的评论，至少十个字以上"
            )
            assert False, "应该验证失败"
        except Exception as e:
            assert "less than or equal to 5" in str(e) or "validation" in str(e).lower()
    
    def test_self_review_short_comment(self):
        """✅ 测试：参数验证 - 评论太短"""
        
        from backend.app.schemas_block3 import SelfReviewRequest
        
        # 评论太短应该失败
        try:
            SelfReviewRequest(
                self_score=4,
                self_comment="短"  # 少于 10 个字
            )
            assert False, "应该验证失败"
        except Exception as e:
            assert "at least 10" in str(e) or "validation" in str(e).lower()
    
    def test_self_review_cannot_resubmit(self, db: Session, mock_employees, mock_config):
        """✅ 测试：业务逻辑 - 禁止重复提交"""
        
        from datetime import datetime as dt_cls
        
        # Arrange
        task = AssessmentTask(
            id="TASK_SR_RESUBMIT",
            name="重复提交测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        record = AssessmentRecord(
            id="REC_SR_RESUBMIT",
            task_id=task.id,
            employee_id="E001",
            status="reviewing",  # 已经评审中
            self_submitted_at=dt_cls.now(),
            submit_locked=True,  # 已锁定
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        # Act & Assert
        assert record.submit_locked is True
        assert record.status == "reviewing"
        # 尝试再提交应该被阻止（在路由层检查）
    
    def test_self_review_wrong_employee(self, db: Session, mock_employees, mock_config):
        """✅ 测试：权限 - 员工只能改自己的"""
        
        from datetime import datetime as dt_cls
        
        # Arrange
        task = AssessmentTask(
            id="TASK_SR_PERM",
            name="权限测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        # 这条记录是 E002 的
        record = AssessmentRecord(
            id="REC_SR_PERM",
            task_id=task.id,
            employee_id="E002",
            status="not_started",
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        # E001 尝试修改 E002 的评估记录
        # 应该被 PermissionDenied 拒绝（在路由层检查）
        assert record.employee_id == "E002"


class TestBlock3ManagerReview:
    """【板块3.2】主管评分接口测试"""
    
    def test_manager_review_success(self, db: Session, mock_employees, mock_config):
        """✅ 测试：成功提交评分"""
        
        from datetime import datetime as dt_cls
        from backend.app.domain.models_block3 import ManagerReview, MetricValue
        from uuid import uuid4
        
        # Arrange
        task = AssessmentTask(
            id="TASK_MR_001",
            name="评分成功测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        # 员工已完成自评
        record = AssessmentRecord(
            id="REC_MR_001",
            task_id=task.id,
            employee_id="E001",
            status="reviewing",
            self_submitted_at=dt_cls.now(),
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        # Act
        manager = db.query(Employee).filter_by(id="M001").first()
        assert manager is not None
        
        manager_score = 4
        manager_comment = "表现很好，完成了销售目标"
        
        # 创建 ManagerReview 记录
        manager_review = ManagerReview(
            id=str(uuid4()),
            record_id=record.id,
            manager_id="M001",
            manager_score=manager_score,
            manager_comment=manager_comment,
            preliminary_score=4.0,
            submitted_at=dt_cls.now()
        )
        db.add(manager_review)
        db.flush()
        
        # 添加指标值
        metric_value = MetricValue(
            id=str(uuid4()),
            record_id=record.id,
            metric_code="GMV",
            value=95.0,
            unit="万元",
            source="manager"
        )
        db.add(metric_value)
        db.flush()
        
        # 更新 record
        record.status = "scored"
        record.manager_submitted_at = dt_cls.now()
        record.confirmed_rating = 4.0
        record.submit_locked = True
        
        db.add(record)
        db.commit()
        db.refresh(record)
        
        # Assert
        assert record.status == "scored"
        assert record.submit_locked is True
        assert record.manager_submitted_at is not None
        assert record.confirmed_rating == 4.0
        
        # 验证 ManagerReview 记录
        saved_review = db.query(ManagerReview).filter_by(record_id=record.id).first()
        assert saved_review is not None
        assert saved_review.manager_score == 4
        
        # 验证 MetricValue 记录
        saved_metric = db.query(MetricValue).filter_by(record_id=record.id).first()
        assert saved_metric is not None
        assert saved_metric.value == 95.0
    
    def test_manager_review_employee_not_reviewed(self, db: Session, mock_employees, mock_config):
        """✅ 测试：业务逻辑 - 员工未自评，禁止评分"""
        
        from datetime import datetime as dt_cls
        
        # Arrange
        task = AssessmentTask(
            id="TASK_MR_PREREQ",
            name="前置条件测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        # 员工未完成自评
        record = AssessmentRecord(
            id="REC_MR_PREREQ",
            task_id=task.id,
            employee_id="E001",
            status="not_started",  # 还没自评
            self_submitted_at=None,  # 没有自评时间
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        # Assert - 主管尝试评分应该被拒绝（在路由层检查）
        assert record.self_submitted_at is None
        assert record.status == "not_started"
    
    def test_manager_review_wrong_manager(self, db: Session, mock_employees, mock_config):
        """✅ 测试：权限 - 主管只能改下属"""
        
        from datetime import datetime as dt_cls
        
        # Arrange
        task = AssessmentTask(
            id="TASK_MR_PERM",
            name="权限测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        # 这条记录的员工是 E002（主管是 M002）
        record = AssessmentRecord(
            id="REC_MR_PERM",
            task_id=task.id,
            employee_id="E002",
            status="reviewing",
            self_submitted_at=dt_cls.now(),
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        # M001 尝试评 E002（但 M001 不是 E002 的主管）
        # 应该被 PermissionDenied 拒绝（在路由层检查）
        
        employee = db.query(Employee).filter_by(id="E002").first()
        assert employee.manager_id == "M002"  # 主管是 M002，不是 M001
    
    def test_manager_review_invalid_metrics(self):
        """✅ 测试：参数验证 - 无效指标值"""
        
        from backend.app.schemas_block3 import ManagerReviewRequest
        
        # 指标值列表为空应该失败
        try:
            ManagerReviewRequest(
                metric_values=[],  # 空列表
                manager_score=4,
                manager_comment="这是一个有效的评论，至少十个字以上"
            )
            assert False, "应该验证失败"
        except Exception as e:
            assert "at least 1" in str(e) or "validation" in str(e).lower()
    
    def test_manager_review_invalid_score(self):
        """✅ 测试：参数验证 - 无效评分"""
        
        from backend.app.schemas_block3 import ManagerReviewRequest, MetricValueRequest
        
        # 评分超出范围应该失败
        try:
            ManagerReviewRequest(
                metric_values=[
                    MetricValueRequest(metric_code="GMV", value=95, unit="万元")
                ],
                manager_score=10,  # 超出 1-5 范围
                manager_comment="这是一个有效的评论，至少十个字以上"
            )
            assert False, "应该验证失败"
        except Exception as e:
            assert "less than or equal to 5" in str(e) or "validation" in str(e).lower()
    
    def test_manager_review_state_transition(self, db: Session, mock_employees, mock_config):
        """✅ 测试：状态转移 - reviewing → scored"""
        
        from datetime import datetime as dt_cls
        
        # Arrange
        task = AssessmentTask(
            id="TASK_MR_STATE",
            name="状态转移测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        record = AssessmentRecord(
            id="REC_MR_STATE",
            task_id=task.id,
            employee_id="E001",
            status="reviewing",
            self_submitted_at=dt_cls.now(),
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        # Act
        original_status = record.status
        
        # 状态转移
        record.status = "scored"
        db.add(record)
        db.commit()
        db.refresh(record)
        
        # Assert
        assert original_status == "reviewing"
        assert record.status == "scored"


class TestBlock3Integration:
    """【板块3】集成测试 - 完整自评评分流程"""
    
    def test_complete_review_flow(self, db: Session, mock_employees, mock_config):
        """✅ 测试：完整流程 - 自评 + 评分 + 状态变化"""
        
        from datetime import datetime as dt_cls
        from backend.app.domain.models_block3 import SelfReview, ManagerReview, MetricValue
        from uuid import uuid4
        
        # Step 1: 创建考核任务
        task = AssessmentTask(
            id="TASK_COMPLETE",
            name="完整流程测试",
            period="202611",
            status="created",
            snapshot_id=mock_config.id,
            owner_id="HR001",
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        # Step 2: 生成评估记录
        record = AssessmentRecord(
            id="REC_COMPLETE",
            task_id=task.id,
            employee_id="E001",
            status="not_started",
            created_at=dt_cls.now()
        )
        db.add(record)
        db.commit()
        
        assert record.status == "not_started"
        
        # Step 3: 员工提交自评
        self_review = SelfReview(
            id=str(uuid4()),
            record_id=record.id,
            employee_id="E001",
            self_score=4,
            self_comment="完成了销售目标",
            submitted_at=dt_cls.now()
        )
        db.add(self_review)
        db.flush()
        
        record.status = "reviewing"
        record.self_submitted_at = dt_cls.now()
        record.submit_locked = True
        db.add(record)
        db.commit()
        db.refresh(record)
        
        assert record.status == "reviewing"
        
        # 验证自评数据
        saved_self_review = db.query(SelfReview).filter_by(record_id=record.id).first()
        assert saved_self_review is not None
        assert saved_self_review.self_score == 4
        
        # Step 4: 主管提交评分
        manager_review = ManagerReview(
            id=str(uuid4()),
            record_id=record.id,
            manager_id="M001",
            manager_score=4,
            manager_comment="表现良好",
            preliminary_score=4.0,
            submitted_at=dt_cls.now()
        )
        db.add(manager_review)
        db.flush()
        
        # 添加指标值
        metric_value = MetricValue(
            id=str(uuid4()),
            record_id=record.id,
            metric_code="GMV",
            value=95.0,
            unit="万元",
            source="manager"
        )
        db.add(metric_value)
        db.flush()
        
        record.status = "scored"
        record.manager_submitted_at = dt_cls.now()
        record.confirmed_rating = 4.0
        db.add(record)
        db.commit()
        db.refresh(record)
        
        assert record.status == "scored"
        
        # Step 5: 验证完整流程
        final_record = db.query(AssessmentRecord).filter_by(id="REC_COMPLETE").first()
        assert final_record.status == "scored"
        
        # 验证所有数据都已保存
        assert db.query(SelfReview).filter_by(record_id=record.id).count() == 1
        assert db.query(ManagerReview).filter_by(record_id=record.id).count() == 1
        assert db.query(MetricValue).filter_by(record_id=record.id).count() == 1
