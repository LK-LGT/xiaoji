"""组织架构变更事件队列（R18 事件驱动增量）。

飞书通讯录事件（``contact.user.*`` / ``contact.department.*``）推送到
webhook 后，**先落库成队列项**，再由后台消费者按队列增量 upsert。

设计要点
--------
1. **webhook 快速返回** —— 飞书对回调有超时要求，直接做业务会超时重推。
   这里只做「校验 + 入队」，微秒级返回。
2. **串行消费** —— 事件可能乱序（先删后建、先改名后移动），
   消费者按创建顺序单条处理，避免状态错乱。
3. **可重试** —— 处理失败按退避重排，超过上限进死信，不静默丢弃。
4. **事件只是快路径** —— 飞书事件可能丢失。现有分页游标全量对账
   仍是兜底，保证最终一致。**绝不能只靠事件。**

去重
----
飞书重推时带同一个 ``event_id``，用它做去重键最精准。
没有 ``event_id`` 时退化为「资源 + 事件类型 + 时间片」——
同一个资源在同一时间片内的重复推送只处理一次。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.models import OrgChangeQueue

logger = logging.getLogger(__name__)

# ── 事件类型 ────────────────────────────────────────────────────────────
# 飞书通讯录事件名 -> 本地事件类型。
EVENT_USER_CREATED = "user_created"
EVENT_USER_UPDATED = "user_updated"
EVENT_USER_DELETED = "user_deleted"
EVENT_DEPARTMENT_CREATED = "department_created"
EVENT_DEPARTMENT_UPDATED = "department_updated"
EVENT_DEPARTMENT_DELETED = "department_deleted"

#: 飞书事件名 -> 本地事件类型。
#: ⚠️ 飞书的通讯录事件命名以 ``contact.`` 开头。
FEISHU_EVENT_MAP: dict[str, str] = {
    "contact.user.created_v3": EVENT_USER_CREATED,
    "contact.user.updated_v3": EVENT_USER_UPDATED,
    "contact.user.deleted_v3": EVENT_USER_DELETED,
    "contact.department.created_v3": EVENT_DEPARTMENT_CREATED,
    "contact.department.updated_v3": EVENT_DEPARTMENT_UPDATED,
    "contact.department.deleted_v3": EVENT_DEPARTMENT_DELETED,
    # 兼容旧版事件名（不带 _v3 后缀）。
    "contact.user.created": EVENT_USER_CREATED,
    "contact.user.updated": EVENT_USER_UPDATED,
    "contact.user.deleted": EVENT_USER_DELETED,
    "contact.department.created": EVENT_DEPARTMENT_CREATED,
    "contact.department.updated": EVENT_DEPARTMENT_UPDATED,
    "contact.department.deleted": EVENT_DEPARTMENT_DELETED,
}

#: 事件类型 -> 资源类型。
EVENT_RESOURCE_TYPE: dict[str, str] = {
    EVENT_USER_CREATED: "user",
    EVENT_USER_UPDATED: "user",
    EVENT_USER_DELETED: "user",
    EVENT_DEPARTMENT_CREATED: "department",
    EVENT_DEPARTMENT_UPDATED: "department",
    EVENT_DEPARTMENT_DELETED: "department",
}

#: 删除类事件 —— 处理时走「置为离职 / 停用」而非 upsert。
DELETED_EVENT_TYPES: frozenset[str] = frozenset(
    {EVENT_USER_DELETED, EVENT_DEPARTMENT_DELETED}
)

# ── 队列状态 ────────────────────────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"
STATUS_DEAD_LETTER = "dead_letter"

#: 单条事件最大重试次数，超过进死信。
MAX_RETRY_COUNT = 5

#: 退避序列（秒）。
RETRY_BACKOFF_SECONDS: tuple[int, ...] = (10, 30, 120, 600, 1800)

#: 去重时间窗（秒）。同一资源同一类型在该窗口内的重复推送只处理一次。
DEDUP_WINDOW_SECONDS = 300


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _retry_backoff_seconds(attempt: int) -> int:
    """按重试次数返回退避秒数（超出上限取最大值）。"""
    index = max(0, min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1))
    return RETRY_BACKOFF_SECONDS[index]


def _is_postgres(db: Session) -> bool:
    """判断当前连接是不是 PostgreSQL。

    ⚠️ PostgreSQL 与 SQLite 在并发语义上**根本不同**：
    - SQLite 有全局写锁 → 天然串行；
    - PostgreSQL 是 MVCC + 行级锁 → 必须显式用
      ``SELECT ... FOR UPDATE SKIP LOCKED`` 才能安全地「领取队列」。

    所以凡是涉及「多实例并发领取」的逻辑都必须区分这两种方言。
    """
    try:
        return db.bind is not None and db.bind.dialect.name == "postgresql"
    except Exception:  # pragma: no cover - 兜底判断
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 事件解析
# ═══════════════════════════════════════════════════════════════════════════


def parse_contact_event(body: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]] | None:
    """解析飞书通讯录事件，提取 (事件类型, 资源类型, 资源ID, 载荷)。

    ⚠️ 飞书事件 v2 的载荷结构::

        {
          "schema": "2.0",
          "header": {
            "event_id": "...",
            "event_type": "contact.user.updated_v3",
            "create_time": "1700000000000",
            "token": "...",
            "app_id": "...",
            "tenant_key": "..."
          },
          "event": {
            "user": {"user_id": "...", "open_id": "...", ...},
            "department": {"department_id": "...", ...}
          }
        }

    旧版（v1）结构没有 ``header``，事件名在 ``type`` 字段、载荷直接在顶层。

    Returns:
        解析成功返回四元组；事件类型不认识或缺少资源 ID 返回 ``None``。
    """
    header = body.get("header") or {}
    event_type_raw = str(
        header.get("event_type") or body.get("type") or body.get("event_type") or ""
    ).strip()
    if not event_type_raw:
        return None

    local_event_type = FEISHU_EVENT_MAP.get(event_type_raw)
    if local_event_type is None:
        # 不认识的事件类型 —— 返回 None，调用方记录后忽略（不报错，
        # 避免飞书因为我们不认识某个事件而反复重推）。
        return None

    resource_type = EVENT_RESOURCE_TYPE[local_event_type]
    event_body = body.get("event") or {}

    resource_id = ""
    payload: dict[str, Any] = {}
    if resource_type == "user":
        # ⚠️ **v1 兼容**：老版事件的载荷直接在顶层，没有 ``event`` 包装。
        #    两处都取，顶层优先（有 ``event`` 就用 ``event`` 里的）。
        user = event_body.get("user") or body.get("user") or {}
        # ⚠️ 字段名在不同版本事件里可能是 user_id / open_id / union_id。
        resource_id = str(
            user.get("user_id") or user.get("open_id") or user.get("union_id") or ""
        ).strip()
        payload = {"user": dict(user)}
    else:
        # ⚠️ 同上：v1 的部门载荷在顶层。
        department = event_body.get("department") or body.get("department") or {}
        resource_id = str(department.get("department_id") or "").strip()
        payload = {"department": dict(department)}

    if not resource_id:
        return None

    return local_event_type, resource_type, resource_id, payload


def build_dedup_key(
    event_type: str,
    resource_type: str,
    resource_id: str,
    *,
    event_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """构造去重键。

    ⚠️ 优先用飞书的 ``event_id`` —— 它在重推时保持不变，是最精准的判据。
    没有 ``event_id`` 时退化为「资源 + 类型 + 时间片」：
    同一资源在同一时间片内的重复推送只处理一次，
    但时间片过后同一资源的再次变更仍会被处理。
    """
    if event_id:
        return f"eid:{event_id}"

    moment = created_at or _now()
    bucket = int(moment.timestamp()) // DEDUP_WINDOW_SECONDS
    return f"w:{resource_type}:{resource_id}:{event_type}:{bucket}"


# ═══════════════════════════════════════════════════════════════════════════
# 入队
# ═══════════════════════════════════════════════════════════════════════════


def enqueue_event(
    db: Session,
    event_type: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    dedup_key: str | None = None,
) -> tuple[OrgChangeQueue | None, bool]:
    """把事件加入队列。

    Returns:
        ``(队列项, 是否新建)``。若被去重，返回 ``(None, False)``。
    """
    key = dedup_key or build_dedup_key(event_type, resource_type, resource_id)

    # ⚠️ 先查是否已存在同去重键的**未完成**记录。
    #    只在「未完成」范围内去重 —— 已处理完的历史记录不影响新事件，
    #    否则同一资源第二次变更会被永久丢弃。
    existing = db.execute(
        select(OrgChangeQueue).where(
            OrgChangeQueue.dedup_key == key,
            OrgChangeQueue.status.in_(
                [STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED]
            ),
        )
    ).scalars().first()
    if existing is not None:
        return None, False

    row = OrgChangeQueue(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        status=STATUS_PENDING,
        dedup_key=key,
        payload_json=payload,
        retry_count=0,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # ⚠️ **并发去重兜底**：上面的「查重 → 插入」是 check-then-act，
        #    两个请求可能同时查不到、然后都插入 → 产生重复队列项。
        #
        #    SQLite 有全局写锁，窗口被掩盖；
        #    PostgreSQL 没有 —— 同一 event_id 的重复推送会真的插两条，
        #    导致同一变更被消费两次（重复回查飞书、重复 upsert）。
        #
        #    这里靠 **数据库唯一约束**兜底：插入冲突时说明别人已经
        #    插入了同一条 → 视为已去重。
        db.rollback()
        existing = db.execute(
            select(OrgChangeQueue).where(
                OrgChangeQueue.dedup_key == key,
                OrgChangeQueue.status.in_(
                    [STATUS_PENDING, STATUS_PROCESSING, STATUS_FAILED]
                ),
            )
        ).scalars().first()
        if existing is not None:
            return None, False
        raise
    db.refresh(row)
    return row, True


# ═══════════════════════════════════════════════════════════════════════════
# 消费
# ═══════════════════════════════════════════════════════════════════════════


def claim_pending_events(db: Session, limit: int = 50) -> list[OrgChangeQueue]:
    """取出待处理的事件（按创建时间排序，保证串行顺序）。

    ⚠️ **为什么按创建时间排序**：事件可能乱序到达。
    「先改名后移动」与「先移动后改名」必须按实际发生顺序处理，
    否则最终状态会错。飞书事件带 ``create_time``，入队时已按到达顺序
    落库，这里用 ``created_at`` 近似。

    ⚠️ **为什么用 ``FOR UPDATE SKIP LOCKED``（PostgreSQL）**
    ---------------------------------------------------------
    多实例部署时，每个实例都会跑消费者。若只是
    「SELECT 出来 → 改成 processing → COMMIT」，
    两个实例会**同时读到同一批记录** —— 这是典型的
    check-then-act 竞态。

    SQLite 上因为有全局写锁，这个窗口被掩盖了；
    但 **PostgreSQL 没有全局写锁**，两个实例会真的同时领取
    同一批事件 → 重复回查飞书、重复 upsert、批次记录混乱。

    ``FOR UPDATE SKIP LOCKED`` 让 PG 在**行级**加锁：
    已被别的事务锁住的行**直接跳过**，各实例领到的是**互不重叠**的批次。

    SQLite 不支持该语法 → 退化为原实现（靠写锁串行化，本就安全）。
    """
    now = _now()
    stmt = (
        select(OrgChangeQueue)
        .where(
            OrgChangeQueue.status.in_([STATUS_PENDING, STATUS_FAILED]),
            (OrgChangeQueue.next_retry_at.is_(None))
            | (OrgChangeQueue.next_retry_at <= now),
        )
        .order_by(OrgChangeQueue.created_at.asc())
        .limit(limit)
    )
    if _is_postgres(db):
        # ⚠️ 行级锁 + 跳过已被锁定的行 —— 多实例安全的关键。
        stmt = stmt.with_for_update(skip_locked=True)

    rows = db.execute(stmt).scalars().all()

    claimed: list[OrgChangeQueue] = []
    for row in rows:
        row.status = STATUS_PROCESSING
        claimed.append(row)
    if claimed:
        db.commit()
    return claimed


def mark_processed(db: Session, row: OrgChangeQueue) -> None:
    """标记事件处理完成。"""
    row.status = STATUS_PROCESSED
    row.processed_at = _now()
    row.error_message = None
    db.commit()


def mark_failed(db: Session, row: OrgChangeQueue, error: str) -> str:
    """标记事件处理失败，按退避重排或进死信。

    Returns:
        新的状态（``pending`` / ``dead_letter``）。
    """
    row.retry_count = (row.retry_count or 0) + 1
    row.error_message = error[:2000]

    if row.retry_count >= MAX_RETRY_COUNT:
        row.status = STATUS_DEAD_LETTER
        row.next_retry_at = None
        new_status = STATUS_DEAD_LETTER
    else:
        row.status = STATUS_FAILED
        row.next_retry_at = _now() + timedelta(
            seconds=_retry_backoff_seconds(row.retry_count)
        )
        new_status = STATUS_FAILED

    db.commit()
    return new_status


def release_stale_processing(db: Session, stale_seconds: int = 900) -> int:
    """把僵死的 ``processing`` 记录重置为 ``pending``。

    ⚠️ **为什么需要**：消费者进程在处理中途崩溃（被 kill、OOM）时，
    记录会永远停在 ``processing`` —— 没人再碰它，事件就静默丢了。
    定期把超过 ``stale_seconds`` 没动静的记录重置，让它重跑。
    """
    cutoff = _now() - timedelta(seconds=stale_seconds)
    rows = db.execute(
        select(OrgChangeQueue).where(
            OrgChangeQueue.status == STATUS_PROCESSING,
            OrgChangeQueue.created_at < cutoff,
        )
    ).scalars().all()
    for row in rows:
        row.status = STATUS_PENDING
        row.next_retry_at = None
    if rows:
        db.commit()
    return len(rows)


# ═══════════════════════════════════════════════════════════════════════════
# 统计与运维
# ═══════════════════════════════════════════════════════════════════════════


def queue_stats(db: Session) -> dict[str, int]:
    """队列状态统计（运维视图）。"""
    rows = db.execute(
        select(OrgChangeQueue.status, func.count())
        .group_by(OrgChangeQueue.status)
    ).all()
    stats = {
        STATUS_PENDING: 0,
        STATUS_PROCESSING: 0,
        STATUS_PROCESSED: 0,
        STATUS_FAILED: 0,
        STATUS_DEAD_LETTER: 0,
    }
    for status, count in rows:
        stats[status] = count
    stats["total"] = sum(
        v for k, v in stats.items() if k != "total"
    )
    return stats


def pending_count(db: Session) -> int:
    """待处理事件数（用于健康检查）。"""
    return (
        db.execute(
            select(func.count())
            .select_from(OrgChangeQueue)
            .where(OrgChangeQueue.status == STATUS_PENDING)
        ).scalar_one()
        or 0
    )
