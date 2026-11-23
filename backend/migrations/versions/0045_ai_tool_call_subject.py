"""P0-10：``ai_tool_call.subject_id`` —— 审计「谁查了谁」。

Revision ID: 0045_ai_tool_call_subject
Revises: 0044_pan_workload_role_inversion

背景
----
P0 起支持「同部门同级别同事只读互查」——
从此，工具调用的审计里**必须**能回答一个问题：

    「谁查了谁？」

⚠️ **为什么必须是独立字段**
----------------------------
``ai_tool_call`` 原本只有 ``employee_id``（= 提问者）。
「谁查了谁」的第二个主体（目标人）**无处可记**：

- 塞进 ``args`` => 会被 ``args_hash`` 哈希掉 => **查不了**；
- 塞进 ``args_keys`` => 那只是参数的**名字**，不含值；
- 新建一张表 => 与 ``ai_tool_call`` 重复，且漏查的风险更大。

=> 新增独立列 ``subject_id``，并建索引 —— 安全看板要按它聚合。

⚠️ **为什么查自己时是 NULL 而不是等于 employee_id**
---------------------------------------------------
``NULL`` = "目标即提问者本人"（绝大多数调用）。
=> 看板里 ``subject_id IS NULL`` 直接就是"查自己"，不需要 join。
=> 而 ``subject_id = employee_id`` 是**显式的**"查自己" ——
   两种形态都合法，但语义不同（一个是默认值，一个是显式指定）。

⚠️ **不存姓名**
--------------
只存员工 id：
- 姓名会变（改名、跨部门），id 才是稳定主键；
- 避免在审计表里复制 PII（姓名可反查回员工表，不需要冗余）。

⚠️ **既有行怎么办**
------------------
存量行 ``subject_id`` 为 NULL => 语义就是"目标即本人"
（那时压根没有 peer 查询能力）=> **不需要回填**。
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_ai_tool_call_subject"
down_revision = "0044_pan_workload_role_inversion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ⚠️ **nullable=True** —— 存量行不回头填（语义上就是"查本人"）。
    op.add_column(
        "ai_tool_call",
        sa.Column("subject_id", sa.String(length=64), nullable=True),
    )
    # ⚠️ **索引是必须的** —— 安全看板按 (employee_id, subject_id) 聚合，
    #    没有索引就是全表扫。数据量上来后看板会拖垮库。
    op.create_index(
        "ix_ai_tool_call_subject_id",
        "ai_tool_call",
        ["subject_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_tool_call_subject_id", table_name="ai_tool_call")
    op.drop_column("ai_tool_call", "subject_id")
