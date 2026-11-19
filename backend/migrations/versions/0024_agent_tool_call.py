"""新增工具调用审计表。

Revision ID: 0024_agent_tool_call
Revises: 0023_bot_session
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_agent_tool_call"
down_revision = "0023_bot_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_tool_call",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("employee_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("args_keys", sa.JSON(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("trace_id", "agent", "tool", "employee_id"):
        op.create_index(f"ix_ai_tool_call_{column}", "ai_tool_call", [column])


def downgrade() -> None:
    for column in ("employee_id", "tool", "agent", "trace_id"):
        op.drop_index(f"ix_ai_tool_call_{column}", table_name="ai_tool_call")
    op.drop_table("ai_tool_call")
