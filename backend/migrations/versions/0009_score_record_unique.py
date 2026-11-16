"""0009: 补齐 score.record_id 唯一约束（修复 ORM↔迁移漂移）

Revision ID: 0009_score_record_unique
Revises: 0008_phase7_plan_execution_metric
Create Date: 2026-11-16

背景
----
``migrations/versions/0001_baseline.py`` 建 ``score`` 表时就声明了::

    sa.UniqueConstraint("record_id", name="uq_score_record")

但 ``app/domain/models.py`` 的 ``Score`` 模型**漏了**这条约束。

后果是**两套建库路径产出不同 schema**：

- ``alembic upgrade head`` 建出来的库 → **有** ``uq_score_record``
- ``Base.metadata.create_all()`` 建出来的库 → **没有**

而实际运行的 ``backend/data/perf.db`` 正是后者建的（实测：真实库
``pragma index_list('score')`` 只有 PK 自动索引，而 Alembic 库多一个
unique 索引；全库 unique 约束数 27 vs 32）。

为什么这是必须修的
------------------
``score`` 表语义上「一条考核记录一份分数」（重算走 UPDATE 覆盖），
但缺唯一约束时：

1. **并发算分可写出重复行** —— ``review_service.persist_score`` 是
   「先 SELECT 再 INSERT/UPDATE」，没有唯一约束兜底时两个并发事务
   都会走 INSERT 分支。
2. **重复行会打爆所有读路径** —— 下面三处都用
   ``scalar_one_or_none()``，一旦有 2 行就抛 ``MultipleResultsFound``：

   - ``app/services/review_service.py:336``（``persist_score``）
   - ``app/main.py:487``（评分结果页）
   - ``app/routes/v1/assessment_records.py:247``（记录详情）

   即：**一条脏数据会让该记录的算分、详情、结果页全部 500**。

迁移做法
--------
- **幂等**：先查 ``score`` 表上是否已有 ``record_id`` 的唯一约束。
- **不删数据**：建约束前先检测重复行；若有重复，**保留最新一行**
  （``computed_at`` 最大），其余删除 —— 因为重复行本身就是脏数据，
  且会让约束无法建立。
- **兼容 SQLite**（``batch_alter_table`` 会走表重建）。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_score_record_unique"
down_revision: str | Sequence[str] | None = "0008_phase7_plan_execution_metric"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_score_record"


def _has_unique_on_record_id(inspector) -> bool:
    """检查 score.record_id 上是否已有唯一约束。"""
    try:
        constraints = inspector.get_unique_constraints("score")
    except Exception:
        constraints = []
    for c in constraints:
        if list(c.get("column_names") or []) == ["record_id"]:
            return True

    # SQLite / 部分方言把唯一约束暴露成 unique index
    try:
        indexes = inspector.get_indexes("score")
    except Exception:
        indexes = []
    for idx in indexes:
        if idx.get("unique") and list(idx.get("column_names") or []) == ["record_id"]:
            return True
    return False


def _duplicate_record_ids(bind) -> list[str]:
    """找出有重复 Score 行的 record_id。"""
    rows = bind.execute(
        sa.text(
            "SELECT record_id, COUNT(*) AS c FROM score "
            "GROUP BY record_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    return [r[0] for r in rows]


def _dedupe(bind, duplicate_ids: list[str]) -> None:
    """同一 record_id 只保留 computed_at 最新的一行，其余删除。

    保留「最新」是因为算分是幂等覆盖语义 —— 最后写入的那份是最新真值。
    """
    if not duplicate_ids:
        return
    for record_id in duplicate_ids:
        rows = bind.execute(
            sa.text(
                "SELECT id FROM score WHERE record_id = :rid "
                "ORDER BY computed_at DESC, id DESC"
            ),
            {"rid": record_id},
        ).fetchall()
        keep = rows[0][0]
        for (row_id,) in rows[1:]:
            bind.execute(sa.text("DELETE FROM score WHERE id = :i"), {"i": row_id})
        print(f"[0009] score 去重 {record_id}: 保留 {keep}，删除 {len(rows) - 1} 行")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "score" not in inspector.get_table_names():
        return

    if _has_unique_on_record_id(inspector):
        print("[0009] score.record_id 唯一约束已存在，跳过")
        return

    duplicates = _duplicate_record_ids(bind)
    if duplicates:
        print(f"[0009] 发现 {len(duplicates)} 个 record_id 有重复 Score 行，先去重")
        _dedupe(bind, duplicates)

    with op.batch_alter_table("score", schema=None) as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT_NAME, ["record_id"])

    print("[0009] 已补齐 score.record_id 唯一约束")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "score" not in inspector.get_table_names():
        return
    if not _has_unique_on_record_id(inspector):
        return
    with op.batch_alter_table("score", schema=None) as batch_op:
        try:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
        except Exception:
            pass
