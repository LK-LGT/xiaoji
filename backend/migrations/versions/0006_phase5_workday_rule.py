"""A5 workday rule: persistent calendar rules with versioning

Revision ID: 0006_phase5_workday_rule
Revises: 0005_phase4_result
Create Date: 2026-11-16

A5 工作日规则迁移：把「硬编码返回 1 条」升级为真实表。

背景
----
此前**完全没有工作日规则表**：
- ``GET /system/workday-rules`` 硬编码返回 1 条 ``2024-01-01 元旦``；
- CRUD 端点返回的字段结构（``name``）与前端契约（``ruleDate`` /
  ``ruleType`` / ``forceReport``）完全对不上；
- ``calendarRule`` 消费侧写死 ``ruleType="WORKDAY"`` / ``forceReport=False``。

新增 1 张表：

1. ``workday_rule`` —— 工作日规则（全局 + 个人共用，版本化）

设计要点：
    - **全局 / 个人共用一张表**：用 ``employee_id`` 区分。``NULL`` = 全局
      （节假日、特殊排班）；非空 = 个人规则（请假、出差）。两者结构、
      版本化、生效逻辑完全一致，分表会让查询与版本化逻辑翻倍。
    - **版本化，历史不被覆盖**：同一 ``(rule_date, rule_type, employee_id)``
      保存调整时新增一版，``version_no`` 递增。唯一约束
      ``(rule_date, rule_type, employee_id, version_no)`` 支撑这一点。
    - **默认规则不落库**：没有显式配置的日期按「周一~周五工作日」推导，
      落库反而要维护成千上万行。前端契约的 ``explicit`` 字段表达
      「这天有没有显式配过」。
    - **幂等**：表已存在则跳过，方便在已建表的库上重放。
    - SQLite 兼容：``env.py`` 已配置 ``render_as_batch=True``。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_phase5_workday_rule"
down_revision: str | Sequence[str] | None = "0005_phase4_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    from sqlalchemy import inspect

    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    """Create the workday rule table."""
    _create_workday_rule()


def downgrade() -> None:
    """Drop the workday rule table."""
    if _table_exists("workday_rule"):
        op.drop_table("workday_rule")


def _create_workday_rule() -> None:
    """workday_rule —— 工作日规则（全局 + 个人共用，版本化）。"""
    if _table_exists("workday_rule"):
        return
    op.create_table(
        "workday_rule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_date", sa.Date(), nullable=False),
        sa.Column("rule_type", sa.String(length=32), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=True),
        sa.Column("force_report", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_date", "rule_type", "employee_id", "version_no",
            name="uq_workday_rule_date_type_employee_version",
        ),
    )
    op.create_index("ix_workday_rule_date", "workday_rule", ["rule_date"])
    op.create_index(
        "ix_workday_rule_employee", "workday_rule", ["employee_id", "rule_date"]
    )
    op.create_index(
        "ix_workday_rule_status", "workday_rule", ["status", "rule_date"]
    )
    op.create_index("ix_workday_rule_employee_id", "workday_rule", ["employee_id"])
