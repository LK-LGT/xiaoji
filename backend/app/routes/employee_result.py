"""员工侧成果提交与草稿版本 API 路由。

对应前端 `src/api/employee.ts`、`src/api/weekResult.ts`、`src/api/planDraftVersions.ts` 的：
- `/employee/results/*`
- `/employee/week-results/*`
- `/employee/plan-draft-versions/*`

前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..core.db import get_db
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee", tags=["employee-result"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 月成果提交
#
# 响应结构：EmployeeResultSubmitOptionsResp / EmployeeResultDetailResp
#           EmployeeResultVersionResp
# ---------------------------------------------------------------------------


@router.get("/results/submit/options")
async def get_employee_result_submit_options(
    db: Session = Depends(get_db),
) -> dict:
    """获取成果提交选项（月计划、月计划任务、历史版本）。

    响应结构：EmployeeResultSubmitOptionsResp
    """
    return _ok(
        {
            "monthPlanOptions": [],
            "monthPlanItemOptions": [],
            "acceptedFileTypes": [],
            "maxFileSizeMb": 0,
            "resultVersions": [],
        }
    )


@router.post("/results/submit")
async def submit_employee_result(
    monthPlanId: Optional[int] = Form(None),
    monthPlanItemId: Optional[int] = Form(None),
    completionRate: Optional[float] = Form(None),
    description: Optional[str] = Form(None),
    completionStatus: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    personalPerformance: Optional[float] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
) -> dict:
    """提交月成果。

    响应结构：EmployeeResultVersionResp
    """
    return _ok(
        {
            "id": 0,
            "versionNo": "",
            "status": "SUBMITTED",
            "submittedAt": "",
        }
    )


@router.post("/results/submit/excel")
async def download_employee_result_excel(
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> Any:
    """导出成果提交 Excel 模板。

    响应结构：二进制文件流
    """
    # 尚无真实数据时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="暂无可导出的成果数据")


@router.get("/results/{result_id}")
async def get_employee_result_detail(
    result_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取成果详情。

    响应结构：EmployeeResultDetailResp
    """
    return _ok(_empty_result_detail(result_id))


@router.get("/results/{result_id}/evidence/{evidence_id}")
async def download_employee_result_evidence(
    result_id: int,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """下载成果证据文件。

    响应结构：二进制文件流
    """
    # 尚无真实证据文件时返回 404，避免前端拿到损坏内容。
    raise HTTPException(status_code=404, detail="证据文件不存在或已过期")


# ---------------------------------------------------------------------------
# 周成果
#
# 响应结构：WeekResultSubmitOptions / WeekResultRecord
# ---------------------------------------------------------------------------


@router.get("/week-results/submit/options")
async def get_week_result_submit_options(
    db: Session = Depends(get_db),
) -> dict:
    """获取周成果提交选项。

    响应结构：WeekResultSubmitOptions
    """
    return _ok(
        {
            "plans": [],
            "acceptedFileTypes": [],
            "maxFileSizeMb": 0,
        }
    )


@router.get("/week-results/{result_id}")
async def get_employee_week_result_detail(
    result_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取周成果详情。

    响应结构：WeekResultRecord
    """
    return _ok(_empty_week_result_record(result_id))


@router.get("/week-results/{result_id}/evidence/{evidence_id}")
async def download_week_result_evidence(
    result_id: int,
    evidence_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """下载周成果证据文件。

    响应结构：二进制文件流
    """
    # 尚无真实证据文件时返回 404，避免前端拿到损坏内容。
    raise HTTPException(status_code=404, detail="证据文件不存在或已过期")


# ---------------------------------------------------------------------------
# 计划草稿版本
#
# 响应结构：PlanDraftVersionSummary[] / PlanDraftVersionDetail
# ---------------------------------------------------------------------------


@router.get("/plan-draft-versions")
async def list_plan_draft_versions(
    planType: str = Query(..., description="计划类型：MONTH / WEEK / DAY"),
    periodKey: str = Query(..., description="计划周期标识，如 2026-11"),
    db: Session = Depends(get_db),
) -> dict:
    """获取计划草稿版本列表。

    响应结构：PlanDraftVersionSummary[]（裸数组）
    """
    return _ok([])


@router.get("/plan-draft-versions/{version_id}")
async def get_plan_draft_version(
    version_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取计划草稿版本详情。

    响应结构：PlanDraftVersionDetail
    """
    return _ok(_empty_draft_version_detail(version_id))


@router.get("/plan-draft-versions/{version_id}/restore")
async def restore_plan_draft_version(
    version_id: int,
    planType: str = Query(..., description="计划类型：MONTH / WEEK / DAY"),
    periodKey: str = Query(..., description="计划周期标识，如 2026-11"),
    db: Session = Depends(get_db),
) -> dict:
    """恢复计划草稿版本。

    响应结构：PlanDraftVersionDetail
    """
    return _ok(_empty_draft_version_detail(version_id))


def _empty_result_detail(result_id: int) -> dict:
    """构造 EmployeeResultDetailResp，字段与 frontend/src/api/employee.ts 保持一致。"""
    return {
        "id": result_id,
        "resultNo": "",
        "title": "",
        "resultDate": "",
        "description": "",
        "completionRate": 0,
        "completionStatus": "",
        "remark": "",
        "personalPerformance": None,
        "versionNo": "",
        "status": "DRAFT",
        "planType": "",
        "planId": None,
        "planTitle": "",
        "monthPlanItemId": None,
        "planItemName": "",
        "submittedAt": "",
        "suggestionStatus": "",
        "leaderSuggestion": "",
        "suggestedAt": None,
        "confirmComment": "",
        "confirmedAt": None,
        "evidenceStatus": "",
        "issueCodes": [],
        "issueText": "",
        "evidences": [],
    }


def _empty_week_result_record(result_id: int) -> dict:
    """构造 WeekResultRecord，字段与 frontend/src/api/weekResult.ts 保持一致。"""
    return {
        "id": result_id,
        "weekPlanId": 0,
        "weekPlanItemId": 0,
        "resultDate": "",
        "title": "",
        "content": "",
        "completionStatus": "",
        "deliverable": "",
        "remark": "",
        "completionRate": 0,
        "evidenceStatus": "",
        "status": "DRAFT",
        "submitAt": "",
        "approverId": None,
        "approveAt": None,
        "approvalComment": None,
        "evidences": [],
    }


def _empty_draft_version_detail(version_id: int) -> dict:
    """构造 PlanDraftVersionDetail，字段与 frontend/src/api/planDraftVersions.ts 保持一致。"""
    return {
        "id": version_id,
        "planType": "MONTH",
        "periodKey": "",
        "planId": None,
        "versionNo": 0,
        "sourceType": "MANUAL_SAVE",
        "snapshotSchemaVersion": 0,
        "baseVersionId": None,
        "conversationId": None,
        "messageId": None,
        "suggestionId": None,
        "requestId": None,
        "createdAt": "",
        "snapshot": {},
    }
