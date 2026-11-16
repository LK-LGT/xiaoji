"""0020: 组织架构变更事件队列表（R18 事件驱动增量）

Revision ID: 0020_org_change_queue
Revises: 0019_sync_alert
Create Date: 2026-11-16

背景
----
飞书通讯录事件（``contact.user.*`` / ``contact.department.*``）推送到
webhook 后，需要先**落库成队列项**，再由后台消费者按队列增量 upsert。

为什么不是「webhook 里直接改数据」
--------------------------------
- 飞书要求 webhook **快速返回**，直接做业务容易超时 → 飞书反复重推；
- 事件可能**乱序**到达（先删后建、先改名后移动），需要串行处理；
- 处理失败要能**重试**，不能丢。

⚠️ 事件不是唯一数据源
---------------------
飞书事件推送**可能丢失**（网络、平台故障）。所以本表只是「快路径」，
现有分页游标全量对账仍是**兜底**，保证最终一致。
绝不能只靠事件 —— 丢一个事件就永久丢一条数据。

表结构
------
    event_type     事件类型（user_updated / department_deleted / ...）
    resource_type  user / department
    resource_id    飞书 user_id / department_id
    status         pending / processing / processed / failed / dead_letter
    retry_count    重试次数
    next_retry_at  退避后的下次可处理时间
    dedup_key      去重键（飞书 event_id，缺失时退化为资源+类型+时间片）
    payload_json   脱敏后的事件载荷
    batch_id       处理时产生的批次（可空）
    error_message  最近一次错误

幂等
----
建表前先检查是否已存在，可重复执行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_org_change_queue"
down_revision: str | Sequence[str] | None = "0019_sync_alert"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "org_change_queue"


def _table_exists() -> bool:
    try:
        return sa.inspect(op.get_bind()).has_table(TABLE)
    except Exception:
        return False


def upgrade() -> None:
    if _table_exists():
        print(f"[0020] {TABLE} 已存在，跳过")
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column(
            "batch_id",
            sa.String(length=64),
            sa.ForeignKey("organization_sync_batch.id"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )

    op.create_index(f"ix_{TABLE}_event_type", TABLE, ["event_type"])
    op.create_index(f"ix_{TABLE}_resource_type", TABLE, ["resource_type"])
    op.create_index(f"ix_{TABLE}_resource_id", TABLE, ["resource_id"])
    op.create_index(f"ix_{TABLE}_status", TABLE, ["status"])
    op.create_index(f"ix_{TABLE}_dedup_key", TABLE, ["dedup_key"])
    op.create_index(f"ix_{TABLE}_batch_id", TABLE, ["batch_id"])
    # 取待处理记录的主索引：按状态过滤 + 按创建时间排序。
    op.create_index(
        "ix_org_change_queue_pending", TABLE, ["status", "created_at"]
    )
    op.create_index(
        "ix_org_change_queue_dedup", TABLE, ["dedup_key", "created_at"]
    )

    print(f"[0020] 已建 {TABLE}")


def downgrade() -> None:
    if not _table_exists():
        return
    op.drop_table(TABLE)
    print(f"[0020] 已移除 {TABLE}")
