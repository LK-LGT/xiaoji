"""系统管理员配置 API。

管理员只能管理系统配置，不自动获得员工绩效等业务数据访问权。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...dependencies import get_current_user
from ...domain.models import Department, HrDepartmentConfig, SystemAdmin
from ...exceptions import PermissionDenied
from ...services.access_control import PermissionAction, build_access_context

router = APIRouter(prefix="/v1/system/config", tags=["system-config"])


class HrDepartmentUpsertRequest(BaseModel):
    department_id: str = Field(..., min_length=1)
    active: bool = True


class SystemAdminUpsertRequest(BaseModel):
    employee_id: str = Field(..., min_length=1)
    active: bool = True


def _require_admin(db: Session, current_user) -> None:
    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.MANAGE_SYSTEM_CONFIG):
        raise PermissionDenied(
            user_id=current_user.id,
            action="manage_system_config",
            resource="system configuration",
        )


@router.get("/hr-departments")
def list_hr_departments(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    rows = (
        db.query(HrDepartmentConfig, Department)
        .join(Department, Department.id == HrDepartmentConfig.department_id)
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "department_id": config.department_id,
                "department_name": department.name,
                "active": config.active,
                "granted_by": config.granted_by,
                "created_at": config.created_at.isoformat(),
            }
            for config, department in rows
        ],
    }


@router.post("/hr-departments")
def upsert_hr_department(
    request: HrDepartmentUpsertRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    department = db.query(Department).filter_by(id=request.department_id).first()
    if not department:
        raise PermissionDenied(
            user_id=current_user.id,
            action="manage_hr_departments",
            resource=f"unknown department:{request.department_id}",
        )

    row = db.query(HrDepartmentConfig).filter_by(
        department_id=request.department_id
    ).first()
    if not row:
        row = HrDepartmentConfig(department_id=request.department_id)
        db.add(row)
    row.active = request.active
    row.granted_by = current_user.id
    db.commit()
    return {
        "code": 0,
        "data": {
            "department_id": row.department_id,
            "active": row.active,
        },
    }


@router.get("/administrators")
def list_administrators(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    rows = db.query(SystemAdmin).all()
    return {
        "code": 0,
        "data": [
            {
                "employee_id": row.employee_id,
                "active": row.active,
                "granted_by": row.granted_by,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.post("/administrators")
def upsert_administrator(
    request: SystemAdminUpsertRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    row = db.query(SystemAdmin).filter_by(employee_id=request.employee_id).first()
    if not row:
        row = SystemAdmin(employee_id=request.employee_id)
        db.add(row)
    row.active = request.active
    row.granted_by = current_user.id
    db.commit()
    return {
        "code": 0,
        "data": {
            "employee_id": row.employee_id,
            "active": row.active,
        },
    }
