"""数据库会话与初始化。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..domain.models import Base
from .config import settings

_engine = None
_SessionLocal = None

#: ⚠️ **建表 / 补列的进程内互斥锁**（2026-11-26）。
#: 多个 ``TestClient(app)`` 会并行触发 startup 钩子里的 ``init_db()``，
#: 而它们共用同一个 SQLite 文件 ⇒ 两个 engine 同时 ``create_all`` ⇒
#: ``table X already exists``（实测每次换一个表名）。
#: ⇒ 用这把锁把「建表 + 补列」串成临界区。
_init_lock = threading.Lock()

# SQLite 写锁等待超时（毫秒）。
# 后台同步线程与 HTTP 请求并发访问时，写方需要等待读事务释放。
SQLITE_BUSY_TIMEOUT_MS = 30_000


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
                # ⚠️ busy_timeout：SQLite 默认遇到锁立刻抛
                #    `OperationalError: database is locked`。
                #    后台同步线程与 HTTP 读请求并发时，写提交会因
                #    一次读事务而失败 —— 批次状态写不进去，永久停在 running。
                #    设置超时后写方会等待锁释放，而不是直接失败。
                cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
                # WAL：读写不互相阻塞，显著降低并发写冲突。
                cur.execute("PRAGMA journal_mode=WAL")
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

    ⚠️ **为什么这里要「建表 + 补列」分两步且都能重入**
    ``create_all`` 只建**缺失的**表；``sync_schema`` 只补**缺失的**列。
    两者都是幂等的，所以重复调用安全 —— 但**并发**调用不安全：
    两个 engine 同时对同一个 SQLite 文件跑 ``create_all`` 时，
    第二个会撞 ``table X already exists``（实测全量测试里
    ``feishu_identity`` / ``bot_message`` 轮流报错，每次换一个表名
    ⇒ 典型的**竞态**，不是某个具体模型的缺陷）。

    ⇒ 这里用一把**进程内锁**把「建表 + 补列」串起来，
    并对「表已存在」这类**并发建表**错误做一次重试。
    """
    logger = logging.getLogger(__name__)

    if not settings.auto_create_tables:
        logger.debug("AUTO_CREATE_TABLES 未开启，schema 由 Alembic 管理")
        return

    from .migrations import sync_schema

    logger.warning("AUTO_CREATE_TABLES 已开启，仅建议在本地开发使用")
    engine = get_engine()

    global _init_lock
    with _init_lock:
        for attempt in range(2):
            try:
                Base.metadata.create_all(engine, checkfirst=True)
                sync_schema(engine)
                return
            except OperationalError as exc:
                # ⚠️ 并发建表 ⇒ 表已被另一个连接建好 ⇒ 重试一次即可。
                message = str(exc).lower()
                if attempt == 0 and ("already exists" in message or "duplicate" in message):
                    logger.warning("并发建表冲突，重试一次: %s", exc)
                    continue
                raise


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
