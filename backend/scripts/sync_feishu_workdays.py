#!/usr/bin/env python
"""从飞书考勤同步员工排班日历（默认只演练）。

用法::

    python backend/scripts/sync_feishu_workdays.py --month 2026-11
    python backend/scripts/sync_feishu_workdays.py --month 2026-11 --confirm

⚠️ 飞书限制：单次 ≤50 人、日期跨度 ≤30 天；脚本内部自动分批。
⚠️ 只有整月全部批次成功时，才会把「查不到排班」判定为 UNASSIGNED；
   部分失败只做增量更新，不会把缺数据当成“未排班”。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.services import feishu_workday_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="同步飞书排班到员工排班日历")
    parser.add_argument(
        "--month",
        default=date.today().strftime("%Y-%m"),
        help="月份 YYYY-MM，默认当前月",
    )
    parser.add_argument("--confirm", action="store_true", help="真正写库；不加只演练")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="未排班员工超过 10% 时仍建立 SUCCESS 覆盖（仅确认排班已完整发布时使用）",
    )
    args = parser.parse_args()

    session = get_sessionmaker()()
    try:
        report = feishu_workday_sync.sync_month(
            session, args.month, confirm=args.confirm, allow_partial=args.allow_partial
        )
    finally:
        session.close()

    print(f"月份: {report['period']}")
    print(f"状态: {report['status']}")
    print(f"在职员工: {report['employeeCount']}")
    print(f"排班记录: {report['shiftCount']}")
    print(f"写入数据库: {'是' if report['written'] else '否（演练）'}")
    print(f"未排班占比: {report['missingRatio']:.1%}")
    if report["missingEmployees"]:
        print(f"未排班员工: {len(report['missingEmployees'])}")
        for item in report["missingEmployees"][:50]:
            print(f"  - {item['id']} {item['name']}")
    if report["errors"]:
        print("错误:")
        for error in report["errors"][:20]:
            print(f"  - {error}")
    if not args.confirm:
        print("\n⚠️ 演练模式未写库；确认后加 --confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
