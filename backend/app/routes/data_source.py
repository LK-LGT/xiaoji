"""P1-5 —— 数据源白名单配置 API（HR 专属）。

配置两类内容：
- **可读源**：哪些数据源允许进入系统（默认不信任，未配置即不可读）。
- **脱敏字段**：每个源哪些字段读出时要打码。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import access_control
from ..services import data_source_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/v1/data-sources",
    tags=["data-source"],
    dependencies=[Depends(get_current_user)],
)


class PolicyUpsert(BaseModel):
    """数据源策略更新请求。"""

    source_code: str = Field(..., description="数据源标识")
    display_name: str = Field("", description="显示名")
    allowed: bool = Field(False, description="是否允许进入算分集")
    allowed_roles: str = Field("hr", description="可读角色，逗号分隔")
    redacted_fields: str = Field("", description="需脱敏字段，逗号分隔")
    note: str = Field("", description="配置说明")


def _require_hr(db: Session, user: Employee) -> None:
    if "hr" not in access_control.build_access_context(db, user).roles:
        raise HTTPException(status_code=403, detail="仅 HR 可配置数据源白名单")


@router.get("")
def list_policies(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """列出全部数据源策略（含未配置的默认项）。"""
    policies = svc.load_policies(db)
    return {
        "code": 0,
        "data": [svc.serialize(policies[code]) for code in sorted(policies)],
    }


@router.put("")
def upsert_policy(
    body: PolicyUpsert,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """新增或更新数据源策略。仅 HR 可写。"""
    _require_hr(db, current_user)
    try:
        svc.upsert_policy(
            db,
            source_code=body.source_code,
            display_name=body.display_name,
            allowed=body.allowed,
            allowed_roles=body.allowed_roles,
            redacted_fields=body.redacted_fields,
            note=body.note,
            updated_by=current_user.id,
        )
    except svc.DataSourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    policy = svc.get_policy(db, body.source_code)
    return {"code": 0, "data": svc.serialize(policy or {})}
