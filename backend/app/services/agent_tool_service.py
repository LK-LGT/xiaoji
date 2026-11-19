"""只读业务工具：规则结果、限定任务的证据和部门聚合。"""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain import models
from ..domain.enums import EvidenceSource, EvidenceStatus, EvidenceUsage, Visibility
from . import evidence_service, privacy, qa_service, scoring
from .metric_context import InvalidState, build_metric_context, load_snapshot
from .period import period_match_keys


def load_metric_values(db: Session, *, employee_id: str, task: models.AssessmentTask) -> dict[str, Any]:
    try:
        snapshot = load_snapshot(db, task)
    except InvalidState:
        return {"metrics": [], "available": False, "note": "该任务暂无冻结配置"}
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


def load_evidence(
    db: Session, *, employee_id: str, task: models.AssessmentTask, usage: str | None,
    is_hr: bool = False,
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
    if not is_hr:
        stmt = stmt.where(models.Evidence.visibility != Visibility.HR_ONLY.value)
    if usage is not None:
        stmt = stmt.where(models.Evidence.usage == usage)
    rows = db.scalars(stmt.order_by(models.Evidence.id).limit(201)).all()
    out = []
    for row in rows[:200]:
        item = {"id": row.id, "kind": row.kind, "summary": row.summary, "impact": row.impact,
                "usage": row.usage, "source": row.source,
                "as_of": row.as_of.isoformat() if row.as_of else None}
        if row.usage == EvidenceUsage.REFERENCE_ONLY.value:
            item["disclaimer"] = evidence_service.ATTENDANCE_DISCLAIMER
        out.append(item)
    return privacy.redact_obj({"evidence": out, "truncated": len(rows) > 200})


def load_department_stats(db: Session, *, department_id: str, task_id: str) -> dict[str, Any]:
    data = qa_service.query_department_summary(department_id, task_id, db).model_dump()
    # k 必须覆盖实际贡献均分的人，不能以尚无成绩的成员凑足人数。
    if data["scored_employees"] < 5:
        data["average_score"] = None
        data["note"] = "实际有成绩人数 < 5，隐私保护中不显示平均分"
    return data
