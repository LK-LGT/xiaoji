"""phase3 domain modeling: plan versions, approvals, disputes, AI records

Revision ID: 0004_phase3_domain_modeling
Revises: 0003_phase3_planning
Create Date: 2026-11-14

阶段三领域建模迁移：在已有计划主体表（`0003_phase3_planning`）之上补齐
此前缺失的领域表。

新增 9 张表：

计划域（多态关联已有的 month_plan / week_plan / day_plan，不建物理外键）：
    1. ``plan_draft_version``  —— 计划草稿版本快照
    2. ``plan_approval``       —— 计划提交 / 撤回 / 审批流水

争议域：
    3. ``dispute_case``         —— 绩效争议案件
    4. ``dispute_reviewer``     —— 评审人与回避状态
    5. ``dispute_opinion``      —— 评审意见流水（一条意见一版本）

AI 域：
    6. ``ai_review``            —— AI 复核记录
    7. ``ai_conversation``      —— AI 对话会话
    8. ``ai_message``           —— AI 对话消息
    9. ``ai_suggestion_action`` —— AI 建议采纳动作流水

设计要点：
    - 迁移保持**幂等**：表已存在则跳过，方便在已建表的库上重放。
    - ``plan_draft_version`` / ``plan_approval`` 用
      ``(plan_type, plan_id)`` 多态关联三张计划表，避免三选一的外键约束复杂度。
    - ``ai_*`` 表只记录 AI 行为，**不写业务表**（决策⑤：分数、等级、状态
      仍由 L2 业务层与人工确认产出）。
    - SQLite 兼容：``env.py`` 已配置 ``render_as_batch=True``。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_phase3_domain_modeling"
down_revision: str | Sequence[str] | None = "0003_phase3_planning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    from sqlalchemy import inspect

    return table_name in inspect(op.get_bind()).get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    from sqlalchemy import inspect

    if not _table_exists(table_name):
        return False
    inspector = inspect(op.get_bind())
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Create the phase-3 domain modeling tables."""
    _create_plan_draft_version()
    _create_plan_approval()
    _create_dispute_case()
    _create_dispute_reviewer()
    _create_dispute_opinion()
    _create_ai_review()
    _create_ai_conversation()
    _create_ai_message()
    _create_ai_suggestion_action()


def downgrade() -> None:
    """Drop the phase-3 domain modeling tables (reverse dependency order)."""
    for table in (
        "ai_suggestion_action",
        "ai_message",
        "ai_conversation",
        "ai_review",
        "dispute_opinion",
        "dispute_reviewer",
        "dispute_case",
        "plan_approval",
        "plan_draft_version",
    ):
        if _table_exists(table):
            op.drop_table(table)


# ---------------------------------------------------------------------------
# 计划域
# ---------------------------------------------------------------------------


def _create_plan_draft_version() -> None:
    """plan_draft_version —— 计划草稿版本快照。"""
    if _table_exists("plan_draft_version"):
        return
    op.create_table(
        "plan_draft_version",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("plan_type", sa.String(length=16), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("snapshot_schema_version", sa.Integer(), nullable=False),
        sa.Column("base_version_id", sa.String(length=64), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("message_id", sa.String(length=64), nullable=True),
        sa.Column("suggestion_id", sa.String(length=256), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_type", "plan_id", "version_no", name="uq_plan_draft_version_no"
        ),
    )
    op.create_index(
        "ix_plan_draft_version_plan",
        "plan_draft_version",
        ["plan_type", "plan_id", "version_no"],
    )
    op.create_index("ix_plan_draft_version_plan_id", "plan_draft_version", ["plan_id"])
    op.create_index("ix_plan_draft_version_request", "plan_draft_version", ["request_id"])
    op.create_index(
        "ix_plan_draft_version_conversation", "plan_draft_version", ["conversation_id"]
    )
    op.create_index(
        "ix_plan_draft_version_suggestion_id", "plan_draft_version", ["suggestion_id"]
    )


def _create_plan_approval() -> None:
    """plan_approval —— 计划提交 / 撤回 / 审批流水。"""
    if _table_exists("plan_approval"):
        return
    op.create_table(
        "plan_approval",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("plan_type", sa.String(length=16), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_plan_approval_plan",
        "plan_approval",
        ["plan_type", "plan_id", "created_at"],
    )
    op.create_index("ix_plan_approval_plan_id", "plan_approval", ["plan_id"])
    op.create_index("ix_plan_approval_actor", "plan_approval", ["actor_id", "created_at"])
    op.create_index("ix_plan_approval_actor_id", "plan_approval", ["actor_id"])
    op.create_index("ix_plan_approval_dedup", "plan_approval", ["dedup_key"])


# ---------------------------------------------------------------------------
# 争议域
# ---------------------------------------------------------------------------


def _create_dispute_case() -> None:
    """dispute_case —— 绩效争议案件。"""
    if _table_exists("dispute_case"):
        return
    op.create_table(
        "dispute_case",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_no", sa.String(length=64), nullable=False),
        sa.Column("appeal_id", sa.String(length=64), nullable=True),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("dispute_subject", sa.String(length=256), nullable=False),
        sa.Column("appeal_title", sa.String(length=256), nullable=False),
        sa.Column("appeal_reason", sa.Text(), nullable=False),
        sa.Column("appeal_status", sa.String(length=32), nullable=False),
        sa.Column("related_result_id", sa.String(length=64), nullable=True),
        sa.Column("result_title", sa.String(length=256), nullable=False),
        sa.Column("result_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("package_status", sa.String(length=32), nullable=False),
        sa.Column("package_items", sa.JSON(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["employee.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
        sa.UniqueConstraint("case_no", name="uq_dispute_case_no"),
    )
    op.create_index("ix_dispute_case_status", "dispute_case", ["status", "created_at"])
    op.create_index("ix_dispute_case_owner", "dispute_case", ["owner_id", "status"])
    op.create_index("ix_dispute_case_owner_id", "dispute_case", ["owner_id"])
    op.create_index("ix_dispute_case_dept", "dispute_case", ["department_id", "status"])
    op.create_index("ix_dispute_case_department_id", "dispute_case", ["department_id"])
    op.create_index("ix_dispute_case_result", "dispute_case", ["related_result_id"])
    op.create_index("ix_dispute_case_appeal_id", "dispute_case", ["appeal_id"])


def _create_dispute_reviewer() -> None:
    """dispute_reviewer —— 评审人与回避状态。"""
    if _table_exists("dispute_reviewer"):
        return
    op.create_table(
        "dispute_reviewer",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("recusal_status", sa.String(length=32), nullable=False),
        sa.Column("recusal_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["case_id"], ["dispute_case.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["employee.id"]),
        sa.UniqueConstraint("case_id", "user_id", name="uq_dispute_reviewer_case_user"),
    )
    op.create_index(
        "ix_dispute_reviewer_case", "dispute_reviewer", ["case_id", "recusal_status"]
    )
    op.create_index("ix_dispute_reviewer_user_id", "dispute_reviewer", ["user_id"])


def _create_dispute_opinion() -> None:
    """dispute_opinion —— 评审意见流水。"""
    if _table_exists("dispute_opinion"):
        return
    op.create_table(
        "dispute_opinion",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("reviewer_id", sa.String(length=64), nullable=False),
        sa.Column("opinion", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["case_id"], ["dispute_case.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["dispute_reviewer.id"]),
        sa.UniqueConstraint(
            "case_id", "reviewer_id", "version_no", name="uq_dispute_opinion_version"
        ),
    )
    op.create_index(
        "ix_dispute_opinion_case", "dispute_opinion", ["case_id", "submitted_at"]
    )
    op.create_index("ix_dispute_opinion_reviewer_id", "dispute_opinion", ["reviewer_id"])


# ---------------------------------------------------------------------------
# AI 域
# ---------------------------------------------------------------------------


def _create_ai_review() -> None:
    """ai_review —— AI 复核记录。"""
    if _table_exists("ai_review"):
        return
    op.create_table(
        "ai_review",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("biz_type", sa.String(length=32), nullable=False),
        sa.Column("biz_id", sa.String(length=64), nullable=False),
        sa.Column("biz_version", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("overall_risk", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_review_biz", "ai_review", ["biz_type", "biz_id", "checked_at"])
    op.create_index("ix_ai_review_hash", "ai_review", ["content_hash"])
    op.create_index("ix_ai_review_status", "ai_review", ["status", "checked_at"])


def _create_ai_conversation() -> None:
    """ai_conversation —— AI 对话会话。"""
    if _table_exists("ai_conversation"):
        return
    op.create_table(
        "ai_conversation",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("scene_code", sa.String(length=64), nullable=False),
        sa.Column("biz_type", sa.String(length=32), nullable=False),
        sa.Column("biz_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["employee.id"]),
    )
    op.create_index(
        "ix_ai_conversation_owner", "ai_conversation", ["owner_id", "status"]
    )
    op.create_index("ix_ai_conversation_owner_id", "ai_conversation", ["owner_id"])
    op.create_index(
        "ix_ai_conversation_scene", "ai_conversation", ["scene_code", "biz_id"]
    )


def _create_ai_message() -> None:
    """ai_message —— AI 对话消息。"""
    if _table_exists("ai_message"):
        return
    op.create_table(
        "ai_message",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("suggestion_id", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ai_conversation.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_ai_message_conversation", "ai_message", ["conversation_id", "created_at"]
    )
    op.create_index("ix_ai_message_request_id", "ai_message", ["request_id"])
    op.create_index("ix_ai_message_suggestion_id", "ai_message", ["suggestion_id"])


def _create_ai_suggestion_action() -> None:
    """ai_suggestion_action —— AI 建议采纳动作流水。"""
    if _table_exists("ai_suggestion_action"):
        return
    op.create_table(
        "ai_suggestion_action",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("suggestion_id", sa.String(length=256), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("applied_fields", sa.JSON(), nullable=False),
        sa.Column("before_hash", sa.String(length=128), nullable=True),
        sa.Column("after_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_suggestion_action_suggestion",
        "ai_suggestion_action",
        ["suggestion_id", "created_at"],
    )
    op.create_index(
        "ix_ai_suggestion_action_actor", "ai_suggestion_action", ["actor_id", "created_at"]
    )
    op.create_index("ix_ai_suggestion_action_actor_id", "ai_suggestion_action", ["actor_id"])
