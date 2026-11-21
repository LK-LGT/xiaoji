"""人工填报增强（scope/target_key + 证据附件 + 飞书来源留痕）。

Revision ID: 0036_pan_manual_metric_scope
Revises: 0035_pan_workload_review_items

背景
----
2026-11-21 业务确认：
- 填报粒度要支持**逐条成片**与**按天**两种 ⇒ 唯一约束必须带 ``target_key``。
- 证据附件要支持**飞书端上传** ⇒ 新增附件表 + 飞书来源留痕。
- 月度聚合写成**独立的 metric_code**（不合成综合分）。

⚠️ **唯一约束变更**
``uq_pan_manual_metric_key`` 从
``(employee_id, metric_date, metric_code)``
扩为
``(employee_id, metric_date, metric_code, target_key)``。
⇒ 旧数据 ``target_key`` 为空串，不冲突。

⚠️ **SQLite 注意**
``alembic`` 用 ``render_as_batch=True`` ⇒ ``batch_alter_table`` 会
「建新表 → 拷数据 → 删旧表 → 改名」。旧表有外键指向它，
必须先 ``drop_constraint`` 再重建。
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_pan_manual_metric_scope"
down_revision = "0035_pan_workload_review_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 扩列 ──
    op.add_column(
        "pan_manual_metric",
        sa.Column("scope", sa.String(16), nullable=False, server_default="day"),
    )
    op.add_column(
        "pan_manual_metric",
        sa.Column("target_key", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "pan_manual_metric",
        sa.Column("feishu_open_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "pan_manual_metric",
        sa.Column("feishu_chat_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "pan_manual_metric",
        sa.Column("feishu_message_id", sa.String(64), nullable=True),
    )

    # ── 唯一约束扩展（要带 target_key）──
    with op.batch_alter_table("pan_manual_metric") as batch:
        batch.drop_constraint("uq_pan_manual_metric_key", type_="unique")
        batch.create_unique_constraint(
            "uq_pan_manual_metric_key",
            ["employee_id", "metric_date", "metric_code", "target_key"],
        )

    op.create_index(
        "ix_pan_manual_metric_date", "pan_manual_metric", ["metric_date"]
    )

    # ── 证据附件 ──
    op.create_table(
        "pan_manual_metric_artifact",
        sa.Column("id", sa.String(64), primary_key=True),
        # ⚠️ **可空** —— 空 = 待关联（员工先发截图，还没补数值）
        sa.Column("metric_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("feishu_image_key", sa.String(255), nullable=True),
        sa.Column("feishu_message_id", sa.String(64), nullable=True),
        sa.Column("uploaded_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["metric_id"], ["pan_manual_metric.id"]),
    )
    op.create_index(
        "ix_pan_manual_metric_artifact_metric",
        "pan_manual_metric_artifact",
        ["metric_id"],
    )


def downgrade() -> None:
    op.drop_table("pan_manual_metric_artifact")
    op.drop_index("ix_pan_manual_metric_date", table_name="pan_manual_metric")

    with op.batch_alter_table("pan_manual_metric") as batch:
        batch.drop_constraint("uq_pan_manual_metric_key", type_="unique")
        batch.create_unique_constraint(
            "uq_pan_manual_metric_key",
            ["employee_id", "metric_date", "metric_code"],
        )
        batch.drop_column("feishu_message_id")
        batch.drop_column("feishu_chat_id")
        batch.drop_column("feishu_open_id")
        batch.drop_column("target_key")
        batch.drop_column("scope")
