"""飞书集成 API 路由。

包括：
- 手动触发组织架构同步
- 接收飞书回调（可选，后续实现）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.config import settings
from app.services.feishu_client import FeishuClient
from app.services.organization_sync import OrganizationSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feishu", tags=["feishu"])


@router.post("/sync-organization")
def sync_organization(db: Session = Depends(get_db)):
    """手动触发组织架构同步。

    仅供管理员使用（后续需添加权限验证）。

    Returns:
        同步统计信息
    """
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        return {
            "code": 400,
            "message": "Feishu credentials not configured",
        }

    try:
        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
        service = OrganizationSyncService(db, client)
        stats = service.sync_all()
        client.close()

        return {
            "code": 0,
            "message": "Organization sync completed",
            "data": stats,
        }
    except Exception as e:
        logger.exception("Organization sync failed")
        return {
            "code": 500,
            "message": str(e),
        }
