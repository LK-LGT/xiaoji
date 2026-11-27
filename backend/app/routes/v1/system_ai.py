"""AI 管理 API 路由。

对应前端 `src/api/aiManagement.ts` 的 `/system/ai/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/system/ai` 前缀。

分页约定：前端 AiCallLogPage 用 `items`（即 PageResponse<T>），
故调用日志列表返回 { items, total, pageNo, pageSize }。
其余列表端点前端直接 .map()，返回裸数组。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...dependencies import get_current_user
from ...domain.models import Employee
from ...services import ai_call_log_service, ai_model_config_service
from ...services.access_control import PermissionAction, build_access_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/system/ai", tags=["system-ai"], dependencies=[Depends(get_current_user)])


def _require_ai_admin(db: Session, current_user: Employee) -> None:
    """🔴 要求「系统配置」权限（HR / 系统管理员），否则 403。

    ⚠️ **为什么必须有（2026-11-26）**
    本模块原先**只校验登录态** ⇒ 任何员工都能：
    - ``POST /configs`` 写入 **API Key**（含供应商凭据）；
    - ``POST /configs/{id}/enable`` 切换**全局启用**的模型配置。

    ⇒ AI 供应商凭据 + 全局开关是**系统级配置**，
    与「谁能看绩效数据」无关，必须走配置权限。

    ⚠️ **权限位口径**
    用 ``MANAGE_SYSTEM_CONFIG``（**动作权限**）——
    ``access_control`` 里只授予 HR 与系统管理员。
    """
    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.MANAGE_SYSTEM_CONFIG):
        raise HTTPException(status_code=403, detail="无权管理 AI 模型配置")


# ---------------------------------------------------------------------------
# 请求模型
#
# 注意：必须用显式 Pydantic 模型，不能用 `payload: dict`。
# 本项目所用 FastAPI 版本不会把裸 dict 注解自动解析为 JSON body，
# 请求体会以字符串形式传入并触发 422（dict_type 校验失败）。
# ---------------------------------------------------------------------------


class AiModelConfigPayload(BaseModel):
    """AI 模型配置请求体。

    对应前端 AiModelConfigPayload = Omit<AiModelConfig, 'id'|'apiKeyConfigured'|'status'> & { apiKey? }
    """

    configName: str = Field(default="")
    providerCode: str = Field(default="OPENAI_COMPATIBLE")
    baseUrl: str = Field(default="")
    apiKey: str | None = Field(default=None, description="仅写入不回传")
    modelName: str = Field(default="")
    timeoutSeconds: int = Field(default=60)
    globalEnabled: bool = Field(default=False)
    draftEnabled: bool = Field(default=False)
    optimizeEnabled: bool = Field(default=False)
    checkEnabled: bool = Field(default=False)
    allowedUserIds: str = Field(default="")
    allowedOrgIds: str = Field(default="")
    draftDailyLimit: int = Field(default=0)
    optimizeDailyLimit: int = Field(default=0)
    checkDailyLimit: int = Field(default=0)
    versionNo: int = Field(default=1)


class AiPromptPayload(BaseModel):
    """AI 提示词发布请求体。"""

    sceneCode: str = Field(default="")
    versionNo: str = Field(default="")
    systemPrompt: str = Field(default="")
    userTemplate: str = Field(default="")
    outputSchemaVersion: str = Field(default="")
    status: str = Field(default="PUBLISHED")


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 模型配置
#
# 响应结构：AiModelConfig
# ---------------------------------------------------------------------------


@router.get("/configs")
async def list_ai_configs(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取 AI 模型配置列表。

    响应结构：AiModelConfig[]（裸数组）
    """
    _require_ai_admin(db, current_user)
    return _ok(_list_model_configs(db))


@router.post("/configs")
async def create_ai_config(
    payload: AiModelConfigPayload,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """创建 AI 模型配置。

    响应结构：AiModelConfig
    """
    _require_ai_admin(db, current_user)
    return _ok(_create_model_config(db, payload))


@router.put("/configs/{config_id}")
async def update_ai_config(
    config_id: int,
    payload: AiModelConfigPayload,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """更新 AI 模型配置。

    响应结构：AiModelConfig
    """
    _require_ai_admin(db, current_user)
    return _ok(_update_model_config(db, config_id, payload))


@router.post("/configs/{config_id}/enable")
async def enable_ai_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """启用 AI 模型配置。

    响应结构：AiModelConfig
    """
    _require_ai_admin(db, current_user)
    return _ok(_enable_model_config(db, config_id))


@router.post("/configs/{config_id}/test")
async def test_ai_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """测试 AI 模型配置的连通性。

    响应结构：{ success, providerCode, modelName, latencyMs, message }
    """
    _require_ai_admin(db, current_user)
    return _ok(_test_model_config(db, config_id))


# ---------------------------------------------------------------------------
# 提示词
#
# 响应结构：AiPrompt
# ---------------------------------------------------------------------------


@router.get("/prompts")
async def list_ai_prompts(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取 AI 提示词列表。

    响应结构：AiPrompt[]（裸数组）
    """
    _require_ai_admin(db, current_user)
    return _ok(_list_prompts(db))


@router.post("/prompts/publish")
async def publish_ai_prompt(
    payload: AiPromptPayload,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """发布 AI 提示词版本。

    响应结构：AiPrompt
    """
    _require_ai_admin(db, current_user)
    return _ok(_publish_prompt(db, payload))


# ---------------------------------------------------------------------------
# 调用指标
#
# 响应结构：AiMetrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def get_ai_metrics(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取 AI 调用指标。

    响应结构：AiMetrics
    """
    _require_ai_admin(db, current_user)
    return _ok(_collect_metrics(db, days))


# ---------------------------------------------------------------------------
# 调用日志
#
# 响应结构：AiCallLogPage（分页） / AiCallLogDetail
# ---------------------------------------------------------------------------


@router.get("/call-logs")
async def list_ai_call_logs(
    start: str | None = Query(None),
    end: str | None = Query(None),
    sceneCode: str | None = Query(None),
    success: bool | None = Query(None),
    userId: str | None = Query(None),
    orgId: str | None = Query(None),
    requestId: str | None = Query(None),
    suggestionId: str | None = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取 AI 调用日志列表。

    响应结构：AiCallLogPage = { items, total, pageNo, pageSize }

    ⚠️ **权限**：调用日志含「谁问了什么」的审计信息
    ⇒ 与审计日志同级，要求配置权限（HR / 系统管理员）。
    """
    _require_ai_admin(db, current_user)
    return _ok(
        _list_call_logs(
            db,
            start=start,
            end=end,
            scene_code=sceneCode,
            success=success,
            user_id=userId,
            org_id=orgId,
            request_id=requestId,
            suggestion_id=suggestionId,
            page_no=pageNo,
            page_size=pageSize,
        )
    )


@router.get("/call-logs/{log_id}")
async def get_ai_call_log(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取 AI 调用日志详情。

    响应结构：AiCallLogDetail
    """
    _require_ai_admin(db, current_user)
    return _ok(_get_call_log(db, log_id))


def _list_model_configs(db: Session) -> list[dict]:
    """模型配置列表（真实数据）。"""
    return ai_model_config_service.list_configs(db)


def _create_model_config(db: Session, payload: AiModelConfigPayload) -> dict:
    """新建模型配置（真实落库）。"""
    return ai_model_config_service.create_config(db, payload.model_dump())


def _update_model_config(db: Session, config_id: int, payload: AiModelConfigPayload) -> dict:
    """更新模型配置（真实落库）。"""
    result = ai_model_config_service.update_config(db, config_id, payload.model_dump())
    if result is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return result


def _enable_model_config(db: Session, config_id: int) -> dict:
    """启用模型配置（真实落库，并下发到 agent-service）。"""
    try:
        result = ai_model_config_service.enable_config(db, config_id)
    except ai_model_config_service.AgentRuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"agent-service 未接受配置：{exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return result


def _test_model_config(db: Session, config_id: int) -> dict:
    """测试模型配置连通性。"""
    return ai_model_config_service.test_config(db, config_id)


def _list_prompts(db: Session) -> list[dict]:
    """提示词列表（真实数据）。"""
    return ai_model_config_service.list_prompts(db)


def _publish_prompt(db: Session, payload: AiPromptPayload) -> dict:
    """发布提示词版本（真实落库）。"""
    try:
        return ai_model_config_service.publish_prompt(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _collect_metrics(db: Session, days: int) -> dict:
    """AI 调用指标（真实数据，来自 ai_trace / ai_feedback）。"""
    return ai_call_log_service.collect_metrics(db, days)


def _list_call_logs(db: Session, **kwargs: Any) -> dict:
    """AI 调用日志分页（真实数据，来自 ai_trace / ai_tool_call）。"""
    return ai_call_log_service.list_call_logs(db, **kwargs)


def _get_call_log(db: Session, log_id: int) -> dict:
    """AI 调用日志详情。"""
    result = ai_call_log_service.get_call_log(db, log_id)
    if result is None:
        raise HTTPException(status_code=404, detail="调用日志不存在")
    return result
