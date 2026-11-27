#!/usr/bin/env python
"""清理真实库里混入的 2099 年 E2E 测试数据（默认只演练）。

⚠️ 背景
真实 ``perf.db`` 里曾出现 ``2099-06/07/08/09`` 的月计划（属真实员工账号），
会被台账、统计和领导工作台当作正常业务数据。

⚠️ 安全默认
- 不加 ``--confirm`` 只打印将删除什么，**不动数据**；
- 加 ``--confirm`` 前请先备份：
  ``cp backend/data/perf.db backend/data/perf.db.bak-$(date +%Y%m%d-%H%M)``
  （WAL 模式请用 ``sqlite3 .backup`` 或同时复制 -wal/-shm）

用法::

    python backend/scripts/clean_e2e_test_data.py
    python backend/scripts/clean_e2e_test_data.py --confirm
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.domain.models import (  # noqa: E402
    DayPlan,
    Employee,
    MonthPlan,
    WeekPlan,
)
from backend.app.domain.plan_models import PlanApproval  # noqa: E402
from backend.app.domain.result_models import Result  # noqa: E402

CUTOFF = date(2099, 1, 1)


def _owner_name(db, owner_id: str) -> str:
    employee = db.get(Employee, str(owner_id))
    return employee.name if employee else str(owner_id)


def _collect(db):
    month_plans = (
        db.execute(select(MonthPlan).where(MonthPlan.plan_month.like("2099%")))
        .scalars()
        .all()
    )
    week_plans = (
        db.execute(select(WeekPlan).where(WeekPlan.week_start >= CUTOFF))
        .scalars()
        .all()
    )
    day_plans = (
        db.execute(select(DayPlan).where(DayPlan.plan_date >= CUTOFF))
        .scalars()
        .all()
    )
    plan_refs = (
        [("month", plan.id, plan.owner_id, plan.plan_month) for plan in month_plans]
        + [("week", plan.id, plan.owner_id, plan.week_start.isoformat()) for plan in week_plans]
        + [("day", plan.id, plan.owner_id, plan.plan_date.isoformat()) for plan in day_plans]
    )
    plan_ids = [ref[1] for ref in plan_refs]
    results = (
        db.execute(select(Result).where(Result.plan_id.in_(plan_ids or ["__none__"])))
        .scalars()
        .all()
    )
    approvals = (
        db.execute(select(PlanApproval).where(PlanApproval.plan_id.in_(plan_ids or ["__none__"])))
        .scalars()
        .all()
    )
    return month_plans, week_plans, day_plans, results, approvals, plan_refs


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 2099 E2E 测试数据")
    parser.add_argument("--confirm", action="store_true", help="真正删除；不加只演练")
    args = parser.parse_args()

    session = get_sessionmaker()()
    try:
        month_plans, week_plans, day_plans, results, approvals, plan_refs = _collect(session)
        print(f"月计划: {len(month_plans)}")
        print(f"周计划: {len(week_plans)}")
        print(f"日计划: {len(day_plans)}")
        print(f"关联成果: {len(results)}")
        print(f"关联审批流水: {len(approvals)}")
        print("\n明细（最多 30 条）：")
        for plan_type, plan_id, owner_id, period in plan_refs[:30]:
            print(f"  {plan_type:5} {plan_id}  归属={_owner_name(session, owner_id)}  周期={period}")

        if not args.confirm:
            print("\n⚠️ 演练模式：未删除任何数据。确认无误后加 --confirm。")
            return 0

        print("\n开始删除……")
        for result in results:
            session.delete(result)
        for approval in approvals:
            session.delete(approval)
        for plan in day_plans + week_plans + month_plans:
            session.delete(plan)
        session.commit()
        print("✅ 已删除；成果附件文件仍保留在证据目录，可按需手工清理。")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
