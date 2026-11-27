"""护栏：**迁移脚本与 ORM 模型必须建出完全一致的表结构**（2026-11-27）。

## 背景

2026-11-27 修 CI 时踩到一个坑：本地 CI 的「Alembic 降级再升级」间歇性报

    sqlite3.OperationalError: no such table: plan_adjustment_seq

追下去发现，**根因不是迁移脚本写错，而是「库的结构」和「版本号」对不上**：
``alembic_version`` 只记版本号、**不记表结构**，
所以「版本号 = 0048，但库里缺 0048 建的表」这种**结构漂移**完全查不出来。

而这类漂移**几乎必然**源于「ORM 改过、迁移没跟上」——
ORM 一改，``create_all()`` 立刻就变了；迁移脚本却要人记得手动补。
两者一旦分叉，**没有任何门禁会发现**。

## 本文件的作用

用**两条路径分别建库**，再逐表逐列比对：

1. **迁移路径**：空库 → ``alembic upgrade head``
2. **ORM 路径**：空库 → ``Base.metadata.create_all()``

两者建出的**表集合**与**列集合**必须完全相同。

⚠️ 为什么用「两条路径建库」而不是「读迁移源码」——
   迁移脚本里列的顺序、类型、默认值都可能和 ORM 不同，
   读源码只能验证「写法」，验证不了「**建出来的库**是不是一样的」。

⚠️ 为什么必须覆盖**每一张表**而不是只查新表 ——
   漂移可能发生在任意一张老表上（比如给 ``employee`` 加了 ``mobile`` 列，
   迁移 ``0046`` 忘了写 ⇒ 老库的 ``employee`` 表少一列 ⇒
   运行时 ``no such column: mobile``）。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
#: Alembic 自己的版本表 —— ORM 路径**不会**建它 ⇒ 比对时要排除。
ALEMBIC_INTERNAL = {"alembic_version"}


def _table_columns(db_path: Path) -> dict[str, list[str]]:
    """读出库中每张表的列名集合。"""
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        result: dict[str, list[str]] = {}
        for table in tables:
            cols = [
                r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
            result[table] = sorted(cols)
        return result
    finally:
        conn.close()


def _build_via_migration(db_path: Path) -> dict[str, list[str]]:
    """路径一：用 Alembic 从零建库。"""
    posix = db_path.resolve().as_posix()
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{posix}",
        "ALEMBIC_EXPECT_TARGET": f"sqlite:{posix}",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        f"alembic upgrade head 失败：\n{proc.stdout}\n{proc.stderr}"
    )
    return _table_columns(db_path)


def _build_via_orm(db_path: Path) -> dict[str, list[str]]:
    """路径二：用 ORM ``create_all()`` 从零建库。"""
    if db_path.exists():
        db_path.unlink()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from sqlalchemy import create_engine

    from backend.app.domain.models import Base

    engine = create_engine(f"sqlite:///{db_path.resolve().as_posix()}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return _table_columns(db_path)


def test_migration_and_orm_produce_identical_schema(tmp_path: Path) -> None:
    """迁移路径与 ORM 路径必须建出**完全一致**的表结构。"""
    mig_db = tmp_path / "_mig.db"
    orm_db = tmp_path / "_orm.db"

    mig = _build_via_migration(mig_db)
    mig = {t: c for t, c in mig.items() if t not in ALEMBIC_INTERNAL}
    orm = _build_via_orm(orm_db)

    only_mig = sorted(set(mig) - set(orm))
    only_orm = sorted(set(orm) - set(mig))
    assert not only_mig, (
        f"只有迁移建出了这些表（ORM 缺）⇒ 模型没跟上：{only_mig}"
    )
    assert not only_orm, (
        f"只有 ORM 建出了这些表（迁移缺）⇒ 迁移没跟上：{only_orm}"
    )

    drift: list[str] = []
    for table in sorted(mig):
        if mig[table] != orm[table]:
            only_in_orm = sorted(set(orm[table]) - set(mig[table]))
            only_in_mig = sorted(set(mig[table]) - set(orm[table]))
            drift.append(
                f"  {table}: ORM多={only_in_orm} 迁移多={only_in_mig}"
            )
    assert not drift, "迁移与 ORM 的列结构漂移了：\n" + "\n".join(drift)
