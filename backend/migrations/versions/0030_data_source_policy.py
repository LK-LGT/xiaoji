"""Data source policy table (P1-5): readable sources + redacted fields.

Revision ID: 0030_data_source_policy
Revises: 0029_delegation
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0030_data_source_policy"
down_revision = "0029_delegation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_source_policy",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_code", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("allowed_roles", sa.String(256), nullable=False),
        sa.Column("redacted_fields", sa.String(512), nullable=False),
        sa.Column("note", sa.String(256), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_code", name="uq_datasourcepolicy_code"),
    )
    op.create_index(
        "ix_datasourcepolicy_source_code", "data_source_policy", ["source_code"]
    )
    # 初始化三条默认策略：与 data_source_service.DEFAULT_POLICIES 保持一致。
    # 「没配置」= 不可读 —— 这三行是**已知**的源，先给出安全的默认可读范围，
    # 其余未知的源一律不信任。
    now = datetime.now(UTC).replace(tzinfo=None)
    for code, name, roles, redacted in (
        ("attendance", "考勤系统", "hr,manager,department_leader,employee", ""),
        ("feishu_approval", "飞书审批", "hr,manager,department_leader,employee", ""),
        ("manual_upload", "人工上传", "hr,manager,employee", "mobile,email"),
    ):
        op.bulk_insert(
            sa.table(
                "data_source_policy",
                sa.column("id", sa.String),
                sa.column("source_code", sa.String),
                sa.column("display_name", sa.String),
                sa.column("allowed", sa.Boolean),
                sa.column("allowed_roles", sa.String),
                sa.column("redacted_fields", sa.String),
                sa.column("note", sa.String),
                sa.column("updated_by", sa.String),
                sa.column("created_at", sa.DateTime),
                sa.column("updated_at", sa.DateTime),
            ),
            [
                {
                    "id": f"dsp_{code}",
                    "source_code": code,
                    "display_name": name,
                    "allowed": True,
                    "allowed_roles": roles,
                    "redacted_fields": redacted,
                    "note": "系统初始化",
                    "updated_by": None,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_datasourcepolicy_source_code", table_name="data_source_policy")
    op.drop_table("data_source_policy")
