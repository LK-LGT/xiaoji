"""🕘 **飞书排班同步 + 未排班判定**（2026-11-27）。

口径：
- 飞书 `shift_id == "0"` ⇒ 休息；
- 有班次 ⇒ 上班；
- 某月同步成功但查不到某人某天 ⇒ ``UNASSIGNED``（不计应填 + 告警）；
- 人工请假/出差优先于排班；
- 同步失败的月份不能判“未排班”（回退旧规则）。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Employee
from backend.app.domain.shift_models import EmployeeShiftDay, ShiftSyncRun
from backend.app.services import feishu_workday_sync, workday_rule


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def employees(db):
    db.add_all(
        [
            Employee(
                id="E_A", name="员工甲", employment_status="active",
                account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_B", name="员工乙", employment_status="active",
                account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_C", name="员工丙", employment_status="active",
                account_status="enabled", token_version=1,
            ),
        ]
    )
    db.commit()


class FakeShiftClient:
    """只返回 E_A 的 11 月排班（E_B/E_C 没有排班）。"""

    def fetch_user_daily_shifts(self, user_ids, start_date, end_date):
        if "E_A" not in user_ids:
            return []
        return [
            {"user_id": "E_A", "month": 202611, "day_no": 5, "shift_id": "S1", "group_id": "G1"},
            {"user_id": "E_A", "month": 202611, "day_no": 6, "shift_id": "0", "group_id": "G1"},
        ]


def test_同步演练不写库(db, employees):
    report = feishu_workday_sync.sync_month(
        db, "2026-11", confirm=False, client=FakeShiftClient(), allow_partial=True
    )
    assert report["status"] == "SUCCESS"
    assert report["shiftCount"] == 2
    assert {item["id"] for item in report["missingEmployees"]} == {"E_B", "E_C"}
    assert db.execute(select(EmployeeShiftDay)).scalars().all() == []
    assert db.execute(select(ShiftSyncRun)).scalars().all() == []


def test_同步确认写入排班与批次(db, employees):
    feishu_workday_sync.sync_month(db, "2026-11", confirm=True, client=FakeShiftClient(), allow_partial=True)
    rows = {
        (row.employee_id, row.work_date): row
        for row in db.execute(select(EmployeeShiftDay)).scalars().all()
    }
    assert rows[("E_A", date(2026, 11, 5))].is_workday is True
    assert rows[("E_A", date(2026, 11, 6))].is_workday is False
    run = db.execute(select(ShiftSyncRun)).scalar_one()
    assert run.period == "2026-11"
    assert run.status == "SUCCESS"
    assert run.shift_count == 2


def test_日历优先使用飞书排班(db, employees):
    feishu_workday_sync.sync_month(db, "2026-11", confirm=True, client=FakeShiftClient(), allow_partial=True)
    calendar = {
        item["date"]: item
        for item in workday_rule.build_calendar(
            db, date(2026, 11, 1), date(2026, 11, 7), "E_A"
        )
    }
    assert calendar["2026-11-05"]["ruleType"] == "WORKDAY"
    assert calendar["2026-11-05"]["forceReport"] is True
    assert calendar["2026-11-05"]["source"] == "feishu"
    assert calendar["2026-11-06"]["ruleType"] == "WEEKEND"
    assert calendar["2026-11-06"]["forceReport"] is False


def test_同步成功但无排班标记未排班且不计应填(db, employees):
    feishu_workday_sync.sync_month(db, "2026-11", confirm=True, client=FakeShiftClient(), allow_partial=True)
    calendar = workday_rule.build_calendar(
        db, date(2026, 11, 1), date(2026, 11, 7), "E_B"
    )
    assert all(item["ruleType"] == "UNASSIGNED" for item in calendar)
    assert workday_rule.count_missing_required(
        db, date(2026, 11, 1), date(2026, 11, 7), "E_B", set()
    ) == 0


def test_个人请假优先于飞书排班(db, employees):
    feishu_workday_sync.sync_month(db, "2026-11", confirm=True, client=FakeShiftClient(), allow_partial=True)
    workday_rule.create_rule(
        db,
        rule_date=date(2026, 11, 5),
        rule_type="LEAVE",
        employee_id="E_A",
        description="年假",
    )
    db.commit()
    item = workday_rule.resolve_rule(db, date(2026, 11, 5), "E_A")
    assert item["ruleType"] == "LEAVE"


def test_未同步月份仍回退旧规则(db, employees):
    item = workday_rule.resolve_rule(db, date(2026, 12, 7), "E_A")
    assert item["source"] == "default"
    assert item["ruleType"] == "WORKDAY"
