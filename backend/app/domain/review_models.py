"""复核与校准中心：案件扩展、受保护版本、证据与调整单。"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _uuid


class ReviewCase(Base):
    __tablename__ = "review_case"
    id: Mapped[str] = mapped_column(String(64), ForeignKey("dispute_case.id"), primary_key=True)
    record_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("assessment_record.id"))
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    policy: Mapped[dict] = mapped_column(JSON, nullable=False)
    original_reviewer_id: Mapped[str | None] = mapped_column(String(64))
    accepted_by: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    parent_case_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dispute_case.id"))


class ReviewArtifact(Base):
    __tablename__ = "review_artifact"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("dispute_case.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ReviewEvent(Base):
    __tablename__ = "review_event"
    __table_args__ = (UniqueConstraint("case_id", "sequence", name="uq_review_event_sequence"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("dispute_case.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class VerificationReport(Base):
    __tablename__ = "review_report"
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_review_report_version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("dispute_case.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ScoreHead(Base):
    __tablename__ = "review_score_head"
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    protected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ScoreVersion(Base):
    __tablename__ = "review_score_version"
    __table_args__ = (UniqueConstraint("record_id", "version", name="uq_review_score_version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64))
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ScoreAdjustment(Base):
    __tablename__ = "review_adjustment"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), index=True)
    case_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dispute_case.id"))
    meeting_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("review_meeting.id"))
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    before: Mapped[dict] = mapped_column(JSON, nullable=False)
    proposed: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_corrections: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approval_comment: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime)


class CalibrationMeeting(Base):
    __tablename__ = "review_meeting"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    department_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    participants: Mapped[list] = mapped_column(JSON, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class CalibrationResolution(Base):
    __tablename__ = "review_resolution"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    meeting_id: Mapped[str] = mapped_column(String(64), ForeignKey("review_meeting.id"), index=True)
    record_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("assessment_record.id"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ReviewOutbox(Base):
    __tablename__ = "review_outbox"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    event_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


def _immutable(_mapper, _connection, _target):
    raise ValueError("正式历史记录禁止覆盖或删除，请追加更正记录")


for _model in (ReviewArtifact, ReviewEvent, VerificationReport, ScoreVersion, CalibrationResolution):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
