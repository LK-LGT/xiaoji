"""Alembic 迁移环境。

在线命令要求进程显式提供 DATABASE_URL 和独立核对的 ALEMBIC_EXPECT_TARGET。

`render_as_batch=True` 是 SQLite 兼容的关键：Alembic 会用「建新表 → 拷数据 →
删旧表 → 改名」的方式模拟 ALTER，从而支持 SQLite 不具备的 ADD CONSTRAINT。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

# 允许从 backend/ 目录直接执行 `alembic` 命令。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.models import Base  # noqa: E402

config = context.config


def confirmed_database_url() -> URL:
    raw_url = os.environ.get("DATABASE_URL", "").strip()
    expected = os.environ.get("ALEMBIC_EXPECT_TARGET", "").strip()
    if not raw_url or not expected:
        raise SystemExit("Alembic 需要显式设置 DATABASE_URL 和 ALEMBIC_EXPECT_TARGET")
    try:
        url = make_url(raw_url)
        if url.get_backend_name() == "sqlite" and url.database and not url.query:
            path = Path(url.database)
            if path.is_absolute() and not url.host:
                if expected == f"sqlite:{path.resolve().as_posix()}":
                    return url.set(database=path.resolve().as_posix())
        if url.get_backend_name() == "postgresql" and url.host and url.port and url.database:
            # 仅允许 TLS 参数，避免连接串通过 service、hostaddr 等参数改写目标。
            allowed_query = {"sslmode", "sslcert", "sslkey", "sslrootcert", "sslcrl"}
            if (
                not set(url.query) - allowed_query
                and "," not in url.host
                and not url.host.startswith(("/", "\\"))
                and "=" not in url.database
                and "://" not in url.database
                and not os.environ.get("PGHOSTADDR")
                and not os.environ.get("PGSERVICE")
            ):
                if expected == f"postgresql:{url.host}:{url.port}/{url.database}":
                    return url
    except (ArgumentError, OSError, ValueError):
        pass
    raise SystemExit("Alembic 目标与确认值不一致，或连接地址格式不受支持")


if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    context.configure(
        url=confirmed_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。"""
    connectable = create_engine(confirmed_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
