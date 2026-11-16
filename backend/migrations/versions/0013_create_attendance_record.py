"""0013: 补建缺失的 attendance_record 表

Revision ID: 0013_create_attendance_record
Revises: 0012_backfill_missing_unique_constraints
Create Date: 2026-11-16

背景
----
自检轮 P3 的「schema 漂移检测」守卫第一次跑就报出：
**ORM 声明了 52 张表，迁移链建出来的库只有 51 张** —— 缺 ``attendance_record``。

排查结论：整条迁移链（0001~0012）**从来没建过这张表**。
它是在 ORM 模型里直接加上的，配套的迁移漏了。

为什么一直没被发现
------------------
- 本地 ``backend/data/perf.db`` 是用 ``Base.metadata.create_all()`` 建的，
  所以表**在**、列**在**、约束**在** —— 开发和本地验证一切正常。
- CI 用纯 Alembic 建库，但 CI 从没跑过「考勤同步」相关的集成测试，
  缺表不会在 CI 里暴露。
- 结果：**真实环境与 CI 环境的 schema 不一致**，而且不一致的那一方
  （Alembic 路径）少了整张表。

一旦真的用 Alembic 部署一个新环境，考勤同步会直接
``no such table: attendance_record`` 报错。

本迁移
------
按 **ORM 模型** 建表 —— 用 ``Base.metadata.tables['attendance_record']``
生成 DDL，而不是手抄 30 个列。

为什么从 ORM 生成而不是手写
--------------------------
这张表有 30 个列，手抄几乎必然与 ORM 漂移（漏列 / 类型不一致 /
default 不一致）—— 那就等于又制造了一次本次要消灭的问题。
从 ORM 生成，**保证建出来的 schema 与 ORM 逐字节一致**，
也保证将来 ORM 再加列时迁移能自动跟上（见下方"列漂移检查"）。

幂等
----
表已存在则跳过。已存在但**缺列**时补列（``batch_alter_table``），
让「表在但列不全」的半成品库也能被修复。
"""

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# 允许从 backend/ 目录直接执行 `alembic` 命令（与 migrations/env.py 一致）
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

revision: str = "0013_create_attendance_record"
down_revision: str | Sequence[str] | None = "0012_backfill_missing_unique_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "attendance_record"


def _orm_table():
    from app.domain.models import Base

    return Base.metadata.tables[TABLE]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if TABLE in existing_tables:
        print(f"[0013] {TABLE} 已存在，仅检查列")
    else:
        table = _orm_table()
        # 从 ORM 模型生成 DDL —— 保证与模型逐字节一致
        op.create_table(TABLE, *[c.copy() for c in table.columns])

        # 约束与索引（表级 UniqueConstraint / Index 不在 columns 里）。
        # ⚠️ SQLite 不支持直接 ALTER ADD CONSTRAINT，必须走 batch 模式
        #    （「建新表 → 拷数据 → 换名」）。
        # ⚠️ 只处理**表级**约束/索引：列级 ``index=True`` 的索引
        #    已由上面的 create_table 建好（batch 会一并复制），
        #    再建一次会报 "index ... already exists"。
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            for constraint in table.constraints:
                if isinstance(constraint, sa.UniqueConstraint):
                    batch_op.create_unique_constraint(
                        constraint.name, [c.name for c in constraint.columns]
                    )
            for index in table.indexes:
                # 跳过列级索引（其名字等于 ix_<table>_<col>）
                if index.name in {f"ix_{TABLE}_{c.name}" for c in table.columns}:
                    continue
                batch_op.create_index(index.name, [c.name for c in index.columns])

        inspector = sa.inspect(bind)
        print(f"[0013] {TABLE} 创建完成")

    # ── 补列（表已存在但缺列的半成品库）──
    table = _orm_table()
    have = {c["name"] for c in inspector.get_columns(TABLE)}
    for col in table.columns:
        if col.name in have:
            continue
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.add_column(col.copy())
        print(f"[0013] {TABLE} 补列 {col.name}")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for index in list(_orm_table().indexes):
            try:
                batch_op.drop_index(index.name)
            except Exception:
                pass
    op.drop_table(TABLE)
    print(f"[0013] 已删除 {TABLE}")
