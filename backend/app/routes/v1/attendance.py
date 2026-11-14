"""考勤域 API —— 阶段二（考勤与证据域）。

接口清单：
- POST /v1/attendance/sync            触发飞书考勤同步（HR / 管理员）
- GET  /v1/attendance/summary         某员工的周期考勤统计
- GET  /v1/attendance/records         原始打卡记录明细
- GET  /v1/attendance/anomalies       六类异常证据（主管评分页视图）
- GET  /v1/attendance/adjustment      考勤调整分建议（仅供参考，需人工确认）

红线：考勤相关接口只产出 reference_only 证据与**建议**，
任何接口都不会把考勤数据写进 MetricValue 算分集。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.db import get_db
from ...dependencies import get_current_user
from ...domain.enums import AttendanceAnomalyType, EvidenceUsage
from ...domain.models import AttendanceRecord, Employee
from ...exceptions import PermissionDenied, RecordNotFound
from ...services import attendance_stats, evidence_service
from ...services.access_control import (
    PermissionAction,
    build_access_context,
    can_access_employee,
)
from ...services.attendance_sync import AttendanceSyncService
from ...services.feishu_client import FeishuClient, FeishuError

router = APIRouter(prefix="/v1/attendance", tags=["attendance"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class AttendanceSyncRequest(BaseModel):
    """考勤同步请求。"""

    start_date: str = Field(..., description="起始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    employee_ids: list[str] | None = Field(
        default=None, description="指定员工；为空则同步全部在职员工"
    )
    limit: int | None = Field(default=None, ge=1, description="最多同步人数（调试用）")
    generate_evidence: bool = Field(
        default=True, description="是否同步生成 reference_only 证据"
    )
    need_overtime_result: bool = Field(default=True, description="是否拉取加班班段")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _parse_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD 格式：{value}") from exc


def _build_client() -> FeishuClient:
    return FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )


def _require_sync_permission(db: Session, current_user: Employee) -> None:
    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.SYNC_ATTENDANCE):
        raise PermissionDenied(
            user_id=current_user.id,
            action="sync_attendance",
            resource="attendance synchronization",
        )


def _require_employee_access(
    db: Session, current_user: Employee, target_employee_id: str
) -> None:
    """校验当前用户能否查看目标员工的考勤数据。"""
    if target_employee_id == current_user.id:
        return
    context = build_access_context(db, current_user)
    if context.has_permission(PermissionAction.VIEW_ALL_SCORES):
        return
    target = db.query(Employee).filter_by(id=target_employee_id).first()
    if target is None:
        raise RecordNotFound("Employee", target_employee_id)
    if not can_access_employee(context, target, db):
        raise PermissionDenied(
            user_id=current_user.id,
            action="view_attendance",
            resource=f"employee:{target_employee_id}",
        )


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


@router.post("/sync")
def sync_attendance(
    req: AttendanceSyncRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """从飞书同步考勤数据。

    幂等：重复同步同一区间不会产生重复记录，也不会重复累加证据。
    """
    _require_sync_permission(db, current_user)

    start_date = _parse_date(req.start_date, "start_date")
    end_date = _parse_date(req.end_date, "end_date")
    if end_date < start_date:
        raise ValueError("end_date 不能早于 start_date")

    client = _build_client()
    try:
        service = AttendanceSyncService(db, client)
        report = service.sync(
            start_date,
            end_date,
            employee_ids=req.employee_ids,
            limit=req.limit,
            generate_evidence=req.generate_evidence,
            need_overtime_result=req.need_overtime_result,
        )
        db.commit()
    except FeishuError as exc:
        db.rollback()
        raise ValueError(f"飞书考勤同步失败：{exc.message}") from exc
    finally:
        client.close()

    return {"code": 0, "data": report.as_dict()}


@router.get("/summary")
def get_attendance_summary(
    employee_id: str = Query(..., description="员工 ID"),
    start_date: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """某员工在日期区间的考勤统计（六类异常次数 + 异常天数）。"""
    _require_employee_access(db, current_user, employee_id)
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")

    service = attendance_stats.AttendanceStatisticsService(db)
    summary = service.summarize(employee_id, start, end)
    return {"code": 0, "data": summary.as_dict()}


@router.get("/records")
def list_attendance_records(
    employee_id: str = Query(..., description="员工 ID"),
    start_date: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """原始打卡记录明细（含班次、打卡时间、结果、异常标记）。"""
    _require_employee_access(db, current_user, employee_id)
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")

    rows = list(
        db.execute(
            select(AttendanceRecord)
            .where(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.work_date >= start,
                AttendanceRecord.work_date <= end,
            )
            .order_by(AttendanceRecord.work_date, AttendanceRecord.shift_seq)
            .limit(limit)
        ).scalars().all()
    )

    return {
        "code": 0,
        "data": [
            {
                "id": row.id,
                "work_date": row.work_date.isoformat(),
                "shift_seq": row.shift_seq,
                "task_shift_type": row.task_shift_type,
                "check_in_result": row.check_in_result,
                "check_out_result": row.check_out_result,
                "check_in_time": row.check_in_time,
                "check_out_time": row.check_out_time,
                "check_in_location": row.check_in_location,
                "check_out_location": row.check_out_location,
                "is_field": row.is_field,
                "anomaly_types": list(row.anomaly_types or []),
                "as_of": row.as_of.isoformat() if row.as_of else None,
            }
            for row in rows
        ],
    }


@router.get("/anomalies")
def list_attendance_anomalies(
    employee_id: str = Query(..., description="员工 ID"),
    period: str = Query(..., description="考核周期 YYYY-MM"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """六类异常证据（主管评分页视图）。

    全部为 reference_only，带免责标注，不参与算分。
    """
    _require_employee_access(db, current_user, employee_id)

    rows = list(
        db.execute(
            select(evidence_service.Evidence).where(
                evidence_service.Evidence.employee_id == employee_id,
                evidence_service.Evidence.source.in_(
                    evidence_service.ATTENDANCE_SOURCES
                ),
                evidence_service.Evidence.usage
                == EvidenceUsage.REFERENCE_ONLY.value,
            )
        ).scalars().all()
    )

    # source_ref 格式：`{employee_id}|{period}|attendance|{anomaly_type}`
    selected = [
        r
        for r in rows
        if (r.source_ref or "").split("|")[1:2] == [period]
    ]
    view = evidence_service.build_attendance_view(selected)
    return {
        "code": 0,
        "data": {
            "employee_id": employee_id,
            "period": period,
            "anomalies": view,
            "disclaimer": evidence_service.ATTENDANCE_DISCLAIMER,
            "usage": EvidenceUsage.REFERENCE_ONLY.value,
        },
    }


@router.get("/adjustment")
def get_attendance_adjustment(
    employee_id: str = Query(..., description="员工 ID"),
    start_date: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    preset: str = Query(default="progressive_weekly", description="考勤规则预设"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """考勤调整分**建议**。

    仅供人工确认参考；系统不会自动把它写进算分集。
    """
    _require_employee_access(db, current_user, employee_id)
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")

    service = attendance_stats.AttendanceStatisticsService(db)
    summary = service.summarize(employee_id, start, end)
    context = attendance_stats.build_adjustment_context(summary, preset=preset)
    return {"code": 0, "data": context}


@router.get("/anomaly-types")
def list_anomaly_types(
    current_user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """六类异常类型定义（前端下拉/展示用）。"""
    labels = {
        AttendanceAnomalyType.LATE.value: "迟到",
        AttendanceAnomalyType.EARLY_LEAVE.value: "早退",
        AttendanceAnomalyType.MISSING_PUNCH.value: "缺卡",
        AttendanceAnomalyType.LEAVE.value: "请假",
        AttendanceAnomalyType.OVERTIME.value: "加班",
        AttendanceAnomalyType.OUTSIDE.value: "外勤",
    }
    return {
        "code": 0,
        "data": [
            {"value": value, "label": labels[value]} for value in labels
        ],
    }
