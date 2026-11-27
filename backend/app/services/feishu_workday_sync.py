"""🕘 **飞书排班 → 员工排班日历**（★ 2026-11-27 新增）。

⚠️ **事实源**
------------------------------------------------------------------
排班与考勤的事实源是**飞书考勤**：HR 在飞书后台给每个人设置每天的班次。
本服务把 ``user_daily_shifts/query`` 的结果同步到 ``employee_shift_day``：

- ``shift_id == "0"`` ⇒ 休息/未排班 ⇒ ``is_workday=False``；
- 其它 shift_id ⇒ 上班 ⇒ ``is_workday=True``。

⚠️ **未排班 ≠ 休息**
如果某月同步成功、但某人某天查不到排班，说明「HR 没排/不在考勤组」，
不是「这天休息」。日历解析会把它标成 ``UNASSIGNED``（不计应填 + 告警）。
只有 ``ShiftSyncRun.status == SUCCESS`` 的月份才允许这样判定；
同步失败/部分成功不能拿来判「未排班」。

⚠️ **分批限制**
飞书：单次 ≤50 人、日期跨度 ≤30 天（含首尾）。
这里按月拆成 30 天窗口，再按 50 人分批。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.models import Employee
from ..domain.shift_models import EmployeeShiftDay, ShiftSyncRun
from .feishu_client import FeishuClient

logger = logging.getLogger(__name__)

MAX_USERS_PER_REQUEST = 50
MAX_WINDOW_DAYS = 30
#: 未排班员工占比超过该阈值时，视为「排班尚未发布」，不建立 SUCCESS 覆盖。
MAX_MISSING_RATIO = 0.10


def _parse_period(period: str) -> tuple[date, date]:
    try:
        year, month = (int(item) for item in str(period).split("-", 1))
        start = date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"period 格式应为 YYYY-MM，收到 {period!r}") from exc
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return start, next_month - timedelta(days=1)


def _active_employees(db: Session) -> list[Employee]:
    return (
        db.execute(
            select(Employee)
            .where(
                Employee.employment_status == "active",
                Employee.account_status == "enabled",
            )
            .order_by(Employee.id)
        )
        .scalars()
        .all()
    )


def _date_windows(start: date, end: date) -> Iterable[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


def _make_client() -> FeishuClient:
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise RuntimeError("未配置飞书应用凭据，无法同步排班")
    return FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )


def _row_date(row: dict[str, Any]) -> date | None:
    try:
        month = int(row.get("month") or 0)
        day_no = int(row.get("day_no") or 0)
        return date(month // 100, month % 100, day_no)
    except (ValueError, TypeError):
        return None


def sync_month(
    db: Session,
    period: str,
    *,
    confirm: bool = False,
    client: FeishuClient | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """同步一个月的飞书排班。

    ``confirm=False`` 只统计，不写库；``confirm=True`` 写 ``employee_shift_day``
    并记录 ``ShiftSyncRun``。
    """
    start, end = _parse_period(period)
    employees = _active_employees(db)
    employee_ids = [employee.id for employee in employees]
    if not employee_ids:
        return {
            "period": period,
            "status": "EMPTY",
            "employeeCount": 0,
            "shiftCount": 0,
            "missingEmployees": [],
            "errors": [],
            "written": False,
        }

    own_client = client is None
    feishu = client or _make_client()
    rows_by_key: dict[tuple[str, date], dict[str, Any]] = {}
    errors: list[str] = []
    try:
        for window_start, window_end in _date_windows(start, end):
            for i in range(0, len(employee_ids), MAX_USERS_PER_REQUEST):
                chunk = employee_ids[i : i + MAX_USERS_PER_REQUEST]
                try:
                    rows = feishu.fetch_user_daily_shifts(
                        chunk,
                        window_start.strftime("%Y%m%d"),
                        window_end.strftime("%Y%m%d"),
                    )
                except Exception as exc:  # noqa: BLE001 - 单批失败不中断整体
                    logger.warning("排班同步批次失败：%s", exc)
                    errors.append(f"{window_start}~{window_end} / {len(chunk)} 人：{exc}")
                    continue
                for row in rows:
                    employee_id = str(row.get("user_id") or "")
                    work_date = _row_date(row)
                    if not employee_id or work_date is None:
                        continue
                    if not (start <= work_date <= end):
                        continue
                    shift_id = str(row.get("shift_id") or "")
                    rows_by_key[(employee_id, work_date)] = {
                        "employee_id": employee_id,
                        "work_date": work_date,
                        "shift_id": shift_id,
                        "is_workday": shift_id != "0",
                        "group_id": str(row.get("group_id") or ""),
                    }
    finally:
        if own_client:
            feishu.close()

    covered = {employee_id for employee_id, _ in rows_by_key}
    missing = [employee for employee in employees if employee.id not in covered]
    missing_ratio = (len(missing) / len(employees)) if employees else 0.0
    too_many_missing = missing_ratio > MAX_MISSING_RATIO and not allow_partial
    status = "SUCCESS" if not errors and not too_many_missing else "PARTIAL"

    if confirm:
        run = db.execute(
            select(ShiftSyncRun).where(ShiftSyncRun.period == period)
        ).scalar_one_or_none()
        if run is None:
            run = ShiftSyncRun(period=period, status="RUNNING", started_at=datetime.now())
            db.add(run)
        run.status = status
        run.employee_count = len(employees)
        run.shift_count = len(rows_by_key)
        run.missing_employee_count = len(missing)
        run.error_message = "\n".join(errors)[:4000] if errors else None
        run.finished_at = datetime.now()

        if status == "SUCCESS":
            # 成功同步才做“整月替换” —— 避免 HR 删除排班后旧数据残留。
            db.query(EmployeeShiftDay).filter(
                EmployeeShiftDay.work_date >= start,
                EmployeeShiftDay.work_date <= end,
            ).delete(synchronize_session=False)
            db.add_all(
                [
                    EmployeeShiftDay(
                        employee_id=item["employee_id"],
                        work_date=item["work_date"],
                        shift_id=item["shift_id"],
                        is_workday=item["is_workday"],
                        group_id=item["group_id"],
                        source="feishu",
                    )
                    for item in rows_by_key.values()
                ]
            )
        else:
            # 部分成功：只 upsert 拉到的行，不做整月替换。
            existing = {
                (row.employee_id, row.work_date): row
                for row in db.execute(
                    select(EmployeeShiftDay).where(
                        EmployeeShiftDay.work_date >= start,
                        EmployeeShiftDay.work_date <= end,
                    )
                )
                .scalars()
                .all()
            }
            for item in rows_by_key.values():
                row = existing.get((item["employee_id"], item["work_date"]))
                if row is None:
                    db.add(EmployeeShiftDay(**item, source="feishu"))
                else:
                    row.shift_id = item["shift_id"]
                    row.is_workday = item["is_workday"]
                    row.group_id = item["group_id"]
                    row.synced_at = datetime.now()
        db.commit()

    return {
        "period": period,
        "status": status,
        "employeeCount": len(employees),
        "shiftCount": len(rows_by_key),
        "missingEmployees": [
            {"id": employee.id, "name": employee.name} for employee in missing
        ],
        "missingRatio": round(missing_ratio, 4),
        "tooManyMissing": too_many_missing,
        "errors": errors,
        "written": confirm,
    }


__all__ = ["sync_month"]
