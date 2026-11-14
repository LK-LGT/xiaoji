"""考勤统计与规则桥接 —— 阶段二。

职责：
1. 从 `attendance_record` 聚合出周期考勤统计（六类异常次数、异常天数）。
2. 把统计结果桥接到 `rules_engine.ScoreCalculator` / `attendance_rules`
   的调整分计算链路。
3. **红线**：考勤统计只用于生成「参考型」调整建议，绝不写入 MetricValue 算分集。
   真正的扣分仍由人工确认后走既有评估流程。

设计说明：
- `attendance_rules.py` 提供的是「按缺勤天数」的扣分策略（simple/tiered/progressive）。
- `rules_engine.calculate_attendance_adjustment` 是阶段递进的等价实现。
- 本模块统一以「异常天数」为输入，输出调整分，并保留可配置策略入口。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import AttendanceAnomalyType
from ..domain.models import AttendanceRecord
from .attendance_rules import ATTENDANCE_RULE_PRESETS, AttendanceRuleConfig

logger = logging.getLogger(__name__)


# 计入「异常天数」的六类异常（请假不计入缺勤天数，因为它是已审批的正当事由）
ABSENCE_ANOMALY_TYPES: frozenset[str] = frozenset(
    {
        AttendanceAnomalyType.LATE.value,
        AttendanceAnomalyType.EARLY_LEAVE.value,
        AttendanceAnomalyType.MISSING_PUNCH.value,
        AttendanceAnomalyType.OUTSIDE.value,
    }
)

# 全部六类（用于完整展示）
ALL_ANOMALY_TYPES: tuple[str, ...] = (
    AttendanceAnomalyType.LATE.value,
    AttendanceAnomalyType.EARLY_LEAVE.value,
    AttendanceAnomalyType.MISSING_PUNCH.value,
    AttendanceAnomalyType.LEAVE.value,
    AttendanceAnomalyType.OVERTIME.value,
    AttendanceAnomalyType.OUTSIDE.value,
)


def _period_label(start_date: date, end_date: date) -> str:
    """区间对应的考核周期标签。

    - 单月区间：`YYYY-MM`
    - 跨月区间：`YYYY-MM~YYYY-MM`
    """
    start_label = f"{start_date.year:04d}-{start_date.month:02d}"
    end_label = f"{end_date.year:04d}-{end_date.month:02d}"
    return start_label if start_label == end_label else f"{start_label}~{end_label}"


@dataclass
class AttendanceSummary:
    """一个员工在一个周期的考勤统计。"""

    employee_id: str
    period: str
    total_records: int = 0
    worked_days: int = 0
    absent_days: int = 0
    anomaly_counts: dict[str, int] = field(default_factory=dict)
    anomaly_days: dict[str, int] = field(default_factory=dict)
    as_of: str | None = None
    # 跨月区间时，按自然月拆分的明细
    by_period: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "period": self.period,
            "total_records": self.total_records,
            "worked_days": self.worked_days,
            "absent_days": self.absent_days,
            "anomaly_counts": dict(self.anomaly_counts),
            "anomaly_days": dict(self.anomaly_days),
            "as_of": self.as_of,
            "by_period": dict(self.by_period),
        }


class AttendanceStatisticsService:
    """考勤统计聚合。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def summarize(
        self,
        employee_id: str,
        start_date: date,
        end_date: date,
    ) -> AttendanceSummary:
        """聚合某员工在日期区间内的考勤统计。"""
        rows = list(
            self.db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.work_date >= start_date,
                    AttendanceRecord.work_date <= end_date,
                )
            ).scalars().all()
        )

        period = _period_label(start_date, end_date)
        summary = AttendanceSummary(employee_id=employee_id, period=period)
        summary.total_records = len(rows)

        counts: dict[str, int] = {t: 0 for t in ALL_ANOMALY_TYPES}
        days_by_type: dict[str, set[str]] = {t: set() for t in ALL_ANOMALY_TYPES}
        absent_dates: set[str] = set()
        worked_dates: set[str] = set()
        latest_as_of = None

        # 按自然月拆分：证据是按月生成的，统计口径必须与之对齐
        period_records: dict[str, int] = {}
        period_absent: dict[str, set[str]] = {}
        period_counts: dict[str, dict[str, int]] = {}
        period_days: dict[str, dict[str, set[str]]] = {}

        for row in rows:
            day_text = row.work_date.isoformat()
            worked_dates.add(day_text)
            month_key = f"{row.work_date.year:04d}-{row.work_date.month:02d}"
            period_records[month_key] = period_records.get(month_key, 0) + 1
            period_absent.setdefault(month_key, set())
            period_counts.setdefault(month_key, {t: 0 for t in ALL_ANOMALY_TYPES})
            period_days.setdefault(
                month_key, {t: set() for t in ALL_ANOMALY_TYPES}
            )

            for atype in row.anomaly_types or []:
                counts[atype] = counts.get(atype, 0) + 1
                days_by_type.setdefault(atype, set()).add(day_text)
                period_counts[month_key][atype] = (
                    period_counts[month_key].get(atype, 0) + 1
                )
                period_days[month_key].setdefault(atype, set()).add(day_text)
                if atype in ABSENCE_ANOMALY_TYPES:
                    absent_dates.add(day_text)
                    period_absent[month_key].add(day_text)
            if row.as_of and (latest_as_of is None or row.as_of > latest_as_of):
                latest_as_of = row.as_of

        summary.worked_days = len(worked_dates)
        summary.absent_days = len(absent_dates)
        summary.anomaly_counts = {k: v for k, v in counts.items() if v > 0}
        summary.anomaly_days = {k: len(v) for k, v in days_by_type.items() if v}
        summary.as_of = latest_as_of.isoformat() if latest_as_of else None
        summary.by_period = {
            key: {
                "total_records": period_records[key],
                "absent_days": len(period_absent.get(key, set())),
                "anomaly_counts": {
                    k: v for k, v in period_counts[key].items() if v > 0
                },
                "anomaly_days": {
                    k: len(v)
                    for k, v in period_days[key].items()
                    if v
                },
            }
            for key in sorted(period_records)
        }
        return summary

    def summarize_all(
        self, start_date: date, end_date: date
    ) -> list[AttendanceSummary]:
        """聚合区间内所有员工的考勤统计。"""
        rows = list(
            self.db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.work_date >= start_date,
                    AttendanceRecord.work_date <= end_date,
                )
            ).scalars().all()
        )
        employee_ids = sorted({r.employee_id for r in rows})
        return [
            self.summarize(emp_id, start_date, end_date) for emp_id in employee_ids
        ]


# ---------------------------------------------------------------------------
# 规则桥接
# ---------------------------------------------------------------------------


def calculate_attendance_adjustment(
    absent_days: int,
    *,
    rule_config: AttendanceRuleConfig | None = None,
    preset: str = "progressive_weekly",
) -> float:
    """按考勤规则计算调整分（负数为扣分）。

    默认使用 `attendance_rules` 的 progressive_weekly 预设，与
    `rules_engine.ScoreCalculator.calculate_attendance_adjustment` 口径一致。
    """
    if absent_days <= 0:
        return 0.0
    if rule_config is None:
        preset_config = ATTENDANCE_RULE_PRESETS.get(preset)
        if preset_config is None:
            raise ValueError(f"未知的考勤规则预设：{preset}")
        rule_config = preset_config["config"]
    return float(rule_config.calculate_deduction(absent_days))


def build_adjustment_context(
    summary: AttendanceSummary,
    *,
    rule_config: AttendanceRuleConfig | None = None,
    preset: str = "progressive_weekly",
) -> dict[str, Any]:
    """构造调整上下文 —— 供人工确认流程使用。

    返回值只是一份**建议**；系统不会自动把它写进算分集。
    """
    adjustment = calculate_attendance_adjustment(
        summary.absent_days, rule_config=rule_config, preset=preset
    )
    return {
        "employee_id": summary.employee_id,
        "period": summary.period,
        "absent_days": summary.absent_days,
        "anomaly_counts": dict(summary.anomaly_counts),
        "suggested_adjustment": adjustment,
        "usage": "reference_only",
        "disclaimer": "考勤异常仅作参考，调整分需人工确认后生效",
        "as_of": summary.as_of,
    }
