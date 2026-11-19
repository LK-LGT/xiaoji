"""HR scheme version aggregates, import batches and organization bindings.

Revision ID: 0026_performance_schemes
Revises: 0025_notification
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_performance_schemes"
down_revision = "0025_notification"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "performance_scheme",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "performance_scheme_version",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "scheme_id", sa.String(64), sa.ForeignKey("performance_scheme.id"), nullable=False
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_import_id", sa.String(64)),
        sa.Column("copied_from_id", sa.String(64)),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime()),
        sa.UniqueConstraint("scheme_id", "version_no", name="uq_scheme_version"),
    )
    op.create_index(
        "ix_performance_scheme_version_scheme_id", "performance_scheme_version", ["scheme_id"]
    )
    op.create_table(
        "performance_scheme_binding",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "version_id",
            sa.String(64),
            sa.ForeignKey("performance_scheme_version.id"),
            nullable=False,
        ),
        sa.Column("department_id", sa.String(64), sa.ForeignKey("department.id"), nullable=False),
        sa.Column("position", sa.String(128), nullable=False),
        sa.Column("period_type", sa.String(16), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=False),
    )
    op.create_index(
        "ix_performance_scheme_binding_version_id", "performance_scheme_binding", ["version_id"]
    )
    op.create_index(
        "ix_performance_scheme_binding_department_id",
        "performance_scheme_binding",
        ["department_id"],
    )
    op.create_table(
        "performance_scheme_import",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("operator_id", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("source_file", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("operator_id", "file_hash", name="uq_scheme_import_file"),
    )

    op.create_table(
        "performance_scheme_response",
        sa.Column(
            "record_id", sa.String(64), sa.ForeignKey("assessment_record.id"), primary_key=True
        ),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("metric_values", sa.JSON(), nullable=False),
        sa.Column("reviews", sa.JSON(), nullable=False),
        sa.Column("reviewer_id", sa.String(64)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("performance_scheme_response")
    op.drop_table("performance_scheme_import")
    op.drop_table("performance_scheme_binding")
    op.drop_table("performance_scheme_version")
    op.drop_table("performance_scheme")
