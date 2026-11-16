"""飞书同步与身份映射管理 API。

全部写接口仅系统管理员可用；响应不包含 App Secret、Access Token。

同步执行模型：
- ``POST /sync-organization`` 只创建批次并在后台执行，立即返回批次 ID；
- 前端通过 ``GET /sync-organization/batches/{batch_id}`` 查询进度；
- 人工触发与定时触发共用分布式锁，保证同一时刻只有一个同步在跑。
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
    OrganizationSyncCursor,
    OrganizationSyncItemError,
)
from ..exceptions import BaseAppError, PermissionDenied
from ..services.access_control import PermissionAction, build_access_context
from ..services.feishu_client import FeishuClient
from ..services.feishu_identity_service import FeishuIdentityService
from ..services.feishu_sync_errors import category_payload
from ..services.feishu_sync_tasks import (
    SyncAlreadyRunningError,
    SyncNotConfiguredError,
    get_sync_status,
    start_async_sync,
)
from ..services.organization_sync import OrganizationSyncService

router = APIRouter(prefix="/v1/feishu", tags=["feishu"])


class SyncRequest(BaseModel):
    mode: str = Field(default="full", pattern="^(full|incremental)$")
    # 可选幂等键。相同键的重复触发复用同一批次。
    idempotency_key: str | None = Field(default=None, max_length=160)


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
    """手动触发组织架构同步，仅系统管理员可用。

    同步在后台执行，本接口立即返回批次 ID（HTTP 202），
    前端应轮询 `GET /sync-organization/batches/{batch_id}` 获取进度。

    幂等：相同幂等键（或同一 5 分钟窗口内的同一操作人+模式）的重复
    触发会复用同一批次，不会重复执行。
    """
    _require_admin(db, current_user)
    try:
        batch, created = start_async_sync(
            db,
            mode=request.mode,
            trigger_type="manual",
            actor_id=current_user.id,
            idempotency_key=request.idempotency_key,
        )
    except SyncNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SyncAlreadyRunningError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "batchId": exc.batch_id,
            },
        ) from exc

    return {
        "code": 0,
        "message": "同步任务已提交，正在后台执行" if created else "已有相同任务在执行，返回原批次",
        "data": {
            "batchId": batch.id,
            "batchNo": batch.batch_no,
            "status": batch.status,
            "mode": batch.mode,
            "created": created,
        },
    }


@router.get("/sync-organization/status")
def get_organization_sync_status(
    current_user=Depends(get_current_user),
    db: Session=Depends(get_db),
) -> dict[str, Any]:
    """同步链路状态：配置、定时开关、上次结果、当前批次与游标。"""
    _require_admin(db, current_user)
    return {"code": 0, "data": get_sync_status(db)}


@router.post("/sync-organization/retry")
def retry_organization_sync(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """重试同步失败记录。

    只重试已到期的记录；可自动重试的分类失败后按指数退避安排下次重试，
    其它分类进入死信等待人工处理。
    """
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


def _serialize_batch(row: OrganizationSyncBatch) -> dict[str, Any]:
    """批次序列化。统一使用 camelCase，与前端契约一致。"""
    return {
        "id": row.id,
        "batchNo": row.batch_no,
        "status": row.status,
        "mode": row.mode,
        "triggerType": row.trigger_type,
        "triggeredBy": row.triggered_by,
        "phase": row.phase,
        "phaseDetail": row.phase_detail,
        "departmentsSynced": row.departments_synced,
        "departmentsSkipped": row.departments_skipped,
        "departmentsFailed": row.departments_failed,
        "employeesSynced": row.employees_synced,
        "employeesSkipped": row.employees_skipped,
        "employeesFailed": row.employees_failed,
        "errorMessage": row.error_message,
        "errorCategory": row.error_category,
        "errorCategoryInfo": category_payload(row.error_category),
        "reconciliation": row.reconciliation_json,
        "startedAt": row.started_at.isoformat() if row.started_at else None,
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
        "durationMs": row.duration_ms,
    }


@router.get("/sync-organization/batches")
def list_sync_batches(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    pageSize: int | None = Query(default=None, ge=1, le=100),
    status: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    size = pageSize if pageSize is not None else page_size
    query = db.query(OrganizationSyncBatch)
    if status:
        query = query.filter(OrganizationSyncBatch.status == status)
    if mode:
        query = query.filter(OrganizationSyncBatch.mode == mode)
    total = query.count()
    rows = (
        query.order_by(OrganizationSyncBatch.started_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "code": 0,
        "data": {
            "records": [_serialize_batch(row) for row in rows],
            "total": total,
            "page": page,
            "pageSize": size,
        },
    }


@router.get("/sync-organization/batches/{batch_id}")
def get_sync_batch(
    batch_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批次详情，供前端轮询进度。"""
    _require_admin(db, current_user)
    row = db.get(OrganizationSyncBatch, batch_id)
    if row is None:
        raise HTTPException(status_code=404, detail="同步批次不存在")
    return {"code": 0, "data": _serialize_batch(row)}


@router.get("/sync-organization/cursors")
def list_sync_cursors(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """同步游标：各资源上一次成功推进的位置。"""
    _require_admin(db, current_user)
    rows = db.execute(select(OrganizationSyncCursor)).scalars().all()
    return {
        "code": 0,
        "data": [
            {
                "resourceType": row.resource_type,
                "checkpointValue": row.checkpoint_value,
                "cursorValue": row.cursor_value,
                "pageToken": row.page_token,
                "snapshotComplete": row.snapshot_complete,
                "lastBatchId": row.last_batch_id,
                "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ],
    }


@router.get("/sync-organization/reconciliation/latest")
def get_latest_reconciliation(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """最近一次对账结果（含数据质量指标）。"""
    _require_admin(db, current_user)
    row = (
        db.query(OrganizationSyncBatch)
        .filter(OrganizationSyncBatch.reconciliation_json.is_not(None))
        .order_by(OrganizationSyncBatch.finished_at.desc())
        .first()
    )
    if row is None:
        return {"code": 0, "data": None}
    return {
        "code": 0,
        "data": {
            "batchId": row.id,
            "batchNo": row.batch_no,
            "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
            "reconciliation": row.reconciliation_json,
        },
    }


@router.get("/sync-organization/errors")
def list_sync_errors(
    status: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resourceType: str | None = Query(default=None),
    error_category: str | None = Query(default=None),
    errorCategory: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    batchId: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    size = pageSize if pageSize is not None else page_size
    resource = resourceType if resourceType is not None else resource_type
    category = errorCategory if errorCategory is not None else error_category
    batch = batchId if batchId is not None else batch_id
    query = db.query(OrganizationSyncItemError)
    if status:
        query = query.filter(OrganizationSyncItemError.status == status)
    if resource:
        query = query.filter(OrganizationSyncItemError.resource_type == resource)
    if category:
        query = query.filter(OrganizationSyncItemError.error_category == category)
    if batch:
        query = query.filter(OrganizationSyncItemError.batch_id == batch)
    total = query.count()
    rows = (
        query.order_by(OrganizationSyncItemError.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "id": row.id,
                    "batchId": row.batch_id,
                    "resourceType": row.resource_type,
                    "resourceId": row.resource_id,
                    "message": row.message,
                    "errorCode": row.error_code,
                    "errorCategory": row.error_category,
                    "errorCategoryInfo": category_payload(row.error_category),
                    "retryCount": row.retry_count,
                    "status": row.status,
                    "nextRetryAt": row.next_retry_at.isoformat() if row.next_retry_at else None,
                    "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
                    "createdAt": row.created_at.isoformat(),
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "pageSize": size,
        },
    }


@router.get("/sync-organization/audits")
def list_sync_audits(
    action: str | None = Query(default=None),
    result: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    actorId: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    batchId: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, current_user)
    size = pageSize if pageSize is not None else page_size
    actor = actorId if actorId is not None else actor_id
    batch = batchId if batchId is not None else batch_id
    query = db.query(OrganizationSyncAudit)
    if action:
        query = query.filter(OrganizationSyncAudit.action == action)
    if result:
        query = query.filter(OrganizationSyncAudit.result == result)
    if actor:
        query = query.filter(OrganizationSyncAudit.actor_id == actor)
    if batch:
        query = query.filter(OrganizationSyncAudit.batch_id == batch)
    total = query.count()
    rows = (
        query.order_by(OrganizationSyncAudit.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "id": row.id,
                    "batchId": row.batch_id,
                    "actorId": row.actor_id,
                    "action": row.action,
                    "resourceType": row.resource_type,
                    "resourceId": row.resource_id,
                    "result": row.result,
                    "detail": row.detail_json,
                    "createdAt": row.created_at.isoformat(),
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "pageSize": size,
        },
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
