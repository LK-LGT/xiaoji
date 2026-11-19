"""站内消息 API；收件人始终从认证会话推导。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import build_access_context_for_user, get_current_user
from ..domain.models import Employee
from ..domain.notification_models import Notification
from ..services.notification_service import now, serialize

router = APIRouter(prefix="/v1/messages", tags=["消息"])


@router.get("/summary")
async def get_messages_summary(
    current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    rows = db.query(Notification).filter_by(employee_id=current_user.id, read_at=None).all()
    badges: dict[str, int] = {}
    for row in rows:
        if row.message_type == "TODO" and row.route:
            badges[row.route] = badges.get(row.route, 0) + 1
    return {"code": 0, "data": {
        "unreadCount": len(rows), "pendingTodoCount": sum(r.message_type == "TODO" for r in rows),
        "unreadNoticeCount": sum(r.message_type != "TODO" for r in rows),
        "menuBadges": badges,
        "unreadMessages": [serialize(r) for r in sorted(rows, key=lambda r: r.created_at, reverse=True)[:10]],
    }}


@router.get("")
async def list_messages(
    pageNo: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100),
    page: int | None = Query(None, ge=1), page_size: int | None = Query(None, ge=1, le=100),
    messageType: str | None = None, unreadOnly: bool = False,
    current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    page_no, size = page or pageNo, page_size or pageSize
    base = db.query(Notification).filter_by(employee_id=current_user.id)
    unread_count = base.filter(Notification.read_at.is_(None)).count()
    query = base
    if messageType:
        query = query.filter_by(message_type="NOTICE" if messageType == "INFO" else messageType)
    if unreadOnly:
        query = query.filter(Notification.read_at.is_(None))
    total = query.count()
    rows = query.order_by(Notification.created_at.desc(), Notification.id).offset((page_no - 1) * size).limit(size).all()
    return {"code": 0, "data": {
        "records": [serialize(row) for row in rows], "total": total,
        "pageNo": page_no, "pageSize": size, "unreadCount": unread_count,
    }}


def _assert_hr(user: Employee, db: Session) -> None:
    if "hr" not in build_access_context_for_user(user, db).roles:
        raise HTTPException(status_code=403, detail="仅 HR 可管理通知投递")


@router.get("/delivery-logs")
async def delivery_logs(
    status: str | None = None, page: int = Query(1, ge=1),
    current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    _assert_hr(current_user, db)
    query = db.query(Notification)
    if status:
        query = query.filter_by(status=status)
    total = query.count()
    rows = query.order_by(Notification.created_at.desc()).offset((page - 1) * 50).limit(50).all()
    return {"code": 0, "data": {"total": total, "records": [
        {**serialize(r), "deliveryStatus": r.status, "attempts": r.attempts,
         "lastError": r.last_error, "skipReason": r.skip_reason, "employeeId": r.employee_id}
        for r in rows
    ]}}


@router.post("/{message_id}/retry")
async def retry_delivery(
    message_id: str, current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    _assert_hr(current_user, db)
    count = db.query(Notification).filter(
        Notification.id == message_id, Notification.status.in_(["failed", "skipped"]),
    ).update({"status": "pending", "last_error": None, "skip_reason": None})
    if not count:
        raise HTTPException(status_code=409, detail="通知不存在或当前状态不能重推")
    return {"code": 0, "data": {"status": "pending"}}


@router.post("/read-all")
async def mark_all_as_read(
    current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    count = db.query(Notification).filter_by(employee_id=current_user.id, read_at=None).update({"read_at": now()})
    return {"code": 0, "data": {"updated": count}}


@router.post("/{message_id}/read")
async def mark_as_read(
    message_id: str, current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    row = db.query(Notification).filter_by(id=message_id, employee_id=current_user.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    if row.read_at is None:
        row.read_at = now()
    return {"code": 0, "data": {"message": "已标记为已读"}}
