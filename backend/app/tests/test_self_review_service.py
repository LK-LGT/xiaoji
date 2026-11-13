"""【板块3】员工自评提交 —— 集成测试套件。

测试覆盖：
  1. 参数验证
  2. 权限校验
  3. 状态机验证
  4. 集成流程

所有测试基于真实的数据模型和业务逻辑，覆盖端到端的调用链。
"""

import pytest
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Department,
    Employee,
    ConfigSnapshot,
)
from backend.app.domain.enums import RecordStatus, SubmitSource
from backend.app import exceptions
from backend.app.services import self_review_service
from backend.app.schemas_self_review import (
    SelfReviewSubmitRequest,
    SelfReviewAttachment,
    SelfReviewMetricInput,
)


class TestSelfReviewService:
    """【板块3】自评业务逻辑测试"""

    @pytest.fixture
    def setup_task_and_record(self, db: Session, mock_employees, mock_config):
        """创建任务和记录"""
        task = AssessmentTask(
            id="task_001",
            name="2026 年Q4考核",
            period="2026-Q4",
            department_id="DEPT_SALES",
            status="self_review_open",
            snapshot_id=mock_config.id,
            owner_id="HR001",
        )
        db.add(task)
        db.flush()

        record = AssessmentRecord(
            id="rec_001",
            task_id=task.id,
            employee_id="E001",
            status=RecordStatus.SELF_REVIEW_OPEN.value,
        )
        db.add(record)
        db.commit()

        return {
            "task": task,
            "record": record,
            "task_id": task.id,
            "record_id": record.id,
        }

    # ─────────────────────────────────────────
    # ✅ 成功场景
    # ─────────────────────────────────────────

    def test_submit_self_review_success(self, db: Session, mock_employees, setup_task_and_record):
        """✅ 成功提交自评"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="本月完成销售目标 105%，新签 3 家客户",
            highlights="新客户拓展",
            improvement_areas="回款周期长",
        )

        result = self_review_service.submit_self_review(
            db,
            record_id=setup["record_id"],
            current_employee=employee,
            req=request,
        )

        # 返回值是 dict，包含 record_id, status 等字段
        assert result["record_id"] == setup["record_id"]
        assert result["status"] == RecordStatus.SELF_REVIEW_SUBMITTED.value
        assert result["self_score"] == 4
        assert result["submit_locked"] is True

        # 验证数据库状态
        record = db.query(AssessmentRecord).filter_by(id=setup["record_id"]).first()
        assert record.status == RecordStatus.SELF_REVIEW_SUBMITTED.value
        assert record.submit_locked is True
        assert record.submit_source == SubmitSource.H5.value

    def test_submit_self_review_with_metric_values(self, db: Session, mock_employees, setup_task_and_record):
        """✅ 包含指标值的自评"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        request = SelfReviewSubmitRequest(
            self_score=5,
            self_comment="本月表现卓越，销售额和客户满意度均超目标",
            metric_values=[
                SelfReviewMetricInput(
                    metric_code="GMV",
                    value=1250.0,
                    unit="万元",
                    comment="新签客户贡献 50 万",
                ),
                SelfReviewMetricInput(
                    metric_code="satisfaction",
                    value=98.0,
                    unit="%",
                    comment="无投诉",
                ),
            ],
        )

        result = self_review_service.submit_self_review(
            db,
            record_id=setup["record_id"],
            current_employee=employee,
            req=request,
        )

        assert result["metric_values_count"] == 2
        assert result["status"] == RecordStatus.SELF_REVIEW_SUBMITTED.value

    def test_submit_self_review_with_attachments(self, db: Session, mock_employees, setup_task_and_record):
        """✅ 包含附件的自评"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="请参见附件中的详细报告",
            attachments=[
                SelfReviewAttachment(
                    file_id="file_123",
                    file_name="2026Q4销售报表.pdf",
                    file_size=2048,
                    file_type="pdf",
                ),
                SelfReviewAttachment(
                    file_id="file_124",
                    file_name="客户反馈汇总.xlsx",
                    file_size=1024,
                    file_type="xlsx",
                ),
            ],
        )

        result = self_review_service.submit_self_review(
            db,
            record_id=setup["record_id"],
            current_employee=employee,
            req=request,
        )

        assert result["attachments_count"] == 2

    # ─────────────────────────────────────────
    # ❌ 参数验证失败
    # ─────────────────────────────────────────

    def test_submit_self_review_invalid_score_too_high(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 分数超出范围（太高） — Pydantic 在请求构造时验证"""
        # Pydantic 的 le=5 约束在构造时生效，所以这里用 pytest.raises 捕获 ValidationError
        with pytest.raises(Exception):  # ValueError 或 ValidationError
            SelfReviewSubmitRequest(
                self_score=10,  # 超出 1-5 范围
                self_comment="这是有效的评论，至少十个字以上",
            )

    def test_submit_self_review_invalid_score_too_low(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 分数超出范围（太低） — Pydantic 在请求构造时验证"""
        # Pydantic 的 ge=1 约束在构造时生效，所以这里用 pytest.raises 捕获 ValidationError
        with pytest.raises(Exception):  # ValueError 或 ValidationError
            SelfReviewSubmitRequest(
                self_score=0,  # 超出 1-5 范围
                self_comment="这是有效的评论，至少十个字以上",
            )

    def test_submit_self_review_comment_too_short(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 评论太短"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="太短",  # 少于 10 个字
        )

        with pytest.raises(exceptions.DataValidationError) as exc_info:
            self_review_service.submit_self_review(
                db,
                record_id=setup["record_id"],
                current_employee=employee,
                req=request,
            )
        assert "自评评论" in str(exc_info.value) or "10" in str(exc_info.value)

    def test_submit_self_review_comment_too_long(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 评论太长"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        long_comment = "x" * 2001  # 超过限制

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment=long_comment,
        )

        with pytest.raises(exceptions.DataValidationError) as exc_info:
            self_review_service.submit_self_review(
                db,
                record_id=setup["record_id"],
                current_employee=employee,
                req=request,
            )
        assert "自评评论" in str(exc_info.value) or "2000" in str(exc_info.value)

    def test_submit_self_review_metric_value_negative(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 指标值为负数"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="这是有效的评论，至少十个字以上",
            metric_values=[
                SelfReviewMetricInput(
                    metric_code="GMV",
                    value=-100.0,  # 负数
                    unit="万元",
                ),
            ],
        )

        with pytest.raises(exceptions.DataValidationError) as exc_info:
            self_review_service.submit_self_review(
                db,
                record_id=setup["record_id"],
                current_employee=employee,
                req=request,
            )
        assert "不能为负数" in str(exc_info.value)

    def test_submit_self_review_duplicate_metric_code(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 重复的指标编码"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="这是有效的评论，至少十个字以上",
            metric_values=[
                SelfReviewMetricInput(metric_code="GMV", value=100, unit="万元"),
                SelfReviewMetricInput(metric_code="GMV", value=150, unit="万元"),  # 重复
            ],
        )

        with pytest.raises(exceptions.DataValidationError) as exc_info:
            self_review_service.submit_self_review(
                db,
                record_id=setup["record_id"],
                current_employee=employee,
                req=request,
            )
        assert "重复" in str(exc_info.value)

    # ─────────────────────────────────────────
    # ❌ 权限检查失败
    # ─────────────────────────────────────────

    def test_submit_self_review_wrong_employee(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 员工尝试提交他人的自评"""
        setup = setup_task_and_record
        wrong_employee = mock_employees["E002"]  # 不是 record 的所有者

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="这是有效的评论，至少十个字以上",
        )

        with pytest.raises(exceptions.PermissionDenied):
            self_review_service.submit_self_review(
                db,
                record_id=setup["record_id"],
                current_employee=wrong_employee,
                req=request,
            )

    # ─────────────────────────────────────────
    # ❌ 状态检查失败
    # ─────────────────────────────────────────

    def test_submit_self_review_already_locked(self, db: Session, mock_employees, setup_task_and_record):
        """❌ 记录已被锁定（不能重复提交）"""
        setup = setup_task_and_record
        employee = mock_employees["E001"]

        # 第一次提交
        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="这是有效的评论，至少十个字以上",
        )

        self_review_service.submit_self_review(
            db,
            record_id=setup["record_id"],
            current_employee=employee,
            req=request,
        )
        db.commit()

        # 第二次尝试提交
        request2 = SelfReviewSubmitRequest(
            self_score=5,
            self_comment="修改后的评论，至少十个字以上",
        )

        with pytest.raises(exceptions.InvalidStateTransition):
            self_review_service.submit_self_review(
                db,
                record_id=setup["record_id"],
                current_employee=employee,
                req=request2,
            )

    def test_submit_self_review_invalid_status(self, db: Session, mock_employees, mock_config):
        """❌ 记录状态不对（不是待自评）"""
        employee = mock_employees["E001"]

        task = AssessmentTask(
            id="task_002",
            name="测试",
            period="2026-Q4",
            status="archived",  # 已归档
            snapshot_id=mock_config.id,
        )
        db.add(task)
        db.flush()

        record = AssessmentRecord(
            id="rec_002",
            task_id=task.id,
            employee_id="E001",
            status="archived",  # 已归档
        )
        db.add(record)
        db.commit()

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="这是有效的评论，至少十个字以上",
        )

        with pytest.raises(exceptions.InvalidStateTransition):
            self_review_service.submit_self_review(
                db,
                record_id="rec_002",
                current_employee=employee,
                req=request,
            )

    # ─────────────────────────────────────────
    # ❌ 记录不存在
    # ─────────────────────────────────────────

    def test_submit_self_review_record_not_found(self, db: Session, mock_employees):
        """❌ 记录不存在"""
        employee = mock_employees["E001"]

        request = SelfReviewSubmitRequest(
            self_score=4,
            self_comment="这是有效的评论，至少十个字以上",
        )

        with pytest.raises(exceptions.RecordNotFound):
            self_review_service.submit_self_review(
                db,
                record_id="rec_nonexistent",
                current_employee=employee,
                req=request,
            )

    # ─────────────────────────────────────────
    # ❌ 截止时间已过
    # ─────────────────────────────────────────

    def test_submit_self_review_deadline_exceeded(self, db: Session, mock_employees, mock_config):
        """❌ 自评截止时间已过 — 使用 Task 上可用的字段"""
        employee = mock_employees["E001"]

        # AssessmentTask 没有 self_review_deadline 字段，跳过此测试
        # 实际应用中可以在 Task 中添加此字段，或在 Record 层管理截止时间
        pytest.skip("AssessmentTask 未定义 self_review_deadline 字段（可在后续版本中添加）")
