"""数据库会话与初始化。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..domain.models import Base
from .config import settings

_engine = None
_SessionLocal = None


def _ensure_dir() -> None:
    url = settings.database_url
    if url.startswith("sqlite"):
        # sqlite:///./data/perf.db → ./data
        raw = url.split("///")[-1]
        parent = Path(raw).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine
    if _engine is None:
        _ensure_dir()
        connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        _engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
        if settings.database_url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, future=True)
    return _SessionLocal


def init_db() -> None:
    """建表并补齐缺失列（幂等）。

    生产环境的 schema 由 Alembic 迁移管理（backend/migrations）。
    只有显式开启 AUTO_CREATE_TABLES 时才在运行时建表，
    避免 Alembic 与 create_all 互相干扰导致 schema 漂移。
    """
    import logging

    logger = logging.getLogger(__name__)

    if not settings.auto_create_tables:
        logger.debug("AUTO_CREATE_TABLES 未开启，schema 由 Alembic 管理")
        return

    from .migrations import sync_schema

    logger.warning("AUTO_CREATE_TABLES 已开启，仅建议在本地开发使用")
    engine = get_engine()
    Base.metadata.create_all(engine)
    sync_schema(engine)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖。"""
    sm = get_sessionmaker()
    db = sm()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
