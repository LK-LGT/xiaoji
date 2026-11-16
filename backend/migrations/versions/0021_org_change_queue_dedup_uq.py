"""0021: 事件队列去重的数据库级唯一约束

Revision ID: 0021_org_change_queue_dedup_uq
Revises: 0020_org_change_queue
Create Date: 2026-11-16

背景
----
R18 的事件入队用的是「查重 → 插入」（check-then-act）：

    existing = SELECT ... WHERE dedup_key = :key AND status IN (未完成)
    if existing: return
    INSERT ...

⚠️ **为什么在 PostgreSQL 上这是个真缺陷**
-----------------------------------------
两个并发 webhook 推送**同一个** ``event_id`` 时：

1. 实例 A 查重 → 没查到
2. 实例 B 查重 → 也没查到
3. A 插入
4. B 插入 → **产生两条重复队列项**

结果：同一个变更被消费两次 → 重复回查飞书（浪费配额）、
重复 upsert、批次记录混乱。

SQLite 上因为有**全局写锁**，这个窗口期被掩盖了；
但 **PostgreSQL 没有全局写锁**，竞态会真实发生。

对策
----
用数据库的**部分唯一索引**兜底：

    UNIQUE (dedup_key) WHERE status IN ('pending','processing','failed')

⚠️ **为什么是「部分」唯一索引，而不是普通 UNIQUE**
----------------------------------------------------
去重只应作用于「**未完成**」的记录。

一条已 ``processed`` 的历史记录**不该**挡住该资源的**下一次**变更 ——
否则同一员工的第二次改名会被永久丢弃。

普通 UNIQUE (dedup_key) 会把历史记录也算进去，导致这个 bug。
部分唯一索引（``WHERE`` 子句）正好表达「只对未完成的行生效」。

幂等
----
建索引前先检查是否已存在，可重复执行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_org_change_queue_dedup_uq"
down_revision: str | Sequence[str] | None = "0020_org_change_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "org_change_queue"
INDEX = "uq_org_change_queue_active_dedup"

#: 只有这些状态算「未完成」，参与去重。
ACTIVE_STATUSES = ("pending", "processing", "failed")


def _index_exists() -> bool:
    try:
        inspector = sa.inspect(op.get_bind())
        indexes = inspector.get_indexes(TABLE)
        return any(idx.get("name") == INDEX for idx in indexes)
    except Exception:
        return False


def upgrade() -> None:
    if _index_exists():
        print(f"[0021] 索引 {INDEX} 已存在，跳过")
        return

    # ⚠️ 部分唯一索引的 WHERE 子句必须与 ORM 里声明的完全一致，
    #    否则 SQLite 与 PostgreSQL 的实际约束会不一致。
    where = sa.text("status IN ('pending', 'processing', 'failed')")
    op.create_index(
        INDEX,
        TABLE,
        ["dedup_key"],
        unique=True,
        sqlite_where=where,
        postgresql_where=where,
    )
    print(f"[0021] 已建唯一索引 {INDEX}")


def downgrade() -> None:
    if not _index_exists():
        return
    op.drop_index(INDEX, table_name=TABLE)
    print(f"[0021] 已移除索引 {INDEX}")
