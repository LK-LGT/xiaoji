"""配置域 + 考核域 + 证据域 + 输出域的 SQLAlchemy 模型。

设计要点（与《框架设计》一一对应）：

1. **唯一事实源**：业务库是唯一权威。飞书、多维表格、Excel 只是输入或输出通道。
2. **冻结快照**：AssessmentTask 启动时把 Metric/WeightSet/Rubric/FormTemplate
   整体复制进 TaskSnapshot（JSON），此后本次考核一切计算只引用快照。
   中途 HR 改配置，不影响已在评的分数。
3. **usage 字段**：Evidence.usage = reference_only 的证据（考勤异常）
   永远不进 MetricValue 算分集 —— 这是决策②的物理保证。
4. **Score 与 RatingDraft 分离**：Score 是规则引擎算出的唯一真值，
   不接受 AI 写入；RatingDraft 是 AI 建议草稿，两者物理隔离。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# 组织域
# ---------------------------------------------------------------------------


class Department(Base):
    """部门。同步源为飞书通讯录，落到本地做快照（考核期间人员变动不乱历史）。"""

    __tablename__ = "department"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    leader_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    last_seen_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Employee(Base):
    """员工。直属上级以飞书为准，落到本地做快照。"""

    __tablename__ = "employee"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_open_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    feishu_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    feishu_union_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    login_account: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    account_status: Mapped[str] = mapped_column(String(32), default="enabled", nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    department_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("department.id"), nullable=True)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("employee.id"), nullable=True)
    employment_status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    joined_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 兼岗 / 虚拟组等飞书表达不清的关系，由 HR 补一张对应表（见方案第八节）
    special_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    last_seen_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmployeeDepartment(Base):
    """员工部门关联。一个员工可属于多个飞书部门，第一条作为主部门。"""

    __tablename__ = "employee_department"
    __table_args__ = (
        UniqueConstraint("employee_id", "department_id", name="uq_employeedept"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    department_id: Mapped[str] = mapped_column(String(64), ForeignKey("department.id"), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class FeishuIdentity(Base):
    """飞书身份映射。

    同一身份值只能绑定一个有效员工；换绑保留历史行并关闭旧绑定。
    """

    __tablename__ = "feishu_identity"
    __table_args__ = (
        UniqueConstraint("identity_type", "identity_value", name="uq_feishu_identity_value"),
        Index("ix_feishu_identity_employee_active", "employee_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_value: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    bound_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bound_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class HrDepartmentConfig(Base):
    """HR 部门配置。成员属于任一启用的 HR 部门时自动获得 HR 角色。"""

    __tablename__ = "hr_department_config"
    __table_args__ = (
        UniqueConstraint("department_id", name="uq_hrdepartmentconfig"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    department_id: Mapped[str] = mapped_column(String(64), ForeignKey("department.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class SystemAdmin(Base):
    """系统管理员显式授权。管理员仅管理配置，不获得业务数据范围。"""

    __tablename__ = "system_admin"
    __table_args__ = (
        UniqueConstraint("employee_id", name="uq_systemadminemployee"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class OrganizationSyncBatch(Base):
    """飞书组织架构同步批次。"""

    __tablename__ = "organization_sync_batch"
    __table_args__ = (
        Index("ix_org_sync_batch_status_started", "status", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), default="full", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    departments_synced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    departments_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    departments_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    employees_synced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    employees_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    employees_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reconciliation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class OrganizationSyncCursor(Base):
    """增量同步游标。每种资源独立保存成功推进的位置。"""

    __tablename__ = "organization_sync_cursor"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    cursor_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_batch_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("organization_sync_batch.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


class OrganizationSyncItemError(Base):
    """组织同步的单条失败记录，用于定位和重试。"""

    __tablename__ = "organization_sync_item_error"
    __table_args__ = (
        Index("ix_org_sync_error_retry", "status", "next_retry_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(String(64), ForeignKey("organization_sync_batch.id"), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class OrganizationSyncAudit(Base):
    """同步操作审计，不保存 Token、Secret 或完整敏感响应。"""

    __tablename__ = "organization_sync_audit"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    batch_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("organization_sync_batch.id"), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[str] = mapped_column(String(32), default="ok", nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class FeishuIdentityAudit(Base):
    """身份映射变更审计，保留变更前后值。"""

    __tablename__ = "feishu_identity_audit"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    identity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    identity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    identity_value: Mapped[str | None] = mapped_column(String(128), nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_value_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ScheduledJobLock(Base):
    """定时任务分布式锁。

    多实例部署时保证同一个定时任务在同一时刻只有一个实例执行。
    锁带过期时间：持有者异常退出后，锁过期即可被其他实例抢占，
    避免任务永久卡死。锁表由 Alembic 迁移创建。
    """

    __tablename__ = "scheduled_job_lock"

    job_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    locked_by: Mapped[str] = mapped_column(String(128), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


# ---------------------------------------------------------------------------
# 配置域
# ---------------------------------------------------------------------------


class Metric(Base):
    """指标定义。

    口径字段（calculation / data_source / owner）必须非空 —— 决策③读入校验项之一。
    """

    __tablename__ = "metric"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 计算方式（口径），如 "gmv_actual / gmv_target"
    calculation: Mapped[str] = mapped_column(Text, nullable=False)
    # 数据来源标识：excel_import / api:ecommerce / api:cs ...
    data_source: Mapped[str] = mapped_column(String(128), nullable=False)
    # 口径说明：给主管看的解释文本
    caliber: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 数据责任人
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 是否为考勤类指标 —— 考勤类一律 reference_only
    is_attendance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


class WeightSet(Base):
    """权重集：部门 × 岗位 → 权重明细。

    明细存 JSON：{"weights": [{"metric_code": "gmv", "weight": 40}, ...]}
    合计必须 = 100 —— 决策③读入校验项之一。
    """

    __tablename__ = "weight_set"
    __table_args__ = (UniqueConstraint("department_id", "position", name="uq_weightscope"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    department_id: Mapped[str] = mapped_column(String(64), ForeignKey("department.id"), nullable=False)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    weights_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Rubric(Base):
    """评分标准（等级矩阵）。

    levels_json: [{"level": "A", "min_ratio": 1.05, "max_ratio": null,
                    "desc": "超额5%以上", "weight_in_dim": 100}, ...]
    """

    __tablename__ = "rubric"
    __table_args__ = (UniqueConstraint("weight_set_id", "metric_code", name="uq_rubricitem"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    weight_set_id: Mapped[str] = mapped_column(String(64), ForeignKey("weight_set.id"), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(64), nullable=False)
    levels_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class FormTemplate(Base):
    """表单定义 —— 一份定义双端渲染（飞书审批控件 / H5 页面）。

    fields_json: [{"key": "...", "label": "...", "type": "text|number|attachment|rating",
                   "required": true, "metric_code": "gmv", "explanation": "...",
                   "follow_up_rule": {...}}]
    """

    __tablename__ = "form_template"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    department_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("department.id"), nullable=True)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    fields_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ConfigSnapshot(Base):
    """配置冻结快照（决策③的第二道保险：冻结快照）。

    考核任务启动时把配置整体复制进来，存 JSON。task_id 唯一。
    """

    __tablename__ = "config_snapshot"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    metrics_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    weights_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    rubric_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    form_template_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ConfigSyncLog(Base):
    """配置同步日志（决策③的读入校验 + 失败兜底的记录载体）。"""

    __tablename__ = "config_sync_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ---------------------------------------------------------------------------
# 考核域
# ---------------------------------------------------------------------------


class AssessmentTask(Base):
    """考核任务。period + scope 维度隔离，与试点部门解耦（决策⑤）。"""

    __tablename__ = "assessment_task"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    department_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("department.id"), nullable=True)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    rating_policy: Mapped[str] = mapped_column(String(32), default="reference_only", nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("config_snapshot.id"), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssessmentRecord(Base):
    """员工考核记录（一次考核 × 一个员工）。"""

    __tablename__ = "assessment_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_task.id"), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="not_started", nullable=False)
    # 填报入口来源（决策：只能从一个入口提交，另一端只读）
    submit_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    submit_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 最终确认的等级（人工确认后写入，不由 AI 决定）
    confirmed_rating: Mapped[str | None] = mapped_column(String(8), nullable=True)
    
    # 自评内容
    self_review_content: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    
    # 主管评分
    manager_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    task: Mapped[AssessmentTask] = relationship()
    employee: Mapped[Employee] = relationship()


class SelfReview(Base):
    """自评。answers_json 落标准表（两个入口共用）。"""

    __tablename__ = "self_review"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    answers_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    attachments_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    submit_source: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 审批事件去重键：approval_instance_id + event_id
    dedup_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)


class ManagerReview(Base):
    """主管评分 + 部门负责人复核意见（决策①：复核是评分审批的第二级节点）。"""

    __tablename__ = "manager_review"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    scores_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(32), default="manager", nullable=False)
    # 部门负责人复核：只读评分项 + 填复核意见
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Appeal(Base):
    """申诉（决策①的第三套审批）。含理由、证据、受理人、复核人、时限。"""

    __tablename__ = "appeal"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    acceptor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="submitted", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class ApprovalEvent(Base):
    """飞书审批事件去重表（幂等：按 approval_instance_id + event_id 去重）。"""

    __tablename__ = "approval_event"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_approvalevent"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ---------------------------------------------------------------------------
# 证据域
# ---------------------------------------------------------------------------


class AttendanceRecord(Base):
    """考勤打卡原始记录（飞书考勤同步落库）。

    定位：**原始事实层**，不是算分集。
    - 每个「员工 × 日期 × 班段」一条记录（task_shift_type 区分正常/加班班段）。
    - 保存飞书返回的原始打卡结果与打卡明细，便于复算与追溯。
    - `anomaly_types` 是本地的六类异常归类结果（late/early_leave/missing_punch/
      leave/overtime/outside），由 `attendance_sync` 依据飞书 check_result 归类。
    - 本表的任何数据都**不得**直接进入 MetricValue 算分集；
      只能经 `evidence_service.record_attendance_anomalies` 转成
      usage=reference_only 的 Evidence 证据。

    唯一键用飞书的 result_id + 班段序号，保证重复同步幂等。
    """

    __tablename__ = "attendance_record"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "work_date", "shift_seq", name="uq_attendancerecord"
        ),
        Index("ix_attendancerecord_employee_date", "employee_id", "work_date"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    # 工作日（YYYY-MM-DD），由飞书 day(yyyyMMdd) 转换而来
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # 同一员工同一天的班段序号（0=正常班段，1=加班班段...）
    shift_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 飞书原始班段类型：0=正常班段，1=加班班段
    task_shift_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 飞书考勤结果 ID（result_id），用于幂等与追溯
    feishu_result_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 考勤组 ID / 班次 ID
    attendance_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shift_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 上班/下班打卡结果（飞书 check_result 原值）
    check_in_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    check_out_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    check_in_result_supplement: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_out_result_supplement: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 班次规定上下班时间（epoch 秒）
    check_in_shift_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_out_shift_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 实际打卡时间（epoch 秒）
    check_in_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_out_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 打卡明细 ID（飞书 record_id）
    check_in_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_out_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 打卡位置 / 是否外勤 / 是否 Wi-Fi 打卡
    check_in_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    check_out_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_field: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_wifi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 打卡方式（飞书 type：0=用户打卡 1=管理员修改 2=用户补卡 ...）
    check_in_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_out_type: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 六类异常归类结果（JSON 数组，取值见 AttendanceAnomalyType）
    anomaly_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 数据截至时间：考勤有延迟与补卡回溯，必须标注 as_of
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 原始响应留档，便于口径变更后重算
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class MetricValue(Base):
    """已归一化的指标值 —— 算分集的唯一入口。

    只有 source 不为 attendance 的记录才进入本表（考勤 → Evidence[reference_only]）。
    """

    __tablename__ = "metric_value"
    __table_args__ = (UniqueConstraint("employee_id", "metric_code", "period", name="uq_metricvalue"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    metric_code: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 数据截至时间（考勤有延迟与补卡回溯，必须带 as_of 标注）
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Evidence(Base):
    """证据卡片 —— 统一化的证据载体。

    usage = reference_only 的证据（考勤异常）永远不进 MetricValue 算分集。
    """

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    record_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual_upload")
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    usage: Mapped[str] = mapped_column(String(32), default="scoring", nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), default="share_with_manager", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="approved", nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    concern: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 考勤类专用：异常类型、次数、是否已审批
    anomaly_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    anomaly_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ---------------------------------------------------------------------------
# 输出域
# ---------------------------------------------------------------------------


class Score(Base):
    """规则引擎算出的分数 —— 唯一真值。**不接受 AI 写入。**

    per_metric_json 保留每项明细，便于复算与追溯。
    trace_json 记录算分过程（口径、权重、目标值），供审计。
    """

    __tablename__ = "score"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    per_metric_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class RatingDraft(Base):
    """AI 评级建议草稿。必须人工确认后才可生效。"""

    __tablename__ = "rating_draft"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    ratings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    citations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    gaps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    fairness_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class CommentDraft(Base):
    """AI 评语草稿。citations 可回溯到 Evidence。"""

    __tablename__ = "comment_draft"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Rollup(Base):
    """汇总（员工 → 部门 → 公司）。数字部分全部来自 Score。"""

    __tablename__ = "rollup"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ---------------------------------------------------------------------------
# 治理域
# ---------------------------------------------------------------------------


class AiTrace(Base):
    """每次 AI 调用留痕：模型、prompt 版本、输入哈希、输出、耗时、状态。"""

    __tablename__ = "ai_trace"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    agent: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ok", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class AuditLog(Base):
    """关键操作留痕（含越权拒绝）。"""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(32), default="ok", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
