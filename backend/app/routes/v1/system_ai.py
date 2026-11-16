"""AI 管理 API 路由。

对应前端 `src/api/aiManagement.ts` 的 `/system/ai/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/system/ai` 前缀。

分页约定：前端 AiCallLogPage 用 `items`（即 PageResponse<T>），
故调用日志列表返回 { items, total, pageNo, pageSize }。
其余列表端点前端直接 .map()，返回裸数组。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/system/ai", tags=["system-ai"], dependencies=[Depends(get_current_user)])


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
async def list_ai_configs(db: Session = Depends(get_db)) -> dict:
    """获取 AI 模型配置列表。

    响应结构：AiModelConfig[]（裸数组）
    """
    return _ok([])


@router.post("/configs")
async def create_ai_config(
    payload: AiModelConfigPayload,
    db: Session = Depends(get_db),
) -> dict:
    """创建 AI 模型配置。

    响应结构：AiModelConfig
    """
    return _ok(_empty_model_config(payload))


@router.put("/configs/{config_id}")
async def update_ai_config(
    config_id: int,
    payload: AiModelConfigPayload,
    db: Session = Depends(get_db),
) -> dict:
    """更新 AI 模型配置。

    响应结构：AiModelConfig
    """
    return _ok(_empty_model_config(payload, config_id))


@router.post("/configs/{config_id}/enable")
async def enable_ai_config(
    config_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """启用 AI 模型配置。

    响应结构：AiModelConfig
    """
    return _ok(_empty_model_config(AiModelConfigPayload(), config_id, status="ENABLED"))


@router.post("/configs/{config_id}/test")
async def test_ai_config(
    config_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """测试 AI 模型配置的连通性。

    响应结构：{ success, providerCode, modelName, latencyMs, message }
    """
    return _ok(
        {
            "success": False,
            "providerCode": "",
            "modelName": "",
            "latencyMs": 0,
            "message": "尚未配置可用的模型",
        }
    )


# ---------------------------------------------------------------------------
# 提示词
#
# 响应结构：AiPrompt
# ---------------------------------------------------------------------------


@router.get("/prompts")
async def list_ai_prompts(db: Session = Depends(get_db)) -> dict:
    """获取 AI 提示词列表。

    响应结构：AiPrompt[]（裸数组）
    """
    return _ok([])


@router.post("/prompts/publish")
async def publish_ai_prompt(
    payload: AiPromptPayload,
    db: Session = Depends(get_db),
) -> dict:
    """发布 AI 提示词版本。

    响应结构：AiPrompt
    """
    return _ok(_empty_prompt(payload))


# ---------------------------------------------------------------------------
# 调用指标
#
# 响应结构：AiMetrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def get_ai_metrics(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> dict:
    """获取 AI 调用指标。

    响应结构：AiMetrics
    """
    return _ok(
        {
            "totalCalls": 0,
            "successCalls": 0,
            "successRate": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "averageLatencyMs": 0,
            "adoptedSuggestions": 0,
            "adoptionRate": 0,
            "byScene": [],
        }
    )


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
) -> dict:
    """获取 AI 调用日志列表。

    响应结构：AiCallLogPage = { items, total, pageNo, pageSize }
    """
    return _ok(
        {
            "items": [],
            "total": 0,
            "pageNo": pageNo,
            "pageSize": pageSize,
        }
    )


@router.get("/call-logs/{log_id}")
async def get_ai_call_log(
    log_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取 AI 调用日志详情。

    响应结构：AiCallLogDetail
    """
    return _ok(_empty_call_log_detail(log_id))


def _empty_model_config(
    payload: AiModelConfigPayload,
    config_id: int = 0,
    status: str = "DISABLED",
) -> dict:
    """构造 AiModelConfig，字段与 frontend/src/api/aiManagement.ts 保持一致。"""
    return {
        "id": config_id,
        "configName": payload.configName,
        "providerCode": payload.providerCode,
        "baseUrl": payload.baseUrl,
        # apiKey 永不回传，只暴露是否已配置。
        "apiKeyConfigured": bool(payload.apiKey),
        "modelName": payload.modelName,
        "timeoutSeconds": payload.timeoutSeconds,
        "globalEnabled": payload.globalEnabled,
        "draftEnabled": payload.draftEnabled,
        "optimizeEnabled": payload.optimizeEnabled,
        "checkEnabled": payload.checkEnabled,
        "allowedUserIds": payload.allowedUserIds,
        "allowedOrgIds": payload.allowedOrgIds,
        "draftDailyLimit": payload.draftDailyLimit,
        "optimizeDailyLimit": payload.optimizeDailyLimit,
        "checkDailyLimit": payload.checkDailyLimit,
        "versionNo": payload.versionNo,
        "status": status,
    }


def _empty_prompt(payload: AiPromptPayload) -> dict:
    """构造 AiPrompt，字段与 frontend/src/api/aiManagement.ts 保持一致。"""
    return {
        "id": 0,
        "sceneCode": payload.sceneCode,
        "versionNo": payload.versionNo,
        "systemPrompt": payload.systemPrompt,
        "userTemplate": payload.userTemplate,
        "outputSchemaVersion": payload.outputSchemaVersion,
        "status": payload.status,
        "createdAt": datetime.utcnow().isoformat(),
    }


def _empty_call_log_detail(log_id: int) -> dict:
    """构造 AiCallLogDetail，字段与 frontend/src/api/aiManagement.ts 保持一致。"""
    return {
        "id": log_id,
        "createdAt": datetime.utcnow().isoformat(),
        "sceneCode": "",
        "success": False,
        "userId": 0,
        "orgId": None,
        "bizType": None,
        "bizId": None,
        "providerCode": None,
        "modelName": None,
        "promptVersion": None,
        "inputTokens": 0,
        "outputTokens": 0,
        "latencyMs": 0,
        "errorCode": None,
        "errorMessage": None,
        "requestId": "",
        "suggestionId": "",
        "inputHash": "",
        "inputSummary": None,
        "outputJson": None,
        "suggestionAction": None,
        "traceEvents": [],
    }
