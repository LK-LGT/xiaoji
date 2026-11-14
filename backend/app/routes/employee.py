"""员工工作台 API 路由。

对应前端 `src/api/employee.ts` 的 `/employee/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ..core.db import get_db
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee", tags=["employee"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 导出任务
#
# 响应结构：二进制文件流
# ---------------------------------------------------------------------------


@router.get("/export-tasks/{task_id}/download")
async def download_employee_export_task(
    task_id: str,
    db: Session = Depends(get_db),
) -> Any:
    """下载员工侧导出任务产物。

    响应结构：二进制文件流
    """
    # 尚无真实导出产物时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="导出产物不存在或已过期")


# ---------------------------------------------------------------------------
# 工作台仪表盘
#
# 响应结构：EmployeeDashboardResp
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def get_employee_dashboard(
    month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取员工工作台仪表盘。

    响应结构：EmployeeDashboardResp
    """
    return _ok(
        {
            "currentMonth": month or "",
            "orgName": "",
            "monthPlans": [],
            "dayPlanCalendar": [],
            "workdayCalendar": [],
            "summary": {
                "monthPlanCount": 0,
                "submittedResultCount": 0,
                "averageCompletionRate": 0,
                "openAppealCount": 0,
                "missingRequiredDayPlanCount": 0,
            },
        }
    )


# ---------------------------------------------------------------------------
# 绩效证据
#
# 响应结构：EmployeePerformanceEvidenceResp
# ---------------------------------------------------------------------------


@router.get("/performance-evidence")
async def get_employee_performance_evidence(
    period_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取员工绩效证据列表。

    响应结构：EmployeePerformanceEvidenceResp
    = { periodType, periodStart, periodEnd, items }
    """
    return _ok(
        {
            "periodType": period_type or "",
            "periodStart": "",
            "periodEnd": "",
            "items": [],
        }
    )


@router.post("/performance-evidence/export")
async def export_employee_performance_evidence(
    data: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """导出员工绩效证据。

    响应结构：EmployeeExportResp = { taskId, fileName, checksum, downloadUrl }
    """
    return _ok(
        {
            "taskId": "",
            "fileName": "",
            "checksum": "",
            "downloadUrl": "",
        }
    )


# ---------------------------------------------------------------------------
# 申诉
#
# 响应结构：EmployeeAppealListResp / EmployeeAppealOption[]
# ---------------------------------------------------------------------------


@router.get("/appeals")
async def list_employee_appeals(db: Session = Depends(get_db)) -> dict:
    """获取员工申诉列表。

    响应结构：EmployeeAppealListResp = { items }
    """
    return _ok({"items": []})


@router.get("/appeals/options")
async def list_employee_appeal_options(db: Session = Depends(get_db)) -> dict:
    """获取可申诉的成果选项。

    响应结构：EmployeeAppealOption[]（裸数组）
    """
    return _ok([])


@router.post("/appeals")
async def create_employee_appeal(data: dict, db: Session = Depends(get_db)) -> dict:
    """发起申诉。

    请求体：CreateEmployeeAppealReq = { relatedResultId?, title, reason }
    响应结构：EmployeeAppealItem
    """
    return _ok(
        {
            "id": 0,
            "appealNo": "",
            "title": data.get("title", ""),
            "reason": data.get("reason", ""),
            "status": "PENDING",
            "handleComment": "",
            "createdAt": "",
        }
    )


@router.get("/appeals/{appeal_id}/package")
async def download_employee_appeal_package(
    appeal_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """下载员工申诉证据包。

    响应结构：二进制文件流
    """
    # 尚无真实证据包时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="申诉证据包不存在或已过期")


# ---------------------------------------------------------------------------
# 计划调整
#
# 响应结构：{ id, adjustmentNo, status }
# ---------------------------------------------------------------------------


@router.post("/plan-adjustments")
async def create_employee_plan_adjustment(data: dict, db: Session = Depends(get_db)) -> dict:
    """发起计划调整申请。

    请求体：CreateEmployeePlanAdjustmentReq
    """
    return _ok({"id": 0, "adjustmentNo": "", "status": "PENDING"})
