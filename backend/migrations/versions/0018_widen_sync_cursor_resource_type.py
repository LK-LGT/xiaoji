"""0018: 放宽 ``organization_sync_cursor.resource_type`` 长度（R17 / 需求2）

Revision ID: 0018_widen_sync_cursor_resource_type
Revises: 0017_employee_password_hash
Create Date: 2026-11-16

背景
----
为了让「增量同步」能按**部门**粒度记录分页续传位置，
游标表的 ``resource_type`` 会被写成 ``员工:<department_id>`` 这种形式。

飞书的 ``department_id`` 是 64 位十六进制串，所以完整键长��：

    "员工:" + 64 位 ID = 3 + 64 = 67 字符

而 ``resource_type`` 原本是 ``VARCHAR(32)`` —— **存不下**。
SQLite 不强制长度，但 PostgreSQL 会直接报
``value too long for type character varying(32)``。

本迁移把该列放宽到 ``VARCHAR(128)``：足够放下 64 位部门 ID
加前缀，仍保留「资源类型」的语义。

幂等
----
先检查列的当前长度；已经是 128 就跳过。可重复执行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_widen_sync_cursor_resource_type"
down_revision: str | Sequence[str] | None = "0017_employee_password_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "organization_sync_cursor"
TARGET_LENGTH = 128


def _column_length() -> int | None:
    """读取 resource_type 当前的 VARCHAR 长度；列不存在返回 None。"""
    try:
        inspector = sa.inspect(op.get_bind())
        for col in inspector.get_columns(TABLE):
            if col["name"] == "resource_type":
                length = col.get("type")
                return getattr(length, "length", None)
    except Exception:
        return None
    return None


def upgrade() -> None:
    current = _column_length()
    if current is None:
        print("[0018] organization_sync_cursor 表不存在或无 resource_type 列，跳过")
        return
    if current >= TARGET_LENGTH:
        print(f"[0018] resource_type 已是 VARCHAR({current})，跳过")
        return

    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.alter_column(
            "resource_type",
            existing_type=sa.String(length=current),
            type_=sa.String(length=TARGET_LENGTH),
            existing_nullable=False,
        )
    print(f"[0018] resource_type: VARCHAR({current}) -> VARCHAR({TARGET_LENGTH})")


def downgrade() -> None:
    current = _column_length()
    if current is None:
        print("[0018] 表不存在，跳过")
        return
    if current == TARGET_LENGTH:
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.alter_column(
                "resource_type",
                existing_type=sa.String(length=TARGET_LENGTH),
                type_=sa.String(length=32),
                existing_nullable=False,
            )
        print("[0018] resource_type: VARCHAR(128) -> VARCHAR(32)")
