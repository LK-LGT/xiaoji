"""P2-3 tables: ai_feedback / prompt_version / case_library_entry.

Revision ID: 0031_ai_feedback_prompt_case
Revises: 0030_data_source_policy
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_ai_feedback_prompt_case"
down_revision = "0030_data_source_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 人工反馈（灰度对比的唯一可信依据）──
    op.create_table(
        "ai_feedback",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "record_id",
            sa.String(64),
            sa.ForeignKey("assessment_record.id"),
            nullable=False,
        ),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("diff_summary", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("dimension", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_feedback_record_id", "ai_feedback", ["record_id"])
    op.create_index("ix_ai_feedback_agent", "ai_feedback", ["agent"])
    op.create_index("ix_ai_feedback_prompt_version", "ai_feedback", ["prompt_version"])
    op.create_index("ix_ai_feedback_actor_id", "ai_feedback", ["actor_id"])
    op.create_index("ix_ai_feedback_created_at", "ai_feedback", ["created_at"])

    # ── Prompt 版本与灰度配置 ──
    op.create_table(
        "prompt_version",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("rollout", sa.String(32), nullable=False),
        sa.Column("canary_ratio", sa.Float(), nullable=False),
        sa.Column("baseline_version", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("agent", "version", name="uq_promptversion_agent_version"),
    )
    op.create_index("ix_prompt_version_agent", "prompt_version", ["agent"])

    # ── 案例库 ──
    op.create_table(
        "case_library_entry",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("scenario", sa.String(64), nullable=False),
        sa.Column("rating", sa.String(8), nullable=True),
        sa.Column("score_range", sa.String(32), nullable=True),
        sa.Column("facts_json", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.String(64), nullable=True),
        sa.Column("redacted", sa.Boolean(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_case_library_entry_scenario", "case_library_entry", ["scenario"]
    )
    op.create_index(
        "ix_case_library_entry_created_at", "case_library_entry", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_case_library_entry_created_at", table_name="case_library_entry")
    op.drop_index("ix_case_library_entry_scenario", table_name="case_library_entry")
    op.drop_table("case_library_entry")
    op.drop_index("ix_prompt_version_agent", table_name="prompt_version")
    op.drop_table("prompt_version")
    op.drop_index("ix_ai_feedback_created_at", table_name="ai_feedback")
    op.drop_index("ix_ai_feedback_actor_id", table_name="ai_feedback")
    op.drop_index("ix_ai_feedback_prompt_version", table_name="ai_feedback")
    op.drop_index("ix_ai_feedback_agent", table_name="ai_feedback")
    op.drop_index("ix_ai_feedback_record_id", table_name="ai_feedback")
    op.drop_table("ai_feedback")
