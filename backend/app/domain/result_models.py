"""成果域的 SQLAlchemy 模型 —— 月成果 / 周成果 + 证据附件。

背景
----
此前**完全没有成果表**（既无 ``month_result`` 也无 ``week_result``），
``employee_result.py`` 的端点全是返回空壳的假数据。本模块补齐成果域。

设计：统一成果表
----------------
月成果与周成果**共用一张** ``result`` 表，用 ``plan_type`` 区分
（``month`` / ``week``），理由：

1. 两者的字段语义高度重叠（完成率、完成情况、交付物、备注、附件、审批态），
   拆两张表会带来大量重复定义与重复查询逻辑。
2. 成果的**状态机、审批流水、证据附件、版本化**逻辑完全一致，
   统一表才能让服务层只写一套。
3. 未来若出现"日成果"，加一个 ``plan_type`` 值即可，不必再加表。

多态关联
--------
``result`` 通过 ``(plan_type, plan_id)`` 多态关联 ``month_plan`` /
``week_plan``，**不建物理外键** —— SQLite 不支持跨表多态外键，
且 A1 的 ``plan_approval`` 已采用同一模式，保持一致。

关联关系：

- 月成果：``plan_id`` → ``month_plan.id``，``plan_item_id`` → ``month_plan_item.id``
- 周成果：``plan_id`` → ``week_plan.id``，``plan_item_id`` → ``week_plan_item.id``

``result_evidence`` 通过 ``result_id`` 外键关联 ``result``。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Base, Employee, _now, _uuid

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

RESULT_PLAN_TYPE_MONTH = "month"
RESULT_PLAN_TYPE_WEEK = "week"

#: 成果状态（对齐前端 `EmployeeResultStatus`：`not_submitted` / `draft` /
#: `submitted` / `confirmed` / `rejected`）。
RESULT_STATUS_DRAFT = "draft"
RESULT_STATUS_SUBMITTED = "submitted"
RESULT_STATUS_CONFIRMED = "confirmed"
RESULT_STATUS_REJECTED = "rejected"


# ---------------------------------------------------------------------------
# 成果
# ---------------------------------------------------------------------------


class Result(Base):
    """成果 —— 员工针对某条计划任务提交的交付成果。

    状态流转（与前端 ``EmployeeResultStatus`` 对齐）：

        draft ──submit──> submitted ──approve──> confirmed
                                 └────reject────> rejected
                                              （驳回后可修改并重新提交）

    ⚠️ 一级审批（决策 D2/D4）：主管审批即完成。`department_leader` 角色的
    审批端点与主管是**同一批端点**，不是第二级审批链。
    """

    __tablename__ = "result"
    __table_args__ = (
        Index("ix_result_owner_type_status", "owner_id", "plan_type", "status"),
        Index("ix_result_plan", "plan_type", "plan_id"),
        Index("ix_result_plan_item", "plan_type", "plan_item_id"),
        # 同一计划条目同一版本号唯一 —— 支撑幂等提交与版本化
        UniqueConstraint(
            "plan_type",
            "plan_id",
            "plan_item_id",
            "version_no",
            name="uq_result_plan_item_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )

    # 多态关联：month → month_plan，week → week_plan
    plan_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RESULT_PLAN_TYPE_MONTH
    )
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 多态关联计划条目：month → month_plan_item，week → week_plan_item
    plan_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 成果内容
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    deliverable: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remark: Mapped[str] = mapped_column(Text, nullable=False, default="")
    completion_status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    completion_rate: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, server_default="0"
    )
    personal_performance: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 周期与版本
    result_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(
        String(32), default=RESULT_STATUS_DRAFT, nullable=False, index=True
    )

    # 提交与审批
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 证据与 AI 复核
    evidence_status: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False)
    ai_review_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )

    owner: Mapped[Employee] = relationship()
    evidences: Mapped[list[ResultEvidence]] = relationship(
        back_populates="result",
        cascade="all, delete-orphan",
        order_by="ResultEvidence.created_at",
    )


# ---------------------------------------------------------------------------
# 成果证据附件
# ---------------------------------------------------------------------------


class ResultEvidence(Base):
    """成果证据附件。

    文件**实际落盘**在证据目录（见 ``services/result_submission.py`` 的
    ``EVIDENCE_DIR``），本表只存元数据（文件名、大小、校验和、相对路径）。

    ⚠️ 与 ``evidence`` 表的分工：
    - ``result_evidence`` = 成果提交的**原始附件**（文件级）
    - ``evidence`` = 统一化的**证据卡片**（业务级，可进算分集）

    两者在 **A6** 打通：成果审批通过时，把附件转换为 ``evidence`` 记录
    （``usage=scoring``，决策 D4 的 E2「转换物化」）。
    """

    __tablename__ = "result_evidence"
    __table_args__ = (
        Index("ix_result_evidence_result", "result_id"),
        UniqueConstraint("result_id", "checksum", name="uq_result_evidence_checksum"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    result_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("result.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 相对 ``EVIDENCE_DIR`` 的存储路径（不存绝对路径，避免换机失效）
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    review_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    result: Mapped[Result] = relationship(back_populates="evidences")
