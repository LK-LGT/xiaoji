"""消息 API 路由。"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db

router = APIRouter(prefix="/v1/messages", tags=["消息"])


@router.get("/summary")
async def get_messages_summary(
    current_user_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取消息汇总（未读消息数等）。"""
    return {
        "code": 0,
        "data": {
            "unread_count": 0,
            "total_count": 0,
            "messages": [],
        },
    }


@router.get("")
async def list_messages(
    current_user_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """获取消息列表。"""
    return {
        "code": 0,
        "data": {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
        },
    }


@router.post("/{message_id}/read")
async def mark_as_read(
    message_id: str,
    current_user_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """标记消息已读。"""
    return {"code": 0, "data": {"message": "已标记为已读"}}


@router.post("/read-all")
async def mark_all_as_read(
    current_user_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """标记所有消息为已读。"""
    return {"code": 0, "data": {"message": "所有消息已标记为已读"}}
