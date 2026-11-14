"""领导工作台 API 路由。

对应前端 `src/api/leader.ts` 的 `/leader/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/leader` 前缀。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from ..core.db import get_db
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/leader", tags=["leader"])


# ---------------------------------------------------------------------------
# 响应构造辅助
# ---------------------------------------------------------------------------


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


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
    owner_id: int,
    page_no: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    work_type: Optional[str] = Query(None),
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
    owner_id: int,
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


def _empty_leader_employee_summary(owner_id: int) -> dict:
    """构造空 LeaderEmployeeSummary，字段与 frontend/src/api/leader.ts 保持一致。"""
    return {
        "ownerId": owner_id,
        "employeeNo": "",
        "employeeName": "",
        "orgId": 0,
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
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧月计划审批列表。

    响应结构：MonthPlanApprovalItem[]（裸数组）
    """
    return _ok([])


@router.get("/month-plan-approvals/page")
async def page_leader_month_plan_approvals(
    page_no: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧月计划审批分页。

    响应结构：LeaderMonthPlanApprovalPage
    """
    return _ok(
        {
            "items": [],
            "total": 0,
            "pageNo": page_no,
            "pageSize": page_size,
            "submittedCount": 0,
            "approvedCount": 0,
            "totalEmployeeCount": 0,
        }
    )


@router.get("/month-plan-approvals/{approval_id}")
async def get_leader_month_plan_approval(
    approval_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """领导侧月计划审批详情。

    响应结构：LeaderMonthPlanApprovalDetail
    """
    return _ok(
        {
            "plan": _empty_month_plan_approval_item(approval_id),
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
async def batch_approve_month_plans(data: dict, db: Session = Depends(get_db)) -> dict:
    """批量通过月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(_batch_action_results(data, "APPROVED"))


@router.post("/month-plan-approvals/batch-reject")
async def batch_reject_month_plans(data: dict, db: Session = Depends(get_db)) -> dict:
    """批量驳回月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(_batch_action_results(data, "REJECTED"))


@router.post("/month-plan-approvals/{approval_id}/approve")
async def approve_month_plan(
    approval_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """通过月计划。

    响应结构：ActionResult
    """
    return _ok(_action_result(approval_id, "APPROVED"))


@router.post("/month-plan-approvals/{approval_id}/reject")
async def reject_month_plan(
    approval_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """驳回月计划。

    响应结构：ActionResult
    """
    return _ok(_action_result(approval_id, "REJECTED"))


# ---------------------------------------------------------------------------
# 周计划审批
# ---------------------------------------------------------------------------


@router.get("/week-plan-approvals")
async def list_leader_week_plan_approvals(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧周计划审批列表。

    响应结构：WeekPlanSummary[]（裸数组）
    """
    return _ok([])


@router.get("/week-plan-approvals/{approval_id}")
async def get_leader_week_plan_approval(
    approval_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """领导侧周计划审批详情。

    响应结构：WeekPlanSummary
    """
    return _ok(_empty_week_plan_summary(approval_id))


@router.post("/week-plan-approvals/{approval_id}/approve")
async def approve_week_plan(
    approval_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """通过周计划。

    响应结构：ActionResult
    """
    return _ok(_action_result(approval_id, "APPROVED"))


@router.post("/week-plan-approvals/{approval_id}/reject")
async def reject_week_plan(
    approval_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """驳回周计划。

    响应结构：ActionResult
    """
    return _ok(_action_result(approval_id, "REJECTED"))


# ---------------------------------------------------------------------------
# 日计划点评
#
# 响应结构：DailyReviewItem[]
# ---------------------------------------------------------------------------


@router.get("/daily-reviews")
async def list_leader_daily_reviews(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧日计划点评列表。

    响应结构：DailyReviewItem[]（裸数组）
    """
    return _ok([])


@router.get("/daily-reviews/{review_id}")
async def get_leader_daily_review(review_id: str, db: Session = Depends(get_db)) -> dict:
    """领导侧日计划点评详情。

    响应结构：DailyReviewItem
    """
    return _ok(_empty_daily_review_item(review_id))


@router.post("/daily-reviews/{review_id}/comment")
async def comment_daily_review(
    review_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """填写日计划点评。

    响应结构：ActionResult
    """
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
async def batch_comment_daily_reviews(data: dict, db: Session = Depends(get_db)) -> dict:
    """批量填写日计划点评。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(_batch_action_results(data, "COMMENTED"))


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


