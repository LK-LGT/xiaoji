"""迁移校验脚本：检查 9 张新表、索引与约束是否落库。

用法（backend 目录下）：
    python scripts/verify_phase3_schema.py [db_path]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

NEW_TABLES = [
    "plan_draft_version",
    "plan_approval",
    "dispute_case",
    "dispute_reviewer",
    "dispute_opinion",
    "ai_review",
    "ai_conversation",
    "ai_message",
    "ai_suggestion_action",
]

EXPECTED_INDEXES = [
    "ix_plan_draft_version_plan",
    "ix_plan_draft_version_request",
    "ix_plan_draft_version_conversation",
    "ix_plan_approval_plan",
    "ix_plan_approval_actor",
    "ix_plan_approval_dedup",
    "ix_dispute_case_status",
    "ix_dispute_case_owner",
    "ix_dispute_case_dept",
    "ix_dispute_case_result",
    "ix_dispute_reviewer_case",
    "ix_dispute_opinion_case",
    "ix_ai_review_biz",
    "ix_ai_review_hash",
    "ix_ai_review_status",
    "ix_ai_conversation_owner",
    "ix_ai_conversation_scene",
    "ix_ai_message_conversation",
    "ix_ai_suggestion_action_suggestion",
    "ix_ai_suggestion_action_actor",
]


def main(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }

        missing_tables = [t for t in NEW_TABLES if t not in tables]
        missing_indexes = [i for i in EXPECTED_INDEXES if i not in indexes]

        print(f"数据库: {db_path}")
        print(f"表总数: {len(tables)}")
        print(f"新表: {len(NEW_TABLES) - len(missing_tables)}/{len(NEW_TABLES)}")
        if missing_tables:
            print(f"  缺失表: {missing_tables}")
        print(f"索引: {len(EXPECTED_INDEXES) - len(missing_indexes)}/{len(EXPECTED_INDEXES)}")
        if missing_indexes:
            print(f"  缺失索引: {missing_indexes}")

        # 外键检查
        fk_rows = list(conn.execute("PRAGMA foreign_key_check"))
        print(f"外键违规: {len(fk_rows)}")
        for row in fk_rows[:10]:
            print(f"  {row}")

        ok = not missing_tables and not missing_indexes and not fk_rows
        print("结论:", "通过" if ok else "失败")
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[1] / "data" / "perf.db"
    target = sys.argv[1] if len(sys.argv) > 1 else str(default)
    raise SystemExit(main(target))
