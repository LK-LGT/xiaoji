"""计划统计 API 路由。

对应前端 `src/api/planning.ts` 的 `/planning/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/planning` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/planning", tags=["planning"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 计划统计
#
# 响应结构：PlanningStats
# ---------------------------------------------------------------------------


@router.get("/stats")
async def get_planning_stats(db: Session = Depends(get_db)) -> dict:
    """获取计划执行统计概览。

    响应结构：PlanningStats
    """
    return _ok(
        {
            "pendingDayPlans": 0,
            "overduePendingDayPlans": 0,
            "currentMonthResults": 0,
            "closureRate": "0%",
        }
    )
