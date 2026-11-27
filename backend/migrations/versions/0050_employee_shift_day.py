"""员工排班日历（飞书考勤）—— employee_shift_day / shift_sync_run。

Revision ID: 0050_employee_shift_day
Revises: 0049_knowledge_base

背景
----
系统原来没有显式规则时按「周一~周五上班」推导，但真实排班在飞书考勤：
HR 会按节假日/调休/班次给每个人设置每天的班次。

本迁移新增两张表：

- ``employee_shift_day`` —— 员工 × 日期 × 是否上班（飞书同步结果）；
- ``shift_sync_run``     —— 某月同步批次；只有 SUCCESS 才允许把
  「查不到排班」判定为 UNASSIGNED（未排班），避免同步失败导致误判。
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_employee_shift_day"
down_revision = "0049_knowledge_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_shift_day",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("shift_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("is_workday", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("group_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="feishu"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "work_date", name="uq_employee_shift_day"),
    )
    op.create_index("ix_employee_shift_day_date", "employee_shift_day", ["work_date"])
    op.create_index(
        "ix_employee_shift_day_employee_date",
        "employee_shift_day",
        ["employee_id", "work_date"],
    )

    op.create_table(
        "shift_sync_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="RUNNING"),
        sa.Column("employee_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shift_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_employee_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period"),
    )


def downgrade() -> None:
    op.drop_table("shift_sync_run")
    op.drop_index("ix_employee_shift_day_employee_date", table_name="employee_shift_day")
    op.drop_index("ix_employee_shift_day_date", table_name="employee_shift_day")
    op.drop_table("employee_shift_day")
