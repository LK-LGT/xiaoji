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
    """建表（幂等）。"""
    Base.metadata.create_all(get_engine())


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
