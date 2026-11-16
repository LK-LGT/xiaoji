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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import ai_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee/ai", tags=["employee-ai"], dependencies=[Depends(get_current_user)])


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
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """记录 AI 建议采纳动作。

    响应结构：void
    """
    ai_service.record_suggestion_action(
        db,
        suggestion_id=suggestion_id,
        actor=current_user,
        action_code=payload.actionCode,
        applied_fields=payload.appliedFields,
        before_hash=payload.beforeHash,
        after_hash=payload.afterHash,
    )
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
    planType: Optional[str] = Query(None),
    periodKey: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取当前活跃的 AI 会话。

    响应结构：AiConversation | null
    """
    scene = _scene_code(sceneCode, planType, periodKey)
    conversation = ai_service.find_active_conversation(
        db,
        owner_id=current_user.id,
        scene_code=scene,
        biz_id=str(bizId) if bizId is not None else None,
    )
    if conversation is None:
        return _ok(None)
    return _ok(_serialize_conversation(conversation))


@router.get("/conversations/history")
async def list_ai_conversation_history(
    sceneCode: Optional[str] = Query(None),
    bizId: Optional[int] = Query(None),
    planType: Optional[str] = Query(None),
    periodKey: Optional[str] = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取历史 AI 会话列表。

    响应结构：AiConversation[]（裸数组）
    """
    scene = _scene_code(sceneCode, planType, periodKey)
    conversations = ai_service.list_conversations(
        db,
        owner_id=current_user.id,
        scene_code=scene,
        biz_id=str(bizId) if bizId is not None else None,
        limit=pageSize,
        offset=(pageNo - 1) * pageSize,
    )
    return _ok([_serialize_conversation(item) for item in conversations])


@router.get("/conversations/{conversation_id}/messages")
async def list_ai_conversation_messages(
    conversation_id: str,
    beforeId: Optional[str] = Query(None),
    size: int = Query(30, ge=1, le=200),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取 AI 会话消息列表。

    响应结构：AiConversationMessagePage = { records, nextBeforeId, hasMore }
    """
    conversation = ai_service.get_conversation(db, conversation_id)
    _assert_owns_conversation(current_user, conversation)

    # beforeId 游标：取比该 ID 更早的消息
    messages = ai_service.list_messages(db, conversation.id, limit=size + 1)
    if beforeId:
        messages = [m for m in messages if m.id < beforeId]

    has_more = len(messages) > size
    page = messages[:size]

    next_before = page[0].id if page else None
    return _ok(
        {
            "records": [_serialize_message(item) for item in page],
            "nextBeforeId": next_before,
            "hasMore": has_more,
        }
    )


@router.post("/conversations/new")
async def create_ai_conversation(
    payload: AiConversationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """新建 AI 会话。

    响应结构：AiConversation
    """
    scene = payload.sceneCode or _scene_code(None, payload.bizType, None)
    conversation = ai_service.create_conversation(
        db,
        owner=current_user,
        scene_code=scene,
        title=payload.title,
        biz_type=payload.bizType,
        biz_id=str(payload.bizId) if payload.bizId is not None else None,
    )
    return _ok(_serialize_conversation(conversation))


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


# ---------------------------------------------------------------------------
# 序列化与权限辅助
# ---------------------------------------------------------------------------


def _serialize_conversation(conversation: Any) -> dict:
    """序列化为前端 ``AiConversation`` 形状。

    ⚠️ 会话 ID 用 UUID 字符串（后端主键口径），前端类型声明为 ``number``
    是历史遗留；在 JS 运行时字符串 ID 同样可用于 URL 拼接与相等判断。
    """
    return {
        "id": conversation.id,
        "sceneCode": conversation.scene_code,
        "bizType": conversation.biz_type,
        "bizId": _as_id(conversation.biz_id) if conversation.biz_id else None,
        "title": conversation.title,
        "status": "ACTIVE" if conversation.status == "active" else "ENDED",
        "createdAt": conversation.created_at.isoformat() if conversation.created_at else "",
        "updatedAt": conversation.updated_at.isoformat() if conversation.updated_at else "",
        "endedAt": "",
    }


def _serialize_message(message: Any) -> dict:
    """序列化为前端 ``AiConversationMessage`` 形状。"""
    role = str(message.role or "").lower()
    return {
        "id": message.id,
        "conversationId": message.conversation_id,
        "role": role.upper() if role in ("user", "assistant", "system") else "EVENT",
        "messageType": "TEXT",
        "operationCode": "",
        "status": "SUCCEEDED",
        "textContent": message.content,
        "payload": message.payload_json or {},
        "suggestionId": message.suggestion_id,
        "requestId": message.request_id,
        "errorMessage": None,
        "createdAt": message.created_at.isoformat() if message.created_at else "",
    }


def _scene_code(
    scene_code: Optional[str],
    plan_type: Optional[str],
    period_key: Optional[str],
) -> str:
    """归一化场景标识。

    优先级：显式 ``sceneCode`` > ``planType`` + ``periodKey`` 组合 > 默认场景。
    """
    if scene_code:
        return str(scene_code).strip()
    if plan_type:
        kind = str(plan_type).strip().upper()
        if period_key:
            return f"{kind}:{period_key}"
        return kind
    return "DEFAULT"


def _assert_owns_conversation(user: Employee, conversation: Any) -> None:
    """校验当前用户是否拥有该会话（token 鉴权 → 不信任 URL 里的 owner）。"""
    if conversation.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该 AI 会话")


def _as_id(value: Any) -> str:
    """主键 ID 原样透出（不要转 int）。

    ⚠️ BUG-R6-14 同类修复：业务主键是**十六进制 / UUID 字符串**，
    原实现 ``int(str(value))`` 失败后回退成 ``0``，
    会话绑定的业务对象 ID 就丢了。
    """
    if value is None:
        return ""
    return str(value)
