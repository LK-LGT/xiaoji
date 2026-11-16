"""领导工作台 API 路由。

对应前端 `src/api/leader.ts` 的 `/leader/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/leader` 前缀。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import build_access_context_for_user, get_current_user
from ..domain.models import Employee
from ..services import plan_approval
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/leader", tags=["leader"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# 响应构造辅助
# ---------------------------------------------------------------------------


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 计划审批 —— 公共辅助
# ---------------------------------------------------------------------------


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

    规则（与 `services/access_control.can_access_employee` 对齐）：
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

    前端 `weekPlan.ts` / `leader.ts` 的审批接口会带 `versionNo`。
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

    返回 `ActionResult[]`，与前端 `_batch_action_results` 的形状一致。
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


# ---------------------------------------------------------------------------
# 企业知识库 —— 领导侧上传
#
# 响应结构：KnowledgeUploadResult / UploadedKnowledgeDocument[]（裸数组）
# ---------------------------------------------------------------------------


@router.post("/knowledge/upload")
async def upload_leader_knowledge(
    file: UploadFile = File(...),
    title: str = Form(...),
    knowledgeType: str = Form(...),
    appliesTo: str = Form(...),
    bindingLevel: str = Form(...),
    db: Session = Depends(get_db),
) -> dict:
    """领导上传知识库文档。

    响应结构：KnowledgeUploadResult
    """
    return _ok(
        {
            "documentId": 0,
            "knowledgeId": "",
            "title": title,
            "fileName": file.filename or "",
            "fileType": "",
            "chunkCount": 0,
            "vectorCount": 0,
            "embeddingModels": [],
            "uploadedAt": datetime.utcnow().isoformat(),
        }
    )


@router.get("/knowledge/uploads")
async def list_leader_knowledge_uploads(
    limit: int = Query(12, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导上传的知识库文档列表。

    响应结构：UploadedKnowledgeDocument[]（裸数组）
    """
    return _ok([])


# ---------------------------------------------------------------------------
# 导出任务
#
# 响应结构：ExportTask / ExportDownload
# ---------------------------------------------------------------------------


@router.post("/export-tasks")
async def create_leader_export_task(
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """创建领导侧导出任务。

    响应结构：ExportTask
    """
    return _ok(_empty_export_task())


@router.get("/export-tasks/{task_id}/download-info")
async def get_leader_export_download_info(
    task_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """获取导出任务下载信息。

    响应结构：ExportDownload
    """
    return _ok(_empty_export_download(task_id))


@router.get("/export-tasks/{task_id}/download")
async def download_leader_export_task(
    task_id: str,
    db: Session = Depends(get_db),
) -> Any:
    """下载导出任务产物。

    响应结构：二进制文件流
    """
    # 尚无真实导出产物时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="导出产物不存在或已过期")


def _empty_export_task() -> dict:
    """构造空 ExportTask，字段与 frontend/src/api/performance.ts 保持一致。"""
    return {
        "id": "",
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


# ---------------------------------------------------------------------------
# 组织树 / 工作台
#
# 响应结构：OrgNode[] / LeaderWorkbench
# ---------------------------------------------------------------------------


@router.get("/org-tree")
async def get_leader_org_tree(db: Session = Depends(get_db)) -> dict:
    """获取领导侧组织树。

    响应结构：OrgNode[]（裸数组）
    """
    return _ok([])


@router.get("/workbench")
async def get_leader_workbench(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """获取领导工作台聚合数据。

    响应结构：LeaderWorkbench
    = { orgTree, metrics, dateStatuses, subordinateSummaries }
    """
    return _ok(
        {
            "orgTree": [],
            "metrics": [],
            "dateStatuses": [],
            "subordinateSummaries": [],
        }
    )


# ---------------------------------------------------------------------------
# 下属员工工作项
#
# 响应结构：LeaderEmployeeSummary[] / LeaderEmployeeWorkPage / LeaderEmployeeMonthPlanDetail
# ---------------------------------------------------------------------------


@router.get("/employees")
async def list_leader_employees(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """获取领导下属员工摘要列表。

    响应结构：LeaderEmployeeSummary[]（裸数组）
    """
    return _ok([])


@router.get("/employees/{owner_id}/work-items")
async def list_leader_employee_work_items(
    owner_id: str,
    page_no: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    work_type: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取指定员工的工作项分页。

    响应结构：LeaderEmployeeWorkPage
    = { employee, items, total, pageNo, pageSize }
    """
    return _ok(
        {
            "employee": _empty_leader_employee_summary(owner_id),
            "items": [],
            "total": 0,
            "pageNo": page_no,
            "pageSize": page_size,
        }
    )


@router.get("/employees/{owner_id}/month-plans/{plan_id}")
async def get_leader_employee_month_plan(
    owner_id: str,
    plan_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取指定员工的月计划详情。

    响应结构：LeaderEmployeeMonthPlanDetail
    """
    return _ok(
        {
            "id": plan_id,
            "title": "",
            "planMonth": "",
            "status": "DRAFT",
            "leaderComment": "",
            "tasks": [],
        }
    )


def _empty_leader_employee_summary(owner_id: str) -> dict:
    """构造空 LeaderEmployeeSummary，字段与 frontend/src/api/leader.ts 保持一致。

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


# ---------------------------------------------------------------------------
# 月计划 / 周计划审批
#
# 响应结构：MonthPlanApprovalItem[] / LeaderMonthPlanApprovalPage /
#           LeaderMonthPlanApprovalDetail
# ---------------------------------------------------------------------------


@router.get("/month-plan-approvals")
async def list_leader_month_plan_approvals(
    request: Request,
    planYear: int | None = Query(None),
    planMonth: int | None = Query(None),
    status: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧月计划审批列表。

    查询参数用 camelCase，与前端 `MonthPlanApproval.vue` 实际发送的参数对齐。

    响应结构：MonthPlanApprovalItem[]（裸数组）
    """
    scope = _approval_scope(current_user, db)

    plan_month = None
    if planYear and planMonth:
        plan_month = f"{planYear:04d}-{planMonth:02d}"

    plans = plan_approval.list_pending_month_plans(
        db,
        scope,
        plan_month=plan_month,
        status=status,
    )
    return _ok(
        [
            plan_approval.serialize_month_plan_approval_item(db, plan)
            for plan in plans
        ]
    )


@router.get("/month-plan-approvals/page")
async def page_leader_month_plan_approvals(
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    planYear: int | None = Query(None),
    planMonth: int | None = Query(None),
    status: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧月计划审批分页。

    查询参数用 camelCase，与前端 `frontend/src/views/department/MonthPlanApproval.vue`
    实际发送的 `pageNo` / `pageSize` / `planYear` / `planMonth` / `status` 对齐。

    响应结构：LeaderMonthPlanApprovalPage
    """
    scope = _approval_scope(current_user, db)

    # 前端传 planYear + planMonth（两个 number），内部统一成 "2026-11"
    plan_month = None
    if planYear and planMonth:
        plan_month = f"{planYear:04d}-{planMonth:02d}"
    elif planMonth:
        plan_month = f"-{planMonth:02d}"

    plans = plan_approval.list_pending_month_plans(
        db,
        scope,
        plan_month=plan_month,
        status=status,
        limit=10000,
    )

    total = len(plans)
    start = (pageNo - 1) * pageSize
    page_items = plans[start : start + pageSize]

    return _ok(
        {
            "items": [
                plan_approval.serialize_month_plan_approval_item(db, plan)
                for plan in page_items
            ],
            "total": total,
            "pageNo": pageNo,
            "pageSize": pageSize,
            "submittedCount": total,
            "approvedCount": 0,
            "totalEmployeeCount": len({p.owner_id for p in plans}),
        }
    )


@router.get("/month-plan-approvals/{approval_id}")
async def get_leader_month_plan_approval(
    approval_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧月计划审批详情。

    响应结构：LeaderMonthPlanApprovalDetail
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_MONTH), approval_id)
    return _ok(
        {
            "plan": plan_approval.serialize_month_plan_approval_item(db, plan),
            "comparisonStatus": "NONE",
        }
    )


@router.get("/month-plan-approvals/{approval_id}/template-download")
async def download_leader_month_plan_template(
    approval_id: str,
    db: Session = Depends(get_db),
) -> Any:
    """下载月计划模板。

    响应结构：二进制文件流
    """
    # 尚无真实模板产物时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="模板不存在或已过期")


@router.post("/month-plan-approvals/batch-approve")
async def batch_approve_month_plans(
    data: dict,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量通过月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(
        _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_MONTH,
            approved=True,
        )
    )


@router.post("/month-plan-approvals/batch-reject")
async def batch_reject_month_plans(
    data: dict,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量驳回月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(
        _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_MONTH,
            approved=False,
        )
    )


@router.post("/month-plan-approvals/{approval_id}/approve")
async def approve_month_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """通过月计划。

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
    return _ok(_action_result(approval_id, "APPROVED"))


@router.post("/month-plan-approvals/{approval_id}/reject")
async def reject_month_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """驳回月计划。

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
    return _ok(_action_result(approval_id, "REJECTED"))


# ---------------------------------------------------------------------------
# 周计划审批
# ---------------------------------------------------------------------------


@router.get("/week-plan-approvals")
async def list_leader_week_plan_approvals(
    request: Request,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧周计划审批列表。

    响应结构：WeekPlanSummary[]（裸数组）
    """
    scope = _approval_scope(current_user, db)
    plans = plan_approval.list_pending_week_plans(db, scope)
    return _ok(
        [plan_approval.serialize_week_plan_summary(db, plan) for plan in plans]
    )


@router.get("/week-plan-approvals/{approval_id}")
async def get_leader_week_plan_approval(
    approval_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧周计划审批详情。

    响应结构：WeekPlanSummary
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_WEEK, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_WEEK), approval_id)
    return _ok(plan_approval.serialize_week_plan_summary(db, plan))


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
    return _ok(_action_result(approval_id, "APPROVED"))


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
    return _ok(_action_result(approval_id, "REJECTED"))


# ---------------------------------------------------------------------------
# 日计划点评
#
# 响应结构：DailyReviewItem[]
# ---------------------------------------------------------------------------


@router.get("/daily-reviews")
async def list_leader_daily_reviews(
    request: Request,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧日计划点评列表。

    响应结构：DailyReviewItem[]（裸数组）
    """
    scope = _approval_scope(current_user, db)
    plans = plan_approval.list_pending_day_plans(db, scope)
    return _ok(
        [plan_approval.serialize_daily_review_item(db, plan) for plan in plans]
    )


@router.get("/daily-reviews/{review_id}")
async def get_leader_daily_review(
    review_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧日计划点评详情。

    响应结构：DailyReviewItem
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_DAY), review_id)
    return _ok(plan_approval.serialize_daily_review_item(db, plan))


@router.post("/daily-reviews/{review_id}/comment")
async def comment_daily_review(
    review_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """填写日计划点评。

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
    return _ok(_action_result(review_id, "COMMENTED"))


@router.post("/daily-reviews/{review_id}/risk")
async def mark_daily_review_risk(
    review_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """标记日计划风险。

    响应结构：ActionResult
    """
    return _ok(_action_result(review_id, "RISK_MARKED"))


@router.post("/daily-reviews/batch-comment")
async def batch_comment_daily_reviews(
    data: dict,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量填写日计划点评。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(
        _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_DAY,
            approved=True,
        )
    )


@router.post("/daily-reviews/batch-risk")
async def batch_mark_daily_reviews_risk(data: dict, db: Session = Depends(get_db)) -> dict:
    """批量标记日计划风险。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(_batch_action_results(data, "RISK_MARKED"))


# ---------------------------------------------------------------------------
# 计划调整
#
# 响应结构：PlanAdjustmentItem[] / PlanAdjustmentItem
# ---------------------------------------------------------------------------


@router.get("/plan-adjustments")
async def list_leader_plan_adjustments(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """获取计划调整申请列表。

    响应结构：PlanAdjustmentItem[]（裸数组）
    """
    return _ok([])


@router.get("/plan-adjustments/{adjustment_id}")
async def get_leader_plan_adjustment(
    adjustment_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """获取计划调整申请详情。

    响应结构：PlanAdjustmentItem
    """
    return _ok(_empty_plan_adjustment_item(adjustment_id))


@router.post("/plan-adjustments/{adjustment_id}/process")
async def process_leader_plan_adjustment(
    adjustment_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """处理计划调整申请。

    响应结构：ActionResult
    """
    return _ok(_action_result(adjustment_id, "PROCESSED"))


# ---------------------------------------------------------------------------
# 团队台账
#
# 响应结构：PageResponse<LedgerItem>
# ---------------------------------------------------------------------------


@router.get("/team-ledgers")
async def list_leader_team_ledgers(
    page_no: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """团队台账分页。

    响应结构：PageResponse<LedgerItem> = { items, total, pageNo, pageSize }
    """
    return _ok({"items": [], "total": 0, "pageNo": page_no, "pageSize": page_size})


@router.post("/team-ledgers/export")
async def export_leader_team_ledgers(data: dict, db: Session = Depends(get_db)) -> dict:
    """导出团队台账。

    响应结构：ExportTask
    """
    return _ok(_empty_export_task())


# ---------------------------------------------------------------------------
# 响应构造辅助
# ---------------------------------------------------------------------------


def _action_result(object_id: str, status: str) -> dict:
    """构造 ActionResult，字段与 frontend/src/api/performance.ts 保持一致。"""
    return {
        "objectId": object_id,
        "status": status,
        "message": "",
        "auditActionCode": "",
        "auditDeferred": False,
    }


def _batch_action_results(data: dict, status: str) -> list[dict]:
    """按 ids 批量构造 ActionResult。"""
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    return [_action_result(str(i), status) for i in ids]


def _empty_month_plan_approval_item(approval_id: str) -> dict:
    """构造 MonthPlanApprovalItem，字段与 frontend/src/api/department.ts 保持一致。"""
    return {
        "id": approval_id,
        "planNo": "",
        "ownerId": 0,
        "employeeNo": "",
        "employeeName": "",
        "orgId": 0,
        "orgName": "",
        "planYear": 0,
        "planMonth": 0,
        "deliverable": "",
        "estimatedStartDate": "",
        "estimatedEndDate": "",
        "status": "PENDING",
        "leaderComment": "",
        "aiCheckResult": "",
        "missingFields": [],
        "submittedAt": "",
        "version": 1,
        "items": [],
    }


def _empty_week_plan_summary(plan_id: str) -> dict:
    """构造 WeekPlanSummary，字段与 frontend/src/api/weekPlan.ts 保持一致。"""
    return {
        "id": plan_id,
        "title": "",
        "weekStart": "",
        "weekEnd": "",
        "status": "PENDING",
        "versionNo": 1,
        "ownerUserId": 0,
        "employeeName": "",
        "departmentName": "",
        "itemCount": 0,
    }


def _empty_daily_review_item(review_id: str) -> dict:
    """构造 DailyReviewItem，字段与 frontend/src/api/leader.ts 保持一致。"""
    return {
        "id": review_id,
        "ownerId": 0,
        "employeeNo": "",
        "employeeName": "",
        "orgId": 0,
        "orgName": "",
        "planDate": "",
        "workContent": "",
        "deliverable": "",
        "overdueApproval": False,
        "missingFields": [],
        "aiCheckResult": "",
        "reviewStatus": "PENDING",
        "riskLevel": "NONE",
    }


def _empty_plan_adjustment_item(adjustment_id: str) -> dict:
    """构造 PlanAdjustmentItem，字段与 frontend/src/api/leader.ts 保持一致。"""
    return {
        "id": adjustment_id,
        "originalPlanType": "",
        "originalPlanId": 0,
        "originalPlanNo": "",
        "originalWorkContent": "",
        "newPlanType": "",
        "newPlanId": 0,
        "newPlanNo": "",
        "ownerId": 0,
        "employeeName": "",
        "adjustmentType": "",
        "reason": "",
        "impactText": "",
        "status": "PENDING",
        "keepEvidenceChain": True,
    }


