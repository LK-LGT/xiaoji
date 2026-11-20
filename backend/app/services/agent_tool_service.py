"""只读业务工具：规则结果、限定任务的证据和部门聚合。"""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain import models
from ..domain.enums import EvidenceSource, EvidenceStatus, EvidenceUsage, Visibility
from . import data_source_service, evidence_service, privacy, qa_service, scoring
from .metric_context import InvalidState, build_metric_context, load_snapshot
from .period import period_match_keys


def load_metric_values(db: Session, *, employee_id: str, task: models.AssessmentTask) -> dict[str, Any]:
    try:
        snapshot = load_snapshot(db, task)
    except InvalidState:
        return {"metrics": [], "available": False, "note": "该任务暂无冻结配置"}
    # ⚠️ 方案任务：走 direct_points 口径，不套通用「达成率」公式。
    #   主管给的是该指标满分口径下的实得分（满分 = max_score），
    #   通用公式算出的 ratio 会把 13/15 显示成 0.13（13%），是**误导**。
    if (snapshot.form_template_json or {}).get("scheme_version_id"):
        return _scheme_metric_values(db, employee_id=employee_id, task=task, snapshot=snapshot)
    specs, inputs = build_metric_context(db, snapshot, employee_id, task.period)
    if not inputs:
        return {"metrics": [], "available": False, "note": "该周期暂无指标数据"}
    result = scoring.score_record(specs, inputs)
    fields = ("metric_code", "metric_name", "value", "target", "unit", "ratio", "level", "source", "as_of")
    rubric = {item["metric_code"]: item for item in snapshot.rubric_json}
    metrics = []
    for item in result.per_metric:
        entry = {key: item.get(key) for key in fields}
        definition = rubric.get(item["metric_code"], {})
        entry["level_scale"] = definition.get("levels_json") or definition.get("levels") or []
        metrics.append(entry)
    return {"available": True, "metrics": metrics, "warnings": result.warnings}


def _scheme_metric_values(
    db: Session,
    *,
    employee_id: str,
    task: models.AssessmentTask,
    snapshot: models.ConfigSnapshot,
) -> dict[str, Any]:
    """方案任务的指标值 —— 读落库真值，不重算。"""
    record = db.execute(
        select(models.AssessmentRecord).where(
            models.AssessmentRecord.task_id == task.id,
            models.AssessmentRecord.employee_id == employee_id,
        )
    ).scalars().first()
    if record is None:
        return {"metrics": [], "available": False, "note": "该任务下没有该员工的考核记录"}
    score = db.execute(
        select(models.Score).where(models.Score.record_id == record.id)
    ).scalars().first()
    if score is None:
        return {"metrics": [], "available": False, "note": "主管尚未完成评分，暂时无法给出指标值"}

    scheme_payload = (snapshot.form_template_json or {}).get("scheme_payload", {})
    max_scores = {
        m["code"]: m.get("max_score", m.get("weight")) for m in scheme_payload.get("metrics", [])
    }
    metrics = []
    for entry in score.per_metric_json or []:
        code = entry.get("metric_code")
        max_score = entry.get("max_score", max_scores.get(code))
        contribution = entry.get("contribution")
        metrics.append({
            "metric_code": code,
            "metric_name": entry.get("metric_name"),
            "value": contribution,
            "target": max_score,
            "unit": entry.get("unit"),
            # 方案路径是「直接点分」，达成率 = 得分/满分（口径正确）
            "ratio": (contribution / max_score) if max_score else None,
            "level": (score.trace_json or {}).get("scheme_rating"),
            "source": entry.get("source"),
            "as_of": entry.get("as_of"),
            "level_scale": [],
        })
    return {"available": True, "metrics": metrics, "warnings": []}


def load_scoring_breakdown(
    db: Session, *, employee_id: str, task: models.AssessmentTask,
) -> dict[str, Any]:
    """评分构成明细 —— 回答「这分数是怎么算出来的」。

    ⚠️ **为什么单独一个工具，而不是把 `get_metric_values` 加宽**
    ------------------------------------------------------
    1. **敏感度不同**：这里会返回权重、等级分、贡献分 ——
       这些是**内部算分配置**。`get_metric_values` 给的是
       「实测值 / 目标值 / 达成率」，是员工本就该看到的事实。
       把配置和事实混在一个工具里，等于让**所有**能问指标的人
       都自动拿到配置细节。
    2. **口径不同**：这里要回答的是「为什么是这个分」，
       需要给出**算式**（归一化权重 × 等级分 = 贡献分）。
    3. **契约稳定**：加宽现有工具会改变已有调用方拿到的结构。

    ⚠️ **只给「可解释的部分」，不给「内部机密」**
    ------------------------------------------
    返回：指标名、原始权重、归一化权重、实测值、目标值、达成率、
    等级、等级分、贡献分、算式说明。
    **不给**：rubric 完整区间表、等级分的完整映射表
    （``level_scores`` 是全局默认配置，给了等于泄露其他任务的评分标准）。

    ⚠️ **不参与算分的指标必须列出**
    ------------------------------
    这是本工具**最有价值**的部分：用户问「我为什么少了 10 分」，
    答案往往在「某指标因缺数据/缺目标值/rubric 配置问题而**未参与算分**，
    其余指标权重被**重新归一**」。

    ⚠️ **不给「最终确认分」**
    --------------------
    本工具给的是**规则算分结果**（按快照配置算出来的）。
    人工确认后的最终成绩（``AssessmentRecord.confirmed_rating``）
    是另一回事，由上层决定要不要一起给。这里**只给规则算分**，
    并在 ``note`` 里说清这一点 —— 避免 AI 把规则分说成「你的最终成绩」。
    """
    try:
        snapshot = load_snapshot(db, task)
    except InvalidState:
        return {
            "available": False,
            "items": [],
            "note": "该任务暂无冻结配置，无法给出评分构成",
        }

    # ⚠️ 方案任务（direct_points）：**不能用通用算分公式重算**。
    #
    #   方案路径下每个指标的满分 = 该指标的 max_score（等于它的权重），
    #   主管给的是「该指标口径下的实得分」（如 KPI_1 满分 15 分，给 13 分），
    #   **不是百分制**。而通用公式 ``归一化权重 × 等级分`` 会把它当百分制算：
    #     真实落库总分 84.0  →  通用公式算出 8.09（差了 10 倍）
    #   AI 读到 8.09 会直接告诉员工「你只有 8 分」—— **与真值完全不符**。
    #
    #   所以方案任务必须走**直接点分**口径：贡献分 = 主管给的分，
    #   总分 = Σ贡献分 + 加减分，与 ``review_service.compute_score`` 一致。
    if (snapshot.form_template_json or {}).get("scheme_version_id"):
        return _scheme_scoring_breakdown(db, employee_id=employee_id, task=task, snapshot=snapshot)

    specs, inputs = build_metric_context(db, snapshot, employee_id, task.period)
    if not inputs:
        return {
            "available": False,
            "items": [],
            "note": "该周期暂无指标数据，无法给出评分构成",
        }

    result = scoring.score_record(specs, inputs)
    rubric = {item["metric_code"]: item for item in snapshot.rubric_json}

    # ── 参与算分的指标：给出完整算式 ────────────────────────
    items: list[dict[str, Any]] = []
    for entry in result.per_metric:
        definition = rubric.get(entry["metric_code"], {})
        items.append({
            "metric_code": entry["metric_code"],
            "metric_name": entry["metric_name"],
            "raw_weight": entry["raw_weight"],
            "normalized_weight": entry["normalized_weight"],
            "value": entry["value"],
            "target": entry["target"],
            "unit": entry["unit"],
            "ratio": entry["ratio"],
            "level": entry["level"],
            "level_desc": entry["level_desc"],
            "level_score": entry["level_score"],
            "contribution": entry["contribution"],
            # 算式 —— 让 AI 能直接说「权重 30% × 等级分 80 = 贡献 24 分」
            "formula": (
                f"{entry['normalized_weight']} × {entry['level_score']} "
                f"= {entry['contribution']}"
            ),
            "level_scale": definition.get("levels_json") or definition.get("levels") or [],
            "source": entry["source"],
            "as_of": entry["as_of"],
        })

    # ── 未参与算分的指标：说明原因 ──────────────────────────
    # ⚠️ 这些指标**不扣分**，但权重被重新归一 ——
    #    不说清楚的话，用户会以为「缺数据 = 扣分」。
    excluded = _excluded_metrics(specs, inputs, result.warnings)

    trace = result.trace or {}
    return {
        "available": True,
        "items": items,
        "excluded_items": excluded,
        "total_score": result.total,
        "rule": trace.get("rule"),
        "spec_count": trace.get("spec_count"),
        "participating_count": trace.get("participating_count"),
        "total_weight": trace.get("total_weight"),
        "participating_weight": trace.get("participating_weight"),
        "warnings": result.warnings,
        "note": (
            "以上为按冻结配置重算的规则得分，"
            "指标等级不等于已确认的最终成绩；"
            "未提供数据的指标按规则不参与算分，其余指标权重已重新归一。"
        ),
    }


def _scheme_scoring_breakdown(
    db: Session,
    *,
    employee_id: str,
    task: models.AssessmentTask,
    snapshot: models.ConfigSnapshot,
) -> dict[str, Any]:
    """方案任务（direct_points）的评分构成 —— 口径与落库分数**完全一致**。

    ⚠️ 这里**不重算**，而是读 ``score`` 表的**唯一真值**：
    方案路径的贡献分 = 主管逐项给的分（满分 = 该指标 max_score），
    重算会引入与 ``review_service.compute_score`` 不一致的口径风险
    （实测通用公式能把 84 分算成 8.09 分）。

    没有落库分数时（如主管还没评完）返回不可用，而不是给一个假分数。
    """
    record = db.execute(
        select(models.AssessmentRecord).where(
            models.AssessmentRecord.task_id == task.id,
            models.AssessmentRecord.employee_id == employee_id,
        )
    ).scalars().first()
    if record is None:
        return {
            "available": False,
            "items": [],
            "note": "该任务下没有该员工的考核记录",
        }

    score = db.execute(
        select(models.Score).where(models.Score.record_id == record.id)
    ).scalars().first()
    if score is None:
        return {
            "available": False,
            "items": [],
            "note": "主管尚未完成评分，暂时无法给出评分构成",
        }

    trace = score.trace_json or {}
    scheme_payload = (snapshot.form_template_json or {}).get("scheme_payload", {})
    max_scores = {
        m["code"]: m.get("max_score", m.get("weight"))
        for m in scheme_payload.get("metrics", [])
    }

    items: list[dict[str, Any]] = []
    for entry in score.per_metric_json or []:
        code = entry.get("metric_code")
        contribution = entry.get("contribution")
        max_score = entry.get("max_score", max_scores.get(code))
        items.append({
            "metric_code": code,
            "metric_name": entry.get("metric_name"),
            "raw_weight": max_score,
            "normalized_weight": None,
            "value": contribution,
            "target": max_score,
            "unit": entry.get("unit"),
            "ratio": None,
            "level": trace.get("scheme_rating"),
            "level_desc": entry.get("level_desc"),
            "level_score": contribution,
            "contribution": contribution,
            # 算式：方案路径是「直接点分」，不是「权重 × 等级分」
            "formula": f"主管评分 {contribution} / {max_score}",
            "level_scale": [],
            "source": entry.get("source"),
            "as_of": entry.get("as_of"),
        })

    return {
        "available": True,
        "items": items,
        "excluded_items": [],
        "total_score": score.total,
        "rule": trace.get("rule"),
        "adjustment": trace.get("adjustment"),
        "scheme_rating": trace.get("scheme_rating"),
        "grade_basis": trace.get("grade_basis"),
        "spec_count": len(items),
        "participating_count": len(items),
        "total_weight": trace.get("total_weight"),
        "participating_weight": trace.get("participating_weight"),
        "warnings": [],
        "note": (
            "以上为按方案规则算出的得分（每项满分即该指标权重），"
            "等级为方案原文等级；指标等级不等于已确认的最终成绩。"
        ),
    }


def _excluded_metrics(
    specs: list[scoring.MetricSpec],
    inputs: list[scoring.MetricInput],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """列出**未参与算分**的指标及原因。

    ⚠️ 原因从 ``score_record`` 的 warnings 里解析 —— 不重新实现一遍判定逻辑。
    重复实现会出现「两处判定不一致」，而这里的一致性是**可追溯性**的基础：
    warnings 是算分时真实产出的，这里只是把它结构化。

    Returns:
        ``[{"metric_code", "metric_name", "raw_weight", "reason"}]``
    """
    # ⚠️ 参与项的判定**不能猜** —— 用 score_record 实际产出的 warnings 反推：
    #    warnings 里每条都以「指标 <code> 」开头，没出现在 warnings 里的才是参与项。
    included_codes: set[str] = set()
    for spec in specs:
        if any(w.startswith(f"指标 {spec.code} ") for w in warnings):
            continue
        included_codes.add(spec.code)

    by_code = {i.code for i in inputs}
    out: list[dict[str, Any]] = []
    for spec in specs:
        if spec.code in included_codes:
            continue
        reason = next(
            (w for w in warnings if w.startswith(f"指标 {spec.code} ")),
            "缺少可用数据",
        )
        # 去掉「指标 CODE 」前缀，保留原因本身
        detail = reason[len(f"指标 {spec.code} "):]
        out.append({
            "metric_code": spec.code,
            "metric_name": spec.name,
            "raw_weight": spec.weight,
            "has_value": spec.code in by_code,
            "reason": detail,
        })
    return out


def load_evidence(
    db: Session, *, employee_id: str, task: models.AssessmentTask, usage: str | None,
    roles: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    record_ids = select(models.AssessmentRecord.id).where(
        models.AssessmentRecord.employee_id == employee_id,
        models.AssessmentRecord.task_id == task.id,
    )
    # 无 record_id 的材料仅允许有明确周期来源键的考勤；不猜测其他历史材料的周期。
    escaped_id = employee_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    attendance = [models.Evidence.source_ref.like(
        f"{escaped_id}|{key}|attendance|%", escape="\\"
    ) for key in period_match_keys(task.period)]
    stmt = select(models.Evidence).where(
        models.Evidence.employee_id == employee_id,
        models.Evidence.status.in_([EvidenceStatus.APPROVED.value, EvidenceStatus.AUTO_APPROVED.value]),
        models.Evidence.visibility != Visibility.PRIVATE_DRAFT.value,
        or_(
            (models.Evidence.record_id.in_(record_ids)) &
            (models.Evidence.source != EvidenceSource.ATTENDANCE.value),
            or_(models.Evidence.record_id.is_(None), models.Evidence.record_id.in_(record_ids)) &
            (models.Evidence.source == EvidenceSource.ATTENDANCE.value) & or_(*attendance),
        ),
    )
    role_set = _normalize_roles(roles)
    if "hr" not in role_set:
        stmt = stmt.where(models.Evidence.visibility != Visibility.HR_ONLY.value)
    if usage is not None:
        stmt = stmt.where(models.Evidence.usage == usage)
    rows = db.scalars(stmt.order_by(models.Evidence.id).limit(201)).all()
    out = []
    for row in rows[:200]:
        # P1-5 数据源白名单：不可读的源直接不返回（默认不信任）。
        if not data_source_service.is_readable(db, row.source, role_set):
            continue
        item = {"id": row.id, "kind": row.kind, "summary": row.summary, "impact": row.impact,
                "usage": row.usage, "source": row.source,
                "as_of": row.as_of.isoformat() if row.as_of else None}
        if row.usage == EvidenceUsage.REFERENCE_ONLY.value:
            item["disclaimer"] = evidence_service.ATTENDANCE_DISCLAIMER
        # P1-5：按源策略脱敏声明字段
        item = data_source_service.apply_redaction(db, row.source, item)
        out.append(item)
    return privacy.redact_obj({"evidence": out, "truncated": len(rows) > 200})


def _normalize_roles(roles: frozenset[str] | set[str] | None) -> frozenset[str]:
    """归一化角色集合。

    传入 ``None`` 表示「调用方未提供角色信息」，此时退化为最严格情形
    （只有 HR 可读）。这是**默认不信任**的一致体现：宁可少给数据，
    也不要在角色不明时按最宽口径放行。
    """
    if not roles:
        return frozenset({"hr"})
    return frozenset(str(r).strip().lower() for r in roles if str(r).strip())


def load_department_stats(db: Session, *, department_id: str, task_id: str) -> dict[str, Any]:
    data = qa_service.query_department_summary(department_id, task_id, db).model_dump()
    # k 必须覆盖实际贡献均分的人，不能以尚无成绩的成员凑足人数。
    if data["scored_employees"] < 5:
        data["average_score"] = None
        data["note"] = "实际有成绩人数 < 5，隐私保护中不显示平均分"
    return data
