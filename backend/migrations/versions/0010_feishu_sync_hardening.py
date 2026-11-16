"""0010: 飞书组织架构同步的生产可用性增强字段

Revision ID: 0010_feishu_sync_hardening
Revises: 0009_score_record_unique
Create Date: 2026-11-16

背景
----
本次为「把飞书组织架构作为组织事实来源」补齐了若干字段：

``organization_sync_batch``
    - ``idempotency_key``：幂等键。相同键的重复触发复用同一批次，
      避免管理员连点或定时任务重入造成重复同步。
    - ``phase`` / ``phase_detail``：异步执行时的阶段标记，供前端展示进度。
    - ``error_category``：错误分级（retryable / config / manual /
      permanent / unknown），决定前端展示什么操作按钮。

``organization_sync_cursor``
    - ``checkpoint_value``：上次成功推进的逻辑位置。
    - ``snapshot_complete``：上次是否取得完整快照。它是
      「远端缺失 = 离职/删除」这一判定的唯一前提。

``organization_sync_item_error``
    - ``error_category``：单条失败的分级，决定是否自动重试。

迁移做法
--------
- **幂等**：每列都先检查是否已存在，存在则跳过。
- **不删数据**：全部是新增可空列，对存量数据无影响。
- **兼容 SQLite**：``batch_alter_table`` 会走表重建路径。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_feishu_sync_hardening"
down_revision: str | Sequence[str] | None = "0009_score_record_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 表名 -> [(列名, 类型, 可空, 默认值, 唯一索引)]
NEW_COLUMNS: dict[str, list[tuple[str, sa.types.TypeEngine, bool, str | None, bool]]] = {
    "organization_sync_batch": [
        ("idempotency_key", sa.String(length=160), True, None, True),
        ("phase", sa.String(length=32), True, None, True),
        ("phase_detail", sa.String(length=256), True, None, False),
        ("error_category", sa.String(length=32), True, None, False),
    ],
    "organization_sync_cursor": [
        ("checkpoint_value", sa.String(length=512), True, None, False),
        (
            "snapshot_complete",
            sa.Boolean(),
            False,
            "0",
            False,
        ),
    ],
    "organization_sync_item_error": [
        ("error_category", sa.String(length=32), True, None, True),
    ],
}


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_exists(inspector, table_name: str, column_name: str) -> bool:
    try:
        columns = {col["name"] for col in inspector.get_columns(table_name)}
    except Exception:
        return False
    return column_name in columns


def _index_exists(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    except Exception:
        return False
    return index_name in indexes


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name, columns in NEW_COLUMNS.items():
        if not _table_exists(inspector, table_name):
            # 表不存在说明该环境从未启用飞书同步，跳过即可；
            # create_all 会在首次启用时按模型建全。
            continue

        for column_name, column_type, nullable, default, needs_index in columns:
            if _column_exists(inspector, table_name, column_name):
                continue

            with op.batch_alter_table(table_name) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        column_name,
                        column_type,
                        nullable=nullable,
                        server_default=default,
                    )
                )

            if needs_index:
                index_name = f"ix_{table_name}_{column_name}"
                if not _index_exists(inspector, table_name, index_name):
                    with op.batch_alter_table(table_name) as batch_op:
                        batch_op.create_index(index_name, [column_name], unique=False)

        inspector = sa.inspect(bind)

    # idempotency_key 需要唯一约束：同一幂等键只能对应一个批次。
    # 唯一索引而非唯一约束，避免 SQLite 表重建时约束名冲突。
    if _table_exists(inspector, "organization_sync_batch"):
        if not _column_exists(inspector, "organization_sync_batch", "idempotency_key"):
            with op.batch_alter_table("organization_sync_batch") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "idempotency_key",
                        sa.String(length=160),
                        nullable=True,
                    )
                )
        index_name = "ix_organization_sync_batch_idempotency_key"
        if not _index_exists(inspector, "organization_sync_batch", index_name):
            with op.batch_alter_table("organization_sync_batch") as batch_op:
                batch_op.create_index(index_name, ["idempotency_key"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── 先删索引，再删列 ──
    # SQLite 的 batch_alter_table 走「建新表 → 拷数据 → 换名」路径，
    # 重建时会尝试把原表上的索引一并建回新表。若索引依赖的列已被删掉，
    # 就会报 `no such column: idempotency_key`。
    # 因此必须在 drop_column 之前把索引删掉。
    for table_name, columns in NEW_COLUMNS.items():
        if not _table_exists(inspector, table_name):
            continue
        for column_name, _col_type, _nullable, _default, _needs_index in columns:
            index_name = f"ix_{table_name}_{column_name}"
            if _index_exists(inspector, table_name, index_name):
                with op.batch_alter_table(table_name) as batch_op:
                    batch_op.drop_index(index_name)
        inspector = sa.inspect(bind)

    # 再删列
    for table_name, columns in NEW_COLUMNS.items():
        if not _table_exists(inspector, table_name):
            continue
        for column_name, _col_type, _nullable, _default, _needs_index in columns:
            if _column_exists(inspector, table_name, column_name):
                with op.batch_alter_table(table_name) as batch_op:
                    batch_op.drop_column(column_name)
        inspector = sa.inspect(bind)
