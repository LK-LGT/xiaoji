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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import build_access_context_for_user, get_current_user
from ..domain.models import Employee, WeekPlan, WeekPlanItem
from ..domain.result_models import (
    RESULT_PLAN_TYPE_WEEK,
    RESULT_STATUS_CONFIRMED,
    RESULT_STATUS_REJECTED,
    RESULT_STATUS_SUBMITTED,
    Result,
    ResultEvidence,
)
from ..services import plan_approval, result_submission
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(tags=["department-confirm-approval"], dependencies=[Depends(get_current_user)])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 周成果审批内部实现
# ---------------------------------------------------------------------------


def _access_context(current_user: Employee, db: Session):
    """当前用户的访问上下文。"""
    return build_access_context_for_user(current_user, db)


def _approval_scope(current_user: Employee, db: Session):
    """当前用户的审批可见范围（复用 `plan_approval.ApprovalScope`）。"""
    context = _access_context(current_user, db)
    return plan_approval.ApprovalScope.from_access_context(context)


def _week_result_approval_rows(
    db: Session,
    scope: Any,
    *,
    weekStart: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> list[dict]:
    """构造周成果审批列表项（对齐前端 `WeekResultApprovalItem`）。

    列表包含**未提交**的任务（`NOT_SUBMITTED`）—— 审批页要能看到"谁还没交"。
    """
    rows = result_submission.list_results_for_approval(db, scope, plan_type=RESULT_PLAN_TYPE_WEEK)

    # 归属人 → 已提交成果（取最新一版）
    latest_by_owner_item: dict[tuple[str, str], Result] = {}
    for result in rows:
        key = (result.owner_id, result.plan_item_id or "")
        current = latest_by_owner_item.get(key)
        if current is None or result.version_no > current.version_no:
            latest_by_owner_item[key] = result

    items: list[dict] = []
    for owner_id in sorted(scope.direct_report_ids | _managed_employee_ids(db, scope)):
        owner = db.get(Employee, owner_id)
        if owner is None:
            continue
        plans = (
            db.query(WeekPlan)
            .filter(WeekPlan.owner_id == owner_id)
            .order_by(WeekPlan.week_start.desc())
            .all()
        )
        for plan in plans:
            if weekStart and str(plan.week_start) != weekStart:
                continue
            plan_items = (
                db.query(WeekPlanItem)
                .filter(WeekPlanItem.plan_id == plan.id)
                .order_by(WeekPlanItem.sort_no)
                .all()
            )
            for plan_item in plan_items:
                result = latest_by_owner_item.get((owner_id, plan_item.id))
                item = _serialize_approval_item(db, owner, plan, plan_item, result)
                if status and item["status"] != status:
                    continue
                if keyword:
                    haystack = " ".join(
                        [
                            owner.name or "",
                            owner.login_account or "",
                            plan.title or "",
                            plan_item.content or "",
                            item["deliverable"] or "",
                        ]
                    )
                    if keyword.lower() not in haystack.lower():
                        continue
                items.append(item)

    items.sort(key=lambda r: (r["submitAt"] or "", r["employeeName"]), reverse=True)
    return items


def _managed_employee_ids(db: Session, scope: Any) -> set[str]:
    """管理范围内部门的所有成员 ID。"""
    managed = set(getattr(scope, "managed_department_ids", ()) or ())
    if not managed:
        return set()
    from ..domain.models import EmployeeDepartment

    rows = (
        db.query(EmployeeDepartment.employee_id)
        .filter(EmployeeDepartment.department_id.in_(managed))
        .all()
    )
    ids = {str(r[0]) for r in rows if r[0]}
    # 主部门归属也要算上
    main_rows = db.query(Employee.id).filter(Employee.department_id.in_(managed)).all()
    ids.update(str(r[0]) for r in main_rows if r[0])
    return ids


def _serialize_approval_item(
    db: Session,
    owner: Employee,
    plan: WeekPlan,
    plan_item: WeekPlanItem,
    result: Result | None,
) -> dict:
    """`WeekResultApprovalItem`。"""
    from ..services import planning

    if result is None:
        status = "NOT_SUBMITTED"
    elif result.status == RESULT_STATUS_SUBMITTED:
        status = "SUBMITTED"
    elif result.status == RESULT_STATUS_CONFIRMED:
        status = "APPROVED"
    elif result.status == RESULT_STATUS_REJECTED:
        status = "REJECTED"
    else:
        status = "NOT_SUBMITTED"

    evidences = []
    if result is not None:
        rows = db.query(ResultEvidence).filter(ResultEvidence.result_id == result.id).all()
        evidences = [
            {
                "id": result_submission._as_id(e.id),
                "fileName": e.file_name,
                "fileType": e.file_type,
                "fileSize": e.file_size,
                "checksum": e.checksum,
                "downloadUrl": (f"/v1/leader/week-result-approvals/{result.id}/evidence/{e.id}"),
                "createdAt": result_submission._iso(e.created_at) or "",
            }
            for e in rows
        ]

    dept = getattr(owner, "department", None)
    return {
        "id": result_submission._as_id(result.id) if result else "",
        "weekPlanId": result_submission._as_id(plan.id),
        "ownerUserId": result_submission._as_id(owner.id),
        "employeeNo": owner.login_account or "",
        "employeeName": owner.name or "",
        "groupName": getattr(dept, "name", "") or "",
        "weekStart": planning._date_str(plan.week_start) or "",
        "weekEnd": planning._date_str(plan.week_end) or "",
        "workContent": plan_item.content or "",
        "planDeliverable": plan_item.deliverable or "",
        "quality": plan_item.quality or "",
        "completionStatus": (result.completion_status if result else plan_item.completion_status)
        or "",
        "deliverable": result.deliverable if result else (plan_item.deliverable or ""),
        "remark": result.remark if result else (plan_item.remark or ""),
        "evidenceStatus": result.evidence_status if result else "NONE",
        "status": status,
        "submitAt": result_submission._iso(result.submitted_at) if result else "",
        "approverId": result_submission._as_id(result.approver_id) if result else "",
        "approveAt": result_submission._iso(result.approved_at) if result else "",
        "approvalComment": result.approval_comment if result else "",
        "supervisorResult": bool(result and result.status == RESULT_STATUS_CONFIRMED),
        "evidences": evidences,
    }


def _paginate(rows: list[dict], pageNo: int, pageSize: int) -> dict:
    """前端 `PageResponse<T>`：`{ items, total, pageNo, pageSize }`。"""
    total = len(rows)
    start = (pageNo - 1) * pageSize
    return {
        "items": rows[start : start + pageSize],
        "total": total,
        "pageNo": pageNo,
        "pageSize": pageSize,
    }


def _approve_week_result(
    db: Session,
    current_user: Employee,
    result_id: str,
    payload: Any,
    approve: bool,
) -> dict:
    """执行周成果审批（通过 / 驳回）。

    校验链：存在性 → 审批人可见范围 → 非自审批 → 状态允许。
    """
    result = db.get(Result, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="成果不存在")

    context = _access_context(current_user, db)
    if not result_submission.can_access_result(db, context, result):
        raise HTTPException(status_code=403, detail="无权审批该成果")

    comment = ""
    if payload is not None:
        # `JsonBody` 是 Pydantic 模型（不是 dict），它自带 `.get()`；
        # 兼容将来换成裸 dict 的情况 —— 两者都有 `.get()`。
        comment = str(payload.get("comment") or "")

    try:
        if approve:
            updated = result_submission.approve_result(
                db, current_user.id, result_id, comment=comment
            )
        else:
            updated = result_submission.reject_result(
                db, current_user.id, result_id, comment=comment
            )
    except result_submission.ResultForbidden as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except result_submission.ResultConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except result_submission.ResultNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc

    return {
        "id": result_submission._as_id(updated.id),
        "status": "APPROVED" if approve else "REJECTED",
        "submittedAt": result_submission._iso(updated.submitted_at) or "",
        "message": "周成果已通过" if approve else "周成果已驳回",
    }


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


@router.get("/v1/leader/week-result-approvals")
async def list_leader_week_result_approvals(
    weekStart: str | None = Query(None),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧周成果审批列表。

    响应结构：PageResponse<WeekResultApprovalItem>
    """
    scope = _approval_scope(current_user, db)
    rows = _week_result_approval_rows(
        db, scope, weekStart=weekStart, status=status, keyword=keyword
    )
    return _ok(_paginate(rows, pageNo, pageSize))


@router.get("/v1/department/week-result-approvals")
async def list_department_week_result_approvals(
    weekStart: str | None = Query(None),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """部门侧周成果审批列表。

    响应结构：PageResponse<WeekResultApprovalItem>
    """
    scope = _approval_scope(current_user, db)
    rows = _week_result_approval_rows(
        db, scope, weekStart=weekStart, status=status, keyword=keyword
    )
    return _ok(_paginate(rows, pageNo, pageSize))


@router.post("/v1/leader/week-result-approvals/{result_id}/approve")
async def approve_leader_week_result(
    result_id: str,
    payload: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧通过周成果。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_approve_week_result(db, current_user, result_id, payload, True))


@router.post("/v1/leader/week-result-approvals/{result_id}/reject")
async def reject_leader_week_result(
    result_id: str,
    payload: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧驳回周成果。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_approve_week_result(db, current_user, result_id, payload, False))


@router.post("/v1/department/week-result-approvals/{result_id}/approve")
async def approve_department_week_result(
    result_id: str,
    payload: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """部门侧通过周成果。

    ⚠️ 一级审批（决策 D2/D4）：部门侧与领导侧是**同一批端点**，
    不是第二级审批链。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_approve_week_result(db, current_user, result_id, payload, True))


@router.post("/v1/department/week-result-approvals/{result_id}/reject")
async def reject_department_week_result(
    result_id: str,
    payload: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """部门侧驳回周成果。

    请求体：{ comment: string }
    响应结构：WeekResultSubmitAction
    """
    return _ok(_approve_week_result(db, current_user, result_id, payload, False))


@router.get("/v1/{workspace}/week-result-approvals/{result_id}/evidence/{evidence_id}")
async def download_week_result_approval_evidence(
    workspace: str,
    result_id: str,
    evidence_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载周成果审批证据文件。

    workspace 取值：leader / department
    响应结构：二进制文件流
    """
    row = db.get(ResultEvidence, evidence_id)
    if row is None or row.result_id != result_id:
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    result = db.get(Result, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    # 审批人必须能看到该成果归属人
    if not result_submission.can_access_result(db, _access_context(current_user, db), result):
        raise HTTPException(status_code=403, detail="无权查看该成果的证据")

    path = result_submission.evidence_path(row)
    if not path.exists():
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")
    return FileResponse(path, filename=row.file_name)


# ---------------------------------------------------------------------------
# 领导侧 AI 计划上下文
#
# 响应结构：AiPlanContext
# ---------------------------------------------------------------------------


@router.get("/v1/leader/ai/month-plan-context")
async def get_leader_ai_month_plan_context(
    planMonth: str = Query(..., description="计划月份 YYYY-MM"),
    orgId: str = Query(..., description="组织 ID（飞书部门 ID，字符串）"),
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
            # 组织 ID 是飞书部门 ID（长十六进制字符串），不能 int() 强转。
            str(payload.get("orgId", "0") or "0"),
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


def _empty_ai_plan_context(plan_month: str, org_id: str) -> dict:
    """构造 AiPlanContext，字段与 frontend/src/api/aiManagement.ts 保持一致。

    ⚠️ `orgId` 是飞书部门 ID（长十六进制字符串），故为 string。
    """
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
