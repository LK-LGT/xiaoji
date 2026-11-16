"""飞书通讯录事件接收 API（R18 事件驱动增量）。

端点分两类：

1. **Webhook 接收端点**（``/v1/feishu/contact/events``）
   —— 飞书通讯录变更（``contact.user.*`` / ``contact.department.*``）时
   回调本系统，**入队**后立即返回。
   **不要求登录态**（飞书服务器调用），但**必须校验 Verification Token**。

2. **管理端点**（管理员查队列状态 / 手动触发消费 / 重试死信）
   —— 运维排查「事件有没有进来」「为什么没生效」。

安全基线
--------
复用审批 webhook 的同一套做法：

- **Verification Token 比对**：请求体 ``token`` 必须等于配置值；
- **未配置 → 503 不放行**：绝不能出现未鉴权的写入入口；
- 载荷**脱敏**后落库，不存 Token/Secret。

⚠️ **事件只是快路径**
--------------------
飞书事件推送**可能丢失**。分页游标全量对账仍是兜底，
保证最终一致。绝不能只靠事件 —— 丢一个事件就永久丢一条数据。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee, OrgChangeQueue
from ..exceptions import PermissionDenied
from ..services.access_control import PermissionAction, build_access_context
from ..services.feishu_client import FeishuClient
from ..services.org_change_consumer import OrgChangeConsumer
from ..services.org_change_queue import (
    build_dedup_key,
    enqueue_event,
    parse_contact_event,
    queue_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feishu", tags=["feishu-contact"])


def _require_admin(db: Session, current_user: Employee) -> None:
    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.SYNC_ORGANIZATION):
        raise PermissionDenied(
            user_id=current_user.id,
            action="manage_feishu_organization",
            resource="Feishu organization",
        )


def _client() -> FeishuClient:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(status_code=503, detail="飞书应用配置未完成")
    return FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )


# ===========================================================================
# 1. Webhook 接收端点
# ===========================================================================


@router.post("/contact/events")
async def receive_contact_event(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """接收飞书通讯录变更事件，入队后立即返回。

    **无需登录态**（调用方是飞书服务器），但必须通过 Verification Token 校验。

    ⚠️ **为什么只入队不直接改数据**：
    - 飞书要求回调**快速返回**，直接做业务容易超时 → 飞书反复重推；
    - 事件可能**乱序**到达，需要串行处理；
    - 处理失败要能**重试**，不能丢。

    响应体遵循飞书 webhook 约定：返回 ``{"code": 0}`` 表示成功。
    """
    # ── 1. 校验 Verification Token ──────────────────────────────
    expected_token = settings.feishu_contact_event_token
    if not expected_token:
        # 未配置 → 端点不可用。**绝不能放行**，
        # 否则任何人都能伪造通讯录变更把员工改成离职。
        logger.error(
            "飞书通讯录 webhook 收到事件但未配置 FEISHU_CONTACT_EVENT_TOKEN"
        )
        raise HTTPException(
            status_code=503,
            detail="飞书通讯录 webhook 未配置 Verification Token",
        )

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    # ⚠️ 飞书 v2 事件的 token 在 header 里，v1 在顶层。
    header = body.get("header") or {}
    token = str(body.get("token") or header.get("token") or "")
    if not token or token != expected_token:
        logger.warning("飞书通讯录 webhook token 校验失败")
        raise HTTPException(status_code=401, detail="Verification Token 校验失败")

    # ── 2. 解析事件 ──────────────────────────────────────────────
    parsed = parse_contact_event(body)
    if parsed is None:
        # ⚠️ 不认识的事件类型 / 缺少资源 ID —— 返回 200 让飞书**别再重推**。
        #    返回错误只会导致飞书反复推同一个我们处理不了的事件。
        logger.info(
            "忽略无法解析的通讯录事件: type=%s",
            (body.get("header") or {}).get("event_type") or body.get("type"),
        )
        return {"code": 0, "message": "事件已忽略"}

    event_type, resource_type, resource_id, payload = parsed

    # ── 3. 入队 ──────────────────────────────────────────────────
    event_id = str(header.get("event_id") or "") or None
    dedup_key = build_dedup_key(
        event_type,
        resource_type,
        resource_id,
        event_id=event_id,
    )
    try:
        row, created = enqueue_event(
            db,
            event_type,
            resource_type,
            resource_id,
            payload,
            dedup_key=dedup_key,
        )
    except Exception as exc:
        # ⚠️ 入队失败必须报错（5xx），让飞书**重推** ——
        #    静默返回 200 就等于丢事件。
        logger.exception("通讯录事件入队失败: %s", exc)
        raise HTTPException(status_code=500, detail="事件入队失败") from exc

    if not created:
        logger.info(
            "通讯录事件重复，已去重: type=%s resource=%s key=%s",
            event_type,
            resource_id,
            dedup_key,
        )
        return {"code": 0, "message": "事件重复已去重"}

    logger.info(
        "通讯录事件已入队: id=%s type=%s resource=%s",
        row.id,
        event_type,
        resource_id,
    )
    return {"code": 0, "message": "ok", "data": {"queueId": row.id}}


# ===========================================================================
# 2. 管理端点
# ===========================================================================


@router.get("/contact/events/stats")
def get_contact_event_stats(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """事件队列状态统计（运维视图）。"""
    _require_admin(db, current_user)
    return {"code": 0, "data": queue_stats(db)}


@router.get("/contact/events")
def list_contact_events(
    status: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    eventType: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resourceType: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """事件队列列表（供排查「某个变更有没有进来」）。"""
    _require_admin(db, current_user)
    size = pageSize if pageSize is not None else page_size
    query = db.query(OrgChangeQueue)
    if status:
        query = query.filter(OrgChangeQueue.status == status)
    etype = eventType if eventType is not None else event_type
    if etype:
        query = query.filter(OrgChangeQueue.event_type == etype)
    rtype = resourceType if resourceType is not None else resource_type
    if rtype:
        query = query.filter(OrgChangeQueue.resource_type == rtype)

    total = query.count()
    rows = (
        query.order_by(OrgChangeQueue.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "code": 0,
        "data": {
            "total": total,
            "page": page,
            "pageSize": size,
            "items": [
                {
                    "id": row.id,
                    "eventType": row.event_type,
                    "resourceType": row.resource_type,
                    "resourceId": row.resource_id,
                    "status": row.status,
                    "retryCount": row.retry_count,
                    "errorMessage": row.error_message,
                    "batchId": row.batch_id,
                    "createdAt": row.created_at.isoformat() if row.created_at else None,
                    "processedAt": (
                        row.processed_at.isoformat() if row.processed_at else None
                    ),
                }
                for row in rows
            ],
        },
    }


@router.post("/contact/events/consume")
def consume_contact_events(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """手动触发一次队列消费。

    ⚠️ 正常情况下由后台调度器自动消费；这个端点用于
    「刚收到事件但想立刻生效」「排查队列卡住」的场景。
    """
    _require_admin(db, current_user)
    client = _client()
    consumer = OrgChangeConsumer(db, client)
    result = consumer.process_pending()
    return {"code": 0, "data": result}


@router.post("/contact/events/{event_id}/retry")
def retry_contact_event(
    event_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """把死信事件重新排回待处理。"""
    _require_admin(db, current_user)
    row = db.get(OrgChangeQueue, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    row.status = "pending"
    row.retry_count = 0
    row.next_retry_at = None
    row.error_message = None
    db.commit()
    return {"code": 0, "data": {"id": row.id, "status": row.status}}
