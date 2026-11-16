"""校验 A8 补强迁移：plan_feishu_binding 表。

用法：python scripts/verify_a8_binding_schema.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "perf.db"

EXPECTED_COLUMNS = {
    "id",
    "plan_type",
    "plan_id",
    "instance_code",
    "approval_code",
    "correlation_key",
    "bind_source",
    "instance_status",
    "created_by",
    "created_at",
    "updated_at",
}

EXPECTED_INDEXES = {
    "ix_plan_feishu_binding_plan",
    "ix_plan_feishu_binding_instance",
    "ix_plan_feishu_binding_corr",
    "ix_plan_feishu_binding_plan_id",
}

EXPECTED_UNIQUE = {
    "uq_plan_feishu_binding_plan",
    "uq_plan_feishu_binding_instance",
    "uq_plan_feishu_binding_corr",
}


def main() -> int:
    if not DB_PATH.exists():
        print(f"❌ 数据库不存在：{DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    failures: list[str] = []

    # ── 表存在 ────────────────────────────────────────────────
    tables = {
        r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "plan_feishu_binding" not in tables:
        failures.append("表 plan_feishu_binding 不存在")
    print(f"总表数: {len(tables)}")

    # ── 列 ────────────────────────────────────────────────────
    if "plan_feishu_binding" in tables:
        cols = {r[1] for r in cur.execute("PRAGMA table_info(plan_feishu_binding)")}
        missing = EXPECTED_COLUMNS - cols
        if missing:
            failures.append(f"缺少列: {sorted(missing)}")
        print(f"列数: {len(cols)}（期望 {len(EXPECTED_COLUMNS)}）")

        # ── 索引 ────────────────────────────────────────────────
        idx = {
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='plan_feishu_binding'"
            )
        }
        missing_idx = EXPECTED_INDEXES - idx
        if missing_idx:
            failures.append(f"缺少索引: {sorted(missing_idx)}")
        print(f"索引数: {len(idx)}（期望 {len(EXPECTED_INDEXES)}）")

        # ── 唯一约束 ────────────────────────────────────────────
        table_sql = " ".join(
            r[0]
            for r in cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='plan_feishu_binding'"
            )
        )
        found_uniq = {u for u in EXPECTED_UNIQUE if u in (table_sql or "")}
        missing_uq = EXPECTED_UNIQUE - found_uniq
        if missing_uq:
            failures.append(f"缺少唯一约束: {sorted(missing_uq)}")
        print(f"唯一约束: {len(found_uniq)}/{len(EXPECTED_UNIQUE)}")

    # ── 外键完整性 ────────────────────────────────────────────
    violations = list(cur.execute("PRAGMA foreign_key_check"))
    if violations:
        failures.append(f"外键违规 {len(violations)} 条")
    print(f"外键违规: {len(violations)}")

    # ── alembic 版本 ──────────────────────────────────────────
    try:
        version = cur.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        print(f"alembic 版本: {version}")
        if version != "0007_phase8_feishu_binding":
            failures.append(f"alembic 版本不是 head：{version}")
    except sqlite3.Error:
        failures.append("无法读取 alembic_version")

    conn.close()

    print()
    if failures:
        print("❌ 校验失败：")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("✅ A8 绑定表 schema 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
