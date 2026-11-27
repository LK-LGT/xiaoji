#!/usr/bin/env python
"""导入法定节假日 / 调休（工作日规则 CSV）。

⚠️ **为什么需要**
``workday_rule`` 表当前是空的 ⇒ 系统按「周一~周五工作日」默认推导，
法定节假日与调休会被算成工作日，影响日计划应填数、逾期判断和统计口径。

⚠️ **不内置节假日数据**
中国法定节假日每年由国务院公布，硬编码在代码里会过期且无人负责。
本脚本只负责**导入**，数据由 HR 按官方安排填写 CSV。

CSV 列（首行表头）::

    date,rule_type,description,force_report
    2026-01-01,HOLIDAY,元旦,false
    2026-02-17,HOLIDAY,春节,false
    2026-02-14,WORKDAY,春节调休上班,true

``rule_type`` 取值：``HOLIDAY`` / ``WORKDAY`` / ``WEEKEND`` /
``SPECIAL_SHIFT``（个人请假/出差不通过本脚本导入）。

用法::

    # 默认只演练，不写库
    python backend/scripts/import_workday_rules.py --csv backend/data/workday_rules_2026.csv

    # 确认后写库
    python backend/scripts/import_workday_rules.py --csv ... --confirm
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.domain.workday_models import RULE_TYPES  # noqa: E402
from backend.app.services import workday_rule  # noqa: E402


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _load_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "rule_type"}
        if not required.issubset({str(item).strip() for item in (reader.fieldnames or [])}):
            raise SystemExit(f"CSV 缺少必填列：{sorted(required)}；实际表头：{reader.fieldnames}")
        rows = []
        for index, raw in enumerate(reader, start=2):
            text = str(raw.get("date") or "").strip()
            if not text:
                continue
            try:
                day = date.fromisoformat(text[:10])
            except ValueError as exc:
                raise SystemExit(f"第 {index} 行日期非法：{text!r}") from exc
            rule_type = str(raw.get("rule_type") or "").strip().upper()
            if rule_type not in RULE_TYPES:
                raise SystemExit(f"第 {index} 行 rule_type 非法：{rule_type!r}")
            rows.append(
                {
                    "date": day,
                    "rule_type": rule_type,
                    "description": str(raw.get("description") or "").strip(),
                    "force_report": _truthy(str(raw.get("force_report") or "")),
                }
            )
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="导入工作日规则（节假日/调休）")
    parser.add_argument("--csv", required=True, help="CSV 文件路径")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="真正写库；不加只演练（默认）",
    )
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        raise SystemExit(f"CSV 不存在：{path}")

    rows = _load_rows(path)
    print(f"待导入 {len(rows)} 条；模式：{'写库' if args.confirm else '演练（不写库）'}")

    session = get_sessionmaker()()
    created = skipped = 0
    try:
        for row in rows:
            existing = [
                item
                for item in workday_rule.list_rules(
                    session, rule_type=row["rule_type"], scope="global"
                )
                if item.rule_date == row["date"]
            ]
            if existing:
                skipped += 1
                print(f"SKIP  {row['date']} {row['rule_type']}（已存在）")
                continue
            if args.confirm:
                workday_rule.create_rule(
                    session,
                    rule_date=row["date"],
                    rule_type=row["rule_type"],
                    force_report=row["force_report"],
                    description=row["description"],
                )
            created += 1
            print(f"{'CREATE' if args.confirm else 'DRY  '} {row['date']} {row['rule_type']} {row['description']}")
        if args.confirm:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()

    print(f"完成：新增/待新增 {created} 条，跳过已存在 {skipped} 条")
    if not args.confirm:
        print("⚠️ 演练模式未写库；确认无误后加 --confirm 重跑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
