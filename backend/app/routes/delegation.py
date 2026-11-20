"""P1-4 —— 代理授权 API。

委托人（请假的主管）把指定范围内的管理权限临时交给受托人。
- **创建委托**：委托人本人。
- **撤销委托**：委托人本人或 HR。
- **查询**：委托人、受托人、HR 都能看相关委托。

有效期是硬闸门：``ends_at`` 之前有效，到点自动失效，
不需要任何后台任务去「撤销」。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Delegation, Employee
from ..services import access_control
from ..services import delegation_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/v1/delegations", tags=["delegation"], dependencies=[Depends(get_current_user)]
)


class DelegationCreate(BaseModel):
    """创建委托请求。"""

    delegate_id: str = Field(..., description="受托人 ID")
    scope_type: str = Field("direct_reports", description="范围类型")
    scope_value: str = Field("", description="范围值（explicit 时为员工 ID 列表）")
    reason: str = Field("", description="委托事由")
    starts_at: datetime | None = Field(None, description="生效时间")
    ends_at: datetime | None = Field(None, description="失效时间")


def _is_hr(db: Session, user: Employee) -> bool:
    return "hr" in access_control.build_access_context(db, user).roles


@router.post("")
def create_delegation(
    body: DelegationCreate,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """创建代理授权（委托人 = 当前登录者）。"""
    try:
        row = svc.create_delegation(
            db,
            delegator_id=current_user.id,
            delegate_id=body.delegate_id,
            scope_type=body.scope_type,
            scope_value=body.scope_value,
            reason=body.reason,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            created_by=current_user.id,
        )
    except svc.DelegationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"code": 0, "data": svc.serialize(row)}


@router.get("")
def list_delegations(
    role: str = Query("all", pattern="^(all|delegator|delegate)$"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询委托记录。

    - ``delegator``：我委托出去的
    - ``delegate``：我被委托的
    - ``all``：以上都查（HR 额外可见全员）
    """
    stmt = select(Delegation)
    hr = _is_hr(db, current_user)
    if role == "delegator":
        stmt = stmt.where(Delegation.delegator_id == current_user.id)
    elif role == "delegate":
        stmt = stmt.where(Delegation.delegate_id == current_user.id)
    elif not hr:
        stmt = stmt.where(
            (Delegation.delegator_id == current_user.id)
            | (Delegation.delegate_id == current_user.id)
        )
    rows = db.scalars(stmt.order_by(Delegation.created_at.desc())).all()
    return {"code": 0, "data": [svc.serialize(row) for row in rows]}


@router.delete("/{delegation_id}")
def revoke_delegation(
    delegation_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """撤销代理授权（到期前主动收回）。"""
    row = db.get(Delegation, delegation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="委托记录不存在")
    if row.delegator_id != current_user.id and not _is_hr(db, current_user):
        raise HTTPException(status_code=403, detail="无权撤销该委托")
    try:
        svc.revoke_delegation(db, delegation_id=delegation_id, actor_id=current_user.id)
    except svc.DelegationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"code": 0, "data": svc.serialize(row)}


@router.get("/my-scope")
def my_delegated_scope(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询我当前通过委托获得的员工范围（到期自动缩小）。"""
    ids = svc.delegated_employee_ids(db, delegate_id=current_user.id)
    return {
        "code": 0,
        "data": {
            "employeeIds": sorted(ids),
            "count": len(ids),
            "activeDelegations": len(
                svc.active_delegations(db, delegate_id=current_user.id)
            ),
        },
    }
