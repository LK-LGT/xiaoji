"""清理 BUG-R6-45 遗留的脏游标数据。

## 背景

BUG-R6-45 的根因：分部门粒度游标（``employee:<部门ID>``）的
``snapshot_complete`` 从未被写入，**恒为 False**。

这导致「远端缺失 = 离职/删除」这一判定的**前提从未成立** ——
离职链路至今没有被真实验证过。

## 为什么需要清理脚本

代码修复后，新同步会把游标正确标记为 True。
但**历史遗留的 30 条脏游标**仍然是 False ——
它们反映的是「代码 bug 期间写入的状态」，不是真实状态。

下一次全量同步会覆盖它们，但：
- 增量同步**不会**重置这些游标；
- 运维看板/对账会一直显示「快照不完整」，干扰判断。

本脚本把**当前真实状态**重新写回：
- 有 ``page_token`` 的 → 还在分页中 → False；
- 无 ``page_token`` 且对应部门存在 → 已拉完 → True。

⚠️ **保守原则**：无法确定状态的，一律保持 False（宁可保守，
不能误判离职）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 BUG-R6-45 遗留的脏游标")
    parser.add_argument(
        "--db",
        default=str(ROOT / "backend" / "data" / "perf.db"),
        help="SQLite 数据库路径",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库（不加此参数只做演练）",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ 数据库不存在：{db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        print(f"数据库：{db_path}")
        print("")

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "organization_sync_cursor" not in tables:
            print("❌ 缺少 organization_sync_cursor 表 —— 请先跑迁移")
            return 1

        dept_ids: set[str] = set()
        if "department" in tables:
            dept_ids = {
                str(r[0])
                for r in conn.execute(
                    "SELECT id FROM department WHERE id IS NOT NULL"
                ).fetchall()
            }

        rows = conn.execute(
            """
            SELECT id, resource_type, page_token, snapshot_complete
            FROM organization_sync_cursor
            ORDER BY resource_type
            """
        ).fetchall()

        print(f"=== 游标现状（共 {len(rows)} 条）===")
        fixed = 0
        updates: list[tuple[bool, str]] = []

        for cursor_id, resource_type, page_token, current in rows:
            target = current

            if resource_type.startswith("employee:"):
                dept_id = resource_type.split(":", 1)[1]
                if page_token:
                    target = False
                elif dept_ids and dept_id not in dept_ids:
                    target = True
                elif dept_ids:
                    target = True
                else:
                    target = False
            elif resource_type in ("department", "employee"):
                target = not page_token

            if bool(target) != bool(current):
                updates.append((target, cursor_id))
                fixed += 1
                flag = "-> True" if target else "-> False"
                print(f"  {resource_type:32s} {current} {flag}")

        print("")
        if not updates:
            print("OK 无需修复（所有游标状态已正确）")
            return 0

        print(f"共需修复 {fixed} 条")

        if not args.apply:
            print("（演练模式，未写库。加 --apply 实际执行）")
            return 0

        conn.executemany(
            "UPDATE organization_sync_cursor SET snapshot_complete = ? WHERE id = ?",
            updates,
        )
        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM organization_sync_cursor WHERE snapshot_complete = 0"
        ).fetchone()[0]
        print(f"OK 已写库。剩余 snapshot_complete=0 的游标：{remaining} 条")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
