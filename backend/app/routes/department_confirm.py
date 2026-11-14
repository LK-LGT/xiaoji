"""部门侧结果确认与周成果审批 API 路由。

对应前端 `src/api/department.ts`、`src/api/weekPlan.ts`、`src/api/weekResult.ts`、
`src/api/aiManagement.ts` 的：
- `/department/result-confirms/*`
- `/department/week-plans/{id}`
- `/department/week-result-approvals/*`
- `/leader/week-result-approvals/*`
- `/leader/ai/month-plan-context`
- `/ai/requests/{id}/cancel`

前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1` 前缀。

分页约定：这些列表端点前端用 `PageResponse<T>`（即 `items`），
故分页列表返回 { items, total, pageNo, pageSize }。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(tags=["department-confirm-approval"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 部门结果确认
#
# 响应结构：ResultConfirmItem / ActionResult
# ---------------------------------------------------------------------------


@router.get("/v1/department/result-confirms/{confirm_id}")
async def get_department_result_confirm(
    confirm_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """获取部门侧结果确认详情。

    响应结构：ResultConfirmItem
    """
    return _ok(_empty_result_confirm(confirm_id))


@router.get("/v1/department/result-confirms/{result_id}/evidence/{evidence_id}")
async def download_department_result_confirm_evidence(
    result_id: str,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """下载部门侧结果证据文件。

    响应结构：二进制文件流
    """
    # 尚无真实证据文件时返回 404，避免前端拿到损坏内容。
    raise HTTPException(status_code=404, detail="证据文件不存在或已过期")


@router.post("/v1/department/result-confirms/{confirm_id}/confirm")
async def confirm_department_result(
    confirm_id: str,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """确认部门侧结果。

    请求体：PerformanceActionPayload
    响应结构：ActionResult
    """
    return _ok(_empty_action_result(confirm_id, "CONFIRMED"))


@router.post("/v1/department/result-confirms/{confirm_id}/reject")
async def reject_department_result(
    confirm_id: str,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """驳回部门侧结果。

    请求体：PerformanceActionPayload
    响应结构：ActionResult
    """
    return _ok(_empty_action_result(confirm_id, "REJECTED"))


# ---------------------------------------------------------------------------
# 部门周计划详情
#
# 响应结构：WeekPlanDetail
# ---------------------------------------------------------------------------


@router.get("/v1/department/week-plans/{plan_id}")
async def get_department_week_plan(
    plan_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取部门侧周计划详情。

    响应结构：WeekPlanDetail
    """
    return _ok(
        {
            "summary": _empty_week_plan_summary(plan_id),
            "items": [],
            "siblingPlans": [],
            "dayPlanCount": 0,
        }
    )


# ---------------------------------------------------------------------------
# 周成果审批（领导侧 + 部门侧）
#
# 响应结构：WeekResultSubmitAction
# ---------------------------------------------------------------------------


@router.post("/v1/leader/week-result-approvals/{result_id}/approve")
async def approve_leader_week_result(
    result_id: int,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """领导侧通过周成果。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_empty_week_result_action(result_id, "APPROVED"))


@router.post("/v1/leader/week-result-approvals/{result_id}/reject")
async def reject_leader_week_result(
    result_id: int,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """领导侧驳回周成果。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_empty_week_result_action(result_id, "REJECTED"))


@router.post("/v1/department/week-result-approvals/{result_id}/approve")
async def approve_department_week_result(
    result_id: int,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """部门侧通过周成果。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_empty_week_result_action(result_id, "APPROVED"))


@router.post("/v1/department/week-result-approvals/{result_id}/reject")
async def reject_department_week_result(
    result_id: int,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """部门侧驳回周成果。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_empty_week_result_action(result_id, "REJECTED"))


@router.get("/v1/{workspace}/week-result-approvals/{result_id}/evidence/{evidence_id}")
async def download_week_result_approval_evidence(
    workspace: str,
    result_id: int,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """下载周成果审批证据文件。

    workspace 取值：leader / department
    响应结构：二进制文件流
    """
    # 尚无真实证据文件时返回 404，避免前端拿到损坏内容。
    raise HTTPException(status_code=404, detail="证据文件不存在或已过期")


# ---------------------------------------------------------------------------
# 领导侧 AI 计划上下文
#
# 响应结构：AiPlanContext
# ---------------------------------------------------------------------------


@router.get("/v1/leader/ai/month-plan-context")
async def get_leader_ai_month_plan_context(
    planMonth: str = Query(..., description="计划月份 YYYY-MM"),
    orgId: int = Query(..., description="组织 ID"),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧月计划 AI 上下文配置。

    响应结构：AiPlanContext
    """
    return _ok(_empty_ai_plan_context(planMonth, orgId))


@router.put("/v1/leader/ai/month-plan-context")
async def update_leader_ai_month_plan_context(
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """更新领导侧月计划 AI 上下文配置。

    响应结构：AiPlanContext
    """
    return _ok(
        _empty_ai_plan_context(
            payload.get("planMonth", ""),
            int(payload.get("orgId", 0) or 0),
        )
    )


# ---------------------------------------------------------------------------
# AI 请求取消
#
# 响应结构：{ requestId, state }
# ---------------------------------------------------------------------------


@router.post("/v1/ai/requests/{request_id}/cancel")
async def cancel_ai_request(
    request_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """取消进行中的 AI 请求。

    响应结构：{ requestId, state }
    """
    return _ok({"requestId": request_id, "state": "ALREADY_FINISHED"})


def _empty_action_result(object_id: str, status: str) -> dict:
    """构造 ActionResult，字段与 frontend/src/api/performance.ts 保持一致。"""
    return {
        "objectId": object_id,
        "status": status,
        "message": "",
        "auditActionCode": "",
        "auditDeferred": False,
    }


def _empty_result_confirm(confirm_id: str) -> dict:
    """构造 ResultConfirmItem，字段与 frontend/src/api/department.ts 保持一致。"""
    return {
        "id": confirm_id,
        "resultNo": "",
        "ownerId": 0,
        "employeeNo": "",
        "employeeName": "",
        "orgId": 0,
        "orgName": "",
        "planType": "",
        "planId": 0,
        "planNo": "",
        "resultTitle": "",
        "resultDate": "",
        "completionRatio": 0,
        "autoLevel": "",
        "evidenceStatus": "",
        "leaderSuggestion": "",
        "issueCodes": [],
        "issueText": "",
        "confirmStatus": "",
        "evidences": [],
    }


def _empty_week_plan_summary(plan_id: int) -> dict:
    """构造 WeekPlanSummary，字段与 frontend/src/api/weekPlan.ts 保持一致。"""
    return {
        "id": plan_id,
        "title": "",
        "weekStart": "",
        "weekEnd": "",
        "status": "DRAFT",
        "versionNo": 0,
        "ownerUserId": 0,
        "employeeName": "",
        "deptId": None,
        "departmentName": "",
        "itemCount": 0,
        "submitAt": None,
        "approveAt": None,
        "approvalComment": None,
    }


def _empty_week_result_action(result_id: int, status: str) -> dict:
    """构造 WeekResultSubmitAction，字段与 frontend/src/api/weekResult.ts 保持一致。"""
    return {
        "id": result_id,
        "status": status,
        "submittedAt": "",
        "message": "",
    }


def _empty_ai_plan_context(plan_month: str, org_id: int) -> dict:
    """构造 AiPlanContext，字段与 frontend/src/api/aiManagement.ts 保持一致。"""
    return {
        "id": 0,
        "orgId": org_id,
        "orgName": "",
        "planMonth": plan_month,
        "departmentGoal": "",
        "leaderRequirement": "",
        "versionNo": 0,
        "updatedAt": "",
    }
