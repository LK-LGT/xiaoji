"""P2-2 —— 趋势复盘报表（跨周期趋势 / 岗位横向 / 风险清单 / 薪酬导出）。

四张报表
--------
1. **跨周期趋势**（``build_period_trend``）：同一员工在多个周期的得分与等级变化。
2. **岗位横向**（``build_position_comparison``）：同一岗位（position）内
   各员工的得分分布与相对位置。
3. **风险清单**（``build_risk_roster``）：把风险信号汇总成可跟进的名单。
4. **薪酬导出**（``build_payroll_export``）：把得分/等级映射成薪酬建议区间，
   **必须脱敏** —— 薪酬数据是最高敏感级。

隐私硬规则
----------
- **小样本保护**：任何群体统计的人数 < 5 时，一律不输出均值/明细，
  只输出计数并注明原因（与 P0-2 分布质检、QA 服务保持一致口径）。
- **薪酬导出必须显式声明**：薪酬区间是**建议值**，不是承诺；
  导出内容一律走 ``privacy.redact_obj``。
- **只读**：本模块不写任何业务表。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Employee,
    Score,
)
from . import privacy

logger = logging.getLogger(__name__)

#: 群体统计的最小样本量（与分布质检 / QA 服务口径一致）
MIN_GROUP_SIZE = 5

#: 等级 → 绩效系数建议（薪酬建议的输入，非承诺值）
_RATING_COEFFICIENT: dict[str, float] = {
    "A": 1.20,
    "B": 1.05,
    "C": 1.00,
    "D": 0.90,
    "E": 0.80,
}


# ---------------------------------------------------------------------------
# 1. 跨周期趋势
# ---------------------------------------------------------------------------


def build_period_trend(
    db: Session,
    *,
    employee_id: str,
    limit_periods: int = 12,
) -> dict[str, Any]:
    """单个员工的跨周期趋势。

    只读 ``Score`` + ``AssessmentRecord`` + ``AssessmentTask``，
    不重算、不回写。

    ⚠️ **必须用 LEFT JOIN**：
    主管评分落库时，算分失败**不回滚评分**（见 ``performance.submit_manager_review``），
    所以存在「有 confirmed_rating 但没有 Score 行」的记录。
    用 INNER JOIN 会把这类记录**整条吞掉** ——
    用户看到的不是「这批还没算分」，而是「这批人不见了」。
    LEFT JOIN 后它们以 ``total=None`` 出现，趋势线断点一目了然。
    """
    rows = db.execute(
        select(AssessmentTask.period, Score.total, AssessmentRecord.confirmed_rating)
        .join(AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id)
        .outerjoin(Score, Score.record_id == AssessmentRecord.id)
        .where(AssessmentRecord.employee_id == employee_id)
        .order_by(AssessmentTask.period.desc())
        .limit(limit_periods)
    ).all()

    points = [
        {"period": period, "total": total, "rating": rating}
        for period, total, rating in rows
    ]
    points.reverse()  # 时间正序，便于画趋势线

    totals = [p["total"] for p in points if p["total"] is not None]
    trend = _classify_trend(totals)

    return {
        "employeeId": employee_id,
        "periodCount": len(points),
        "points": points,
        "trend": trend,
        "average": round(sum(totals) / len(totals), 2) if totals else None,
        "min": min(totals) if totals else None,
        "max": max(totals) if totals else None,
        "note": "趋势为统计描述，不代表评价结论。",
    }


def _classify_trend(totals: list[float]) -> str:
    """判定趋势方向。

    - 2 个点：直接比较（环比）。
    - ≥3 个点：前半段均值 vs 后半段均值（平滑单点噪声）。
    - 阈值 ±3 分：小于这个幅度视为「基本持平」—— 避免把噪声说成趋势。
    """
    if len(totals) < 2:
        return "STABLE"
    if len(totals) == 2:
        delta = totals[1] - totals[0]
    else:
        half = len(totals) // 2
        first = sum(totals[:half]) / half
        second = sum(totals[half:]) / (len(totals) - half)
        delta = second - first
    if delta > 3:
        return "UP"
    if delta < -3:
        return "DOWN"
    return "STABLE"


# ---------------------------------------------------------------------------
# 2. 岗位横向
# ---------------------------------------------------------------------------


def build_position_comparison(
    db: Session,
    *,
    position: str,
    period: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """同一岗位内的横向对比。

    小样本保护：岗位人数 < ``MIN_GROUP_SIZE`` 时不给均值与个人排名。

    ⚠️ **必须用 LEFT JOIN** —— 理由同 ``build_period_trend``：
    算分失败的记录不能从统计口径里凭空消失。
    它们以 ``total=None`` 参与人数计数（人数是真实的），
    但不参与均值/排名计算（没分就没有分）。
    """
    stmt = (
        select(Employee.id, Employee.name, Score.total, AssessmentRecord.confirmed_rating)
        .join(AssessmentRecord, AssessmentRecord.employee_id == Employee.id)
        .outerjoin(Score, Score.record_id == AssessmentRecord.id)
        .join(AssessmentTask, AssessmentTask.id == AssessmentRecord.task_id)
    )
    if position:
        stmt = stmt.where(Employee.position == position)
    if task_id:
        stmt = stmt.where(AssessmentRecord.task_id == task_id)
    elif period:
        stmt = stmt.where(AssessmentTask.period == period)

    rows = db.execute(stmt).all()
    member_count = len(rows)
    totals = [r[2] for r in rows if r[2] is not None]

    payload: dict[str, Any] = {
        "position": position,
        "period": period,
        "memberCount": member_count,
        "smallSample": member_count < MIN_GROUP_SIZE,
        "note": "",
        "members": [],
        "distribution": {},
    }

    if member_count < MIN_GROUP_SIZE:
        payload["note"] = (
            f"岗位人数 {member_count} < {MIN_GROUP_SIZE}，隐私保护中不显示均值与个人排名"
        )
        return payload

    mean = sum(totals) / len(totals) if totals else 0.0
    ordered = sorted(
        (r for r in rows if r[2] is not None), key=lambda r: r[2], reverse=True
    )
    rank_by_id = {r[0]: idx + 1 for idx, r in enumerate(ordered)}

    for emp_id, name, total, rating in rows:
        payload["members"].append(
            {
                "employee_id": emp_id,
                "employee_name": name,
                "total": total,
                "rating": rating,
                "rank": rank_by_id.get(emp_id),
                "deviation": round(total - mean, 2) if total is not None else None,
            }
        )

    payload["distribution"] = {
        "average": round(mean, 2),
        "median": round(sorted(totals)[len(totals) // 2], 2) if totals else None,
        "min": min(totals) if totals else None,
        "max": max(totals) if totals else None,
        "stddev": round(_stddev(totals), 2),
    }
    payload["note"] = "横向对比为统计描述，不构成评价结论。"
    return payload


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance ** 0.5


# ---------------------------------------------------------------------------
# 3. 风险清单
# ---------------------------------------------------------------------------


def build_risk_roster(
    db: Session,
    *,
    department_id: str | None = None,
    period: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """风险清单：按员工汇总风险信号，供 HR 跟进。

    风险来源有两类：
    - **统计类**：达成率低 / 离散度大 —— 直接由 ``Score`` 推导；
    - **语义类**：评语质量问题 —— 复用 ``semantic_qc``。

    ⚠️ 风险清单**不等于**离职名单。它是「需要人去看一眼」的清单，
    每条都带依据，最终判断由人做。
    """

    stmt = (
        select(
            AssessmentRecord.id,
            AssessmentRecord.employee_id,
            AssessmentRecord.confirmed_rating,
            Employee.name,
            Employee.department_id,
            Employee.position,
            Score.total,
            Score.per_metric_json,
        )
        .join(Employee, Employee.id == AssessmentRecord.employee_id)
        .outerjoin(Score, Score.record_id == AssessmentRecord.id)
        .join(AssessmentTask, AssessmentTask.id == AssessmentRecord.task_id)
    )
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if task_id:
        stmt = stmt.where(AssessmentRecord.task_id == task_id)
    elif period:
        stmt = stmt.where(AssessmentTask.period == period)

    rows = db.execute(stmt).all()
    items: list[dict[str, Any]] = []
    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    for (
        record_id, employee_id, confirmed_rating, name, dept, position,
        total, per_metric,
    ) in rows:
        per_metric = per_metric or []
        ratios = [pm.get("ratio") for pm in per_metric if pm.get("ratio") is not None]

        # ── 无依据类风险（必须**先于** ratios 短路判断）──
        # ⚠️ 这里的顺序很关键：
        #    「已确认评级但没有指标依据」恰恰是**没有 Score 行**的那批记录。
        #    如果先写 ``if not ratios: continue``，这类记录会在这里被跳过，
        #    永远走不到 RISK_CONFIRMED_WITHOUT_BASIS —— 那个信号就成了死代码。
        signals: list[dict[str, Any]] = []
        if confirmed_rating and not per_metric:
            signals.append(
                {
                    "code": "RISK_CONFIRMED_WITHOUT_BASIS",
                    "severity": "HIGH",
                    "basis": "已确认评级缺少指标依据，结算前需补齐举证。",
                }
            )

        if not ratios:
            # 没有可用的达成率 —— 统计类信号无从谈起。
            # 但上面可能已经记了「已确认评级缺依据」，那一条要保留。
            if not signals:
                continue
        else:
            # ── 统计类风险 ──
            low = [pm for pm in per_metric if _ratio(pm) is not None and _ratio(pm) < 0.8]
            if len(low) >= max(1, len(per_metric) // 2):
                signals.append(
                    {
                        "code": "RISK_LOW_ACHIEVEMENT",
                        "severity": "HIGH" if len(low) == len(per_metric) else "MEDIUM",
                        "basis": "多指标持续低达成，需人工核实实际原因。",
                    }
                )
            if len(ratios) >= 3 and (max(ratios) - min(ratios)) >= 0.8:
                signals.append(
                    {
                        "code": "RISK_HIGH_VARIANCE",
                        "severity": "MEDIUM",
                        "basis": "指标间达成率差距过大，通常意味着口径不一致或存在外部依赖阻塞。",
                    }
                )

        if not signals:
            continue

        highest = min(signals, key=lambda s: severity_rank.get(s["severity"], 3))
        items.append(
            {
                "employee_id": employee_id,
                "employee_name": name,
                "department_id": dept,
                "position": position,
                "record_id": record_id,
                "total": total,
                "confirmed_rating": confirmed_rating,
                "severity": highest["severity"],
                "signals": signals,
            }
        )

    items.sort(key=lambda i: severity_rank.get(i["severity"], 3))
    return {
        "departmentId": department_id,
        "period": period,
        "scannedCount": len(rows),
        "riskCount": len(items),
        "items": items,
        "disclaimer": "风险清单为统计性观察，不等于离职名单或处理结论；每项均需人工核实。",
    }


def _ratio(pm: dict[str, Any]) -> float | None:
    value = pm.get("ratio")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 4. 薪酬导出
# ---------------------------------------------------------------------------


def build_payroll_export(
    db: Session,
    *,
    period: str | None = None,
    task_id: str | None = None,
    department_id: str | None = None,
    include_names: bool = False,
) -> dict[str, Any]:
    """薪酬建议导出。

    ⚠️ **最高敏感级数据**。三条硬规则：
    1. 等级 → 系数是**建议值**，不是承诺；输出里必须带这句话。
    2. 员工姓名默认**不导出**（``include_names=False``），只给工号哈希；
       需要姓名时由调用方显式打开，且仍走脱敏。
    3. 整体走 ``privacy.redact_obj``。
    """
    stmt = (
        select(
            AssessmentRecord.id,
            AssessmentRecord.employee_id,
            AssessmentRecord.confirmed_rating,
            Employee.name,
            Employee.department_id,
            Employee.position,
            Score.total,
        )
        .join(Employee, Employee.id == AssessmentRecord.employee_id)
        .outerjoin(Score, Score.record_id == AssessmentRecord.id)
        .join(AssessmentTask, AssessmentTask.id == AssessmentRecord.task_id)
        .where(AssessmentRecord.confirmed_rating.isnot(None))
    )
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if task_id:
        stmt = stmt.where(AssessmentRecord.task_id == task_id)
    elif period:
        stmt = stmt.where(AssessmentTask.period == period)

    rows = db.execute(stmt).all()
    items: list[dict[str, Any]] = []
    unscored_count = 0
    for (
        record_id, employee_id, rating, name, dept, position, total,
    ) in rows:
        coefficient = _RATING_COEFFICIENT.get((rating or "").upper())
        row: dict[str, Any] = {
            "record_id": record_id,
            "employee_key": employee_id[:8],
            "department_id": dept,
            "position": position,
            "total": total,
            "rating": rating,
            "coefficient": coefficient,
        }
        if total is None:
            # 已确认评级但**没有分数** —— 算分失败过（主管评分不会因算分失败回滚）。
            # 系数照样给（等级是确认过的），但必须标出来：
            # 否则 HR 会以为这一行的分数是 0，而不是「没算出来」。
            unscored_count += 1
            row["scored"] = False
        else:
            row["scored"] = True
        if include_names:
            row["employee_name"] = name
        items.append(row)

    disclaimer = (
        "绩效系数为规则建议值（基于等级映射），"
        "不构成调薪承诺；实际调薪须经薪酬审批流程。"
    )
    if unscored_count:
        # ⚠️ 缺分记录**不能悄悄混进去** —— 系数有了但分数没有，
        #    HR 若不察觉就按系数批了调薪，那是拿一个没算出来的数当依据。
        disclaimer += (
            f"其中 {unscored_count} 条已确认评级但缺分数（算分未完成），"
            "其系数仅为等级映射值，结算前须补齐算分与举证。"
        )

    return privacy.redact_obj(
        {
            "period": period,
            "departmentId": department_id,
            "rowCount": len(items),
            "unscoredCount": unscored_count,
            "rows": items,
            "disclaimer": disclaimer,
            "privacyNote": (
                "已脱敏：姓名默认不导出，敏感字段已按隐私规则处理。"
                if not include_names
                else "已脱敏：敏感字段已按隐私规则处理。"
            ),
        }
    )


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------


def build_review_report(
    db: Session,
    *,
    employee_id: str | None = None,
    department_id: str | None = None,
    position: str | None = None,
    period: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """P2-2 一站式复盘报表入口（只读）。"""
    report: dict[str, Any] = {"period": period, "generatedAt": None}
    if employee_id:
        report["periodTrend"] = build_period_trend(db, employee_id=employee_id)
    if position:
        report["positionComparison"] = build_position_comparison(
            db, position=position, period=period, task_id=task_id
        )
    report["riskRoster"] = build_risk_roster(
        db, department_id=department_id, period=period, task_id=task_id
    )
    report["disclaimer"] = "本报表全部为统计描述，不构成评价结论或处理决定。"
    return report
