"""SQLite 轻量迁移：为已有表补齐模型中新增的列。

`Base.metadata.create_all()` 只能创建缺失的表，不能为已存在的表补列。
本模块根据 SQLAlchemy 模型元数据与实际数据库的差异，幂等地补齐列。

仅支持 SQLite（当前默认数据库）。生产环境如切换到 PostgreSQL，
应改用 Alembic 正式迁移。
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchTableError, OperationalError

from ..domain.models import Base

logger = logging.getLogger(__name__)


def _sqlite_type(column, engine: Engine) -> str:
    """把 SQLAlchemy 列类型转换为 SQLite 可用的 DDL 类型。"""
    try:
        return column.type.compile(dialect=engine.dialect)
    except Exception:  # pragma: no cover - 极端类型兜底
        return "TEXT"


def sync_schema(engine: Engine) -> list[str]:
    """补齐所有缺失列，返回实际执行的 ALTER 语句列表。"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    with engine.begin() as connection:
        for table_name, table in Base.metadata.tables.items():
            if table_name not in existing_tables:
                # 缺失的表交给 create_all 创建。
                continue

            try:
                existing_columns = {
                    col["name"] for col in inspector.get_columns(table_name)
                }
            except NoSuchTableError:
                # ⚠️ 表在「快照 → 反射」之间被别的连接删/建了
                #    （并发建表场景）⇒ 跳过这一张，交给下一轮 create_all。
                continue

            for column in table.columns:
                if column.name in existing_columns:
                    continue

                ddl_type = _sqlite_type(column, engine)
                statement = (
                    f'ALTER TABLE "{table_name}" '
                    f'ADD COLUMN "{column.name}" {ddl_type}'
                )
                try:
                    connection.execute(text(statement))
                except OperationalError as exc:
                    # ⚠️ 并发补列 ⇒ 另一连接已补好 ⇒ 不算失败。
                    if "duplicate column" not in str(exc).lower():
                        raise
                    logger.info("列已存在，跳过: %s", statement)
                    continue
                applied.append(statement)
                logger.info("补齐数据库列: %s", statement)

    if applied:
        logger.info("数据库结构同步完成，共补齐 %d 个列", len(applied))
    return applied


def init_db_with_migration(engine: Engine | None = None) -> None:
    """创建缺失表并补齐缺失列。"""
    from ..core.db import get_engine

    target_engine = engine or get_engine()
    Base.metadata.create_all(target_engine)
    sync_schema(target_engine)
