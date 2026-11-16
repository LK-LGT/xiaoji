"""AI 复核 API 路由。

对应前端 `src/api/aiReview.ts` 的 `/ai/reviews/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/ai/reviews` 前缀。

实现说明：
- 复核结果由 `services/ai_review_engine.py` 的**确定性规则**产出
  （``RULE_ONLY`` 模式；接入真实模型后叠加模型结论，规则保留作兜底）。
- 复核记录写入 `ai_review` 表（`services/ai_service.record_review`），
  支持「最近一次复核」查询与 ``ensure`` 幂等语义。
- ⚠️ **只记录，不写业务表**（决策⑤）：复核不会修改计划 / 成果的状态或分数。
- 权限复用既有 token 鉴权：计划**所有者**与对该员工有**数据可见权**的人
  （直属主管 / 部门负责人 / HR）都可复核（见 `_get_plan_for_review`）。

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
from ..services import access_control

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ai/reviews", tags=["ai-review"], dependencies=[Depends(get_current_user)])


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

    **权限**（BUG-R6-11 修复）：
    原实现只允许复核**自己的**计划（`planning.get_month_plan(db, user.id, ...)`
    内部校验 `owner_id == user.id`），导致**审批人**（主管 / 部门负责人）
    想给下属的计划跑 AI 检查时必然 404「月计划不存在」——
    而审批页的 `canApprove()` 又把「AI 检查正常」当作硬前置，
    于是审批链路彻底走不通。

    现在放行两类人：
    1. 计划**所有者**；
    2. 对该计划所有者有**数据可见权**的人
       （`access_control.can_access_employee`：直属下属 / 所管部门成员 / HR）。
    """
    plan_id = str(biz_id)
    try:
        if biz_type == "MONTH_PLAN":
            plan = _get_plan_for_review(db, user, biz_type, plan_id)
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
            plan = _get_plan_for_review(db, user, biz_type, plan_id)
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
            plan = _get_plan_for_review(db, user, biz_type, plan_id)
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


def _get_plan_for_review(
    db: Session,
    user: Employee,
    biz_type: str,
    plan_id: str,
) -> Any:
    """取计划并校验当前用户是否有权复核。

    先按 owner 取（本人路径）；取不到时按「审批人 / 可见该员工的范围」再试一次。
    """
    try:
        return _getter_for_biz_type(biz_type)(db, user.id, plan_id)
    except planning.PlanNotFound:
        pass

    # 审批人路径：按计划主键直接取，再校验可见性。
    from ..domain.models import DayPlan, MonthPlan, WeekPlan

    model = {
        "MONTH_PLAN": MonthPlan,
        "WEEK_PLAN": WeekPlan,
        "DAY_PLAN": DayPlan,
    }.get(biz_type)
    if model is None:
        raise planning.PlanNotFound("计划不存在")

    candidate = db.get(model, plan_id)
    if candidate is None:
        raise planning.PlanNotFound("计划不存在")

    owner = db.get(Employee, candidate.owner_id)
    if owner is None:
        raise planning.PlanNotFound("计划不存在")

    # ⚠️ `can_access_employee(context, target_employee, db)` — 第一个参数是
    #    **AccessContext**（不是 db！）。直接传 db 会让内部 `context.employee_id`
    #    触发 `AttributeError: 'Session' object has no attribute 'employee_id'`。
    context = access_control.build_access_context(db, user)
    if not access_control.can_access_employee(context, owner, db):
        # ⚠️ 沿用本模块既有的「不泄露存在性」约定：越权访问一律回 **404**
        #    「计划不存在」，而不是 403「无权复核该计划」。
        #    403 会明确告诉调用方「这个 plan_id 真实存在」，
        #    与 `planning.get_*_plan` 的 owner 校验失败路径（404）不一致。
        raise HTTPException(status_code=404, detail="计划不存在")

    return candidate


def _getter_for_biz_type(biz_type: str):
    """按业务类型取对应的 `get_*_plan` 读取函数。"""
    return {
        "MONTH_PLAN": planning.get_month_plan,
        "WEEK_PLAN": planning.get_week_plan,
        "DAY_PLAN": planning.get_day_plan,
    }[biz_type]


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
