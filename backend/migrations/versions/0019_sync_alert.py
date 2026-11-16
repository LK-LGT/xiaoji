"""0019: 同步失败告警表

Revision ID: 0019_sync_alert
Revises: 0018_widen_sync_cursor_resource_type
Create Date: 2026-11-16

背景
----
飞书组织同步在定时模式下无人值守运行。同步失败时如果没有告警，
问题会一直潜伏到「有人发现组织架构数据过期」为止 ——
而那时往往已经过了好几个同步周期，影响面成倍放大。

本迁移新建 ``organization_sync_alert`` 表：

    alert_type        告警类型（batch_failed / scheduler_consecutive_failures / ...）
    severity          info / warning / error
    status            open / acknowledged / resolved
    title / message   人类可读标题与正文
    batch_id          关联批次
    dedup_key         去重键：同一问题在抑制窗口内只告警一次
    suppressed_until  抑制截止时间
    notified_*        通知投递状态与错误
    acknowledged_*    认领人与时间
    resolved_at       恢复时间

幂等
----
建表前先检查是否已存在，可重复执行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_sync_alert"
down_revision: str | Sequence[str] | None = "0018_widen_sync_cursor_resource_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "organization_sync_alert"


def _table_exists() -> bool:
    try:
        return sa.inspect(op.get_bind()).has_table(TABLE)
    except Exception:
        return False


def upgrade() -> None:
    if _table_exists():
        print(f"[0019] {TABLE} 已存在，跳过")
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="error"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "batch_id",
            sa.String(length=64),
            sa.ForeignKey("organization_sync_batch.id"),
            nullable=True,
        ),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("suppressed_until", sa.DateTime(), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("notified_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column("notify_error", sa.Text(), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_index(f"ix_{TABLE}_alert_type", TABLE, ["alert_type"])
    op.create_index(f"ix_{TABLE}_severity", TABLE, ["severity"])
    op.create_index(f"ix_{TABLE}_status", TABLE, ["status"])
    op.create_index(f"ix_{TABLE}_batch_id", TABLE, ["batch_id"])
    op.create_index(f"ix_{TABLE}_dedup_key", TABLE, ["dedup_key"])
    op.create_index(
        "ix_org_sync_alert_status_created", TABLE, ["status", "created_at"]
    )
    op.create_index(
        "ix_org_sync_alert_dedup", TABLE, ["dedup_key", "created_at"]
    )

    print(f"[0019] 已建 {TABLE}")


def downgrade() -> None:
    if not _table_exists():
        return
    op.drop_table(TABLE)
    print(f"[0019] 已移除 {TABLE}")
