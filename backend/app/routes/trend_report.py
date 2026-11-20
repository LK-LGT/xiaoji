"""P2-2 —— 趋势复盘报表 API（跨周期趋势 / 岗位横向 / 风险清单 / 薪酬导出）。

权限口径
--------
- 跨周期趋势 / 岗位横向：调用者需能看目标员工 / 目标岗位的数据
  （走 ``access_control``，与既有「看绩效」口径一致）。
- 风险清单：**仅 HR 与部门负责人**（这是跨员工的名单，敏感度更高）。
- 薪酬导出：**仅 HR**（最高敏感级）。

所有端点只读 —— 不写任何业务表。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import access_control, trend_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/trend-report", tags=["trend-report"])


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "data": data}


def _roles(db: Session, user: Employee) -> frozenset[str]:
    return access_control.build_access_context(db, user).roles


def _require_roster_role(db: Session, user: Employee) -> None:
    if not ({"hr", "department_leader"} & _roles(db, user)):
        raise HTTPException(status_code=403, detail="仅 HR 或部门负责人可查看风险清单")


def _require_hr(db: Session, user: Employee) -> None:
    if "hr" not in _roles(db, user):
        raise HTTPException(status_code=403, detail="仅 HR 可导出薪酬数据")


@router.get("/trend/{employee_id}")
def period_trend(
    employee_id: str,
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """单个员工的跨周期趋势。"""
    ctx = access_control.build_access_context(db, user)
    if not ctx.can_view_score_of(employee_id):
        raise HTTPException(status_code=403, detail="无权查看该员工的绩效数据")
    return _ok(trend_report.build_period_trend(db, employee_id=employee_id))


@router.get("/position")
def position_comparison(
    position: str = Query(..., min_length=1, max_length=128),
    period: str | None = Query(None),
    task_id: str | None = Query(None),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """岗位横向对比（自带小样本保护）。"""
    ctx = access_control.build_access_context(db, user)
    roles = ctx.roles
    if not ({"hr", "department_leader", "manager"} & roles):
        raise HTTPException(status_code=403, detail="无权查看岗位对比")
    return _ok(
        trend_report.build_position_comparison(
            db, position=position, period=period, task_id=task_id
        )
    )


@router.get("/risk-roster")
def risk_roster(
    department_id: str | None = Query(None),
    period: str | None = Query(None),
    task_id: str | None = Query(None),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """风险清单。"""
    _require_roster_role(db, user)
    ctx = access_control.build_access_context(db, user)
    # 部门负责人只能看自己管理的部门
    if department_id and "hr" not in ctx.roles:
        if department_id not in ctx.managed_department_ids:
            raise HTTPException(status_code=403, detail="无权查看该部门的风险清单")
    return _ok(
        trend_report.build_risk_roster(
            db, department_id=department_id, period=period, task_id=task_id
        )
    )


@router.get("/payroll-export")
def payroll_export(
    period: str | None = Query(None),
    task_id: str | None = Query(None),
    department_id: str | None = Query(None),
    include_names: bool = Query(False),
    user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """薪酬建议导出（仅 HR）。"""
    _require_hr(db, user)
    return _ok(
        trend_report.build_payroll_export(
            db,
            period=period,
            task_id=task_id,
            department_id=department_id,
            include_names=include_names,
        )
    )
