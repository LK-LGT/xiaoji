"""消息 API 路由。"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user

router = APIRouter(prefix="/v1/messages", tags=["消息"], dependencies=[Depends(get_current_user)])


def _empty_summary() -> dict[str, Any]:
    """返回前端 MessageSummary 契约的空值。

    前端 `src/api/messages.ts` 期望 camelCase 字段：
    unreadCount / pendingTodoCount / unreadNoticeCount /
    menuBadges / unreadMessages。
    历史实现返回的是 unread_count / total_count / messages，
    会让前端 `summary.menuBadges` 为 undefined，
    进而在 AppShell 渲染时抛错、导致整个布局 patch 中断。
    """
    return {
        "unreadCount": 0,
        "pendingTodoCount": 0,
        "unreadNoticeCount": 0,
        "menuBadges": {},
        "unreadMessages": [],
    }


@router.get("/summary")
async def get_messages_summary(
    current_user_id: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取消息汇总（未读消息数等）。"""
    return {
        "code": 0,
        "data": _empty_summary(),
    }


@router.get("")
async def list_messages(
    current_user_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """获取消息列表。"""
    return {
        "code": 0,
        "data": {
            "records": [],
            "total": 0,
            "pageNo": page,
            "pageSize": page_size,
            "unreadCount": 0,
        },
    }


@router.post("/{message_id}/read")
async def mark_as_read(
    message_id: str,
    current_user_id: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """标记消息已读。"""
    return {"code": 0, "data": {"message": "已标记为已读"}}


@router.post("/read-all")
async def mark_all_as_read(
    current_user_id: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """标记所有消息为已读。"""
    return {"code": 0, "data": {"updated": 0}}
