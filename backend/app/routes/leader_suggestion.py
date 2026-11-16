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
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/leader", tags=["leader-suggestion-package"], dependencies=[Depends(get_current_user)])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 结果建议
#
# 响应结构：ResultSuggestionItem[] / ResultSuggestionItem / ActionResult
# ---------------------------------------------------------------------------


@router.get("/result-suggestions")
async def list_result_suggestions(
    status: Optional[str] = Query(None),
    orgId: Optional[str] = Query(None),
    ownerId: Optional[str] = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """获取结果建议列表。

    响应结构：ResultSuggestionItem[]（裸数组）
    """
    return _ok([])


@router.get("/result-suggestions/{suggestion_id}")
async def get_result_suggestion(
    suggestion_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """获取结果建议详情。

    响应结构：ResultSuggestionItem
    """
    return _ok(_empty_result_suggestion(suggestion_id))


@router.post("/result-suggestions/{suggestion_id}/suggest")
async def submit_result_suggestion(
    suggestion_id: str,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """提交结果建议。

    请求体：PerformanceActionPayload
    响应结构：ActionResult
    """
    return _ok(_empty_action_result(suggestion_id, payload.get("action", "")))


@router.post("/result-suggestions/batch-suggest")
async def batch_submit_result_suggestions(
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """批量提交结果建议。

    请求体：BatchActionPayload = PerformanceActionPayload & { ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    ids = payload.get("ids") or []
    action = payload.get("action", "")
    return _ok([_empty_action_result(str(i), action) for i in ids])


@router.get("/result-suggestions/{result_id}/evidence/{evidence_id}")
async def download_leader_result_evidence(
    result_id: str,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """下载领导侧结果证据文件。

    响应结构：二进制文件流
    """
    # 尚无真实证据文件时返回 404，避免前端拿到损坏内容。
    raise HTTPException(status_code=404, detail="证据文件不存在或已过期")


@router.get("/result-suggestions/{result_id}/evidence/{evidence_id}/preview")
async def get_leader_result_evidence_preview(
    result_id: str,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧结果证据预览信息。

    响应结构：ResultEvidencePreview
    """
    return _ok(
        {
            "fileName": "",
            "previewType": "DOWNLOAD",
            "mimeType": "application/octet-stream",
            "textContent": None,
            "entries": [],
        }
    )


@router.get("/result-suggestions/{result_id}/evidence/{evidence_id}/archive-entry")
async def open_leader_result_evidence_archive_entry(
    result_id: str,
    evidence_id: int,
    path: str = Query(..., description="压缩包内条目路径"),
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
    scopeOrgId: Optional[str] = Query(None),
    planDate: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取项目日计划包列表。

    响应结构：ProjectDayPlanPackage[]（裸数组）
    """
    return _ok([])


@router.get("/project-day-plan-packages/{group_id}")
async def get_leader_project_day_plan_package(
    group_id: str,
    planDate: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取项目日计划包详情。

    响应结构：ProjectDayPlanPackageDetail
    """
    return _ok(
        {
            "packageInfo": _empty_day_plan_package(group_id),
            "rows": [],
        }
    )


# ---------------------------------------------------------------------------
# 项目周计划包
#
# 响应结构：ProjectWeekPlanPackage[] / ProjectWeekPlanPackageDetail
# ---------------------------------------------------------------------------


@router.get("/project-week-plan-packages")
async def list_leader_project_week_plan_packages(
    scopeOrgId: Optional[str] = Query(None),
    weekStart: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取项目周计划包列表。

    响应结构：ProjectWeekPlanPackage[]（裸数组）
    """
    return _ok([])


@router.get("/project-week-plan-packages/{group_id}")
async def get_leader_project_week_plan_package(
    group_id: str,
    weekStart: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取项目周计划包详情。

    响应结构：ProjectWeekPlanPackageDetail
    """
    return _ok(
        {
            "packageInfo": _empty_week_plan_package(group_id),
            "rows": [],
        }
    )


@router.get("/project-week-plan-packages/{group_id}/excel")
async def download_leader_project_week_plan_excel(
    group_id: str,
    weekStart: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Any:
    """导出项目周计划 Excel。

    响应结构：二进制文件流
    """
    # 尚无真实数据时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="暂无可导出的项目周计划数据")


# ---------------------------------------------------------------------------
# 项目周成果包
#
# 响应结构：ProjectWeekResultPackage[] / ProjectWeekResultPackageDetail
# ---------------------------------------------------------------------------


@router.get("/project-week-result-packages")
async def list_leader_project_week_result_packages(
    scopeOrgId: Optional[str] = Query(None),
    weekStart: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取项目周成果包列表。

    响应结构：ProjectWeekResultPackage[]（裸数组）
    """
    return _ok([])


@router.get("/project-week-result-packages/{group_id}")
async def get_leader_project_week_result_package(
    group_id: str,
    weekStart: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取项目周成果包详情。

    响应结构：ProjectWeekResultPackageDetail
    """
    return _ok(
        {
            "packageInfo": _empty_week_result_package(group_id),
            "rows": [],
        }
    )


@router.get("/project-week-result-packages/{group_id}/excel")
async def download_leader_project_week_result_excel(
    group_id: str,
    weekStart: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Any:
    """导出项目周成果 Excel。

    响应结构：二进制文件流
    """
    # 尚无真实数据时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="暂无可导出的项目周成果数据")


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
