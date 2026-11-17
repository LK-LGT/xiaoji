"""BUG-R6-47：历史脏 / 重复同步批次清理脚本的自动化测试。

## 为什么需要测试

清理脚本是**写生产数据**的 —— 一旦逻辑写错，要么删掉不该删的批次，
要么留下该清的历史脏数据。而且这类脚本**没人会天天跑**，
回归全靠测试兜底。

## 锁定的契约

1. **默认是演练模式** —— 不加 ``--apply`` 绝不写库；
2. **超时失败 + 其后有成功批次** → 标 ``superseded``；
3. **卡住的 ``running``（超过阈值）** → 标 ``failed``；
4. **真正的失败历史一律保留** —— 不许「清理」掉证据；
5. **绝不物理删除** —— 批次是审计链的一环，删了会留悬空外键；
6. **幂等** —— 跑第二遍不再有待清理项。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "backend" / "scripts" / "clean_dirty_sync_batches.py"


def _load_script():
    """按文件路径加载脚本模块（脚本名不是合法包名，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("clean_dirty_sync_batches", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path, batches: list[dict]) -> Path:
    """造一个只含 ``organization_sync_batch`` 表的 SQLite 库。"""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE organization_sync_batch (
            id TEXT PRIMARY KEY,
            batch_no TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'full',
            trigger_type TEXT NOT NULL DEFAULT 'manual',
            triggered_by TEXT,
            idempotency_key TEXT,
            departments_synced INTEGER NOT NULL DEFAULT 0,
            departments_skipped INTEGER NOT NULL DEFAULT 0,
            departments_failed INTEGER NOT NULL DEFAULT 0,
            employees_synced INTEGER NOT NULL DEFAULT 0,
            employees_skipped INTEGER NOT NULL DEFAULT 0,
            employees_failed INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_ms INTEGER,
            reconciliation_json TEXT,
            phase TEXT,
            phase_detail TEXT,
            error_category TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO organization_sync_batch
            (id, batch_no, status, mode, trigger_type, triggered_by,
             started_at, finished_at, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                b["id"],
                b["batch_no"],
                b["status"],
                b.get("mode", "full"),
                b.get("trigger_type", "manual"),
                b.get("triggered_by", "ADMIN1"),
                b["started_at"],
                b.get("finished_at"),
                b.get("error_message"),
            )
            for b in batches
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def _run(script, db_path: Path, *args: str) -> int:
    """以脚本的 ``main()`` 跑一次（传参）。"""
    argv = sys.argv
    sys.argv = ["clean_dirty_sync_batches.py", "--db", str(db_path), *args]
    try:
        return script.main()
    finally:
        sys.argv = argv


def _statuses(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT batch_no, status FROM organization_sync_batch"
            ).fetchall()
        }
    finally:
        conn.close()


STALE_MSG = "同步超时未完成（实例异常退出或重启），请手工重试"


# ---------------------------------------------------------------------------
# 1. 演练模式不写库
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(script, tmp_path, capsys):
    """⚠️ 默认（不加 --apply）**绝不写库**。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-1",
                "status": "failed",
                "started_at": "2026-11-16 07:46:17.607844",
                "finished_at": "2026-11-16 07:58:35.760874",
                "error_message": STALE_MSG,
            },
            {
                "id": "b2",
                "batch_no": "ORG-2",
                "status": "ok",
                "started_at": "2026-11-16 07:46:17.738948",
                "finished_at": "2026-11-16 07:46:29.924020",
            },
        ],
    )

    assert _run(script, db_path) == 0
    # 状态未变
    assert _statuses(db_path)["ORG-1"] == "failed"
    out = capsys.readouterr().out
    assert "演练模式" in out


# ---------------------------------------------------------------------------
# 2. 超时失败 + 后继成功 → superseded
# ---------------------------------------------------------------------------


def test_stale_failure_with_successor_becomes_superseded(script, tmp_path):
    """超时失败且其后有成功批次 → 标 ``superseded``。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-FAILED",
                "status": "failed",
                "started_at": "2026-11-16 07:46:17.607844",
                "finished_at": "2026-11-16 07:58:35.760874",
                "error_message": STALE_MSG,
            },
            {
                "id": "b2",
                "batch_no": "ORG-OK",
                "status": "ok",
                "started_at": "2026-11-16 07:46:17.738948",
                "finished_at": "2026-11-16 07:46:29.924020",
            },
        ],
    )

    assert _run(script, db_path, "--apply") == 0
    statuses = _statuses(db_path)
    assert statuses["ORG-FAILED"] == "superseded"
    assert statuses["ORG-OK"] == "ok"


def test_real_failure_without_successor_is_preserved(script, tmp_path):
    """⚠️ **真正的失败历史必须保留** —— 那是证据，不是垃圾。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-REAL-FAIL",
                "status": "failed",
                "started_at": "2026-11-16 09:52:46.298996",
                "finished_at": "2026-11-16 09:53:27.811162",
                "error_message": "database is locked",
            },
        ],
    )

    assert _run(script, db_path, "--apply") == 0
    assert _statuses(db_path)["ORG-REAL-FAIL"] == "failed"


def test_partial_failed_is_preserved(script, tmp_path):
    """``partial_failed`` 表示「部分同步成功」，不是脏数据 —— 必须保留。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-PARTIAL",
                "status": "partial_failed",
                "started_at": "2026-11-14 06:56:05.683146",
                "finished_at": "2026-11-14 06:56:15.205957",
            },
        ],
    )

    assert _run(script, db_path, "--apply") == 0
    assert _statuses(db_path)["ORG-PARTIAL"] == "partial_failed"


def test_stale_failure_without_successor_is_preserved(script, tmp_path):
    """超时失败但**没有**后继成功 → 保留（可能真的是最后一次尝试）。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-LAST-FAIL",
                "status": "failed",
                "started_at": "2026-11-16 09:52:46.298996",
                "finished_at": "2026-11-16 09:53:27.811162",
                "error_message": STALE_MSG,
            },
        ],
    )

    assert _run(script, db_path, "--apply") == 0
    assert _statuses(db_path)["ORG-LAST-FAIL"] == "failed"


# ---------------------------------------------------------------------------
# 3. 卡住的 running → failed
# ---------------------------------------------------------------------------


def test_stale_running_becomes_failed(script, tmp_path):
    """卡在 running 且超过阈值 → 标 ``failed``。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-HUNG",
                "status": "running",
                "started_at": "2020-01-01 00:00:00.000000",
                "finished_at": None,
            },
        ],
    )

    assert _run(script, db_path, "--apply") == 0
    assert _statuses(db_path)["ORG-HUNG"] == "failed"


def test_fresh_running_is_preserved(script, tmp_path):
    """⚠️ 正在跑的批次（刚启动）**不许**被误杀。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-ACTIVE",
                "status": "running",
                "started_at": "2999-01-01 00:00:00.000000",
                "finished_at": None,
            },
        ],
    )

    assert _run(script, db_path, "--apply") == 0
    assert _statuses(db_path)["ORG-ACTIVE"] == "running"


# ---------------------------------------------------------------------------
# 4. 绝不物理删除
# ---------------------------------------------------------------------------


def test_script_never_deletes_rows(script, tmp_path):
    """⚠️ 批次是审计链的一环 —— 脚本**只改状态，不删行**。

    删掉会留下悬空外键（``organization_sync_audit.batch_id`` /
    ``organization_sync_item_error.batch_id`` /
    ``organization_sync_cursor.last_batch_id``）。
    """
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-A",
                "status": "failed",
                "started_at": "2026-11-16 07:46:17.607844",
                "finished_at": "2026-11-16 07:58:35.760874",
                "error_message": STALE_MSG,
            },
            {
                "id": "b2",
                "batch_no": "ORG-B",
                "status": "ok",
                "started_at": "2026-11-16 07:46:17.738948",
                "finished_at": "2026-11-16 07:46:29.924020",
            },
            {
                "id": "b3",
                "batch_no": "ORG-C",
                "status": "running",
                "started_at": "2020-01-01 00:00:00.000000",
                "finished_at": None,
            },
        ],
    )

    before = len(_statuses(db_path))
    assert _run(script, db_path, "--apply") == 0
    after = len(_statuses(db_path))
    assert before == after == 3, "脚本不得删除批次行"


# ---------------------------------------------------------------------------
# 5. 幂等
# ---------------------------------------------------------------------------


def test_script_is_idempotent(script, tmp_path):
    """跑第二遍不再有待清理项（可重复执行）。"""
    db_path = _make_db(
        tmp_path,
        [
            {
                "id": "b1",
                "batch_no": "ORG-FAILED",
                "status": "failed",
                "started_at": "2026-11-16 07:46:17.607844",
                "finished_at": "2026-11-16 07:58:35.760874",
                "error_message": STALE_MSG,
            },
            {
                "id": "b2",
                "batch_no": "ORG-OK",
                "status": "ok",
                "started_at": "2026-11-16 07:46:17.738948",
                "finished_at": "2026-11-16 07:46:29.924020",
            },
            {
                "id": "b3",
                "batch_no": "ORG-HUNG",
                "status": "running",
                "started_at": "2020-01-01 00:00:00.000000",
                "finished_at": None,
            },
        ],
    )

    assert _run(script, db_path, "--apply") == 0
    first = _statuses(db_path)
    assert first["ORG-FAILED"] == "superseded"
    assert first["ORG-HUNG"] == "failed"

    # 第二遍：状态已归位，不该再有任何改动
    assert _run(script, db_path, "--apply") == 0
    assert _statuses(db_path) == first


# ---------------------------------------------------------------------------
# 6. 结构性护栏
# ---------------------------------------------------------------------------


def test_script_supports_apply_flag():
    """⚠️ 脚本必须有 ``--apply`` 开关（演练 / 实执行分离）。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert '"--apply"' in source or "'--apply'" in source


def test_script_does_not_use_relative_db_path():
    """⚠️ 数据库路径必须用绝对路径（基于 ``__file__`` 推导）。

    相对路径在不同 CWD 下会打到不同的库 —— 运维踩过的坑。
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "ROOT /" in source or "parents[2]" in source


def test_script_has_dry_run_output():
    """演练模式必须明确告知「未写库」，避免误以为已经执行。"""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "演练模式" in source
