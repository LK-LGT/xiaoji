"""百度网盘绩效校验管理 API。

端点清单（全部挂在 ``/v1/baidu/pan``）：

    GET    /status                  授权状态 + 目录可读性探针
    GET    /authorize-url           生成授权链接（管理员去勾选网盘目录）
    POST   /authorize               提交授权码 → 换 token
    POST   /sync                    立即取数（可指定统计窗口）
    GET    /counts                  计数快照列表
    GET    /batches                 同步批次历史
    GET    /compare                 比对「申报值 vs 网盘实测值」

权限
----
写操作要求 **HR 或系统管理员**；读操作要求 **HR 或部门负责人**。

⚠️ **token 绝不出现在响应里** —— 授权状态只返回「是否已授权」和到期时间。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.baidu_pan_models import BaiduPanCount, BaiduPanSyncBatch
from ..domain.models import Employee
from ..services.access_control import build_access_context
from ..services.baidu_pan_client import BaiduPanAuthError, BaiduPanError
from ..services.baidu_pan_service import BaiduPanService, compare_with_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/baidu/pan", tags=["baidu-pan"])


# ─────────────────────────────────────────────────────────────────────────
# 请求模型
# ─────────────────────────────────────────────────────────────────────────


class AuthorizeRequest(BaseModel):
    """授权码提交。

    ⚠️ **code 只能用一次、10 分钟过期** —— 重复提交会失败，
    需要重新打开授权链接。
    """

    code: str = Field(min_length=1, max_length=512)


class SyncRequest(BaseModel):
    """手动触发取数。"""

    window_start: str = Field(default="", max_length=16)
    window_end: str = Field(default="", max_length=16)


class CompareRequest(BaseModel):
    """比对申报值与实测值。"""

    reported: float | None = None
    actual: float | None = None


# ─────────────────────────────────────────────────────────────────────────
# 权限
# ─────────────────────────────────────────────────────────────────────────


def _require_hr(db: Session, user: Employee) -> None:
    """要求 HR 或系统管理员。"""
    ctx = build_access_context(db, user)
    if "hr" in ctx.roles or "admin" in ctx.roles:
        return
    raise HTTPException(status_code=403, detail="仅HR与系统管理员可维护百度网盘校验")


def _require_viewer(db: Session, user: Employee) -> None:
    """可查看：HR / 管理员 / 部门负责人。"""
    ctx = build_access_context(db, user)
    if {"hr", "admin", "department_leader"} & set(ctx.roles):
        return
    raise HTTPException(status_code=403, detail="无权查看百度网盘校验数据")


# ─────────────────────────────────────────────────────────────────────────
# 序列化
# ─────────────────────────────────────────────────────────────────────────


def _serialize_count(row: BaiduPanCount) -> dict[str, Any]:
    return {
        "id": row.id,
        "sourceCode": row.source_code,
        "windowStart": row.window_start,
        "windowEnd": row.window_end,
        "total": row.total,
        "dailyCounts": row.daily_counts or {},
        "scannedFiles": row.scanned_files,
        "excludedFiles": row.excluded_files,
        "scannedPath": row.scanned_path,
        "asOf": row.as_of.isoformat() if row.as_of else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_batch(batch: BaiduPanSyncBatch) -> dict[str, Any]:
    return {
        "id": batch.id,
        "triggerType": batch.trigger_type,
        "status": batch.status,
        "scannedFiles": batch.scanned_files,
        "countedFiles": batch.counted_files,
        "excludedFiles": batch.excluded_files,
        "errorMessage": batch.error_message,
        "detail": batch.detail,
        "startedAt": batch.started_at.isoformat() if batch.started_at else None,
        "finishedAt": batch.finished_at.isoformat() if batch.finished_at else None,
    }


# ─────────────────────────────────────────────────────────────────────────
# 端点
# ─────────────────────────────────────────────────────────────────────────


@router.get("/status")
def get_status(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """授权状态 + 目录可读性探针。

    ⚠️ **只返回「是否已授权」，绝不返回 token 本身。**
    """
    _require_viewer(db, current_user)
    service = BaiduPanService(db)
    try:
        authorized = service.is_authorized()
        probe: dict[str, Any] | None = None
        if authorized:
            probe = service.probe_access()
        return {
            "enabled": settings.baidu_pan_enabled,
            "configured": bool(
                settings.baidu_pan_app_key and settings.baidu_pan_app_secret
            ),
            "authorized": authorized,
            "appName": settings.baidu_pan_app_name,
            "watchPath": service.watch_path(),
            "redirectUri": settings.baidu_pan_redirect_uri or "oob",
            "countExtensions": settings.baidu_pan_count_extensions,
            "excludeKeywords": settings.baidu_pan_exclude_keywords,
            "probe": probe,
        }
    finally:
        service.close()


@router.get("/authorize-url")
def get_authorize_url(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """生成授权链接。

    ⚠️ **管理员去这个链接勾选网盘目录** —— 勾了哪些，
    应用之后就能读哪些（会挂到 ``/apps/{应用名}/`` 下）。
    这是「自动拉取」的唯一入口。
    """
    _require_hr(db, current_user)
    if not settings.baidu_pan_app_key or not settings.baidu_pan_app_secret:
        raise HTTPException(
            status_code=503,
            detail="百度网盘凭证未配置（BAIDU_PAN_APP_KEY / BAIDU_PAN_APP_SECRET）",
        )
    service = BaiduPanService(db)
    try:
        return {
            "authorizeUrl": service.build_authorize_url(),
            "watchPath": service.watch_path(),
            "hint": (
                "打开链接后请在授权页面**勾选要校验的网盘目录**。"
                "未勾选的目录应用读不到。"
            ),
        }
    finally:
        service.close()


@router.post("/authorize")
def authorize(
    body: AuthorizeRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """提交授权码 → 换 token → 落库。

    ⚠️ **code 只能用一次** —— 失败需重新打开授权链接。
    """
    _require_hr(db, current_user)
    service = BaiduPanService(db)
    try:
        try:
            result = service.authorize(body.code)
        except BaiduPanAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except BaiduPanError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        db.commit()
        return result
    finally:
        service.close()


@router.post("/sync")
def trigger_sync(
    body: SyncRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """立即取数。

    ⚠️ 留空窗口 = **上个自然月** —— 当月还没过完，数据必然不完整，
    拿去核对会产生假异常。
    """
    _require_hr(db, current_user)
    service = BaiduPanService(db)
    try:
        batch = service.run_sync(
            window_start=body.window_start,
            window_end=body.window_end,
            trigger_type="manual",
        )
        db.commit()
        return _serialize_batch(batch)
    finally:
        service.close()


@router.get("/counts")
def list_counts(
    limit: int = 20,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """计数快照列表。"""
    _require_viewer(db, current_user)
    service = BaiduPanService(db)
    try:
        rows = service.list_snapshots(limit=max(1, min(limit, 100)))
        return {"code": 0, "data": [_serialize_count(row) for row in rows]}
    finally:
        service.close()


@router.get("/batches")
def list_batches(
    limit: int = 20,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """同步批次历史。"""
    _require_viewer(db, current_user)
    from sqlalchemy import select

    rows = list(
        db.scalars(
            select(BaiduPanSyncBatch)
            .order_by(BaiduPanSyncBatch.started_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
    )
    return {"code": 0, "data": [_serialize_batch(row) for row in rows]}


@router.post("/compare")
def compare(
    body: CompareRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """比对「员工申报值」与「网盘实测值」。

    ⚠️ **只提示差异，不判定对错，更不自动改分** ——
    差异可能是口径问题（返工片、跨月交付、命名规则），必须让人来判断。
    """
    _require_viewer(db, current_user)
    return {"code": 0, "data": compare_with_report(body.reported, body.actual)}


@router.get("/sync/status")
def get_sync_status(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """调度器运行态。"""
    _require_viewer(db, current_user)
    from ..services.baidu_pan_scheduler import get_scheduler_status

    return {"code": 0, "data": get_scheduler_status()}
