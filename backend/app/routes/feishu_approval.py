"""飞书审批回流 + IM 通知 API（A8）。

端点分三类：

1. **Webhook 接收端点**（`/v1/feishu/approval/events`）
   —— 飞书审批状态变化时回调本系统，触发审批回流。
   **不要求登录态**（飞书服务器调用），但**必须通过来源校验**。

2. **管理端点**（管理员可手动触发回流 / 查回流流水）
   —— 补偿 webhook 漏推；排查问题时看流水。

3. **通知测试端点** —— 验证 IM 通知链路是否通。

来源校验（两种模式自动切换）
--------------------------
飞书后台「事件与回调 > 加密策略」配没配 Encrypt Key，决定了推送形态：

**明文模式**（未配 Encrypt Key）

    body 直接是事件 JSON，比对 ``header.token`` == Verification Token。

**加密模式**（配了 Encrypt Key）—— body **只有** ``{"encrypt": "..."}``

    1. 先用 Encrypt Key 做 AES-256-CBC 解密拿到明文；
    2. 用 ``X-Lark-Signature`` 做 SHA-256 签名校验
       （``sha256(timestamp + nonce + encrypt_key + 原始 body)``）；
    3. 再比对明文里的 Verification Token。

两种模式都**不支持降级放行** —— 解密失败 / 验签失败一律拒绝。

握手
----
飞书保存回调地址时会 POST 一个带 ``challenge`` 的请求，**必须在 1 秒内**
原样返回 ``{"challenge": "<值>"}``，否则地址保存不上。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import feishu_approval_sync, feishu_binding, feishu_event, plan_approval
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


async def _try_dispatch_to_bot(db: Session, body: dict[str, Any]) -> dict[str, Any] | None:
    """把机器人事件转给机器人处理器；**不是**机器人事件则返回 ``None``。

    ⚠️ **为什么需要这个函数**：

    飞书官方：「**每个应用只能配置一个请求地址，该应用订阅的所有事件
    都会发送到该请求地址**。」

    ⇒ 审批 / 通讯录 / 机器人**必然共用**一个地址 —— 这是飞书的机制，
    不是配置问题。**任何**「给机器人单独配一个地址」的想法都不成立。

    实测现状（2026-11-17）：
    - 后台的请求地址指向 ``/v1/feishu/approval/events``
      （审批 Token 已配、握手通过；通讯录 Token 反而是空的）
    - ⇒ 机器人端点 ``/v1/feishu/bot/events`` **收不到任何事件**

    ⇒ 所以**统一入口必须能分发**，否则订阅了
      ``im.message.receive_v1`` 也会打到这里，然后被当成审批事件
      解析 → 找不到计划 → 事件被丢弃，**机器人永远收不到员工消息**。

    ⚠️ **为什么放在 Token 校验之后**：
    机器人事件与审批事件**共用同一个 Verification Token**
    （应用级，见 FS-3 的 C2 修正）。校验放在分发之前，
    保证「来源不可信」的事件在任何处理器里都进不去。

    Returns:
        ``dict`` —— 是机器人事件且已处理（含忽略）。
        ``None`` —— 不是机器人事件，调用方应继续走审批回流。
    """
    # ⚠️ 延迟导入：避免审批路由与机器人路由**循环导入**
    #   （机器人路由本身不导入审批路由，但反向导入容易埋雷）。
    from .feishu_bot import (
        EVENT_CARD_ACTION,
        EVENT_MESSAGE_RECEIVE,
        extract_event_type,
        receive_bot_event_body,
    )

    event_type = extract_event_type(body)
    if event_type not in (EVENT_MESSAGE_RECEIVE, EVENT_CARD_ACTION):
        return None

    logger.info("统一入口分发：命中机器人事件类型 %s", event_type)
    # ⚠️ 复用机器人端点自己的处理逻辑 —— **不复制一份**。
    #   复制会导致两边行为漂移（比如幂等键规则改了只改一处）。
    #
    # ⚠️ `receive_bot_event_body` 是 **async** 函数（返回协程）——
    #   因为 `performance_qa` 走 AI 问答，内部要等 L3（最长 35 秒）。
    #   同步调用会**堵住事件循环** —— 整个后端（包括其他人的请求）都卡住。
    #   ⇒ 这里**必须 await**。
    return await receive_bot_event_body(db, body)


@router.post("/approval/events")
async def receive_approval_event(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """接收飞书审批状态变更事件，触发审批回流。

    **无需登录态**（调用方是飞书服务器），但必须通过来源校验。

    响应体遵循飞书 webhook 约定：返回 `{"code": 0}` 表示成功。
    """
    expected_token = settings.feishu_approval_webhook_token
    if not expected_token:
        # 未配置 → 端点不可用。**绝不能放行**，否则任何人都能改审批状态。
        logger.error("飞书审批 webhook 收到事件但未配置 FEISHU_APPROVAL_WEBHOOK_TOKEN")
        raise HTTPException(
            status_code=503,
            detail="飞书审批 webhook 未配置 Verification Token",
        )

    # ── 1. 取原始 body（签名校验必须用原始字节）───────────────
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="请求体为空")

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体不是 JSON 对象")

    encrypt_key = settings.feishu_encrypt_key

    # ── 2. 加密模式：先验签，再解密 ────────────────────────────
    if isinstance(body.get("encrypt"), str) and body["encrypt"]:
        if not encrypt_key:
            # 收到密文却没配密钥 → 无法解密。**不能猜**，直接拒绝。
            logger.error("飞书审批 webhook 收到加密事件但未配置 FEISHU_ENCRYPT_KEY")
            raise HTTPException(status_code=503, detail="未配置 Encrypt Key，无法解密事件")

        # 签名校验（可不解密就完成来源校验，官方推荐）
        timestamp = request.headers.get(feishu_event.HEADER_TIMESTAMP, "")
        nonce = request.headers.get(feishu_event.HEADER_NONCE, "")
        signature = request.headers.get(feishu_event.HEADER_SIGNATURE, "")
        if not feishu_event.verify_signature(
            encrypt_key=encrypt_key,
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            raw_body=raw_body,
        ):
            logger.warning("飞书审批 webhook 签名校验失败")
            raise HTTPException(status_code=401, detail="签名校验失败")

        try:
            plain = feishu_event.decrypt_event(encrypt_key, body["encrypt"])
        except feishu_event.FeishuEventError as exc:
            logger.warning("飞书审批 webhook 解密失败：%s", exc)
            raise HTTPException(status_code=400, detail=f"事件解密失败：{exc}") from exc

        try:
            body = json.loads(plain)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="解密结果不是合法 JSON") from exc

    # ── 3. 握手：飞书保存回调地址时的 url_verification ──────────
    # 必须在 1 秒内原样返回 challenge，否则飞书保存不了地址。
    if feishu_event.is_url_verification(body):
        challenge = feishu_event.extract_challenge(body)
        if not challenge:
            raise HTTPException(status_code=400, detail="握手请求缺少 challenge")
        logger.info("飞书回调地址校验握手通过")
        return {"challenge": challenge}

    # ── 4. 校验 Verification Token ─────────────────────────────
    event = feishu_event.parse_event(body)
    if not event.token or event.token != expected_token:
        logger.warning("飞书审批 webhook token 校验失败")
        raise HTTPException(status_code=401, detail="Verification Token 校验失败")

    # ── 4.5 ⚠️⚠️ **统一入口的事件类型分发**（2026-11-17 新增）────────
    #
    #   **为什么必须有这一步**：
    #   飞书官方：「**每个应用只能配置一个请求地址，该应用订阅的所有事件
    #   都会发送到该请求地址**。」⇒ 审批 / 通讯录 / 机器人**共用**一个地址。
    #
    #   实测现状（2026-11-17）：
    #     - 后台请求地址 → 指向本端点（审批 Token 已配、握手通；通讯录 Token 却是空的）
    #     - 机器人端点 `/v1/feishu/bot/events` **收不到任何事件**
    #       —— 因为飞书只往这一个地址发。
    #
    #   ⇒ 若不在此分发：订阅了 `im.message.receive_v1` 也会打到这里，
    #     然后被当成审批事件解析 → 找不到计划 → 事件被丢弃，
    #     **机器人永远收不到员工消息**。
    #
    #   ⇒ 分发后：机器人事件转到机器人处理器，其余仍走审批回流。
    #     **不改**现有审批链路的行为（审批事件类型不匹配 → 原样处理）。
    bot_result = await _try_dispatch_to_bot(db, body)
    if bot_result is not None:
        return bot_result

    # ── 5. 解析事件字段 ────────────────────────────────────────
    plan_type = event.plan_type
    plan_id = event.plan_id
    correlation_key = event.correlation_key
    status_value = event.instance_status
    instance_id = event.instance_code or event.event_id

    # 计划标识可以来自事件本身，也可以由绑定表反查（飞书侧发起的场景）。
    # 三者都拿不到 → 无法定位计划，记日志忽略（不报错，避免飞书反复重试）。
    if not (plan_type and plan_id) and not (instance_id or correlation_key):
        logger.warning("飞书审批 webhook 事件缺少计划标识与绑定线索，跳过")
        return {"code": 0, "message": "事件缺少计划标识，已忽略"}

    # ── 6. 回流 ────────────────────────────────────────────────
    try:
        result = feishu_approval_sync.apply_feishu_approval_event(
            db,
            plan_type=plan_type,
            plan_id=plan_id,
            feishu_status=status_value,
            instance_id=instance_id,
            correlation_key=correlation_key,
            approval_code=event.approval_code,
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
        "飞书审批 webhook 处理完成：plan=%s instance=%s status=%s result=%s",
        plan_id or "-",
        instance_id or "-",
        status_value or "-",
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


# ===========================================================================
# 4. 定时补偿（FS-7）
# ===========================================================================


@router.get("/approval/compensation/status")
def compensation_status(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """审批补偿调度器的运行态（是否在跑、上次结果、连续失败次数）。"""
    _require_admin(db, current_user)
    from ..services.feishu_approval_compensation_scheduler import get_scheduler_status

    return {"code": 0, "data": get_scheduler_status()}


@router.post("/approval/compensation/run")
def compensation_run(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """手工触发一轮审批补偿。

    ⚠️ **运维手段** —— webhook 漏推导致计划卡在审批中时，
    不必等定时器（默认 10 分钟一轮）自然恢复。
    """
    _require_admin(db, current_user)
    from ..services.feishu_approval_compensation_scheduler import run_compensation

    try:
        result = run_compensation()
    except Exception as exc:  # noqa: BLE001
        logger.exception("手工触发审批补偿失败")
        raise HTTPException(status_code=503, detail="审批补偿执行失败") from exc
    return {"code": 0, "data": result}
