"""PostgreSQL 真机验证 —— **就绪度自检**（不需要 PG 实例）。

用途
----
在 PG 实例就位**之前**，先把「除数据库以外的所有前置条件」全部验一遍，
避免「PG 起来了 → 跑脚本 → 才发现缺驱动/缺迁移/缺表」这种来回折腾。

检查项
------
1. **驱动可用** —— ``psycopg`` 能不能 import（版本 3.x，SQLAlchemy 2.x 兼容）；
2. **依赖声明** —— ``requirements.txt`` 里有没有钉住 PG 驱动（部署阻断项）；
3. **迁移链完整** —— Alembic 版本目录构成**单一线性链**、head 唯一；
4. **迁移脚本 PG 兼容** —— 没有 SQLite 专有语法；
5. **ORM 与迁移一致** —— 关键表在 ORM 里都声明了（否则 PG 上会少建表）；
6. **PG 硬限制** —— 标识符 ≤ 63 字节、VARCHAR 长度够用；
7. **验证脚本自检** —— ``verify_postgres_sync_e2e.py`` 存在且可解析；
8. **环境变量** —— ``DATABASE_URL`` 当前是什么状态。

运行
----
::

    python backend/scripts/verify_postgres_readiness.py

退出码 ``0`` = 一切就绪，只差一个 PG 实例；
``1`` = 有阻断项，按提示修。

⚠️ **本脚本不会连接任何数据库**，也不会修改任何数据。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
MIGRATIONS = BACKEND / "migrations"
VERSIONS = MIGRATIONS / "versions"

PASS = "\033[92m✔\033[0m"
FAIL = "\033[91m✖\033[0m"
WARN = "\033[33m⚠\033[0m"
INFO = "\033[36mℹ\033[0m"

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
    return ok


def warn(name: str, detail: str = "") -> None:
    print(f"  {WARN} {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}")
    print("─" * 60)


# ═══════════════════════════════════════════════════════════════════════
# 1. 驱动
# ═══════════════════════════════════════════════════════════════════════
def check_driver() -> None:
    section("1. PostgreSQL 驱动")
    try:
        import psycopg  # noqa: F401

        version = getattr(psycopg, "__version__", "?")
        check("psycopg 可导入", True, f"版本 {version}")
    except Exception as exc:
        check("psycopg 可导入", False, str(exc)[:150])
        print(f"      → 修复：pip install \"psycopg[binary]\"")

    # SQLAlchemy 必须认识 postgresql+psycopg 这个方言
    try:
        from sqlalchemy import create_engine

        eng = create_engine(
            "postgresql+psycopg://u:p@localhost:5432/db", future=True
        )
        check(
            "SQLAlchemy 认识 postgresql+psycopg 方言",
            eng.dialect.name == "postgresql",
            eng.dialect.name,
        )
        eng.dispose()
    except Exception as exc:
        check("SQLAlchemy 认识 postgresql+psycopg 方言", False, str(exc)[:150])


# ═══════════════════════════════════════════════════════════════════════
# 2. 依赖声明
# ═══════════════════════════════════════════════════════════════════════
def check_requirements() -> None:
    section("2. 依赖声明（部署阻断项）")
    req = BACKEND / "requirements.txt"
    if not req.exists():
        check("requirements.txt 存在", False, str(req))
        return
    text = req.read_text(encoding="utf-8")
    has_pg = re.search(r"^psycopg", text, re.M | re.I) is not None
    check("requirements.txt 声明了 psycopg", has_pg)
    if not has_pg:
        print('      → 修复：echo "psycopg[binary]>=3.1" >> backend/requirements.txt')


# ═══════════════════════════════════════════════════════════════════════
# 3. 迁移链
# ═══════════════════════════════════════════════════════════════════════
def _parse_revisions() -> dict[str, dict[str, str | None]]:
    """解析版本脚本，拿到 revision → {down_revision, filename}。"""
    result: dict[str, dict[str, str | None]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name.startswith("__"):
            continue
        source = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision:\s*str\s*=\s*["\']([^"\']+)["\']', source, re.M)
        down = re.search(
            r'^down_revision:\s*[^=]+=\s*["\']([^"\']+)["\']', source, re.M
        )
        if rev:
            result[rev.group(1)] = {
                "down": down.group(1) if down else None,
                "file": path.name,
            }
    return result


def check_migration_chain() -> None:
    section("3. Alembic 迁移链")
    revs = _parse_revisions()
    check("版本脚本可解析", bool(revs), f"{len(revs)} 个")

    # 单一线性链：每个 revision 只能被一个 revision 当作 down_revision
    parents: dict[str, list[str]] = {}
    for rev, info in revs.items():
        down = info["down"]
        if down:
            parents.setdefault(str(down), []).append(rev)
    branches = {k: v for k, v in parents.items() if len(v) > 1}
    check(
        "迁移链无分叉（单一线性链）",
        not branches,
        f"分叉: {branches}" if branches else "",
    )

    # 孤儿：down_revision 指向不存在的 revision
    orphans = [
        (rev, str(info["down"]))
        for rev, info in revs.items()
        if info["down"] and str(info["down"]) not in revs
    ]
    check(
        "无孤儿迁移（down_revision 都存在）",
        not orphans,
        f"孤儿: {orphans}" if orphans else "",
    )

    # head 唯一
    down_set = {str(i["down"]) for i in revs.values() if i["down"]}
    heads = [r for r in revs if r not in down_set]
    check("head 唯一", len(heads) == 1, f"head={heads}")

    # 全量往返：head → 根 → head 的链路完整性
    if heads:
        cur = heads[0]
        seen: list[str] = []
        while cur and cur in revs and cur not in seen:
            seen.append(cur)
            cur = str(revs[cur]["down"]) if revs[cur]["down"] else ""
        check(
            "能从 head 回溯到根（链路连续）",
            len(seen) == len(revs),
            f"回溯 {len(seen)}/{len(revs)}",
        )


# ═══════════════════════════════════════════════════════════════════════
# 4. 迁移脚本的 PG 兼容
# ═══════════════════════════════════════════════════════════════════════
SQLITE_ONLY = [
    ("PRAGMA ", "PRAGMA"),
    ("WITHOUT ROWID", "WITHOUT ROWID"),
    ("INSERT OR REPLACE", "INSERT OR REPLACE"),
    ("INSERT OR IGNORE", "INSERT OR IGNORE"),
]


def _strip_py_comments_and_docstrings(source: str) -> str:
    """去掉注释与**文档字符串**，但**保留普通字符串字面量**。

    ⚠️ 为什么不能一刀切去掉所有字符串：
    迁移里的 SQLite 专有 SQL 往往就写在字符串里 ——

        op.execute(sa.text("PRAGMA foreign_keys=ON"))

    把字符串也剥掉，这条真问题就**看不见**了。

    所以这里只去掉 **docstring**（模块/类/函数的说明文字）：
    ``0009_score_record_unique.py`` 的模块 docstring 里就写着
    ``pragma index_list('score')``（描述问题用的），不剥会误报。

    实现用 ``ast`` 而不是 tokenize —— ast 能**准确**区分
    「docstring」和「普通字符串字面量」，tokenize 做不到
    （它只看到 STRING token，得靠上下文猜）。

    同理，``autoincrement=True`` 是 **SQLAlchemy 通用参数**，
    两个方言都支持，不是 SQLite 专有 —— 不该出现在黑名单里。
    """
    import ast
    import io
    import tokenize

    # 1) 找出所有 docstring 的行号（模块/类/函数的第一条语句）
    docstring_lines: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                value = first.value.value
                if isinstance(value, str):
                    docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))

    # 2) tokenize 逐个处理：跳过注释、跳过 docstring 行的字符串、保留其余
    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and tok.start[0] in docstring_lines:
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return source
    return " ".join(out)


def check_migration_sqlite_only() -> None:
    section("4. 迁移脚本的 PostgreSQL 兼容")
    offenders: list[str] = []
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        # 只看**真正的代码**（去掉注释与字符串字面量）
        code = _strip_py_comments_and_docstrings(source).upper()
        for marker, label in SQLITE_ONLY:
            if marker in code:
                offenders.append(f"{path.name}: {label}")
    check(
        "迁移里没有 SQLite 专有语法",
        not offenders,
        "; ".join(offenders) if offenders else "",
    )

    # 部分唯一索引必须同时声明两个方言
    missing: list[str] = []
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "sqlite_where" in source and "postgresql_where" not in source:
            missing.append(path.name)
    check(
        "部分唯一索引同时声明 sqlite_where 与 postgresql_where",
        not missing,
        "; ".join(missing) if missing else "",
    )


# ═══════════════════════════════════════════════════════════════════════
# 5. ORM ↔ 迁移
# ═══════════════════════════════════════════════════════════════════════
REQUIRED_TABLES = [
    "organization_sync_batch",
    "organization_sync_cursor",
    "organization_sync_item_error",
    "organization_sync_audit",
    "organization_sync_alert",
    "feishu_identity",
    "feishu_identity_audit",
    "org_change_queue",
    "employee",
    "department",
]


def check_orm_tables() -> None:
    section("5. ORM 声明（PG 上按 ORM 建表会缺这些）")
    sys.path.insert(0, str(ROOT))
    try:
        from backend.app.domain.models import Base

        tables = set(Base.metadata.tables)
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        check("关键表都在 ORM 里", not missing, f"缺: {missing}" if missing else "")

        # PG 标识符 63 字节限制
        long_names: list[str] = []
        for table_name, table in Base.metadata.tables.items():
            if len(table_name.encode()) > 63:
                long_names.append(f"table:{table_name}")
            for idx in table.indexes:
                if len((idx.name or "").encode()) > 63:
                    long_names.append(f"index:{idx.name}")
            for con in table.constraints:
                if len((con.name or "").encode()) > 63:
                    long_names.append(f"constraint:{con.name}")
        check(
            "标识符均 ≤ 63 字节（PG 限制）",
            not long_names,
            "; ".join(long_names) if long_names else "",
        )

        # VARCHAR 长度：游标 resource_type 必须装得下「员工:<64位部门ID>」
        cursor_table = Base.metadata.tables.get("organization_sync_cursor")
        if cursor_table is not None:
            col = cursor_table.columns.get("resource_type")
            length = getattr(getattr(col, "type", None), "length", None)
            check(
                "organization_sync_cursor.resource_type 长度 ≥ 128",
                length is not None and length >= 128,
                f"实际 {length}",
            )
    except Exception as exc:
        check("ORM 可导入", False, str(exc)[:200])


# ═══════════════════════════════════════════════════════════════════════
# 6. 验证脚本自检
# ═══════════════════════════════════════════════════════════════════════
def check_verify_script() -> None:
    section("6. 真机验证脚本")
    script = BACKEND / "scripts" / "verify_postgres_sync_e2e.py"
    check("verify_postgres_sync_e2e.py 存在", script.exists())
    if not script.exists():
        return
    source = script.read_text(encoding="utf-8")
    try:
        compile(source, str(script), "exec")
        check("脚本语法正确", True)
    except SyntaxError as exc:
        check("脚本语法正确", False, str(exc)[:150])
        return

    for needed, label in [
        ("--dry-run", "支持 --dry-run"),
        ("DATABASE_URL", "读取 DATABASE_URL"),
        ("postgresql", "强制 PG 连接串"),
    ]:
        check(f"脚本 {label}", needed in source)

    # 不得默认连生产库
    check(
        "脚本拒绝非 PG 连接串",
        "must be PostgreSQL" in source or "必须是 PostgreSQL" in source,
    )


# ═══════════════════════════════════════════════════════════════════════
# 7. 环境变量现状
# ═══════════════════════════════════════════════════════════════════════
def check_env() -> None:
    section("7. 当前环境")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print(f"  {INFO} DATABASE_URL 未设置 —— 符合预期（PG 实例还没就位）")
    elif url.startswith("postgresql"):
        print(f"  {PASS} DATABASE_URL 已指向 PostgreSQL")
    else:
        print(f"  {WARN} DATABASE_URL 当前指向 {url.split('://')[0]}://（非 PG）")
        print("      → 真机验证前需 export DATABASE_URL=postgresql+psycopg://...")


# ═══════════════════════════════════════════════════════════════════════
def main() -> int:
    print("=" * 60)
    print("PostgreSQL 真机验证 —— 就绪度自检")
    print("=" * 60)

    check_driver()
    check_requirements()
    check_migration_chain()
    check_migration_sqlite_only()
    check_orm_tables()
    check_verify_script()
    check_env()

    section("结果")
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed
    print(f"  {passed} 项通过，{failed} 项失败")

    if failed:
        print("\n  失败明细：")
        for name, ok, detail in _results:
            if not ok:
                print(f"    ✖ {name} — {detail}")
        print("\n  ❌ 尚未就绪 —— 请先修掉上面的失败项。")
        return 1

    print()
    print("  ✅ 一切就绪 —— 只差一个 PostgreSQL 实例。")
    print()
    print("  PG 实例就位后执行：")
    print('    $env:DATABASE_URL = "postgresql+psycopg://USER:PASS@HOST:5432/perf_verify"')
    print("    python backend/scripts/verify_postgres_sync_e2e.py --dry-run   # 先只查 schema")
    print("    python backend/scripts/verify_postgres_sync_e2e.py            # 再跑全链路")
    print()
    print("  ⚠️ 请使用**隔离的验证库**，不要指向生产库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
