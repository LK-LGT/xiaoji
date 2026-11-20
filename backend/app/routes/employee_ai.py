"""员工侧 AI 助手 API 路由。

对应前端 `src/api/employeeAi.ts` 的 `/employee/ai/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee/ai` 前缀。

## Step 6 计划助手工具化

计划助手的 8 个端点（月/周/日 上下文 + 生成 + 调整 + 检查 + 示例提取）
已从「结构完整的空结果」升级为**真实实现**：

```
前端 → 本路由 → plan_ai_service → agent-service /v1/agent/plan
                                             ↓ 模型自主调工具
                                      backend /v1/plan-tools/*
```

⚠️ **默认关闭**（``PLAN_AI_ENABLED=false``）：
关闭时端点仍返回**结构完整的空结果**，前端不崩、不白屏。
理由：计划会变成**考核指标**，真模型输出必须由人工确认后才落库。

⚠️ 其余端点（会话、建议动作、取消等）行为不变。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import ai_service, plan_ai_service, plan_tool_service

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
    conversationId: int | None = None
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
    bizId: int | None = None
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
    intentText: str | None = Query(None),
    selectedKnowledgeTypes: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取月计划 AI 上下文。

    响应结构：AiContextResponse
    """
    return _ok(await _build_context(
        db, current_user=current_user, plan_type="month", period=planMonth,
        intent_text=intentText or "",
    ))


@router.get("/week-plans/context")
async def get_week_plan_ai_context(
    weekStart: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周计划 AI 上下文。

    响应结构：AiContextResponse
    """
    return _ok(await _build_context(
        db, current_user=current_user, plan_type="week", period=weekStart,
    ))


@router.get("/day-plans/context")
async def get_day_plan_ai_context(
    planDate: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取日计划 AI 上下文。

    响应结构：AiContextResponse
    """
    return _ok(await _build_context(
        db, current_user=current_user, plan_type="day", period=planDate,
    ))


# ---------------------------------------------------------------------------
# 生成 / 调整
#
# 响应结构：AiGenerateResponse / WeekPlanAiDraft / DayPlanAiDraft
# ---------------------------------------------------------------------------


@router.post("/month-plans/generate")
async def generate_month_plan_ai(
    payload: AiOperationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """生成月计划建议。

    响应结构：AiGenerateResponse
    """
    if not plan_ai_service.is_enabled():
        return _ok(_empty_generate_response())

    result = await plan_ai_service.call_plan_agent(
        db,
        current_user=current_user,
        mode="generate",
        intent_text=payload.intentText,
        plan_type="month",
        period=payload.planMonth,
        current_form=_extract_current_form(payload),
    )
    return _ok({
        "suggestionId": _new_suggestion_id(),
        "items": plan_ai_service.shape_items(result["items"]),
        "warnings": result["warnings"],
        "missingContext": result["missingContext"],
        "notice": _build_notice(result),
        "draftVersionId": 0,
        "draftVersionNo": 0,
        "effectiveOperationMode": "GENERATE",
        "generationProfile": "EMPLOYEE_EXECUTION",
        "generationProfileLabel": "员工执行",
    })


@router.post("/month-plans/check")
async def check_month_plan_ai(
    payload: AiOperationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """检查月计划内容。

    响应结构：AiCheckResponse

    ⚠️ **只提示不阻断** —— 判重等问题只是建议，员工仍可提交。
    """
    if not plan_ai_service.is_enabled():
        return _ok({"suggestionId": "", "issues": [], "notice": ""})

    result = await plan_ai_service.call_plan_agent(
        db,
        current_user=current_user,
        mode="check",
        intent_text=payload.intentText,
        plan_type="month",
        period=payload.planMonth,
        current_form=_extract_current_form(payload),
    )
    return _ok({
        "suggestionId": _new_suggestion_id(),
        "issues": _findings_to_issues(plan_ai_service.shape_findings(result["findings"])),
        "notice": _build_notice(result),
    })


@router.post("/month-plans/items/optimize")
async def optimize_month_plan_item_ai(
    payload: AiOperationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """优化单条月计划任务。

    响应结构：AiOptimizeResponse
    """
    if not plan_ai_service.is_enabled():
        return _ok({
            "suggestionId": "",
            "item": _empty_plan_item(),
            "warnings": [],
            "notice": "",
            "draftVersionId": 0,
            "draftVersionNo": 0,
        })

    form = _extract_current_form(payload)
    # ⚠️ 优化单条 = 让模型「调整」这**一条**。
    #    用 adjust 模式 + 把当前表单作为草稿传进去，
    #    模型只会改这一条 —— 而不是重新生成整个计划。
    result = await plan_ai_service.call_plan_agent(
        db,
        current_user=current_user,
        mode="adjust",
        intent_text=payload.intentText or "优化这条计划任务，使其更具体可执行",
        instruction=payload.instruction or "优化这条计划任务，使其更具体可执行",
        plan_type="month",
        period=payload.planMonth,
        current_form=form,
    )
    items = plan_ai_service.shape_items(result["items"])
    return _ok({
        "suggestionId": _new_suggestion_id(),
        "item": items[0] if items else _empty_plan_item(),
        "warnings": result["warnings"],
        "notice": _build_notice(result),
        "draftVersionId": 0,
        "draftVersionNo": 0,
    })


@router.post("/week-plans/generate")
async def generate_week_plan_ai(
    payload: AiOperationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """生成周计划建议。

    响应结构：WeekPlanAiDraft
    """
    if not plan_ai_service.is_enabled():
        return _ok(_empty_week_plan_draft())

    result = await plan_ai_service.call_plan_agent(
        db,
        current_user=current_user,
        mode="generate",
        intent_text=payload.intentText,
        plan_type="week",
        period=payload.weekStart,
        current_form=_extract_current_form(payload),
    )
    return _ok({
        "suggestionId": _new_suggestion_id(),
        "items": _to_week_items(plan_ai_service.shape_items(result["items"])),
        "warnings": result["warnings"],
        "missingContext": result["missingContext"],
        "notice": _build_notice(result),
        "draftVersionId": 0,
        "draftVersionNo": 0,
    })


@router.post("/week-plans/adjust")
async def adjust_week_plan_ai(
    payload: AiOperationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """调整周计划建议。

    响应结构：WeekPlanAiDraft
    """
    if not plan_ai_service.is_enabled():
        return _ok(_empty_week_plan_draft())

    result = await plan_ai_service.call_plan_agent(
        db,
        current_user=current_user,
        mode="adjust",
        intent_text=payload.intentText,
        instruction=payload.instruction,
        plan_type="week",
        period=payload.weekStart,
        current_form=_extract_current_form(payload),
    )
    return _ok({
        "suggestionId": _new_suggestion_id(),
        "items": _to_week_items(plan_ai_service.shape_items(result["items"])),
        "warnings": result["warnings"],
        "missingContext": result["missingContext"],
        "notice": _build_notice(result),
        "draftVersionId": 0,
        "draftVersionNo": 0,
    })


@router.post("/day-plans/generate")
async def generate_day_plan_ai(
    payload: AiOperationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """生成日计划建议。

    响应结构：DayPlanAiDraft
    """
    if not plan_ai_service.is_enabled():
        return _ok(_empty_day_plan_draft())

    result = await plan_ai_service.call_plan_agent(
        db,
        current_user=current_user,
        mode="generate",
        intent_text=payload.intentText,
        plan_type="day",
        period=payload.planDate,
        current_form=_extract_current_form(payload),
    )
    return _ok({
        "suggestionId": _new_suggestion_id(),
        "relatedWeekPlanItemIds": [],
        "content": _items_to_day_content(result["items"]),
        "remark": "",
        "warnings": result["warnings"],
        "missingContext": result["missingContext"],
        "notice": _build_notice(result),
        "draftVersionId": 0,
        "draftVersionNo": 0,
    })


@router.post("/day-plans/adjust")
async def adjust_day_plan_ai(
    payload: AiOperationPayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """调整日计划建议。

    响应结构：DayPlanAiDraft
    """
    if not plan_ai_service.is_enabled():
        return _ok(_empty_day_plan_draft())

    result = await plan_ai_service.call_plan_agent(
        db,
        current_user=current_user,
        mode="adjust",
        intent_text=payload.intentText,
        instruction=payload.instruction,
        plan_type="day",
        period=payload.planDate,
        current_form=_extract_current_form(payload),
    )
    return _ok({
        "suggestionId": _new_suggestion_id(),
        "relatedWeekPlanItemIds": [],
        "content": _items_to_day_content(result["items"]),
        "remark": "",
        "warnings": result["warnings"],
        "missingContext": result["missingContext"],
        "notice": _build_notice(result),
        "draftVersionId": 0,
        "draftVersionNo": 0,
    })


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

    ⚠️ **只做文本提取，不调模型**：
    这一步的价值是「让 AI 学用户的写法风格」，
    原文必须**原样**传给模型（前端会带上 exampleContent）。
    如果这里做「AI 总结」，就把用户的原话改写成 AI 的话，
    学风格的效果全丢了 —— 而且多一次模型调用、多一个失败点。

    ⚠️ 因此这里只做：解码 + 截断 + 类型判断。
    """
    file_name = file.filename or ""
    raw = await file.read()
    # ⚠️ 大小上限 —— 防止有人上传 500MB 把服务拖垮
    if len(raw) > _EXAMPLE_MAX_BYTES:
        return _ok({
            "fileName": file_name,
            "content": "",
            "truncated": True,
        })

    content = _decode_example(raw, file_name)
    truncated = False
    if len(content) > _EXAMPLE_MAX_CHARS:
        content = content[:_EXAMPLE_MAX_CHARS]
        truncated = True
    return _ok({
        "fileName": file_name,
        "content": content,
        "truncated": truncated,
    })


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
    sceneCode: str | None = Query(None),
    bizId: int | None = Query(None),
    planType: str | None = Query(None),
    periodKey: str | None = Query(None),
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
    sceneCode: str | None = Query(None),
    bizId: int | None = Query(None),
    planType: str | None = Query(None),
    periodKey: str | None = Query(None),
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
    beforeId: str | None = Query(None),
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


# ---------------------------------------------------------------------------
# 计划助手辅助
#
# ⚠️ 这一段是 Step 6 新增的。所有函数都是**纯转换**或**纯查询**，
#    没有副作用 —— 便于测试。
# ---------------------------------------------------------------------------

#: 示例文件大小上限（字节）—— 防止超大文件拖垮服务
_EXAMPLE_MAX_BYTES = 5 * 1024 * 1024
#: 示例文本长度上限（字符）
_EXAMPLE_MAX_CHARS = 20000


def _new_suggestion_id() -> str:
    """生成建议 ID —— 前端用它关联「这次 AI 建议」和「采纳动作」。"""
    return uuid.uuid4().hex


def _decode_example(raw: bytes, file_name: str) -> str:
    """解码上传文件为文本。

    ⚠️ 编码探测顺序：UTF-8 → GBK（国内 Excel 导出常见）→ 兜底 latin-1。
       GBK 必须放前面 —— 中文计划示例用 GBK 存的比例很高，
       只试 UTF-8 会把文件解成乱码，用户以为功能坏了。
    """
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _extract_current_form(payload: AiOperationPayload) -> dict[str, Any]:
    """从请求体里取出当前表单（草稿）。

    ⚠️ 前端字段名可能随版本演进（``currentForm`` / ``draft`` / ``current_form``），
       所以这里**都认** —— 找不到就返回空字典，不报错。
    """
    data = payload.model_dump()
    for key in ("currentForm", "draft", "current_form", "form"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _to_week_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把月计划条目转成周计划条目形状。

    ⚠️ 周计划条目字段不同（``content`` 而不是 ``taskContent``）。
       所以做一次**字段重映射**，而不是直接把月计划条目塞过去。
    """
    result: list[dict[str, Any]] = []
    for item in items:
        content = (item.get("taskContent") or item.get("content") or "").strip()
        result.append({
            "id": None,
            "monthPlanItemId": None,
            "content": content,
            "deliverable": item.get("deliverable") or "",
            "quality": item.get("quality") or "",
            "plannedFinishDate": item.get("estimatedEndDate") or "",
        })
    return result


def _items_to_day_content(items: list[dict[str, Any]]) -> str:
    """把条目列表拼成日计划的 ``content`` 文本。

    ⚠️ 日计划是**一段文本**，不是条目数组 —— 所以用换行拼接。
    """
    lines: list[str] = []
    for item in items:
        name = (item.get("taskName") or "").strip()
        content = (item.get("taskContent") or "").strip()
        if name and content:
            lines.append(f"{name}：{content}")
        elif name or content:
            lines.append(name or content)
    return "\n".join(lines)


def _findings_to_issues(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把体检结论转成前端 ``AiCheckIssue`` 形状。

    ⚠️ ``severity`` → ``level`` 的映射：
       ``info`` → INFO，``warning`` → WARNING。
       最高只到 WARNING —— 体检**不阻断**，不用 HIGH 吓人。
    """
    issues: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        severity = str(finding.get("severity") or "info")
        level = "WARNING" if severity == "warning" else "INFO"
        target = finding.get("targetIndex")
        field_path = f"items[{target}]" if isinstance(target, int) and target >= 0 else f"plan[{index}]"
        issues.append({
            "code": str(finding.get("kind") or "other"),
            "level": level,
            "fieldPath": field_path,
            "message": str(finding.get("message") or ""),
            "suggestion": str(finding.get("suggestion") or ""),
        })
    return issues


def _build_notice(result: dict[str, Any]) -> str:
    """组装给用户看的一句话提示。"""
    warnings = result.get("warnings") or []
    missing = result.get("missingContext") or []
    parts: list[str] = []
    if result.get("degraded"):
        parts.append("AI 建议暂不可用，已返回空结果")
    elif warnings:
        parts.append(str(warnings[0]))
    if missing:
        parts.append(f"缺少上下文：{'、'.join(str(m) for m in missing[:3])}")
    return "；".join(parts)


async def _build_context(
    db: Session, *, current_user: Employee, plan_type: str, period: str,
    intent_text: str = "",
) -> dict:
    """组装计划助手的上下文响应。

    ⚠️ 这里的上下文是**真实数据**：自己的历史计划 + 部门参考。
       前端用它显示「可参考的历史计划数量」和「缺什么信息」。

    ⚠️ 部门参考**只在配置打开且用户有部门时**才有 ——
       否则 contextOptions 里会标 ``available=False``，
       前端如实显示「部门参考不可用」，不假装有。
    """
    response = _empty_context_response()
    response["enabled"] = plan_ai_service.is_enabled()
    response["providerCode"] = settings.model_mode
    response["modelName"] = settings.model_name
    response["generationProfile"] = "EMPLOYEE_EXECUTION"
    response["generationProfileLabel"] = "员工执行"

    if not response["enabled"]:
        response["notice"] = "AI 计划助手未启用"
        return response

    months = max(1, min(int(settings.plan_ai_history_months or 6), 24))
    history = plan_tool_service.load_my_plan_history(
        db, employee_id=current_user.id, months=months, plan_type="month",
    )
    response["historyPlanCount"] = int(history.get("planCount") or 0)

    available: list[str] = []
    if history.get("planCount"):
        available.append("HISTORY_PLANS")
        months_seen = sorted({
            str(p.get("planMonth")) for p in history.get("plans") or [] if p.get("planMonth")
        }, reverse=True)
        response["historyMonthOptions"] = [
            {"month": m, "label": f"{m[:4]}年{int(m[4:6])}月", "available": True,
             "planCount": sum(1 for p in history.get("plans") or [] if p.get("planMonth") == m)}
            for m in months_seen
        ]
    if current_user.department_id and settings.plan_ai_department_context:
        dept = plan_tool_service.load_department_plan_history(
            db, employee_id=current_user.id, months=months,
        )
        if dept.get("planCount"):
            available.append("DEPARTMENT_GOAL")

    # 上下文类型选项 —— 前端据此渲染可勾选的上下文清单
    option_specs = (
        ("HISTORY_PLANS", "最近三个月已审批月计划", "HISTORY_PLANS" in available),
        ("DEPARTMENT_GOAL", "部门目标", "DEPARTMENT_GOAL" in available),
        ("ORGANIZATION", "组织信息", bool(current_user.department_id)),
        ("JOB_DESCRIPTION", "岗位说明", False),
        ("LEADER_REQUIREMENT", "领导要求", False),
        ("DELIVERABLE_TEMPLATES", "交付物与验收模板", False),
        ("ENTERPRISE_KNOWLEDGE", "企业知识依据", False),
    )
    response["contextOptions"] = [
        {"type": key, "label": label, "available": ok} for key, label, ok in option_specs
    ]

    missing: list[str] = []
    if not intent_text.strip():
        missing.append("未填写计划意图，AI 只能参考历史计划生成")
    if not history.get("planCount"):
        missing.append("没有历史月计划可参考，AI 建议仅供参考")
    response["missingContext"] = missing
    response["availableContext"] = available
    if missing:
        response["notice"] = "；".join(missing)
    return response


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
    scene_code: str | None,
    plan_type: str | None,
    period_key: str | None,
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

