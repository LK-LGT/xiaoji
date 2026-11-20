"""Peer review assignments (P0-1).

Revision ID: 0028_peer_review
Revises: 0027_review_center
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_peer_review"
down_revision = "0027_review_center"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "performance_peer_review",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("record_id", sa.String(64), sa.ForeignKey("assessment_record.id"), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("reviewee_id", sa.String(64), nullable=False),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("assigned_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("record_id", "reviewer_id", name="uq_peer_record_reviewer"),
    )
    op.create_index("ix_peer_record", "performance_peer_review", ["record_id"])
    op.create_index("ix_peer_task", "performance_peer_review", ["task_id"])
    op.create_index("ix_peer_reviewer", "performance_peer_review", ["reviewer_id"])
    op.create_index("ix_peer_reviewee", "performance_peer_review", ["reviewee_id"])


def downgrade() -> None:
    op.drop_index("ix_peer_reviewee", table_name="performance_peer_review")
    op.drop_index("ix_peer_reviewer", table_name="performance_peer_review")
    op.drop_index("ix_peer_task", table_name="performance_peer_review")
    op.drop_index("ix_peer_record", table_name="performance_peer_review")
    op.drop_table("performance_peer_review")
