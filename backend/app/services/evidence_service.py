"""证据服务 —— 决策②「考勤六类异常全部展示、均不自动扣分」的物理落点。

关键设计：
1. 考勤异常写进 Evidence 表，usage = reference_only
2. **永远不进 MetricValue 算分集** —— 唯一的入库出口是 add_metric_value，
   而它对考勤来源直接拒绝
3. 员工本人可见自己的异常记录（决策②配套约束）
4. 每条异常带 as_of（数据截至 X 日 X 时），因考勤有延迟和补卡回溯
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    AttendanceAnomalyType,
    EvidenceSource,
    EvidenceStatus,
    EvidenceUsage,
    Visibility,
)
from ..domain.models import Evidence, MetricValue

# 考勤类来源的集合 —— 进入此集合的来源一律不得进算分集
ATTENDANCE_SOURCES: frozenset[str] = frozenset({EvidenceSource.ATTENDANCE.value})

# 主管评分页固定文案（决策②配套约束）
ATTENDANCE_DISCLAIMER = "考勤异常不自动扣分，仅作参考"

# 加班标注（决策②配套约束）
OVERTIME_NOTE = "仅参考、不参与评分"


class AttendanceEvidenceRejected(Exception):
    """试图把考勤数据送进算分集 —— 直接拒绝。"""


@dataclass(frozen=True)
class AnomalyInput:
    """一条考勤异常。"""

    employee_id: str
    period: str
    anomaly_type: AttendanceAnomalyType
    count: int = 1
    as_of: datetime | None = None
    # 请假标注"已审批"；加班标注"仅参考、不参与评分"
    approved: bool | None = None
    detail: str | None = None


def _annotate(anom: AnomalyInput) -> tuple[str, str | None]:
    """生成摘要文本与补充说明，落地决策②的配套标注。"""
    t = anom.anomaly_type
    if t == AttendanceAnomalyType.LEAVE:
        tag = "已审批" if anom.approved else "待确认"
        return f"请假 {anom.count} 次（{tag}）", "请假记录仅供参考，不自动扣分"
    if t == AttendanceAnomalyType.OVERTIME:
        return f"加班 {anom.count} 次", OVERTIME_NOTE
    if t == AttendanceAnomalyType.LATE:
        return f"迟到 {anom.count} 次", "迟到异常仅供参考，不自动扣分"
    if t == AttendanceAnomalyType.EARLY_LEAVE:
        return f"早退 {anom.count} 次", "早退异常仅供参考，不自动扣分"
    if t == AttendanceAnomalyType.MISSING_PUNCH:
        return f"缺卡 {anom.count} 次", "缺卡异常仅供参考，不自动扣分"
    if t == AttendanceAnomalyType.OUTSIDE:
        return f"外勤 {anom.count} 次", "外勤记录仅供参考，不自动扣分"
    return f"{t.value} {anom.count} 次", None


def record_attendance_anomalies(
    db: Session, anomalies: list[AnomalyInput], *, record_id: str | None = None
) -> list[Evidence]:
    """写入考勤异常证据。全部标记 reference_only。"""
    out: list[Evidence] = []
    for anom in anomalies:
        summary, note = _annotate(anom)
        if anom.detail:
            summary = f"{summary}：{anom.detail}"
        if note:
            summary = f"{summary}（{note}）"

        ev = Evidence(
            employee_id=anom.employee_id,
            record_id=record_id,
            kind=EvidenceSource.ATTENDANCE.value,
            summary=summary,
            impact=note,
            source=EvidenceSource.ATTENDANCE.value,
            # source_ref 承载「周期 + 考勤来源 + 异常类型」的定位键，
            # 便于按 (员工, 周期, 类型) 幂等重建，避免跨员工误删。
            source_ref=(
                f"{anom.employee_id}|{anom.period}|attendance|{anom.anomaly_type.value}"
            ),
            as_of=anom.as_of,
            # 决策②：六类全部只作参考，不自动扣分
            usage=EvidenceUsage.REFERENCE_ONLY.value,
            # 员工本人可见自己的异常记录，避免"背后记录"的观感
            visibility=Visibility.SHARE_WITH_MANAGER.value,
            status=EvidenceStatus.APPROVED.value,
            anomaly_type=anom.anomaly_type.value,
            anomaly_count=anom.count,
            approved_flag=anom.approved,
        )
        db.add(ev)
        out.append(ev)
    db.flush()
    return out


def add_metric_value(
    db: Session,
    *,
    employee_id: str,
    metric_code: str,
    period: str,
    value: float,
    target: float | None = None,
    unit: str | None = None,
    source: str,
    source_ref: str | None = None,
    as_of: datetime | None = None,
) -> MetricValue:
    """写入指标实测值 —— **算分集的唯一入口**。

    考勤来源一律拒绝：考勤只作参考证据，永不参与算分（决策②）。
    """
    if source in ATTENDANCE_SOURCES:
        raise AttendanceEvidenceRejected(
            f"考勤数据（{source}）不得进入算分集，请使用 record_attendance_anomalies"
        )

    existing = db.execute(
        select(MetricValue).where(
            MetricValue.employee_id == employee_id,
            MetricValue.metric_code == metric_code,
            MetricValue.period == period,
        )
    ).scalar_one_or_none()

    if existing:
        existing.value = value
        existing.target = target
        existing.unit = unit
        existing.source = source
        existing.source_ref = source_ref
        existing.as_of = as_of
        db.flush()
        return existing

    mv = MetricValue(
        employee_id=employee_id,
        metric_code=metric_code,
        period=period,
        value=value,
        target=target,
        unit=unit,
        source=source,
        source_ref=source_ref,
        as_of=as_of,
    )
    db.add(mv)
    db.flush()
    return mv


def list_scoring_evidence(
    db: Session, employee_id: str, period: str | None = None
) -> list[Evidence]:
    """取可用于评语/评级的证据。

    规则：
    - 排除 usage = reference_only 的证据？不 —— 它们**要展示**给主管，
      但在返回里用 is_reference_only 标记，让调用方（主管评分页）加标注。
    - 排除 visibility = private_draft
    - 排除 status = rejected
    """
    q = select(Evidence).where(
        Evidence.employee_id == employee_id,
        Evidence.visibility != Visibility.PRIVATE_DRAFT.value,
        Evidence.status != EvidenceStatus.REJECTED.value,
    )
    rows = list(db.execute(q).scalars().all())
    return rows


def split_by_usage(rows: list[Evidence]) -> tuple[list[Evidence], list[Evidence]]:
    """拆成 (可作结论支撑, 仅参考)。

    调用方对第二组必须附加 ATTENDANCE_DISCLAIMER 标注。
    """
    scoring = [e for e in rows if e.usage == EvidenceUsage.SCORING.value]
    reference = [e for e in rows if e.usage == EvidenceUsage.REFERENCE_ONLY.value]
    return scoring, reference


def build_attendance_view(rows: list[Evidence]) -> list[dict[str, Any]]:
    """主管评分页的考勤视图：六类全展示 + 标注 + 数据截至时间。"""
    out: list[dict[str, Any]] = []
    for e in rows:
        if e.usage != EvidenceUsage.REFERENCE_ONLY.value:
            continue
        out.append(
            {
                "evidence_id": e.id,
                "type": e.anomaly_type,
                "count": e.anomaly_count,
                "summary": e.summary,
                "approved": e.approved_flag,
                "as_of": e.as_of.isoformat() if e.as_of else None,
                "disclaimer": ATTENDANCE_DISCLAIMER,
            }
        )
    return out
