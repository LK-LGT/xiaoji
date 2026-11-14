"""A4 result domain: unified result table + result evidence attachments

Revision ID: 0005_phase4_result
Revises: 0004_phase3_domain_modeling
Create Date: 2026-11-14

A4 成果域迁移：在阶段三领域建模之上补齐**成果表**。

背景
----
此前**完全没有成果表** —— 既无 ``month_result`` 也无 ``week_result``，
``employee_result.py`` 的端点全部是返回空壳的假数据。

新增 2 张表：

1. ``result``          —— 统一成果表（``plan_type`` 区分 month / week）
2. ``result_evidence`` —— 成果证据附件元数据（文件落盘，库内存元数据）

设计要点：
    - **统一表**：月 / 周成果字段语义高度重叠、状态机与证据逻辑完全一致，
      拆两张表会带来大量重复定义与重复查询逻辑。
    - **多态关联**：`result` 通过 ``(plan_type, plan_id)`` 多态关联
      ``month_plan`` / ``week_plan``，**不建物理外键**（SQLite 不支持跨表
      多态外键）。与 ``plan_approval`` / ``plan_draft_version`` 保持同一模式。
    - **幂等**：表已存在则跳过，方便在已建表的库上重放。
    - SQLite 兼容：``env.py`` 已配置 ``render_as_batch=True``。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_phase4_result"
down_revision: str | Sequence[str] | None = "0004_phase3_domain_modeling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    from sqlalchemy import inspect

    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    """Create the result domain tables."""
    _create_result()
    _create_result_evidence()


def downgrade() -> None:
    """Drop the result domain tables (reverse dependency order)."""
    for table in ("result_evidence", "result"):
        if _table_exists(table):
            op.drop_table(table)


def _create_result() -> None:
    """result —— 统一成果表（month / week 共用）。"""
    if _table_exists("result"):
        return
    op.create_table(
        "result",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("plan_type", sa.String(length=16), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("plan_item_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("deliverable", sa.Text(), nullable=False),
        sa.Column("remark", sa.Text(), nullable=False),
        sa.Column("completion_status", sa.String(length=32), nullable=False),
        sa.Column("completion_rate", sa.Float(), nullable=False),
        sa.Column("personal_performance", sa.Float(), nullable=True),
        sa.Column("result_date", sa.Date(), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("approver_id", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approval_comment", sa.Text(), nullable=True),
        sa.Column("evidence_status", sa.String(length=32), nullable=False),
        sa.Column("ai_review_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_type",
            "plan_id",
            "plan_item_id",
            "version_no",
            name="uq_result_plan_item_version",
        ),
    )
    op.create_index(
        "ix_result_owner_type_status",
        "result",
        ["owner_id", "plan_type", "status"],
    )
    op.create_index("ix_result_plan", "result", ["plan_type", "plan_id"])
    op.create_index("ix_result_plan_item", "result", ["plan_type", "plan_item_id"])
    op.create_index("ix_result_owner_id", "result", ["owner_id"])
    op.create_index("ix_result_plan_id", "result", ["plan_id"])
    op.create_index("ix_result_status", "result", ["status"])


def _create_result_evidence() -> None:
    """result_evidence —— 成果证据附件元数据。"""
    if _table_exists("result_evidence"):
        return
    op.create_table(
        "result_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("result_id", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("file_type", sa.String(length=128), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("review_passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["result_id"], ["result.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("result_id", "checksum", name="uq_result_evidence_checksum"),
    )
    op.create_index("ix_result_evidence_result", "result_evidence", ["result_id"])
