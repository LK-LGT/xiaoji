"""Delegation table (P1-4): manager leave / acting delegation.

Revision ID: 0029_delegation
Revises: 0028_peer_review
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_delegation"
down_revision = "0028_peer_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delegation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("delegator_id", sa.String(64), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("delegate_id", sa.String(64), sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("scope_value", sa.String(256), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "delegator_id", "delegate_id", "scope_type", "scope_value",
            name="uq_delegation_scope",
        ),
    )
    op.create_index("ix_delegation_delegator_id", "delegation", ["delegator_id"])
    op.create_index("ix_delegation_delegate_id", "delegation", ["delegate_id"])


def downgrade() -> None:
    op.drop_index("ix_delegation_delegate_id", table_name="delegation")
    op.drop_index("ix_delegation_delegator_id", table_name="delegation")
    op.drop_table("delegation")
