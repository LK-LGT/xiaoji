"""phase3: planning domain (month / week / day plans)

Revision ID: 0003_phase3_planning
Revises: 0002_phase2_feishu
Create Date: 2026-11-14

计划域建表：月 / 周 / 日三层计划及其条目。

设计要点：
- 三层计划逐层细化：周计划条目可关联月计划条目，日计划条目可关联周计划条目。
- 每张计划表带 `version_no`，用于提交 / 撤回时的乐观锁校验。
- 状态机统一用 `PlanStatus`（draft / submitted / withdrawn / pending /
  approved / rejected）。
- 迁移保持幂等：已存在的表跳过，方便在已建表的库上重放。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase3_planning"
down_revision: Union[str, Sequence[str], None] = "0002_phase2_feishu"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    from sqlalchemy import inspect

    return table_name in inspect(op.get_bind()).get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    from sqlalchemy import inspect

    if not _table_exists(table_name):
        return False
    inspector = inspect(op.get_bind())
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
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
    """Create the planning domain tables."""
    _create_month_plan()
    _create_week_plan()
    _create_day_plan()


def _create_month_plan() -> None:
    """month_plan + month_plan_item."""
    if not _table_exists("month_plan"):
        op.create_table(
            "month_plan",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=64), nullable=False),
            sa.Column("plan_month", sa.String(length=16), nullable=False),
            sa.Column("title", sa.String(length=256), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("project_name", sa.String(length=128), nullable=True),
            sa.Column("position_name", sa.String(length=128), nullable=True),
            sa.Column("result_status", sa.String(length=32), nullable=False),
            sa.Column("completion_rate", sa.Float(), nullable=False, server_default="0"),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approval_comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["employee.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column, unique in (
            ("ix_month_plan_owner_id", "owner_id", False),
            ("ix_month_plan_plan_month", "plan_month", False),
            ("ix_month_plan_status", "status", False),
        ):
            if not _index_exists("month_plan", name):
                op.create_index(name, "month_plan", [column], unique=unique)

    if not _table_exists("month_plan_item"):
        op.create_table(
            "month_plan_item",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("plan_id", sa.String(length=64), nullable=False),
            sa.Column("task_type", sa.String(length=32), nullable=False),
            sa.Column("task_name", sa.String(length=256), nullable=False),
            sa.Column("task_content", sa.Text(), nullable=False),
            sa.Column("deliverable", sa.Text(), nullable=False),
            sa.Column("quality", sa.Text(), nullable=False),
            sa.Column("performance_weight", sa.Float(), nullable=False, server_default="0"),
            sa.Column("estimated_value", sa.Float(), nullable=False, server_default="0"),
            sa.Column("estimated_start_date", sa.Date(), nullable=True),
            sa.Column("estimated_end_date", sa.Date(), nullable=True),
            sa.Column("progress", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("sort_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approval_comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["plan_id"], ["month_plan.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        if not _index_exists("month_plan_item", "ix_month_plan_item_plan_id"):
            op.create_index("ix_month_plan_item_plan_id", "month_plan_item", ["plan_id"], unique=False)
        if not _index_exists("month_plan_item", "ix_month_plan_item_plan_sort"):
            op.create_index(
                "ix_month_plan_item_plan_sort", "month_plan_item", ["plan_id", "sort_no"], unique=False
            )

    if _table_exists("month_plan") and not _constraint_exists(
        "month_plan", "uq_month_plan_owner_month"
    ):
        with op.batch_alter_table("month_plan", schema=None) as batch_op:
            batch_op.create_unique_constraint(
                "uq_month_plan_owner_month", ["owner_id", "plan_month"]
            )
    if _table_exists("month_plan") and not _index_exists(
        "month_plan", "ix_month_plan_owner_status"
    ):
        with op.batch_alter_table("month_plan", schema=None) as batch_op:
            batch_op.create_index(
                "ix_month_plan_owner_status", ["owner_id", "status"], unique=False
            )


def _create_week_plan() -> None:
    """week_plan + week_plan_item."""
    if not _table_exists("week_plan"):
        op.create_table(
            "week_plan",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=256), nullable=False),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("week_end", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("approve_at", sa.DateTime(), nullable=True),
            sa.Column("approval_comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["employee.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (
            ("ix_week_plan_owner_id", "owner_id"),
            ("ix_week_plan_week_start", "week_start"),
            ("ix_week_plan_status", "status"),
        ):
            if not _index_exists("week_plan", name):
                op.create_index(name, "week_plan", [column], unique=False)
        if not _index_exists("week_plan", "ix_week_plan_owner_start"):
            op.create_index(
                "ix_week_plan_owner_start", "week_plan", ["owner_id", "week_start"], unique=False
            )
        if not _index_exists("week_plan", "ix_week_plan_owner_status"):
            op.create_index(
                "ix_week_plan_owner_status", "week_plan", ["owner_id", "status"], unique=False
            )

    if not _table_exists("week_plan_item"):
        op.create_table(
            "week_plan_item",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("plan_id", sa.String(length=64), nullable=False),
            sa.Column("month_plan_item_id", sa.String(length=64), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("deliverable", sa.Text(), nullable=True),
            sa.Column("quality", sa.Text(), nullable=True),
            sa.Column("planned_finish_date", sa.Date(), nullable=True),
            sa.Column("completion_status", sa.String(length=32), nullable=True),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("sort_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["plan_id"], ["week_plan.id"]),
            sa.ForeignKeyConstraint(["month_plan_item_id"], ["month_plan_item.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        if not _index_exists("week_plan_item", "ix_week_plan_item_plan_id"):
            op.create_index("ix_week_plan_item_plan_id", "week_plan_item", ["plan_id"], unique=False)
        if not _index_exists("week_plan_item", "ix_week_plan_item_plan_sort"):
            op.create_index(
                "ix_week_plan_item_plan_sort", "week_plan_item", ["plan_id", "sort_no"], unique=False
            )
        if not _index_exists("week_plan_item", "ix_week_plan_item_month_item"):
            op.create_index(
                "ix_week_plan_item_month_item",
                "week_plan_item",
                ["month_plan_item_id"],
                unique=False,
            )


def _create_day_plan() -> None:
    """day_plan + day_plan_item."""
    if not _table_exists("day_plan"):
        op.create_table(
            "day_plan",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=64), nullable=False),
            sa.Column("plan_date", sa.Date(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("remark", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("review_status", sa.String(length=32), nullable=True),
            sa.Column("risk_level", sa.String(length=32), nullable=True),
            sa.Column("leader_comment", sa.Text(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("department_comment", sa.Text(), nullable=True),
            sa.Column("department_reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("approval_due_at", sa.DateTime(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["employee.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (
            ("ix_day_plan_owner_id", "owner_id"),
            ("ix_day_plan_plan_date", "plan_date"),
            ("ix_day_plan_status", "status"),
        ):
            if not _index_exists("day_plan", name):
                op.create_index(name, "day_plan", [column], unique=False)
        if not _index_exists("day_plan", "ix_day_plan_owner_date"):
            op.create_index(
                "ix_day_plan_owner_date", "day_plan", ["owner_id", "plan_date"], unique=False
            )

    if not _table_exists("day_plan_item"):
        op.create_table(
            "day_plan_item",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("plan_id", sa.String(length=64), nullable=False),
            sa.Column("week_plan_item_id", sa.String(length=64), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("sort_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["plan_id"], ["day_plan.id"]),
            sa.ForeignKeyConstraint(["week_plan_item_id"], ["week_plan_item.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        if not _index_exists("day_plan_item", "ix_day_plan_item_plan_id"):
            op.create_index("ix_day_plan_item_plan_id", "day_plan_item", ["plan_id"], unique=False)
        if not _index_exists("day_plan_item", "ix_day_plan_item_plan_sort"):
            op.create_index(
                "ix_day_plan_item_plan_sort", "day_plan_item", ["plan_id", "sort_no"], unique=False
            )
        if not _index_exists("day_plan_item", "ix_day_plan_item_week_item"):
            op.create_index(
                "ix_day_plan_item_week_item",
                "day_plan_item",
                ["week_plan_item_id"],
                unique=False,
            )

    if _table_exists("day_plan") and not _constraint_exists("day_plan", "uq_day_plan_owner_date"):
        with op.batch_alter_table("day_plan", schema=None) as batch_op:
            batch_op.create_unique_constraint("uq_day_plan_owner_date", ["owner_id", "plan_date"])


def downgrade() -> None:
    """Drop the planning domain tables."""
    for table in ("day_plan_item", "day_plan", "week_plan_item", "week_plan", "month_plan_item", "month_plan"):
        if _table_exists(table):
            op.drop_table(table)
