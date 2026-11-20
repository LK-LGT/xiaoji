"""飞书绩效方案来源绑定与自动同步批次。

Revision ID: 0032_feishu_scheme_source
Revises: 0031_ai_feedback_prompt_case

背景
----
把「飞书上的部门绩效方案文档」变成系统的**事实来源**，实现全自动同步：

    飞书文档 ──> feishu_scheme_source ──> PerformanceScheme ──> SchemeVersion

两张表
------
``feishu_scheme_source``
    方案来源绑定。一条记录 = 一份飞书文档 + 它对应的适用范围。
    ``binding_mode`` 区分「自动发现（auto）」与「人工指定（manual）」——
    人工指定优先，且自动发现**不覆盖**人工绑定。

``feishu_scheme_sync_batch``
    一次同步的执行记录，便于排查「为什么没同步」。
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_feishu_scheme_source"
down_revision = "0031_ai_feedback_prompt_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feishu_scheme_source",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("node_token", sa.String(128), nullable=False),
        sa.Column("obj_token", sa.String(64), nullable=False),
        sa.Column("obj_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("space_id", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column(
            "department_id",
            sa.String(64),
            sa.ForeignKey("department.id"),
            nullable=False,
        ),
        sa.Column("position", sa.String(128), nullable=False),
        sa.Column("period_type", sa.String(16), nullable=False),
        sa.Column("scheme_code", sa.String(64), nullable=False),
        sa.Column("binding_mode", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_edit_time", sa.String(32), nullable=False),
        sa.Column("last_content_hash", sa.String(64), nullable=False),
        sa.Column("last_version_id", sa.String(64)),
        sa.Column("last_synced_at", sa.DateTime()),
        sa.Column("last_error", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("obj_token", name="uq_feishu_scheme_source_obj_token"),
    )
    op.create_index(
        "ix_feishu_scheme_source_department_id", "feishu_scheme_source", ["department_id"]
    )
    op.create_index(
        "ix_feishu_scheme_source_dept",
        "feishu_scheme_source",
        ["department_id", "period_type"],
    )

    op.create_table(
        "feishu_scheme_sync_batch",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("changed_count", sa.Integer(), nullable=False),
        sa.Column("published_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(160)),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.String(1024), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index(
        "ix_feishu_scheme_sync_batch_status", "feishu_scheme_sync_batch", ["status"]
    )


def downgrade() -> None:
    op.drop_table("feishu_scheme_sync_batch")
    op.drop_table("feishu_scheme_source")
