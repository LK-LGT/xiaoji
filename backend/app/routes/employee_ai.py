"""员工侧 AI 助手 API 路由。

对应前端 `src/api/employeeAi.ts` 的 `/employee/ai/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee/ai` 前缀。

注意：当前 `MODEL_MODE=echo`，模型调用走确定性假模型（`agent-service` 的
`EchoModelClient`），输出可复现。真实模型接入前，所有端点返回结构完整的空结果，
保证前端不因缺字段而崩溃。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee/ai", tags=["employee-ai"])


# ---------------------------------------------------------------------------
# 请求模型
#
# 注意：必须用显式 Pydantic 模型，不能用 `payload: dict`。
# 本项目所用 FastAPI 版本不会把裸 dict 注解自动解析为 JSON body，
# 请求体会以字符串形式传入并触发 422（dict_type 校验失败）。
#
# AI 请求体字段随场景演进较快，这里用宽松模型（extra=allow）承接，
# 避免前端新增字段就导致 422。
# ---------------------------------------------------------------------------


class AiOperationPayload(BaseModel):
    """AI 生成/调整/检查请求体（宽松承接）。"""

    model_config = {"extra": "allow"}

    requestId: str = ""
    planMonth: str = ""
    weekStart: str = ""
    planDate: str = ""
    intentText: str = ""
    instruction: str = ""
    conversationId: Optional[int] = None
    conciseOutput: bool = False
    exampleFileName: str = ""
    exampleContent: str = ""


class AiSuggestionActionPayload(BaseModel):
    """AI 建议采纳动作请求体。"""

    model_config = {"extra": "allow"}

    actionCode: str = ""
    appliedFields: list[str] = []
    beforeHash: str = ""
    afterHash: str = ""


class AiConversationPayload(BaseModel):
    """AI 会话新建请求体。"""

    model_config = {"extra": "allow"}

    sceneCode: str = ""
    bizType: str = ""
    bizId: Optional[int] = None
    title: str = ""


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 上下文
#
# 响应结构：AiContextResponse
# ---------------------------------------------------------------------------


@router.get("/month-plans/context")
async def get_month_plan_ai_context(
    planMonth: str,
    intentText: Optional[str] = Query(None),
    selectedKnowledgeTypes: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取月计划 AI 上下文。

    响应结构：AiContextResponse
    """
    return _ok(_empty_context_response())


@router.get("/week-plans/context")
async def get_week_plan_ai_context(
    weekStart: str,
    db: Session = Depends(get_db),
) -> dict:
    """获取周计划 AI 上下文。

    响应结构：AiContextResponse
    """
    return _ok(_empty_context_response())


@router.get("/day-plans/context")
async def get_day_plan_ai_context(
    planDate: str,
    db: Session = Depends(get_db),
) -> dict:
    """获取日计划 AI 上下文。

    响应结构：AiContextResponse
    """
    return _ok(_empty_context_response())


# ---------------------------------------------------------------------------
# 生成 / 调整
#
# 响应结构：AiGenerateResponse / WeekPlanAiDraft / DayPlanAiDraft
# ---------------------------------------------------------------------------


@router.post("/month-plans/generate")
async def generate_month_plan_ai(
    payload: AiOperationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """生成月计划建议。

    响应结构：AiGenerateResponse
    """
    return _ok(_empty_generate_response())


@router.post("/month-plans/check")
async def check_month_plan_ai(
    payload: AiOperationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """检查月计划内容。

    响应结构：AiCheckResponse
    """
    return _ok(
        {
            "suggestionId": "",
            "issues": [],
            "notice": "",
        }
    )


@router.post("/month-plans/items/optimize")
async def optimize_month_plan_item_ai(
    payload: AiOperationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """优化单条月计划任务。

    响应结构：AiOptimizeResponse
    """
    return _ok(
        {
            "suggestionId": "",
            "item": _empty_plan_item(),
            "warnings": [],
            "notice": "",
            "draftVersionId": 0,
            "draftVersionNo": 0,
        }
    )


@router.post("/week-plans/generate")
async def generate_week_plan_ai(
    payload: AiOperationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """生成周计划建议。

    响应结构：WeekPlanAiDraft
    """
    return _ok(_empty_week_plan_draft())


@router.post("/week-plans/adjust")
async def adjust_week_plan_ai(
    payload: AiOperationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """调整周计划建议。

    响应结构：WeekPlanAiDraft
    """
    return _ok(_empty_week_plan_draft())


@router.post("/day-plans/generate")
async def generate_day_plan_ai(
    payload: AiOperationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """生成日计划建议。

    响应结构：DayPlanAiDraft
    """
    return _ok(_empty_day_plan_draft())


@router.post("/day-plans/adjust")
async def adjust_day_plan_ai(
    payload: AiOperationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """调整日计划建议。

    响应结构：DayPlanAiDraft
    """
    return _ok(_empty_day_plan_draft())


# ---------------------------------------------------------------------------
# 示例提取
#
# 响应结构：AiGenerationExample
# ---------------------------------------------------------------------------


@router.post("/plan-examples/extract")
async def extract_plan_example(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """从上传文件中提取计划示例。

    响应结构：AiGenerationExample
    """
    return _ok(
        {
            "fileName": file.filename or "",
            "content": "",
            "truncated": False,
        }
    )


# ---------------------------------------------------------------------------
# 建议动作
#
# 响应结构：void
# ---------------------------------------------------------------------------


@router.post("/suggestions/{suggestion_id}/actions")
async def record_ai_suggestion_action(
    suggestion_id: str,
    payload: AiSuggestionActionPayload,
    db: Session = Depends(get_db),
) -> dict:
    """记录 AI 建议采纳动作。

    响应结构：void
    """
    return _ok(None)


# ---------------------------------------------------------------------------
# 会话
#
# 响应结构：AiConversation / AiConversationMessage
# ---------------------------------------------------------------------------


@router.get("/conversations/active")
async def get_active_ai_conversation(
    sceneCode: Optional[str] = Query(None),
    bizId: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取当前活跃的 AI 会话。

    响应结构：AiConversation | null
    """
    return _ok(None)


@router.get("/conversations/history")
async def list_ai_conversation_history(
    sceneCode: Optional[str] = Query(None),
    bizId: Optional[int] = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """获取历史 AI 会话列表。

    响应结构：AiConversation[]（裸数组）
    """
    return _ok([])


@router.get("/conversations/{conversation_id}/messages")
async def list_ai_conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取 AI 会话消息列表。

    响应结构：AiConversationMessage[]（裸数组）
    """
    return _ok([])


@router.post("/conversations/new")
async def create_ai_conversation(
    payload: AiConversationPayload,
    db: Session = Depends(get_db),
) -> dict:
    """新建 AI 会话。

    响应结构：AiConversation
    """
    return _ok(_empty_conversation())


def _empty_context_response() -> dict:
    """构造 AiContextResponse，字段与 frontend/src/api/employeeAi.ts 保持一致。"""
    return {
        "enabled": False,
        "providerCode": "",
        "modelName": "",
        "generationProfile": "EMPLOYEE_EXECUTION",
        "generationProfileLabel": "",
        "availableContext": [],
        "missingContext": [],
        "historyPlanCount": 0,
        "contextOptions": [],
        "knowledgeReferences": [],
        "historyMonthOptions": [],
        "knowledgeTypeOptions": [],
        "parentOptions": [],
        "relatedWeekItems": [],
        "remainingCalls": {},
        "notice": "",
    }


def _empty_plan_item() -> dict:
    """构造 AiPlanItem，字段与 frontend/src/api/employeeAi.ts 保持一致。"""
    return {
        "workType": "TASK",
        "taskName": "",
        "taskContent": "",
        "deliverable": "",
        "quality": "",
        "estimatedStartDate": "",
        "estimatedEndDate": "",
        "performanceWeight": 0,
        "estimatedValue": 0,
    }


def _empty_generate_response() -> dict:
    """构造 AiGenerateResponse，字段与 frontend/src/api/employeeAi.ts 保持一致。"""
    return {
        "suggestionId": "",
        "items": [],
        "warnings": [],
        "missingContext": [],
        "notice": "",
        "draftVersionId": 0,
        "draftVersionNo": 0,
        "effectiveOperationMode": "GENERATE",
        "clarificationMessage": None,
        "generationProfile": "EMPLOYEE_EXECUTION",
        "generationProfileLabel": "",
        "knowledgeReferences": [],
    }


def _empty_week_plan_draft() -> dict:
    """构造 WeekPlanAiDraft，字段与 frontend/src/api/employeeAi.ts 保持一致。"""
    return {
        "suggestionId": "",
        "items": [],
        "warnings": [],
        "missingContext": [],
        "notice": "",
        "draftVersionId": 0,
        "draftVersionNo": 0,
    }


def _empty_day_plan_draft() -> dict:
    """构造 DayPlanAiDraft，字段与 frontend/src/api/employeeAi.ts 保持一致。"""
    return {
        "suggestionId": "",
        "relatedWeekPlanItemIds": [],
        "content": "",
        "remark": "",
        "warnings": [],
        "missingContext": [],
        "notice": "",
        "draftVersionId": 0,
        "draftVersionNo": 0,
    }


def _empty_conversation() -> dict:
    """构造 AiConversation，字段与 frontend/src/api/employeeAi.ts 保持一致。"""
    now = datetime.utcnow().isoformat()
    return {
        "id": 0,
        "sceneCode": "",
        "bizType": "",
        "bizId": None,
        "title": "",
        "createdAt": now,
        "updatedAt": now,
    }
