"""争议管理 API 路由。

对应前端 `src/api/dispute.ts` 的 `/dispute/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/dispute` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/dispute", tags=["dispute"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 争议仪表盘
#
# 响应结构：DisputeDashboard = { metrics, recentCases }
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def get_dispute_dashboard(db: Session = Depends(get_db)) -> dict:
    """获取争议管理仪表盘。

    响应结构：DisputeDashboard
    """
    return _ok({"metrics": [], "recentCases": []})
