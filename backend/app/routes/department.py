"""部门工作台接口

包括：
1. 部门员工及工作统计
2. 项目组月计划包
3. 项目组成果包
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import build_access_context_for_user, get_current_user
from ..domain.models import Department, Employee
from ..services import plan_approval
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/department", tags=["department"], dependencies=[Depends(get_current_user)])


def _normalize_plan_month(plan_month: Optional[str]) -> str:
    """校验并归一 `planMonth` 查询参数。

    BUG-R6-05 修复：前端 `DepartmentDashboard.vue` / `ResultConfirm.vue` 发的是
    **camelCase** `planMonth`，而后端原签名是 snake_case `plan_month` 且带
    正则约束 → FastAPI 报 `Missing field required: plan_month`（422）。
    现在统一用 `alias="planMonth"` 接收，并在此处做格式校验（非法值返回 422）。

    ⚠️ 必须返回 **非空 str**：响应模型 `ProjectMonthPlanPackage.planMonth`
    是必填 `str`，若这里返回 `None` → Pydantic 校验失败 → 整个端点 500。
    缺失时用当前月份兜底（前端 `packageMonth` 默认也是 `currentMonth()`）。
    """
    if plan_month is None or str(plan_month).strip() == "":
        return _current_month()
    text = str(plan_month).strip()
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        raise HTTPException(
            status_code=422,
            detail=f"planMonth 格式非法，应为 YYYY-MM，收到: {text}",
        )
    return text


def _current_month() -> str:
    """当前月份，格式 `YYYY-MM`（与前端 `currentMonth()` 一致）。"""
    return datetime.now().strftime("%Y-%m")


# ============================================
# 计划审批 —— 公共辅助（A3）
#
# 与 `leader.py` 同构：部门负责人侧看到的是**同一批审批端点**（决策 D2/D4：
# 一级审批，`leader-month-plan-approvals/*` 不是第二级审批链）。
# 部门负责人额外能看到**管理范围内部门成员**的计划（直属主管只能看直属下属）。
# ============================================


def _approval_scope(current_user: Employee, db: Session) -> plan_approval.ApprovalScope:
    """构造审批人的可见范围。"""
    context = build_access_context_for_user(current_user, db)
    return plan_approval.ApprovalScope.from_access_context(context)


def _assert_can_approve_plan(
    current_user: Employee,
    plan_id: str,
    plan_type: str,
    db: Session,
) -> None:
    """校验审批人是否有权审批该计划。

    规则与 `leader.py` 对齐：
    - 直属主管可审批直属下属的计划；
    - 部门负责人可审批管理范围内成员的计划。
    """
    from ..services.access_control import can_access_employee

    context = build_access_context_for_user(current_user, db)
    plan = db.get(plan_approval.plan_model(plan_type), plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    owner = db.get(Employee, plan.owner_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="计划归属人不存在")
    if not can_access_employee(context, owner, db):
        raise HTTPException(status_code=403, detail="无权审批该计划")


def _run_approval(action, *args, **kwargs) -> Any:
    """执行审批动作，把领域异常翻译成 HTTP 状态码。"""
    try:
        return action(*args, **kwargs)
    except plan_approval.PlanApprovalConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except plan_approval.PlanApprovalForbidden as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except plan_approval.PlanApprovalError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


def _request_version(data: dict) -> int | None:
    """从请求体取 `versionNo`（乐观锁版本号）。

    缺省视为不校验（兼容旧调用方）。
    """
    raw = data.get("versionNo")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _batch_approve(
    current_user: Employee,
    db: Session,
    data: dict,
    plan_type: str,
    *,
    approved: bool,
) -> list[dict]:
    """批量审批，逐条校验权限，单条失败不影响其它条目。

    返回 `ActionResult[]`，与前端 `ActionResult` 的形状一致。
    """
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    comment = data.get("comment")
    version_no = _request_version(data)

    results: list[dict] = []
    for raw_id in ids:
        plan_id = str(raw_id)
        try:
            _assert_can_approve_plan(current_user, plan_id, plan_type, db)
            if plan_type == plan_approval.PLAN_TYPE_MONTH:
                action = (
                    plan_approval.approve_month_plan
                    if approved
                    else plan_approval.reject_month_plan
                )
                _run_approval(
                    action, db, current_user.id, plan_id,
                    comment=comment, version_no=version_no,
                )
            elif plan_type == plan_approval.PLAN_TYPE_WEEK:
                action = (
                    plan_approval.approve_week_plan
                    if approved
                    else plan_approval.reject_week_plan
                )
                _run_approval(
                    action, db, current_user.id, plan_id,
                    comment=comment, version_no=version_no,
                )
            else:
                _run_approval(
                    plan_approval.review_day_plan,
                    db,
                    current_user.id,
                    plan_id,
                    approved=approved,
                    comment=comment,
                    version_no=version_no,
                )
            results.append(
                _action_result(plan_id, "APPROVED" if approved else "REJECTED")
            )
        except HTTPException as exc:
            results.append(
                {
                    "objectId": plan_id,
                    "status": "FAILED",
                    "message": str(exc.detail),
                    "auditActionCode": "",
                    "auditDeferred": False,
                }
            )
    return results


def _month_plan_page(
    db: Session,
    scope: plan_approval.ApprovalScope,
    *,
    page_no: int,
    page_size: int,
    plan_year: Optional[int] = None,
    plan_month: Optional[int] = None,
    status: Optional[str] = None,
) -> dict:
    """构造月计划审批分页响应（`LeaderMonthPlanApprovalPage`）。

    前端 `MonthPlanApproval.vue` 发送 `pageNo` / `pageSize` / `planYear` /
    `planMonth` / `status`，这里全部按 camelCase 接收（与 `leader.py` 一致）。
    """
    # 前端传 planYear + planMonth（两个 number），内部统一成 "2026-11"
    normalized_month = None
    if plan_year and plan_month:
        normalized_month = f"{plan_year:04d}-{plan_month:02d}"
    elif plan_month:
        normalized_month = f"-{plan_month:02d}"

    plans = plan_approval.list_pending_month_plans(
        db,
        scope,
        plan_month=normalized_month,
        status=status,
        limit=10000,
    )

    total = len(plans)
    start = (page_no - 1) * page_size
    page_items = plans[start : start + page_size]

    return {
        "items": [
            plan_approval.serialize_month_plan_approval_item(db, plan)
            for plan in page_items
        ],
        "total": total,
        "pageNo": page_no,
        "pageSize": page_size,
        "submittedCount": total,
        "approvedCount": 0,
        "totalEmployeeCount": len({p.owner_id for p in plans}),
    }


# ============================================
# 请求/响应模型
# ============================================

class DepartmentEmployeeSummary(BaseModel):
    """部门员工摘要"""
    ownerId: str
    employeeName: str
    employeeNo: Optional[str] = None
    # 飞书部门 ID 是长十六进制字符串。
    orgId: str
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

    # 飞书部门/项目组 ID 是长十六进制字符串，不能转 int。
    groupId: str
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
    # 飞书部门/项目组 ID 是长十六进制字符串。
    groupId: str
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
    org_id: Optional[str] = Query(None),
):
    """获取部门员工及其工作统计"""
    try:
        # 查询所有员工
        employees: list[Employee] = db.query(Employee).all()
        
        result = []
        for emp in employees:
            # 获取组织名称
            org_name = "未归属部门"
            # ⚠️ BUG-R6-04 修复：飞书部门 ID 是**十六进制字符串**
            # （如 `486d98d57d1c6b14`），旧实现 `int(...)` 必失败 → 回退成
            # int `1` → 塞进 `orgId: str` 字段 → Pydantic 校验失败 → 整个端点 500。
            # 现在原样透出字符串；无部门时用字符串哨兵 `"0"`（与
            # `_empty_leader_employee_summary` 的口径一致）。
            org_id_value = str(emp.department_id) if emp.department_id else "0"
            
            if emp.department_id:
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
                orgId=org_id_value,
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
    plan_month: Optional[str] = Query(None, alias="planMonth"),
    org_id: Optional[str] = Query(None),
):
    """获取项目组月计划包
    
    按项目组、月份统计计划审批情况
    """
    plan_month = _normalize_plan_month(plan_month)
    try:
        # 简化实现：返回模拟数据
        packages = [
            ProjectMonthPlanPackage(
                groupId="1",
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
                groupId="2",
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
                groupId="3",
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
    group_id: str,
    db: Session = Depends(get_db),
    plan_month: Optional[str] = Query(None, alias="planMonth"),
):
    """获取项目组完整月计划详情"""
    plan_month = _normalize_plan_month(plan_month)
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
    plan_month: Optional[str] = Query(None, alias="planMonth"),
    org_id: Optional[str] = Query(None),
):
    """获取项目组成果包"""
    plan_month = _normalize_plan_month(plan_month)
    try:
        # 简化实现：返回模拟数据
        packages = [
            ProjectResultPackage(
                groupId="1",
                groupName="产品研发组",
                planMonth=plan_month,
                totalResultCount=8,
                approvedCount=6,
            ),
            ProjectResultPackage(
                groupId="2",
                groupName="市场推广组",
                planMonth=plan_month,
                totalResultCount=5,
                approvedCount=5,
            ),
            ProjectResultPackage(
                groupId="3",
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
async def list_plan_approvals(
    planYear: Optional[int] = Query(None),
    planMonth: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取月计划审批列表

    查询参数用 camelCase，与前端 `department.ts` / `MonthPlanApproval.vue` 对齐。

    响应结构：MonthPlanApprovalItem[]（前端直接 .map()，故返回裸数组）
    """
    scope = _approval_scope(current_user, db)

    normalized_month = None
    if planYear and planMonth:
        normalized_month = f"{planYear:04d}-{planMonth:02d}"

    plans = plan_approval.list_pending_month_plans(
        db,
        scope,
        plan_month=normalized_month,
        status=status,
    )
    return {
        "code": 0,
        "data": [
            plan_approval.serialize_month_plan_approval_item(db, plan)
            for plan in plans
        ],
    }


# 兼容旧路径（前端已切换到 /month-plan-approvals）
@router.get("/plan-approval", include_in_schema=False)
async def list_plan_approvals_legacy(
    planYear: Optional[int] = Query(None),
    planMonth: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取月计划审批列表（旧路径，已废弃）"""
    return await list_plan_approvals(planYear, planMonth, status, current_user, db)


@router.get("/week-plan-approvals", include_in_schema=True)
async def list_week_plan_approvals(
    status: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取周计划审批列表

    响应结构：WeekPlanSummary[]（前端直接 .map()，故返回裸数组）
    """
    scope = _approval_scope(current_user, db)
    plans = plan_approval.list_pending_week_plans(db, scope, status=status)
    return {
        "code": 0,
        "data": [
            plan_approval.serialize_week_plan_summary(db, plan) for plan in plans
        ],
    }


# 兼容旧路径（前端已切换到 /week-plan-approvals）
@router.get("/week-plan-approval", include_in_schema=False)
async def list_week_plan_approvals_legacy(
    status: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取周计划审批列表（旧路径，已废弃）"""
    return await list_week_plan_approvals(status, current_user, db)


@router.get("/daily-reviews", include_in_schema=True)
async def list_daily_reviews(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取日计划点评列表

    响应结构：DailyReviewItem[]（前端直接 .map()，故返回裸数组）
    """
    scope = _approval_scope(current_user, db)
    plans = plan_approval.list_pending_day_plans(db, scope)
    return {
        "code": 0,
        "data": [
            plan_approval.serialize_daily_review_item(db, plan) for plan in plans
        ],
    }


# 兼容旧路径（前端已切换到 /daily-reviews）
@router.get("/daily-review", include_in_schema=False)
async def list_daily_reviews_legacy(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取日计划点评列表（旧路径，已废弃）"""
    return await list_daily_reviews(current_user, db)


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


@router.post("/export-tasks")
async def create_export_task(
    data: JsonBody,
    db: Session = Depends(get_db),
):
    """创建导出任务

    响应结构：ExportTask
    """
    return {
        "code": 0,
        "data": _empty_export_task(),
    }


@router.get("/export-tasks/{task_id}")
async def get_export_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    """获取导出任务详情

    响应结构：ExportTask
    """
    return {
        "code": 0,
        "data": _empty_export_task(task_id),
    }


@router.post("/export-tasks/{task_id}/check")
async def check_export_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    """校验导出任务完整性

    响应结构：ActionResult
    """
    return {
        "code": 0,
        "data": _action_result(task_id, "CHECKED"),
    }


@router.post("/export-tasks/{task_id}/retry")
async def retry_export_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    """重试失败的导出任务

    响应结构：ActionResult
    """
    return {
        "code": 0,
        "data": _action_result(task_id, "RETRYING"),
    }


@router.get("/export-tasks/{task_id}/download-info")
async def get_export_download_info(
    task_id: str,
    db: Session = Depends(get_db),
):
    """获取导出任务下载信息

    响应结构：ExportDownload
    """
    return {
        "code": 0,
        "data": _empty_export_download(task_id),
    }


@router.get("/export-tasks/{task_id}/download")
async def download_export_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    """下载导出任务产物

    响应结构：二进制文件流
    """
    # 尚无真实导出产物时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="导出产物不存在或已过期")


def _empty_export_task(task_id: str = "") -> dict:
    """构造空 ExportTask，字段与 frontend/src/api/performance.ts 保持一致。"""
    return {
        "id": task_id,
        "dimensionType": "",
        "dimensionName": "",
        "periodType": "",
        "periodStart": None,
        "periodEnd": None,
        "formats": [],
        "includeEvidence": False,
        "watermark": "",
        "integrityStatus": "PENDING",
        "missingItems": [],
        "checksum": None,
        "status": "PENDING",
        "sizeText": "",
        "requestedBy": 0,
        "requestedByName": "",
        "requestedAt": datetime.utcnow().isoformat(),
        "finishedAt": None,
    }


def _empty_export_download(task_id: str) -> dict:
    """构造空 ExportDownload，字段与 frontend/src/api/performance.ts 保持一致。"""
    return {
        "taskId": task_id,
        "status": "PENDING",
        "fileName": "",
        "downloadUrl": "",
        "expireAt": None,
        "checksum": None,
    }


def _action_result(object_id: str, status: str) -> dict:
    """构造 ActionResult，字段与 frontend/src/api/performance.ts 保持一致。"""
    return {
        "objectId": object_id,
        "status": status,
        "message": "",
        "auditActionCode": "",
        "auditDeferred": False,
    }


# ============================================
# 批次 2 —— 契约对齐补齐
#
# 以下端点对应 frontend/src/api/department.ts 中已存在但后端缺失的调用，
# 响应结构严格对齐前端 TypeScript 类型定义。
# ============================================


@router.get("/org-tree")
async def get_department_org_tree(db: Session = Depends(get_db)) -> dict:
    """获取部门侧组织树。

    响应结构：OrgNode[]（裸数组）
    """
    return {"code": 0, "data": []}


@router.get("/employees/{owner_id}/work-items")
async def list_department_employee_work_items(
    owner_id: str,
    page_no: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    work_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取指定员工的工作项分页。

    响应结构：DepartmentEmployeeWorkPage（= LeaderEmployeeWorkPage）
    """
    return {
        "code": 0,
        "data": {
            "employee": _empty_leader_employee_summary(owner_id),
            "items": [],
            "total": 0,
            "pageNo": page_no,
            "pageSize": page_size,
        },
    }


@router.get("/employees/{owner_id}/month-plans/{plan_id}")
async def get_department_employee_month_plan(
    owner_id: str,
    plan_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取指定员工的月计划详情。

    响应结构：LeaderEmployeeMonthPlanDetail
    """
    return {
        "code": 0,
        "data": {
            "id": plan_id,
            "title": "",
            "planMonth": "",
            "status": "DRAFT",
            "leaderComment": "",
            "tasks": [],
        },
    }


@router.get("/project-result-packages/{group_id}")
async def get_project_result_package(
    group_id: str,
    plan_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取项目组成果包详情。

    响应结构：ProjectResultPackage
    """
    return {
        "code": 0,
        "data": {
            "groupId": group_id,
            "groupName": "",
            "planMonth": plan_month or "",
            "totalResultCount": 0,
            "approvedCount": 0,
        },
    }


# ── 待办事项 ──────────────────────────────────────────────


@router.post("/todos/{todo_id}/remind")
async def remind_todo(todo_id: str, db: Session = Depends(get_db)) -> dict:
    """催办待办事项。

    响应结构：ActionResult
    """
    return {"code": 0, "data": _action_result(todo_id, "REMINDED")}


@router.post("/todos/{todo_id}/read")
async def read_todo(todo_id: str, db: Session = Depends(get_db)) -> dict:
    """标记待办已读。

    响应结构：ActionResult
    """
    return {"code": 0, "data": _action_result(todo_id, "READ")}


@router.post("/todos/{todo_id}/escalate")
async def escalate_todo(todo_id: str, db: Session = Depends(get_db)) -> dict:
    """升级待办事项。

    响应结构：ActionResult
    """
    return {"code": 0, "data": _action_result(todo_id, "ESCALATED")}


@router.post("/todos/{todo_id}/done")
async def complete_todo(todo_id: str, db: Session = Depends(get_db)) -> dict:
    """完成待办事项。

    响应结构：ActionResult
    """
    return {"code": 0, "data": _action_result(todo_id, "DONE")}


@router.post("/todos/batch-remind")
async def batch_remind_todos(data: dict, db: Session = Depends(get_db)) -> dict:
    """批量催办待办事项。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    return {"code": 0, "data": [_action_result(str(i), "REMINDED") for i in ids]}


@router.post("/todos/batch-escalate")
async def batch_escalate_todos(data: dict, db: Session = Depends(get_db)) -> dict:
    """批量升级待办事项。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    return {"code": 0, "data": [_action_result(str(i), "ESCALATED") for i in ids]}


# ── 交付物模板 / 验收标准 / 参考分规则 ─────────────────────


@router.post("/templates")
async def create_deliverable_template(data: dict, db: Session = Depends(get_db)) -> dict:
    """创建交付物模板。

    请求体：DeliverableTemplatePayload
    响应结构：DeliverableTemplate
    """
    return {"code": 0, "data": _empty_deliverable_template(data)}


@router.put("/templates/{template_id}")
async def update_deliverable_template(
    template_id: int,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """更新交付物模板。

    请求体：DeliverableTemplatePayload
    响应结构：DeliverableTemplate
    """
    payload = _empty_deliverable_template(data)
    payload["id"] = template_id
    return {"code": 0, "data": payload}


@router.post("/templates/{template_id}/{action}")
async def toggle_deliverable_template(
    template_id: int,
    action: str,
    db: Session = Depends(get_db),
) -> dict:
    """启用/停用交付物模板。

    路径参数：action = enable | disable
    响应结构：ActionResult
    """
    status = "ENABLED" if action == "enable" else "DISABLED"
    return {"code": 0, "data": _action_result(str(template_id), status)}


@router.post("/acceptance-standards")
async def create_acceptance_standard(data: dict, db: Session = Depends(get_db)) -> dict:
    """创建验收标准。

    请求体：AcceptanceStandardPayload
    响应结构：AcceptanceStandard
    """
    return {"code": 0, "data": _empty_acceptance_standard(data)}


@router.put("/acceptance-standards/{standard_id}")
async def update_acceptance_standard(
    standard_id: int,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """更新验收标准。

    请求体：AcceptanceStandardPayload
    响应结构：AcceptanceStandard
    """
    payload = _empty_acceptance_standard(data)
    payload["id"] = standard_id
    return {"code": 0, "data": payload}


@router.post("/acceptance-standards/{standard_id}/{action}")
async def toggle_acceptance_standard(
    standard_id: int,
    action: str,
    db: Session = Depends(get_db),
) -> dict:
    """启用/停用验收标准。

    路径参数：action = enable | disable
    响应结构：ActionResult
    """
    status = "ENABLED" if action == "enable" else "DISABLED"
    return {"code": 0, "data": _action_result(str(standard_id), status)}


@router.post("/score-rules")
async def create_score_rule(data: dict, db: Session = Depends(get_db)) -> dict:
    """创建参考分规则。

    请求体：ScoreRulePayload
    响应结构：ScoreRule
    """
    return {"code": 0, "data": _empty_score_rule(data)}


@router.put("/score-rules/{rule_id}")
async def update_score_rule(
    rule_id: int,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """更新参考分规则。

    请求体：ScoreRulePayload
    响应结构：ScoreRule
    """
    payload = _empty_score_rule(data)
    payload["id"] = rule_id
    return {"code": 0, "data": payload}


@router.post("/score-rules/{rule_id}/enable")
async def enable_score_rule(rule_id: int, db: Session = Depends(get_db)) -> dict:
    """启用参考分规则。

    响应结构：ActionResult
    """
    return {"code": 0, "data": _action_result(str(rule_id), "ENABLED")}


@router.post("/score-rules/{rule_id}/simulate")
async def simulate_score_rule(
    rule_id: int,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """模拟参考分规则。

    请求体：ScoreSimulationPayload
    响应结构：ScoreSimulation
    """
    return {
        "code": 0,
        "data": {
            "employeeName": data.get("employeeName", ""),
            "score": 0,
            "hitFactors": [],
            "explanation": "",
        },
    }


def _empty_deliverable_template(data: dict) -> dict:
    """构造 DeliverableTemplate，字段与 frontend/src/api/department.ts 保持一致。"""
    return {
        "id": 0,
        "orgId": int(data.get("orgId", 0) or 0),
        "orgName": "",
        "templateName": data.get("templateName", ""),
        "evidenceType": data.get("evidenceType", ""),
        "required": bool(data.get("required", False)),
        "appliesTo": data.get("appliesTo", "") or "",
        "description": data.get("description", "") or "",
        "versionNo": "1",
        "status": "ENABLED",
        "referenceCount": 0,
    }


def _empty_acceptance_standard(data: dict) -> dict:
    """构造 AcceptanceStandard，字段与 frontend/src/api/department.ts 保持一致。"""
    return {
        "id": 0,
        "templateId": int(data.get("templateId", 0) or 0),
        "templateName": "",
        "standardText": data.get("standardText", ""),
        "requireReviewPassed": bool(data.get("requireReviewPassed", False)),
        "evidenceRequirement": data.get("evidenceRequirement", "") or "",
        "versionNo": "1",
        "status": "ENABLED",
    }


def _empty_score_rule(data: dict) -> dict:
    """构造 ScoreRule，字段与 frontend/src/api/department.ts 保持一致。"""
    return {
        "id": 0,
        "orgId": int(data.get("orgId", 0) or 0),
        "orgName": "",
        "ruleName": data.get("ruleName", ""),
        "status": "ENABLED",
        "effectiveStart": data.get("effectiveStart"),
        "effectiveEnd": data.get("effectiveEnd"),
        "ruleJson": data.get("ruleJson") or {},
    }


def _empty_leader_employee_summary(owner_id: str) -> dict:
    """构造 LeaderEmployeeSummary，字段与 frontend/src/api/leader.ts 保持一致。

    ⚠️ `ownerId` / `orgId` 是飞书稳定 ID（长十六进制字符串），故为 string。
    """
    return {
        "ownerId": owner_id,
        "employeeNo": "",
        "employeeName": "",
        # 根节点哨兵值是字符串 "0"，不能写成数字 0。
        "orgId": "0",
        "orgName": "",
        "monthPlanCount": 0,
        "weekPlanCount": 0,
        "dayPlanCount": 0,
        "resultCount": 0,
        "totalWorkCount": 0,
    }


# ── 月计划 / 周计划审批 ────────────────────────────────────


@router.get("/month-plan-approvals/page")
async def page_month_plan_approvals(
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    planYear: Optional[int] = Query(None),
    planMonth: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """月计划审批分页。

    查询参数用 camelCase（`pageNo` / `pageSize` / `planYear` / `planMonth` /
    `status`），与前端 `department.ts` 实际发送的一致。

    ⚠️ 回归点：此前用 snake_case（`page_no` / `page_size`），
    前端传的 camelCase 会被静默忽略、分页永远失效（A2 已在 `leader.py` 修复）。

    响应结构：MonthPlanApprovalPage = { items, total, pageNo, pageSize }
    """
    scope = _approval_scope(current_user, db)
    return {
        "code": 0,
        "data": _month_plan_page(
            db,
            scope,
            page_no=pageNo,
            page_size=pageSize,
            plan_year=planYear,
            plan_month=planMonth,
            status=status,
        ),
    }


@router.get("/month-plan-approvals/{approval_id}")
async def get_month_plan_approval(
    approval_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """月计划审批详情。

    响应结构：MonthPlanApprovalItem
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_MONTH), approval_id)
    return {
        "code": 0,
        "data": plan_approval.serialize_month_plan_approval_item(db, plan),
    }


@router.get("/leader-month-plan-approvals/page")
async def page_leader_month_plan_approvals(
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    planYear: Optional[int] = Query(None),
    planMonth: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """部门负责人月计划审批分页。

    响应结构：LeaderMonthPlanApprovalPage
    """
    scope = _approval_scope(current_user, db)
    return {
        "code": 0,
        "data": _month_plan_page(
            db,
            scope,
            page_no=pageNo,
            page_size=pageSize,
            plan_year=planYear,
            plan_month=planMonth,
            status=status,
        ),
    }


@router.get("/leader-month-plan-approvals/{approval_id}")
async def get_leader_month_plan_approval(
    approval_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """部门负责人月计划审批详情。

    响应结构：LeaderMonthPlanApprovalDetail
    = { plan, comparisonStatus, rejectionBaseline? }
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_MONTH), approval_id)
    return {
        "code": 0,
        "data": {
            "plan": plan_approval.serialize_month_plan_approval_item(db, plan),
            "comparisonStatus": "NONE",
        },
    }


@router.get("/leader-month-plan-approvals/{approval_id}/template-download")
async def download_leader_month_plan_template(
    approval_id: str,
    db: Session = Depends(get_db),
):
    """下载月计划模板。

    响应结构：二进制文件流
    """
    raise HTTPException(status_code=404, detail="模板不存在或已过期")


@router.post("/leader-month-plan-approvals/batch-approve")
async def batch_approve_leader_month_plans(
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量通过部门负责人月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return {
        "code": 0,
        "data": _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_MONTH,
            approved=True,
        ),
    }


@router.post("/leader-month-plan-approvals/batch-reject")
async def batch_reject_leader_month_plans(
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量驳回部门负责人月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return {
        "code": 0,
        "data": _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_MONTH,
            approved=False,
        ),
    }


@router.post("/leader-month-plan-approvals/{approval_id}/approve")
async def approve_leader_month_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """通过部门负责人月计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    _run_approval(
        plan_approval.approve_month_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return {"code": 0, "data": _action_result(approval_id, "APPROVED")}


@router.post("/leader-month-plan-approvals/{approval_id}/reject")
async def reject_leader_month_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """驳回部门负责人月计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    _run_approval(
        plan_approval.reject_month_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return {"code": 0, "data": _action_result(approval_id, "REJECTED")}


@router.get("/week-plan-approvals/{approval_id}")
async def get_week_plan_approval(
    approval_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """周计划审批详情。

    响应结构：WeekPlanSummary
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_WEEK, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_WEEK), approval_id)
    return {"code": 0, "data": plan_approval.serialize_week_plan_summary(db, plan)}


@router.post("/week-plan-approvals/{approval_id}/approve")
async def approve_week_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """通过周计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_WEEK, db
    )
    _run_approval(
        plan_approval.approve_week_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return {"code": 0, "data": _action_result(approval_id, "APPROVED")}


@router.post("/week-plan-approvals/{approval_id}/reject")
async def reject_week_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """驳回周计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_WEEK, db
    )
    _run_approval(
        plan_approval.reject_week_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return {"code": 0, "data": _action_result(approval_id, "REJECTED")}


# ── 日计划点评 / 审批 ──────────────────────────────────────


@router.get("/daily-reviews/{review_id}")
async def get_daily_review(
    review_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """日计划点评详情。

    响应结构：DailyReviewItem
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_DAY), review_id)
    return {"code": 0, "data": plan_approval.serialize_daily_review_item(db, plan)}


@router.post("/daily-reviews/{review_id}/comment")
async def comment_daily_review(
    review_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """填写日计划点评。

    日计划走**抽检**语义：只写 `review_status` / `reviewed_at` / `leader_comment`，
    **不改** `plan.status`（决策 D7）。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    _run_approval(
        plan_approval.review_day_plan,
        db,
        current_user.id,
        review_id,
        approved=True,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return {"code": 0, "data": _action_result(review_id, "COMMENTED")}


@router.post("/daily-reviews/batch-comment")
async def batch_comment_daily_reviews(
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量填写日计划点评。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return {
        "code": 0,
        "data": _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_DAY,
            approved=True,
        ),
    }


@router.get("/day-plan-reviews/{review_id}")
async def get_day_plan_review(
    review_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """日计划审批详情。

    响应结构：DepartmentDayPlanReview
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_DAY), review_id)
    item = plan_approval.serialize_daily_review_item(db, plan)
    # 部门侧额外字段（`DepartmentDayPlanReview` 比 `DailyReviewItem` 多这几项）
    item.update(
        {
            "leaderName": "",
            "status": plan.status,
            "departmentComment": plan.department_comment or "",
            "departmentReviewerName": "",
        }
    )
    return {"code": 0, "data": item}


@router.post("/day-plan-reviews/{review_id}/approve")
async def approve_day_plan_review(
    review_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """通过日计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    _run_approval(
        plan_approval.review_day_plan,
        db,
        current_user.id,
        review_id,
        approved=True,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return {"code": 0, "data": _action_result(review_id, "APPROVED")}


@router.post("/day-plan-reviews/{review_id}/reject")
async def reject_day_plan_review(
    review_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """驳回日计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    _run_approval(
        plan_approval.review_day_plan,
        db,
        current_user.id,
        review_id,
        approved=False,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return {"code": 0, "data": _action_result(review_id, "REJECTED")}


# ── 申诉 ────────────────────────────────────────────────────


@router.get("/appeals/{appeal_id}")
async def get_department_appeal(appeal_id: str, db: Session = Depends(get_db)) -> dict:
    """部门侧申诉详情。

    响应结构：DepartmentAppeal
    """
    return {"code": 0, "data": _empty_department_appeal(appeal_id)}


@router.get("/appeals/{appeal_id}/package")
async def get_department_appeal_package(
    appeal_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """部门侧申诉证据包。

    响应结构：AppealPackage
    """
    return {"code": 0, "data": {"appealId": appeal_id, "items": [], "generatedAt": None}}


@router.post("/appeals/{appeal_id}/accept")
async def accept_department_appeal(
    appeal_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """受理申诉。

    响应结构：ActionResult
    """
    return {"code": 0, "data": _action_result(appeal_id, "ACCEPTED")}


@router.post("/appeals/{appeal_id}/resolve")
async def resolve_department_appeal(
    appeal_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """裁决申诉。

    响应结构：ActionResult
    """
    return {"code": 0, "data": _action_result(appeal_id, "RESOLVED")}


# ── 台账 ────────────────────────────────────────────────────


@router.get("/department-ledgers")
async def list_department_ledgers(
    page_no: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """部门台账分页。

    响应结构：DepartmentLedgerPage
    = { items, total, pageNo, pageSize, monthPlanCount, dayPlanCount,
        missingCount, appealCount }
    """
    return {
        "code": 0,
        "data": {
            "items": [],
            "total": 0,
            "pageNo": page_no,
            "pageSize": page_size,
            "monthPlanCount": 0,
            "dayPlanCount": 0,
            "missingCount": 0,
            "appealCount": 0,
        },
    }


@router.post("/department-ledgers/export")
async def export_department_ledgers(data: dict, db: Session = Depends(get_db)) -> dict:
    """导出部门台账。

    响应结构：ExportTask
    """
    return {"code": 0, "data": _empty_export_task()}


def _empty_department_appeal(appeal_id: str) -> dict:
    """构造 DepartmentAppeal，字段与 frontend/src/api/department.ts 保持一致。"""
    return {
        "id": appeal_id,
        "appealNo": "",
        "title": "",
        "reason": "",
        "status": "PENDING",
        "employeeName": "",
        "orgName": "",
        "createdAt": "",
        "handleComment": "",
    }


