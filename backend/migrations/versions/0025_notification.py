"""通知发件箱与站内消息。

Revision ID: 0025_notification
Revises: 0024_agent_tool_call
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_notification"
down_revision = "0024_agent_tool_call"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("employee_id", sa.String(64), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("scene", sa.String(16), nullable=False),
        sa.Column("period", sa.String(128), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(16), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("route", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("skip_reason", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("dedup_key", name="uq_notification_dedup"),
    )
    op.create_index("ix_notification_employee_id", "notification", ["employee_id"])
    op.create_index("ix_notification_status", "notification", ["status"])
    op.add_column("assessment_task", sa.Column("self_review_deadline", sa.DateTime(), nullable=True))
    op.add_column("assessment_task", sa.Column("manager_review_deadline", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("assessment_task", "manager_review_deadline")
    op.drop_column("assessment_task", "self_review_deadline")
    op.drop_index("ix_notification_status", table_name="notification")
    op.drop_index("ix_notification_employee_id", table_name="notification")
    op.drop_table("notification")
