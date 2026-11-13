"""【板块3】自评和评分接口的路由实现

包含两个核心接口：
  1. POST /v1/assessment/records/{record_id}/self-review - 员工自评
  2. POST /v1/assessment/records/{record_id}/manager-review - 主管评分
"""

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
import logging

from backend.app.core.security import get_current_user
from backend.app.core.db import get_db
from backend.app.domain.models import (
    Employee,
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
)
from backend.app.domain.models_block3 import (
    SelfReview,
    ManagerReview,
    MetricValue,
)
from backend.app import exceptions
from backend.app.schemas_block3 import (
    SelfReviewRequest,
    SelfReviewResponse,
    ManagerReviewRequest,
    ManagerReviewResponse,
    MetricValueResponse,
    ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/assessment/records", tags=["assessment_records"])


# ─────────────────────────────────────────────────────────────
# 【板块3.1】员工自评接口
# ─────────────────────────────────────────────────────────────


@router.post(
    "/{record_id}/self-review",
    response_model=SelfReviewResponse,
    responses={
        401: {"model": ErrorResponse, "description": "无效 Token"},
        403: {"model": ErrorResponse, "description": "权限不足"},
        404: {"model": ErrorResponse, "description": "评估记录不存在"},
        409: {"model": ErrorResponse, "description": "业务冲突（已提交）"},
        422: {"model": ErrorResponse, "description": "参数或时间验证失败"},
    },
    summary="【板块3.1】员工提交自评",
    description="员工填写自评表单，包括自评分数、评论、指标值等",
)
async def submit_self_review(
    record_id: str = Path(..., description="评估记录 ID"),
    request: SelfReviewRequest = None,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    员工提交自评表单
    
    【步骤 1】权限校验
    ├─ Token 有效性检查
    └─ 身份是否为员工
    
    【步骤 2】数据查询
    ├─ 获取 AssessmentRecord
    ├─ 获取相关的 Task
    └─ 获取 ConfigSnapshot
    
    【步骤 3】参数验证
    ├─ record_id 是否存在
    ├─ 员工 ID 是否为当前用户
    ├─ 自评分数范围 [1-5]
    ├─ 自评评论长度 [10-2000]
    └─ 指标值是否有效
    
    【步骤 4】时间检查
    └─ 是否超过截止时间
    
    【步骤 5】状态检查
    ├─ Record.status 是否为 "not_started"
    └─ Record.submit_locked 是否为 False
    
    【步骤 6】业务逻辑
    ├─ 验证指标值（如有）
    ├─ 保存自评内容
    ├─ 更新 Record.status = "reviewing"
    ├─ 设置 Record.self_submitted_at
    └─ 设置 Record.submit_locked = True
    
    【步骤 7】异常处理
    ├─ PermissionDenied: 非员工或不是自己的评估
    ├─ DataValidationError: 参数不合法
    ├─ BusinessLogicError: 超过截止时间或状态错误
    └─ ConflictError: 已经提交过
    
    【步骤 8】事务提交
    └─ db.commit()
    """
    
    try:
        logger.info(f"[自评] 开始处理 employee={current_user.id}, record_id={record_id}")
        
        # ───────────────────────────────────────────
        # 步骤 1：权限校验
        # ───────────────────────────────────────────
        
        if not current_user:
            raise exceptions.NotAuthenticated("Token 无效或已过期")
        
        # 检查是否是员工（非 HR、非主管）
        if current_user.role and current_user.role not in ["employee", None]:
            raise exceptions.PermissionDenied(
                user_id=current_user.id,
                action="submit_self_review",
                resource="AssessmentRecord",
                reason="仅员工可以提交自评"
            )
        
        # ───────────────────────────────────────────
        # 步骤 2：数据查询
        # ───────────────────────────────────────────
        
        record = db.query(AssessmentRecord).filter_by(id=record_id).first()
        
        if not record:
            raise exceptions.ResourceNotFoundError(
                resource="AssessmentRecord",
                resource_id=record_id
            )
        
        task = db.query(AssessmentTask).filter_by(id=record.task_id).first()
        if not task:
            raise exceptions.ResourceNotFoundError(
                resource="AssessmentTask",
                resource_id=record.task_id
            )
        
        snapshot = db.query(ConfigSnapshot).filter_by(id=task.snapshot_id).first()
        if not snapshot:
            raise exceptions.ResourceNotFoundError(
                resource="ConfigSnapshot",
                resource_id=task.snapshot_id
            )
        
        # ───────────────────────────────────────────
        # 步骤 3：参数验证
        # ───────────────────────────────────────────
        
        # 检查当前用户是否为被评人
        if current_user.id != record.employee_id:
            raise exceptions.PermissionDenied(
                user_id=current_user.id,
                action="submit_self_review",
                resource=f"AssessmentRecord({record_id})",
                reason="只能提交自己的自评"
            )
        
        # 参数验证（Pydantic 已处理范围检查）
        if not request.self_score or request.self_score < 1 or request.self_score > 5:
            raise exceptions.DataValidationError(
                message="自评分数必须在 1-5 之间",
                field="self_score"
            )
        
        if not request.self_comment or len(request.self_comment) < 10:
            raise exceptions.DataValidationError(
                message="自评评论至少 10 个字",
                field="self_comment"
            )
        
        # ───────────────────────────────────────────
        # 步骤 4：时间检查
        # ───────────────────────────────────────────
        
        now = datetime.now()
        self_review_deadline = task.self_review_deadline if hasattr(task, 'self_review_deadline') else None
        
        if self_review_deadline and now > self_review_deadline:
            raise exceptions.BusinessLogicError(
                message=f"自评截止时间已过（截止于 {self_review_deadline}）"
            )
        
        # ───────────────────────────────────────────
        # 步骤 5：状态检查
        # ───────────────────────────────────────────
        
        if record.status != "not_started":
            raise exceptions.ConflictError(
                message=f"记录状态不对，期望 not_started，实际 {record.status}"
            )
        
        if record.submit_locked:
            raise exceptions.ConflictError(
                message="您已经提交过自评，不能重复提交"
            )
        
        # ───────────────────────────────────────────
        # 步骤 6：业务逻辑 - 保存自评数据
        # ───────────────────────────────────────────
        
        from uuid import uuid4
        
        # 创建 SelfReview 记录
        self_review = SelfReview(
            id=str(uuid4()),
            record_id=record.id,
            employee_id=record.employee_id,
            self_score=request.self_score,
            self_comment=request.self_comment,
            highlights=request.highlights,
            improvement_areas=request.improvement_areas,
            submitted_at=now
        )
        
        db.add(self_review)
        db.flush()
        
        # 更新 AssessmentRecord
        record.status = "reviewing"
        record.self_submitted_at = now
        record.submit_locked = True
        
        db.add(record)
        db.flush()
        
        # ───────────────────────────────────────────
        # 步骤 7：处理指标值（如有）
        # ───────────────────────────────────────────
        
        if request.metric_values:
            logger.info(f"保存 {len(request.metric_values)} 条自评指标值")
            for mv in request.metric_values:
                metric_value = MetricValue(
                    id=str(uuid4()),
                    record_id=record.id,
                    metric_code=mv.metric_code,
                    value=mv.value,
                    unit=mv.unit,
                    source="self",
                    comment=mv.comment
                )
                db.add(metric_value)
            db.flush()
        
        # ───────────────────────────────────────────
        # 步骤 8：事务提交
        # ───────────────────────────────────────────
        
        db.commit()
        db.refresh(record)
        
        logger.info(f"[自评] 成功提交 record_id={record_id}, 状态已转移为 reviewing")
        
        # ───────────────────────────────────────────
        # 返回响应
        # ───────────────────────────────────────────
        
        return SelfReviewResponse(
            record_id=record.id,
            employee_id=record.employee_id,
            status=record.status,
            self_score=record.self_score,
            self_comment=record.self_comment,
            highlights=getattr(record, 'highlights', None),
            improvement_areas=getattr(record, 'improvement_areas', None),
            self_submitted_at=record.self_submitted_at,
            self_review_deadline=self_review_deadline or task.deadline,
            manager_review_deadline=task.deadline,
            message="自评已成功提交，等待主管评分"
        )
    
    except exceptions.AppException as e:
        logger.warning(f"[自评] 业务异常: {e}")
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "code": e.code,
                "message": str(e),
                "status_code": e.status_code
            }
        )
    except Exception as e:
        logger.error(f"[自评] 系统异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="系统内部错误")


# ─────────────────────────────────────────────────────────────
# 【板块3.2】主管评分接口
# ─────────────────────────────────────────────────────────────


@router.post(
    "/{record_id}/manager-review",
    response_model=ManagerReviewResponse,
    responses={
        401: {"model": ErrorResponse, "description": "无效 Token"},
        403: {"model": ErrorResponse, "description": "权限不足"},
        404: {"model": ErrorResponse, "description": "评估记录不存在"},
        409: {"model": ErrorResponse, "description": "业务冲突（员工未自评）"},
        422: {"model": ErrorResponse, "description": "参数或时间验证失败"},
    },
    summary="【板块3.2】主管提交评分",
    description="主管填写员工评分表单，包括指标值、评分、评论等",
)
async def submit_manager_review(
    record_id: str = Path(..., description="评估记录 ID"),
    request: ManagerReviewRequest = None,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    主管提交员工评分表单
    
    【步骤 1】权限校验
    ├─ Token 有效性检查
    ├─ 身份是否为主管或 HR
    └─ 是否是该员工的主管
    
    【步骤 2】数据查询
    ├─ 获取 AssessmentRecord
    ├─ 获取被评员工 Employee
    ├─ 获取相关的 Task
    └─ 获取 ConfigSnapshot
    
    【步骤 3】参数验证
    ├─ record_id 是否存在
    ├─ 当前用户是否为该员工的主管
    ├─ 指标值列表是否有效
    ├─ 指标值中的 metric_code 是否有效
    ├─ 指标值的数值是否在合理范围
    ├─ 主管评分 [1-5]
    ├─ 评论长度 [10-2000]
    └─ 是否超过截止时间
    
    【步骤 4】状态检查
    ├─ Record.status 是否为 "reviewing"
    ├─ Record.submit_locked 是否为 False
    └─ 员工是否已经完成自评
    
    【步骤 5】指标值验证
    ├─ 指标代码是否存在于 ConfigSnapshot
    ├─ 指标值是否超出合理范围
    └─ 是否缺少必需的指标
    
    【步骤 6】业务逻辑
    ├─ 保存所有指标值
    ├─ 保存主管评论和评分
    ├─ 更新 Record.status = "scored"
    ├─ 设置 Record.manager_submitted_at
    ├─ 计算初步得分
    └─ 设置 Record.confirmed_rating
    
    【步骤 7】异常处理
    ├─ PermissionDenied: 不是主管
    ├─ DataValidationError: 参数不合法
    ├─ BusinessLogicError: 超过截止时间
    ├─ ConflictError: 员工未完成自评
    └─ ConflictError: 已经评分过
    
    【步骤 8】事务提交
    └─ db.commit()
    """
    
    try:
        logger.info(f"[评分] 开始处理 manager={current_user.id}, record_id={record_id}")
        
        # ───────────────────────────────────────────
        # 步骤 1：权限校验
        # ───────────────────────────────────────────
        
        if not current_user:
            raise exceptions.NotAuthenticated("Token 无效或已过期")
        
        # ───────────────────────────────────────────
        # 步骤 2：数据查询
        # ───────────────────────────────────────────
        
        record = db.query(AssessmentRecord).filter_by(id=record_id).first()
        
        if not record:
            raise exceptions.ResourceNotFoundError(
                resource="AssessmentRecord",
                resource_id=record_id
            )
        
        # 查询被评员工
        employee = db.query(Employee).filter_by(id=record.employee_id).first()
        if not employee:
            raise exceptions.ResourceNotFoundError(
                resource="Employee",
                resource_id=record.employee_id
            )
        
        # 检查权限：当前用户是否为被评人的主管
        if current_user.id != employee.manager_id:
            raise exceptions.PermissionDenied(
                user_id=current_user.id,
                action="submit_manager_review",
                resource=f"AssessmentRecord({record_id})",
                reason=f"您不是 {employee.name} 的主管（主管是 {employee.manager_id}）"
            )
        
        task = db.query(AssessmentTask).filter_by(id=record.task_id).first()
        if not task:
            raise exceptions.ResourceNotFoundError(
                resource="AssessmentTask",
                resource_id=record.task_id
            )
        
        snapshot = db.query(ConfigSnapshot).filter_by(id=task.snapshot_id).first()
        if not snapshot:
            raise exceptions.ResourceNotFoundError(
                resource="ConfigSnapshot",
                resource_id=task.snapshot_id
            )
        
        # ───────────────────────────────────────────
        # 步骤 3：参数验证
        # ───────────────────────────────────────────
        
        if not request.metric_values or len(request.metric_values) == 0:
            raise exceptions.DataValidationError(
                message="至少需要一个指标值",
                field="metric_values"
            )
        
        if not request.manager_score or request.manager_score < 1 or request.manager_score > 5:
            raise exceptions.DataValidationError(
                message="主管评分必须在 1-5 之间",
                field="manager_score"
            )
        
        if not request.manager_comment or len(request.manager_comment) < 10:
            raise exceptions.DataValidationError(
                message="主管评论至少 10 个字",
                field="manager_comment"
            )
        
        # ───────────────────────────────────────────
        # 步骤 4：时间检查
        # ───────────────────────────────────────────
        
        now = datetime.now()
        manager_review_deadline = task.deadline if hasattr(task, 'deadline') else None
        
        if manager_review_deadline and now > manager_review_deadline:
            raise exceptions.BusinessLogicError(
                message=f"评分截止时间已过（截止于 {manager_review_deadline}）"
            )
        
        # ───────────────────────────────────────────
        # 步骤 5：状态检查
        # ───────────────────────────────────────────
        
        if record.status != "reviewing":
            raise exceptions.ConflictError(
                message=f"记录状态不对，期望 reviewing，实际 {record.status}"
            )
        
        if record.submit_locked and record.status == "scored":
            raise exceptions.ConflictError(
                message="您已经评分过，不能重复评分"
            )
        
        if not record.self_submitted_at:
            raise exceptions.ConflictError(
                message="员工尚未完成自评，无法进行主管评分"
            )
        
        # ───────────────────────────────────────────
        # 步骤 6：指标值验证
        # ───────────────────────────────────────────
        
        logger.info(f"验证 {len(request.metric_values)} 条指标值")
        for mv in request.metric_values:
            if not mv.metric_code:
                raise exceptions.DataValidationError(
                    message="指标代码不能为空",
                    field="metric_code"
                )
            if mv.value < 0:
                raise exceptions.DataValidationError(
                    message=f"指标 {mv.metric_code} 的值不能为负数",
                    field="value"
                )
        
        # ───────────────────────────────────────────
        # 步骤 6/7：业务逻辑 - 保存评分数据和指标值
        # ───────────────────────────────────────────
        
        from uuid import uuid4
        
        # 创建 ManagerReview 记录
        preliminary_score = float(request.manager_score)
        
        manager_review = ManagerReview(
            id=str(uuid4()),
            record_id=record.id,
            manager_id=current_user.id,
            manager_score=request.manager_score,
            manager_comment=request.manager_comment,
            rating_reason=request.rating_reason,
            recommended_action=request.recommended_action,
            preliminary_score=preliminary_score,
            submitted_at=now
        )
        
        db.add(manager_review)
        db.flush()
        
        # 保存所有指标值
        logger.info(f"保存 {len(request.metric_values)} 条指标值")
        for mv in request.metric_values:
            metric_value = MetricValue(
                id=str(uuid4()),
                record_id=record.id,
                metric_code=mv.metric_code,
                value=mv.value,
                unit=mv.unit,
                source="manager",
                comment=mv.comment
            )
            db.add(metric_value)
        db.flush()
        
        # 更新 AssessmentRecord
        record.status = "scored"
        record.manager_submitted_at = now
        record.confirmed_rating = preliminary_score
        record.submit_locked = True
        
        db.add(record)
        db.flush()
        
        # ───────────────────────────────────────────
        # 步骤 8：事务提交
        # ───────────────────────────────────────────
        
        db.commit()
        db.refresh(record)
        
        logger.info(f"[评分] 成功提交 record_id={record_id}, 状态已转移为 scored")
        
        # ───────────────────────────────────────────
        # 返回响应
        # ───────────────────────────────────────────
        
        metric_responses = [
            MetricValueResponse(
                metric_code=mv.metric_code,
                value=mv.value,
                unit=mv.unit,
                comment=mv.comment,
                source="manager"
            )
            for mv in request.metric_values
        ]
        
        return ManagerReviewResponse(
            record_id=record.id,
            employee_id=record.employee_id,
            manager_id=current_user.id,
            status=record.status,
            manager_score=record.manager_score,
            manager_comment=record.manager_comment,
            rating_reason=request.rating_reason,
            recommended_action=request.recommended_action,
            metric_values_count=len(request.metric_values),
            metric_values=metric_responses,
            preliminary_score=preliminary_score,
            manager_submitted_at=record.manager_submitted_at,
            message="评分已成功提交，等待规则引擎计算",
            next_step="等待 HR 最终确认或规则引擎计算最终得分"
        )
    
    except exceptions.AppException as e:
        logger.warning(f"[评分] 业务异常: {e}")
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "code": e.code,
                "message": str(e),
                "status_code": e.status_code
            }
        )
    except Exception as e:
        logger.error(f"[评分] 系统异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="系统内部错误")
