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
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
    # ⚠️ 四个飞书身份列**都**要唯一。
    #    `migrations/versions/0002_phase2_feishu.py` 明确声明了
    #    uq_employee_feishu_user_id / uq_employee_feishu_union_id /
    #    uq_employee_login_account / uq_employee_feishu_open_id 四个约束，
    #    但 ORM 这里原先只声明了三个 —— 漏了 feishu_open_id。
    #    后果：`feishu_approval_sync.resolve_employee_by_feishu_id` 用
    #    ``scalar_one_or_none()`` 按 open_id 查人，重复行会直接抛 500。
    feishu_open_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    feishu_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    feishu_union_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    login_account: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    account_status: Mapped[str] = mapped_column(String(32), default="enabled", nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    # ── 密码哈希（R4）───────────────────────────────────────────────────
    # 历史上所有员工的密码是**明文 "123456"**，等于没设密码。
    # 现在改为存 PBKDF2-HMAC-SHA256 哈希（见 services/password_hasher.py）。
    #
    # ⚠️ `password_hash` 为 NULL 表示「还没设过密码」——
    #    这类账号**不允许**用默认口令登录，必须走改密流程。
    #    登录时若发现 hash 为空但命中了兼容明文，会**立即补写哈希**（渐进迁移）。
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 最近一次设置密码的时间（改密成功后更新）。
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 是否强制该用户下次登录后改密（管理员重置密码 / 首登时置 True）。
    force_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )
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

    department: Mapped[Department | None] = relationship()


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


class Delegation(Base):
    """代理授权（主管请假/外出时的临时委托，P1-4）。

    委托人在 ``starts_at`` ~ ``ends_at`` 期间，把**指定范围**的管理权限
    临时交给受托人。范围由 ``scope_type`` + ``scope_value`` 决定：

    - ``direct_reports``：委托人原有的直属下属（最常用）
    - ``department``：委托人管理的整个部门
    - ``explicit``：``scope_value`` 里逗号分隔的员工 ID 白名单

    ⚠️ **有效期是硬闸门**：``ends_at`` 之前有效，之后自动失效，
    不依赖任何后台任务去「撤销」—— 时间一到权限自然消失。
    """

    __tablename__ = "delegation"
    __table_args__ = (
        UniqueConstraint(
            "delegator_id", "delegate_id", "scope_type", "scope_value",
            name="uq_delegation_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # 委托人（请假的主管）
    delegator_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    # 受托人（代为主管）
    delegate_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    scope_type: Mapped[str] = mapped_column(String(32), default="direct_reports", nullable=False)
    # department 时为部门 ID；explicit 时为逗号分隔的员工 ID
    scope_value: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    reason: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 是否已撤销（到期前主动收回）
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class DataSourcePolicy(Base):
    """数据源白名单（P1-4 授权边界的另一侧：数据可读性）。

    HR 配置「哪些数据源可以进入算分 / 证据链」以及「哪些字段需要脱敏」。

    - ``source_code``：数据源标识（如 ``attendance`` / ``feishu_approval`` / ``hr_core``）
    - ``allowed``：是否允许进入算分集（``usage=scoring``）
    - ``allowed_roles``：哪些角色可以读取该源（逗号分隔，如 ``hr,manager``）
    - ``redacted_fields``：需要脱敏的字段（逗号分隔）
    - ``note``：配置说明 / 依据

    ⚠️ **默认不信任**：没有配置的数据源**不可读**。
    这是白名单语义 —— 与「默认全放行」相比，漏配会导致「数据没进来」
    （可见、可修），而不是「数据泄露了」（不可见、不可修）。
    """

    __tablename__ = "data_source_policy"
    __table_args__ = (
        UniqueConstraint("source_code", name="uq_datasourcepolicy_code"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # 是否允许进入算分集
    allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 可读角色（逗号分隔）；为空表示仅 HR
    allowed_roles: Mapped[str] = mapped_column(String(256), default="hr", nullable=False)
    # 需要脱敏的字段（逗号分隔）
    redacted_fields: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    note: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )


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
    # 幂等键：相同 key 的重复触发直接复用同一批次，避免重复同步。
    # 形如 manual:{actor}:{mode}:{window} / scheduled:{window}。
    idempotency_key: Mapped[str | None] = mapped_column(
        String(160), nullable=True, unique=True, index=True
    )
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
    # 异步执行时的阶段标记，供前端展示进度：
    # pending / running / finished。
    phase: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 人类可读的当前阶段描述（如「正在拉取部门树」）。
    phase_detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 错误分级：retryable / config / manual / permanent / unknown。
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OrganizationSyncCursor(Base):
    """增量同步游标。每种资源独立保存成功推进的位置。"""

    __tablename__ = "organization_sync_cursor"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # ⚠️ 长度 128 —— 增量同步会写入「员工:<64位部门ID>」这种复合键
    #    （按部门粒度记录分页续传位置）。32 装不下。
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    cursor_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_batch_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("organization_sync_batch.id"), nullable=True)
    # 上次成功推进的逻辑位置（部门 ID / 员工 ID）。
    # 拉取按位置续传时从这里恢复；为空表示从头开始。
    checkpoint_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 上次全量快照是否完整。完整快照才能判定「远端已删除」。
    snapshot_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


class OrgChangeQueue(Base):
    """组织架构变更事件队列（R18 事件驱动增量）。

    飞书通讯录事件（``contact.user.*`` / ``contact.department.*``）
    推送到 webhook 后，**先落库成队列项**，再由后台消费者按队列增量 upsert。

    ⚠️ **为什么要有队列，而不是在 webhook 里直接改数据**：
    - 飞书要求 webhook **快速返回**，直接做业务会超时 → 飞书反复重推；
    - 事件可能**乱序**到达（先删后建、先改名后移动），需要串行处理；
    - 处理失败要能**重试**，不能丢。

    ⚠️ **事件不是唯一数据源**：
    飞书事件推送**可能丢失**（网络、平台故障）。所以本表只是「快路径」，
    现有分页游标全量对账仍是**兜底**，保证最终一致。
    绝不能只靠事件 —— 丢一个事件就永久丢一条数据。
    """

    __tablename__ = "org_change_queue"
    __table_args__ = (
        # ⚠️ 取待处理记录的主要索引：按状态过滤 + 按创建时间排序。
        Index("ix_org_change_queue_pending", "status", "created_at"),
        # 去重键：同一资源同一事件重复推送时只留一条。
        Index("ix_org_change_queue_dedup", "dedup_key", "created_at"),
        # ⚠️ **去重的数据库级兜底**（PostgreSQL 关键）。
        #
        # 应用层的「查重 → 插入」是 check-then-act，两个并发 webhook
        # 可能同时查不到、都去插入 → 重复队列项 → 同一变更被消费两次。
        #
        # SQLite 有全局写锁，窗口被掩盖；PostgreSQL 没有，
        # 必须靠数据库约束兜底。
        #
        # 为什么是**部分唯一索引**（``WHERE status IN (...)``）：
        # 去重只应作用于「**未完成**」的记录 —— 一条已处理的记录
        # 不该挡住该资源**下一次**变更（否则第二次变更会被永久丢弃）。
        # 部分唯一索引正好表达这个语义：只对未完成的行生效。
        Index(
            "uq_org_change_queue_active_dedup",
            "dedup_key",
            unique=True,
            # ⚠️ 这里**必须用字面量 SQL 文本**，不能用 ``status.in_(...)``。
            #
            # 原因：``__table_args__`` 在类体求值时执行，此时 ``status``
            # 还没被赋值（列声明在 ``__table_args__`` 之后）——
            # 写成 Python 表达式会直接抛 ``NameError: name 'status' is not defined``，
            # 整个应用**起不来**。
            #
            # 部分唯一索引的 ``WHERE`` 子句是**原始 SQL**，
            # 不经过 SQLAlchemy 的表达式解析，因此这里只能写字符串。
            sqlite_where=text("status IN ('pending', 'processing', 'failed')"),
            postgresql_where=text("status IN ('pending', 'processing', 'failed')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # 事件类型：user_created / user_updated / user_deleted /
    #           department_created / department_updated / department_deleted。
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 资源类型：user / department。
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 资源 ID（飞书 user_id / department_id）。
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # pending / processing / processed / failed / dead_letter。
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    # 重试次数；超过上限进死信。
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 下次可处理时间（退避）。
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ⚠️ 去重键 —— 同一资源同一类型在**很短时间窗内**的重复推送只处理一次。
    #    飞书在重推时会带同一个 event_id，用它最精准；
    #    没有 event_id 时退化为「资源+类型+时间片」。
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    # 事件原始载荷（已脱敏，不含 Token/Secret）。
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 关联的同步批次（处理时产生的批次，可为空 —— 事件级增量不一定产生批次）。
    batch_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("organization_sync_batch.id"), nullable=True, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


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
    # 错误分级，决定是否自动重试：
    # retryable（限流/5xx/超时）/ config（权限或配置）/
    # manual（身份冲突、需人工介入）/ permanent（数据非法）/ unknown。
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
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


class OrganizationSyncAlert(Base):
    """同步失败告警。

    与 ``organization_sync_audit`` 的区别：审计记录「做了什么」，
    告警记录「哪里坏了、需要人处理」。

    设计要点：

    - **去重**：同一个 ``dedup_key`` 在 ``suppressed_until`` 之前不再产生
      新告警，避免「每轮定时同步都失败」刷屏。
    - **分级**：severity=error 需要立即处理；warning 可稍后处理。
    - **可恢复**：``resolved_at`` 非空表示问题已解决（恢复时补一条 info 告警）。
    """

    __tablename__ = "organization_sync_alert"
    __table_args__ = (
        Index("ix_org_sync_alert_status_created", "status", "created_at"),
        Index("ix_org_sync_alert_dedup", "dedup_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # 告警类型：batch_failed / batch_partial_failed / item_error_backlog /
    # scheduler_stopped / scheduler_consecutive_failures / sync_recovered。
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # info / warning / error。
    severity: Mapped[str] = mapped_column(String(16), default="error", nullable=False, index=True)
    # open / acknowledged / resolved。
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    batch_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("organization_sync_batch.id"), nullable=True, index=True
    )
    # 去重键：同一问题在抑制窗口内只告警一次。
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    # 抑制截止时间：在此之前同 dedup_key 不再产生新告警。
    suppressed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 通知投递状态：pending / sent / failed / disabled。
    notified_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class FeishuIdentityAudit(Base):

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
# 计划域
#
# 三层计划：月 → 周 → 日，逐层细化。
#
# 设计要点：
# 1. **层级归属靠外键**：周计划条目可关联月计划条目，日计划条目可关联周计划条目，
#    形成 月→周→日 的下钻链路。
# 2. **乐观锁**：每张计划表带 `version_no`，提交/撤回时校验前端传来的版本号，
#    不匹配直接 409，避免并发覆盖。
# 3. **状态机统一**：三层共用 `PlanStatus` 枚举，流转规则集中在
#    `services/planning.py`，不在路由里散落 if-else。
# 4. **owner_id 冗余**：计划归属人冗余存一份，避免每次列表都 join employee 表，
#    同时让「我的计划」查询走单表索引。
# ---------------------------------------------------------------------------


class MonthPlan(Base):
    """月计划。一名员工一个月的计划容器。"""

    __tablename__ = "month_plan"
    __table_args__ = (
        UniqueConstraint("owner_id", "plan_month", name="uq_month_plan_owner_month"),
        Index("ix_month_plan_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    plan_month: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    # 乐观锁版本号：每次内容变更 +1，提交/撤回时由前端回传做校验
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    # 项目 / 岗位：月计划通常挂在具体项目上
    project_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 结果状态：not_submitted / draft / submitted / confirmed / rejected
    result_status: Mapped[str] = mapped_column(String(32), default="not_submitted", nullable=False)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, server_default="0")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    owner: Mapped[Employee] = relationship()
    items: Mapped[list[MonthPlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="MonthPlanItem.sort_no"
    )


class MonthPlanItem(Base):
    """月计划条目。一条待办任务，是周计划条目的父级。"""

    __tablename__ = "month_plan_item"
    __table_args__ = (
        Index("ix_month_plan_item_plan_sort", "plan_id", "sort_no"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("month_plan.id"), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(32), default="REGULAR", nullable=False)
    task_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    task_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    deliverable: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quality: Mapped[str] = mapped_column(Text, nullable=False, default="")
    performance_weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, server_default="0")
    estimated_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, server_default="0")
    estimated_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    progress: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    sort_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    plan: Mapped[MonthPlan] = relationship(back_populates="items")


class WeekPlan(Base):
    """周计划。一名员工一周的计划容器。"""

    __tablename__ = "week_plan"
    __table_args__ = (
        Index("ix_week_plan_owner_start", "owner_id", "week_start"),
        Index("ix_week_plan_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approve_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    owner: Mapped[Employee] = relationship()
    items: Mapped[list[WeekPlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="WeekPlanItem.sort_no"
    )


class WeekPlanItem(Base):
    """周计划条目。"""

    __tablename__ = "week_plan_item"
    __table_args__ = (
        Index("ix_week_plan_item_plan_sort", "plan_id", "sort_no"),
        Index("ix_week_plan_item_month_item", "month_plan_item_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("week_plan.id"), nullable=False, index=True)
    # 父级月计划条目：周计划由月计划下钻而来
    month_plan_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("month_plan_item.id"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    deliverable: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_finish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completion_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    plan: Mapped[WeekPlan] = relationship(back_populates="items")
    month_item: Mapped[MonthPlanItem | None] = relationship()


class DayPlan(Base):
    """日计划。一名员工某一天的计划容器。"""

    __tablename__ = "day_plan"
    __table_args__ = (
        UniqueConstraint("owner_id", "plan_date", name="uq_day_plan_owner_date"),
        Index("ix_day_plan_owner_date", "owner_id", "plan_date"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("employee.id"), nullable=False, index=True)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remark: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    # 审批与风控字段
    review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    leader_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    department_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    department_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    owner: Mapped[Employee] = relationship()
    items: Mapped[list[DayPlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="DayPlanItem.sort_no"
    )


class DayPlanItem(Base):
    """日计划条目。关联到周计划条目（日计划由周计划下钻而来）。"""

    __tablename__ = "day_plan_item"
    __table_args__ = (
        Index("ix_day_plan_item_plan_sort", "plan_id", "sort_no"),
        Index("ix_day_plan_item_week_item", "week_plan_item_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("day_plan.id"), nullable=False, index=True)
    week_plan_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("week_plan_item.id"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    plan: Mapped[DayPlan] = relationship(back_populates="items")


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
    self_review_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    manager_review_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssessmentRecord(Base):
    """员工考核记录（一次考核 × 一个员工）。"""

    __tablename__ = "assessment_record"
    # ⚠️ 一次考核 × 一个员工只能有一条记录 —— 迁移 0001_baseline.py 已声明
    #    uq_assessmentrecord_task_employee，但 ORM 漏了 → 两条建库路径分叉。
    __table_args__ = (
        UniqueConstraint("task_id", "employee_id", name="uq_assessmentrecord_task_employee"),
    )

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
    # ⚠️ 迁移 0001_baseline.py 声明了 uq_self_review_record（record_id 唯一），
    #    ORM 漏了 → 两条建库路径分叉。重复自评行会让按 record_id 的
    #    `scalar_one_or_none()` 查询直接 500。
    __table_args__ = (
        UniqueConstraint("record_id", name="uq_self_review_record"),
    )

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
    # ⚠️ 迁移 0001_baseline.py 声明了 uq_manager_review_record（record_id 唯一），
    #    ORM 漏了 → 两条建库路径分叉。
    __table_args__ = (
        UniqueConstraint("record_id", name="uq_manager_review_record"),
    )

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
    # ⚠️ 一条考核记录只能有一份分数（幂等重算走 UPDATE，不新增行）。
    #    `migrations/versions/0001_baseline.py` 早已声明 `uq_score_record`，
    #    但 ORM 模型漏了 —— 导致 `Base.metadata.create_all` 建出来的库
    #    **没有**这个约束，与 Alembic 建库不一致（真实运行库就是这个漂移的受害者）。
    #    并发算分会写出重复行，之后所有 `scalar_one_or_none()` 查询都会 500。
    __table_args__ = (UniqueConstraint("record_id", name="uq_score_record"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    per_metric_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    trace_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class RatingDraft(Base):
    """AI 评级建议草稿。必须人工确认后才可生效。"""

    __tablename__ = "rating_draft"
    # ⚠️ 迁移 0001_baseline.py 声明了 uq_rating_draft_record（record_id 唯一），
    #    ORM 漏了 → 两条建库路径分叉。
    __table_args__ = (
        UniqueConstraint("record_id", name="uq_rating_draft_record"),
    )

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
    # ⚠️ 迁移 0001_baseline.py 声明了 uq_comment_draft_record（record_id 唯一），
    #    ORM 漏了 → 两条建库路径分叉。
    __table_args__ = (
        UniqueConstraint("record_id", name="uq_comment_draft_record"),
    )

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
    # ⚠️ 迁移 0001_baseline.py 声明了 uq_rollup_scope_period
    #    (scope, scope_id, period) 唯一，ORM 漏了 → 两条建库路径分叉。
    __table_args__ = (
        UniqueConstraint("scope", "scope_id", "period", name="uq_rollup_scope_period"),
    )

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


class AiToolCall(Base):
    """工具调用审计，仅记录参数哈希与键名，不存凭据或业务正文。"""

    __tablename__ = "ai_tool_call"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tool: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    args_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class AiFeedback(Base):
    """P2-3 人工反馈 —— A/B 灰度回流的数据来源。

    反馈是**人工产出**的判断（采纳/修改/拒绝草稿），因此它是灰度
    对比的唯一可信依据 —— 模型自己说「我觉得好」不算数。

    ⚠️ 只存反馈**结论**与**差异摘要**，不存评语正文 ——
    正文可能含个人信息，留在 ``CommentDraft`` 里已有出处可查。
    """

    __tablename__ = "ai_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String(64), ForeignKey("assessment_record.id"), nullable=False, index=True)
    agent: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: ACCEPT（采纳）/ MODIFY（修改后采纳）/ REJECT（拒绝）
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 反馈人
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 差异摘要（改了什么），不存正文
    diff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 反馈理由
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 评分维度（可选）：这条反馈针对哪个指标
    dimension: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False, index=True)


class PromptVersion(Base):
    """P2-3 Prompt 版本与灰度配置。

    灰度模型（``rollout``）：
    - ``off``：完全不用该版本；
    - ``canary``：按 ``canary_ratio`` 比例随机命中（默认桶固定，避免抖动）；
    - ``full``：全量。

    ⚠️ 命中是**确定性**的 —— 同一 ``record_id`` 永远落到同一个桶。
    否则同一条记录在两次生成里用不同 prompt，灰度对比就失去意义。
    """

    __tablename__ = "prompt_version"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    agent: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 完整 prompt 文本（不含凭据）
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: off / canary / full
    rollout: Mapped[str] = mapped_column(String(32), default="off", nullable=False)
    #: 灰度比例 0.0 ~ 1.0
    canary_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    #: 灰度基准版本（对照组）
    baseline_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("agent", "version", name="uq_promptversion_agent_version"),
    )


class CaseLibraryEntry(Base):
    """P2-3 案例库 —— 已定稿的绩效处理案例，供后续参考与培训。

    ⚠️ 入库门槛：必须是**已确认**的结论（``confirmed_rating`` 有值），
    且必须**脱敏** —— 不存员工姓名、不存可识别的评语正文。
    案例的用途是「参考做法」，不是「查某人历史」。
    """

    __tablename__ = "case_library_entry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    #: 场景分类：veto / low_score / dispute / promotion / calibration ...
    scenario: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 结论等级（已确认）
    rating: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: 评分区间描述（如 "40-60"）
    score_range: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 事实摘要（脱敏后的结构化描述）
    facts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 处理要点（人工撰写）
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    #: 来源记录 ID（可回溯，但不暴露员工身份）
    source_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 是否已脱敏（入库时强制为 True）
    redacted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 被复用次数（用于排序热度）
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False, index=True)


# ---------------------------------------------------------------------------
# 计划 / 争议 / AI / 成果 / 工作日规则域模型
#
# 放在文件末尾导入，保证 `Base.metadata` 收集到这些表：
# - `Base.metadata.create_all()` 能建出它们；
# - Alembic 的 `target_metadata` 能自动发现它们（autogenerate 不会漏表）。
#
# `plan_models` / `result_models` / `workday_models` / `bot_models` 反过来 import 本模块的
# `Base` / `Employee` / `_uuid` / `_now`，因此必须在本模块**定义完成之后**
# 再导入，否则会循环引用。
# ---------------------------------------------------------------------------

from . import baidu_pan_models as _baidu_pan_models  # noqa: E402,F401
from . import bot_models as _bot_models  # noqa: E402,F401
from . import feishu_scheme_models as _feishu_scheme_models  # noqa: E402,F401
from . import notification_models as _notification_models  # noqa: E402,F401
from . import pan_workload_models as _pan_workload_models  # noqa: E402,F401
from . import plan_models as _plan_models  # noqa: E402,F401
from . import result_models as _result_models  # noqa: E402,F401
from . import review_models as _review_models  # noqa: E402,F401
from . import scheme_models as _scheme_models  # noqa: E402,F401
from . import workday_models as _workday_models  # noqa: E402,F401

__all__ = [name for name in dir() if not name.startswith("_")]
