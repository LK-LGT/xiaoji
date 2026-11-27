"""🕘 **员工排班日历（飞书考勤）**（★ 2026-11-27 新增）。

⚠️ **为什么需要**
------------------------------------------------------------------
原来 ``workday_rule`` 没有显式规则时按「周一~周五上班」推导。
但公司真正的排班在**飞书考勤**里：HR 会根据法定节假日、调休、
门店/班次安排给每个人设置每天的班次。

本模块只存**飞书排班的同步结果**（员工 × 日期 × 是否上班），
日历解析优先读它；``workday_rule`` 继续承担**人工例外**
（请假 / 出差 / 手工调整）。

⚠️ **未排班 ≠ 休息**
飞书没有返回某人某天的班次，可能是「HR 还没排班」「不在考勤组」，
不是「这天休息」。因此同步会记录 ``ShiftSyncRun`` 覆盖状态：
- 该月同步成功 + 查不到某人某天 ⇒ 日历标记 ``UNASSIGNED``（不计应填 + 告警）；
- 该月没同步过 ⇒ 回退旧的规则/默认推导（兼容历史数据）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now


class EmployeeShiftDay(Base):
    """员工某天的飞书排班事实。"""

    __tablename__ = "employee_shift_day"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_employee_shift_day"),
        Index("ix_employee_shift_day_date", "work_date"),
        Index("ix_employee_shift_day_employee_date", "employee_id", "work_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: 飞书班次 ID；``"0"`` 表示休息/未排班（实测值）。
    shift_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 是否上班：shift_id != "0"。
    is_workday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 飞书考勤组 ID。
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="feishu")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ShiftSyncRun(Base):
    """某个月的排班同步批次（决定「未排班」是否可信）。

    ⚠️ 只有 ``status == SUCCESS`` 的月份，查不到排班才会被判定为
    ``UNASSIGNED``；同步失败/部分成功不能拿来判定「未排班」。
    """

    __tablename__ = "shift_sync_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: ``YYYY-MM``
    period: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    #: RUNNING / SUCCESS / PARTIAL / FAILED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shift_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_employee_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
