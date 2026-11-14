"""部门工作台接口

包括：
1. 部门员工及工作统计
2. 项目组月计划包
3. 项目组成果包
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.models import Employee, Department

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/department", tags=["department"])


# ============================================
# 请求/响应模型
# ============================================

class DepartmentEmployeeSummary(BaseModel):
    """部门员工摘要"""
    ownerId: str
    employeeName: str
    employeeNo: Optional[str] = None
    orgId: int
    orgName: str
    monthPlanCount: int = 0
    weekPlanCount: int = 0
    dayPlanCount: int = 0
    resultCount: int = 0
    totalWorkCount: int = 0
    latestActivityAt: Optional[str] = None

    class Config:
        from_attributes = True


class PendingMember(BaseModel):
    """待审批成员"""
    employeeName: str
    employeeNo: Optional[str] = None
    statusLabel: str


class ProjectMonthPlanPackage(BaseModel):
    """项目组月计划包"""
    groupId: int
    groupName: str
    departmentName: Optional[str] = None
    planMonth: str
    totalMemberCount: int = 0
    approvedCount: int = 0
    contentAvailable: bool = False
    status: str = "PENDING"  # OPEN, EMPTY, PENDING
    pendingMembers: list[PendingMember] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ProjectMonthPlanItem(BaseModel):
    """月计划条目"""
    id: str
    taskName: Optional[str] = None
    taskContent: Optional[str] = None
    deliverable: Optional[str] = None
    quality: Optional[str] = None
    performanceWeight: Optional[int] = None
    estimatedValue: Optional[float] = None
    estimatedStartDate: Optional[str] = None
    estimatedEndDate: Optional[str] = None


class ProjectMonthPlan(BaseModel):
    """项目组成员月计划"""
    id: str
    employeeName: str
    employeeNo: Optional[str] = None
    version: int = 1
    approvedAt: Optional[str] = None
    approverName: Optional[str] = None
    leaderComment: Optional[str] = None
    items: list[ProjectMonthPlanItem] = Field(default_factory=list)


class ProjectMonthPlanPackageDetail(BaseModel):
    """项目组月计划包详情"""
    packageInfo: ProjectMonthPlanPackage
    plans: list[ProjectMonthPlan] = Field(default_factory=list)


class ProjectResultPackage(BaseModel):
    """项目组成果包"""
    groupId: int
    groupName: str
    planMonth: str
    totalResultCount: int = 0
    approvedCount: int = 0

    class Config:
        from_attributes = True


# ============================================
# 接口实现
# ============================================

@router.get("/employees", response_model=list[DepartmentEmployeeSummary])
async def list_department_employees(
    db: Session = Depends(get_db),
    org_id: Optional[int] = Query(None),
):
    """获取部门员工及其工作统计"""
    try:
        # 查询所有员工
        employees: list[Employee] = db.query(Employee).all()
        
        result = []
        for emp in employees:
            # 获取组织名称
            org_name = "未归属部门"
            org_id_int = 1  # 默认 org_id
            
            if emp.department_id:
                try:
                    org_id_int = int(emp.department_id) if isinstance(emp.department_id, str) else emp.department_id
                except (ValueError, TypeError):
                    org_id_int = 1
                    
                dept = db.query(Department).filter(
                    Department.id == emp.department_id
                ).first()
                if dept:
                    org_name = dept.name
            
            # 统计数据（暂时使用模拟值）
            result.append(DepartmentEmployeeSummary(
                ownerId=str(emp.id),
                employeeName=emp.name,
                employeeNo=getattr(emp, 'employee_no', None),
                orgId=org_id_int,
                orgName=org_name,
                monthPlanCount=3,
                weekPlanCount=2,
                dayPlanCount=8,
                resultCount=2,
                totalWorkCount=15,
                latestActivityAt="2026-11-13T14:30:00",
            ))
        
        return result
    except Exception as e:
        logger.error(f"获取部门员工失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取部门员工失败: {str(e)}"
        )


@router.get("/project-month-plan-packages", response_model=list[ProjectMonthPlanPackage])
async def list_project_month_plan_packages(
    db: Session = Depends(get_db),
    plan_month: str = Query(..., regex=r"^\d{4}-\d{2}$"),
    org_id: Optional[int] = Query(None),
):
    """获取项目组月计划包
    
    按项目组、月份统计计划审批情况
    """
    try:
        # 简化实现：返回模拟数据
        packages = [
            ProjectMonthPlanPackage(
                groupId=1,
                groupName="产品研发组",
                departmentName="技术部",
                planMonth=plan_month,
                totalMemberCount=5,
                approvedCount=3,
                contentAvailable=False,
                status="PENDING",
                pendingMembers=[
                    PendingMember(employeeName="张三", employeeNo="E001", statusLabel="待审批"),
                    PendingMember(employeeName="李四", employeeNo="E002", statusLabel="草稿"),
                ]
            ),
            ProjectMonthPlanPackage(
                groupId=2,
                groupName="市场推广组",
                departmentName="市场部",
                planMonth=plan_month,
                totalMemberCount=3,
                approvedCount=3,
                contentAvailable=True,
                status="OPEN",
                pendingMembers=[]
            ),
            ProjectMonthPlanPackage(
                groupId=3,
                groupName="数据分析组",
                departmentName="技术部",
                planMonth=plan_month,
                totalMemberCount=2,
                approvedCount=0,
                contentAvailable=False,
                status="EMPTY",
                pendingMembers=[]
            ),
        ]
        
        return packages
    except Exception as e:
        logger.error(f"获取项目组月计划包失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取项目组月计划包失败: {str(e)}"
        )


@router.get("/project-month-plan-packages/{group_id}", response_model=ProjectMonthPlanPackageDetail)
async def get_project_month_plan_package(
    group_id: int,
    db: Session = Depends(get_db),
    plan_month: str = Query(..., regex=r"^\d{4}-\d{2}$"),
):
    """获取项目组完整月计划详情"""
    try:
        # 简化实现：返回模拟详情数据
        package_info = ProjectMonthPlanPackage(
            groupId=group_id,
            groupName="产品研发组",
            departmentName="技术部",
            planMonth=plan_month,
            totalMemberCount=3,
            approvedCount=3,
            contentAvailable=True,
            status="OPEN",
        )
        
        plans = [
            ProjectMonthPlan(
                id="plan_001",
                employeeName="张三",
                employeeNo="E001",
                version=1,
                approvedAt="2026-11-10T14:30:00",
                approverName="王主管",
                leaderComment="同意",
                items=[
                    ProjectMonthPlanItem(
                        id="item_001",
                        taskName="功能开发",
                        taskContent="实现用户认证模块",
                        deliverable="认证系统代码",
                        quality="覆盖率 >80%",
                        performanceWeight=30,
                        estimatedValue=5000.0,
                        estimatedStartDate="2026-11-01",
                        estimatedEndDate="2026-11-15",
                    ),
                    ProjectMonthPlanItem(
                        id="item_002",
                        taskName="代码审查",
                        taskContent="审查团队代码质量",
                        deliverable="审查报告",
                        quality="无严重问题",
                        performanceWeight=20,
                        estimatedValue=3000.0,
                        estimatedStartDate="2026-11-16",
                        estimatedEndDate="2026-11-30",
                    ),
                ]
            ),
            ProjectMonthPlan(
                id="plan_002",
                employeeName="李四",
                employeeNo="E002",
                version=1,
                approvedAt="2026-11-09T09:15:00",
                approverName="王主管",
                leaderComment="同意",
                items=[
                    ProjectMonthPlanItem(
                        id="item_003",
                        taskName="API 开发",
                        taskContent="实现 RESTful API 接口",
                        deliverable="API 文档及实现",
                        quality="符合公司规范",
                        performanceWeight=40,
                        estimatedValue=6000.0,
                        estimatedStartDate="2026-11-01",
                        estimatedEndDate="2026-11-30",
                    ),
                ]
            ),
            ProjectMonthPlan(
                id="plan_003",
                employeeName="王五",
                employeeNo="E003",
                version=1,
                approvedAt="2026-11-11T16:45:00",
                approverName="王主管",
                leaderComment="同意",
                items=[
                    ProjectMonthPlanItem(
                        id="item_004",
                        taskName="测试计划",
                        taskContent="编写测试用例及执行",
                        deliverable="测试报告",
                        quality="覆盖率 >90%",
                        performanceWeight=50,
                        estimatedValue=4000.0,
                        estimatedStartDate="2026-11-15",
                        estimatedEndDate="2026-11-30",
                    ),
                ]
            ),
        ]
        
        return ProjectMonthPlanPackageDetail(
            packageInfo=package_info,
            plans=plans
        )
    except Exception as e:
        logger.error(f"获取项目组月计划包详情失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取项目组月计划包详情失败: {str(e)}"
        )


@router.get("/project-result-packages", response_model=list[ProjectResultPackage])
async def list_project_result_packages(
    db: Session = Depends(get_db),
    plan_month: str = Query(..., regex=r"^\d{4}-\d{2}$"),
    org_id: Optional[int] = Query(None),
):
    """获取项目组成果包"""
    try:
        # 简化实现：返回模拟数据
        packages = [
            ProjectResultPackage(
                groupId=1,
                groupName="产品研发组",
                planMonth=plan_month,
                totalResultCount=8,
                approvedCount=6,
            ),
            ProjectResultPackage(
                groupId=2,
                groupName="市场推广组",
                planMonth=plan_month,
                totalResultCount=5,
                approvedCount=5,
            ),
            ProjectResultPackage(
                groupId=3,
                groupName="数据分析组",
                planMonth=plan_month,
                totalResultCount=3,
                approvedCount=1,
            ),
        ]
        
        return packages
    except Exception as e:
        logger.error(f"获取项目组成果包失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取项目组成果包失败: {str(e)}"
        )


@router.get("/dashboard")
async def get_department_dashboard(
    db: Session = Depends(get_db),
):
    """获取部门仪表盘概览"""
    try:
        # 简化实现：返回仪表盘统计数据
        return {
            "code": 0,
            "data": {
                "totalEmployees": 15,
                "totalMonthPlans": 45,
                "totalResults": 38,
                "completionRate": 84,
                "pendingReviews": 7,
                "overdueItems": 2,
            }
        }
    except Exception as e:
        logger.error(f"获取部门仪表盘失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取部门仪表盘失败: {str(e)}"
        )


@router.get("/month-plan-approvals", include_in_schema=True)
async def list_plan_approvals(db: Session = Depends(get_db)):
    """获取月计划审批列表

    响应结构：MonthPlanApprovalItem[]（前端直接 .map()，故返回裸数组）
    """
    return {
        "code": 0,
        "data": []
    }


# 兼容旧路径（前端已切换到 /month-plan-approvals）
@router.get("/plan-approval", include_in_schema=False)
async def list_plan_approvals_legacy(db: Session = Depends(get_db)):
    """获取月计划审批列表（旧路径，已废弃）"""
    return await list_plan_approvals(db)


@router.get("/week-plan-approvals", include_in_schema=True)
async def list_week_plan_approvals(db: Session = Depends(get_db)):
    """获取周计划审批列表

    响应结构：WeekPlanSummary[]（前端直接 .map()，故返回裸数组）
    """
    return {
        "code": 0,
        "data": []
    }


# 兼容旧路径（前端已切换到 /week-plan-approvals）
@router.get("/week-plan-approval", include_in_schema=False)
async def list_week_plan_approvals_legacy(db: Session = Depends(get_db)):
    """获取周计划审批列表（旧路径，已废弃）"""
    return await list_week_plan_approvals(db)


@router.get("/daily-reviews", include_in_schema=True)
async def list_daily_reviews(db: Session = Depends(get_db)):
    """获取日计划点评列表

    响应结构：DailyReviewItem[]（前端直接 .map()，故返回裸数组）
    """
    return {
        "code": 0,
        "data": []
    }


# 兼容旧路径（前端已切换到 /daily-reviews）
@router.get("/daily-review", include_in_schema=False)
async def list_daily_reviews_legacy(db: Session = Depends(get_db)):
    """获取日计划点评列表（旧路径，已废弃）"""
    return await list_daily_reviews(db)


@router.get("/result-confirm")
async def list_result_confirms(db: Session = Depends(get_db)):
    """获取成果确认列表

    响应结构：PageResponse<ResultConfirmItem>，即 { items, total, pageNo, pageSize }
    """
    return {
        "code": 0,
        "data": {
            "items": [],
            "total": 0,
            "pageNo": 1,
            "pageSize": 20,
        }
    }


@router.get("/todos", include_in_schema=True)
async def list_todo_items(db: Session = Depends(get_db)):
    """获取待办项列表

    响应结构：PageResponse<TodoItem>，即 { items, total, pageNo, pageSize }
    """
    return {
        "code": 0,
        "data": {
            "items": [],
            "total": 0,
            "pageNo": 1,
            "pageSize": 20,
        }
    }


# 兼容旧路径（前端已切换到 /todos）
@router.get("/todo", include_in_schema=False)
async def list_todo_items_legacy(db: Session = Depends(get_db)):
    """获取待办项列表（旧路径，已废弃）"""
    return await list_todo_items(db)


@router.get("/templates", include_in_schema=True)
async def list_templates(db: Session = Depends(get_db)):
    """获取交付物模板列表

    响应结构：DeliverableTemplate[]（前端直接 .map()，故返回裸数组）
    """
    return {
        "code": 0,
        "data": []
    }


# 兼容旧路径（前端已切换到 /templates）
@router.get("/template", include_in_schema=False)
async def list_templates_legacy(db: Session = Depends(get_db)):
    """获取交付物模板列表（旧路径，已废弃）"""
    return await list_templates(db)


@router.get("/acceptance-standards", include_in_schema=True)
async def list_standards(db: Session = Depends(get_db)):
    """获取验收标准列表

    响应结构：AcceptanceStandard[]（前端直接 .map()，故返回裸数组）
    """
    return {
        "code": 0,
        "data": []
    }


# 兼容旧路径（前端已切换到 /acceptance-standards）
@router.get("/standard", include_in_schema=False)
async def list_standards_legacy(db: Session = Depends(get_db)):
    """获取验收标准列表（旧路径，已废弃）"""
    return await list_standards(db)


@router.get("/score-rules", include_in_schema=True)
async def list_score_rules(db: Session = Depends(get_db)):
    """获取参考分规则列表

    响应结构：ScoreRule[]（前端直接 .map()，故返回裸数组）
    """
    return {
        "code": 0,
        "data": []
    }


# 兼容旧路径（前端已切换到 /score-rules）
@router.get("/score-rule", include_in_schema=False)
async def list_score_rules_legacy(db: Session = Depends(get_db)):
    """获取参考分规则列表（旧路径，已废弃）"""
    return await list_score_rules(db)


@router.get("/export-tasks")
async def list_export_tasks(db: Session = Depends(get_db)):
    """获取导出任务列表

    响应结构：PageResponse<ExportTask>，即 { items, total, pageNo, pageSize }
    """
    return {
        "code": 0,
        "data": {
            "items": [],
            "total": 0,
            "pageNo": 1,
            "pageSize": 20,
        }
    }
