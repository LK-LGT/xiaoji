"""领导侧结果建议与项目包 API 路由。

对应前端 `src/api/leader.ts`、`src/api/weekPlan.ts`、`src/api/weekResult.ts` 的：
- `/leader/result-suggestions/*`
- `/leader/project-day-plan-packages/*`
- `/leader/project-week-plan-packages/*`
- `/leader/project-week-result-packages/*`

前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/leader` 前缀。

分页约定：前端这些列表端点直接 .map()，故返回裸数组。
下载类端点：无资源时返回 404，不返回空文件，防止前端拿到损坏内容。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import build_access_context_for_user, get_current_user
from ..domain.models import Employee
from ..domain.result_models import Result, ResultEvidence
from ..services import plan_approval, project_package_service, result_submission
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/leader", tags=["leader-suggestion-package"], dependencies=[Depends(get_current_user)])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


def _access_context(current_user: Employee, db: Session):
    """当前用户的访问上下文（`can_access_result` 需要它，不是 `ApprovalScope`）。"""
    return build_access_context_for_user(current_user, db)


def _approval_scope(current_user: Employee, db: Session):
    """当前用户的审批可见范围（复用 `plan_approval.ApprovalScope`）。"""
    context = _access_context(current_user, db)
    return plan_approval.ApprovalScope.from_access_context(context)


def _suggestion_rows(
    db: Session,
    scope: Any,
    *,
    scope_org_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    suggestion_status: str | None = None,
    evidence_status: str | None = None,
    owner_id: str | None = None,
) -> list[dict]:
    """构造审批人侧成果列表（对齐前端 `ResultSuggestionItem`）。

    ⚠️ 只列**已提交**的成果（`submitted` / `confirmed` / `rejected`）；
    未提交的条目不占审批人的处理队列。
    """
    rows = result_submission.list_results_for_approval(
        db, scope, plan_type=result_submission.RESULT_PLAN_TYPE_MONTH
    )

    items: list[dict] = []
    for result in rows:
        if result.status not in (
            result_submission.RESULT_STATUS_SUBMITTED,
            result_submission.RESULT_STATUS_CONFIRMED,
            result_submission.RESULT_STATUS_REJECTED,
        ):
            continue
        if owner_id and str(result.owner_id) != str(owner_id):
            continue
        item = result_submission.serialize_result_suggestion(db, result)

        if scope_org_id and str(item["orgId"]) != str(scope_org_id):
            continue
        period = str(item.get("period") or "")
        if start_date and period and period < str(start_date)[:7]:
            continue
        if end_date and period and period > str(end_date)[:7]:
            continue
        if suggestion_status and item["suggestionStatus"] != suggestion_status:
            continue
        if evidence_status and item["evidenceStatus"] != evidence_status:
            continue
        items.append(item)

    items.sort(key=lambda r: (r.get("submittedAt") or "", r.get("employeeName") or ""), reverse=True)
    return items


def _load_evidence_for_reviewer(
    db: Session,
    current_user: Employee,
    result_id: str,
    evidence_id: str,
) -> tuple[ResultEvidence, Result]:
    """加载证据并校验审批人可见性。

    跨人 / 越权访问统一返回 404，不泄露存在性。
    """
    row = db.get(ResultEvidence, evidence_id)
    if row is None or str(row.result_id) != str(result_id):
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    result = db.get(Result, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    scope = _access_context(current_user, db)
    if not result_submission.can_access_result(db, scope, result):
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")
    return row, result

# ---------------------------------------------------------------------------
# 结果建议
#
# 响应结构：ResultSuggestionItem[] / ResultSuggestionItem / ActionResult
# ---------------------------------------------------------------------------


@router.get("/result-suggestions")
async def list_result_suggestions(
    status: str | None = Query(None),
    orgId: str | None = Query(None),
    ownerId: str | None = Query(None),
    scopeOrgId: str | None = Query(None),
    startDate: str | None = Query(None),
    endDate: str | None = Query(None),
    suggestionStatus: str | None = Query(None),
    evidenceStatus: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取结果建议列表。

    响应结构：ResultSuggestionItem[]（裸数组）

    ⚠️ 查询参数**用 camelCase** —— 前端 `ResultSuggest.vue` 传的是
    `scopeOrgId` / `startDate` / `endDate` / `suggestionStatus` /
    `evidenceStatus`；FastAPI 按声明名取参，snake_case 会被静默忽略
    （同 BUG-R6-05 的坑）。
    """
    scope = _approval_scope(current_user, db)
    rows = _suggestion_rows(
        db,
        scope,
        scope_org_id=scopeOrgId or orgId,
        start_date=startDate,
        end_date=endDate,
        suggestion_status=suggestionStatus or status,
        evidence_status=evidenceStatus,
        owner_id=ownerId,
    )
    return _ok(rows)


@router.get("/result-suggestions/{suggestion_id}")
async def get_result_suggestion(
    suggestion_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取结果建议详情。

    响应结构：ResultSuggestionItem
    """
    result = db.get(Result, suggestion_id)
    if result is None:
        raise HTTPException(status_code=404, detail="成果不存在")
    scope = _access_context(current_user, db)
    if not result_submission.can_access_result(db, scope, result):
        raise HTTPException(status_code=403, detail="无权查看该成果")
    return _ok(result_submission.serialize_result_suggestion(db, result))


def _apply_suggestion(
    db: Session,
    current_user: Employee,
    result_id: str,
    payload: Any,
) -> dict:
    """执行一次成果审批（通过 / 驳回）。

    校验链与周成果审批一致：存在性 → 可见范围 → 状态允许（服务层内含禁自审批）。
    """
    result = db.get(Result, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="成果不存在")

    scope = _access_context(current_user, db)
    if not result_submission.can_access_result(db, scope, result):
        raise HTTPException(status_code=403, detail="无权审批该成果")

    decision = str((payload.get("decision") if payload else "") or "")
    comment = str((payload.get("comment") if payload else "") or "")
    approve = decision in ("SUGGEST_CONFIRM", "CONFIRMED", "APPROVED")

    try:
        if approve:
            updated = result_submission.approve_result(
                db, current_user.id, result_id, comment=comment
            )
        else:
            updated = result_submission.reject_result(
                db, current_user.id, result_id, comment=comment
            )
        db.commit()
    except result_submission.ResultForbidden as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except result_submission.ResultConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except result_submission.ResultNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=exc.message) from exc

    return {
        "objectId": result_submission._as_id(updated.id),
        "id": result_submission._as_id(updated.id),
        "status": "CONFIRMED" if approve else "REJECTED",
        "message": "成果已通过" if approve else "成果已驳回",
        "auditActionCode": "RESULT_SUGGEST",
        "auditDeferred": False,
        "leaderSuggestion": comment,
    }


@router.post("/result-suggestions/{suggestion_id}/suggest")
async def submit_result_suggestion(
    suggestion_id: str,
    payload: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交结果建议。

    请求体：PerformanceActionPayload
    响应结构：ActionResult
    """
    return _ok(_apply_suggestion(db, current_user, suggestion_id, payload))


@router.post("/result-suggestions/batch-suggest")
async def batch_submit_result_suggestions(
    payload: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量提交结果建议。

    请求体：BatchActionPayload = PerformanceActionPayload & { ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    ids = payload.get("ids") or []
    results: list[dict] = []
    for raw_id in ids:
        try:
            results.append(_apply_suggestion(db, current_user, str(raw_id), payload))
        except HTTPException as exc:
            results.append(
                {
                    "objectId": str(raw_id),
                    "id": str(raw_id),
                    "status": "FAILED",
                    "message": exc.detail,
                    "auditActionCode": "RESULT_SUGGEST",
                    "auditDeferred": False,
                }
            )
    return _ok(results)


@router.get("/result-suggestions/{result_id}/evidence/{evidence_id}")
async def download_leader_result_evidence(
    result_id: str,
    evidence_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载领导侧结果证据文件。

    响应结构：二进制文件流
    """
    row, result = _load_evidence_for_reviewer(db, current_user, result_id, evidence_id)
    path = result_submission.evidence_path(row)
    if not path.exists():
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")
    return FileResponse(path, filename=row.file_name)


@router.get("/result-suggestions/{result_id}/evidence/{evidence_id}/preview")
async def get_leader_result_evidence_preview(
    result_id: str,
    evidence_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧结果证据预览信息。

    响应结构：ResultEvidencePreview
    """
    row, _ = _load_evidence_for_reviewer(db, current_user, result_id, evidence_id)
    path = result_submission.evidence_path(row)
    mime = row.file_type or "application/octet-stream"
    preview_type = "TEXT" if mime.startswith("text/") else "DOWNLOAD"
    text_content: str | None = None
    if preview_type == "TEXT" and path.exists():
        try:
            text_content = path.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            text_content = None
            preview_type = "DOWNLOAD"
    return _ok(
        {
            "fileName": row.file_name,
            "previewType": preview_type,
            "mimeType": mime,
            "textContent": text_content,
            "entries": [],
        }
    )


@router.get("/result-suggestions/{result_id}/evidence/{evidence_id}/archive-entry")
async def open_leader_result_evidence_archive_entry(
    result_id: str,
    evidence_id: str,
    path: str = Query(..., description="压缩包内条目路径"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载压缩包内的单个条目。

    响应结构：二进制文件流
    """
    # 尚无真实证据文件时返回 404，避免前端拿到损坏内容。
    raise HTTPException(status_code=404, detail="证据文件不存在或已过期")


# ---------------------------------------------------------------------------
# 项目日计划包
#
# 响应结构：ProjectDayPlanPackage[] / ProjectDayPlanPackageDetail
# ---------------------------------------------------------------------------


@router.get("/project-day-plan-packages")
async def list_leader_project_day_plan_packages(
    scopeOrgId: str | None = Query(None),
    planDate: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取项目日计划包列表。

    响应结构：ProjectDayPlanPackage[]（裸数组）
    """
    scope = _approval_scope(current_user, db)
    try:
        packages = project_package_service.day_packages(
            db, scope, plan_date=planDate, scope_org_id=scopeOrgId
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _ok(packages)


@router.get("/project-day-plan-packages/{group_id}")
async def get_leader_project_day_plan_package(
    group_id: str,
    planDate: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取项目日计划包详情。

    响应结构：ProjectDayPlanPackageDetail
    """
    scope = _approval_scope(current_user, db)
    try:
        detail = project_package_service.day_package_detail(
            db, scope, group_id, plan_date=planDate
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=403, detail="无权查看该项目的日计划包")
    return _ok(detail)


# ---------------------------------------------------------------------------
# 项目周计划包
#
# 响应结构：ProjectWeekPlanPackage[] / ProjectWeekPlanPackageDetail
# ---------------------------------------------------------------------------


@router.get("/project-week-plan-packages")
async def list_leader_project_week_plan_packages(
    scopeOrgId: str | None = Query(None),
    weekStart: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取项目周计划包列表。

    响应结构：ProjectWeekPlanPackage[]（裸数组）
    """
    scope = _approval_scope(current_user, db)
    try:
        packages = project_package_service.week_packages(
            db, scope, week_start=weekStart, scope_org_id=scopeOrgId
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _ok(packages)


@router.get("/project-week-plan-packages/{group_id}")
async def get_leader_project_week_plan_package(
    group_id: str,
    weekStart: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取项目周计划包详情。

    响应结构：ProjectWeekPlanPackageDetail
    """
    scope = _approval_scope(current_user, db)
    try:
        detail = project_package_service.week_package_detail(
            db, scope, group_id, week_start=weekStart
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=403, detail="无权查看该项目的周计划包")
    return _ok(detail)


@router.get("/project-week-plan-packages/{group_id}/excel")
async def download_leader_project_week_plan_excel(
    group_id: str,
    weekStart: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> Any:
    """导出项目周计划 Excel（CSV，UTF-8 BOM，Excel 可直接打开）。"""
    scope = _approval_scope(current_user, db)
    try:
        detail = project_package_service.week_package_detail(
            db, scope, group_id, week_start=weekStart
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if detail is None or not detail["rows"]:
        raise HTTPException(status_code=404, detail="暂无可导出的项目周计划数据")
    content = project_package_service.to_csv(
        detail["rows"],
        [
            ("employeeName", "姓名"),
            ("workContent", "工作内容"),
            ("completionStatus", "完成情况"),
            ("deliverable", "交付物"),
            ("quality", "质量标准"),
        ],
    )
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="project-week-plan.csv"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# 项目周成果包
#
# 响应结构：ProjectWeekResultPackage[] / ProjectWeekResultPackageDetail
# ---------------------------------------------------------------------------


@router.get("/project-week-result-packages")
async def list_leader_project_week_result_packages(
    scopeOrgId: str | None = Query(None),
    weekStart: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取项目周成果包列表。

    响应结构：ProjectWeekResultPackage[]（裸数组）
    """
    scope = _approval_scope(current_user, db)
    try:
        packages = project_package_service.week_result_packages(
            db, scope, week_start=weekStart, scope_org_id=scopeOrgId
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _ok(packages)


@router.get("/project-week-result-packages/{group_id}")
async def get_leader_project_week_result_package(
    group_id: str,
    weekStart: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取项目周成果包详情。

    响应结构：ProjectWeekResultPackageDetail
    """
    scope = _approval_scope(current_user, db)
    try:
        detail = project_package_service.week_result_package_detail(
            db, scope, group_id, week_start=weekStart
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=403, detail="无权查看该项目的周成果包")
    return _ok(detail)


@router.get("/project-week-result-packages/{group_id}/excel")
async def download_leader_project_week_result_excel(
    group_id: str,
    weekStart: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> Any:
    """导出项目周成果 Excel（CSV，UTF-8 BOM，Excel 可直接打开）。"""
    scope = _approval_scope(current_user, db)
    try:
        detail = project_package_service.week_result_package_detail(
            db, scope, group_id, week_start=weekStart
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if detail is None or not detail["rows"]:
        raise HTTPException(status_code=404, detail="暂无可导出的项目周成果数据")
    content = project_package_service.to_csv(
        detail["rows"],
        [
            ("employeeName", "姓名"),
            ("workContent", "工作内容"),
            ("completionStatus", "完成情况"),
            ("deliverable", "交付物"),
            ("remark", "备注"),
        ],
    )
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="project-week-result.csv"',
            "Cache-Control": "no-store",
        },
    )


def _empty_action_result(object_id: str, action: str) -> dict:
    """构造 ActionResult，字段与 frontend/src/api/performance.ts 保持一致。"""
    return {
        "objectId": object_id,
        "status": action,
        "message": "",
        "auditActionCode": "",
        "auditDeferred": False,
    }


def _empty_result_suggestion(suggestion_id: str) -> dict:
    """构造 ResultSuggestionItem，字段与 frontend/src/api/leader.ts 保持一致。"""
    return {
        "id": suggestion_id,
        "ownerId": 0,
        "employeeNo": "",
        "employeeName": "",
        "orgId": 0,
        "orgName": "",
        "resultNo": "",
        "resultTitle": "",
        "resultDate": "",
        "planType": "",
        "planId": 0,
        "planNo": "",
        "completionRatio": 0,
        "autoLevel": "",
        "evidenceStatus": "",
        "issueCodes": [],
        "issueText": "",
        "suggestionStatus": "",
        "leaderSuggestion": None,
        "resultStatus": "",
        "evidences": [],
    }


def _empty_day_plan_package(group_id: str) -> dict:
    """构造 ProjectDayPlanPackage，字段与 frontend/src/api/leader.ts 保持一致。"""
    return {
        "groupId": group_id,
        "groupName": "",
        "planDate": "",
        "submittedCount": 0,
        "totalMemberCount": 0,
        "status": "EMPTY",
        "contentAvailable": False,
        "pendingMembers": [],
        "completedAt": None,
    }


def _empty_week_plan_package(group_id: str) -> dict:
    """构造 ProjectWeekPlanPackage，字段与 frontend/src/api/weekPlan.ts 保持一致。"""
    return {
        "groupId": group_id,
        "groupName": "",
        "weekStart": "",
        "weekEnd": "",
        "approvedCount": 0,
        "totalMemberCount": 0,
        "status": "EMPTY",
        "contentAvailable": False,
        "pendingMembers": [],
        "completedAt": None,
    }


def _empty_week_result_package(group_id: str) -> dict:
    """构造 ProjectWeekResultPackage，字段与 frontend/src/api/weekResult.ts 保持一致。"""
    return {
        "groupId": group_id,
        "groupName": "",
        "weekStart": "",
        "weekEnd": "",
        "approvedCount": 0,
        "totalMemberCount": 0,
        "status": "EMPTY",
        "contentAvailable": False,
        "pendingMembers": [],
        "completedAt": None,
    }
