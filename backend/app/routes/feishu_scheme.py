"""飞书绩效方案来源绑定与自动同步管理 API。

端点清单（全部挂在 ``/v1/feishu/scheme``）：

    GET    /sources                     列出来源绑定
    POST   /sources                     人工绑定（部门 + 飞书链接）
    PATCH  /sources/{id}                启用 / 停用 / 改适用范围
    DELETE /sources/{id}                解除绑定（停用）
    POST   /sources/discover            触发自动发现（dry_run 可选）
    POST   /sync                        立即同步（可指定单个来源）
    GET    /sync/batches                同步批次历史
    GET    /sync/status                 调度器运行态

权限
----
写操作要求 **HR 或系统管理员**；读操作要求 **HR 或部门负责人**
（部门负责人只能看到自己管辖部门的绑定）。

⚠️ **鉴权一律以 token 里的身份推导**，不接受请求体里的 user_id。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.feishu_scheme_models import FeishuSchemeSource, FeishuSchemeSyncBatch
from ..domain.models import Employee
from ..services.access_control import build_access_context
from ..services.feishu_docs import (
    DocsContentUnavailable,
    DocsPermissionError,
    build_docs_client,
)
from ..services.feishu_scheme_source import SchemeSourceService
from ..services.feishu_scheme_sync import FeishuSchemeSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feishu/scheme", tags=["feishu-scheme"])


# ─────────────────────────────────────────────────────────────
# 请求模型
# ─────────────────────────────────────────────────────────────


class BindRequest(BaseModel):
    department_id: str = Field(min_length=1, max_length=64)
    source_url: str = Field(min_length=1, max_length=512)
    period_type: str = Field(default="monthly", pattern="^(monthly|quarterly|annual)$")
    position: str = Field(default="", max_length=128)
    scheme_code: str = Field(default="", max_length=64)


class PatchSourceRequest(BaseModel):
    active: bool | None = None
    position: str | None = Field(default=None, max_length=128)
    period_type: str | None = Field(default=None, pattern="^(monthly|quarterly|annual)$")
    scheme_code: str | None = Field(default=None, max_length=64)


class DiscoverRequest(BaseModel):
    space_id: str = Field(default="", max_length=64)
    dry_run: bool = False


class SyncRequest(BaseModel):
    source_id: str | None = Field(default=None, max_length=64)


# ─────────────────────────────────────────────────────────────
# 权限
# ─────────────────────────────────────────────────────────────


def _require_manager(db: Session, user: Employee) -> dict[str, Any]:
    """要求 HR 或系统管理员。"""
    ctx = build_access_context(db, user)
    if "hr" in ctx.roles or "admin" in ctx.roles:
        return {"scope": None, "ctx": ctx}
    raise HTTPException(status_code=403, detail="仅HR与系统管理员可维护飞书方案绑定")


def _require_viewer(db: Session, user: Employee) -> list[str] | None:
    """可查看范围：HR / 管理员 → None（全量）；部门负责人 → 自己管辖的部门。"""
    ctx = build_access_context(db, user)
    if "hr" in ctx.roles or "admin" in ctx.roles:
        return None
    if "department_leader" in ctx.roles:
        return list(ctx.managed_department_ids)
    raise HTTPException(status_code=403, detail="无权查看飞书方案绑定")


def _serialize(source: FeishuSchemeSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "nodeToken": source.node_token,
        "objToken": source.obj_token,
        "objType": source.obj_type,
        "title": source.title,
        "spaceId": source.space_id,
        "sourceUrl": source.source_url,
        "departmentId": source.department_id,
        "position": source.position,
        "periodType": source.period_type,
        "schemeCode": source.scheme_code,
        "bindingMode": source.binding_mode,
        "active": source.active,
        "lastEditTime": source.last_edit_time,
        "lastSyncedAt": source.last_synced_at.isoformat() if source.last_synced_at else None,
        "lastVersionId": source.last_version_id,
        "lastError": source.last_error,
    }


def _serialize_batch(batch: FeishuSchemeSyncBatch) -> dict[str, Any]:
    return {
        "id": batch.id,
        "triggerType": batch.trigger_type,
        "status": batch.status,
        "sourceCount": batch.source_count,
        "changedCount": batch.changed_count,
        "publishedCount": batch.published_count,
        "failedCount": batch.failed_count,
        "errorMessage": batch.error_message,
        "detail": batch.detail,
        "startedAt": batch.started_at.isoformat() if batch.started_at else None,
        "finishedAt": batch.finished_at.isoformat() if batch.finished_at else None,
    }


def _docs_service(db: Session):
    """构造云文档服务（客户端生命周期由调用方负责关闭）。"""
    try:
        docs = build_docs_client()
    except DocsPermissionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return docs, SchemeSourceService(db, docs)


# ─────────────────────────────────────────────────────────────
# 来源绑定
# ─────────────────────────────────────────────────────────────


@router.get("/sources")
def list_sources(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出来源绑定。"""
    scope = _require_viewer(db, current_user)
    docs, service = _docs_service(db)
    try:
        sources = service.list_sources()
    finally:
        docs.client.close()
    items = [
        _serialize(s) for s in sources if scope is None or s.department_id in scope
    ]
    return {"code": 0, "data": items}


@router.post("/sources")
def bind_source(
    body: BindRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """人工绑定：把某部门的绩效方案指向一份飞书文档。

    ⚠️ 人工绑定**优先级最高** —— 自动发现永远不会覆盖它。
    """
    _require_manager(db, current_user)
    docs, service = _docs_service(db)
    try:
        source = service.bind_manual(
            department_id=body.department_id,
            source_url=body.source_url,
            period_type=body.period_type,
            position=body.position,
            scheme_code=body.scheme_code,
            actor_id=current_user.id,
        )
        db.commit()
        return {"code": 0, "message": "绑定成功", "data": _serialize(source)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DocsPermissionError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DocsContentUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        docs.client.close()


@router.patch("/sources/{source_id}")
def patch_source(
    source_id: str,
    body: PatchSourceRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """调整绑定（启用 / 停用 / 改适用范围）。"""
    _require_manager(db, current_user)
    docs, service = _docs_service(db)
    try:
        source = service.get(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="绑定不存在")
        if body.active is not None:
            source.active = body.active
        if body.position is not None:
            source.position = body.position
        if body.period_type is not None:
            source.period_type = body.period_type
        if body.scheme_code is not None:
            source.scheme_code = body.scheme_code
        db.commit()
        db.refresh(source)
        return {"code": 0, "message": "已更新", "data": _serialize(source)}
    finally:
        docs.client.close()


@router.delete("/sources/{source_id}")
def delete_source(
    source_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """解除绑定（停用，保留审计线索）。"""
    _require_manager(db, current_user)
    docs, service = _docs_service(db)
    try:
        service.unbind(source_id, actor_id=current_user.id)
        db.commit()
        return {"code": 0, "message": "已解除绑定"}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        docs.client.close()


@router.post("/sources/discover")
def discover_sources(
    body: DiscoverRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """自动发现：扫描知识空间，按命名规则匹配部门方案文档。

    ⚠️ 需要 ``wiki:wiki:readonly`` 权限。**2026-11-20 实测本应用没有该权限**
    ⇒ 返回 ``skipped`` 而不是报错。
    """
    _require_manager(db, current_user)
    docs, service = _docs_service(db)
    try:
        result = service.discover(body.space_id, dry_run=body.dry_run)
        db.commit()
        return {"code": 0, "message": "自动发现完成", "data": result}
    finally:
        docs.client.close()


# ─────────────────────────────────────────────────────────────
# 同步
# ─────────────────────────────────────────────────────────────


@router.post("/sync")
def trigger_sync(
    body: SyncRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """立即执行一轮同步。

    ⚠️ 同步会**写业务数据**（建方案版本 / 发布），仅 HR 与管理员可触发。
    """
    _require_manager(db, current_user)
    docs, _ = _docs_service(db)
    try:
        service = FeishuSchemeSyncService(
            db, docs, actor_id=current_user.id, trigger_type="manual"
        )
        batch = service.run(only_source_id=body.source_id)
        db.commit()
        return {"code": 0, "message": "同步完成", "data": _serialize_batch(batch)}
    finally:
        docs.client.close()


@router.get("/sync/batches")
def list_sync_batches(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """同步批次历史。"""
    _require_viewer(db, current_user)
    total = db.query(FeishuSchemeSyncBatch).count()
    rows = (
        db.query(FeishuSchemeSyncBatch)
        .order_by(FeishuSchemeSyncBatch.started_at.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )
    return {
        "code": 0,
        "data": {
            "records": [_serialize_batch(r) for r in rows],
            "total": total,
            "page": page,
            "pageSize": pageSize,
        },
    }


@router.get("/sync/status")
def sync_status(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """调度器运行态 + 权限现状。"""
    _require_viewer(db, current_user)
    from ..services.feishu_scheme_scheduler import get_scheduler_status

    return {
        "code": 0,
        "data": {
            "scheduler": get_scheduler_status(),
            "autoPublish": settings.feishu_scheme_sync_auto_publish,
            "autoStartTask": settings.feishu_scheme_sync_auto_start_task,
            "spaceId": settings.feishu_scheme_sync_space_id,
            "periodType": settings.feishu_scheme_sync_period_type,
            "boundSourceCount": db.query(FeishuSchemeSource)
            .filter(FeishuSchemeSource.active.is_(True))
            .count(),
        },
    }


__all__ = ["router"]
