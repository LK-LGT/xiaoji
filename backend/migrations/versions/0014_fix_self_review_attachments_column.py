"""0014: 修正 self_review 的列漂移（attachments → attachments_json）

Revision ID: 0014_fix_self_review_attachments_column
Revises: 0013_create_attendance_record
Create Date: 2026-11-16

背景
----
schema 漂移检测（``backend/scripts/check_schema_drift.py``）比对列时发现：

    迁移库：  id, record_id, answers_json, **attachments**,      submit_source, …
    ORM：     id, record_id, answers_json, **attachments_json**,  submit_source, …

根因：``0001_baseline.py`` 建 ``self_review`` 时把列写成了 ``attachments``，
而后来的 ORM 模型用的是 ``attachments_json``。

谁对？
----
**ORM 是对的**，因为业务代码用的是 ``attachments_json``：

    ``app/services/review_service.py:213``
        SelfReview(..., attachments_json=attachments, ...)

也就是说，迁移链建出来的库里**没有业务代码要写的那一列**。
用 Alembic 部署的新环境，`review_service` 写自评时会直接
``no such column: attachments_json`` —— 除非 ORM 的
``Base.metadata.create_all()`` 在别处补建过（实际不会，create_all 整库建）。

⚠️ 而且迁移建出来的 ``attachments`` 是 **nullable**，
ORM 的 ``attachments_json`` 是 **NOT NULL**。
光加一列不够 —— 还要把迁移那列的数据搬过去，再删掉多余的那列。

本迁移做什么
------------
1. 若库里有 ``attachments`` 而没有 ``attachments_json``：
   加 ``attachments_json`` 列 → 把 ``attachments`` 的值搬过去
   （NULL 归一化成 ``[]``，因为 ORM 侧 NOT NULL 且默认 ``[]``）
   → 删掉多余的 ``attachments`` 列。
2. 若两列都有：只把 ``attachments`` 里非 NULL、``attachments_json`` 为 NULL 的
   行补上，然后删掉 ``attachments``。
3. 若只有 ``attachments_json``：什么都不做（已是正确形态）。

幂等
----
以上三种情形都覆盖，重复执行安全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_fix_self_review_attachments_column"
down_revision: str | Sequence[str] | None = "0013_create_attendance_record"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "self_review"
OLD = "attachments"
NEW = "attachments_json"


def _columns() -> set[str]:
    try:
        return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return

    cols = _columns()
    if OLD not in cols:
        print(f"[0014] {TABLE} 没有 {OLD} 列，跳过")
        return

    if NEW not in cols:
        print(f"[0014] {TABLE} 缺 {NEW} 列，先补列")
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(NEW, sa.JSON(), nullable=False, server_default="[]")
            )

    # 搬数据：老列非空、新列为空的行取老列的值
    # ⚠️ SQLite 的 ALTER COLUMN TYPE / SET DEFAULT 不可用，
    #    用 batch_alter_table（表重建）绕过。
    bind.execute(
        sa.text(
            f"UPDATE {TABLE} SET {NEW} = {OLD} "
            f"WHERE ({NEW} IS NULL OR {NEW} = '[]') AND {OLD} IS NOT NULL"
        )
    )

    print(f"[0014] {TABLE} 删除多余的 {OLD} 列")
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        try:
            batch_op.drop_column(OLD)
        except Exception as exc:  # pragma: no cover - SQLite 表重建路径
            print(f"[0014] 删除 {OLD} 列失败: {exc}")
            raise

    print(f"[0014] {TABLE} 列已对齐为 {NEW}")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    if OLD in _columns():
        return
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column(OLD, sa.JSON(), nullable=True))
    print(f"[0014] {TABLE} 已恢复 {OLD} 列")
