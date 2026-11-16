"""考勤同步服务 —— 阶段二（考勤与证据域）。

职责：
1. 从飞书拉取打卡结果（原始事实层）→ 落库 `attendance_record`。
2. 依据飞书 check_result 归类**六类异常**：
   late / early_leave / missing_punch / leave / overtime / outside。
3. 按周期把异常聚合成 `Evidence`（usage = reference_only）。
4. 提供增量同步与幂等保证。

关键红线：
- 考勤数据**只**产出 reference_only 证据，**严禁**进入 MetricValue 算分集。
  该保证由 `evidence_service.add_metric_value` 在算分集唯一入口硬拦截，
  本模块从不直接写 MetricValue。

飞书接口约束（实测）：
- 查询打卡结果为 POST /attendance/v1/user_tasks/query；
- 单次最多 50 个 user_ids；单次日期跨度最多 30 天；
- 权限要求 `attendance:task:readonly`（当前应用已具备，实测可正常返回）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import AttendanceAnomalyType
from ..domain.models import AttendanceRecord, Employee
from . import evidence_service
from .feishu_client import FeishuClient, FeishuError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 飞书 check_result → 六类异常 的映射
# ---------------------------------------------------------------------------

# 上班打卡结果 → 异常类型
CHECK_IN_ANOMALY_MAP: dict[str, AttendanceAnomalyType] = {
    "Late": AttendanceAnomalyType.LATE,
    "SeriousLate": AttendanceAnomalyType.LATE,
    "Lack": AttendanceAnomalyType.MISSING_PUNCH,
    "Early": AttendanceAnomalyType.EARLY_LEAVE,
}

# 下班打卡结果 → 异常类型
CHECK_OUT_ANOMALY_MAP: dict[str, AttendanceAnomalyType] = {
    "Early": AttendanceAnomalyType.EARLY_LEAVE,
    "Lack": AttendanceAnomalyType.MISSING_PUNCH,
    "Late": AttendanceAnomalyType.LATE,
}

# 无需打卡（请假/出差/外勤等由飞书排班免除）→ 请假类异常
NO_NEED_CHECK = "NoNeedCheck"
# 尚未打卡（未来班次）
TODO_CHECK = "Todo"
# 正常
NORMAL_CHECK = "Normal"


@dataclass
class AttendanceSyncReport:
    """一次同步的结果统计。"""

    start_date: str
    end_date: str
    employee_count: int = 0
    task_count: int = 0
    inserted: int = 0
    updated: int = 0
    anomaly_counts: dict[str, int] = field(default_factory=dict)
    skipped_users: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "employee_count": self.employee_count,
            "task_count": self.task_count,
            "inserted": self.inserted,
            "updated": self.updated,
            "anomaly_counts": dict(self.anomaly_counts),
            "skipped_users": list(self.skipped_users),
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# 周期工具
# ---------------------------------------------------------------------------


def period_of(day: date) -> str:
    """考核周期键：YYYY-MM。"""
    return f"{day.year:04d}-{day.month:02d}"


def _epoch_to_datetime(value: Any) -> datetime | None:
    """飞书 epoch 秒 → datetime（UTC）。"""
    if value in (None, "", "0", 0):
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


def _parse_day(day: Any) -> date | None:
    """飞书 day（yyyyMMdd 或 yyyy-MM-dd）→ date。"""
    if day in (None, ""):
        return None
    text = str(day)
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 异常识别
# ---------------------------------------------------------------------------


def classify_record(
    record: dict[str, Any],
    *,
    shift_seq: int = 0,
) -> list[str]:
    """识别单条打卡记录所属的异常类型。

    规则：
    - 上班 `Late`/`SeriousLate` → late
    - 下班 `Early` → early_leave
    - 上下班任一 `Lack` → missing_punch
    - 上下班 `NoNeedCheck` → leave（飞书排班免除打卡，视为请假/免打卡）
    - `task_shift_type == 1`（加班班段）→ overtime
    - 打卡明细 `is_field == true` → outside（外勤打卡）
    - `Todo`（尚未打卡）不计异常

    Returns:
        异常类型字符串列表（去重、保持稳定顺序）。
    """
    anomalies: list[str] = []

    def _add(value: AttendanceAnomalyType) -> None:
        if value.value not in anomalies:
            anomalies.append(value.value)

    check_in = record.get("check_in_result")
    check_out = record.get("check_out_result")

    if check_in in CHECK_IN_ANOMALY_MAP:
        _add(CHECK_IN_ANOMALY_MAP[check_in])
    if check_out in CHECK_OUT_ANOMALY_MAP:
        _add(CHECK_OUT_ANOMALY_MAP[check_out])

    # 无需打卡：飞书已按排班免除，计为请假类参考记录
    if check_in == NO_NEED_CHECK or check_out == NO_NEED_CHECK:
        _add(AttendanceAnomalyType.LEAVE)

    # 加班班段
    if int(record.get("task_shift_type") or 0) == 1:
        _add(AttendanceAnomalyType.OVERTIME)

    # 外勤打卡
    for side in ("check_in_record", "check_out_record"):
        detail = record.get(side) or {}
        if detail.get("is_field"):
            _add(AttendanceAnomalyType.OUTSIDE)
            break

    return anomalies


def _extract_punch_detail(record: dict[str, Any], side: str) -> dict[str, Any]:
    return record.get(f"{side}_record") or {}


# ---------------------------------------------------------------------------
# 同步服务
# ---------------------------------------------------------------------------


class AttendanceSyncService:
    """飞书考勤 → 本地 attendance_record → reference_only 证据。"""

    def __init__(self, db: Session, client: FeishuClient) -> None:
        self.db = db
        self.client = client

    # -- 员工选择 -----------------------------------------------------------

    def _resolve_employees(
        self, employee_ids: Sequence[str] | None, limit: int | None
    ) -> list[Employee]:
        """取待同步员工。默认取全部在职员工。"""
        stmt = select(Employee)
        if employee_ids:
            stmt = stmt.where(Employee.id.in_(list(employee_ids)))
        if limit:
            stmt = stmt.limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    # -- 日期窗口 -----------------------------------------------------------

    @staticmethod
    def _split_windows(
        start: date, end: date, max_days: int = FeishuClient.ATTENDANCE_MAX_DATE_RANGE_DAYS
    ) -> list[tuple[date, date]]:
        """把日期区间切成不超过 max_days 的窗口（飞书硬限制）。"""
        windows: list[tuple[date, date]] = []
        cursor = start
        while cursor <= end:
            window_end = min(cursor + timedelta(days=max_days - 1), end)
            windows.append((cursor, window_end))
            cursor = window_end + timedelta(days=1)
        return windows

    # -- 主流程 -------------------------------------------------------------

    def sync(
        self,
        start_date: date,
        end_date: date,
        *,
        employee_ids: Sequence[str] | None = None,
        limit: int | None = None,
        generate_evidence: bool = True,
        need_overtime_result: bool = True,
    ) -> AttendanceSyncReport:
        """同步一个日期区间的考勤数据。

        幂等：按 (employee_id, work_date, shift_seq) 唯一键 upsert，
        重复同步同一区间不会产生重复记录，也不会重复生成证据。
        """
        report = AttendanceSyncReport(
            start_date=start_date.isoformat(), end_date=end_date.isoformat()
        )

        employees = self._resolve_employees(employee_ids, limit)
        report.employee_count = len(employees)
        if not employees:
            return report

        user_ids = [e.id for e in employees]
        windows = self._split_windows(start_date, end_date)

        for win_start, win_end in windows:
            try:
                tasks = self.client.fetch_attendance_tasks(
                    user_ids,
                    win_start.strftime("%Y%m%d"),
                    win_end.strftime("%Y%m%d"),
                    need_overtime_result=need_overtime_result,
                )
            except FeishuError as exc:
                msg = f"窗口 {win_start}~{win_end} 拉取失败：{exc.message}"
                logger.warning(msg)
                report.errors.append(msg)
                continue

            report.task_count += len(tasks)
            self._persist_tasks(tasks, report)

        self.db.flush()

        if generate_evidence:
            self.generate_evidence(start_date, end_date, report)

        return report

    # -- 落库 ---------------------------------------------------------------

    def _persist_tasks(
        self, tasks: Iterable[dict[str, Any]], report: AttendanceSyncReport
    ) -> None:
        """把飞书任务写入 attendance_record（按唯一键 upsert）。"""
        for task in tasks:
            user_id = str(task.get("user_id") or "").strip()
            if not user_id:
                continue
            work_date = _parse_day(task.get("day"))
            if work_date is None:
                continue

            records = task.get("records") or []
            for shift_seq, record in enumerate(records):
                anomalies = classify_record(record, shift_seq=shift_seq)
                for atype in anomalies:
                    report.anomaly_counts[atype] = report.anomaly_counts.get(atype, 0) + 1

                check_in_detail = _extract_punch_detail(record, "check_in")
                check_out_detail = _extract_punch_detail(record, "check_out")

                values: dict[str, Any] = {
                    "employee_id": user_id,
                    "work_date": work_date,
                    "shift_seq": shift_seq,
                    "task_shift_type": int(record.get("task_shift_type") or 0),
                    "feishu_result_id": task.get("result_id"),
                    "attendance_group_id": task.get("group_id"),
                    "shift_id": task.get("shift_id"),
                    "check_in_result": record.get("check_in_result"),
                    "check_out_result": record.get("check_out_result"),
                    "check_in_result_supplement": record.get("check_in_result_supplement"),
                    "check_out_result_supplement": record.get(
                        "check_out_result_supplement"
                    ),
                    "check_in_shift_time": _to_int(record.get("check_in_shift_time")),
                    "check_out_shift_time": _to_int(record.get("check_out_shift_time")),
                    "check_in_time": _to_int(check_in_detail.get("check_time")),
                    "check_out_time": _to_int(check_out_detail.get("check_time")),
                    "check_in_record_id": record.get("check_in_record_id") or None,
                    "check_out_record_id": record.get("check_out_record_id") or None,
                    "check_in_location": check_in_detail.get("location_name") or None,
                    "check_out_location": check_out_detail.get("location_name") or None,
                    "is_field": bool(
                        check_in_detail.get("is_field") or check_out_detail.get("is_field")
                    ),
                    "is_wifi": bool(
                        check_in_detail.get("is_wifi") or check_out_detail.get("is_wifi")
                    ),
                    "check_in_type": _to_int(check_in_detail.get("type")),
                    "check_out_type": _to_int(check_out_detail.get("type")),
                    "anomaly_types": anomalies,
                    "as_of": datetime.now(UTC).replace(tzinfo=None),
                    "raw_json": {
                        "task": {
                            "day": task.get("day"),
                            "group_id": task.get("group_id"),
                            "shift_id": task.get("shift_id"),
                            "result_id": task.get("result_id"),
                            "employee_name": task.get("employee_name"),
                        },
                        "record": record,
                    },
                }

                existing = self.db.execute(
                    select(AttendanceRecord).where(
                        AttendanceRecord.employee_id == user_id,
                        AttendanceRecord.work_date == work_date,
                        AttendanceRecord.shift_seq == shift_seq,
                    )
                ).scalar_one_or_none()

                if existing is None:
                    self.db.add(AttendanceRecord(**values))
                    report.inserted += 1
                else:
                    for key, value in values.items():
                        if key in ("employee_id", "work_date", "shift_seq"):
                            continue
                        setattr(existing, key, value)
                    report.updated += 1

    # -- 证据生成 -----------------------------------------------------------

    def generate_evidence(
        self,
        start_date: date,
        end_date: date,
        report: AttendanceSyncReport | None = None,
    ) -> int:
        """把区间内的异常聚合成 reference_only 证据。

        每个「员工 × 周期 × 异常类型」生成一条 Evidence，
        避免同一周期内同类型异常产生几十条重复证据。
        """
        rows = list(
            self.db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.work_date >= start_date,
                    AttendanceRecord.work_date <= end_date,
                )
            ).scalars().all()
        )
        if not rows:
            return 0

        # 聚合：(employee_id, period, anomaly_type) → count
        buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            period = period_of(row.work_date)
            for atype in row.anomaly_types or []:
                key = (row.employee_id, period, atype)
                bucket = buckets.setdefault(
                    key,
                    {
                        "count": 0,
                        "as_of": row.as_of,
                        "dates": [],
                        "approved": None,
                    },
                )
                bucket["count"] += 1
                bucket["dates"].append(row.work_date.isoformat())
                if row.as_of and (
                    bucket["as_of"] is None or row.as_of > bucket["as_of"]
                ):
                    bucket["as_of"] = row.as_of

        # 请假类：飞书免除打卡视为已由排班确认
        for (_emp_id, _period, atype), bucket in buckets.items():
            if atype == AttendanceAnomalyType.LEAVE.value:
                bucket["approved"] = True

        anomalies: list[evidence_service.AnomalyInput] = []
        for (emp_id, period, atype), bucket in sorted(buckets.items()):
            try:
                enum_type = AttendanceAnomalyType(atype)
            except ValueError:
                continue
            anomalies.append(
                evidence_service.AnomalyInput(
                    employee_id=emp_id,
                    period=period,
                    anomaly_type=enum_type,
                    count=bucket["count"],
                    as_of=bucket["as_of"],
                    approved=bucket["approved"],
                    detail=None,
                )
            )

        if not anomalies:
            return 0

        # 幂等：先删掉该周期同类型、同员工的旧证据，再重建，避免重复累加
        self._clear_existing_evidence(anomalies)

        evidence_service.record_attendance_anomalies(self.db, anomalies)
        self.db.flush()
        logger.info(
            "考勤证据生成完成，条数=%s，区间=%s~%s",
            len(anomalies),
            start_date,
            end_date,
        )
        return len(anomalies)

    def _clear_existing_evidence(
        self, anomalies: Sequence[evidence_service.AnomalyInput]
    ) -> None:
        """清除将被重建的考勤证据（仅限 reference_only 的考勤来源）。

        幂等保证：证据的 source_ref 形如
        `{employee_id}|{period}|attendance|{anomaly_type}`，
        精确匹配后才删除，不会误删其他员工 / 其他周期 / 其他来源的证据。
        """
        target_refs = {
            f"{a.employee_id}|{a.period}|attendance|{a.anomaly_type.value}"
            for a in anomalies
        }
        if not target_refs:
            return

        rows = list(
            self.db.execute(
                select(evidence_service.Evidence).where(
                    evidence_service.Evidence.source.in_(
                        evidence_service.ATTENDANCE_SOURCES
                    ),
                    evidence_service.Evidence.usage
                    == evidence_service.EvidenceUsage.REFERENCE_ONLY.value,
                )
            ).scalars().all()
        )
        for row in rows:
            if row.source_ref in target_refs:
                self.db.delete(row)
        self.db.flush()


def _to_int(value: Any) -> int | None:
    if value in (None, "", "0", 0):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
