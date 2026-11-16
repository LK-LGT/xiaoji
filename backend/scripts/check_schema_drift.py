"""Schema 漂移检测：ORM 模型 vs Alembic 迁移。

背景
----
本仓库有**两条建库路径**，它们可能产出**不同的 schema**：

1. ``alembic upgrade head`` —— 按 ``migrations/versions/*.py`` 建库
2. ``Base.metadata.create_all()`` —— 按 ``app/domain/models.py`` 建库

如果 ORM 模型与迁移链不同步，两条路径就会分叉。
这**真的发生过，而且不止一次**：

    1. ``Score.record_id`` 缺唯一约束
       → 并发算分写出重复行，之后所有 ``scalar_one_or_none()`` 读路径全 500。

    2. ``attendance_record`` 整张表在迁移链里**从来没建过**
       → 用 Alembic 部署新环境，考勤同步直接 ``no such table``。

    3. 6 张表缺唯一约束（assessment_record / self_review / manager_review /
       rating_draft / comment_draft / rollup）
       → ORM 有、迁移没有。

    4. 5 张表 ORM 与迁移**方向相反**
       （rubric / weight_set / config_snapshot / attendance_record / self_review）
       → 迁移没有、ORM 有。

为什么 CI 之前没抓到
------------------
本地 ``backend/data/perf.db`` 是用 ``create_all`` 建的，所以它**总是**
符合 ORM；CI 用纯 Alembic 建库，所以它**总是**符合迁移。
**两条路径各自都"看起来正常"，分叉天然不可见** ——
除非把两条路径**摆在一起比**。

本脚本做什么
------------
用 ORM 建一个临时库（``create_all``），与给定的对照库
**逐表比对：表是否存在、列是否一致、唯一约束是否一致**。
有分叉就报错退出。

用法
----
    python backend/scripts/check_schema_drift.py <对照库路径>
"""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# 让脚本能从仓库根目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _orm_unique_constraints() -> dict[str, set[tuple[str, ...]]]:
    """从 ORM 模型取每张表的唯一约束（列组合）。

    ⚠️ 两种声明方式都要认：
       1. 表级 ``__table_args__ = (UniqueConstraint(...),)``
       2. 列级 ``mapped_column(..., unique=True)``
    只读第 1 种会漏掉列级声明的表（``metric.code`` 就是列级），
    造成**误报**漂移。
    """
    from sqlalchemy import UniqueConstraint

    from backend.app.domain.models import Base

    result: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for table_name, table in Base.metadata.tables.items():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                cols = tuple(sorted(col.name for col in constraint.columns))
                result[table_name].add(cols)
        # 列级 unique=True —— 在 SQLite 里同样表现为唯一索引
        for col in table.columns:
            if col.unique:
                result[table_name].add((col.name,))
    return dict(result)


def _db_unique_constraints(db_path: str) -> dict[str, set[tuple[str, ...]]]:
    """从 SQLite 库取每张表的唯一约束（列组合）。

    SQLite 把 UNIQUE 约束暴露成 ``sqlite_autoindex_*`` 唯一索引，
    约束名读不到（只能从 DDL 解析），所以这里按**列组合**比对 ——
    列组合一致即视为一致。
    """
    conn = sqlite3.connect(db_path)
    try:
        tables = _db_tables(conn)
        result: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        for table in tables:
            for row in conn.execute(f"PRAGMA index_list('{table}')").fetchall():
                # row = (seq, name, unique, origin, partial)
                if not row[2]:
                    continue
                if row[3] == "pk":
                    continue  # 主键由 id 列单独比对，不算"约束漂移"
                cols = tuple(
                    sorted(
                        info[2]
                        for info in conn.execute(
                            f"PRAGMA index_info('{row[1]}')"
                        ).fetchall()
                        if info[2]
                    )
                )
                if cols:
                    result[table].add(cols)
        return dict(result)
    finally:
        conn.close()


def _db_tables(conn: sqlite3.Connection) -> set[str]:
    """库里的业务表（排除 alembic 自己的 bookkeeping 表）。"""
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        )
    }


def _db_columns(db_path: str) -> dict[str, set[str]]:
    """从 SQLite 库取每张表的列名集合。"""
    conn = sqlite3.connect(db_path)
    try:
        result: dict[str, set[str]] = {}
        for table in _db_tables(conn):
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            result[table] = {c[1] for c in cols}
        return result
    finally:
        conn.close()


def _orm_columns() -> dict[str, set[str]]:
    from backend.app.domain.models import Base

    return {
        table_name: {col.name for col in table.columns}
        for table_name, table in Base.metadata.tables.items()
    }


def _build_orm_db(tmp_path: Path) -> None:
    """用 ORM 建一个临时库（模拟 ``create_all`` 那条路径）。"""
    from sqlalchemy import create_engine

    from backend.app.domain.models import Base

    engine = create_engine(f"sqlite:///{tmp_path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()


def main(reference_db_path: str) -> int:
    import tempfile

    print(f"对照库: {reference_db_path}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "_orm_schema.db"
        _build_orm_db(tmp_db)
        orm_db_constraints = _db_unique_constraints(str(tmp_db))
        orm_db_columns = _db_columns(str(tmp_db))
        print(f"ORM 建库（create_all）: {tmp_db}")

    # ⚠️ 比对方向：拿**对照库**跟 ORM 建出来的库比。
    #    早先写成「ORM 建库 vs ORM 建库」= 自己跟自己比，永远报"无漂移"，
    #    守卫形同虚设 —— 注入漂移也抓不到。
    db_constraints = _db_unique_constraints(reference_db_path)
    db_columns = _db_columns(reference_db_path)

    drift: list[str] = []

    # ── 1. 表是否存在 ──
    orm_tables = set(orm_db_columns)
    ref_tables = set(db_columns)
    for table in sorted(orm_tables - ref_tables):
        drift.append(f"  表 {table}: ORM 有但对照库**没有这张表**")
    for table in sorted(ref_tables - orm_tables):
        drift.append(f"  表 {table}: 对照库有但 ORM 没有这张表")

    # ── 2. 列是否一致 ──
    for table in sorted(orm_tables & ref_tables):
        missing = orm_db_columns[table] - db_columns[table]
        extra = db_columns[table] - orm_db_columns[table]
        if missing:
            drift.append(f"  表 {table}: 对照库缺列 → {sorted(missing)}")
        if extra:
            drift.append(f"  表 {table}: 对照库多列 → {sorted(extra)}")

    # ── 3. 唯一约束是否一致 ──
    for table in sorted(orm_tables & ref_tables):
        orm_set = orm_db_constraints.get(table, set())
        db_set = db_constraints.get(table, set())
        only_orm = orm_set - db_set
        only_db = db_set - orm_set
        if only_orm:
            drift.append(
                f"  表 {table}: ORM 声明了但对照库没有 → {sorted(only_orm)}"
            )
        if only_db:
            drift.append(
                f"  表 {table}: 对照库有但 ORM 没声明 → {sorted(only_db)}"
            )

    print()
    print(f"比对表数: {len(orm_tables | ref_tables)}")
    print(f"ORM 建库侧有唯一约束的表: {len(orm_db_constraints)}")
    print(f"对照库侧有唯一约束的表: {len(db_constraints)}")
    print()

    if drift:
        print("❌ 发现 schema 漂移:")
        for line in drift:
            print(line)
        print()
        print("修复方向:")
        print("  · 对照库多出来的 → ORM 模型漏声明 → 在 models.py 补 UniqueConstraint")
        print("  · ORM 多出来的 → 迁移缺约束 → 补一条 Alembic 迁移")
        print("  · 表/列对不上 → 迁移链缺建表或缺列 → 补一条 Alembic 迁移")
        print()
        print("⚠️ 这类漂移会让两条建库路径产出不同 schema，")
        print("   真实库与 CI 库行为不一致（约束缺失 → 并发重复写 → 读路径 500；")
        print("   缺表 → 部署到新环境直接 no such table）。")
        return 1

    print("✅ 无漂移：ORM 模型与对照库的表/列/唯一约束完全一致")
    return 0


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[1] / "data" / "perf.db"
    target = sys.argv[1] if len(sys.argv) > 1 else str(default)
    raise SystemExit(main(target))
