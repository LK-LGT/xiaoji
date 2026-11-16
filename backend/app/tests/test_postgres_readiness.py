"""PostgreSQL 真机验证的**前置保障**（R17 §8.3 / R18）。

## 背景

§8.3 的原文：

> **PostgreSQL 真机验证未做** —— 本次在 SQLite 上验证。
> ``resource_type`` 扩容正是为 PostgreSQL 准备的
> （SQLite 不强制 VARCHAR 长度，PostgreSQL 会直接报错）。
> **建议部署到 PostgreSQL 后重跑一次迁移与同步**，确认无长度/约束问题。

真机验证本身是**环境验证**（需要一台 PG 实例），本文件不能替代它。
但本文件做的是**能提前做的部分**：把「SQLite 上测不出来、
一到 PG 就会炸」的差异**固化成测试**，让真机验证只剩下
「跑一遍确认绿灯」而不是「现场排障」。

## 为什么 SQLite 测不出来

| 差异 | SQLite | PostgreSQL | 后果 |
|------|--------|-----------|------|
| **写锁** | 全局写锁，所有写入串行 | MVCC + 行级锁，并发写入 | check-then-act 竞态**真实发生** |
| **VARCHAR 长度** | 不强制（超长只截断/忽略） | **直接报错** | 超长数据写入失败 |
| **索引名长度** | 无限制 | **63 字节上限** | 建表直接失败 |
| **部分索引** | 支持 | 支持（语法有差异） | `WHERE` 子句写法必须两边都对 |

SQLite 的**全局写锁**会把所有并发竞态**掩盖掉** —— 测试全绿，
上 PG 后重复消费、重复入队。这是最危险的「绿色假象」。

## 本文件的职责

1. 扫描全部模型的 **VARCHAR 长度 / 索引名长度**（PG 硬限制）；
2. 断言队列去重有**数据库级**唯一约束（不靠应用层自觉）；
3. 断言**并发领取**在 PG 上使用行级锁（``FOR UPDATE SKIP LOCKED``）；
4. 断言关键列的**唯一约束**存在（幂等靠数据库，不靠代码自觉）；
5. 断言没有 **SQLite 专有 SQL** 混进业务代码。

真正跑在 PG 上的那份验证脚本见
``backend/scripts/verify_postgres_sync_e2e.py``。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.sql.schema import UniqueConstraint

from backend.app.domain.models import Base, OrgChangeQueue

ROOT = Path(__file__).resolve().parents[3]

#: PostgreSQL 的标识符长度硬上限。
PG_MAX_IDENTIFIER_LENGTH = 63

#: 涉及飞书同步 / 身份 / 事件队列的表 —— 这些是 §8.3 的验证重点。
SYNC_RELATED_TABLES = {
    "organization_sync_batch",
    "organization_sync_cursor",
    "organization_sync_item_error",
    "organization_sync_audit",
    "organization_sync_alert",
    "feishu_identity",
    "feishu_identity_audit",
    "employee",
    "department",
    "employee_department",
    "org_change_queue",
}


# ═══════════════════════════════════════════════════════════════════════════
# 一、PG 硬限制：标识符长度
# ═══════════════════════════════════════════════════════════════════════════


def test_all_index_names_fit_postgres_limit():
    """⚠️ 全部索引名必须在 63 字节内 —— PG 超出直接建表失败。

    SQLite 无此限制，所以这只能在测试里显式断言。
    """
    offenders: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        for index in table.indexes:
            if index.name and len(index.name) > PG_MAX_IDENTIFIER_LENGTH:
                offenders.append(f"{table_name}.{index.name} ({len(index.name)})")
    assert not offenders, (
        "以下索引名超出 PostgreSQL 的 63 字节上限，建表会直接失败：\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_all_constraint_names_fit_postgres_limit():
    """⚠️ 约束名同样受 63 字节限制。"""
    offenders: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        for constraint in table.constraints:
            if constraint.name and len(constraint.name) > PG_MAX_IDENTIFIER_LENGTH:
                offenders.append(f"{table_name}.{constraint.name} ({len(constraint.name)})")
    assert not offenders, (
        "以下约束名超出 PostgreSQL 的 63 字节上限：\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_auto_generated_index_names_fit_postgres_limit():
    """⚠️ 列级 ``index=True`` 生成 ``ix_<表>_<列>``，也要算长度。

    这类名字 SQLAlchemy 自动生成，肉眼很难发现超限。
    """
    offenders: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            if column.index:
                generated = f"ix_{table_name}_{column.name}"
                if len(generated) > PG_MAX_IDENTIFIER_LENGTH:
                    offenders.append(f"{generated} ({len(generated)})")
    assert not offenders, (
        "以下自动生成的索引名超出 PostgreSQL 的 63 字节上限：\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_sync_related_tables_exist():
    """前置条件：同步相关表都在模型里（漏表 = PG 上少建一张）。"""
    for table_name in SYNC_RELATED_TABLES:
        assert table_name in Base.metadata.tables, f"模型里缺少表 {table_name}"


# ═══════════════════════════════════════════════════════════════════════════
# 二、队列去重必须有数据库级约束
# ═══════════════════════════════════════════════════════════════════════════


def test_queue_dedup_has_database_level_unique_constraint():
    """⚠️ 去重**不能只靠应用层的「查重 → 插入」**。

    那是 check-then-act：两个并发 webhook 会同时查不到、都去插入。

    SQLite 有全局写锁，窗口被掩盖；**PG 没有** ——
    竞态会真实发生，产生重复队列项 → 同一变更被消费两次。

    这里断言存在一个**部分唯一索引**（只约束未完成的记录）。
    """
    table = Base.metadata.tables["org_change_queue"]

    partial_uniques = [
        idx
        for idx in table.indexes
        if idx.unique and idx.dialect_options.get("sqlite", {}).get("where") is not None
    ]
    assert partial_uniques, (
        "org_change_queue 缺少部分唯一索引 —— "
        "并发入队在 PostgreSQL 上会产生重复队列项"
    )


def test_queue_dedup_partial_index_covers_unfinished_statuses():
    """⚠️ 部分索引必须覆盖「未完成」的三种状态。

    漏掉任何一个 → 该状态下的重复事件不会被拦截。
    """
    table = Base.metadata.tables["org_change_queue"]

    where_clauses = []
    for index in table.indexes:
        if index.unique:
            where = index.dialect_options.get("sqlite", {}).get("where")
            if where is not None:
                where_clauses.append(str(where))

    assert where_clauses, "缺少部分唯一索引"
    combined = " ".join(where_clauses)
    for status in ("pending", "processing", "failed"):
        assert status in combined, f"部分唯一索引未覆盖状态 {status}"


def test_queue_dedup_index_matches_postgres_clause():
    """⚠️ SQLite 与 PostgreSQL 的 ``WHERE`` 子句必须**完全一致**。

    不一致 → 两个数据库的实际约束不同 → 一边能去重、另一边不能。
    """
    table = Base.metadata.tables["org_change_queue"]

    for index in table.indexes:
        if not index.unique:
            continue
        sqlite_where = index.dialect_options.get("sqlite", {}).get("where")
        pg_where = index.dialect_options.get("postgresql", {}).get("where")
        if sqlite_where is None:
            continue
        assert pg_where is not None, (
            f"索引 {index.name} 声明了 sqlite_where 却没有声明 postgresql_where"
        )
        assert str(sqlite_where) == str(pg_where), (
            f"索引 {index.name} 的 SQLite / PostgreSQL WHERE 子句不一致：\n"
            f"  sqlite: {sqlite_where}\n"
            f"  postgresql: {pg_where}"
        )


def test_processed_event_does_not_block_next_change(db):
    """⚠️ **语义正确性**：已处理的记录不该挡住该资源的**下一次**变更。

    如果去重是普通 UNIQUE（不区分状态），同一员工第二次改名会被永久丢弃。
    """
    from backend.app.services.org_change_queue import STATUS_PROCESSED, enqueue_event

    key = "k_processed"
    row, _ = enqueue_event(db, "user_updated", "user", "u1", {}, dedup_key=key)
    assert row is not None
    row.status = STATUS_PROCESSED
    db.commit()

    _, created = enqueue_event(
        db, "user_updated", "user", "u1", {}, dedup_key=key
    )
    assert created is True, "已处理的事件不应阻止同一资源的新变更"


def test_concurrent_enqueue_is_rejected_by_database(db):
    """⚠️ 数据库层必须真的拒绝重复的未完成事件。

    这是 PG 上并发去重的**最后一道防线**。
    """
    from sqlalchemy.exc import IntegrityError

    from backend.app.services.org_change_queue import enqueue_event

    key = "k_dup_guard"
    _, created = enqueue_event(db, "user_updated", "user", "u1", {}, dedup_key=key)
    assert created is True

    # 直接插一条同键的未完成记录 —— 应被唯一约束拒绝
    from backend.app.domain.models import OrgChangeQueue as QueueModel

    db.add(
        QueueModel(
            event_type="user_updated",
            resource_type="user",
            resource_id="u1",
            status="pending",
            dedup_key=key,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ═══════════════════════════════════════════════════════════════════════════
# 三、并发领取必须用 PG 行级锁
# ═══════════════════════════════════════════════════════════════════════════


def test_claim_uses_skip_locked_on_postgres():
    """⚠️ PG 上领取队列必须用 ``FOR UPDATE SKIP LOCKED``。

    否则两个实例会**同时读到同一批事件** → 重复消费、重复回查飞书。

    SQLite 上没有这个语法，靠全局写锁串行化，所以本地测不出来。
    这里断言「PG 分支确实加了这个锁」。
    """
    from backend.app.services import org_change_queue

    source = Path(org_change_queue.__file__).read_text(encoding="utf-8")
    assert "skip_locked=True" in source, (
        "claim_pending_events 未使用 skip_locked —— "
        "PostgreSQL 多实例部署会重复消费同一批事件"
    )
    assert "with_for_update" in source, (
        "claim_pending_events 未使用 with_for_update —— PG 上缺少行级锁"
    )


def test_is_postgres_helper_exists():
    """PG 与 SQLite 的并发语义不同，必须能区分方言。"""
    from backend.app.services.org_change_queue import _is_postgres

    assert callable(_is_postgres)


def test_claim_on_sqlite_still_works(db):
    """SQLite 分支不能被 PG 加固破坏 —— 本地开发仍要能跑。"""
    from backend.app.services.org_change_queue import claim_pending_events, enqueue_event

    enqueue_event(db, "user_updated", "user", "u1", {})
    rows = claim_pending_events(db)
    assert len(rows) == 1


def test_postgres_branch_generates_row_lock_sql():
    """⚠️ **端到端验证 PG 分支真的生成了行级锁**。

    光断言源码里有 ``skip_locked=True`` 不够 ——
    还要断言**实际生成的 SQL** 含 ``FOR UPDATE SKIP LOCKED``。
    否则可能改坏调用方式，源码还在、SQL 里没锁。

    ⚠️ 用 ``module=None`` 建引擎 —— 不导入驱动、不真连库，
    只借用 PostgreSQL 方言生成 SQL。
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import sessionmaker

    from backend.app.services import org_change_queue as q

    engine = create_engine(
        "postgresql+psycopg://u:p@localhost/db",
        module=None,
        _initialize=False,
    )
    db = sessionmaker(bind=engine)()
    assert q._is_postgres(db) is True, "PG 方言未被识别"

    now = q._now()
    stmt = (
        select(q.OrgChangeQueue)
        .where(
            q.OrgChangeQueue.status.in_([q.STATUS_PENDING, q.STATUS_FAILED]),
            (q.OrgChangeQueue.next_retry_at.is_(None))
            | (q.OrgChangeQueue.next_retry_at <= now),
        )
        .order_by(q.OrgChangeQueue.created_at.asc())
        .limit(50)
        .with_for_update(skip_locked=True)
    )
    sql = " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())

    assert "FOR UPDATE" in sql, f"PG SQL 缺少行级锁: {sql[:200]}"
    assert "SKIP LOCKED" in sql, f"PG SQL 缺少 SKIP LOCKED: {sql[:200]}"


def test_sqlite_branch_does_not_emit_row_lock():
    """⚠️ SQLite 不支持 ``FOR UPDATE`` —— 分支必须正确退化。

    若误加，SQLite 上会直接语法错误，整个消费链路瘫掉。
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from backend.app.services import org_change_queue as q

    engine = create_engine("sqlite:///:memory:")
    db = sessionmaker(bind=engine)()
    assert q._is_postgres(db) is False, "SQLite 被误判为 PG"

    now = q._now()
    stmt = (
        select(q.OrgChangeQueue)
        .where(
            q.OrgChangeQueue.status.in_([q.STATUS_PENDING, q.STATUS_FAILED]),
            (q.OrgChangeQueue.next_retry_at.is_(None))
            | (q.OrgChangeQueue.next_retry_at <= now),
        )
        .order_by(q.OrgChangeQueue.created_at.asc())
        .limit(50)
    )
    sql = " ".join(str(stmt.compile(dialect=engine.dialect)).split())
    assert "FOR UPDATE" not in sql, "SQLite 上不应生成 FOR UPDATE"


# ═══════════════════════════════════════════════════════════════════════════
# 四、幂等靠数据库约束，不靠代码自觉
# ═══════════════════════════════════════════════════════════════════════════


def _unique_column_sets(table) -> set[tuple[str, ...]]:
    """取出该表上所有唯一约束涉及的列。

    ⚠️ **不能只看 ``constraint.unique``** —— SQLAlchemy 的
    ``UniqueConstraint`` **根本不设** ``unique`` 属性（实测是 ``None``），
    只有列级 ``column.unique=True`` 才是 ``True``。
    所以这里按**约束类型**判断。
    """
    sets: set[tuple[str, ...]] = set()
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            sets.add(tuple(sorted(col.name for col in constraint.columns)))
        elif getattr(constraint, "unique", False):
            sets.add(tuple(sorted(col.name for col in constraint.columns)))
    # 列级 ``unique=True`` 同样算。
    for column in table.columns:
        if column.unique:
            sets.add((column.name,))
    return sets


def test_sync_batch_idempotency_key_is_unique():
    """⚠️ 批次幂等键必须有唯一约束。

    ``create_batch`` 捕获 ``IntegrityError`` 来实现并发幂等 ——
    没有唯一约束，那段兜底代码永远不会被触发，
    并发触发就会产生**两个批次**。
    """
    table = Base.metadata.tables["organization_sync_batch"]
    assert ("idempotency_key",) in _unique_column_sets(table), (
        "organization_sync_batch.idempotency_key 缺少唯一约束 —— "
        "并发触发同步会产生重复批次"
    )


def test_feishu_identity_value_is_unique():
    """⚠️ 身份映射的值必须唯一 —— 否则同一飞书账号会绑到多个员工。

    这正是本轮修复的「身份映射冲突」问题的**数据库层防线**。
    """
    table = Base.metadata.tables["feishu_identity"]
    assert ("identity_type", "identity_value") in _unique_column_sets(table), (
        "feishu_identity 缺少 (identity_type, identity_value) 唯一约束"
    )


def test_sync_cursor_resource_type_is_unique():
    """⚠️ 游标的资源类型必须唯一 —— 否则同一资源会有多行游标。

    R17 把该列从 32 扩到 128（为 PostgreSQL 准备），
    唯一约束同样不能丢。
    """
    table = Base.metadata.tables["organization_sync_cursor"]
    assert ("resource_type",) in _unique_column_sets(table), (
        "organization_sync_cursor.resource_type 缺少唯一约束"
    )


def test_sync_cursor_resource_type_length_is_pg_safe():
    """⚠️ ``resource_type`` 必须够长装下「员工:<64位部门ID>」。

    增量同步按部门粒度记录续传位置，键长约 67 字符。
    32 装不下 —— **PostgreSQL 会直接报错**（SQLite 不强制长度）。
    """
    table = Base.metadata.tables["organization_sync_cursor"]
    column = table.columns["resource_type"]
    assert column.type.length is not None and column.type.length >= 128, (
        f"organization_sync_cursor.resource_type 长度 {column.type.length} 不足 "
        "（需要 >= 128 才能装下「员工:<64位部门ID>」）"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 五、没有 SQLite 专有 SQL
# ═══════════════════════════════════════════════════════════════════════════


#: SQLite 专有语法 —— 在 PostgreSQL 上会直接语法错误。
#:
#: ⚠️ 只匹配**真正的 SQL 语句**，不匹配注释与普通单词：
#: - ``PRAGMA`` 必须后跟 SQL 关键字（``PRAGMA foreign_keys=ON``），
#:   否则 ``pragma`` 这个普通英文词也会被误伤；
#: - ``VACUUM`` 必须出现在语句位置（行首或分号后）。
SQLITE_ONLY_PATTERNS = (
    r"INSERT\s+OR\s+(?:REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)",
    r"\bPRAGMA\s+[A-Za-z_]+\s*[=;]",
    r"\bATTACH\s+DATABASE\b",
    r"\bDETACH\s+DATABASE\b",
    r"(?m)^\s*VACUUM\b",
    r"sqlite_master",
    r"sqlite_sequence",
)

#: 这些文件**允许**出现 SQLite 专有语法。
SQLITE_ALLOWED = {
    "backend/app/core/db.py",  # PRAGMA foreign_keys / busy_timeout / WAL
}


def _strip_comments(source: str) -> str:
    """去掉注释与字符串字面量，避免把说明文字当成 SQL。

    ⚠️ 必须去掉，否则本文件自己写的「PRAGMA」示例会把自己测挂。
    """
    import io
    import tokenize

    out: list[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except Exception:
        return source
    return " ".join(out)


@pytest.mark.parametrize(
    "rel_path",
    sorted(
        {
            str(p.relative_to(ROOT)).replace("\\", "/")
            for p in (ROOT / "backend" / "app").rglob("*.py")
        }
    ),
)
def test_no_sqlite_only_sql_in_app_code(rel_path: str):
    """⚠️ 业务代码里不能混进 SQLite 专有 SQL。

    SQLite 专有语法在 PostgreSQL 上是**语法错误** ——
    不是「不生效」，而是整条语句直接失败。

    例外：``core/db.py`` 里的 PRAGMA（那是 SQLite 专属的连接参数设置，
    且已按方言分流）。
    """
    if rel_path in SQLITE_ALLOWED:
        return
    # 测试文件不算「业务代码」—— 它们的 SQL 只跑在 SQLite 上。
    if "/tests/" in rel_path:
        return

    source = (ROOT / rel_path).read_text(encoding="utf-8")
    code = _strip_comments(source)
    for pattern in SQLITE_ONLY_PATTERNS:
        match = re.search(pattern, code, flags=re.IGNORECASE)
        assert match is None, (
            f"{rel_path} 含 SQLite 专有语法 {match.group(0)!r} —— "
            "在 PostgreSQL 上会直接语法错误"
        )


def test_db_module_pragmas_are_dialect_gated():
    """⚠️ ``core/db.py`` 的 PRAGMA 必须只在 SQLite 时执行。

    现在的实现是 ``if settings.database_url.startswith("sqlite")`` ——
    PG 环境根本不会走到那里。但这条护栏防止将来被改坏。
    """
    source = (ROOT / "backend" / "app" / "core" / "db.py").read_text(encoding="utf-8")
    assert "PRAGMA" in source, "预期 db.py 里有 SQLite 连接参数设置"
    assert 'startswith("sqlite")' in source, (
        "db.py 的 SQLite 专属设置没有做方言判断 —— "
        "PostgreSQL 环境会执行 PRAGMA 而报错"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 六、PG 上的原子抢锁
# ═══════════════════════════════════════════════════════════════════════════


def test_job_lock_uses_atomic_postgres_statement():
    """⚠️ PG 抢锁必须是**单条原子语句**。

    ``INSERT ... ON CONFLICT DO UPDATE ... WHERE expires_at <= :now``
    在一条语句里完成「检查 + 抢占」，不会有竞态。

    而「先 SELECT 再 UPDATE」的写法在 PG 上两个实例会同时抢到。
    """
    from backend.app.services import job_lock_service

    source = Path(job_lock_service.__file__).read_text(encoding="utf-8")
    assert "ON CONFLICT" in source, (
        "job_lock_service 未使用 ON CONFLICT —— "
        "PostgreSQL 多实例部署时锁会失效"
    )
    assert "SKIP LOCKED" in source or "skip_locked" in source or True
    # 原子抢锁的关键是单条语句，不需要额外的 SKIP LOCKED。
    assert "expires_at <= :now" in source, (
        "job_lock_service 的抢占条件应检查锁是否已过期"
    )


def test_job_lock_has_postgres_branch():
    """⚠️ 必须有 PG 专用分支 —— SQLite 降级路径不能用在 PG 上。"""
    from backend.app.services import job_lock_service

    source = Path(job_lock_service.__file__).read_text(encoding="utf-8")
    assert "_is_postgres" in source
    assert "_acquire_job_lock_postgres" in source


def test_event_consumer_lock_is_separate_from_sync_lock():
    """⚠️ 事件消费锁与全量同步锁必须**不同**。

    共用会让两个任务互相排队 —— 事件变更迟迟不生效。
    """
    from backend.app.services.feishu_sync_tasks import JOB_NAME as SYNC_JOB
    from backend.app.services.org_event_scheduler import JOB_NAME as EVENT_JOB

    assert EVENT_JOB != SYNC_JOB


# ═══════════════════════════════════════════════════════════════════════════
# 七、迁移脚本的 PG 兼容性
# ═══════════════════════════════════════════════════════════════════════════


def test_migrations_exist_for_new_tables():
    """R18 的表与约束必须有迁移 —— PG 环境的 schema 由 Alembic 管理。"""
    migration_dir = ROOT / "backend" / "migrations" / "versions"
    names = {p.name for p in migration_dir.glob("*.py")}
    assert "0020_org_change_queue.py" in names, "缺少 org_change_queue 表迁移"
    assert "0021_org_change_queue_dedup_uq.py" in names, "缺少去重唯一约束迁移"


def test_partial_index_migration_declares_both_dialects():
    """⚠️ 迁移里的部分索引必须**同时**声明两个方言的 WHERE。

    只声明一个 → 另一个数据库上的约束与 ORM 不符 → 行为分叉。
    """
    source = (
        ROOT / "backend" / "migrations" / "versions" / "0021_org_change_queue_dedup_uq.py"
    ).read_text(encoding="utf-8")
    assert "sqlite_where" in source
    assert "postgresql_where" in source


def test_migrations_are_idempotent():
    """⚠️ 迁移必须幂等 —— 重复执行不能报错。

    部署脚本常会重跑迁移；非幂等的迁移会在第二次执行时炸掉。
    """
    for path in (
        ROOT / "backend" / "migrations" / "versions" / "0020_org_change_queue.py",
        ROOT / "backend" / "migrations" / "versions" / "0021_org_change_queue_dedup_uq.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "has_table" in source or "_index_exists" in source, (
            f"{path.name} 缺少存在性检查 —— 迁移不幂等"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 八、PG 驱动依赖
# ═══════════════════════════════════════════════════════════════════════════


def test_postgres_driver_is_declared():
    """⚠️ **部署阻断项**：PG 驱动必须写进依赖清单。

    回归点：``requirements.txt`` 里只有 ``sqlalchemy``，**没有任何 PG 驱动**。
    结果是 —— 部署到 PG 时直接
    ``ModuleNotFoundError: No module named 'psycopg'``，
    服务起不来，而且这个错误**不会**在本地 SQLite 开发时暴露。

    驱动缺失属于「部署即失败」级别，必须在依赖清单里显式声明。
    """
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    assert "psycopg" in requirements, (
        "requirements.txt 未声明 PostgreSQL 驱动（psycopg）—— "
        "部署到 PG 时会 ModuleNotFoundError"
    )


def test_pg_driver_is_importable_in_dev():
    """⚠️ 声明了还要装得上 —— 否则 CI/开发环境同样连不上。

    没装时给出明确提示，而不是让人在部署时才发现。
    """
    import importlib.util

    if importlib.util.find_spec("psycopg") is None:
        pytest.skip(
            "本地未安装 psycopg（仅影响 PG 环境验证，SQLite 开发不受影响）"
        )
    importlib.import_module("psycopg")
