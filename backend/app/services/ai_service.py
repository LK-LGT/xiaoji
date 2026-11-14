"""AI 域服务层 —— 复核记录、会话、消息、建议采纳流水。

⚠️ **边界约束（决策⑤）**：本服务只负责**记录** AI 的行为，
绝不写业务表（计划、争议、绩效记录的状态与分数仍由 L2 业务层与人工确认产出）。

- ``ai_review``：一次 AI 复核的结果（规则引擎 / 模型产出的风险与建议）。
- ``ai_conversation`` / ``ai_message``：人机对话记录。
- ``ai_suggestion_action``：人采纳了哪条建议、改了什么的审计流水。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import exceptions
from ..domain.enums import (
    AiConversationStatus,
    AiMessageRole,
    AiRiskLevel,
    AiSuggestionAction,
)
from ..domain.models import Employee
from ..domain.plan_models import (
    AiConversation,
    AiMessage,
    AiReview,
    AiSuggestionAction as AiSuggestionActionRow,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def content_hash(payload: Any) -> str:
    """对内容做稳定哈希（用于幂等去重）。"""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso_now() -> str:
    """当前时间 ISO 字符串。"""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ---------------------------------------------------------------------------
# AI 复核记录
# ---------------------------------------------------------------------------


def record_review(
    db: Session,
    *,
    biz_type: str,
    biz_id: str,
    result: dict[str, Any],
    status: str,
    overall_risk: str = AiRiskLevel.LOW,
    biz_version: str | None = None,
    provider: str = "",
    model_name: str = "",
    prompt_version: str = "",
    error_message: str | None = None,
) -> AiReview:
    """写入一条 AI 复核记录。"""
    review = AiReview(
        biz_type=biz_type,
        biz_id=biz_id,
        biz_version=biz_version,
        content_hash=content_hash(result),
        status=status,
        overall_risk=overall_risk,
        provider=provider,
        model_name=model_name,
        prompt_version=prompt_version,
        result_json=result,
        error_message=error_message,
        checked_at=datetime.utcnow(),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def list_reviews(
    db: Session,
    *,
    biz_type: str | None = None,
    biz_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AiReview]:
    """查询 AI 复核记录。"""
    stmt = select(AiReview)
    if biz_type:
        stmt = stmt.where(AiReview.biz_type == biz_type)
    if biz_id:
        stmt = stmt.where(AiReview.biz_id == biz_id)
    if status:
        stmt = stmt.where(AiReview.status == status)
    stmt = stmt.order_by(AiReview.checked_at.desc())
    stmt = stmt.limit(max(1, min(limit, 200))).offset(max(0, offset))
    return list(db.execute(stmt).scalars().all())


def count_reviews(
    db: Session,
    *,
    biz_type: str | None = None,
    biz_id: str | None = None,
    status: str | None = None,
) -> int:
    """统计 AI 复核记录数。"""
    stmt = select(func.count()).select_from(AiReview)
    if biz_type:
        stmt = stmt.where(AiReview.biz_type == biz_type)
    if biz_id:
        stmt = stmt.where(AiReview.biz_id == biz_id)
    if status:
        stmt = stmt.where(AiReview.status == status)
    return int(db.execute(stmt).scalar_one())


# ---------------------------------------------------------------------------
# 会话与消息
# ---------------------------------------------------------------------------


def get_conversation(db: Session, conversation_id: str) -> AiConversation:
    """按 ID 取会话。"""
    conversation = db.get(AiConversation, conversation_id)
    if conversation is None:
        raise exceptions.RecordNotFound("AI 会话", conversation_id)
    return conversation


def create_conversation(
    db: Session,
    *,
    owner: Employee,
    scene_code: str,
    title: str = "",
    biz_type: str = "",
    biz_id: str | None = None,
) -> AiConversation:
    """创建 AI 会话。"""
    conversation = AiConversation(
        owner_id=owner.id,
        scene_code=scene_code,
        biz_type=biz_type,
        biz_id=biz_id,
        title=title or _default_title(scene_code),
        status=AiConversationStatus.ACTIVE,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(
    db: Session,
    *,
    owner_id: str | None = None,
    scene_code: str | None = None,
    biz_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AiConversation]:
    """查询会话列表。"""
    stmt = select(AiConversation)
    if owner_id:
        stmt = stmt.where(AiConversation.owner_id == owner_id)
    if scene_code:
        stmt = stmt.where(AiConversation.scene_code == scene_code)
    if biz_id:
        stmt = stmt.where(AiConversation.biz_id == biz_id)
    if status:
        stmt = stmt.where(AiConversation.status == status)
    stmt = stmt.order_by(AiConversation.updated_at.desc())
    stmt = stmt.limit(max(1, min(limit, 200))).offset(max(0, offset))
    return list(db.execute(stmt).scalars().all())


def find_active_conversation(
    db: Session,
    *,
    owner_id: str,
    scene_code: str,
    biz_type: str = "",
    biz_id: str | None = None,
) -> AiConversation | None:
    """查找某场景下的活跃会话（同一员工 + 场景 + 业务对象只有一条）。"""
    stmt = select(AiConversation).where(
        AiConversation.owner_id == owner_id,
        AiConversation.scene_code == scene_code,
        AiConversation.status == AiConversationStatus.ACTIVE,
    )
    if biz_type:
        stmt = stmt.where(AiConversation.biz_type == biz_type)
    if biz_id:
        stmt = stmt.where(AiConversation.biz_id == biz_id)
    stmt = stmt.order_by(AiConversation.updated_at.desc())
    return db.execute(stmt).scalars().first()


def count_conversations(
    db: Session,
    *,
    owner_id: str | None = None,
    scene_code: str | None = None,
    biz_id: str | None = None,
    status: str | None = None,
) -> int:
    """统计会话数。"""
    stmt = select(func.count()).select_from(AiConversation)
    if owner_id:
        stmt = stmt.where(AiConversation.owner_id == owner_id)
    if scene_code:
        stmt = stmt.where(AiConversation.scene_code == scene_code)
    if biz_id:
        stmt = stmt.where(AiConversation.biz_id == biz_id)
    if status:
        stmt = stmt.where(AiConversation.status == status)
    return int(db.execute(stmt).scalar_one())


def append_message(
    db: Session,
    conversation: AiConversation,
    *,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
    suggestion_id: str | None = None,
) -> AiMessage:
    """追加一条会话消息。"""
    if role not in {
        AiMessageRole.USER,
        AiMessageRole.ASSISTANT,
        AiMessageRole.SYSTEM,
    }:
        raise exceptions.BaseAppError(
            code="INVALID_AI_MESSAGE_ROLE",
            message=f"非法的消息角色：{role}",
            status_code=400,
        )

    message = AiMessage(
        conversation_id=conversation.id,
        role=role,
        content=content,
        payload_json=payload or {},
        request_id=request_id,
        suggestion_id=suggestion_id,
    )
    db.add(message)
    conversation.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    return message


def list_messages(
    db: Session,
    conversation_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[AiMessage]:
    """查询会话消息（按时间正序）。"""
    stmt = (
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation_id)
        .order_by(AiMessage.created_at.asc(), AiMessage.id.asc())
    )
    stmt = stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))
    return list(db.execute(stmt).scalars().all())


def count_messages(db: Session, conversation_id: str) -> int:
    """统计会话消息数。"""
    stmt = (
        select(func.count())
        .select_from(AiMessage)
        .where(AiMessage.conversation_id == conversation_id)
    )
    return int(db.execute(stmt).scalar_one())


def archive_conversation(db: Session, conversation: AiConversation) -> AiConversation:
    """归档会话（只读）。"""
    conversation.status = AiConversationStatus.ARCHIVED
    db.commit()
    db.refresh(conversation)
    return conversation


# ---------------------------------------------------------------------------
# 建议采纳流水
# ---------------------------------------------------------------------------


def record_suggestion_action(
    db: Session,
    *,
    suggestion_id: str,
    actor: Employee,
    action_code: str,
    applied_fields: list[Any] | None = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
) -> AiSuggestionActionRow:
    """记录一次「人采纳 AI 建议」的动作。"""
    if action_code not in {
        AiSuggestionAction.PREVIEW,
        AiSuggestionAction.APPLY_ALL,
        AiSuggestionAction.APPLY_ITEM,
        AiSuggestionAction.APPLY_FIELDS,
        AiSuggestionAction.ADOPT_WITH_EDIT,
        AiSuggestionAction.IGNORE,
    }:
        raise exceptions.BaseAppError(
            code="INVALID_AI_SUGGESTION_ACTION",
            message=f"非法的建议采纳动作：{action_code}",
            status_code=400,
        )

    record = AiSuggestionActionRow(
        suggestion_id=suggestion_id,
        actor_id=actor.id,
        action_code=action_code,
        applied_fields=applied_fields or [],
        before_hash=before_hash,
        after_hash=after_hash,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_suggestion_actions(
    db: Session,
    *,
    suggestion_id: str | None = None,
    actor_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AiSuggestionActionRow]:
    """查询建议采纳流水。"""
    stmt = select(AiSuggestionActionRow)
    if suggestion_id:
        stmt = stmt.where(AiSuggestionActionRow.suggestion_id == suggestion_id)
    if actor_id:
        stmt = stmt.where(AiSuggestionActionRow.actor_id == actor_id)
    stmt = stmt.order_by(AiSuggestionActionRow.created_at.desc())
    stmt = stmt.limit(max(1, min(limit, 200))).offset(max(0, offset))
    return list(db.execute(stmt).scalars().all())


def _default_title(scene_code: str) -> str:
    """按场景生成默认会话标题。"""
    labels = {
        "PLAN_DRAFT": "计划草稿助手",
        "PLAN_OPTIMIZE": "计划优化助手",
        "DISPUTE_HELP": "争议答疑助手",
        "RESULT_REVIEW": "结果复核助手",
    }
    return labels.get(scene_code, "AI 助手")
