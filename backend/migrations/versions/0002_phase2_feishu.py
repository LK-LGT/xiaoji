"""phase2: feishu org sync, identity mapping and scheduler lock

Revision ID: 0002_phase2_feishu
Revises: 0001_baseline
Create Date: 2026-11-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase2_feishu"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    from sqlalchemy import inspect

    return table_name in inspect(op.get_bind()).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    from sqlalchemy import inspect

    if not _table_exists(table_name):
        return False
    inspector = inspect(op.get_bind())
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}


def _index_exists(table_name: str, index_name: str) -> bool:
    """Check whether an index already exists on a table."""
    from sqlalchemy import inspect

    if not _table_exists(table_name):
        return False
    inspector = inspect(op.get_bind())
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    """Check whether a UNIQUE constraint already exists on a table."""
    from sqlalchemy import inspect

    if not _table_exists(table_name):
        return False
    inspector = inspect(op.get_bind())
    for uc in inspector.get_unique_constraints(table_name):
        if uc.get("name") == constraint_name:
            return True
    for idx in inspector.get_indexes(table_name):
        if idx.get("name") == constraint_name and idx.get("unique"):
            return True
    return False


def upgrade() -> None:
    """Add Phase 2 schema and enforce uniqueness."""
    if _table_exists("employee"):
        for col, col_type in (
            ("feishu_user_id", sa.String(length=64)),
            ("feishu_union_id", sa.String(length=128)),
            ("login_account", sa.String(length=64)),
            ("account_status", sa.String(length=32)),
            ("token_version", sa.Integer()),
            ("last_seen_batch_id", sa.String(length=64)),
            ("feishu_deleted_at", sa.DateTime()),
        ):
            if not _column_exists("employee", col):
                op.add_column("employee", sa.Column(col, col_type, nullable=True))

        op.execute("UPDATE employee SET account_status = 'enabled' WHERE account_status IS NULL")
        op.execute("UPDATE employee SET token_version = 1 WHERE token_version IS NULL")

    if _table_exists("department"):
        for col, col_type in (
            ("last_seen_batch_id", sa.String(length=64)),
            ("feishu_deleted_at", sa.DateTime()),
        ):
            if not _column_exists("department", col):
                op.add_column("department", sa.Column(col, col_type, nullable=True))

    if _table_exists("feishu_identity"):
        for col, col_type in (
            ("active", sa.Boolean()),
            ("bound_by", sa.String(length=64)),
            ("bound_at", sa.DateTime()),
            ("unbound_at", sa.DateTime()),
        ):
            if not _column_exists("feishu_identity", col):
                op.add_column("feishu_identity", sa.Column(col, col_type, nullable=True))

        op.execute("UPDATE feishu_identity SET active = 1 WHERE active IS NULL")
        op.execute("UPDATE feishu_identity SET bound_at = created_at WHERE bound_at IS NULL")

    _create_sync_tables()
    _create_audit_tables()
    _create_lock_table()
    _cleanup_duplicates()
    _create_constraints()


def _create_sync_tables() -> None:
    """Create the organization sync tables."""
    if not _table_exists("organization_sync_batch"):
        op.create_table(
            "organization_sync_batch",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("batch_no", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("mode", sa.String(length=32), nullable=False),
            sa.Column("trigger_type", sa.String(length=32), nullable=False),
            sa.Column("departments_synced", sa.Integer(), nullable=False),
            sa.Column("departments_skipped", sa.Integer(), nullable=False),
            sa.Column("departments_failed", sa.Integer(), nullable=False),
            sa.Column("employees_synced", sa.Integer(), nullable=False),
            sa.Column("employees_skipped", sa.Integer(), nullable=False),
            sa.Column("employees_failed", sa.Integer(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("triggered_by", sa.String(length=64), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("reconciliation_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_no", name="uq_org_sync_batch_batch_no"),
        )
        with op.batch_alter_table("organization_sync_batch", schema=None) as batch_op:
            if not _index_exists("organization_sync_batch", "ix_organization_sync_batch_batch_no"):
                batch_op.create_index(batch_op.f("ix_organization_sync_batch_batch_no"), ["batch_no"], unique=True)
            if not _index_exists("organization_sync_batch", "ix_organization_sync_batch_status"):
                batch_op.create_index(batch_op.f("ix_organization_sync_batch_status"), ["status"], unique=False)
            if not _index_exists("organization_sync_batch", "ix_organization_sync_batch_triggered_by"):
                batch_op.create_index(batch_op.f("ix_organization_sync_batch_triggered_by"), ["triggered_by"], unique=False)
            if not _index_exists("organization_sync_batch", "ix_org_sync_batch_status_started"):
                batch_op.create_index(
                    batch_op.f("ix_org_sync_batch_status_started"), ["status", "started_at"], unique=False
                )

    if not _table_exists("organization_sync_cursor"):
        op.create_table(
            "organization_sync_cursor",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("resource_type", sa.String(length=32), nullable=False),
            sa.Column("cursor_value", sa.String(length=512), nullable=True),
            sa.Column("page_token", sa.String(length=512), nullable=True),
            sa.Column("last_batch_id", sa.String(length=64), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["last_batch_id"], ["organization_sync_batch.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("resource_type", name="uq_org_sync_cursor_resource"),
        )

    if not _table_exists("organization_sync_item_error"):
        op.create_table(
            "organization_sync_item_error",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("batch_id", sa.String(length=64), nullable=False),
            sa.Column("resource_type", sa.String(length=32), nullable=False),
            sa.Column("resource_id", sa.String(length=128), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("next_retry_at", sa.DateTime(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["batch_id"], ["organization_sync_batch.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("organization_sync_item_error", schema=None) as batch_op:
            if not _index_exists("organization_sync_item_error", "ix_organization_sync_item_error_batch_id"):
                batch_op.create_index(
                    batch_op.f("ix_organization_sync_item_error_batch_id"), ["batch_id"], unique=False
                )
            if not _index_exists("organization_sync_item_error", "ix_organization_sync_item_error_status"):
                batch_op.create_index(
                    batch_op.f("ix_organization_sync_item_error_status"), ["status"], unique=False
                )
            if not _index_exists("organization_sync_item_error", "ix_org_sync_error_retry"):
                batch_op.create_index(
                    batch_op.f("ix_org_sync_error_retry"), ["status", "next_retry_at"], unique=False
                )


def _create_audit_tables() -> None:
    """Create the audit tables."""
    if not _table_exists("organization_sync_audit"):
        op.create_table(
            "organization_sync_audit",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("batch_id", sa.String(length=64), nullable=True),
            sa.Column("actor_id", sa.String(length=64), nullable=True),
            sa.Column("action", sa.String(length=128), nullable=False),
            sa.Column("resource_type", sa.String(length=64), nullable=True),
            sa.Column("resource_id", sa.String(length=128), nullable=True),
            sa.Column("result", sa.String(length=32), nullable=False),
            sa.Column("detail_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["batch_id"], ["organization_sync_batch.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("organization_sync_audit", schema=None) as batch_op:
            if not _index_exists("organization_sync_audit", "ix_organization_sync_audit_batch_id"):
                batch_op.create_index(
                    batch_op.f("ix_organization_sync_audit_batch_id"), ["batch_id"], unique=False
                )
            if not _index_exists("organization_sync_audit", "ix_organization_sync_audit_actor_id"):
                batch_op.create_index(
                    batch_op.f("ix_organization_sync_audit_actor_id"), ["actor_id"], unique=False
                )

    if not _table_exists("feishu_identity_audit"):
        op.create_table(
            "feishu_identity_audit",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("identity_id", sa.String(length=64), nullable=True),
            sa.Column("identity_type", sa.String(length=32), nullable=True),
            sa.Column("identity_value", sa.String(length=128), nullable=True),
            sa.Column("employee_id", sa.String(length=64), nullable=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("old_value_json", sa.JSON(), nullable=True),
            sa.Column("new_value_json", sa.JSON(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("actor_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("feishu_identity_audit", schema=None) as batch_op:
            if not _index_exists("feishu_identity_audit", "ix_feishu_identity_audit_employee_id"):
                batch_op.create_index(
                    batch_op.f("ix_feishu_identity_audit_employee_id"), ["employee_id"], unique=False
                )
            if not _index_exists("feishu_identity_audit", "ix_feishu_identity_audit_identity_id"):
                batch_op.create_index(
                    batch_op.f("ix_feishu_identity_audit_identity_id"), ["identity_id"], unique=False
                )
            if not _index_exists("feishu_identity_audit", "ix_feishu_identity_audit_actor_id"):
                batch_op.create_index(
                    batch_op.f("ix_feishu_identity_audit_actor_id"), ["actor_id"], unique=False
                )


def _create_lock_table() -> None:
    """Create the distributed scheduler lock table."""
    if not _table_exists("scheduled_job_lock"):
        op.create_table(
            "scheduled_job_lock",
            sa.Column("job_name", sa.String(length=64), nullable=False),
            sa.Column("locked_by", sa.String(length=128), nullable=False),
            sa.Column("locked_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("job_name"),
        )
        with op.batch_alter_table("scheduled_job_lock", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_scheduled_job_lock_expires_at"), ["expires_at"], unique=False
            )


def _cleanup_duplicates() -> None:
    """Deactivate duplicate identity bindings and null duplicate employee values."""
    if _table_exists("feishu_identity"):
        op.execute(
            """
            UPDATE feishu_identity
               SET active = 0,
                   unbound_at = COALESCE(unbound_at, CURRENT_TIMESTAMP)
             WHERE id NOT IN (
                   SELECT MIN(id) FROM feishu_identity
                    GROUP BY identity_type, identity_value
             )
            """
        )

    if _table_exists("employee"):
        for column in ("feishu_open_id", "feishu_user_id", "feishu_union_id", "login_account"):
            op.execute(
                f"""
                UPDATE employee
                   SET {column} = NULL
                 WHERE {column} IS NOT NULL
                   AND id NOT IN (
                         SELECT MIN(id) FROM employee
                          WHERE {column} IS NOT NULL
                          GROUP BY {column}
                   )
                """
            )


def _create_constraints() -> None:
    """Create the UNIQUE constraints and composite indexes."""
    if _table_exists("feishu_identity"):
        with op.batch_alter_table("feishu_identity", schema=None) as batch_op:
            if not _constraint_exists("feishu_identity", "uq_feishu_identity_value"):
                batch_op.create_unique_constraint(
                    "uq_feishu_identity_value", ["identity_type", "identity_value"]
                )
            if not _index_exists("feishu_identity", "ix_feishu_identity_employee_active"):
                batch_op.create_index(
                    batch_op.f("ix_feishu_identity_employee_active"),
                    ["employee_id", "active"],
                    unique=False,
                )

    if _table_exists("employee"):
        with op.batch_alter_table("employee", schema=None) as batch_op:
            for constraint_name, columns in (
                ("uq_employee_feishu_user_id", ["feishu_user_id"]),
                ("uq_employee_feishu_union_id", ["feishu_union_id"]),
                ("uq_employee_login_account", ["login_account"]),
                ("uq_employee_feishu_open_id", ["feishu_open_id"]),
            ):
                if not _constraint_exists("employee", constraint_name):
                    batch_op.create_unique_constraint(constraint_name, columns)

            for index_name, column in (
                ("ix_employee_feishu_user_id", "feishu_user_id"),
                ("ix_employee_feishu_union_id", "feishu_union_id"),
                ("ix_employee_login_account", "login_account"),
            ):
                if not _index_exists("employee", index_name):
                    batch_op.create_index(batch_op.f(index_name), [column], unique=True)


def downgrade() -> None:
    """Revert Phase 2 schema."""
    if _table_exists("scheduled_job_lock"):
        op.drop_table("scheduled_job_lock")
    if _table_exists("feishu_identity_audit"):
        op.drop_table("feishu_identity_audit")
    if _table_exists("organization_sync_audit"):
        op.drop_table("organization_sync_audit")
    if _table_exists("organization_sync_item_error"):
        op.drop_table("organization_sync_item_error")
    if _table_exists("organization_sync_cursor"):
        op.drop_table("organization_sync_cursor")
    if _table_exists("organization_sync_batch"):
        op.drop_table("organization_sync_batch")

    if _table_exists("employee"):
        with op.batch_alter_table("employee", schema=None) as batch_op:
            for name in (
                "ix_employee_login_account",
                "ix_employee_feishu_union_id",
                "ix_employee_feishu_user_id",
            ):
                try:
                    batch_op.drop_index(name)
                except Exception:
                    pass
            for name in (
                "uq_employee_feishu_open_id",
                "uq_employee_login_account",
                "uq_employee_feishu_union_id",
                "uq_employee_feishu_user_id",
            ):
                try:
                    batch_op.drop_constraint(name, type_="unique")
                except Exception:
                    pass
        for col in (
            "feishu_deleted_at",
            "last_seen_batch_id",
            "token_version",
            "account_status",
            "login_account",
            "feishu_union_id",
            "feishu_user_id",
        ):
            if _column_exists("employee", col):
                op.drop_column("employee", col)

    if _table_exists("department"):
        for col in ("feishu_deleted_at", "last_seen_batch_id"):
            if _column_exists("department", col):
                op.drop_column("department", col)

    if _table_exists("feishu_identity"):
        with op.batch_alter_table("feishu_identity", schema=None) as batch_op:
            try:
                batch_op.drop_index("ix_feishu_identity_employee_active")
            except Exception:
                pass
            try:
                batch_op.drop_constraint("uq_feishu_identity_value", type_="unique")
            except Exception:
                pass
        for col in ("unbound_at", "bound_at", "bound_by", "active"):
            if _column_exists("feishu_identity", col):
                op.drop_column("feishu_identity", col)
