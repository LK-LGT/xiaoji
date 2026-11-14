"""计划版本 / 审批流水 / 争议 / AI 域的 SQLAlchemy 模型。

本模块与 `models.py` 共享同一个 `Base`，因此：
- `Base.metadata.create_all()` 会一并建出这里的表；
- Alembic 的 `target_metadata` 也能自动发现这些表。

⚠️ **重要边界**：月 / 周 / 日计划的**主体表与条目表已存在**
（`month_plan` / `month_plan_item` / `week_plan` / `week_plan_item` /
`day_plan` / `day_plan_item`，见 `models.py` 与迁移 `0003_phase3_planning`）。
本模块**不重复定义**它们，只补齐此前缺失的部分：

1. `plan_draft_version` —— 计划草稿版本快照（对应前端 ``recordVersion`` /
   ``restoredFromVersionId`` 语义）。通过 ``(plan_type, plan_id)`` 多态关联
   已有的三张计划表，不建外键（SQLite 不支持跨表多态外键）。

2. `plan_approval` —— 计划提交 / 撤回 / 审批流水。同样用多态关联。

3. `dispute_case` / `dispute_reviewer` / `dispute_opinion` —— 绩效争议。

4. `ai_review` / `ai_conversation` / `ai_message` / `ai_suggestion_action`
   —— AI 复核与对话记录。**只记录 AI 行为，不写业务表**
   （分数、等级、状态仍由 L2 业务层与人工确认产出）。

设计要点：

- **周期标识统一**：月 ``2026-11``、周 ``2026-11-02``、日 ``2026-11-02``。
- **版本快照不可变**：`snapshot_json` 只写不改；恢复历史版本时**新建**一条
  ``RESTORED_SAVE`` 版本，而不是把老版本改回 active。
- **争议案件与评审意见分离**：一条意见一个版本，便于留痕。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Base, Employee, _now, _uuid


# ---------------------------------------------------------------------------
# 计划域：版本快照与审批流水
# ---------------------------------------------------------------------------


class PlanDraftVersion(Base):
    """计划草稿版本快照。

    对应前端 ``recordVersion`` / ``restoredFromVersionId``：
    开启 ``recordVersion`` 时每次保存留一份不可变快照；
    恢复历史版本时新建 ``RESTORED_SAVE`` 记录，**不改老版本**。

    ``plan_type`` 取 ``MONTH`` / ``WEEK`` / ``DAY``，与
    ``plan_id`` 组成多态关联（不建物理外键，避免三选一的约束复杂度）。
    """

    __tablename__ = "plan_draft_version"
    __table_args__ = (
        UniqueConstraint("plan_type", "plan_id", "version_no", name="uq_plan_draft_version_no"),
        Index("ix_plan_draft_version_plan", "plan_type", "plan_id", "version_no"),
        # 按来源 / 请求 ID 反查（幂等与排查）
        Index("ix_plan_draft_version_request", "request_id"),
        Index("ix_plan_draft_version_conversation", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    plan_type: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="MANUAL_SAVE", nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    snapshot_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    base_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggestion_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PlanApproval(Base):
    """计划审批 / 提交 / 撤回流水。

    每次状态流转写一行，形成完整操作轨迹。
    同样通过 ``(plan_type, plan_id)`` 多态关联已有的三张计划表。
    """

    __tablename__ = "plan_approval"
    __table_args__ = (
        Index("ix_plan_approval_plan", "plan_type", "plan_id", "created_at"),
        Index("ix_plan_approval_actor", "actor_id", "created_at"),
        # 幂等键：同一计划同一动作同一幂等键只写一次
        Index("ix_plan_approval_dedup", "dedup_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    plan_type: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ---------------------------------------------------------------------------
# 争议域
# ---------------------------------------------------------------------------


class DisputeCase(Base):
    """绩效争议案件。

    一个争议对应一次「员工对绩效结果不认可」的申诉。
    """

    __tablename__ = "dispute_case"
    __table_args__ = (
        UniqueConstraint("case_no", name="uq_dispute_case_no"),
        Index("ix_dispute_case_status", "status", "created_at"),
        Index("ix_dispute_case_owner", "owner_id", "status"),
        Index("ix_dispute_case_dept", "department_id", "status"),
        # 同一结果同一员工同一周期只允许一个活跃争议
        Index("ix_dispute_case_result", "related_result_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    case_no: Mapped[str] = mapped_column(String(64), nullable=False)
    appeal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    department_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("department.id"), nullable=True, index=True
    )

    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    dispute_subject: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    appeal_title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    appeal_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    appeal_status: Mapped[str] = mapped_column(String(32), default="submitted", nullable=False)

    related_result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    result_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="SUBMITTED", nullable=False)
    package_status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    package_items: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)

    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )

    reviewers: Mapped[list["DisputeReviewer"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    opinions: Mapped[list["DisputeOpinionRecord"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class DisputeReviewer(Base):
    """争议案件评审人（含回避状态）。"""

    __tablename__ = "dispute_reviewer"
    __table_args__ = (
        UniqueConstraint("case_id", "user_id", name="uq_dispute_reviewer_case_user"),
        Index("ix_dispute_reviewer_case", "case_id", "recusal_status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dispute_case.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), default="MANUAL", nullable=False)
    recusal_status: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False)
    recusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    case: Mapped[DisputeCase] = relationship(back_populates="reviewers")


class DisputeOpinionRecord(Base):
    """评审意见流水（一条意见一个版本）。"""

    __tablename__ = "dispute_opinion"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "reviewer_id", "version_no", name="uq_dispute_opinion_version"
        ),
        Index("ix_dispute_opinion_case", "case_id", "submitted_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dispute_case.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("dispute_reviewer.id"), nullable=False, index=True
    )
    opinion: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    case: Mapped[DisputeCase] = relationship(back_populates="opinions")


# ---------------------------------------------------------------------------
# AI 域
# ---------------------------------------------------------------------------


class AiReview(Base):
    """AI 复核记录。

    ⚠️ 只记录 AI 的判断，**不写业务表**。风险等级、建议等级仅供参考，
    最终结论仍由 L2 业务层与人工确认产出。
    """

    __tablename__ = "ai_review"
    __table_args__ = (
        Index("ix_ai_review_biz", "biz_type", "biz_id", "checked_at"),
        Index("ix_ai_review_hash", "content_hash"),
        Index("ix_ai_review_status", "status", "checked_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False)
    biz_id: Mapped[str] = mapped_column(String(64), nullable=False)
    biz_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    status: Mapped[str] = mapped_column(String(32), default="RULE_ONLY", nullable=False)
    overall_risk: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    checked_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class AiConversation(Base):
    """AI 对话会话（一个业务对象 / 场景一条活跃会话）。"""

    __tablename__ = "ai_conversation"
    __table_args__ = (
        Index("ix_ai_conversation_owner", "owner_id", "status"),
        Index("ix_ai_conversation_scene", "scene_code", "biz_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    scene_code: Mapped[str] = mapped_column(String(64), nullable=False)
    biz_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    biz_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )

    messages: Mapped[list["AiMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class AiMessage(Base):
    """AI 对话消息。"""

    __tablename__ = "ai_message"
    __table_args__ = (
        Index("ix_ai_message_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ai_conversation.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    suggestion_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    conversation: Mapped[AiConversation] = relationship(back_populates="messages")


class AiSuggestionAction(Base):
    """AI 建议采纳动作流水。

    记录「人采纳了 AI 的哪条建议、改了什么」，用于效果统计与审计。
    """

    __tablename__ = "ai_suggestion_action"
    __table_args__ = (
        Index("ix_ai_suggestion_action_suggestion", "suggestion_id", "created_at"),
        Index("ix_ai_suggestion_action_actor", "actor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    suggestion_id: Mapped[str] = mapped_column(String(256), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_code: Mapped[str] = mapped_column(String(32), nullable=False)
    applied_fields: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
