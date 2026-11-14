"""AI 复核 API 路由。

对应前端 `src/api/aiReview.ts` 的 `/ai/reviews/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/ai/reviews` 前缀。

实现说明：
- 复核结果由 `services/ai_review_engine.py` 的**确定性规则**产出
  （``RULE_ONLY`` 模式；接入真实模型后叠加模型结论，规则保留作兜底）。
- 复核记录写入 `ai_review` 表（`services/ai_service.record_review`），
  支持「最近一次复核」查询与 ``ensure`` 幂等语义。
- ⚠️ **只记录，不写业务表**（决策⑤）：复核不会修改计划 / 成果的状态或分数。
- 权限复用既有 token 鉴权：员工只能复核自己的计划。

分页约定：前端这些端点直接消费单个对象或 Record，故返回对象而非分页结构。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import ai_review_engine, ai_service, planning

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
    # 尚未接入真实模型 → 仅规则复核。
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
    bizId: str = Query(..., description="业务对象 ID"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取指定业务对象的最近一次 AI 复核结果。

    响应结构：AiReview | null
    """
    review = _latest_review(db, bizType, bizId)
    if review is None:
        return _ok(None)
    return _ok(_serialize_review(review))


@router.get("/latest/batch")
async def get_latest_ai_reviews_batch(
    bizType: str = Query(..., description="业务类型"),
    bizIds: str = Query(..., description="业务对象 ID 列表，逗号分隔"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量获取多个业务对象的最近一次 AI 复核结果。

    响应结构：Record<string, AiReview>（前端按 bizId 索引取值）
    """
    ids: list[str] = []
    for chunk in (bizIds or "").split(","):
        chunk = chunk.strip()
        if chunk:
            ids.append(chunk)

    result: dict[str, Any] = {}
    for biz_id in ids:
        review = _latest_review(db, bizType, biz_id)
        if review is not None:
            result[str(biz_id)] = _serialize_review(review)
    return _ok(result)


@router.post("/plans/{biz_type}/{biz_id}/check")
async def check_plan_with_ai(
    biz_type: str,
    biz_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """触发计划 AI 复核。

    响应结构：AiReview
    """
    review = _review_plan(db, current_user, biz_type, biz_id, force=True)
    return _ok(_serialize_review(review))


@router.post("/plans/{biz_type}/{biz_id}/ensure")
async def ensure_plan_ai_review(
    biz_type: str,
    biz_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """确保计划存在有效 AI 复核（无则触发一次）。

    响应结构：AiReview
    """
    review = _review_plan(db, current_user, biz_type, biz_id, force=False)
    return _ok(_serialize_review(review))


@router.post("/results/preview")
async def preview_result_ai_review(
    bizType: str = Form(...),
    bizId: str = Form(...),
    file: Optional[UploadFile] = File(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """预览成果的 AI 复核。

    响应结构：AiReview
    """
    # 成果内容目前只能从上传文件读取摘要；文本成果由后续版本接入。
    description = ""
    if file is not None:
        raw = await file.read()
        try:
            description = raw.decode("utf-8", errors="ignore")
        except Exception:  # pragma: no cover - 解码失败按空处理
            description = ""

    issues, _ = ai_review_engine.review_result_content(
        description=description,
        attachments_count=1 if file is not None else 0,
    )
    result = ai_review_engine.build_review_result(issues)

    review = ai_service.record_review(
        db,
        biz_type=bizType,
        biz_id=str(bizId),
        result=result,
        status="RULE_ONLY",
        overall_risk=result["overallRisk"],
        provider="",
        model_name="",
        prompt_version="",
    )
    return _ok(_serialize_review(review))


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _latest_review(db: Session, biz_type: str, biz_id: str):
    """取某业务对象最近一次复核记录。"""
    reviews = ai_service.list_reviews(
        db, biz_type=biz_type, biz_id=str(biz_id), limit=1
    )
    return reviews[0] if reviews else None


def _review_plan(
    db: Session,
    user: Employee,
    biz_type: str,
    biz_id: str,
    *,
    force: bool,
):
    """复核计划内容并落库。

    Args:
        force: True → 总是重新复核；False → 已有有效结果则直接复用。
    """
    normalized_type = _normalize_plan_type(biz_type)
    if normalized_type is None:
        raise HTTPException(status_code=400, detail=f"不支持的业务类型：{biz_type}")

    if not force:
        existing = _latest_review(db, normalized_type, biz_id)
        if existing is not None:
            return existing

    items = _load_plan_items(db, user, normalized_type, biz_id)

    issues, _ = ai_review_engine.review_plan_content(items=items)
    result = ai_review_engine.build_review_result(issues)

    return ai_service.record_review(
        db,
        biz_type=normalized_type,
        biz_id=str(biz_id),
        result=result,
        status="RULE_ONLY",
        overall_risk=result["overallRisk"],
        provider="",
        model_name="",
        prompt_version="",
    )


def _normalize_plan_type(biz_type: str) -> str | None:
    """归一化业务类型；不支持则返回 None。"""
    key = str(biz_type).strip().upper()
    mapping = {
        "MONTH_PLAN": "MONTH_PLAN",
        "WEEK_PLAN": "WEEK_PLAN",
        "DAY_PLAN": "DAY_PLAN",
        "MONTH": "MONTH_PLAN",
        "WEEK": "WEEK_PLAN",
        "DAY": "DAY_PLAN",
    }
    return mapping.get(key)


def _load_plan_items(
    db: Session,
    user: Employee,
    biz_type: str,
    biz_id: str,
) -> list[dict[str, Any]]:
    """载入计划条目并转成规则引擎可用的字典列表。

    **权限**：只允许复核自己的计划（token 鉴权得出的当前用户）。
    """
    plan_id = str(biz_id)
    try:
        if biz_type == "MONTH_PLAN":
            plan = planning.get_month_plan(db, user.id, plan_id)
            rows = planning.list_month_plan_items(db, plan.id)
            return [
                {
                    "taskContent": row.task_content,
                    "deliverable": row.deliverable,
                    "quality": row.quality,
                    "performanceWeight": row.performance_weight,
                    "estimatedStartDate": _iso(row.estimated_start_date),
                    "estimatedEndDate": _iso(row.estimated_end_date),
                }
                for row in rows
            ]
        if biz_type == "WEEK_PLAN":
            plan = planning.get_week_plan(db, user.id, plan_id)
            rows = planning.list_week_plan_items(db, plan.id)
            return [
                {
                    "content": row.content,
                    "deliverable": row.deliverable or "",
                    "quality": row.quality or "",
                    "performanceWeight": 0,
                    "plannedFinishDate": _iso(row.planned_finish_date),
                }
                for row in rows
            ]
        if biz_type == "DAY_PLAN":
            plan = planning.get_day_plan(db, user.id, plan_id)
            return [
                {
                    "content": plan.content,
                    "deliverable": "",
                    "quality": "",
                    "performanceWeight": 0,
                }
            ]
    except planning.PlanNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc

    return []


def _serialize_review(review: Any) -> dict[str, Any]:
    """序列化为前端 ``AiReview`` 形状。

    ⚠️ 业务对象 ID 用字符串（后端主键口径），前端类型声明为 ``number``
    是历史遗留；在 JS 运行时字符串 ID 同样可用于索引与相等判断。
    """
    result = review.result_json or {}
    return {
        "id": review.id,
        "bizType": review.biz_type,
        "bizId": review.biz_id,
        "bizVersion": review.biz_version,
        "contentHash": review.content_hash,
        "status": review.status,
        "overallRisk": review.overall_risk,
        "provider": review.provider,
        "modelName": review.model_name,
        "promptVersion": review.prompt_version,
        "checkedAt": review.checked_at.isoformat() if review.checked_at else "",
        "stale": False,
        "modelEnabled": False,
        "errorMessage": review.error_message,
        "result": result,
    }


def _iso(value: Any) -> str:
    """date → ISO 字符串。"""
    return value.isoformat() if value else ""
