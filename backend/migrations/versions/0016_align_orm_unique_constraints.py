"""0016: 补齐 0001_baseline 已声明、ORM 曾漏声明的 6 组唯一约束

Revision ID: 0016_align_orm_unique_constraints
Revises: 0015_align_orm_columns
Create Date: 2026-11-16

背景
----
schema 漂移检测发现**反向漂移**：``0001_baseline.py`` 建表时声明了唯一约束，
但 ORM 模型**漏了**对应的声明，于是：

    迁移建库的库    → 有约束
    create_all 建的库 → 没约束（ORM 没声明）

这 6 组约束在迁移链里本来就存在（建表时带的），缺的是 ORM 侧。
本轮已在 ``models.py`` 补齐 ORM 声明；本迁移确保**任何建库路径**
（包括历史上用 ``create_all`` 建、之后才接入迁移的库）都拿得到这组约束。

    assessment_record (task_id, employee_id)     一个考核 × 一个员工只能一条
    self_review       (record_id)                 一条记录只能一份自评
    manager_review    (record_id)                 一条记录只能一份主管评分
    rating_draft      (record_id)                 一条记录只能一份评级草稿
    comment_draft     (record_id)                 一条记录只能一份评语草稿
    rollup            (scope, scope_id, period)   同一汇总维度同一周期只能一行

⚠️ 为什么要专门写一条迁移（0012 里已经列过同样的清单）：
``0012`` 已经在这两个库上跑过，**不会重跑**。
但"用 create_all 建库、之后才接入迁移"的库（真实开发库就是这种），
必须有一条**在 0012 之后**的迁移来补这组约束。

去重
----
加约束前先查重。约束缺失期间可能已写入重复行 ——
去重时保留**最早**的一行（``id`` 最小），因为先写入的那份是权威数据。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_align_orm_unique_constraints"
down_revision: str | Sequence[str] | None = "0015_align_orm_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINTS: list[tuple[str, str, list[str]]] = [
    ("assessment_record", "uq_assessmentrecord_task_employee", ["task_id", "employee_id"]),
    ("self_review", "uq_self_review_record", ["record_id"]),
    ("manager_review", "uq_manager_review_record", ["record_id"]),
    ("rating_draft", "uq_rating_draft_record", ["record_id"]),
    ("comment_draft", "uq_comment_draft_record", ["record_id"]),
    ("rollup", "uq_rollup_scope_period", ["scope", "scope_id", "period"]),
]


def _inspector():
    return sa.inspect(op.get_bind())


def _has_unique_on(table: str, columns: list[str]) -> bool:
    insp = _inspector()
    try:
        for uc in insp.get_unique_constraints(table):
            if sorted(c["column_name"] for c in uc.get("column_names", [])) == sorted(columns):
                return True
    except Exception:
        pass
    try:
        for idx in insp.get_indexes(table):
            if idx.get("unique") and sorted(idx.get("column_names") or []) == sorted(columns):
                return True
    except Exception:
        pass
    return False


def _dedupe(bind, table: str, columns: list[str]) -> None:
    """去掉重复行，保留 id 最小的一行（最早写入的那份是权威数据）。"""
    key_expr = ", ".join(columns)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in columns)
    rows = bind.execute(
        sa.text(
            f"SELECT {key_expr}, COUNT(*) FROM {table} "
            f"WHERE {not_null} GROUP BY {key_expr} HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for row in rows:
        key = row[:-1]
        count = row[-1]
        where = " AND ".join(f"{c} = :v{i}" for i, c in enumerate(columns))
        params = {f"v{i}": v for i, v in enumerate(key)}
        keep = bind.execute(
            sa.text(f"SELECT id FROM {table} WHERE {where} ORDER BY id ASC"), params
        ).fetchall()[0][0]
        bind.execute(
            sa.text(f"DELETE FROM {table} WHERE {where} AND id <> :keep"),
            {**params, "keep": keep},
        )
        print(f"[0016] {table} 去重 {dict(zip(columns, key, strict=True))}: 保留 {keep}，删除 {count - 1} 行")


def upgrade() -> None:
    bind = op.get_bind()
    db_tables = set(sa.inspect(bind).get_table_names())

    for table, name, columns in CONSTRAINTS:
        if table not in db_tables:
            print(f"[0016] 表 {table} 不存在，跳过")
            continue
        have = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if any(c not in have for c in columns):
            print(f"[0016] {table} 缺少列 {columns}，跳过")
            continue
        if _has_unique_on(table, columns):
            print(f"[0016] {table} 已有 {columns} 的唯一约束，跳过")
            continue

        _dedupe(bind, table, columns)

        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_unique_constraint(name, columns)
        print(f"[0016] {table} 已补唯一约束 {name} {columns}")


def downgrade() -> None:
    bind = op.get_bind()
    db_tables = set(sa.inspect(bind).get_table_names())
    for table, name, columns in CONSTRAINTS:
        if table not in db_tables:
            continue
        if not _has_unique_on(table, columns):
            continue
        with op.batch_alter_table(table, schema=None) as batch_op:
            try:
                batch_op.drop_constraint(name, type_="unique")
            except Exception:
                pass
        print(f"[0016] {table} 已移除唯一约束 {name}")
