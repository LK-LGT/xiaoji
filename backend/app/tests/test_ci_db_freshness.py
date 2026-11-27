"""护栏：**本地 CI 必须每次从干净数据库跑**（2026-11-27）。

## 背景

「Alembic 迁移 —— 降级再升级（往返无损）」这一项**间歇性变红**，
而「升级到 head」「再次升级」「schema 校验」全部是绿的。
表面看像迁移脚本写错了，实际不是：

- ``backend/scripts/run_ci_locally.py`` **从不删除** ``data/_ci_local.db``；
- 该库**跨多次运行一直复用** ⇒ CI 结果取决于**上一轮跑之前的历史**；
- ``alembic_version`` 表**只记版本号、不记表结构**；
- 于是出现「版本号 = 0048，但库里缺 ``0048`` 建的某张表」的**结构漂移**：

  ``plan_adjustment_seq`` 由迁移 ``0048`` 创建，
  但它是在某轮「先用 ORM ``create_all()`` 建库、之后再 stamp 到 0048」的历史里被漏掉的。

- 随后的 ``downgrade -1`` 执行 ``DROP TABLE plan_adjustment_seq``
  ⇒ ``sqlite3.OperationalError: no such table: plan_adjustment_seq``。

## 为什么这类失败特别难查

1. **删库后立刻全绿** ⇒ 看起来像「迁移脚本有时好有时坏」，容易往玄学上猜；
2. **同一份代码、同一台机器**，结果取决于**之前跑过什么** ⇒ 不可复现；
3. **真正的验证目标是「迁移脚本的升降级可逆」**，
   而复用旧库根本**验证不了**这件事 —— 旧库的结构本来就是上一轮的产物。

## 本文件的作用

持续校验：

1. ``run_ci_locally.py`` **必须**在建检查项之前清理 CI 数据库；
2. 清理必须**连同 WAL/SHM 一起删**（只删主文件会残留事务日志）；
3. 清理的必须是**本脚本自己用的那个**库（不能误删开发库）；
4. 库文件路径必须落在仓库的 ``data/`` 目录下（防止误伤生产库路径）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CI_SCRIPT = ROOT / "backend" / "scripts" / "run_ci_locally.py"


def _script_text() -> str:
    return CI_SCRIPT.read_text(encoding="utf-8")


def test_ci_script_exists() -> None:
    assert CI_SCRIPT.exists(), f"缺少本地 CI 脚本：{CI_SCRIPT}"


def test_ci_runner_cleans_database_before_checks() -> None:
    """本地 CI 必须在跑检查项**之前**清理数据库。

    ⚠️ 顺序很重要：如果清理写在检查项**之后**，
    那么「本轮失败」仍会留下污染的库 ⇒ 下一轮继续红 ⇒ 永远修不好。
    """
    text = _script_text()
    assert "_purge_ci_db" in text, "run_ci_locally.py 没有清理 CI 数据库的逻辑"

    purge_at = text.index("_purge_ci_db(db_path)")
    # 第一个真正执行迁移的检查项
    upgrade_at = text.index('"alembic", "upgrade", "head"')
    assert purge_at < upgrade_at, "清理数据库必须发生在 alembic upgrade 之前"


def test_ci_runner_purges_wal_and_shm() -> None:
    """必须连同 ``-wal`` / ``-shm`` 一起删。

    ⚠️ 只删主库文件、留下 WAL ⇒ 旧事务日志仍可能被 SQLite 恢复回来
    ⇒ 库「看起来还在」⇒ 结构漂移照旧 ⇒ 护栏形同虚设。
    """
    text = _script_text()
    for suffix in ('""', '"-wal"', '"-shm"'):
        assert suffix in text, f"清理逻辑缺少后缀 {suffix}（WAL/SHM 必须一起删）"


def test_ci_runner_does_not_touch_dev_database() -> None:
    """清理的必须是 CI 专用库，不能误伤开发库。

    ⚠️ 开发库是 ``backend/data/perf.db`` —— 里面有**真实的组织架构数据**。
    CI 脚本若误删它，开发环境直接归零。
    """
    text = _script_text()
    purge_block = text[text.index("def _purge_ci_db") : text.index("def backend_checks")]
    assert "perf.db" not in purge_block, "清理逻辑不得硬编码开发库文件名"


def test_ci_database_lives_under_repo_data_dir() -> None:
    """CI 库路径必须落在仓库 ``data/`` 下。

    ⚠️ 迁移往返是**破坏性操作**（降级会 DROP TABLE）⇒
    绝不能对着生产库跑。
    """
    text = _script_text()
    match = re.search(r'REPO_ROOT\s*/\s*"data"\s*/\s*"([^"]+)"', text)
    assert match, "未找到 CI 数据库路径定义"
    assert match.group(1).startswith("_"), "CI 库文件名应以 _ 开头，避免误认为开发库"


def test_ci_runner_removes_stale_db_files() -> None:
    """端到端：造一个「脏」库 + WAL，跑清理逻辑 ⇒ 文件必须消失。

    ⚠️ 这是唯一能证明「护栏真的生效」的测试 ——
    只读源码只能证明「写了清理函数」，证明不了「清理函数真的删了」。
    """
    import tempfile

    from backend.scripts.run_ci_locally import _purge_ci_db

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "_ci_probe.db"
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).write_bytes(b"stale")

        _purge_ci_db(db)

        for suffix in ("", "-wal", "-shm"):
            assert not Path(str(db) + suffix).exists(), f"清理未删除 {suffix!r} 文件"
