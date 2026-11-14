"""AI 复核 API 路由。

对应前端 `src/api/aiReview.ts` 的 `/ai/reviews/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/ai/reviews` 前缀。

分页约定：前端这些端点直接消费单个对象或 Record，故返回对象而非分页结构。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from ..core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ai/reviews", tags=["ai-review"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 能力探测
#
# 响应结构：AiCapability
# ---------------------------------------------------------------------------


@router.get("/capability")
async def get_ai_capability(db: Session = Depends(get_db)) -> dict:
    """获取 AI 复核能力与当前模式。

    响应结构：AiCapability
    """
    return _ok(
        {
            "modelEnabled": False,
            "mode": "RULE_ONLY",
            "provider": "",
            "modelName": "",
            "promptVersion": "",
            "message": "尚未配置可用的模型，当前仅执行规则复核",
        }
    )


# ---------------------------------------------------------------------------
# 复核
#
# 响应结构：AiReview / AiReview | null
# ---------------------------------------------------------------------------


@router.get("/latest")
async def get_latest_ai_review(
    bizType: str = Query(..., description="业务类型：MONTH_PLAN / WEEK_PLAN / DAY_PLAN / RESULT"),
    bizId: int = Query(..., description="业务对象 ID"),
    db: Session = Depends(get_db),
) -> dict:
    """获取指定业务对象的最近一次 AI 复核结果。

    响应结构：AiReview | null
    """
    return _ok(None)


@router.post("/plans/{biz_type}/{biz_id}/check")
async def check_plan_with_ai(
    biz_type: str,
    biz_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """触发计划 AI 复核。

    响应结构：AiReview
    """
    return _ok(_empty_ai_review(biz_type, biz_id))


@router.post("/plans/{biz_type}/{biz_id}/ensure")
async def ensure_plan_ai_review(
    biz_type: str,
    biz_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """确保计划存在有效 AI 复核（无则触发一次）。

    响应结构：AiReview
    """
    return _ok(_empty_ai_review(biz_type, biz_id))


@router.post("/results/preview")
async def preview_result_ai_review(
    bizType: str = Form(...),
    bizId: int = Form(...),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
) -> dict:
    """预览成果的 AI 复核。

    响应结构：AiReview
    """
    return _ok(_empty_ai_review(bizType, bizId))


def _empty_ai_review(biz_type: str, biz_id: int) -> dict:
    """构造 AiReview，字段与 frontend/src/api/aiReview.ts 保持一致。"""
    return {
        "id": 0,
        "bizType": biz_type,
        "bizId": biz_id,
        "bizVersion": None,
        "contentHash": "",
        "status": "RULE_ONLY",
        "overallRisk": "LOW",
        "provider": "",
        "modelName": "",
        "promptVersion": "",
        "checkedAt": "",
        "stale": False,
        "modelEnabled": False,
        "errorMessage": None,
        "result": {
            "overallRisk": "LOW",
            "summary": "",
            "issues": [],
            "analysisDimensions": [],
            "acceptanceCoverage": [],
            "suggestedCompletionMin": None,
            "suggestedCompletionMax": None,
            "evidenceStatus": "UNKNOWN",
            "declaredCompletionRate": None,
            "completionCalculationBasis": None,
        },
    }
