"""网盘剪辑工作量 —— 复核工单增强（争议项 + 证据附件）。

Revision ID: 0035_pan_workload_review_items
Revises: 0034_pan_workload

背景
----
2026-11-21 业务澄清：复核不是「我说 10 条，系统算 6 条」——
而是**指着具体哪些文件有争议**，并**上传证据截图**。

    员工 → 勾选争议文件 / 手填漏传的文件 → 传截图 → 发起
      → 主管逐项裁定（成立/不成立/需补证） → 写回日汇总

三张变化
--------
``pan_workload_review``（改）
    加 ``scope``（裁定粒度 day/file）与 ``daily_snapshot``（发起时快照）。

``pan_workload_review_item``（新）
    ★ 争议项 —— 具体哪些文件有争议，逐项裁定。

``pan_workload_review_artifact``（新）
    ★ 证据附件 —— 截图/视频/文档，存 LargeBinary + sha256，**不可变**。
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_pan_workload_review_items"
down_revision = "0034_pan_workload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 工单加字段 ──
    op.add_column(
        "pan_workload_review",
        sa.Column("scope", sa.String(16), nullable=False, server_default="day"),
    )
    op.add_column(
        "pan_workload_review",
        sa.Column("daily_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )

    # ── 争议项 ──
    op.create_table(
        "pan_workload_review_item",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("pan_workload_file_id", sa.String(64), nullable=True),
        sa.Column("manual_name", sa.String(255), nullable=False),
        sa.Column("manual_description", sa.Text(), nullable=False),
        sa.Column("dispute_type", sa.String(32), nullable=False),
        sa.Column("claimed_weight", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.String(16), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=False),
        sa.Column("final_weight", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["pan_workload_review.id"]),
        sa.ForeignKeyConstraint(["pan_workload_file_id"], ["pan_workload_file.id"]),
    )
    op.create_index(
        "ix_pan_workload_review_item_review",
        "pan_workload_review_item",
        ["review_id"],
    )
    op.create_index(
        "ix_pan_workload_review_item_file",
        "pan_workload_review_item",
        ["pan_workload_file_id"],
    )

    # ── 证据附件 ──
    op.create_table(
        "pan_workload_review_artifact",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("review_item_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["pan_workload_review.id"]),
        sa.ForeignKeyConstraint(["review_item_id"], ["pan_workload_review_item.id"]),
    )
    op.create_index(
        "ix_pan_workload_review_artifact_review",
        "pan_workload_review_artifact",
        ["review_id"],
    )
    op.create_index(
        "ix_pan_workload_review_artifact_item",
        "pan_workload_review_artifact",
        ["review_item_id"],
    )


def downgrade() -> None:
    op.drop_table("pan_workload_review_artifact")
    op.drop_table("pan_workload_review_item")
    op.drop_column("pan_workload_review", "daily_snapshot")
    op.drop_column("pan_workload_review", "scope")
