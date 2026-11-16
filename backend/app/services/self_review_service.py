"""【板块3】员工自评提交 —— 渠道无关的业务编排服务。

定位（严格遵循多渠道架构规则）：
    本模块是「自研前端渠道」与「业务核心」之间的**薄编排层**，
    不含任何渠道特有的逻辑（无飞书、无表单翻译、无卡片包装）。

    它只做三件事：
      1. 把 API 入参翻译成业务核心的参数形状；
      2. 按既有业务规则做参数/时间/状态的前置校验（校验口径与业务层一致，不新增规则）；
      3. 调用业务核心 `review_service.submit_self_review()` 落库，
         并把结果整理成统一的响应结构。

不变式：
    - 自评实体只有一张标准表（domain.models.SelfReview），两入口共用；
    - 状态流转、submit_locked、证据生成、审计日志全部由 review_service 负责，
      本模块**不重复实现**；
    - 权限校验不在这里做（由路由层复用 dependencies.check_can_submit_review）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .. import exceptions
from ..domain.enums import RecordStatus, SubmitSource
from ..domain.models import AssessmentRecord, AssessmentTask, Employee
from ..schemas_self_review import SelfReviewSubmitRequest
from ..services import review_service

logger = logging.getLogger(__name__)

# 自评评论最小长度（与业务校验口径一致）
SELF_COMMENT_MIN_LEN = 10
SELF_COMMENT_MAX_LEN = 2000


def _answers_from_request(req: SelfReviewSubmitRequest) -> dict[str, Any]:
    """把结构化自评表单翻译成标准 answers_json。

    渠道无关：飞书渠道在板块6只需构造同一个 SelfReviewSubmitRequest，
    无需改动本函数，也无需改动业务层。
    """
    answers: dict[str, Any] = {
        "self_score": req.self_score,
        "self_comment": req.self_comment,
    }
    if req.highlights is not None:
        answers["highlights"] = req.highlights
    if req.improvement_areas is not None:
        answers["improvement_areas"] = req.improvement_areas
    if req.metric_values:
        answers["metric_values"] = [
            {
                "metric_code": mv.metric_code,
                "value": mv.value,
                "unit": mv.unit,
                "comment": mv.comment,
            }
            for mv in req.metric_values
        ]
    return answers


def _attachments_from_request(req: SelfReviewSubmitRequest) -> list[dict[str, Any]]:
    """附件统一转为业务层接受的 list[dict]。"""
    if not req.attachments:
        return []
    return [
        {
            "file_id": att.file_id,
            "file_name": att.file_name,
            "file_size": att.file_size,
            "file_type": att.file_type,
            **({"url": att.url} if att.url else {}),
        }
        for att in req.attachments
    ]


def submit_self_review(
    db: Session,
    *,
    record_id: str,
    current_employee: Employee,
    req: SelfReviewSubmitRequest,
) -> dict[str, Any]:
    """提交自评（自研前端渠道入口的业务编排）。

    前置条件：调用方已完成鉴权与权限校验（依赖注入方式复用 dependencies）。

    抛出的异常全部来自 exceptions 模块，由路由层统一转 HTTP 响应：
        RecordNotFound / PermissionDenied / InvalidStateTransition /
        DataValidationError / DeadlineExceeded
    """
    # ── 1. 记录存在性 ─────────────────────────────────────────
    record = db.query(AssessmentRecord).filter_by(id=record_id).first()
    if record is None:
        raise exceptions.RecordNotFound("AssessmentRecord", record_id)

    # ── 2. 归属校验（纵深防御：路由层已查过，这里再护一层） ──
    if record.employee_id != current_employee.id:
        raise exceptions.PermissionDenied(
            user_id=current_employee.id,
            action="submit_self_review",
            resource=f"AssessmentRecord({record_id})",
        )

    # ── 3. 参数校验（与业务层口径一致，不新增规则） ────────────
    comment = (req.self_comment or "").strip()
    if len(comment) < SELF_COMMENT_MIN_LEN:
        raise exceptions.DataValidationError(
            detail=f"自评评论至少 {SELF_COMMENT_MIN_LEN} 个字",
            field="self_comment",
        )
    if len(comment) > SELF_COMMENT_MAX_LEN:
        raise exceptions.DataValidationError(
            detail=f"自评评论不能超过 {SELF_COMMENT_MAX_LEN} 个字",
            field="self_comment",
        )
    if req.self_score < 1 or req.self_score > 5:
        raise exceptions.DataValidationError(
            detail="自评分数必须在 1-5 之间", field="self_score"
        )
    if req.metric_values:
        seen: set[str] = set()
        for mv in req.metric_values:
            code = (mv.metric_code or "").strip()
            if not code:
                raise exceptions.DataValidationError(
                    detail="指标编码不能为空", field="metric_values.metric_code"
                )
            if code in seen:
                raise exceptions.DataValidationError(
                    detail=f"指标 {code} 重复提交", field="metric_values.metric_code"
                )
            seen.add(code)
            if mv.value < 0:
                raise exceptions.DataValidationError(
                    detail=f"指标 {code} 的数值不能为负数", field="metric_values.value"
                )

    # ── 4. 状态与锁定校验 ────────────────────────────────────
    if record.submit_locked:
        raise exceptions.InvalidStateTransition(
            entity_type="AssessmentRecord",
            current_state=record.status,
            attempted_action="submit_self_review",
        )
    if record.status not in (
        RecordStatus.NOT_STARTED.value,
        RecordStatus.SELF_REVIEW_OPEN.value,
    ):
        raise exceptions.InvalidStateTransition(
            entity_type="AssessmentRecord",
            current_state=record.status,
            attempted_action="submit_self_review",
        )

    # ── 5. 截止时间校验（任务上若配置了截止则生效） ───────────
    task = db.query(AssessmentTask).filter_by(id=record.task_id).first()
    if task is None:
        raise exceptions.RecordNotFound("AssessmentTask", record.task_id)

    deadline = getattr(task, "self_review_deadline", None)
    if deadline is not None and datetime.now() > deadline:
        raise exceptions.DeadlineExceeded(
            deadline_type="自评", deadline=deadline.isoformat()
        )

    # ── 6. 交给业务核心落库（状态流转/证据/审计均在其中） ────
    self_review = review_service.submit_self_review(
        db,
        record_id=record_id,
        answers=_answers_from_request(req),
        attachments=_attachments_from_request(req),
        source=SubmitSource.H5,  # 自研前端工作台
        dedup_key=req.dedup_key,
    )
    db.commit()
    db.refresh(record)

    submitted_at = self_review.submitted_at
    logger.info(
        "[self-review] record=%s employee=%s status=%s",
        record_id,
        current_employee.id,
        record.status,
    )

    # ── 7. 统一响应结构（渠道层可在板块6二次包装） ────────────
    return {
        "record_id": record.id,
        "employee_id": record.employee_id,
        "self_review_id": self_review.id,
        "status": record.status,
        "submit_source": record.submit_source,
        "submit_locked": record.submit_locked,
        "self_score": req.self_score,
        "self_comment": comment,
        "metric_values_count": len(req.metric_values or []),
        "attachments_count": len(req.attachments or []),
        "submitted_at": submitted_at.isoformat() if submitted_at else None,
        "message": "自评已提交成功，等待主管评分",
        "next_step": "主管评分",
    }
