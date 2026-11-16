"""飞书审批回流 + IM 通知 API（A8）。

端点分三类：

1. **Webhook 接收端点**（`/v1/feishu/approval/events`）
   —— 飞书审批状态变化时回调本系统，触发审批回流。
   **不要求登录态**（飞书服务器调用），但**必须校验 Verification Token**。

2. **管理端点**（管理员可手动触发回流 / 查回流流水）
   —— 补偿 webhook 漏推；排查问题时看流水。

3. **通知测试端点** —— 验证 IM 通知链路是否通。

签名校验
--------
飞书 webhook 提供 **Verification Token**（请求体 `token` 字段）与
**Encrypt Key**（AES 加密）两套机制。本项目采用 **Verification Token 比对**：
请求体中的 `token` 字段必须等于配置的 token，不等则 401。

> 加密模式（Encrypt Key）暂未实现 —— 需要 AES 解密依赖且复杂度更高。
> 当前按明文 token 模式实现，配置 `FEISHU_APPROVAL_WEBHOOK_TOKEN` 即可启用；
> 未配置时端点返回 503（**不放行**，避免未鉴权的写入入口）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import feishu_approval_sync, feishu_binding, plan_approval
from ..services.feishu_client import FeishuClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feishu", tags=["feishu-approval"])


# ===========================================================================
# 依赖
# ===========================================================================


def _client() -> FeishuClient:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(status_code=503, detail="飞书应用配置未完成")
    return FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )


def _require_admin(db: Session, current_user: Employee) -> None:
    """仅系统管理员可操作。"""
    from ..services.access_control import PermissionAction, build_access_context

    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.SYNC_ORGANIZATION):
        raise HTTPException(status_code=403, detail="需要系统管理员权限")


# ===========================================================================
# 请求模型
# ===========================================================================


class ApprovalEventRequest(BaseModel):
    """飞书审批事件体（webhook 推送）。

    飞书实际推送的字段比这多，这里只取**回流必需**的部分，
    多余字段由 Pydantic 默认忽略。
    """

    token: str = Field(default="", description="飞书 Verification Token")
    event_id: str = Field(default="", description="事件 ID，用于幂等")
    event_type: str = Field(default="", description="事件类型")
    # 审批实例信息
    instance_code: str = Field(default="", description="审批实例 code")
    instance_status: str = Field(default="", description="审批实例状态")
    approval_code: str = Field(default="", description="审批定义 code")
    user_id: str = Field(default="", description="发起人 user_id")
    open_id: str = Field(default="", description="发起人 open_id")
    comment: str = Field(default="", description="审批意见")
    # 本系统计划标识（事件自带时可省略 —— 走绑定表反查）
    plan_type: str = Field(default="", description="计划类型 month/week/day")
    plan_id: str = Field(default="", description="计划 ID")
    # 关联码（飞书侧发起时，员工在表单里填写的短码）
    correlation_key: str = Field(default="", description="关联码，用于反查计划")


class ReflowRequest(BaseModel):
    """手工触发回流。

    ``plan_type`` / ``plan_id`` 可留空 —— 此时按 ``instance_id`` 走绑定表反查。
    """

    plan_type: str = Field(default="", pattern="^(month|week|day)?$")
    plan_id: str = Field(default="")
    instance_id: str = Field(..., min_length=1)


class BindRequest(BaseModel):
    """手工绑定计划与飞书审批实例。"""

    plan_type: str = Field(..., pattern="^(month|week|day)$")
    plan_id: str = Field(..., min_length=1)
    instance_code: str = Field(default="")
    approval_code: str = Field(default="")
    correlation_key: str = Field(default="")


class NotifyRequest(BaseModel):
    """手工触发 IM 通知。"""

    plan_type: str = Field(..., pattern="^(month|week|day)$")
    plan_id: str = Field(..., min_length=1)
    message: str = Field(default="", description="自定义消息；留空则用默认文案")


# ===========================================================================
# 1. Webhook 接收端点
# ===========================================================================


@router.post("/approval/events")
async def receive_approval_event(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """接收飞书审批状态变更事件，触发审批回流。

    **无需登录态**（调用方是飞书服务器），但必须通过 Verification Token 校验。

    响应体遵循飞书 webhook 约定：返回 `{"code": 0}` 表示成功。
    """
    # ── 1. 校验 Verification Token ──────────────────────────────
    expected_token = settings.feishu_approval_webhook_token
    if not expected_token:
        # 未配置 → 端点不可用。**绝不能放行**，否则任何人都能改审批状态。
        logger.error("飞书审批 webhook 收到事件但未配置 FEISHU_APPROVAL_WEBHOOK_TOKEN")
        raise HTTPException(
            status_code=503,
            detail="飞书审批 webhook 未配置 Verification Token",
        )

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from exc

    token = str(body.get("token") or "")
    if not token or token != expected_token:
        logger.warning("飞书审批 webhook token 校验失败")
        raise HTTPException(status_code=401, detail="Verification Token 校验失败")

    # ── 2. 解析事件 ────────────────────────────────────────────
    event = ApprovalEventRequest.model_validate(body)
    plan_type = event.plan_type.strip().lower()
    plan_id = event.plan_id.strip()
    correlation_key = event.correlation_key.strip()

    status_value = event.instance_status.strip().upper()
    instance_id = event.instance_code.strip() or event.event_id.strip()

    # 计划标识可以来自事件本身，也可以由绑定表反查（飞书侧发起的场景）。
    # 三者都拿不到 → 无法定位计划，记日志忽略（不报错，避免飞书反复重试）。
    if not (plan_type and plan_id) and not (instance_id or correlation_key):
        logger.warning("飞书审批 webhook 事件缺少计划标识与绑定线索，跳过")
        return {"code": 0, "message": "事件缺少计划标识，已忽略"}

    # ── 3. 回流 ────────────────────────────────────────────────
    try:
        result = feishu_approval_sync.apply_feishu_approval_event(
            db,
            plan_type=plan_type,
            plan_id=plan_id,
            feishu_status=status_value,
            instance_id=instance_id,
            correlation_key=correlation_key,
            approval_code=event.approval_code.strip(),
            actor_feishu_id=event.user_id or event.open_id,
            comment=event.comment or None,
        )
    except plan_approval.PlanApprovalError as exc:
        # 计划不存在 / 类型非法 → 404
        db.rollback()
        logger.warning("飞书审批回流失败：%s", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except feishu_approval_sync.FeishuApprovalSyncError as exc:
        db.rollback()
        logger.warning("飞书审批回流失败：%s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("飞书审批回流异常")
        raise HTTPException(status_code=500, detail="审批回流失败") from exc

    db.commit()
    logger.info(
        "飞书审批 webhook 处理完成：plan=%s status=%s result=%s",
        plan_id,
        status_value,
        result,
    )
    return {"code": 0, "message": "ok", "data": {"result": result}}


# ===========================================================================
# 2. 管理端点
# ===========================================================================


@router.post("/approval/reflow")
def trigger_reflow(
    request: ReflowRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """手工触发一次审批回流（主动拉取模式）。

    用于补偿 webhook 漏推 —— 从飞书拉取实例最新状态并应用。
    """
    _require_admin(db, current_user)

    client = _client()
    try:
        result, detail = feishu_approval_sync.sync_feishu_approval_instance(
            db,
            plan_type=request.plan_type,
            plan_id=request.plan_id,
            instance_id=request.instance_id,
            client=client,
        )
    except feishu_approval_sync.FeishuApprovalSyncError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("手工审批回流失败")
        raise HTTPException(status_code=502, detail=f"飞书接口调用失败：{exc}") from exc
    finally:
        client.close()

    db.commit()
    return {"code": 0, "message": "回流完成", "data": {"result": result, **detail}}


@router.get("/approval/reflow-records")
def list_reflow_records(
    plan_type: str | None = Query(default=None, pattern="^(month|week|day)$"),
    plan_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """列出飞书回流产生的审批流水（按 `dedup_key` 的 `feishu:` 前缀识别）。"""
    _require_admin(db, current_user)
    rows = feishu_approval_sync.list_feishu_reflow_records(
        db, plan_type=plan_type, plan_id=plan_id, limit=limit
    )
    return {
        "code": 0,
        "data": {
            "items": [feishu_approval_sync.serialize_reflow_record(r) for r in rows],
            "total": len(rows),
        },
    }


@router.post("/approval/notify")
def trigger_notify(
    request: NotifyRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """手工触发一次 IM 通知（验证通知链路是否通）。"""
    _require_admin(db, current_user)

    plan_info = feishu_approval_sync.find_plan_by_feishu_instance(
        db, plan_type=request.plan_type, plan_id=request.plan_id
    )
    if plan_info is None:
        raise HTTPException(status_code=404, detail="计划不存在")

    owner_id = plan_info.get("ownerId") or ""
    message = request.message or f"【计划通知】计划 {request.plan_id} 待审批。"

    client = _client()
    try:
        sent = feishu_approval_sync.notify_plan_submitted(
            db,
            owner_id=owner_id,
            plan_type=request.plan_type,
            plan_id=request.plan_id,
            plan_title=request.plan_id,
            client=client,
            submitter_note=message,
        )
    finally:
        client.close()

    return {
        "code": 0,
        "message": "通知已发送" if sent else "未发送（无审批人或无飞书 ID）",
        "data": {"sent": sent},
    }


@router.get("/approval/status")
def get_approval_sync_status(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """审批回流链路配置状态（管理员可查，便于排障）。"""
    _require_admin(db, current_user)
    return {
        "code": 0,
        "data": {
            "appConfigured": bool(settings.feishu_app_id and settings.feishu_app_secret),
            "webhookTokenConfigured": bool(settings.feishu_approval_webhook_token),
            "webhookEnabled": bool(settings.feishu_approval_webhook_token),
        },
    }


# ===========================================================================
# 3. 绑定管理端点
# ===========================================================================


@router.get("/approval/bindings")
def list_bindings(
    plan_type: str | None = Query(default=None, pattern="^(month|week|day)?$"),
    plan_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """列出计划 ↔ 飞书实例的绑定关系。"""
    _require_admin(db, current_user)
    rows = feishu_binding.list_bindings(
        db, plan_type=plan_type or None, plan_id=plan_id, limit=limit
    )
    return {
        "code": 0,
        "data": {
            "items": [feishu_binding.serialize_binding(r) for r in rows],
            "total": len(rows),
        },
    }


@router.post("/approval/bindings")
def create_binding(
    request: BindRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """手工绑定计划与飞书审批实例（幂等）。

    用于补建历史绑定，或把「飞书侧发起的审批」人工关联到本系统计划。
    """
    _require_admin(db, current_user)

    # 校验计划存在
    try:
        plan_approval.fetch_plan(db, request.plan_type, request.plan_id)
    except plan_approval.PlanApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        row = feishu_binding.bind_plan_to_instance(
            db,
            plan_type=request.plan_type,
            plan_id=request.plan_id,
            instance_code=request.instance_code,
            approval_code=request.approval_code,
            correlation_key=(
                feishu_binding.normalize_correlation_key(request.correlation_key)
                if request.correlation_key
                else ""
            ),
            bind_source=feishu_binding.BIND_SOURCE_MANUAL,
            created_by=current_user.id,
        )
    except feishu_binding.FeishuBindingError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("绑定失败")
        raise HTTPException(status_code=409, detail="绑定失败（可能与既有绑定冲突）") from exc

    db.commit()
    return {
        "code": 0,
        "message": "绑定成功",
        "data": feishu_binding.serialize_binding(row),
    }


@router.get("/approval/bindings/by-plan/{plan_type}/{plan_id}")
def get_plan_binding(
    plan_type: str,
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查某个计划的绑定关系（含关联码）。"""
    _require_admin(db, current_user)
    row = feishu_binding.get_binding_by_plan(db, plan_type, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="该计划尚无绑定")
    return {"code": 0, "data": feishu_binding.serialize_binding(row)}
