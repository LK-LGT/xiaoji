"""A8 fix: persistent plan ↔ Feishu approval instance binding

Revision ID: 0007_phase8_feishu_binding
Revises: 0006_phase5_workday_rule
Create Date: 2026-11-16

计划 ↔ 飞书审批实例持久绑定迁移。

背景
----
A8 首批实现的审批回流依赖 webhook 事件**自带** ``plan_type`` / ``plan_id``。
这只覆盖了「本系统提交 → 飞书收到通知」这一半：

- **员工从飞书侧发起审批**时，事件里只有飞书实例信息（instance_code /
  approval_code / 发起人），**没有本系统的计划 ID** —— 那条链完全接不上。
- 即便是本系统发起的，回流也依赖事件方「记得」带上计划标识，
  一旦事件来源变化（飞书工作流、第三方转发）就会断。

新增 1 张表：

1. ``plan_feishu_binding`` —— 计划与飞书审批实例的绑定关系

设计要点
--------
- **两种关联方式并存**：
  - ``instance_code`` 精确匹配 —— 本系统发起时立刻写入绑定；
  - ``correlation_key`` 关联键 —— 飞书侧发起时，员工把随表单下发的短码
    填进飞书审批表单，回流时按码反查计划。
- **唯一约束保证 1:1**：同一计划只能绑一个实例，同一实例不能绑多个计划，
  同一关联码全局唯一。这样反查不会有歧义。
- **多态关联**：``(plan_type, plan_id)`` 关联三张计划表，**不建物理外键**
  （SQLite 不支持跨表多态外键），与 ``plan_approval`` / ``plan_draft_version``
  保持同一模式。
- **幂等**：表已存在则跳过，方便在已建表的库上重放。
- SQLite 兼容：``env.py`` 已配置 ``render_as_batch=True``。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_phase8_feishu_binding"
down_revision: str | Sequence[str] | None = "0006_phase5_workday_rule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    from sqlalchemy import inspect

    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    """Create the plan ↔ Feishu binding table."""
    _create_plan_feishu_binding()


def downgrade() -> None:
    """Drop the plan ↔ Feishu binding table."""
    if _table_exists("plan_feishu_binding"):
        op.drop_table("plan_feishu_binding")


def _create_plan_feishu_binding() -> None:
    """plan_feishu_binding —— 计划与飞书审批实例的持久绑定。"""
    if _table_exists("plan_feishu_binding"):
        return
    op.create_table(
        "plan_feishu_binding",
        sa.Column("id", sa.String(length=64), nullable=False),
        # 多态关联（不建物理外键）
        sa.Column("plan_type", sa.String(length=16), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        # 飞书侧
        sa.Column("instance_code", sa.String(length=64), nullable=True),
        sa.Column("approval_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_key", sa.String(length=32), nullable=True),
        sa.Column("bind_source", sa.String(length=32), nullable=False),
        sa.Column("instance_status", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # 1:1 保证反查无歧义
        sa.UniqueConstraint(
            "plan_type", "plan_id", name="uq_plan_feishu_binding_plan"
        ),
        sa.UniqueConstraint("instance_code", name="uq_plan_feishu_binding_instance"),
        sa.UniqueConstraint(
            "correlation_key", name="uq_plan_feishu_binding_corr"
        ),
    )
    op.create_index(
        "ix_plan_feishu_binding_plan", "plan_feishu_binding", ["plan_type", "plan_id"]
    )
    op.create_index(
        "ix_plan_feishu_binding_instance",
        "plan_feishu_binding",
        ["instance_code"],
    )
    op.create_index(
        "ix_plan_feishu_binding_corr", "plan_feishu_binding", ["correlation_key"]
    )
    op.create_index(
        "ix_plan_feishu_binding_plan_id", "plan_feishu_binding", ["plan_id"]
    )
