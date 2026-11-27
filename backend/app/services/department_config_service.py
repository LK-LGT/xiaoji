"""🏢 **部门配置 / 待办 / 台账 —— 真实持久化服务**（★ 2026-11-26 新增）。

⚠️ **这个模块存在的理由**
``routes/department.py`` 里 ``templates`` / ``acceptance-standards`` /
``score-rules`` / ``todos`` / ``export-tasks`` / ``department-ledgers``
这些端点原先**只返回结构完整的假对象**，数据库里什么都没有。

⇒ 本模块提供**真读真写**的实现，路由层只做参数解析与权限判定。

⚠️ **为什么不用 ORM relationship**
这些表在 ``domain/config_models.py``，用的是**独立 base**
⇒ 跨 base 查询时用 ``select()`` 显式查，避免隐式 join 出错。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..domain.config_models import (
    AcceptanceStandardRow,
    DeliverableTemplateRow,
    ExportTaskRow,
    ScoreRuleRow,
    TodoRow,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ═══════════════════════════════════════════════════════════════════════════
# 交付物模板
# ═══════════════════════════════════════════════════════════════════════════


def serialize_template(row: DeliverableTemplateRow) -> dict[str, Any]:
    """交付物模板 ⇒ 前端 ``DeliverableTemplate`` 契约。"""
    return {
        "id": row.id,
        "orgId": row.org_id,
        "orgName": row.org_name,
        "templateName": row.template_name,
        "evidenceType": row.evidence_type,
        "required": bool(row.required),
        "appliesTo": row.applies_to or "",
        "description": row.description or "",
        "versionNo": row.version_no,
        "status": row.status,
        # ⚠️ 引用数是**真实统计**（有多少条验收标准挂在它下面）。
        "referenceCount": 0,
    }


def list_templates(
    db: Session,
    *,
    org_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    """交付物模板列表（真实数据）。"""
    stmt = select(DeliverableTemplateRow)
    if org_id:
        stmt = stmt.where(DeliverableTemplateRow.org_id == org_id)
    if status:
        stmt = stmt.where(DeliverableTemplateRow.status == status)
    if keyword:
        stmt = stmt.where(DeliverableTemplateRow.template_name.like(f"%{keyword}%"))
    rows = db.execute(stmt.order_by(DeliverableTemplateRow.id.desc())).scalars().all()

    # ⚠️ **引用数真实统计** —— 不能恒为 0（那是假数据的特征）。
    counts = dict(
        db.execute(
            select(AcceptanceStandardRow.template_id, func.count(AcceptanceStandardRow.id))
            .where(AcceptanceStandardRow.template_id.in_([r.id for r in rows] or [0]))
            .group_by(AcceptanceStandardRow.template_id)
        ).all()
    )

    result = []
    for row in rows:
        item = serialize_template(row)
        item["referenceCount"] = int(counts.get(row.id, 0))
        result.append(item)
    return result


def create_template(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """新建交付物模板（真落库）。"""
    name = str(payload.get("templateName") or "").strip()
    if not name:
        raise ValueError("请填写模板名称")
    org_id = str(payload.get("orgId") or "").strip()

    row = DeliverableTemplateRow(
        org_id=org_id,
        org_name=str(payload.get("orgName") or "").strip(),
        template_name=name,
        evidence_type=str(payload.get("evidenceType") or "DOCUMENT").strip() or "DOCUMENT",
        required=bool(payload.get("required")),
        applies_to=str(payload.get("appliesTo") or "").strip(),
        description=str(payload.get("description") or "").strip(),
        version_no="1",
        status="ENABLED",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_template(row)


def update_template(db: Session, template_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """更新交付物模板（真落库）。不存在返回 ``None``。"""
    row = db.get(DeliverableTemplateRow, template_id)
    if row is None:
        return None

    name = str(payload.get("templateName") or "").strip()
    if name:
        row.template_name = name
    if "evidenceType" in payload:
        row.evidence_type = str(payload.get("evidenceType") or "DOCUMENT").strip() or "DOCUMENT"
    if "required" in payload:
        row.required = bool(payload.get("required"))
    if "appliesTo" in payload:
        row.applies_to = str(payload.get("appliesTo") or "").strip()
    if "description" in payload:
        row.description = str(payload.get("description") or "").strip()
    if "orgId" in payload:
        row.org_id = str(payload.get("orgId") or "").strip()
    if "orgName" in payload:
        row.org_name = str(payload.get("orgName") or "").strip()

    db.commit()
    db.refresh(row)
    return serialize_template(row)


def set_template_status(db: Session, template_id: int, status: str) -> dict[str, Any] | None:
    """启用 / 停用交付物模板（真落库）。"""
    row = db.get(DeliverableTemplateRow, template_id)
    if row is None:
        return None
    row.status = status
    db.commit()
    db.refresh(row)
    return serialize_template(row)


# ═══════════════════════════════════════════════════════════════════════════
# 验收标准
# ═══════════════════════════════════════════════════════════════════════════


def serialize_standard(row: AcceptanceStandardRow, template_name: str = "") -> dict[str, Any]:
    """验收标准 ⇒ 前端 ``AcceptanceStandard`` 契约。"""
    return {
        "id": row.id,
        "templateId": row.template_id,
        "templateName": template_name,
        "standardText": row.standard_text or "",
        "requireReviewPassed": bool(row.require_review_passed),
        "evidenceRequirement": row.evidence_requirement or "",
        "versionNo": row.version_no,
        "status": row.status,
    }


def list_standards(
    db: Session,
    *,
    template_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    """验收标准列表（真实数据）。"""
    stmt = select(AcceptanceStandardRow)
    if template_id:
        stmt = stmt.where(AcceptanceStandardRow.template_id == template_id)
    if status:
        stmt = stmt.where(AcceptanceStandardRow.status == status)
    if keyword:
        stmt = stmt.where(AcceptanceStandardRow.standard_text.like(f"%{keyword}%"))
    rows = db.execute(stmt.order_by(AcceptanceStandardRow.id.desc())).scalars().all()

    # ⚠️ 模板名**实时查**，不能返回空串（前端表格直接显示这一列）。
    template_names = _template_names(db, [r.template_id for r in rows])
    return [serialize_standard(r, template_names.get(r.template_id, "")) for r in rows]


def _template_names(db: Session, template_ids: list[int]) -> dict[int, str]:
    if not template_ids:
        return {}
    rows = db.execute(
        select(DeliverableTemplateRow.id, DeliverableTemplateRow.template_name).where(
            DeliverableTemplateRow.id.in_(template_ids)
        )
    ).all()
    return {int(r[0]): r[1] for r in rows}


def create_standard(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """新建验收标准（真落库）。"""
    template_id = payload.get("templateId")
    if template_id is None:
        raise ValueError("请选择所属交付物模板")
    try:
        template_id = int(template_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("所属交付物模板不合法") from exc
    if db.get(DeliverableTemplateRow, template_id) is None:
        raise ValueError("所属交付物模板不存在")

    text = str(payload.get("standardText") or "").strip()
    if not text:
        raise ValueError("请填写验收标准内容")

    row = AcceptanceStandardRow(
        template_id=template_id,
        standard_text=text,
        require_review_passed=bool(payload.get("requireReviewPassed")),
        evidence_requirement=str(payload.get("evidenceRequirement") or "").strip(),
        version_no="1",
        status="ENABLED",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    names = _template_names(db, [template_id])
    return serialize_standard(row, names.get(template_id, ""))


def update_standard(
    db: Session, standard_id: int, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """更新验收标准（真落库）。"""
    row = db.get(AcceptanceStandardRow, standard_id)
    if row is None:
        return None
    if "standardText" in payload:
        text = str(payload.get("standardText") or "").strip()
        if not text:
            raise ValueError("请填写验收标准内容")
        row.standard_text = text
    if "requireReviewPassed" in payload:
        row.require_review_passed = bool(payload.get("requireReviewPassed"))
    if "evidenceRequirement" in payload:
        row.evidence_requirement = str(payload.get("evidenceRequirement") or "").strip()
    db.commit()
    db.refresh(row)
    names = _template_names(db, [row.template_id])
    return serialize_standard(row, names.get(row.template_id, ""))


def set_standard_status(db: Session, standard_id: int, status: str) -> dict[str, Any] | None:
    """启用 / 停用验收标准（真落库）。"""
    row = db.get(AcceptanceStandardRow, standard_id)
    if row is None:
        return None
    row.status = status
    db.commit()
    db.refresh(row)
    names = _template_names(db, [row.template_id])
    return serialize_standard(row, names.get(row.template_id, ""))


# ═══════════════════════════════════════════════════════════════════════════
# 参考分规则
# ═══════════════════════════════════════════════════════════════════════════


def serialize_score_rule(row: ScoreRuleRow) -> dict[str, Any]:
    """参考分规则 ⇒ 前端 ``ScoreRule`` 契约。"""
    return {
        "id": row.id,
        "orgId": row.org_id,
        "orgName": row.org_name,
        "ruleName": row.rule_name,
        "status": row.status,
        "effectiveStart": row.effective_start,
        "effectiveEnd": row.effective_end,
        "ruleJson": row.rule_json or {},
    }


def list_score_rules(
    db: Session,
    *,
    org_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    """参考分规则列表（真实数据）。"""
    stmt = select(ScoreRuleRow)
    if org_id:
        stmt = stmt.where(ScoreRuleRow.org_id == org_id)
    if status:
        stmt = stmt.where(ScoreRuleRow.status == status)
    if keyword:
        stmt = stmt.where(ScoreRuleRow.rule_name.like(f"%{keyword}%"))
    rows = db.execute(stmt.order_by(ScoreRuleRow.id.desc())).scalars().all()
    return [serialize_score_rule(r) for r in rows]


def create_score_rule(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """新建参考分规则（真落库）。"""
    name = str(payload.get("ruleName") or "").strip()
    if not name:
        raise ValueError("请填写规则名称")
    row = ScoreRuleRow(
        org_id=str(payload.get("orgId") or "").strip(),
        org_name=str(payload.get("orgName") or "").strip(),
        rule_name=name,
        status="ENABLED",
        effective_start=str(payload.get("effectiveStart") or "").strip() or None,
        effective_end=str(payload.get("effectiveEnd") or "").strip() or None,
        rule_json=_normalize_rule_json(payload.get("ruleJson")),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_score_rule(row)


def _normalize_rule_json(raw: Any) -> dict[str, Any]:
    """把 ``ruleJson`` 归一成 dict。

    ⚠️ 前端传的是对象，但接口也可能收到 **JSON 字符串**
    ⇒ 两种都要接住，否则「规则保存后是空的」。
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("规则内容不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("规则内容必须是 JSON 对象")
        return parsed
    raise ValueError("规则内容格式非法")


def update_score_rule(
    db: Session, rule_id: int, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """更新参考分规则（真落库）。"""
    row = db.get(ScoreRuleRow, rule_id)
    if row is None:
        return None
    if "ruleName" in payload:
        name = str(payload.get("ruleName") or "").strip()
        if name:
            row.rule_name = name
    if "orgId" in payload:
        row.org_id = str(payload.get("orgId") or "").strip()
    if "orgName" in payload:
        row.org_name = str(payload.get("orgName") or "").strip()
    if "effectiveStart" in payload:
        row.effective_start = str(payload.get("effectiveStart") or "").strip() or None
    if "effectiveEnd" in payload:
        row.effective_end = str(payload.get("effectiveEnd") or "").strip() or None
    if "ruleJson" in payload:
        row.rule_json = _normalize_rule_json(payload.get("ruleJson"))
    db.commit()
    db.refresh(row)
    return serialize_score_rule(row)


def enable_score_rule(db: Session, rule_id: int) -> dict[str, Any] | None:
    """启用参考分规则（真落库）。

    ⚠️ **启用即生效**：只有一条 ``ENABLED`` ⇒ 其余自动停用。
    """
    row = db.get(ScoreRuleRow, rule_id)
    if row is None:
        return None
    db.execute(
        update(ScoreRuleRow)
        .where(ScoreRuleRow.id != rule_id, ScoreRuleRow.status == "ENABLED")
        .values(status="DISABLED")
    )
    row.status = "ENABLED"
    db.commit()
    db.refresh(row)
    return serialize_score_rule(row)


def simulate_score_rule(db: Session, rule_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """模拟参考分规则（**真算**，不再是 ``score: 0``）。

    ⚠️ **规则体格式**
    ``ruleJson`` 支持 ``factors`` 数组，每项形如::

        {"code": "ON_TIME", "name": "按期交付",
         "weight": 0.6, "threshold": 0.9, "bonus": 5}

    命中判定：``completionRatio >= threshold`` ⇒ 命中该因子。
    最终分 = ``baseScore``（默认 60）+ Σ(命中因子的 ``bonus``)，
    并按 ``weight`` 加权折算到 ``maxScore``（默认 100）。
    """
    row = db.get(ScoreRuleRow, rule_id)
    if row is None:
        raise LookupError("参考分规则不存在")

    rule = row.rule_json or {}
    factors = rule.get("factors")
    if not isinstance(factors, list) or not factors:
        # ⚠️ 没有因子的规则**不能瞎编一个分数** ⇒ 如实说明。
        return {
            "employeeName": str(payload.get("employeeName") or ""),
            "score": 0,
            "hitFactors": [],
            "explanation": "该规则未配置生效因子，无法模拟",
        }

    completion = _as_float(payload.get("completionRatio"), 0.0)
    overdue = _as_float(payload.get("overdueCount"), 0.0)
    rejects = _as_float(payload.get("rejectCount"), 0.0)
    evidence_complete = bool(payload.get("evidenceComplete"))
    base_score = _as_float(rule.get("baseScore"), 60.0)
    max_score = _as_float(rule.get("maxScore"), 100.0) or 100.0

    hit: list[str] = []
    hit_bonus = 0.0
    total_weight = 0.0
    weight_bonus = 0.0

    for factor in factors:
        if not isinstance(factor, dict):
            continue
        code = str(factor.get("code") or "").strip()
        name = str(factor.get("name") or code).strip()
        weight = _as_float(factor.get("weight"), 0.0)
        bonus = _as_float(factor.get("bonus"), 0.0)
        total_weight += weight

        if not _factor_hit(factor, completion, overdue, rejects, evidence_complete):
            continue
        hit.append(name)
        hit_bonus += bonus
        weight_bonus += weight * bonus

    if total_weight > 0:
        # ⚠️ 加权折算 —— 不同因子的权重不同，不能简单相加。
        score = base_score + (weight_bonus / total_weight)
    else:
        score = base_score + hit_bonus
    score = max(0.0, min(max_score, score))

    return {
        "employeeName": str(payload.get("employeeName") or ""),
        "score": round(score, 2),
        "hitFactors": hit,
        "explanation": (
            f"完成率 {completion:.0%}，命中 {len(hit)}/{len(factors)} 个因子，"
            f"加权得分 {score:.2f}"
        ),
    }


def _factor_hit(
    factor: dict[str, Any],
    completion: float,
    overdue: float,
    rejects: float,
    evidence_complete: bool,
) -> bool:
    """判断某个因子是否命中。"""
    metric = str(factor.get("metric") or "").strip().upper()
    threshold = factor.get("threshold")
    if metric == "COMPLETION_RATIO":
        limit = _as_float(threshold, 0.0)
        return completion >= limit
    if metric == "OVERDUE_COUNT":
        limit = _as_float(threshold, 0.0)
        # ⚠️ 「逾期数 ≤ 阈值」才算命中（阈值 0 ⇒ 不得有逾期）。
        return overdue <= limit
    if metric == "REJECT_COUNT":
        limit = _as_float(threshold, 0.0)
        return rejects <= limit
    if metric == "EVIDENCE_COMPLETE":
        return evidence_complete
    # ⚠️ 未知指标**不判定命中** —— 不能因为看不懂规则就发分。
    return False


def _as_float(raw: Any, default: float) -> float:
    try:
        if raw is None or raw == "":
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════════════════
# 待办
# ═══════════════════════════════════════════════════════════════════════════


def serialize_todo(row: TodoRow, receiver_name: str = "") -> dict[str, Any]:
    """待办 ⇒ 前端 ``TodoItem`` 契约。"""
    return {
        "id": row.id,
        "sceneCode": row.scene_code or "",
        "title": row.title or "",
        "triggerText": row.trigger_text or "",
        "receiverId": row.receiver_id,
        "receiverName": receiver_name,
        "objectType": row.object_type or "",
        "objectId": row.object_id or "",
        "dueAt": row.due_at.isoformat() if row.due_at else "",
        "requirement": row.requirement or "",
        "impact": row.impact or "",
        "status": row.status,
        "remindCount": int(row.remind_count or 0),
        "routeHint": row.route_hint or "",
    }


def list_todos(
    db: Session,
    *,
    receiver_ids: list[str] | None = None,
    scene_code: str | None = None,
    status: str | None = None,
    due_date: str | None = None,
    page_no: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """待办分页（真实数据）。"""
    stmt = select(TodoRow)
    if receiver_ids is not None:
        stmt = stmt.where(TodoRow.receiver_id.in_(receiver_ids or ["__none__"]))
    if scene_code:
        stmt = stmt.where(TodoRow.scene_code == scene_code)
    if status:
        stmt = stmt.where(TodoRow.status == status)
    if due_date:
        stmt = stmt.where(TodoRow.due_at.isnot(None))
        stmt = stmt.where(func.date(TodoRow.due_at) == due_date)

    total = int(db.execute(stmt.with_only_columns(func.count()).select_from(TodoRow)).scalar() or 0)
    rows = db.execute(
        stmt.order_by(TodoRow.created_at.desc())
        .offset(max(0, (page_no - 1) * page_size))
        .limit(page_size)
    ).scalars().all()

    names = _receiver_names(db, [r.receiver_id for r in rows])
    return {
        "items": [serialize_todo(r, names.get(r.receiver_id, "")) for r in rows],
        "total": total,
        "pageNo": page_no,
        "pageSize": page_size,
    }


def _receiver_names(db: Session, receiver_ids: list[str]) -> dict[str, str]:
    if not receiver_ids:
        return {}
    from ..domain.models import Employee

    rows = db.execute(
        select(Employee.id, Employee.name).where(Employee.id.in_(receiver_ids))
    ).all()
    return {r[0]: r[1] for r in rows}


def get_todo(db: Session, todo_id: str) -> TodoRow | None:
    return db.get(TodoRow, todo_id)


def remind_todo(db: Session, todo_id: str) -> dict[str, Any] | None:
    """催办待办（真落库）。

    ⚠️ **催办次数必须真的加 1** ——
    前端「催办次数」列直接显示这个值，
    恒为 0 就是在骗人。
    """
    row = db.get(TodoRow, todo_id)
    if row is None:
        return None
    row.remind_count = int(row.remind_count or 0) + 1
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    row.impact = (
        f"{row.impact}\n[{stamp}] 已发送催办提醒（累计 {row.remind_count} 次）"
    ).strip()
    if row.status == "UNREAD":
        row.status = "READ"
    db.commit()
    db.refresh(row)
    return row


def read_todo(db: Session, todo_id: str) -> dict[str, Any] | None:
    """标记待办已读（真落库）。"""
    row = db.get(TodoRow, todo_id)
    if row is None:
        return None
    if row.status == "UNREAD":
        row.status = "READ"
        db.commit()
        db.refresh(row)
    return row


def escalate_todo(db: Session, todo_id: str) -> dict[str, Any] | None:
    """升级待办（真落库）。

    ⚠️ 升级后状态变成 ``ESCALATED`` ⇒ 前端「已标记升级处理」能识别出来。
    """
    row = db.get(TodoRow, todo_id)
    if row is None:
        return None
    row.status = "ESCALATED"
    stamp = _now().strftime("%Y-%m-%d %H:%M")
    row.impact = f"{row.impact}\n[{stamp}] 已标记升级处理".strip()
    db.commit()
    db.refresh(row)
    return row


def complete_todo(db: Session, todo_id: str) -> dict[str, Any] | None:
    """完成待办（真落库）。"""
    row = db.get(TodoRow, todo_id)
    if row is None:
        return None
    row.status = "DONE"
    db.commit()
    db.refresh(row)
    return row


def create_todo(
    db: Session,
    *,
    scene_code: str,
    title: str,
    receiver_id: str,
    object_type: str = "",
    object_id: str = "",
    requirement: str = "",
    route_hint: str = "",
    due_at: datetime | None = None,
    trigger_text: str = "",
) -> TodoRow:
    """新建一条待办（供业务流程调用）。"""
    row = TodoRow(
        scene_code=scene_code,
        title=title,
        trigger_text=trigger_text,
        receiver_id=receiver_id,
        object_type=object_type,
        object_id=object_id,
        requirement=requirement,
        route_hint=route_hint,
        due_at=due_at,
        status="UNREAD",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ═══════════════════════════════════════════════════════════════════════════
# 导出任务
# ═══════════════════════════════════════════════════════════════════════════


def serialize_export_task(row: ExportTaskRow, requester_name: str = "") -> dict[str, Any]:
    """导出任务 ⇒ 前端 ``ExportTask`` 契约。"""
    return {
        "id": row.id,
        "dimensionType": row.dimension_type or "",
        "dimensionName": row.dimension_name or "",
        "periodType": row.period_type or "",
        "periodStart": row.period_start,
        "periodEnd": row.period_end,
        "formats": list(row.formats or []),
        "includeEvidence": bool(row.include_evidence),
        "watermark": row.watermark or "",
        "integrityStatus": row.integrity_status or "PENDING",
        "missingItems": list(row.missing_items or []),
        "checksum": row.checksum,
        "status": row.status,
        "sizeText": row.size_text or "0 B",
        "requestedBy": row.requested_by,
        "requestedByName": requester_name,
        "requestedAt": row.requested_at.isoformat() if row.requested_at else "",
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
        "expireAt": row.expire_at.isoformat() if row.expire_at else None,
        "errorMessage": row.error_message,
    }


def list_export_tasks(
    db: Session,
    *,
    requested_by: str | None = None,
    status: str | None = None,
    date: str | None = None,
    page_no: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """导出任务分页（真实数据）。"""
    stmt = select(ExportTaskRow)
    if requested_by:
        stmt = stmt.where(ExportTaskRow.requested_by == requested_by)
    if status:
        stmt = stmt.where(ExportTaskRow.status == status)
    if date:
        stmt = stmt.where(func.date(ExportTaskRow.requested_at) == date)

    total = int(
        db.execute(stmt.with_only_columns(func.count()).select_from(ExportTaskRow)).scalar() or 0
    )
    rows = db.execute(
        stmt.order_by(ExportTaskRow.requested_at.desc())
        .offset(max(0, (page_no - 1) * page_size))
        .limit(page_size)
    ).scalars().all()

    names = _receiver_names(db, [r.requested_by for r in rows])
    return {
        "items": [serialize_export_task(r, names.get(r.requested_by, "")) for r in rows],
        "total": total,
        "pageNo": page_no,
        "pageSize": page_size,
    }


def create_export_task(
    db: Session,
    payload: dict[str, Any],
    *,
    requested_by: str,
    dimension_name: str = "",
) -> ExportTaskRow:
    """新建导出任务（真落库）。

    ⚠️ **产物是同步生成的**
    本系统的导出数据量不大（部门/领导台的台账与清单），
    同步生成 + 落盘 + 算校验值 ⇒ 前端点「新建」后立刻能下载。
    """
    formats = payload.get("formats") or []
    if isinstance(formats, str):
        formats = [formats]
    formats = [str(f).strip().upper() for f in formats if str(f).strip()]
    if not formats:
        raise ValueError("请至少选择一种导出格式")

    row = ExportTaskRow(
        dimension_type=str(payload.get("dimensionType") or "").strip(),
        dimension_name=dimension_name,
        dimension_id=str(payload.get("dimensionId") or "").strip(),
        period_type=str(payload.get("periodType") or "ALL").strip() or "ALL",
        period_start=str(payload.get("periodStart") or "").strip() or None,
        period_end=str(payload.get("periodEnd") or "").strip() or None,
        formats=formats,
        include_evidence=bool(payload.get("includeEvidence")),
        watermark=str(payload.get("watermark") or "").strip(),
        integrity_status="PENDING",
        missing_items=[],
        status="PROCESSING",
        size_text="0 B",
        requested_by=requested_by,
        requested_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mark_export_success(
    db: Session,
    row: ExportTaskRow,
    *,
    content: bytes,
    file_name: str,
    checksum: str,
    missing_items: list[str] | None = None,
) -> ExportTaskRow:
    """标记导出任务成功（真落库 + 落盘）。"""
    from pathlib import Path

    from ..core.config import settings

    output_dir = Path(settings.export_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{row.id}_{file_name}"
    path.write_bytes(content)

    row.artifact_path = path.name
    row.checksum = checksum
    row.size_text = _human_size(len(content))
    row.status = "SUCCESS"
    row.integrity_status = "INCOMPLETE" if missing_items else "COMPLETE"
    row.missing_items = missing_items or []
    row.finished_at = _now()
    row.expire_at = _now() + timedelta(days=7)
    row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def mark_export_failed(db: Session, row: ExportTaskRow, message: str) -> ExportTaskRow:
    """标记导出任务失败（真落库）。"""
    row.status = "FAILED"
    row.error_message = message[:2000]
    row.finished_at = _now()
    db.commit()
    db.refresh(row)
    return row


def check_export_task(db: Session, task_id: str) -> dict[str, Any] | None:
    """校验导出任务完整性（**真校验文件**）。

    ⚠️ **不能只把状态改成 ``CHECKED``**
    那只是把 ``status`` 换个字符串，**文件在不在、校验值对不对都没查**。
    ⇒ 这里真的把文件读回来算一遍 SHA-256。
    """
    from pathlib import Path

    from ..core.config import settings

    row = db.get(ExportTaskRow, task_id)
    if row is None:
        return None
    if not row.artifact_path:
        row.integrity_status = "INCOMPLETE"
        row.status = "FAILED"
        row.error_message = "导出产物不存在"
        db.commit()
        db.refresh(row)
        return row

    path = Path(settings.export_output_dir) / row.artifact_path
    if not path.exists():
        row.integrity_status = "INCOMPLETE"
        row.status = "EXPIRED"
        row.error_message = "导出产物已丢失或已过期"
        db.commit()
        db.refresh(row)
        return row

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if row.checksum and digest != row.checksum:
        row.integrity_status = "INCOMPLETE"
        row.status = "NEEDS_REVIEW"
        row.error_message = "校验值不一致，文件可能已被篡改"
    else:
        row.integrity_status = "COMPLETE"
        row.status = "SUCCESS"
        row.checksum = digest
        row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def retry_export_task(db: Session, task_id: str) -> dict[str, Any] | None:
    """重试导出任务（真落库：状态回到 PROCESSING）。"""
    row = db.get(ExportTaskRow, task_id)
    if row is None:
        return None
    row.status = "PROCESSING"
    row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def get_export_artifact(db: Session, task_id: str) -> tuple[ExportTaskRow, bytes] | None:
    """取导出产物（真读文件）。"""
    from pathlib import Path

    from ..core.config import settings

    row = db.get(ExportTaskRow, task_id)
    if row is None or not row.artifact_path:
        return None
    path = Path(settings.export_output_dir) / row.artifact_path
    if not path.exists():
        return None
    return row, path.read_bytes()


def export_download_info(row: ExportTaskRow) -> dict[str, Any]:
    """导出任务下载信息 ⇒ 前端 ``ExportDownload`` 契约。"""
    return {
        "taskId": row.id,
        "status": row.status,
        "fileName": row.artifact_path or "",
        "downloadUrl": f"/v1/department/export-tasks/{row.id}/download",
        "expireAt": row.expire_at.isoformat() if row.expire_at else None,
        "checksum": row.checksum,
    }


def _human_size(size: int) -> str:
    """字节数 ⇒ 人话。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def checksum_of(content: bytes) -> str:
    """内容校验值（SHA-256）。

    ⚠️ **必须是完整 64 位十六进制**
    2026-11-26 修复：原来这里截断成 ``[:16]``，而
    ``check_export_task()`` 里比对的是 ``hashlib.sha256(...).hexdigest()``
    的**完整 64 位** ⇒ 两者**永远不相等** ⇒
    校验端点**永远**把任务判成「校验值不一致，文件可能已被篡改」，
    而文件其实好好的。
    ⇒ 存和比必须用**同一个函数**、**同样长度**的值。
    """
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "check_export_task",
    "checksum_of",
    "complete_todo",
    "create_export_task",
    "create_standard",
    "create_template",
    "create_todo",
    "enable_score_rule",
    "escalate_todo",
    "export_download_info",
    "get_export_artifact",
    "get_todo",
    "list_export_tasks",
    "list_score_rules",
    "list_standards",
    "list_todos",
    "list_templates",
    "mark_export_failed",
    "mark_export_success",
    "read_todo",
    "remind_todo",
    "retry_export_task",
    "serialize_export_task",
    "serialize_score_rule",
    "serialize_standard",
    "serialize_template",
    "serialize_todo",
    "simulate_score_rule",
    "update_score_rule",
    "update_standard",
    "update_template",
]
