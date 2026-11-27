"""AI 模型配置与提示词的**真实存储**（★ 2026-11-26 新增）。

⚠️ **为什么有本模块**
------------------------------------------------------------------
``routes/v1/system_ai.py`` 的 10 个端点原先**全是空壳**：

| 端点 | 原行为 | 后果 |
|---|---|---|
| ``GET /configs`` | 恒 ``[]`` | 页面永远空白 |
| ``POST /configs`` | 回显 ``id=0``，**不落库** | 「配置已新增」，刷新即消失 |
| ``PUT /configs/{id}`` | 同上 | 改不动 |
| ``POST /configs/{id}/enable`` | 回显 ``status=ENABLED``，**不落库** | 全局开关形同虚设 |
| ``POST /configs/{id}/test`` | 恒 ``success=False`` | 测不了 |
| ``GET /prompts`` | 恒 ``[]`` | 提示词页永远空白 |
| ``POST /prompts/publish`` | 回显 ``id=0``，**不落库** | 提示词存不住 |
| ``GET /metrics`` | 恒 0 | 指标全是 0 |
| ``GET /call-logs`` | 恒空 | 日志页永远空白 |

⇒ 这不是「功能没做完」，是**页面看起来能用、实际什么都不生效**——
与已修的「绩效工作台假数据页」是同一类缺陷。

⚠️ **API Key 的处理**
------------------------------------------------------------------
- 入库：:func:`services.secret_box.encrypt_secret` 加密（信封加密）；
- 出库：**只暴露** ``apiKeyConfigured`` 布尔位，**永不回传**密文或明文；
- 使用：只有真正要调模型时才解密。

⚠️ **「全局只能有一条启用」的实现**
------------------------------------------------------------------
在**事务内**「先关旧的、再开新的」⇒ 不会出现两条 ENABLED。
⚠️ 数据库唯一约束做不到「软互斥」，所以放服务层。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.models import AiModelConfigRow, AiPromptRow
from . import secret_box

logger = logging.getLogger(__name__)


class AgentRuntimeError(RuntimeError):
    """把配置下发给 agent-service 失败。"""


def _agent_payload(row: AiModelConfigRow) -> dict[str, Any]:
    """构造运行时配置；API Key 只在内存中解密，绝不写日志。"""
    if not row.api_key_enc:
        raise AgentRuntimeError("配置缺少 API Key")
    try:
        api_key = secret_box.decrypt_secret(row.api_key_enc)
    except Exception as exc:  # noqa: BLE001
        raise AgentRuntimeError(f"API Key 解密失败：{exc}") from exc
    return {
        "mode": "openai",
        "base_url": row.base_url or "",
        "model": row.model_name or "",
        "api_key": api_key,
        "timeout_seconds": 60.0,
        "max_retries": 2,
    }


def apply_runtime_config(row: AiModelConfigRow) -> dict[str, Any]:
    """把启用的配置推给 agent-service，让它立即使用。"""
    base = (settings.agent_service_url or "").rstrip("/")
    if not base:
        raise AgentRuntimeError("未配置 AGENT_SERVICE_URL")
    if not settings.agent_shared_secret:
        raise AgentRuntimeError("未配置 AGENT_SHARED_SECRET")
    try:
        response = httpx.post(
            f"{base}/v1/agent/runtime-config",
            headers={"Authorization": f"Bearer {settings.agent_shared_secret}"},
            json=_agent_payload(row),
            timeout=10.0,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise AgentRuntimeError(f"agent-service 不可达：{exc}") from exc
    if response.status_code != 200:
        raise AgentRuntimeError(
            f"agent-service 拒绝配置：HTTP {response.status_code} {response.text[:200]}"
        )
    return response.json()


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def serialize_config(row: AiModelConfigRow) -> dict[str, Any]:
    """``AiModelConfigRow`` → 前端 ``AiModelConfig``。

    ⚠️ **绝不包含 apiKey / apiKeyEncrypted** —— 只给「是否已配置」。
    """
    return {
        "id": row.id,
        "configName": row.config_name,
        "providerCode": row.provider_code,
        "baseUrl": row.base_url,
        "apiKeyConfigured": bool(row.api_key_enc),
        "modelName": row.model_name,
        "timeoutSeconds": row.timeout_seconds,
        "globalEnabled": row.global_enabled,
        "draftEnabled": row.draft_enabled,
        "optimizeEnabled": row.optimize_enabled,
        "checkEnabled": row.check_enabled,
        "allowedUserIds": row.allowed_user_ids,
        "allowedOrgIds": row.allowed_org_ids,
        "draftDailyLimit": row.draft_daily_limit,
        "optimizeDailyLimit": row.optimize_daily_limit,
        "checkDailyLimit": row.check_daily_limit,
        "versionNo": row.version_no,
        "status": row.status,
    }


def serialize_prompt(row: AiPromptRow) -> dict[str, Any]:
    """``AiPromptRow`` → 前端 ``AiPrompt``。"""
    return {
        "id": row.id,
        "sceneCode": row.scene_code,
        "versionNo": row.version_no,
        "systemPrompt": row.system_prompt,
        "userTemplate": row.user_template,
        "outputSchemaVersion": row.output_schema_version,
        "status": row.status,
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }


# ---------------------------------------------------------------------------
# 模型配置
# ---------------------------------------------------------------------------


def list_configs(db: Session) -> list[dict[str, Any]]:
    """模型配置列表（按 id 升序）。"""
    rows = db.execute(
        select(AiModelConfigRow).order_by(AiModelConfigRow.id)
    ).scalars().all()
    return [serialize_config(row) for row in rows]


def create_config(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """新建一条模型配置。

    ⚠️ **新建的配置默认 ``DISABLED``** —— 绝不能因为「有人建了一条」
    就自动把全公司的 AI 开关打开。
    """
    row = AiModelConfigRow(
        config_name=str(payload.get("configName") or "").strip() or "未命名配置",
        provider_code=str(payload.get("providerCode") or "OPENAI_COMPATIBLE").strip(),
        base_url=str(payload.get("baseUrl") or "").strip(),
        model_name=str(payload.get("modelName") or "").strip(),
        timeout_seconds=int(payload.get("timeoutSeconds") or 60),
        global_enabled=bool(payload.get("globalEnabled")),
        draft_enabled=bool(payload.get("draftEnabled")),
        optimize_enabled=bool(payload.get("optimizeEnabled")),
        check_enabled=bool(payload.get("checkEnabled")),
        allowed_user_ids=str(payload.get("allowedUserIds") or "").strip(),
        allowed_org_ids=str(payload.get("allowedOrgIds") or "").strip(),
        draft_daily_limit=int(payload.get("draftDailyLimit") or 0),
        optimize_daily_limit=int(payload.get("optimizeDailyLimit") or 0),
        check_daily_limit=int(payload.get("checkDailyLimit") or 0),
        version_no=int(payload.get("versionNo") or 1),
        status="DISABLED",
    )

    api_key = payload.get("apiKey")
    if api_key:
        row.api_key_enc = secret_box.encrypt_secret(str(api_key))

    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_config(row)


def update_config(db: Session, config_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """更新一条模型配置（不存在返回 ``None``）。"""
    row = db.get(AiModelConfigRow, config_id)
    if row is None:
        return None

    text_fields = {
        "configName": ("config_name", True),
        "providerCode": ("provider_code", True),
        "baseUrl": ("base_url", False),
        "modelName": ("model_name", True),
        "allowedUserIds": ("allowed_user_ids", False),
        "allowedOrgIds": ("allowed_org_ids", False),
    }
    for key, (attr, fallback_when_blank) in text_fields.items():
        if key not in payload:
            continue
        value = str(payload.get(key) or "").strip()
        if not value and fallback_when_blank:
            # ⚠️ 名称/供应商/模型名**不能清空**（清空了这条配置就没意义了）
            #    ⇒ 空值时保持原值。
            continue
        setattr(row, attr, value)

    int_fields = {
        "timeoutSeconds": "timeout_seconds",
        "draftDailyLimit": "draft_daily_limit",
        "optimizeDailyLimit": "optimize_daily_limit",
        "checkDailyLimit": "check_daily_limit",
        "versionNo": "version_no",
    }
    for key, attr in int_fields.items():
        if key in payload:
            setattr(row, attr, int(payload.get(key) or 0))

    bool_fields = {
        "globalEnabled": "global_enabled",
        "draftEnabled": "draft_enabled",
        "optimizeEnabled": "optimize_enabled",
        "checkEnabled": "check_enabled",
    }
    for key, attr in bool_fields.items():
        if key in payload:
            setattr(row, attr, bool(payload.get(key)))

    # ⚠️ API Key：**传了才改**，没传（空串 / None）就**保持原值**
    # ⇒ 前端编辑表单不回显 API Key（永远拿不到），
    #    如果后端把「没填」当成「清空」，管理员改个名字就会把凭据擦掉。
    api_key = payload.get("apiKey")
    if api_key:
        row.api_key_enc = secret_box.encrypt_secret(str(api_key))

    db.commit()
    db.refresh(row)
    return serialize_config(row)


def enable_config(db: Session, config_id: int) -> dict[str, Any] | None:
    """启用一条配置（**同时停用其它配置**）。

    ⚠️ **「全局唯一启用」在事务内保证**：先关旧的，再开新的。
    """
    row = db.get(AiModelConfigRow, config_id)
    if row is None:
        return None

    # ⚠️ 先让 agent-service 真正切过去，再改数据库状态。
    #    否则会出现「页面显示已启用、实际调用仍是旧模型」的假成功。
    apply_runtime_config(row)

    for other in db.execute(
        select(AiModelConfigRow).where(
            AiModelConfigRow.id != config_id,
            AiModelConfigRow.status == "ENABLED",
        )
    ).scalars().all():
        other.status = "DISABLED"

    row.status = "ENABLED"
    db.commit()
    db.refresh(row)
    return serialize_config(row)


def apply_enabled_config_on_startup(db: Session) -> dict[str, Any] | None:
    """后端启动时把已启用的配置重新推给 agent-service（best effort）。

    agent-service 重启后运行时配置会丢；后端重启/部署时补推一次。
    推不动只记日志，不阻塞启动 —— 等管理员在页面上重新启用即可。
    """
    row = db.execute(
        select(AiModelConfigRow).where(AiModelConfigRow.status == "ENABLED")
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        result = apply_runtime_config(row)
        logger.info("已把 AI 模型配置推送到 agent-service：%s", result.get("model"))
        return result
    except AgentRuntimeError as exc:
        logger.warning("启动时推送 AI 模型配置失败（不影响启动）：%s", exc)
        return None


def test_config(db: Session, config_id: int) -> dict[str, Any]:
    """测试模型连通性。

    ⚠️ **当前实现只做「配置完整性自检」**，**不真的发请求**：
    - 真模型供应商尚未选型（``docs/11`` §六 第 4 项待领导拍板），
      现在发请求只会打到不存在的地址；
    - 「测试连接」按钮对管理员的价值是「我填对了没有」，
      字段级校验已经覆盖这个需求。

    ⇒ 返回 ``success=False`` + **具体缺什么**，而不是笼统的「失败」。

    ⚠️ 未来接真模型时，把这里换成真实的 HTTP 探测即可，
    接口形状（``success`` / ``providerCode`` / ``modelName`` /
    ``latencyMs`` / ``message``）**保持不变** ⇒ 前端不用改。
    """
    row = db.get(AiModelConfigRow, config_id)
    if row is None:
        return {
            "success": False,
            "providerCode": "",
            "modelName": "",
            "latencyMs": 0,
            "message": "配置不存在",
        }

    missing: list[str] = []
    if not row.base_url:
        missing.append("接口地址")
    if not row.model_name:
        missing.append("模型名称")
    if not row.api_key_enc:
        missing.append("API Key")

    if missing:
        return {
            "success": False,
            "providerCode": row.provider_code,
            "modelName": row.model_name,
            "latencyMs": 0,
            "message": f"配置不完整，缺少：{'、'.join(missing)}",
        }

    try:
        api_key = secret_box.decrypt_secret(row.api_key_enc)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "providerCode": row.provider_code,
            "modelName": row.model_name,
            "latencyMs": 0,
            "message": f"API Key 解密失败：{exc}",
        }

    base = (row.base_url or "").rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    started = time.perf_counter()
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": row.model_name,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=10.0,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "success": False,
            "providerCode": row.provider_code,
            "modelName": row.model_name,
            "latencyMs": elapsed,
            "message": f"连接失败：{exc}",
        }

    elapsed = int((time.perf_counter() - started) * 1000)
    if 200 <= response.status_code < 300:
        return {
            "success": True,
            "providerCode": row.provider_code,
            "modelName": row.model_name,
            "latencyMs": elapsed,
            "message": "连接成功",
        }
    return {
        "success": False,
        "providerCode": row.provider_code,
        "modelName": row.model_name,
        "latencyMs": elapsed,
        "message": f"HTTP {response.status_code}：{response.text[:200]}",
    }


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------


def list_prompts(db: Session) -> list[dict[str, Any]]:
    """提示词列表（按 id 升序）。"""
    rows = db.execute(select(AiPromptRow).order_by(AiPromptRow.id)).scalars().all()
    return [serialize_prompt(row) for row in rows]


def publish_prompt(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """发布提示词版本（同场景同版本号则**覆盖**）。"""
    scene_code = str(payload.get("sceneCode") or "").strip()
    version_no = str(payload.get("versionNo") or "").strip()
    if not scene_code:
        raise ValueError("缺少场景标识（sceneCode）")
    if not version_no:
        raise ValueError("缺少版本号（versionNo）")

    row = db.execute(
        select(AiPromptRow).where(
            AiPromptRow.scene_code == scene_code,
            AiPromptRow.version_no == version_no,
        )
    ).scalars().first()

    if row is None:
        row = AiPromptRow(scene_code=scene_code, version_no=version_no)
        db.add(row)

    row.system_prompt = str(payload.get("systemPrompt") or "")
    row.user_template = str(payload.get("userTemplate") or "")
    row.output_schema_version = str(payload.get("outputSchemaVersion") or "")
    row.status = str(payload.get("status") or "PUBLISHED")

    db.commit()
    db.refresh(row)
    return serialize_prompt(row)


__all__ = [
    "create_config",
    "enable_config",
    "list_configs",
    "list_prompts",
    "publish_prompt",
    "serialize_config",
    "serialize_prompt",
    "test_config",
    "update_config",
]
