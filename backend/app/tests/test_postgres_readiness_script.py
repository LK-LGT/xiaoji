"""PostgreSQL 就绪度自检脚本的回归测试。

⚠️ 为什么这个脚本本身也要有测试：
它是「PG 实例就位前」的唯一一道关卡 —— 如果它**误报**（把
SQLAlchemy 通用语法当成 SQLite 专有），运维就会去改本来正确的迁移；
如果它**漏报**（真有问题没查出来），PG 上线时才会炸。

所以这里锁住两个最容易出错的判定：
1. 只看**代码**，不看注释/文档字符串（``0009`` 的 docstring 里就写了 ``pragma``）；
2. 不把 ``autoincrement=True`` 当成 SQLite 专有（它是 SQLAlchemy 通用参数）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "verify_postgres_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_postgres_readiness", SCRIPT)
    assert spec and spec.loader, "无法加载就绪度脚本"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


# ── 注释/文档字符串剥离 ──────────────────────────────────────────────


def test_strip_removes_docstring_mentioning_pragma(mod):
    """⚠️ 0009 的模块 docstring 里写着 ``pragma index_list('score')``。

    不剥离就会误判「迁移里有 SQLite 专有语法」。
    """
    source = '''
"""背景说明
pragma index_list('score') 只有 PK 自动索引
"""
x = 1
'''
    code = mod._strip_py_comments_and_docstrings(source)
    assert "pragma" not in code.lower()
    assert "x = 1" in code


def test_strip_removes_line_and_block_comments(mod):
    source = """
# PRAGMA foreign_keys=ON
x = 1  # INSERT OR REPLACE
"""
    code = mod._strip_py_comments_and_docstrings(source)
    assert "PRAGMA" not in code
    assert "INSERT OR REPLACE" not in code
    assert "x = 1" in code


def test_strip_keeps_real_code_in_string_literal(mod):
    """⚠️ **字符串字面量必须保留** —— SQLite 专有 SQL 常写在字符串里：

        op.execute(sa.text("PRAGMA foreign_keys=ON"))

    一刀切剥掉所有字符串，这条真问题就看不见了。
    """
    source = 'op.execute(sa.text("PRAGMA foreign_keys=ON"))'
    code = mod._strip_py_comments_and_docstrings(source)
    assert "PRAGMA" in code


# ── SQLite 专有语法黑名单 ────────────────────────────────────────────


def test_autoincrement_is_not_treated_as_sqlite_only(mod):
    """⚠️ ``sa.Column(..., autoincrement=True)`` 是 **SQLAlchemy 通用参数**，
    SQLite 与 PostgreSQL 都支持 —— 不能当成 SQLite 专有语法。
    """
    labels = [label for _, label in mod.SQLITE_ONLY]
    assert "AUTOINCREMENT" not in labels
    assert not any("AUTOINCREMENT" in label.upper() for label in labels)


def test_blacklist_contains_real_sqlite_only_syntax(mod):
    markers = [m for m, _ in mod.SQLITE_ONLY]
    assert any("PRAGMA" in m for m in markers)
    assert any("WITHOUT ROWID" in m for m in markers)
    assert any("INSERT OR REPLACE" in m for m in markers)


# ── 端到端：脚本本身必须能跑通 ──────────────────────────────────────


def test_readiness_script_runs_and_passes(mod, capsys):
    """⚠️ 完整跑一遍 —— 必须 0 失败。

    这同时是一条「持续护栏」：将来有人加了带 SQLite 专有语法的迁移，
    这个测试会立刻变红。
    """
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "一切就绪" in out
