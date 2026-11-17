"""0023: 飞书机器人会话表（FS-3 工作台机器人会话填表）

Revision ID: 0023_bot_session
Revises: 0022_normalize_period_format
Create Date: 2026-11-17

背景
----
飞书机器人会话填表需要两张表：

``bot_session``
    机器人会话（状态载体）。机器人与员工的对话是**异步**的：
    员工发消息 → 机器人回卡片 → 员工点卡片 → 机器人收到回调。
    过程中可能隔几分钟，也可能跨进程 → 会话状态**必须落库**，
    放内存的话进程一重启员工就看到「我明明填了一半」。

``bot_message``
    消息流水（收发两侧都记）。用途：
    - **排障** —— 员工说「机器人没回我」，得能看到到底发出去没有；
    - **幂等** —— 飞书会重推事件，靠 ``dedup_key`` 唯一约束挡住重复处理；
    - **审计** —— 谁在什么时候让机器人做了什么。

⚠️ **幂等靠数据库唯一约束，不靠应用层先查后写**
--------------------------------------------------------
应用层「先 SELECT 再 INSERT」在并发下有竞态：两个请求同时 SELECT 都没查到，
然后都 INSERT → 产生两条草稿。唯一约束在**存储层**挡住，
无论多少并发都只会有一行。

⚠️ **只存标识，不存飞书 token**
--------------------------
``open_id`` / ``user_id`` / ``employee_id`` 都是**标识**，不是凭证。
本表不存任何 token —— 会话凭据的生命周期由 ``feishu_oauth`` 那边管理。

⚠️ **不物理删除历史行**
----------------------
``bot_message`` 是审计流水，删了就查不到「谁在什么时候让机器人做了什么」。
需求侧的清理应该用**时间窗**（比如保留 90 天），
而不是让业务代码随手 ``DELETE``。

幂等
----
建表前先检查是否已存在，可重复执行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_bot_session"
down_revision: str | Sequence[str] | None = "0022_normalize_period_format"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SESSION_TABLE = "bot_session"
MESSAGE_TABLE = "bot_message"


def _table_exists(name: str) -> bool:
    try:
        return sa.inspect(op.get_bind()).has_table(name)
    except Exception:
        return False


def upgrade() -> None:
    # ── bot_session ─────────────────────────────────────────────
    if _table_exists(SESSION_TABLE):
        print(f"[0023] {SESSION_TABLE} 已存在，跳过")
    else:
        op.create_table(
            SESSION_TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column("open_id", sa.String(length=128), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=True),
            sa.Column("app_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column(
                "employee_id",
                sa.String(length=64),
                sa.ForeignKey("employee.id"),
                nullable=True,
            ),
            sa.Column("state", sa.String(length=32), nullable=False, server_default="idle"),
            sa.Column("intent", sa.String(length=32), nullable=False, server_default="none"),
            sa.Column("plan_month", sa.String(length=16), nullable=True),
            sa.Column("plan_id", sa.String(length=64), nullable=True),
            sa.Column("collected", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("last_message_id", sa.String(length=128), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
            ),
            sa.Column(
                "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
            ),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            # ⚠️ 同一员工 + 同一应用只能有一条会话
            sa.UniqueConstraint("open_id", "app_id", name="uq_bot_session_open_id_app"),
        )
        op.create_index(f"ix_{SESSION_TABLE}_open_id", SESSION_TABLE, ["open_id"])
        op.create_index(f"ix_{SESSION_TABLE}_state", SESSION_TABLE, ["state"])
        op.create_index(f"ix_{SESSION_TABLE}_employee_id", SESSION_TABLE, ["employee_id"])
        print(f"[0023] 已建 {SESSION_TABLE}")

    # ── bot_message ─────────────────────────────────────────────
    if _table_exists(MESSAGE_TABLE):
        print(f"[0023] {MESSAGE_TABLE} 已存在，跳过")
    else:
        op.create_table(
            MESSAGE_TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                sa.String(length=64),
                sa.ForeignKey("bot_session.id"),
                nullable=True,
            ),
            sa.Column("direction", sa.String(length=16), nullable=False),
            sa.Column(
                "message_type", sa.String(length=32), nullable=False, server_default="text"
            ),
            # ⚠️ 幂等键 —— 唯一约束在存储层挡住重复处理
            sa.Column("dedup_key", sa.String(length=160), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
            ),
            sa.UniqueConstraint("dedup_key", name="uq_bot_message_dedup_key"),
        )
        op.create_index(f"ix_{MESSAGE_TABLE}_session_id", MESSAGE_TABLE, ["session_id"])
        op.create_index(f"ix_{MESSAGE_TABLE}_direction", MESSAGE_TABLE, ["direction"])
        print(f"[0023] 已建 {MESSAGE_TABLE}")


def downgrade() -> None:
    # ⚠️ 顺序与建表相反：先删引用方（bot_message），再删被引用方（bot_session）。
    #   反过来删会撞外键约束。
    if _table_exists(MESSAGE_TABLE):
        op.drop_table(MESSAGE_TABLE)
        print(f"[0023] 已移除 {MESSAGE_TABLE}")
    if _table_exists(SESSION_TABLE):
        op.drop_table(SESSION_TABLE)
        print(f"[0023] 已移除 {SESSION_TABLE}")
