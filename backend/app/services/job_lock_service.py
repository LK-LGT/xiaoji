"""定时任务分布式锁。

多实例部署时，同一个定时任务必须保证同一时刻只有一个实例执行。
本模块基于数据库表 `scheduled_job_lock` 实现，无需引入 Redis 等额外组件。

锁带有过期时间：若持有者因异常退出而没有释放锁，其他实例在锁过期后
可以抢占，避免任务被永久卡死。因此 TTL 必须大于任务的最坏耗时。
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.models import ScheduledJobLock

logger = logging.getLogger(__name__)

# 锁 TTL 默认值（秒）。应大于同步任务的最坏耗时。
DEFAULT_TTL_SECONDS = 3600


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def current_holder_id() -> str:
    """当前实例的唯一标识：主机名 + 进程号。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def _is_postgres(db: Session) -> bool:
    try:
        return db.bind is not None and db.bind.dialect.name == "postgresql"
    except Exception:  # pragma: no cover - 兜底判断
        return False


def acquire_job_lock(
    db: Session,
    job_name: str,
    holder_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    """尝试获取锁。

    返回 True 表示本次成功持有；返回 False 表示锁被其他实例持有。

    PostgreSQL 使用单条 INSERT ... ON CONFLICT 语句原子抢锁；
    SQLite 退化为「插入或更新」，仅适用于本地开发。
    """
    now = _now()
    expires = now + timedelta(seconds=ttl_seconds)

    if _is_postgres(db):
        return _acquire_job_lock_postgres(db, job_name, holder_id, now, expires)

    row = db.get(ScheduledJobLock, job_name)
    if row is None:
        try:
            db.add(
                ScheduledJobLock(
                    job_name=job_name,
                    locked_by=holder_id,
                    locked_at=now,
                    expires_at=expires,
                )
            )
            db.commit()
            return True
        except IntegrityError:
            # 并发插入：其他实例已抢到锁。
            db.rollback()
            return False

    if row.expires_at > now:
        return False  # 他人持有且未过期

    row.locked_by = holder_id
    row.locked_at = now
    row.expires_at = expires
    db.commit()
    return True


def _acquire_job_lock_postgres(
    db: Session,
    job_name: str,
    holder_id: str,
    now: datetime,
    expires: datetime,
) -> bool:
    """PostgreSQL 原子抢锁。

    仅当锁不存在或已过期时才允许被抢占，整个过程由单条语句完成，
    不会产生竞态。
    """
    result = db.execute(
        text(
            """
            INSERT INTO scheduled_job_lock (job_name, locked_by, locked_at, expires_at)
            VALUES (:job, :holder, :now, :expires)
            ON CONFLICT (job_name) DO UPDATE
                SET locked_by = :holder,
                    locked_at = :now,
                    expires_at = :expires
            WHERE scheduled_job_lock.expires_at <= :now
            """
        ),
        {"job": job_name, "holder": holder_id, "now": now, "expires": expires},
    )
    db.commit()
    return result.rowcount == 1


def release_job_lock(db: Session, job_name: str, holder_id: str) -> None:
    """释放锁。只有持有者本人才能释放，避免误删其他实例的锁。"""
    try:
        row = db.get(ScheduledJobLock, job_name)
        if row is not None and row.locked_by == holder_id:
            db.delete(row)
            db.commit()
    except Exception:  # pragma: no cover - 释放失败不应影响主流程
        db.rollback()
        logger.warning("释放定时任务锁失败: job=%s holder=%s", job_name, holder_id)


def get_job_lock(db: Session, job_name: str) -> ScheduledJobLock | None:
    """查询当前锁状态（用于运维排查）。"""
    return db.get(ScheduledJobLock, job_name)
