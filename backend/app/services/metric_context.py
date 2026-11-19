"""评审与工具查询共用冻结配置和指标输入，避免换算口径分叉。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AssessmentTask, ConfigSnapshot, MetricValue
from . import scoring
from .period import period_match_keys


class InvalidState(Exception):
    pass


def load_snapshot(db: Session, task: AssessmentTask) -> ConfigSnapshot:
    if not task.snapshot_id:
        raise InvalidState(f"任务 {task.id} 没有配置快照，无法计算")
    snapshot = db.get(ConfigSnapshot, task.snapshot_id)
    if snapshot is None:
        raise InvalidState(f"任务 {task.id} 的配置快照缺失")
    return snapshot


def build_metric_context(
    db: Session,
    snapshot: ConfigSnapshot,
    employee_id: str,
    period: str,
) -> tuple[list[scoring.MetricSpec], list[scoring.MetricInput]]:
    """只读 MetricValue 算分集；兼容历史 YYYY-MM 周期。"""
    specs = scoring.build_specs(snapshot.metrics_json, snapshot.weights_json, snapshot.rubric_json)
    rows = (
        db.execute(
            select(MetricValue).where(
                MetricValue.employee_id == employee_id,
                MetricValue.period.in_(period_match_keys(period) or (str(period),)),
            )
        )
        .scalars()
        .all()
    )
    inputs = [
        scoring.MetricInput(
            code=mv.metric_code,
            value=mv.value,
            target=mv.target,
            unit=mv.unit,
            source=mv.source,
            as_of=mv.as_of.isoformat() if mv.as_of else None,
        )
        for mv in rows
    ]
    form = snapshot.form_template_json or {}
    if form.get("scheme_version_id"):
        # New HR schemes must never silently reweight missing or manually evaluated metrics.
        from ..domain.models import AssessmentRecord
        from ..domain.scheme_models import SchemeResponse

        record = db.scalar(
            select(AssessmentRecord).where(
                AssessmentRecord.task_id == snapshot.task_id,
                AssessmentRecord.employee_id == employee_id,
            )
        )
        response = db.get(SchemeResponse, record.id) if record else None
        by_code = {}  # Scheme tasks use only task-scoped, validated responses.
        if response:
            by_code = {
                code: scoring.MetricInput(
                    code=code, value=raw["actual"], target=raw["target"], source="scheme_response"
                )
                for code, raw in response.metric_values.items()
            }
        normalized = []
        for metric in snapshot.metrics_json:
            code = metric["code"]
            if metric.get("scoring_method") == "manager_review":
                review = response.reviews.get(code) if response else None
                if not review:
                    raise InvalidState(f"指标 {code} 尚未完成主管逐项评价")
                normalized.append(
                    scoring.MetricInput(
                        code=code,
                        value=float(review["score"]),
                        target=100.0,
                        source="scheme_manager_review",
                    )
                )
                continue
            value = by_code.get(code)
            if value is None or value.target is None or value.target <= 0:
                raise InvalidState(f"指标 {code} 缺少实际值或有效目标值，不能生成正式总分")
            if metric.get("direction") == "lower_is_better":
                if value.value <= 0:
                    raise InvalidState(f"指标 {code} 实际值必须大于0，零值需HR明确评分规则")
                value = scoring.MetricInput(
                    code=code,
                    value=value.target,
                    target=value.value,
                    unit=value.unit,
                    source=value.source,
                    as_of=value.as_of,
                )
            normalized.append(value)
        specs = [
            scoring.MetricSpec(
                code=s.code,
                name=s.name,
                weight=s.weight,
                unit=s.unit,
                levels=(
                    {
                        "level": "MANUAL",
                        "min_ratio": 0,
                        "max_ratio": None,
                        "score": float(response.reviews[s.code]["score"]),
                    },
                )
                if next(m for m in snapshot.metrics_json if m["code"] == s.code).get(
                    "scoring_method"
                )
                == "manager_review"
                else s.levels,
            )
            for s in specs
        ]
        inputs = normalized
    return specs, inputs
