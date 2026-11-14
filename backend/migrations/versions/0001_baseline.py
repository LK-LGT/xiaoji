"""baseline: existing schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-14

Baseline migration for the pre-existing database.

The production database was created before Alembic was introduced and was
maintained by Base.metadata.create_all() plus a lightweight SQLite column
sync (app.core.migrations.sync_schema). That mechanism could add columns and
tables but never created UNIQUE constraints, so the live database is missing
several constraints that the models declare.

This baseline recreates the ORIGINAL schema exactly as it existed, so a fresh
database can be built from scratch. The subsequent Phase 2 migration then
applies the Phase 2 changes (new columns, new tables, UNIQUE constraints) in
the same way they must be applied to the already-populated live database.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the original schema."""
    op.create_table(
        "department",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("leader_id", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "employee",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("feishu_open_id", sa.String(length=128), nullable=True),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.String(length=128), nullable=True),
        sa.Column("manager_id", sa.String(length=64), nullable=True),
        sa.Column("employment_status", sa.String(length=32), nullable=False),
        sa.Column("joined_at", sa.Date(), nullable=True),
        sa.Column("special_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.ForeignKeyConstraint(["manager_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("employee", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_employee_feishu_open_id"), ["feishu_open_id"], unique=False)

    op.create_table(
        "employee_department",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "department_id", name="uq_employeedept"),
    )
    with op.batch_alter_table("employee_department", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_employee_department_department_id"), ["department_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_employee_department_employee_id"), ["employee_id"], unique=False)

    op.create_table(
        "feishu_identity",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("identity_type", sa.String(length=32), nullable=False),
        sa.Column("identity_value", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("feishu_identity", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_feishu_identity_employee_id"), ["employee_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_feishu_identity_identity_value"), ["identity_value"], unique=False)

    op.create_table(
        "hr_department_config",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("granted_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("department_id", name="uq_hrdepartmentconfig"),
    )
    with op.batch_alter_table("hr_department_config", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_hr_department_config_department_id"), ["department_id"], unique=False)

    op.create_table(
        "system_admin",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("granted_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", name="uq_systemadminemployee"),
    )
    with op.batch_alter_table("system_admin", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_system_admin_employee_id"), ["employee_id"], unique=False)

    op.create_table(
        "metric",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("calculation", sa.Text(), nullable=True),
        sa.Column("data_source", sa.String(length=128), nullable=True),
        sa.Column("caliber", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("is_attendance", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_metric_code"),
    )
    with op.batch_alter_table("metric", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_metric_active"), ["active"], unique=False)
        batch_op.create_index(batch_op.f("ix_metric_code"), ["code"], unique=False)

    op.create_table(
        "weight_set",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.String(length=128), nullable=True),
        sa.Column("weights_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("weight_set", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_weight_set_department_id"), ["department_id"], unique=False)

    op.create_table(
        "rubric",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("weight_set_id", sa.String(length=64), nullable=False),
        sa.Column("metric_code", sa.String(length=64), nullable=False),
        sa.Column("levels_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("rubric", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_rubric_weight_set_id"), ["weight_set_id"], unique=False)

    op.create_table(
        "form_template",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("form_template", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_form_template_department_id"), ["department_id"], unique=False)

    op.create_table(
        "config_snapshot",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("weights_json", sa.JSON(), nullable=False),
        sa.Column("rubric_json", sa.JSON(), nullable=False),
        sa.Column("form_template_json", sa.JSON(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("config_snapshot", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_config_snapshot_task_id"), ["task_id"], unique=False)

    op.create_table(
        "config_sync_log",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("snapshot_used", sa.String(length=64), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("config_sync_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_config_sync_log_source"), ["source"], unique=False)

    op.create_table(
        "assessment_task",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("position", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rating_policy", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("owner_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("assessment_task", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assessment_task_department_id"), ["department_id"], unique=False)

    op.create_table(
        "assessment_record",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submit_source", sa.String(length=32), nullable=True),
        sa.Column("submit_locked", sa.Boolean(), nullable=False),
        sa.Column("confirmed_rating", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["assessment_task.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "employee_id", name="uq_assessmentrecord_task_employee"),
    )
    with op.batch_alter_table("assessment_record", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assessment_record_employee_id"), ["employee_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_assessment_record_task_id"), ["task_id"], unique=False)

    op.create_table(
        "self_review",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("answers_json", sa.JSON(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("submit_source", sa.String(length=32), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("dedup_key", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["record_id"], ["assessment_record.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", name="uq_self_review_record"),
    )
    with op.batch_alter_table("self_review", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_self_review_dedup_key"), ["dedup_key"], unique=False)

    op.create_table(
        "manager_review",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("scores_json", sa.JSON(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.String(length=64), nullable=True),
        sa.Column("reviewer_role", sa.String(length=32), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["assessment_record.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", name="uq_manager_review_record"),
    )
    with op.batch_alter_table("manager_review", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_manager_review_record_id"), ["record_id"], unique=False)

    op.create_table(
        "appeal",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("acceptor_id", sa.String(length=64), nullable=True),
        sa.Column("reviewer_id", sa.String(length=64), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["assessment_record.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("appeal", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_appeal_record_id"), ["record_id"], unique=False)

    op.create_table(
        "approval_event",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("approval_instance_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_approval_event_dedup"),
    )
    with op.batch_alter_table("approval_event", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approval_event_dedup_key"), ["dedup_key"], unique=False)

    op.create_table(
        "metric_value",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("metric_code", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("target", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column("as_of", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "metric_code", "period", name="uq_metric_value_employee_metric_period"),
    )
    with op.batch_alter_table("metric_value", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_metric_value_employee_id"), ["employee_id"], unique=False)

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("source_ref", sa.String(length=256), nullable=True),
        sa.Column("as_of", sa.DateTime(), nullable=True),
        sa.Column("usage", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("concern", sa.Text(), nullable=True),
        sa.Column("anomaly_type", sa.String(length=64), nullable=True),
        sa.Column("anomaly_count", sa.Integer(), nullable=True),
        sa.Column("approved_flag", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("evidence", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_evidence_employee_id"), ["employee_id"], unique=False)

    op.create_table(
        "score",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("per_metric_json", sa.JSON(), nullable=False),
        sa.Column("trace_json", sa.JSON(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["assessment_record.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", name="uq_score_record"),
    )
    with op.batch_alter_table("score", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_score_record_id"), ["record_id"], unique=False)

    op.create_table(
        "rating_draft",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("ratings_json", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("gaps_json", sa.JSON(), nullable=True),
        sa.Column("fairness_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["assessment_record.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", name="uq_rating_draft_record"),
    )
    with op.batch_alter_table("rating_draft", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_rating_draft_record_id"), ["record_id"], unique=False)

    op.create_table(
        "comment_draft",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["record_id"], ["assessment_record.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", name="uq_comment_draft_record"),
    )
    with op.batch_alter_table("comment_draft", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_comment_draft_record_id"), ["record_id"], unique=False)

    op.create_table(
        "rollup",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("stats_json", sa.JSON(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "scope_id", "period", name="uq_rollup_scope_period"),
    )
    with op.batch_alter_table("rollup", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_rollup_scope_id"), ["scope_id"], unique=False)

    op.create_table(
        "ai_trace",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ai_trace", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ai_trace_record_id"), ["record_id"], unique=False)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_log_actor_id"), ["actor_id"], unique=False)


def downgrade() -> None:
    """Drop the entire schema."""
    for table in (
        "audit_log",
        "ai_trace",
        "rollup",
        "comment_draft",
        "rating_draft",
        "score",
        "evidence",
        "metric_value",
        "approval_event",
        "appeal",
        "manager_review",
        "self_review",
        "assessment_record",
        "assessment_task",
        "config_sync_log",
        "config_snapshot",
        "form_template",
        "rubric",
        "weight_set",
        "metric",
        "system_admin",
        "hr_department_config",
        "feishu_identity",
        "employee_department",
        "employee",
        "department",
    ):
        op.drop_table(table)
