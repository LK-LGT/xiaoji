"""计划成果 → 绩效证据物化（A6 · 决策 D4 的 E2「转换物化」）。

背景
----
A4 交付了完整的成果提交闭环：员工提交月 / 周成果 → 上传附件 → 主管 / 部门
负责人审批通过。**数据是全的**，但成果**进不了绩效**——

算分链路只认 ``evidence`` 表（``evidence_service.list_scoring_evidence`` →
``review_service._collect_evidence`` → AI 评语 / 规则引擎），
而 ``Evidence`` 的写入点原本只有两处：

- ``evidence_service.record_attendance_anomalies``（``source=attendance``，
  ``usage=reference_only``，**永不进算分**）
- ``review_service`` 的飞书审批意见（``source=feishu_approval``）

**没有任何一处从 ``result`` / ``result_evidence`` 生成 ``Evidence``。**

为什么是「转换物化」而不是「直接写」（D4 的 E1/E2/E3 取舍）
--------------------------------------------------------------
1. ``Evidence`` 是**绩效域**的表。草稿成果、被驳回的成果都不该出现在绩效
   证据里 —— 混进去会污染语义。
2. 与方案 B 的经验一致：考勤同步写 Evidence 是**只写 reference_only**，
   计划证据应该只在**审批通过**那一刻物化。
3. 转换点唯一，便于追溯「这条证据从哪个成果来的」。

物化口径（2026-11-16 确认）
--------------------------
| 决策点 | 口径 |
|---|---|
| 物化时机 | **审批通过时**（``approve_result`` 成功那一刻） |
| 无附件 | **也要生成** —— 成果描述本身就是证据，不强制要求上传文件 |
| ``MetricValue`` | **不碰** —— 「计划执行率」是 A7 的正式指标，由规则引擎算，绕过它会破坏「算分唯一真值」 |
| 周期归属 | **D6：按周起始月**（月成果取 ``plan_month``，周成果取 ``week_start`` 所在月） |
| 幂等 | 靠 ``source_ref`` 定位键 + 服务层查重，重复审批不重复生成 |

幂等键格式
----------
``{employee_id}|{period}|plan_result|{plan_type}|{plan_id}|{plan_item_id}|{version_no}``

带上 ``version_no`` 是因为**驳回后重新提交会产生新版本** —— 每个版本对应
一次独立的审批，应各自生成一张证据卡片。带 ``plan_item_id`` 是因为同一条
计划下的多个条目是**不同的成果**。

跨周期的坑（方案 B 踩过）
------------------------
考勤曾出现「10 月考核混入 11 月异常」。成果这边同样要防：周成果跨月时
（11/30~12/6）按 **D6 决策**归属到 **11 月**（按周起始月），不按结束月。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    EvidenceSource,
    EvidenceStatus,
    EvidenceUsage,
    Visibility,
)
from ..domain.models import Evidence, MonthPlan, WeekPlan
from ..domain.result_models import (
    RESULT_PLAN_TYPE_MONTH,
    RESULT_PLAN_TYPE_WEEK,
    RESULT_STATUS_CONFIRMED,
    Result,
    ResultEvidence,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# 领域异常
# ===========================================================================


class MaterializationError(Exception):
    """成果物化失败。"""

    def __init__(self, message: str, *, code: str = "MATERIALIZATION_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# ===========================================================================
# 内部工具
# ===========================================================================


def _now() -> datetime:
    """当前 UTC 时间（naive）。"""
    return datetime.now().replace(tzinfo=None)


def resolve_period(db: Session, result: Result) -> str | None:
    """成果归属的考核周期（``YYYY-MM``）。

    - 月成果 → 所属计划的 ``plan_month``
    - 周成果 → 所属计划的 ``week_start`` 所在月（**D6 决策：按周起始月**）
    - 兜底 → 成果自身的 ``result_date`` 所在月

    跨月周（如 11/30~12/6）归属 **11 月**。
    """
    if result.plan_type == RESULT_PLAN_TYPE_MONTH:
        plan = db.get(MonthPlan, result.plan_id)
        if plan is not None and plan.plan_month:
            return plan.plan_month
    elif result.plan_type == RESULT_PLAN_TYPE_WEEK:
        plan = db.get(WeekPlan, result.plan_id)
        if plan is not None and plan.week_start is not None:
            return f"{plan.week_start.year:04d}-{plan.week_start.month:02d}"

    if result.result_date is not None:
        return f"{result.result_date.year:04d}-{result.result_date.month:02d}"
    return None


def build_source_ref(
    *,
    employee_id: str,
    period: str,
    plan_type: str,
    plan_id: str,
    plan_item_id: str | None,
    version_no: int,
) -> str:
    """构造幂等定位键。

    格式：``{employee_id}|{period}|plan_result|{plan_type}|{plan_id}|{plan_item_id}|{version_no}``
    """
    return "|".join(
        [
            str(employee_id),
            period,
            EvidenceSource.PLAN_RESULT.value,
            plan_type,
            str(plan_id),
            str(plan_item_id or ""),
            str(version_no),
        ]
    )


# ===========================================================================
# 摘要文本
# ===========================================================================


def _attachments_of(db: Session, result: Result) -> list[ResultEvidence]:
    """成果的附件列表（按创建时间排序）。"""
    stmt = (
        select(ResultEvidence)
        .where(ResultEvidence.result_id == result.id)
        .order_by(ResultEvidence.created_at)
    )
    return list(db.execute(stmt).scalars().all())


def _build_summary(
    result: Result,
    attachments: list[ResultEvidence],
) -> str:
    """生成证据摘要。

    摘要要能独立成立 —— 主管在评分页看到的是这一行字，不是成果详情页。
    """
    parts: list[str] = []

    title = (result.title or "").strip()
    if not title:
        # 没有标题时退回描述 / 交付物 / 计划条目名
        title = (
            (result.description or "").strip()
            or (result.deliverable or "").strip()
            or (result.content or "").strip()
        )
    if title:
        parts.append(title[:200])

    if result.completion_rate:
        parts.append(f"完成率 {result.completion_rate:g}%")

    if attachments:
        names = "、".join(a.file_name for a in attachments[:3])
        more = f" 等 {len(attachments)} 个附件" if len(attachments) > 3 else ""
        parts.append(f"附件：{names}{more}")

    if not parts:
        parts.append(f"{result.plan_type} 成果（无描述）")

    return "；".join(parts)


def _build_impact(result: Result, attachments: list[ResultEvidence]) -> str | None:
    """生成补充说明（成果对绩效的影响说明）。"""
    bits: list[str] = []

    if result.deliverable:
        bits.append(f"交付物：{result.deliverable.strip()[:200]}")

    if result.completion_status:
        bits.append(f"完成情况：{result.completion_status}")

    if result.personal_performance is not None:
        bits.append(f"个人绩效分：{result.personal_performance:g}")

    if result.approval_comment:
        bits.append(f"审批意见：{result.approval_comment.strip()[:200]}")

    if not bits:
        return None
    return "；".join(bits)


# ===========================================================================
# 物化
# ===========================================================================


def materialize_result(
    db: Session,
    result: Result,
    *,
    record_id: str | None = None,
) -> Evidence:
    """把一份已审批通过的成果物化成绩效证据卡片。

    **幂等**：同一 ``source_ref`` 已存在证据时直接返回，不重复创建。

    Args:
        db: 数据库会话。
        result: 成果（须已 ``confirmed``）。
        record_id: 关联的考核记录（可空 —— 审批时可能还没起考核）。

    Returns:
        创建或复用的 ``Evidence``。
    """
    if result.status != RESULT_STATUS_CONFIRMED:
        raise MaterializationError(
            f"只有已通过的成果才能物化（当前状态：{result.status}）",
            code="MATERIALIZATION_NOT_CONFIRMED",
        )

    period = resolve_period(db, result)
    if not period:
        raise MaterializationError(
            "无法解析成果归属周期，不能物化", code="MATERIALIZATION_NO_PERIOD"
        )

    source_ref = build_source_ref(
        employee_id=result.owner_id,
        period=period,
        plan_type=result.plan_type,
        plan_id=result.plan_id,
        plan_item_id=result.plan_item_id,
        version_no=result.version_no,
    )

    # ── 幂等：同一 source_ref 已存在则复用 ─────────────────────────────
    existing = db.execute(
        select(Evidence).where(Evidence.source_ref == source_ref)
    ).scalar_one_or_none()
    if existing is not None:
        logger.debug("成果 %s 的证据已存在，复用：%s", result.id, existing.id)
        return existing

    attachments = _attachments_of(db, result)
    summary = _build_summary(result, attachments)
    impact = _build_impact(result, attachments)

    evidence = Evidence(
        employee_id=result.owner_id,
        record_id=record_id,
        kind=EvidenceSource.PLAN_RESULT.value,
        summary=summary,
        impact=impact,
        source=EvidenceSource.PLAN_RESULT.value,
        source_ref=source_ref,
        as_of=result.approved_at or _now(),
        # 成果是真业绩 —— 进算分集（与考勤的 reference_only 相反）
        usage=EvidenceUsage.SCORING.value,
        visibility=Visibility.SHARE_WITH_MANAGER.value,
        # 已经过主管 / 部门负责人审批
        status=EvidenceStatus.APPROVED.value,
    )
    db.add(evidence)
    db.flush()
    logger.info(
        "成果 %s 已物化为证据 %s（周期 %s，附件 %d 个）",
        result.id,
        evidence.id,
        period,
        len(attachments),
    )
    return evidence


def materialize_for_result(
    db: Session,
    result_id: str,
    *,
    record_id: str | None = None,
) -> Evidence:
    """按 id 取成果并物化。"""
    result = db.get(Result, result_id)
    if result is None:
        raise MaterializationError("成果不存在", code="MATERIALIZATION_NO_RESULT")
    return materialize_result(db, result, record_id=record_id)


def find_evidence_by_result(db: Session, result: Result) -> Evidence | None:
    """查某成果已物化的证据（没有则 None）。

    与 ``materialize_result`` 的幂等键一致 —— 但这里按「同一成果的任一版本」
    查找，用于回溯。
    """
    period = resolve_period(db, result)
    if not period:
        return None
    prefix = f"{result.owner_id}|{period}|{EvidenceSource.PLAN_RESULT.value}|{result.plan_type}|{result.plan_id}|"
    stmt = select(Evidence).where(Evidence.source_ref.like(f"{prefix}%"))
    return db.execute(stmt).scalar_one_or_none()


def list_plan_result_evidence(
    db: Session,
    employee_id: str,
    period: str | None = None,
) -> list[Evidence]:
    """列出某员工的成果类证据（可按周期过滤）。

    周期过滤依赖 ``source_ref`` 的第 2 段（``{employee_id}|{period}|...``）。
    """
    stmt = select(Evidence).where(
        Evidence.employee_id == employee_id,
        Evidence.source == EvidenceSource.PLAN_RESULT.value,
    )
    rows = list(db.execute(stmt).scalars().all())
    if period:
        rows = [r for r in rows if _period_of_ref(r.source_ref) == period]
    return rows


def _period_of_ref(source_ref: str | None) -> str | None:
    """从 ``source_ref`` 解析周期段。"""
    if not source_ref:
        return None
    parts = source_ref.split("|")
    return parts[1] if len(parts) >= 2 else None


# ===========================================================================
# 序列化
# ===========================================================================


def serialize_evidence(evidence: Evidence) -> dict[str, Any]:
    """``Evidence` → dict（对齐前端绩效域证据卡片契约）。"""
    return {
        "id": evidence.id,
        "employeeId": evidence.employee_id,
        "recordId": evidence.record_id or "",
        "kind": evidence.kind,
        "summary": evidence.summary,
        "impact": evidence.impact or "",
        "source": evidence.source,
        "sourceRef": evidence.source_ref or "",
        "asOf": evidence.as_of.isoformat() if evidence.as_of else "",
        "usage": evidence.usage,
        "visibility": evidence.visibility,
        "status": evidence.status,
    }


def serialize_result_evidence(
    db: Session,
    result: Result,
) -> dict[str, Any] | None:
    """成果已物化的证据（未物化则 None）。"""
    evidence = find_evidence_by_result(db, result)
    if evidence is None:
        return None
    return serialize_evidence(evidence)
