"""0011: 修复 organization_sync_batch.idempotency_key 的唯一索引

Revision ID: 0011_sync_batch_idempotency_unique
Revises: 0010_feishu_sync_hardening
Create Date: 2026-11-16

背景
----
``0010_feishu_sync_hardening`` 的意图是给 ``idempotency_key`` 建**唯一索引**
（注释原文：「同一幂等键只能对应一个批次」）。但实际执行顺序有 bug：

    1. 通用循环遍历 ``NEW_COLUMNS``，对 ``needs_index=True`` 的列建索引，
       且用的是 ``unique=False``
       → 先建出 ``ix_organization_sync_batch_idempotency_key``（**非唯一**）
    2. 后面专门处理幂等键的代码块检查
       ``if not _index_exists(..., index_name)``
       → 索引**已经存在**（第 1 步建的），于是**跳过**创建唯一版本

结果：真实库里该索引是 ``unique=0``，而 ORM 模型声明的是 ``unique=True`` ——
**幂等保证在真实库上根本没生效**。重复触发同一个幂等键会插入多个批次，
正是这条约束要防的事。

本迁移
------
- 幂等：索引不存在 → 建唯一索引；已存在但非唯一 → 删掉重建为唯一。
- 兼容 SQLite：``batch_alter_table`` 会走表重建路径。
- 先查重：若已有重复的 idempotency_key（约束缺失期间写入的），
  先去重（保留最早的一行 —— 幂等语义下**第一次**触发的批次才是权威批次，
  后续重复触发本应复用它），再建约束。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_sync_batch_idempotency_unique"
down_revision: str | Sequence[str] | None = "0010_feishu_sync_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "organization_sync_batch"
COLUMN = "idempotency_key"
INDEX = "ix_organization_sync_batch_idempotency_key"


def _index_exists(inspector, table_name: str, index_name: str) -> bool:
    try:
        return any(
            i.get("name") == index_name for i in inspector.get_indexes(table_name)
        )
    except Exception:
        return False


def _column_exists(inspector, table_name: str, column_name: str) -> bool:
    try:
        return any(
            c.get("name") == column_name for c in inspector.get_columns(table_name)
        )
    except Exception:
        return False


def _is_unique(inspector, table_name: str, index_name: str) -> bool:
    try:
        for i in inspector.get_indexes(table_name):
            if i.get("name") == index_name:
                return bool(i.get("unique"))
    except Exception:
        pass
    return False


def _dedupe(bind) -> None:
    """去掉重复的 idempotency_key。

    保留**最早**的一行：幂等语义下第一次触发的批次才是权威批次，
    后续重复触发本应复用它而不是新建。
    """
    rows = bind.execute(
        sa.text(
            f"SELECT {COLUMN}, COUNT(*) FROM {TABLE} "
            f"WHERE {COLUMN} IS NOT NULL GROUP BY {COLUMN} HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for key, count in rows:
        keep = bind.execute(
            sa.text(
                f"SELECT id FROM {TABLE} WHERE {COLUMN} = :k "
                f"ORDER BY started_at ASC, id ASC"
            ),
            {"k": key},
        ).fetchall()[0][0]
        bind.execute(
            sa.text(f"DELETE FROM {TABLE} WHERE {COLUMN} = :k AND id <> :keep"),
            {"k": key, "keep": keep},
        )
        print(f"[0011] {TABLE}.{COLUMN} 去重 {key}: 保留 {keep}，删除 {count - 1} 行")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    if not _column_exists(inspector, TABLE, COLUMN):
        return

    if _index_exists(inspector, TABLE, INDEX) and _is_unique(inspector, TABLE, INDEX):
        print("[0011] 幂等键唯一索引已存在，跳过")
        return

    _dedupe(bind)

    # 已存在但非唯一 → 先删再建唯一
    if _index_exists(inspector, TABLE, INDEX):
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            try:
                batch_op.drop_index(INDEX)
            except Exception:
                pass

    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index(INDEX, [COLUMN], unique=True)

    print("[0011] 已把 idempotency_key 索引改为唯一")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    if not _index_exists(inspector, TABLE, INDEX):
        return
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        try:
            batch_op.drop_index(INDEX)
        except Exception:
            pass
        batch_op.create_index(INDEX, [COLUMN], unique=False)
