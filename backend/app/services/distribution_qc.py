"""Distribution QC (P0-2) — rating distribution inspection, prompt only.

Boundary (non-negotiable):
- **Never writes Score / confirmed_rating.** This module only *reads* scores and
  produces statistical observations + risk hints.
- Observations are recorded into ``AiReview`` (RULE_ONLY) so they are auditable,
  but they never decide a grade and never auto-adjust anything.
- Following decision 7: forced distribution is **reference only** in phase one.
  This module compares distributions; it never forces a ratio.

Metrics per comparison group:
- group key = (department, position, frozen scheme version) — same rule set only.
- mean / median / stddev / min / max
- tightness (leniency): stddev relative to group mean
- outliers: |z| > threshold
- grade distribution (rating -> count / percentage)
"""
from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import (
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
    Employee,
    Score,
)

#: 最小样本量。低于此值不展示均值/标准差，避免小样本泄露个体信息
#: （与 ``qa_service.query_department_summary`` 的 k-匿名口径保持一致）。
MIN_GROUP_SIZE = 5

#: 异常分阈值：|z| 超过该值视为异常分标记
OUTLIER_Z_THRESHOLD = 2.0

#: 松紧度阈值：变异系数（stddev / mean）超过该值视为组内评分过松（区分度过低）
LENIENCY_CV_THRESHOLD = 0.15

#: 分布偏移阈值：实际占比与目标占比之差超过该值才提示
DISTRIBUTION_GAP_THRESHOLD = 0.15


def _group_key(db: Session, rec: AssessmentRecord, emp: Employee, task: AssessmentTask) -> tuple:
    """比较组键：同部门 + 同岗位 + 同冻结方案。

    Different positions or different rule versions are **not** comparable —
    mixing them produces meaningless numbers.
    """
    snap = db.get(ConfigSnapshot, task.snapshot_id) if task.snapshot_id else None
    scheme = (snap.form_template_json or {}).get("scheme_version_id") if snap else None
    return (
        emp.department_id or "",
        emp.position or "未设岗位",
        scheme or task.snapshot_id or task.id,
    )


def collect_scores(
    db: Session, *, period: str, department_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """读取周期内已算分的记录（只读）。"""
    stmt = (
        select(AssessmentRecord, Score, Employee, AssessmentTask)
        .join(Score, Score.record_id == AssessmentRecord.id)
        .join(Employee, Employee.id == AssessmentRecord.employee_id)
        .join(AssessmentTask, AssessmentTask.id == AssessmentRecord.task_id)
        .where(AssessmentTask.period == period)
    )
    if department_ids:
        stmt = stmt.where(Employee.department_id.in_(department_ids))
    rows = db.execute(stmt).all()
    out: list[dict[str, Any]] = []
    for rec, score, emp, task in rows:
        out.append(
            {
                "record_id": rec.id,
                "employee_id": emp.id,
                "employee_name": emp.name,
                "department_id": emp.department_id,
                "position": emp.position,
                "task_id": task.id,
                "total": float(score.total),
                "rating": (score.trace_json or {}).get("scheme_rating") or rec.confirmed_rating,
            }
        )
    return out


def _group_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    count = len(values)
    mean = statistics.mean(values)
    median = statistics.median(values)
    stddev = statistics.pstdev(values) if count > 1 else 0.0
    cv = (stddev / mean) if mean else 0.0
    return {
        "count": count,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "stddev": round(stddev, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "range": round(max(values) - min(values), 4),
        "cv": round(cv, 4),
        "small_sample": count < MIN_GROUP_SIZE,
    }


def analyze(
    db: Session,
    *,
    period: str,
    department_ids: list[str] | None = None,
    target_distribution: dict[str, float] | None = None,
) -> dict[str, Any]:
    """对周期内分数做分布质检。

    Args:
        period: 周期（紧凑格式，如 ``202611``）
        department_ids: 限定部门；None 表示全部
        target_distribution: 目标等级分布（如 ``{"A": 0.3, "B": 0.4, ...}``），
            仅用于**对比提示**，不执行强制分布（决策⑦第一期）。

    Returns:
        ``{"groups": [...], "alerts": [...], "records": [...], "limitations": [...]}``
    """
    records = collect_scores(db, period=period, department_ids=department_ids)
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for r in records:
        groups.setdefault(
            (r["department_id"], r["position"], r["task_id"]), []
        ).append(r)

    group_reports: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    for (dept, position, rule_group), members in groups.items():
        values = [m["total"] for m in members]
        stats = _group_stats(values)
        ratings: dict[str, int] = {}
        for m in members:
            key = m["rating"] or "未定级"
            ratings[key] = ratings.get(key, 0) + 1

        report: dict[str, Any] = {
            "department_id": dept,
            "position": position,
            "rule_group": rule_group,
            "rating_counts": ratings,
            **stats,
        }

        # 小样本：只展示人数，不展示均值/标准差（隐私）
        if stats.get("small_sample"):
            report["mean"] = None
            report["median"] = None
            report["stddev"] = None
            report["cv"] = None
            report["rating_distribution"] = {
                k: {"count": v, "percentage": None} for k, v in ratings.items()
            }
            alerts.append(
                {
                    "department_id": dept,
                    "position": position,
                    "rule_group": rule_group,
                    "type": "SMALL_SAMPLE",
                    "severity": "LOW",
                    "message": f"该组仅 {stats['count']} 人，不足 {MIN_GROUP_SIZE} 人不展示均值与离散度",
                }
            )
        else:
            total = sum(ratings.values())
            report["rating_distribution"] = {
                k: {"count": v, "percentage": round(v / total, 4)}
                for k, v in sorted(ratings.items())
            }

            # ── 松紧度：组内区分度过低 ──
            if stats["cv"] < LENIENCY_CV_THRESHOLD:
                alerts.append(
                    {
                        "department_id": dept,
                        "position": position,
                        "rule_group": rule_group,
                        "type": "TOO_LENIENT",
                        "severity": "MEDIUM",
                        "message": (
                            f"组内分数区分度偏低（变异系数 {stats['cv']}，阈值 {LENIENCY_CV_THRESHOLD}），"
                            "请确认评分标准是否真正区分了表现差异"
                        ),
                        "metrics": {"cv": stats["cv"], "stddev": stats["stddev"], "mean": stats["mean"]},
                    }
                )

            # ── 异常分：|z| > 阈值 ──
            stddev = stats["stddev"]
            outliers: list[dict[str, Any]] = []
            if stddev > 0:
                for m in members:
                    z = (m["total"] - stats["mean"]) / stddev
                    if abs(z) > OUTLIER_Z_THRESHOLD:
                        outliers.append(
                            {
                                "record_id": m["record_id"],
                                "employee_id": m["employee_id"],
                                "employee_name": m["employee_name"],
                                "total": m["total"],
                                "z": round(z, 4),
                                "direction": "HIGH" if z > 0 else "LOW",
                            }
                        )
            report["outliers"] = outliers
            for o in outliers:
                alerts.append(
                    {
                        "department_id": dept,
                        "position": position,
                        "rule_group": rule_group,
                        "type": "SCORE_OUTLIER",
                        "severity": "MEDIUM",
                        "message": (
                            f"{o['employee_name']} 分数 {o['total']}（z={o['z']}）偏离组内均值较大，"
                            "建议核查指标数据与评分依据"
                        ),
                        "metrics": o,
                    }
                )

            # ── 分布对比（决策⑦：只对比，不强制） ──
            if target_distribution:
                gaps: list[dict[str, Any]] = []
                for level, target_pct in target_distribution.items():
                    actual = report["rating_distribution"].get(level, {}).get("percentage", 0.0) or 0.0
                    gap = actual - target_pct
                    if abs(gap) > DISTRIBUTION_GAP_THRESHOLD:
                        gaps.append(
                            {
                                "level": level,
                                "target": target_pct,
                                "actual": round(actual, 4),
                                "gap": round(gap, 4),
                            }
                        )
                report["distribution_gaps"] = gaps
                if gaps:
                    alerts.append(
                        {
                            "department_id": dept,
                            "position": position,
                            "rule_group": rule_group,
                            "type": "DISTRIBUTION_GAP",
                            "severity": "LOW",
                            "message": "等级分布与目标分布存在差异（仅供参考，第一期不强制分布）",
                            "metrics": {"gaps": gaps},
                        }
                    )

        group_reports.append(report)

    return {
        "period": period,
        "groups": group_reports,
        "alerts": alerts,
        "records": records,
        "captured_at": None,
        "limitations": [
            f"不足 {MIN_GROUP_SIZE} 人的组不展示均值与离散度",
            "不同岗位、不同冻结方案版本不直接比较",
            "异常分与分布差异仅作为核查线索，不自动调整分数、不强制分布",
            "本模块只读分数，不写入 Score 与最终等级",
        ],
    }


def build_ai_review_payload(report: dict[str, Any]) -> dict[str, Any]:
    """把质检结果转成 ``AiReview.result_json`` 形状（仅建议）。"""
    alerts = report.get("alerts", [])
    high = [a for a in alerts if a.get("severity") in ("HIGH", "BLOCKING")]
    medium = [a for a in alerts if a.get("severity") == "MEDIUM"]
    overall = "HIGH" if high else ("MEDIUM" if medium else "LOW")
    summary = (
        f"分布质检发现 {len(alerts)} 项提示"
        + (f"，其中高风险 {len(high)} 项" if high else "")
        + "。"
        if alerts
        else "分布质检未发现异常。"
    )
    return {
        "overallRisk": overall,
        "summary": summary,
        "issues": [
            {
                "code": a["type"],
                "source": "RULE",
                "severity": a.get("severity", "LOW"),
                "field": "distribution",
                "title": a["type"],
                "ruleId": f"R-DIST-{a['type']}",
                "quote": "",
                "basis": "《绩效分布质检规范》：同岗位同规则组内统计一致性检查。",
                "suggestion": a.get("message", ""),
                "confidence": 0.7 if a.get("severity") != "LOW" else 0.5,
                "references": [],
            }
            for a in alerts
        ],
        "analysisDimensions": report.get("groups", []),
        "acceptanceCoverage": [],
        "suggestedCompletionMin": None,
        "suggestedCompletionMax": None,
        "evidenceStatus": "UNKNOWN",
        "declaredCompletionRate": None,
        "completionCalculationBasis": None,
    }
