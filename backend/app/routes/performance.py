"""绩效考核完整流程 API。

包括：
1. 发起考核任务 (HR)
2. 员工自评 (员工)
3. 主管评分 (主管)
4. 确认最终成绩 (HR)
5. 查询成绩 (权限检查)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Employee,
    Department,
    Score,
    RatingDraft,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/performance", tags=["performance"])


# -------------------------------------------------------
# 请求/响应模型
# -------------------------------------------------------


class CreatePerformanceTaskRequest(BaseModel):
    """发起考核任务请求。"""
    title: str = Field(..., min_length=1, max_length=200)
    period: str = Field(..., description="考核期间，格式 202611")
    target_employee_ids: list[str] = Field(..., min_items=1)
    start_date: str = Field(..., description="ISO 8601 格式")
    end_date: str = Field(..., description="ISO 8601 格式")
    self_review_deadline: str = Field(...)
    manager_review_deadline: str = Field(...)


class SubmitSelfReviewRequest(BaseModel):
    """员工提交自评请求。"""
    record_id: str
    performance_summary: str = Field(..., min_length=1, max_length=5000)
    achievements: list[str] = Field(default_factory=list)
    challenges: list[str] = Field(default_factory=list)


class SubmitManagerReviewRequest(BaseModel):
    """主管提交评分请求。"""
    record_id: str
    rating: str = Field(..., pattern="^[A-D]$")
    manager_comment: str = Field(..., min_length=1, max_length=5000)


class ConfirmPerformanceRequest(BaseModel):
    """HR 确认最终成绩请求。"""
    task_id: str


class PerformanceQueryRequest(BaseModel):
    """查询成绩请求。"""
    record_id: str


# -------------------------------------------------------
# 权限检查函数
# -------------------------------------------------------


def check_is_hr(db: Session, employee_id: str) -> bool:
    """检查是否 HR（簡易實現：后续应从数据库读权限）。"""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        return False
    # 簡易：HR 部門或特定職位
    return emp.department_id in ("DEPT_HR", "cc7a7gf9e937gb42")  # DEPT_HR 或 人事行政部


def check_is_manager_of(db: Session, manager_id: str, employee_id: str) -> bool:
    """检查 manager_id 是否是 employee_id 的直属主管。"""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        return False
    return emp.manager_id == manager_id


def check_can_view_employee(db: Session, viewer_id: str, target_employee_id: str) -> bool:
    """检查权限：viewer 是否可以查看 target_employee 的信息。
    
    规则：
    - HR 可以查看所有人
    - 主管可以查看直属下级
    - 员工只能查看自己
    """
    # HR 权限
    if check_is_hr(db, viewer_id):
        return True
    
    # 本人
    if viewer_id == target_employee_id:
        return True
    
    # 主管权限
    if check_is_manager_of(db, viewer_id, target_employee_id):
        return True
    
    return False


def check_can_submit_self_review(db: Session, employee_id: str, record_id: str) -> bool:
    """检查权限：只有该员工才能提交自己的自评。"""
    record = db.query(AssessmentRecord).filter(AssessmentRecord.id == record_id).first()
    if not record:
        return False
    return record.employee_id == employee_id


def check_can_submit_manager_review(db: Session, manager_id: str, record_id: str) -> bool:
    """检查权限：只有该员工的直属主管才能提交评分。"""
    record = db.query(AssessmentRecord).filter(AssessmentRecord.id == record_id).first()
    if not record:
        return False
    return check_is_manager_of(db, manager_id, record.employee_id)


# -------------------------------------------------------
# 1. 发起考核任务 (HR 专属)
# -------------------------------------------------------


@router.post("/tasks")
def create_performance_task(
    request: CreatePerformanceTaskRequest,
    current_user_id: str = Query(default="8d5c97gb", description="当前用户 ID（查询参数或 X-User-ID 头）"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """发起考核任务。
    
    仅 HR 可调用。生成 ConfigSnapshot + AssessmentTask + AssessmentRecord。
    
    Args:
        request.title: 考核标题
        request.period: 考核期间，如 "202611"
        request.target_employee_ids: 被评员工 ID 列表
        request.start_date / end_date: 考核周期（ISO 8601）
        request.self_review_deadline: 自评截止时间
        request.manager_review_deadline: 主管评分截止时间
    
    Returns:
        {
            "code": 0,
            "data": {
                "task_id": "...",
                "status": "created",
                "target_count": 10,
                "message": "..."
            }
        }
    """
    try:
        # 1. 权限检查：仅 HR
        if not check_is_hr(db, current_user_id):
            raise HTTPException(
                status_code=403,
                detail="仅 HR 可发起考核任务"
            )
        
        # 2. 参数验证
        if not request.target_employee_ids:
            raise HTTPException(status_code=400, detail="被评员工列表不能为空")
        
        # 检查所有员工都存在
        employees = db.query(Employee).filter(
            Employee.id.in_(request.target_employee_ids)
        ).all()
        
        found_ids = {e.id for e in employees}
        missing_ids = set(request.target_employee_ids) - found_ids
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"以下员工不存在：{', '.join(missing_ids)}"
            )
        
        # 验证日期顺序
        from datetime import datetime as dt
        start_dt = dt.fromisoformat(request.start_date)
        end_dt = dt.fromisoformat(request.end_date)
        sr_deadline_dt = dt.fromisoformat(request.self_review_deadline)
        mgr_deadline_dt = dt.fromisoformat(request.manager_review_deadline)
        
        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")
        if sr_deadline_dt <= start_dt or sr_deadline_dt > mgr_deadline_dt:
            raise HTTPException(status_code=400, detail="自评/主管评分截止时间顺序错误")
        
        # 3. 创建 AssessmentTask
        task_id = f"TASK_{dt.now().strftime('%Y%m%d%H%M%S')}_{request.period}"
        
        task = AssessmentTask(
            id=task_id,
            name=request.title,
            period=request.period,
            status="created",
            owner_id=current_user_id,
            created_at=dt.now(),
        )
        db.add(task)
        db.flush()
        
        # 4. 批量生成 AssessmentRecord
        records = []
        for emp_id in request.target_employee_ids:
            record = AssessmentRecord(
                id=f"REC_{task_id}_{emp_id}",
                task_id=task_id,
                employee_id=emp_id,
                status="not_started",
                created_at=dt.now(),
                updated_at=dt.now(),
            )
            records.append(record)
        
        db.add_all(records)
        db.commit()
        
        logger.info(f"考核任务创建成功: {task_id}, 包含 {len(records)} 人")
        
        return {
            "code": 0,
            "data": {
                "task_id": task_id,
                "status": "created",
                "target_count": len(records),
                "message": f"成功创建考核任务，包含 {len(records)} 名被评员工"
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"创建考核任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# -------------------------------------------------------
# 2. 查询考核任务
# -------------------------------------------------------


@router.get("/tasks/{task_id}")
def get_performance_task(
    task_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询考核任务详情。"""
    task = db.query(AssessmentTask).filter(AssessmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="考核任务不存在")
    
    # 统计记录状态
    records = db.query(AssessmentRecord).filter(AssessmentRecord.task_id == task_id).all()
    
    status_count = {
        "not_started": sum(1 for r in records if r.status == "not_started"),
        "self_review_submitted": sum(1 for r in records if r.status == "self_review_submitted"),
        "manager_review_submitted": sum(1 for r in records if r.status == "manager_review_submitted"),
        "confirmed": sum(1 for r in records if r.status == "confirmed"),
    }
    
    return {
        "code": 0,
        "data": {
            "task_id": task.id,
            "name": task.name,
            "period": task.period,
            "status": task.status,
            "owner_id": task.owner_id,
            "created_at": task.created_at.isoformat(),
            "total_records": len(records),
            "status_breakdown": status_count,
        }
    }


@router.get("/tasks")
def list_performance_tasks(
    status: Optional[str] = Query(None),
    period: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """分页查询考核任务列表。"""
    query = db.query(AssessmentTask)
    
    if status:
        query = query.filter(AssessmentTask.status == status)
    if period:
        query = query.filter(AssessmentTask.period == period)
    
    total = query.count()
    tasks = query.order_by(AssessmentTask.created_at.desc()).offset(skip).limit(limit).all()
    
    return {
        "code": 0,
        "data": {
            "items": [
                {
                    "task_id": t.id,
                    "name": t.name,
                    "period": t.period,
                    "status": t.status,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tasks
            ],
            "total": total,
            "skip": skip,
            "limit": limit,
        }
    }


# -------------------------------------------------------
# 3. 员工自评
# -------------------------------------------------------


@router.post("/self-reviews")
def submit_self_review(
    request: SubmitSelfReviewRequest,
    current_user_id: str = Query(default="2aaf32ea", description="当前用户 ID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """员工提交自评。
    
    Args:
        request.record_id: 考核记录 ID
        request.performance_summary: 业绩总结
        request.achievements: 主要成就列表
        request.challenges: 遇到的挑战列表
    
    Returns:
        {
            "code": 0,
            "data": {
                "record_id": "...",
                "status": "self_review_submitted",
                "submitted_at": "..."
            }
        }
    """
    try:
        # 1. 检查权限：只能提交自己的自评
        if not check_can_submit_self_review(db, current_user_id, request.record_id):
            raise HTTPException(
                status_code=403,
                detail="无权提交该自评"
            )
        
        # 2. 检查记录状态
        record = db.query(AssessmentRecord).filter(
            AssessmentRecord.id == request.record_id
        ).first()
        
        if not record:
            raise HTTPException(status_code=404, detail="考核记录不存在")
        
        if record.status != "not_started":
            raise HTTPException(
                status_code=409,
                detail=f"当前状态不允许提交自评: {record.status}"
            )
        
        # 3. 保存自评数据
        record.status = "self_review_submitted"
        record.self_review_content = {
            "performance_summary": request.performance_summary,
            "achievements": request.achievements,
            "challenges": request.challenges,
            "submitted_at": datetime.now().isoformat(),
        }
        record.updated_at = datetime.now()
        
        db.add(record)
        db.commit()
        
        logger.info(f"自评提交成功: {request.record_id}")
        
        return {
            "code": 0,
            "data": {
                "record_id": record.id,
                "status": record.status,
                "submitted_at": record.updated_at.isoformat(),
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"提交自评失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@router.get("/records/{record_id}/self-review")
def get_self_review(
    record_id: str,
    current_user_id: str = Query(default="2aaf32ea", description="当前用户 ID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询员工自评内容。
    
    权限检查：
    - 本人可查看
    - 主管可查看下属
    - HR 可查看所有
    """
    record = db.query(AssessmentRecord).filter(
        AssessmentRecord.id == record_id
    ).first()
    
    if not record:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    
    # 权限检查
    if not check_can_view_employee(db, current_user_id, record.employee_id):
        raise HTTPException(status_code=403, detail="无权查看该自评")
    
    return {
        "code": 0,
        "data": {
            "record_id": record.id,
            "employee_id": record.employee_id,
            "status": record.status,
            "self_review": record.self_review_content,
            "submitted_at": record.updated_at.isoformat() if record.status == "self_review_submitted" else None,
        }
    }


# -------------------------------------------------------
# 4. 主管评分
# -------------------------------------------------------


@router.post("/manager-reviews")
def submit_manager_review(
    request: SubmitManagerReviewRequest,
    current_user_id: str = Query(default="8d5c97gb", description="当前用户 ID（主管）"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """主管提交评分。
    
    Args:
        request.record_id: 考核记录 ID
        request.rating: 等级 A/B/C/D
        request.manager_comment: 主管评语
    
    Returns:
        {
            "code": 0,
            "data": {
                "record_id": "...",
                "status": "manager_review_submitted",
                "rating": "A"
            }
        }
    """
    try:
        # 1. 检查权限：主管可以评分
        if not check_can_submit_manager_review(db, current_user_id, request.record_id):
            raise HTTPException(
                status_code=403,
                detail="无权提交该员工的评分"
            )
        
        # 2. 检查记录
        record = db.query(AssessmentRecord).filter(
            AssessmentRecord.id == request.record_id
        ).first()
        
        if not record:
            raise HTTPException(status_code=404, detail="考核记录不存在")
        
        if record.status not in ("not_started", "self_review_submitted"):
            raise HTTPException(
                status_code=409,
                detail=f"当前状态不允许提交评分: {record.status}"
            )
        
        # 3. 验证等级
        if request.rating not in ("A", "B", "C", "D"):
            raise HTTPException(status_code=400, detail="等级必须是 A/B/C/D")
        
        # 4. 保存评分
        record.status = "manager_review_submitted"
        record.confirmed_rating = request.rating
        record.manager_comment = request.manager_comment
        record.updated_at = datetime.now()
        
        db.add(record)
        db.commit()
        
        logger.info(f"主管评分提交成功: {request.record_id}, 等级: {request.rating}")
        
        return {
            "code": 0,
            "data": {
                "record_id": record.id,
                "status": record.status,
                "rating": record.confirmed_rating,
                "submitted_at": record.updated_at.isoformat(),
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"提交评分失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# -------------------------------------------------------
# 5. 确认最终成绩 (HR)
# -------------------------------------------------------


@router.post("/tasks/{task_id}/confirm")
def confirm_performance_task(
    task_id: str,
    current_user_id: str = Query(default="8d5c97gb", description="当前用户 ID（HR）"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """HR 确认最终成绩，任务进入 confirmed 状态。
    
    Args:
        task_id: 任务 ID
    
    Returns:
        {
            "code": 0,
            "data": {
                "task_id": "...",
                "status": "confirmed",
                "confirmed_records": 10,
                "message": "..."
            }
        }
    """
    try:
        # 1. 权限检查
        if not check_is_hr(db, current_user_id):
            raise HTTPException(status_code=403, detail="仅 HR 可确认成绩")
        
        # 2. 检查任务
        task = db.query(AssessmentTask).filter(AssessmentTask.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="考核任务不存在")
        
        # 3. 检查所有记录是否已评分
        records = db.query(AssessmentRecord).filter(
            AssessmentRecord.task_id == task_id
        ).all()
        
        unfinished = [r for r in records if r.status != "manager_review_submitted"]
        if unfinished:
            raise HTTPException(
                status_code=409,
                detail=f"还有 {len(unfinished)} 条记录未完成评分"
            )
        
        # 4. 更新所有记录为 confirmed
        for record in records:
            record.status = "confirmed"
            record.updated_at = datetime.now()
        
        # 5. 更新任务状态
        task.status = "confirmed"
        
        db.add_all(records)
        db.add(task)
        db.commit()
        
        logger.info(f"考核任务确认完成: {task_id}, {len(records)} 条记录")
        
        return {
            "code": 0,
            "data": {
                "task_id": task.id,
                "status": task.status,
                "confirmed_records": len(records),
                "message": f"成功确认 {len(records)} 名员工的考核成绩"
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"确认成绩失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# -------------------------------------------------------
# 6. 查询成绩（权限检查）
# -------------------------------------------------------


@router.get("/records/{record_id}")
def get_performance_record(
    record_id: str,
    current_user_id: str = Query(default="2aaf32ea", description="当前用户 ID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询考核记录详情。
    
    权限检查：
    - 本人可查看
    - 主管可查看下属
    - HR 可查看所有
    """
    record = db.query(AssessmentRecord).filter(
        AssessmentRecord.id == record_id
    ).first()
    
    if not record:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    
    # 权限检查
    if not check_can_view_employee(db, current_user_id, record.employee_id):
        raise HTTPException(status_code=403, detail="无权查看该考核记录")
    
    emp = db.query(Employee).filter(Employee.id == record.employee_id).first()
    
    return {
        "code": 0,
        "data": {
            "record_id": record.id,
            "task_id": record.task_id,
            "employee": {
                "id": emp.id,
                "name": emp.name,
                "department_id": emp.department_id,
                "position": emp.position,
            } if emp else None,
            "status": record.status,
            "rating": record.confirmed_rating,
            "manager_comment": record.manager_comment,
            "self_review": record.self_review_content if record.status in (
                "self_review_submitted",
                "manager_review_submitted",
                "confirmed"
            ) else None,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }
    }


@router.get("/tasks/{task_id}/records")
def list_task_records(
    task_id: str,
    status: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    current_user_id: str = Query(default="8d5c97gb", description="当前用户 ID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询任务的所有记录。
    
    权限检查：
    - HR 可查看所有
    - 主管只能查看自己部门的员工
    """
    task = db.query(AssessmentTask).filter(AssessmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="考核任务不存在")
    
    # 基础查询
    query = db.query(AssessmentRecord).filter(AssessmentRecord.task_id == task_id)
    
    if status:
        query = query.filter(AssessmentRecord.status == status)
    if rating:
        query = query.filter(AssessmentRecord.confirmed_rating == rating)
    
    records = query.all()
    
    # HR 可以查看所有，主管只能查看下属
    result_records = []
    for r in records:
        if check_can_view_employee(db, current_user_id, r.employee_id):
            result_records.append(r)
    
    result_data = []
    for r in result_records:
        emp = db.query(Employee).filter(Employee.id == r.employee_id).first()
        result_data.append({
            "record_id": r.id,
            "employee": {
                "id": emp.id,
                "name": emp.name,
            } if emp else None,
            "status": r.status,
            "rating": r.confirmed_rating,
            "updated_at": r.updated_at.isoformat(),
        })
    
    return {
        "code": 0,
        "data": {
            "task_id": task.id,
            "records": result_data,
            "total": len(result_data),
        }
    }


# -------------------------------------------------------
# 7. 员工查询个人成绩
# -------------------------------------------------------


@router.get("/my-performance")
def get_my_performance(
    task_id: Optional[str] = Query(None),
    current_user_id: str = Query(default="2aaf32ea", description="当前用户 ID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """员工查询自己的成绩。"""
    query = db.query(AssessmentRecord).filter(
        AssessmentRecord.employee_id == current_user_id
    )
    
    if task_id:
        query = query.filter(AssessmentRecord.task_id == task_id)
    
    records = query.order_by(AssessmentRecord.updated_at.desc()).all()
    
    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "record_id": r.id,
                    "task_id": r.task_id,
                    "status": r.status,
                    "rating": r.confirmed_rating,
                    "manager_comment": r.manager_comment,
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in records
            ],
            "total": len(records),
        }
    }
