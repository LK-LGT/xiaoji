"""飞书同步与身份映射管理 API。

全部写接口仅系统管理员可用；响应不包含 App Secret、Access Token。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import (
    Employee,
    FeishuIdentity,
    FeishuIdentityAudit,
    OrganizationSyncAudit,
    OrganizationSyncBatch,
    OrganizationSyncItemError,
)
from ..exceptions import BaseAppError, PermissionDenied
from ..services.access_control import PermissionAction, build_access_context
from ..services.feishu_client import FeishuClient
from ..services.feishu_identity_service import FeishuIdentityService
from ..services.organization_sync import OrganizationSyncService

router = APIRouter(prefix="/v1/feishu", tags=["feishu"])


class SyncRequest(BaseModel):
    mode: str = Field(default="full", pattern="^(full|incremental)$")


class IdentityBindRequest(BaseModel):
    employee_id: str = Field(..., min_length=1)
    identity_type: str = Field(..., pattern="^(user_id|open_id|union_id)$")
    identity_value: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(..., min_length=1, max_length=500)
    allow_rebind: bool = False


class IdentityUnbindRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class LoginAccountRequest(BaseModel):
    login_account: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=500)


def _require_admin(db: Session, current_user: Employee) -> None:
    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.SYNC_ORGANIZATION):
        raise PermissionDenied(
            user_id=current_user.id,
            action="manage_feishu_organization",
            resource="Feishu organization",
        )


def _client() -> FeishuClient:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(status_code=503, detail="飞书应用配置未完成")
    return FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )


@router.post("/sync-organization")
def sync_organization(
    request: SyncRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """手动触发组织架构同步，仅系统管理员可用。"""
    _require_admin(db, current_user)
    client = _client()
    try:
        service = OrganizationSyncService(
            db,
            client,
            mode=request.mode,
            triggered_by=current_user.id,
            trigger_type="manual",
        )
        stats = service.sync_all()
    finally:
        client.close()
    return {"code": 0, "message": "组织架构同步完成", "data": stats}


@router.post("/sync-organization/retry")
def retry_organization_sync(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """重试同步失败记录。"""
    _require_admin(db, current_user)
    client = _client()
    try:
        service = OrganizationSyncService(
            db,
            client,
            triggered_by=current_user.id,
            trigger_type="manual_retry",
        )
        result = service.retry_failed(actor_id=current_user.id)
    finally:
        client.close()
    return {"code": 0, "message": "失败记录重试完成", "data": result}


@router.post("/sync-organization/recover-stale")
def recover_stale_sync_batches(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """把超时的 running 批次标记为 failed。

    多实例部署时，若某实例在同步中被 kill，批次会永久停留在 running。
    定时任务会自动调用本逻辑，本接口供管理员手工触发。
    """
    _require_admin(db, current_user)
    client = _client()
    try:
        service = OrganizationSyncService(
            db,
            client,
            triggered_by=current_user.id,
            trigger_type="manual",
        )
        recovered = service.recover_stale_batches()
    finally:
        client.close()
    return {
        "code": 0,
        "message": "超时批次已恢复",
        "data": {"recovered": recovered},
    }


@router.get("/sync-organization/batches")
def list_sync_batches(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    total = db.query(OrganizationSyncBatch).count()
    rows = (
        db.query(OrganizationSyncBatch)
        .order_by(OrganizationSyncBatch.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "id": row.id,
                    "batch_no": row.batch_no,
                    "status": row.status,
                    "mode": row.mode,
                    "trigger_type": row.trigger_type,
                    "triggered_by": row.triggered_by,
                    "departments_synced": row.departments_synced,
                    "departments_skipped": row.departments_skipped,
                    "departments_failed": row.departments_failed,
                    "employees_synced": row.employees_synced,
                    "employees_skipped": row.employees_skipped,
                    "employees_failed": row.employees_failed,
                    "reconciliation": row.reconciliation_json,
                    "started_at": row.started_at.isoformat(),
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                    "duration_ms": row.duration_ms,
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/sync-organization/errors")
def list_sync_errors(
    status: str | None = Query(default=None),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    query = db.query(OrganizationSyncItemError)
    if status:
        query = query.filter(OrganizationSyncItemError.status == status)
    rows = query.order_by(OrganizationSyncItemError.created_at.desc()).limit(500).all()
    return {
        "code": 0,
        "data": [
            {
                "id": row.id,
                "batch_id": row.batch_id,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "message": row.message,
                "retry_count": row.retry_count,
                "status": row.status,
                "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/sync-organization/audits")
def list_sync_audits(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    rows = (
        db.query(OrganizationSyncAudit)
        .order_by(OrganizationSyncAudit.created_at.desc())
        .limit(500)
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": row.id,
                "batch_id": row.batch_id,
                "actor_id": row.actor_id,
                "action": row.action,
                "resource_type": row.resource_type,
                "result": row.result,
                "detail": row.detail_json,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/identities/unbound")
def list_unbound_identities(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    return {
        "code": 0,
        "data": FeishuIdentityService(db).list_unbound(),
    }


@router.get("/identities/conflicts")
def list_identity_conflicts(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    return {
        "code": 0,
        "data": FeishuIdentityService(db).detect_conflicts(),
    }


@router.post("/identities/bind")
def bind_identity(
    request: IdentityBindRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """人工绑定飞书身份；冲突必须显式 allow_rebind。"""
    _require_admin(db, current_user)
    service = FeishuIdentityService(db)
    try:
        row = service.bind(
            employee_id=request.employee_id,
            identity_type=request.identity_type,
            identity_value=request.identity_value,
            actor_id=current_user.id,
            reason=request.reason,
            allow_rebind=request.allow_rebind,
        )
        db.commit()
    except BaseAppError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="身份绑定冲突") from exc
    return {
        "code": 0,
        "message": "身份绑定成功",
        "data": {
            "id": row.id,
            "employee_id": row.employee_id,
            "identity_type": row.identity_type,
            "identity_value": row.identity_value,
            "active": row.active,
        },
    }


@router.post("/identities/{identity_id}/unbind")
def unbind_identity(
    identity_id: str,
    request: IdentityUnbindRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    service = FeishuIdentityService(db)
    try:
        service.unbind(
            identity_id=identity_id,
            actor_id=current_user.id,
            reason=request.reason,
        )
        db.commit()
    except BaseAppError:
        db.rollback()
        raise
    return {"code": 0, "message": "身份解绑成功"}


@router.post("/identities/{identity_id}/disable-account")
def disable_identity_account(
    identity_id: str,
    request: IdentityUnbindRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    identity = db.get(FeishuIdentity, identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail="身份映射不存在")
    service = FeishuIdentityService(db)
    try:
        service.disable_account(
            employee_id=identity.employee_id,
            actor_id=current_user.id,
            reason=request.reason,
        )
        db.commit()
    except BaseAppError:
        db.rollback()
        raise
    return {"code": 0, "message": "员工账号已禁用"}


@router.post("/identities/employee/{employee_id}/login-account")
def set_login_account(
    employee_id: str,
    request: LoginAccountRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    service = FeishuIdentityService(db)
    try:
        service.set_login_account(
            employee_id=employee_id,
            login_account=request.login_account,
            actor_id=current_user.id,
            reason=request.reason,
        )
        db.commit()
    except BaseAppError:
        db.rollback()
        raise
    return {"code": 0, "message": "登录账号设置成功"}


@router.get("/identities/audits")
def list_identity_audits(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    rows = (
        db.query(FeishuIdentityAudit)
        .order_by(FeishuIdentityAudit.created_at.desc())
        .limit(500)
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "id": row.id,
                "identity_id": row.identity_id,
                "identity_type": row.identity_type,
                "identity_value": row.identity_value,
                "employee_id": row.employee_id,
                "action": row.action,
                "old_value": row.old_value_json,
                "new_value": row.new_value_json,
                "reason": row.reason,
                "actor_id": row.actor_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }
