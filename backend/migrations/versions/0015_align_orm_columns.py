"""0015: 通用列对齐 —— 把 ORM 声明但迁移库缺失的列补上

Revision ID: 0015_align_orm_columns
Revises: 0014_fix_self_review_attachments_column
Create Date: 2026-11-16

背景
----
schema 漂移检测逐项排查后，发现迁移链**漏建了若干列**（ORM 有、迁移库没有）：

    assessment_record.manager_comment
    assessment_record.self_review_content
    comment_draft.created_at
    rating_draft.confirmed_at

这些都是**业务代码实际在写**的列 —— ORM 里声明了，
``review_service`` / ``ai_review_service`` 写库时就会带上。
用 Alembic 部署的新环境，这些写入会直接 ``no such column``。

为什么本迁移写成"通用"而不是一条条补
--------------------------------------
逐列手写迁移的毛病是**下一个漏列时又得写一条迁移** ——
本轮已经补过 3 条（0012 补约束、0013 补表、0014 改列名），
再加 4 条列迁移只会让迁移链越来越长、越来越难维护。

所以这里改成**通用对齐**：从 ORM 元数据读出每张表的列，
凡是迁移库缺的，就用 ORM 的列定义补上（类型、nullable、default 全部照抄）。

⚠️ 关键约束：**只加列，绝不删列、绝不改类型**。
    - 加列是安全的（旧数据为 NULL/默认值）。
    - 删列会丢数据；改类型在 SQLite 上要表重建，风险更高。
      这两件事留给人工判断，不在迁移里自动做。

安全性
------
- 每列都先查存在性，存在则跳过（幂等）。
- 只处理"ORM 有、库里没有"的列 —— 反向的（库里多出来的）只**报告**不处理。
- NOT NULL 且无默认值的列在已有数据的表上无法直接加 —— SQLite 会报错。
  这类列会**跳过并报告**，需要人工确认（不猜默认��）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_align_orm_columns"
down_revision: str | Sequence[str] | None = "0014_fix_self_review_attachments_column"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 这些表只补列，不动其它表（避免把无关的历史漂移也带进来）
TABLES = [
    "assessment_record",
    "comment_draft",
    "rating_draft",
]


def _orm_tables():
    from app.domain.models import Base

    return Base.metadata.tables


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    db_tables = set(inspector.get_table_names())
    orm_tables = _orm_tables()

    added = 0
    skipped: list[str] = []
    extra: list[str] = []

    for table_name in TABLES:
        if table_name not in db_tables:
            continue
        table = orm_tables.get(table_name)
        if table is None:
            continue

        have = {c["name"] for c in inspector.get_columns(table_name)}
        orm_cols = {c.name for c in table.columns}

        for col_name in sorted(orm_cols - have):
            col = table.columns[col_name]
            # NOT NULL 且无默认值 → 已有数据的表上无法直接加，必须人工处理
            if not col.nullable and col.default is None and col.server_default is None:
                skipped.append(f"{table_name}.{col_name}（NOT NULL 且无默认值）")
                continue
            with op.batch_alter_table(table_name, schema=None) as batch_op:
                batch_op.add_column(col.copy())
            print(f"[0015] {table_name} 补列 {col_name}")
            added += 1

        for col_name in sorted(have - orm_cols):
            extra.append(f"{table_name}.{col_name}")

    if skipped:
        print("[0015] 以下列需要人工确认（NOT NULL 且无默认值，未自动补）:")
        for s in skipped:
            print(f"      - {s}")
    if extra:
        print("[0015] 以下列在迁移库里有、ORM 里没有（未删除，仅报告）:")
        for e in extra:
            print(f"      - {e}")
    print(f"[0015] 共补列 {added} 个")


def downgrade() -> None:
    print("[0015] 补的列不自动回滚（删列会丢数据，需人工决定）")
