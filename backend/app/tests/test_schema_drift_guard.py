"""Schema 漂移检测守卫的回归测试。

背景
----
``backend/scripts/check_schema_drift.py`` 是自检轮 P3 加的 CI 守卫：
比对「ORM 建库」（``create_all``）与「迁移建库」（``alembic upgrade head``）
两条路径产出的 schema。

它曾经**抓不到**漂移 —— 因为比对方向写反了（拿 ORM 建的库跟 ORM 建的库比，
自己跟自己比，永远报"无漂移"）。注入漂移也测不出来。

所以这里既测「守卫能抓到注入的漂移」，也测「守卫在无漂移时放行」。
一个从不报错的守卫等于没有守卫 —— 这是它必须被测试的原因。
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "check_schema_drift.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_schema_drift", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load_guard()


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory) -> Path:
    """用 Alembic 从零建一个库（迁移路径的真值）。"""
    import subprocess

    db_path = tmp_path_factory.mktemp("migrated") / "migrated.db"
    env = {
        **__import__("os").environ,
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "ALEMBIC_EXPECT_TARGET": f"sqlite:{db_path.resolve().as_posix()}",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT / "backend"),
        env=env,
        capture_output=True,
        text=True,
        # ⚠️ 必须显式指定 encoding：Windows 默认用 GBK 解码子进程输出，
        #    而 Alembic 的输出含非 ASCII（表名/中文注释）→ UnicodeDecodeError，
        #    测试在 Windows 上必挂。CI 在 Linux 上是 UTF-8，所以只在本地暴露。
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return db_path


def test_guard_passes_on_migrated_db(guard, migrated_db):
    """迁移链建出来的库应当**无漂移**。

    这是 CI 里的主场景：迁移建库 + 守卫放行。
    """
    rc = guard.main(str(migrated_db))
    assert rc == 0, "迁移链与 ORM 模型应当完全一致"


def test_guard_detects_missing_unique_constraint(guard, migrated_db, tmp_path):
    """注入"迁移库缺唯一约束" → 守卫必须**报错退出**。

    回归场景：``0001_baseline.py`` 声明了 uq_score_record，
    但 ORM 的 Score 模型漏了 → create_all 建出来的库没有这个约束。
    守卫必须能抓到它（否则守卫形同虚设）。
    """
    drifted = tmp_path / "drifted.db"
    shutil.copy(migrated_db, drifted)

    conn = sqlite3.connect(drifted)
    conn.executescript(
        """
        PRAGMA foreign_keys=off;
        CREATE TABLE score_new (
            id VARCHAR(64) NOT NULL PRIMARY KEY,
            record_id VARCHAR(64) NOT NULL,
            total FLOAT NOT NULL,
            per_metric_json JSON NOT NULL,
            trace_json JSON NOT NULL,
            computed_at DATETIME NOT NULL
        );
        INSERT INTO score_new
            SELECT id, record_id, total, per_metric_json, trace_json, computed_at
              FROM score;
        DROP TABLE score;
        ALTER TABLE score_new RENAME TO score;
        CREATE INDEX ix_score_record_id ON score (record_id);
        """
    )
    conn.commit()
    conn.close()

    rc = guard.main(str(drifted))
    assert rc != 0, "缺唯一约束的漂移必须被抓到"


def test_guard_detects_missing_table(guard, migrated_db, tmp_path):
    """注入"迁移库缺整张表" → 守卫必须报错。

    回归场景：``attendance_record`` 整张表在迁移链里从来没建过
    （0001~0012 全都没有），用 Alembic 部署新环境会直接 no such table。
    """
    drifted = tmp_path / "missing_table.db"
    shutil.copy(migrated_db, drifted)

    conn = sqlite3.connect(drifted)
    conn.execute("DROP TABLE attendance_record")
    conn.commit()
    conn.close()

    rc = guard.main(str(drifted))
    assert rc != 0, "缺表的漂移必须被抓到"


def test_guard_detects_missing_column(guard, migrated_db, tmp_path):
    """注入"迁移库缺列" → 守卫必须报错。

    回归场景：``self_review.attachments_json``（业务代码实际在写的列）
    在迁移链里叫 ``attachments`` —— 名字对不上，ORM 有、迁移库没有。
    """
    drifted = tmp_path / "missing_column.db"
    shutil.copy(migrated_db, drifted)

    conn = sqlite3.connect(drifted)
    conn.executescript(
        """
        PRAGMA foreign_keys=off;
        CREATE TABLE rating_draft_new (
            id VARCHAR(64) NOT NULL PRIMARY KEY,
            record_id VARCHAR(64) NOT NULL,
            ratings_json JSON NOT NULL,
            citations JSON NOT NULL,
            gaps_json JSON NOT NULL,
            fairness_json JSON NOT NULL,
            status VARCHAR(32) NOT NULL,
            confirmed_by VARCHAR(64),
            created_at DATETIME NOT NULL
        );
        INSERT INTO rating_draft_new
            SELECT id, record_id, ratings_json, citations, gaps_json,
                   fairness_json, status, confirmed_by, created_at
              FROM rating_draft;
        DROP TABLE rating_draft;
        ALTER TABLE rating_draft_new RENAME TO rating_draft;
        """
    )
    conn.commit()
    conn.close()

    rc = guard.main(str(drifted))
    assert rc != 0, "缺列的漂移必须被抓到"


def test_guard_detects_extra_column(guard, migrated_db, tmp_path):
    """注入"迁移库多一列" → 守卫必须报错。"""
    drifted = tmp_path / "extra_column.db"
    shutil.copy(migrated_db, drifted)

    conn = sqlite3.connect(drifted)
    conn.execute("ALTER TABLE rating_draft ADD COLUMN stray_column VARCHAR(16)")
    conn.commit()
    conn.close()

    rc = guard.main(str(drifted))
    assert rc != 0, "多列的漂移必须被抓到"
