"""📦 **部门配置 / 待办 / 导出 / 计划调整 —— 持久化域模型**（★ 2026-11-26 新增）。

⚠️ **为什么需要这个模块**
``routes/department.py`` / ``routes/leader.py`` / ``routes/employee.py`` 里
有一批端点**只返回结构完整的假对象**，**数据库里什么都没写**：

- ``GET  /v1/department/templates``              → 恒返回 ``[]``
- ``POST /v1/department/templates``              → 返回 ``id: 0``
- ``GET  /v1/department/acceptance-standards``   → 恒返回 ``[]``
- ``POST /v1/department/acceptance-standards``   → 返回 ``id: 0``
- ``GET  /v1/department/score-rules``            → 恒返回 ``[]``
- ``POST /v1/department/score-rules``            → 返回 ``id: 0``
- ``GET  /v1/department/todos``                  → 恒返回空分页
- ``POST /v1/department/todos/{id}/remind|read|escalate|done`` → 只回字符串
- ``GET/POST /v1/department/export-tasks``       → 返回空壳任务
- ``GET  /v1/department/department-ledgers``     → 恒返回空分页
- ``GET  /v1/leader/team-ledgers``               → 恒返回空分页
- ``GET  /v1/leader/plan-adjustments``           → 恒返回 ``[]``
- ``POST /v1/employee/plan-adjustments``         → 返回 ``id: 0``

而前端**有完整的页面和按钮**（``DeliverableTemplate.vue`` /
``AcceptanceStandard.vue`` / ``ScoreRule.vue`` / ``Todo.vue`` /
``ExportTasks.vue`` / ``DepartmentLedger.vue`` / ``PlanAdjust.vue``）
⇒ 用户点「保存」弹「已保存」，**刷新后数据全没了**。

⚠️ **表结构刻意保持「最小可用」**
本模块只实现**能真正存住、能真正查回来**的字段，
不臆造前端用不到的列 —— 避免「建了一堆没人用的字段」这种反向技术债。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _uuid

# ═══════════════════════════════════════════════════════════════════════════
# 交付物模板
# ═══════════════════════════════════════════════════════════════════════════


class DeliverableTemplateRow(Base):
    """交付物模板（部门级配置）。

    ⚠️ **org_id 是飞书 department_id（字符串）**
    与前端 ``DeliverableTemplate.orgId: string`` 一致。
    """

    __tablename__ = "deliverable_template"
    __table_args__ = (
        Index("ix_deliverable_template_org", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    org_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    template_name: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False, default="DOCUMENT")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 逗号分隔的适用场景（MONTH_PLAN / DAY_PLAN / RESULT）。
    applies_to: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_no: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    #: ENABLED / DISABLED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ENABLED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


class AcceptanceStandardRow(Base):
    """验收标准（挂在交付物模板下）。"""

    __tablename__ = "acceptance_standard"
    __table_args__ = (
        Index("ix_acceptance_standard_template", "template_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("deliverable_template.id"), nullable=False
    )
    standard_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    require_review_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_requirement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_no: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ENABLED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


class ScoreRuleRow(Base):
    """参考分规则（部门级）。"""

    __tablename__ = "score_rule"
    __table_args__ = (
        Index("ix_score_rule_org", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    org_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ENABLED", index=True)
    effective_start: Mapped[str | None] = mapped_column(String(16), nullable=True)
    effective_end: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: 规则本体（JSON）—— 前端 ``ruleJson: Record<string, unknown>``。
    rule_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


# ---------------------------------------------------------------------------
# 待办
# ---------------------------------------------------------------------------


class TodoRow(Base):
    """部门待办。

    ⚠️ **为什么不复用 ``notification`` 表**
    ``notification`` 是**飞书投递发件箱**（去重键 = 投递语义），
    而待办是**业务待办**（要有处理状态、催办次数、升级痕迹）
    ⇒ 两者生命周期不同，混用会让「投递成功」被当成「业务已处理」。
    """

    __tablename__ = "dept_todo"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: DAY_PLAN_REVIEW / RESULT_CONFIRM / APPEAL_PROCESS / EXPORT_DONE …
    scene_code: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    trigger_text: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    #: 接收人（本地 ``employee.id``）。
    receiver_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    #: MONTH_PLAN / DAY_PLAN / RESULT / APPEAL / EXPORT_TASK …
    object_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    object_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requirement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 影响说明（催办 / 升级痕迹会追加到这里）。
    impact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: UNREAD / READ / DONE / ESCALATED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNREAD", index=True)
    remind_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    route_hint: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


# ---------------------------------------------------------------------------
# 导出任务
# ---------------------------------------------------------------------------


class ExportTaskRow(Base):
    """导出任务（资料包导出）。

    ⚠️ **产物怎么存**
    这里只存**任务元信息 + 校验值**；
    真正的文件字节由 ``services/export_task_service`` 写到
    ``settings.export_output_dir`` 下的目录里，
    下载时按 ``artifact_path`` 读回 ⇒ 不往数据库塞二进制。
    """

    __tablename__ = "export_task"
    __table_args__ = (
        Index("ix_export_task_status", "status"),
        Index("ix_export_task_requester", "requested_by"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    dimension_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dimension_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    dimension_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    period_type: Mapped[str] = mapped_column(String(16), nullable=False, default="ALL")
    period_start: Mapped[str | None] = mapped_column(String(16), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: JSON 数组（PDF / WORD / ZIP）。
    formats: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    include_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    watermark: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    #: COMPLETE / INCOMPLETE / PENDING
    integrity_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    missing_items: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: SUCCESS / PROCESSING / FAILED / NEEDS_REVIEW / EXPIRED
    #: ⚠️ **不要写 ``index=True``** —— 下面 ``__table_args__`` 里已显式建了
    #: ``ix_export_task_status``；两处都建会**同名冲突**，在
    #: ``Base.metadata.create_all()`` 时直接抛
    #: ``OperationalError: index ix_export_task_status already exists``。
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROCESSING")
    size_text: Mapped[str] = mapped_column(String(32), nullable=False, default="0 B")
    #: ⚠️ 同上：索引名由 ``__table_args__`` 统一提供，勿重复 ``index=True``。
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 产物落盘路径（相对 ``settings.export_output_dir``）。
    artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


# ---------------------------------------------------------------------------
# 计划调整
# ---------------------------------------------------------------------------


class PlanAdjustmentRow(Base):
    """计划调整申请。

    ⚠️ **流转**
    员工发起 → ``PENDING``；
    直属领导处理 → ``PAUSED``（暂停原计划）或 ``CANCELED``（撤销原计划）。
    """

    __tablename__ = "plan_adjustment"
    __table_args__ = (
        Index("ix_plan_adjustment_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: 调整单号（``ADJ-YYYYMM-0001``）。
    adjustment_no: Mapped[str] = mapped_column(String(32), nullable=False, default="", index=True)
    #: MONTH / WEEK / DAY
    original_plan_type: Mapped[str] = mapped_column(String(16), nullable=False, default="MONTH")
    original_plan_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    original_plan_no: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    original_work_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    new_plan_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    new_plan_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    new_plan_no: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 归属人（本地 ``employee.id``）。
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    #: PAUSE / CANCEL
    adjustment_type: Mapped[str] = mapped_column(String(16), nullable=False, default="PAUSE")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    impact_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    operation_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: PENDING / PAUSED / CANCELED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    keep_evidence_chain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    operator_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


class PlanAdjustmentNoRow(Base):
    """计划调整单号序列。

    ⚠️ **为什么要单独一张表**
    单号要求**单调递增且不重复**（``ADJ-202611-0001``）。
    用 ``count()`` 会被并发写坏 ⇒ 必须有独立序列行。
    """

    __tablename__ = "plan_adjustment_seq"
    __table_args__ = (
        UniqueConstraint("period", name="uq_plan_adjustment_seq_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 形如 ``202611``。
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = [
    "AcceptanceStandardRow",
    "DeliverableTemplateRow",
    "ExportTaskRow",
    "PlanAdjustmentNoRow",
    "PlanAdjustmentRow",
    "ScoreRuleRow",
    "TodoRow",
]
