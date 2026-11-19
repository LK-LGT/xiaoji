"""HR-owned scheme aggregates: immutable published payloads and normalized scope bindings."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _uuid


class PerformanceScheme(Base):
    __tablename__ = "performance_scheme"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    # Serializes all draft/version writes, including on SQLite (which ignores FOR UPDATE).
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SchemeVersion(Base):
    __tablename__ = "performance_scheme_version"
    __table_args__ = (UniqueConstraint("scheme_id", "version_no", name="uq_scheme_version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    scheme_id: Mapped[str] = mapped_column(
        ForeignKey("performance_scheme.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    # Metrics, scoring descriptions, interval rules, grades and fields form one atomic version.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_import_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    copied_from_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SchemeBinding(Base):
    __tablename__ = "performance_scheme_binding"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("performance_scheme_version.id"), nullable=False, index=True
    )
    department_id: Mapped[str] = mapped_column(
        ForeignKey("department.id"), nullable=False, index=True
    )
    position: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, nullable=False)


class SchemeResponse(Base):
    __tablename__ = "performance_scheme_response"
    record_id: Mapped[str] = mapped_column(ForeignKey("assessment_record.id"), primary_key=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reviews: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class SchemeImportBatch(Base):
    __tablename__ = "performance_scheme_import"
    __table_args__ = (UniqueConstraint("operator_id", "file_hash", name="uq_scheme_import_file"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="uploaded", nullable=False)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    preview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
