"""百度网盘绩效校验 —— OAuth 凭证 / 计数快照 / 同步批次。

Revision ID: 0033_baidu_pan_verify
Revises: 0032_feishu_scheme_source

背景
----
把「网盘里的成片文件数」变成可核对的产出证据：

    网盘文件 ──> baidu_pan_count ──> MetricValue（算分集） ──> 主管核对

三张表
------
``baidu_pan_token``
    OAuth 凭证（**唯一一条**）。
    ⚠️ 必须存 —— 百度的 ``refresh_token`` 只能用一次，
    刷新后要用响应里的**新** refresh_token 覆盖旧的，
    不存就 30 天后断链。

``baidu_pan_count``
    一次取数的计数快照（同一窗口**覆盖**而非追加 ⇒ 幂等）。

``baidu_pan_sync_batch``
    一次取数同步的执行记录。
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_baidu_pan_verify"
down_revision = "0032_feishu_scheme_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "baidu_pan_token",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("app_key", sa.String(128), nullable=False),
        sa.Column("app_secret", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.String(512), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("authorized_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "baidu_pan_count",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_code", sa.String(64), nullable=False),
        sa.Column("window_start", sa.String(16), nullable=False),
        sa.Column("window_end", sa.String(16), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("daily_counts", sa.JSON(), nullable=False),
        sa.Column("scanned_files", sa.Integer(), nullable=False),
        sa.Column("excluded_files", sa.Integer(), nullable=False),
        sa.Column("scanned_path", sa.String(512), nullable=False),
        sa.Column("file_digest", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "source_code", "window_start", "window_end", name="uq_baidu_pan_count_window"
        ),
    )
    op.create_index(
        "ix_baidu_pan_count_window",
        "baidu_pan_count",
        ["window_start", "window_end"],
    )

    op.create_table(
        "baidu_pan_sync_batch",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scanned_files", sa.Integer(), nullable=False),
        sa.Column("counted_files", sa.Integer(), nullable=False),
        sa.Column("excluded_files", sa.Integer(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.String(1024), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index(
        "ix_baidu_pan_sync_batch_status", "baidu_pan_sync_batch", ["status"]
    )


def downgrade() -> None:
    op.drop_table("baidu_pan_sync_batch")
    op.drop_table("baidu_pan_count")
    op.drop_table("baidu_pan_token")
