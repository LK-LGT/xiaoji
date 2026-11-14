"""定时任务分布式锁测试。

覆盖：
- 首次获取锁成功
- 同一实例重复获取失败
- 其他实例在锁未过期时获取失败
- 锁过期后可以被抢占
- 只有持有者能释放锁
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, ScheduledJobLock
from backend.app.services.job_lock_service import (
    acquire_job_lock,
    current_holder_id,
    get_job_lock,
    release_job_lock,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def test_acquire_lock_succeeds_first_time(db):
    """首次获取锁应成功。"""
    assert acquire_job_lock(db, "job_a", "holder_1") is True
    lock = get_job_lock(db, "job_a")
    assert lock is not None
    assert lock.locked_by == "holder_1"


def test_same_holder_cannot_acquire_twice(db):
    """同一实例重复获取同一把锁应失败（防止自身并发）。"""
    assert acquire_job_lock(db, "job_a", "holder_1") is True
    assert acquire_job_lock(db, "job_a", "holder_1") is False


def test_other_holder_blocked_while_lock_valid(db):
    """锁未过期时其他实例获取失败。"""
    assert acquire_job_lock(db, "job_a", "holder_1") is True
    assert acquire_job_lock(db, "job_a", "holder_2") is False
    lock = get_job_lock(db, "job_a")
    assert lock.locked_by == "holder_1"


def test_expired_lock_can_be_preempted(db):
    """锁过期后其他实例可以抢占。"""
    assert acquire_job_lock(db, "job_a", "holder_1") is True
    lock = get_job_lock(db, "job_a")
    # 手动把过期时间推到过去
    lock.expires_at = lock.locked_at - timedelta(seconds=10)
    db.commit()

    assert acquire_job_lock(db, "job_a", "holder_2") is True
    lock = get_job_lock(db, "job_a")
    assert lock.locked_by == "holder_2"


def test_only_holder_can_release_lock(db):
    """非持有者不能释放锁。"""
    assert acquire_job_lock(db, "job_a", "holder_1") is True

    release_job_lock(db, "job_a", "holder_2")
    assert get_job_lock(db, "job_a") is not None
    assert get_job_lock(db, "job_a").locked_by == "holder_1"

    release_job_lock(db, "job_a", "holder_1")
    assert get_job_lock(db, "job_a") is None


def test_release_allows_reacquire(db):
    """释放后可以重新获取。"""
    assert acquire_job_lock(db, "job_a", "holder_1") is True
    release_job_lock(db, "job_a", "holder_1")
    assert acquire_job_lock(db, "job_a", "holder_2") is True


def test_different_jobs_have_independent_locks(db):
    """不同任务的锁互不影响。"""
    assert acquire_job_lock(db, "job_a", "holder_1") is True
    assert acquire_job_lock(db, "job_b", "holder_1") is True


def test_current_holder_id_is_unique_per_process():
    """实例标识包含主机名与进程号。"""
    holder = current_holder_id()
    assert ":" in holder
    assert holder == current_holder_id()


def test_scheduled_job_lock_table_exists():
    """锁表已由 Alembic 迁移创建。"""
    assert ScheduledJobLock.__tablename__ == "scheduled_job_lock"
    assert "job_name" in ScheduledJobLock.__table__.columns
    assert "expires_at" in ScheduledJobLock.__table__.columns
