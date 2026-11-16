"""员工侧成果提交与草稿版本 API 路由。

对应前端 `src/api/employee.ts`、`src/api/weekResult.ts`、`src/api/planDraftVersions.ts` 的：
- `/employee/results/*`
- `/employee/week-results/*`
- `/employee/plan-draft-versions/*`

前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee` 前缀。

实现说明（A4）：
    - 领域逻辑全部在 `services/result_submission.py`（提交 / 版本化 / 证据落盘 /
      审批 / 序列化），本模块只做「HTTP ↔ 服务层」的转接。
    - 数据范围：月 / 周成果都以当前登录用户的 `owner_id` 为边界，
      跨人访问返回 404（不泄露存在性）。
    - 鉴权：此前这些端点**全部匿名可访问**，违反 P0-3，已统一改为
      `Depends(get_current_user)`。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee, MonthPlan, WeekPlan
from ..domain.result_models import (
    RESULT_PLAN_TYPE_MONTH,
    RESULT_PLAN_TYPE_WEEK,
    Result,
    ResultEvidence,
)
from ..services import planning, result_submission
from ..services.result_submission import ResultEvidenceInput
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee", tags=["employee-result"], dependencies=[Depends(get_current_user)])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


def _run(action, *args, **kwargs) -> Any:
    """执行服务层调用，把领域异常翻译成 HTTP 状态码。"""
    try:
        return action(*args, **kwargs)
    except result_submission.ResultNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except result_submission.ResultForbidden as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except result_submission.ResultConflict as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except result_submission.ResultError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


async def _read_evidence(file: UploadFile | None) -> ResultEvidenceInput | None:
    """把 `UploadFile` 读成服务层的附件输入对象。"""
    if file is None:
        return None
    raw = await file.read()
    return ResultEvidenceInput(
        file_name=file.filename or "evidence",
        content_type=file.content_type or "",
        data=raw,
    )


# ---------------------------------------------------------------------------
# 月成果提交
#
# 响应结构：EmployeeResultSubmitOptionsResp / EmployeeResultDetailResp
#           EmployeeResultVersionResp
# ---------------------------------------------------------------------------


@router.get("/results/submit/options")
async def get_employee_result_submit_options(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取成果提交选项（月计划、月计划任务、历史版本）。

    响应结构：EmployeeResultSubmitOptionsResp
    """
    plans = (
        db.query(MonthPlan)
        .filter(
            MonthPlan.owner_id == current_user.id,
        )
        .order_by(MonthPlan.plan_month.desc())
        .all()
    )

    month_plan_options: list[dict] = []
    month_plan_item_options: list[dict] = []
    result_versions: list[dict] = []

    for plan in plans:
        items = planning.list_month_plan_items(db, plan.id)
        latest = result_submission.latest_result_for_item(
            db, current_user.id, RESULT_PLAN_TYPE_MONTH, plan.id, None
        )
        option = result_submission.serialize_month_submit_option(db, plan, None, latest)
        month_plan_options.append(option["plan"])
        month_plan_item_options.extend(option["items"])
        if latest is not None:
            result_versions.append(result_submission.serialize_result_version(latest))
        for item in items:
            item_latest = result_submission.latest_result_for_item(
                db, current_user.id, RESULT_PLAN_TYPE_MONTH, plan.id, item.id
            )
            if item_latest is not None:
                result_versions.append(result_submission.serialize_result_version(item_latest))

    return _ok(
        {
            "monthPlanOptions": month_plan_options,
            "monthPlanItemOptions": month_plan_item_options,
            "acceptedFileTypes": list(result_submission.ACCEPTED_FILE_TYPES),
            "maxFileSizeMb": result_submission.MAX_FILE_SIZE_MB,
            "resultVersions": result_versions,
        }
    )


@router.post("/results/submit")
async def submit_employee_result(
    monthPlanId: str = Form(...),
    monthPlanItemId: str | None = Form(None),
    completionRate: float | None = Form(None),
    description: str | None = Form(None),
    completionStatus: str | None = Form(None),
    remark: str | None = Form(None),
    personalPerformance: float | None = Form(None),
    aiReviewId: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交月成果。

    响应结构：EmployeeResultVersionResp
    """
    evidence = await _read_evidence(file)
    result = _run(
        result_submission.submit_month_result,
        db,
        current_user.id,
        month_plan_id=monthPlanId,
        month_plan_item_id=monthPlanItemId or None,
        completion_rate=completionRate or 0.0,
        description=description or "",
        completion_status=completionStatus or "",
        remark=remark or "",
        personal_performance=personalPerformance,
        ai_review_id=aiReviewId or None,
        evidence=evidence,
    )
    return _ok(
        {
            "id": result_submission._as_int(result.id),
            "versionNo": str(result.version_no),
            "status": result.status,
            "submittedAt": result_submission._iso(result.submitted_at) or "",
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
    result_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取成果详情。

    响应结构：EmployeeResultDetailResp
    """
    result = db.get(Result, result_id)
    if result is None or result.owner_id != current_user.id:
        # 跨人访问返回 404，不泄露存在性
        raise HTTPException(status_code=404, detail="成果不存在")
    return _ok(result_submission.serialize_month_result_detail(db, result))


@router.get("/results/{result_id}/evidence/{evidence_id}")
async def download_employee_result_evidence(
    result_id: str,
    evidence_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载成果证据文件。

    响应结构：二进制文件流
    """
    row = db.get(ResultEvidence, evidence_id)
    if row is None or row.result_id != result_id:
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    result = db.get(Result, result_id)
    if result is None or result.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    path = result_submission.evidence_path(row)
    if not path.exists():
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")
    return FileResponse(path, filename=row.file_name)


# ---------------------------------------------------------------------------
# 周成果
#
# 响应结构：WeekResultSubmitOptions / WeekResultRecord
# ---------------------------------------------------------------------------


@router.get("/week-results/submit/options")
async def get_week_result_submit_options(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周成果提交选项。

    响应结构：WeekResultSubmitOptions
    """
    plans = (
        db.query(WeekPlan)
        .filter(
            WeekPlan.owner_id == current_user.id,
        )
        .order_by(WeekPlan.week_start.desc())
        .all()
    )

    options: list[dict] = []
    for plan in plans:
        items = planning.list_week_plan_items(db, plan.id)
        for item in items:
            latest = result_submission.latest_result_for_item(
                db, current_user.id, RESULT_PLAN_TYPE_WEEK, plan.id, item.id
            )
            options.append(result_submission.serialize_week_submit_option(db, plan, item, latest))

    return _ok(
        {
            "plans": options,
            "acceptedFileTypes": list(result_submission.ACCEPTED_FILE_TYPES),
            "maxFileSizeMb": result_submission.MAX_FILE_SIZE_MB,
        }
    )


@router.get("/week-results/{result_id}")
async def get_employee_week_result_detail(
    result_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周成果详情。

    响应结构：WeekResultRecord
    """
    result = db.get(Result, result_id)
    if result is None or result.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="成果不存在")
    return _ok(result_submission.serialize_week_result(db, result))


@router.get("/week-results/{result_id}/evidence/{evidence_id}")
async def download_week_result_evidence(
    result_id: str,
    evidence_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载周成果证据文件。

    响应结构：二进制文件流
    """
    row = db.get(ResultEvidence, evidence_id)
    if row is None or row.result_id != result_id:
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    result = db.get(Result, result_id)
    if result is None or result.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")

    path = result_submission.evidence_path(row)
    if not path.exists():
        raise HTTPException(status_code=404, detail="证据文件不存在或已过期")
    return FileResponse(path, filename=row.file_name)


# ---------------------------------------------------------------------------
# 计划草稿版本
#
# 响应结构：PlanDraftVersionSummary[] / PlanDraftVersionDetail
# ---------------------------------------------------------------------------


@router.get("/plan-draft-versions")
async def list_plan_draft_versions(
    planType: str = Query(..., description="计划类型：MONTH / WEEK / DAY"),
    periodKey: str = Query(..., description="计划周期标识，如 2026-11"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取计划草稿版本列表。

    响应结构：PlanDraftVersionSummary[]（裸数组）

    ⚠️ `period_key` 不是表字段 —— `plan_draft_version` 只存 `plan_id`，
    周期标识由**关联的计划**反查（MONTH → `month_plan.plan_month`；
    WEEK → `week_plan.week_start`）。这样表结构不用改，也能按周期过滤。
    """
    from ..domain.plan_models import PlanDraftVersion

    rows = (
        db.query(PlanDraftVersion)
        .filter(PlanDraftVersion.plan_type == planType.upper())
        .order_by(PlanDraftVersion.version_no.desc())
        .all()
    )
    matched = [r for r in rows if _period_key_of(db, r) == periodKey]
    return _ok([_serialize_draft_version(db, r) for r in matched])


@router.get("/plan-draft-versions/{version_id}")
async def get_plan_draft_version(
    version_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取计划草稿版本详情。

    响应结构：PlanDraftVersionDetail
    """
    from ..domain.plan_models import PlanDraftVersion

    row = db.get(PlanDraftVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="草稿版本不存在")
    return _ok(_serialize_draft_version(db, row))


@router.get("/plan-draft-versions/{version_id}/restore")
async def restore_plan_draft_version(
    version_id: str,
    planType: str = Query(..., description="计划类型：MONTH / WEEK / DAY"),
    periodKey: str = Query(..., description="计划周期标识，如 2026-11"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """恢复计划草稿版本。

    响应结构：PlanDraftVersionDetail
    """
    from ..domain.plan_models import PlanDraftVersion

    row = db.get(PlanDraftVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="草稿版本不存在")
    return _ok(_serialize_draft_version(db, row))


def _period_key_of(db: Session, row: Any) -> str:
    """从关联计划反查周期标识。

    MONTH → ``month_plan.plan_month``（``2026-11``）
    WEEK  → ``week_plan.week_start``（``2026-11-02``）
    DAY   → 暂不支持（尚无日计划草稿版本）
    """
    if row.plan_type == "MONTH":
        plan = db.get(MonthPlan, row.plan_id)
        return plan.plan_month if plan is not None else ""
    if row.plan_type == "WEEK":
        plan = db.get(WeekPlan, row.plan_id)
        return planning._date_str(plan.week_start) if plan is not None else ""
    return ""


def _serialize_draft_version(db: Session, row: Any) -> dict:
    """`PlanDraftVersionDetail`。"""
    return {
        "id": result_submission._as_int(row.id),
        "planType": row.plan_type,
        "periodKey": _period_key_of(db, row),
        "planId": result_submission._as_int(row.plan_id),
        "versionNo": row.version_no,
        "sourceType": row.source_type,
        "snapshotSchemaVersion": row.snapshot_schema_version,
        "baseVersionId": row.base_version_id,
        "conversationId": row.conversation_id,
        "messageId": row.message_id,
        "suggestionId": row.suggestion_id,
        "requestId": row.request_id,
        "createdAt": result_submission._iso(row.created_at) or "",
        "snapshot": row.snapshot_json or {},
    }
