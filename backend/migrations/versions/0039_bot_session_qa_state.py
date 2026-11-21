"""P0-6：``bot_session.qa_state`` —— 问答会话上下文槽位。

Revision ID: 0039_bot_session_qa_state
Revises: 0038_pan_koubo_binding

背景
----
P0-5 引入多轮会话上下文（周期、主体、话题、最近几轮），
需要一个**持久化**位置 —— 否则飞书机器人每次事件都是新进程，
上下文丢了，「那扣分原因呢」就不知道该查哪个月。

⚠️ **为什么用 JSON 列而不是多列**
--------------------------------
槽位结构会演进（后续可能加"聚焦指标""对比维度"等）。
JSON 免去频繁 DDL —— 加一个槽位不用改表结构。

⚠️ **为什么不复用 ``bot_session.collected``**
------------------------------------------
两者生命周期不同：

    ==============  ==========================  ====================
    字段             含义                        生命周期
    ==============  ==========================  ====================
    ``collected``   填报流程收集到的字段（业务态）  随「填报/取消」走
    ``qa_state``    问答上下文槽位（对话态）        随「问答会话」走
    ==============  ==========================  ====================

混用会在「取消填报」时把问答上下文一起清掉 ——
用户刚问完绩效，顺手取消个月计划填报，结果问答上下文也没了。

⚠️ **既有行怎么办**
------------------
存量行 ``qa_state`` 为 NULL ⇒ 读出来当作空状态（``ConversationState.from_dict``
对 None/非 dict 都容错）⇒ **不需要回填**。
但列上仍设 ``server_default='{}'`` 且 ``nullable=False``，
这样新写入的行不会是 NULL —— 避免"老行 NULL、新行 '{}'"的两种形态并存。
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_bot_session_qa_state"
down_revision = "0038_pan_koubo_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ⚠️ **批量回填空字典** 而不是留 NULL ——
    #    让列上始终是 '{}' 或 JSON 对象，不出现 NULL 与 '{}' 两种形态并存。
    op.add_column(
        "bot_session",
        sa.Column(
            "qa_state",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    # SQLite 的 ALTER TABLE ADD COLUMN 带 server_default 是安全的；
    # 但历史行仍可能是 NULL —— 显式回填一次。
    op.execute(
        sa.text("update bot_session set qa_state = '{}' where qa_state is null")
    )


def downgrade() -> None:
    op.drop_column("bot_session", "qa_state")
