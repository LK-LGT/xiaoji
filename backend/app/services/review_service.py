"""考核编排服务 —— 一次考核的业务闭环。

这里是「框架四条不可破原则」的代码落点：

原则二（证据链贯穿）：评分、评语都能回溯到 Evidence → 原始源 → 冻结快照口径
原则三（模型永远不产生数字）：compute_score 只用规则引擎；
                            AI 评级建议写进 RatingDraft，绝不写进 Score
原则四（可降级、不阻塞）：智能体不可用 → 用模板草稿，流程继续
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.enums import (
    AssessmentStatus,
    DraftStatus,
    EvidenceSource,
    EvidenceStatus,
    EvidenceUsage,
    RecordStatus,
    SubmitSource,
    Visibility,
)
from ..domain.models import (
    AssessmentRecord,
    AssessmentTask,
    AuditLog,
    CommentDraft,
    ConfigSnapshot,
    Employee,
    Evidence,
    MetricValue,
    RatingDraft,
    Score,
    SelfReview,
)
from . import config_service, evidence_service, scoring

logger = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


@dataclass
class DraftBundle:
    """一次评估的完整产物。"""

    score: scoring.ScoreResult
    ratings: list[dict[str, Any]]
    comment_markdown: str
    citations: list[str]
    gaps: list[dict[str, Any]]
    fairness: dict[str, Any]
    degraded: bool
    agent_status: dict[str, str]


# ---------------------------------------------------------------------------
# 智能体客户端 —— 业务层 → 智能体层
# ---------------------------------------------------------------------------


class AgentClient:
    """业务层调用智能体服务的 HTTP 客户端。

    智能体服务挂了 → 业务层自己降级出模板，流程不中断。
    """

    def __init__(self, base_url: str | None = None, secret: str | None = None) -> None:
        self.base_url = base_url or settings.agent_service_url
        self.secret = secret if secret is not None else settings.agent_shared_secret

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.secret:
            h["Authorization"] = f"Bearer {self.secret}"
        return h

    def draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        """调 POST /v1/agent/draft。失败抛异常，由调用方降级。"""
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url.rstrip('/')}/v1/agent/draft",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()


# ---------------------------------------------------------------------------
# 考核任务
# ---------------------------------------------------------------------------


def create_task(
    db: Session,
    *,
    name: str,
    period: str,
    department_id: str | None,
    position: str | None,
    owner_id: str | None = None,
) -> AssessmentTask:
    """建考核任务并冻结配置快照（决策③第二道保险）。"""
    task = AssessmentTask(
        name=name,
        period=period,
        department_id=department_id,
        position=position,
        owner_id=owner_id,
        status=AssessmentStatus.DRAFT.value,
        rating_policy=settings.rating_policy,
    )
    db.add(task)
    db.flush()

    provider = config_service.LocalProvider(db)
    config_service.freeze_snapshot(db, task, provider)
    task.status = AssessmentStatus.SELF_REVIEW_OPEN.value
    db.flush()
    logger.info("考核任务 %s 已创建，配置快照 %s 已冻结", task.id, task.snapshot_id)
    return task


def open_records(db: Session, task_id: str) -> list[AssessmentRecord]:
    """按任务范围生成员工考核记录。"""
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise NotFound(f"考核任务 {task_id} 不存在")

    q = select(Employee).where(Employee.employment_status == "active")
    if task.department_id:
        q = q.filter(Employee.department_id == task.department_id)
    if task.position:
        q = q.filter(Employee.position == task.position)
    employees = list(db.execute(q).scalars().all())

    existing = {
        r.employee_id: r
        for r in db.execute(
            select(AssessmentRecord).where(AssessmentRecord.task_id == task_id)
        ).scalars().all()
    }

    out: list[AssessmentRecord] = []
    for emp in employees:
        rec = existing.get(emp.id)
        if rec is None:
            rec = AssessmentRecord(
                task_id=task_id,
                employee_id=emp.id,
                status=RecordStatus.SELF_REVIEW_OPEN.value,
            )
            db.add(rec)
            db.flush()
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 自评提交
# ---------------------------------------------------------------------------


def submit_self_review(
    db: Session,
    *,
    record_id: str,
    answers: dict[str, Any],
    attachments: list[dict[str, Any]],
    source: SubmitSource,
    dedup_key: str | None = None,
) -> SelfReview:
    """提交自评。

    幂等：dedup_key 相同则不重复写（飞书事件回调可能重复投递）。
    """
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise NotFound(f"考核记录 {record_id} 不存在")

    if rec.submit_locked and rec.submit_source and rec.submit_source != source.value:
        raise InvalidState(
            f"本次考核已从「{rec.submit_source}」提交，另一入口只读。如需换入口请 HR 重置。"
        )

    if dedup_key:
        existing = db.execute(
            select(SelfReview).where(SelfReview.dedup_key == dedup_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    sr = SelfReview(
        record_id=record_id,
        answers_json=answers,
        attachments_json=attachments,
        submit_source=source.value,
        submitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
        dedup_key=dedup_key,
    )
    db.add(sr)
    db.flush()

    rec.submit_source = source.value
    rec.submit_locked = True
    rec.status = RecordStatus.SELF_REVIEW_SUBMITTED.value
    db.flush()

    # 自评内容 → 证据卡片（自评是 evidence 的主要来源之一）
    _create_evidence_from_self_review(db, record_id=record_id, answers=answers)

    _audit(db, action="self_review.submit", resource_type="assessment_record", resource_id=record_id)
    return sr


def _create_evidence_from_self_review(
    db: Session, *, record_id: str, answers: dict[str, Any]
) -> list[Evidence]:
    """把自评文本转成证据卡片。

    visibility = allow_for_review —— 员工授权这些内容可用于绩效评审
    （沿用 reviewops 的 consent gate）。私有草稿不参与。
    """
    out: list[Evidence] = []
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        return out

    for key, value in (answers or {}).items():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue

        ev = Evidence(
            employee_id=rec.employee_id,
            record_id=record_id,
            kind=EvidenceSource.FEISHU_APPROVAL.value,
            summary=text[:500],
            impact=None,
            source=EvidenceSource.FEISHU_APPROVAL.value,
            usage=EvidenceUsage.SCORING.value,
            visibility=Visibility.ALLOW_FOR_REVIEW.value,
            status=EvidenceStatus.AUTO_APPROVED.value,
        )
        db.add(ev)
        out.append(ev)

    db.flush()
    return out


# ---------------------------------------------------------------------------
# 评估 —— 核心
# ---------------------------------------------------------------------------


def _load_snapshot(db: Session, task: AssessmentTask) -> ConfigSnapshot:
    if not task.snapshot_id:
        raise InvalidState(f"任务 {task.id} 没有配置快照，无法计算")
    snap = db.get(ConfigSnapshot, task.snapshot_id)
    if snap is None:
        raise InvalidState(f"任务 {task.id} 的配置快照缺失")
    return snap


def _build_metric_context(
    db: Session,
    snapshot: ConfigSnapshot,
    employee_id: str,
    period: str,
) -> tuple[list[scoring.MetricSpec], list[scoring.MetricInput]]:
    """从冻结快照 + MetricValue 算分集组装规格与实测值。

    **只取 MetricValue 表里的数据** —— 考勤（reference_only）根本不在其中，
    所以考勤永远无法影响分数（决策②的物理保证）。
    """
    specs = scoring.build_specs(
        snapshot.metrics_json, snapshot.weights_json, snapshot.rubric_json
    )

    rows = db.execute(
        select(MetricValue).where(
            MetricValue.employee_id == employee_id, MetricValue.period == period
        )
    ).scalars().all()
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
    return specs, inputs


def compute_score(db: Session, *, record_id: str) -> scoring.ScoreResult:
    """算分 —— 唯一真值。**可复算**：关掉 AI 重跑，结果完全一致。"""
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise NotFound(f"考核记录 {record_id} 不存在")
    task = db.get(AssessmentTask, rec.task_id)
    if task is None:
        raise NotFound(f"考核任务 {rec.task_id} 不存在")

    snapshot = _load_snapshot(db, task)
    specs, inputs = _build_metric_context(db, snapshot, rec.employee_id, task.period)
    return scoring.score_record(specs, inputs)


def persist_score(db: Session, *, record_id: str, result: scoring.ScoreResult) -> Score:
    """落分。**这个函数不接受任何 AI 来源的数据。**"""
    existing = db.execute(
        select(Score).where(Score.record_id == record_id)
    ).scalar_one_or_none()
    if existing:
        existing.total = result.total
        existing.per_metric_json = result.per_metric
        existing.trace_json = result.trace
        db.flush()
        return existing

    s = Score(
        record_id=record_id,
        total=result.total,
        per_metric_json=result.per_metric,
        trace_json=result.trace,
    )
    db.add(s)
    db.flush()
    return s


def _collect_evidence(db: Session, employee_id: str) -> tuple[list[Evidence], list[Evidence]]:
    rows = evidence_service.list_scoring_evidence(db, employee_id)
    return evidence_service.split_by_usage(rows)


def _to_agent_evidence(rows: list[Evidence]) -> list[dict[str, Any]]:
    return [
        {
            "id": e.id,
            "kind": e.kind,
            "summary": e.summary,
            "impact": e.impact,
            "usage": e.usage,
            "visibility": e.visibility,
            "source": e.source,
            "as_of": e.as_of.isoformat() if e.as_of else None,
        }
        for e in rows
    ]


def build_draft_payload(db: Session, *, record_id: str) -> dict[str, Any]:
    """组装给智能体层的请求体。

    三处刻意的设计（对应《框架设计》第 6 节 C1）：
    1. metrics 传的是**已算好的数值和目标** —— 模型只做区间映射
    2. attendance_anomalies **单独一个字段** —— 不混进 evidence
    3. 整个 payload 里没有 score 字段
    """
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise NotFound(f"考核记录 {record_id} 不存在")
    task = db.get(AssessmentTask, rec.task_id)
    if task is None:
        raise NotFound(f"考核任务 {rec.task_id} 不存在")

    snapshot = _load_snapshot(db, task)
    specs, inputs = _build_metric_context(db, snapshot, rec.employee_id, task.period)

    # 先算一遍，拿到 ratio 与等级 —— 业务层算好的，模型只是复述
    result = scoring.score_record(specs, inputs)
    per_metric = result.per_metric

    scoring_ev, reference_ev = _collect_evidence(db, rec.employee_id)

    by_code: dict[str, list[str]] = {}
    for e in scoring_ev:
        for code in _extract_metric_codes(e, snapshot):
            by_code.setdefault(code, []).append(e.id)

    rubric_by_code = {r["metric_code"]: r for r in snapshot.rubric_json}

    metrics_payload = []
    for pm in per_metric:
        code = pm["metric_code"]
        rubric = rubric_by_code.get(code, {})
        metrics_payload.append(
            {
                "code": code,
                "dimension": pm["metric_name"],
                "value": pm["value"],
                "target": pm["target"],
                "unit": pm["unit"],
                "ratio": pm["ratio"],
                "ratio_level": pm["level"],
                "level_scale": rubric.get("levels_json") or rubric.get("levels") or [],
                "source": pm["source"],
                "as_of": pm["as_of"],
                "evidence_ids": by_code.get(code, []),
            }
        )

    emp = db.get(Employee, rec.employee_id)
    employee_payload = {
        "id": rec.employee_id,
        "name": emp.name if emp else "",
        "role_title": emp.position if emp else "",
        "dept": emp.department_id if emp else "",
    }

    return {
        "task_id": task.id,
        "employee": employee_payload,
        "period": task.period,
        "metrics": metrics_payload,
        "evidence": _to_agent_evidence(scoring_ev),
        # 决策②：考勤独立字段，模型无法把它当结论支撑
        "attendance_anomalies": evidence_service.build_attendance_view(reference_ev),
        "rubric": snapshot.rubric_json,
    }


def _extract_metric_codes(e: Evidence, snapshot: ConfigSnapshot) -> list[str]:
    """从证据里提取关联的指标编码。

    证据本身不强制带 metric_code —— 通过摘要文本里出现的指标名做匹配。
    匹配不到就不关联（宁可少关联，不要错关联）。
    """
    haystack = f"{e.summary} {e.impact or ''}".lower()
    hits: list[str] = []
    for m in snapshot.metrics_json:
        code = (m.get("code") or "").lower()
        name = (m.get("name") or "").lower()
        if code and code in haystack:
            hits.append(m["code"])
        elif name and name in haystack:
            hits.append(m["code"])
    return hits


def generate_draft(
    db: Session,
    *,
    record_id: str,
    agent_client: AgentClient | None = None,
) -> DraftBundle:
    """生成评级建议 + 评语草稿。

    降级：智能体服务不可用 → 本地模板出草稿，流程继续（原则四）。
    """
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise NotFound(f"考核记录 {record_id} 不存在")
    task = db.get(AssessmentTask, rec.task_id)
    if task is None:
        raise NotFound(f"考核任务 {rec.task_id} 不存在")

    payload = build_draft_payload(db, record_id=record_id)

    # 分数先算 —— 与 AI 无关
    snapshot = _load_snapshot(db, task)
    specs, inputs = _build_metric_context(db, snapshot, rec.employee_id, task.period)
    score_result = scoring.score_record(specs, inputs)

    client = agent_client or AgentClient()
    try:
        resp = client.draft(payload)
        degraded = bool(resp.get("degraded"))
        agent_status = resp.get("agent_status", {})
        ratings = resp.get("ratings", [])
        comment = resp.get("comment_draft", "")
        citations = resp.get("citations", [])
        gaps = resp.get("gaps", [])
        fairness = resp.get("fairness", {})
    except Exception as exc:
        logger.warning("智能体服务不可用，降级为模板草稿: %s", exc)
        degraded = True
        agent_status = {"rating": "degraded", "comment": "degraded"}
        ratings = []
        comment = _template_comment(payload, score_result)
        citations = []
        gaps = _template_gaps(payload)
        fairness = {
            "grounded": False,
            "warnings": [],
            "unsupported_claims": 0,
            "cited_evidence": [],
        }

    # ★ 关键：AI 结果写进 RatingDraft / CommentDraft，
    #   **绝不写进 Score**。Score 只由 compute_score 产生。
    _persist_drafts(
        db,
        record_id=record_id,
        ratings=ratings,
        comment=comment,
        citations=citations,
        gaps=gaps,
        fairness=fairness,
    )

    return DraftBundle(
        score=score_result,
        ratings=ratings,
        comment_markdown=comment,
        citations=citations,
        gaps=gaps,
        fairness=fairness,
        degraded=degraded,
        agent_status=agent_status,
    )


def _template_comment(payload: dict[str, Any], result: scoring.ScoreResult) -> str:
    """本地降级评语模板。"""
    lines = ["## 本月总结", ""]
    for pm in result.per_metric:
        lines.append(
            f"- **{pm['metric_name']}**：实际 {pm['value']} {pm['unit'] or ''}，"
            f"目标 {pm['target']}，达成率 {pm['ratio']}，等级 {pm['level']}"
        )
    anomalies = payload.get("attendance_anomalies") or []
    if anomalies:
        lines.append("")
        lines.append("> 考勤异常不自动扣分，仅作参考。")
    lines.append("")
    lines.append("> AI 总结暂不可用，以上为规则引擎计算的指标明细。")
    return "\n".join(lines)


def _template_gaps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "dimension": m.get("dimension", m.get("code")),
            "request": f"「{m.get('dimension', m.get('code'))}」缺少可引用的成果证据，请补充。",
        }
        for m in payload.get("metrics", [])
        if not m.get("evidence_ids")
    ]


def _persist_drafts(
    db: Session,
    *,
    record_id: str,
    ratings: list[dict[str, Any]],
    comment: str,
    citations: list[str],
    gaps: list[dict[str, Any]],
    fairness: dict[str, Any],
) -> None:
    """AI 草稿落库。**这是 AI 唯一的写入口。**"""
    for old in db.execute(
        select(RatingDraft).where(
            RatingDraft.record_id == record_id, RatingDraft.status == DraftStatus.DRAFT.value
        )
    ).scalars().all():
        old.status = DraftStatus.SUPERSEDED.value

    for old in db.execute(
        select(CommentDraft).where(
            CommentDraft.record_id == record_id, CommentDraft.status == DraftStatus.DRAFT.value
        )
    ).scalars().all():
        old.status = DraftStatus.SUPERSEDED.value

    db.add(
        RatingDraft(
            record_id=record_id,
            ratings_json=ratings,
            citations=citations,
            gaps_json=gaps,
            fairness_json=fairness,
            status=DraftStatus.DRAFT.value,
        )
    )
    db.add(
        CommentDraft(
            record_id=record_id,
            markdown=comment,
            citations=citations,
            status=DraftStatus.DRAFT.value,
        )
    )
    db.flush()


# ---------------------------------------------------------------------------
# 引用校验 —— 业务层的二次防线
# ---------------------------------------------------------------------------


def validate_citations(
    db: Session,
    *,
    record_id: str,
    evidence_ids: list[str],
) -> tuple[bool, list[str]]:
    """校验结论引用的证据 id 是否存在、是否属于该员工、可见性是否允许。

    这是「结论必须能对应到证据」的运行时保证（业务层二次校验，
    不依赖模型自觉）。
    """
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        return False, []

    valid: list[str] = []
    for eid in evidence_ids:
        ev = db.get(Evidence, eid)
        if ev is None:
            continue
        if ev.employee_id != rec.employee_id:
            continue
        if ev.visibility == Visibility.PRIVATE_DRAFT.value:
            continue
        valid.append(eid)

    return len(valid) == len(evidence_ids), valid


# ---------------------------------------------------------------------------
# 人工确认
# ---------------------------------------------------------------------------


def confirm_draft(
    db: Session,
    *,
    record_id: str,
    confirmed_by: str,
    confirmed_rating: str | None = None,
) -> dict[str, Any]:
    """人工确认 AI 草稿。

    这里做三件事：
    1. 草稿状态 → confirmed
    2. confirmed_rating 写进 AssessmentRecord（**人工确认的等级**）
    3. **重新算一遍分** —— 等级可能与 AI 建议不同，分数必须重算
    """
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise NotFound(f"考核记录 {record_id} 不存在")

    for d in db.execute(
        select(RatingDraft).where(
            RatingDraft.record_id == record_id, RatingDraft.status == DraftStatus.DRAFT.value
        )
    ).scalars().all():
        d.status = DraftStatus.CONFIRMED.value
        d.confirmed_by = confirmed_by
        d.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    for d in db.execute(
        select(CommentDraft).where(
            CommentDraft.record_id == record_id, CommentDraft.status == DraftStatus.DRAFT.value
        )
    ).scalars().all():
        d.status = DraftStatus.CONFIRMED.value
        d.confirmed_by = confirmed_by
        d.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    if confirmed_rating:
        rec.confirmed_rating = confirmed_rating

    # 分数重算 —— 始终由规则引擎产出
    result = compute_score(db, record_id=record_id)
    persist_score(db, record_id=record_id, result=result)

    _audit(
        db,
        actor_id=confirmed_by,
        action="draft.confirm",
        resource_type="assessment_record",
        resource_id=record_id,
    )
    return {"record_id": record_id, "score": result.total, "rating": rec.confirmed_rating}


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


def _audit(
    db: Session,
    *,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_id: str | None = None,
    result: str = "ok",
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata_json=metadata or {},
        )
    )
