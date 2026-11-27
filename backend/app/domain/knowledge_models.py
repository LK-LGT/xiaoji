"""📚 **企业知识库 —— 持久化域模型**（★ 2026-11-27 新增）。

⚠️ **为什么需要这个模块**
------------------------------------------------------------------
``routes/v1/system.py`` 的 **10 个 knowledge 端点** + ``routes/leader.py``
的 **2 个 knowledge 端点**原先**全是空壳**：

============================  ==========================================
端点                            原行为
============================  ==========================================
``GET  /v1/system/knowledge/status``            恒返回 ``chunkCount: 0``
``GET  /v1/system/knowledge/documents``         恒返回 ``[]``
``GET  /v1/system/knowledge/documents/{id}``    恒返回 ``document: None``
``GET  /v1/system/knowledge/documents/{id}/history`` 恒返回 ``[]``
``POST /v1/system/knowledge/documents/{id}/versions`` 回显但**不落库**
``POST /v1/system/knowledge/documents/{id}/disable``  只回字符串
``POST /v1/system/knowledge/documents/{id}/enable``   只回字符串
``DELETE /v1/system/knowledge/documents/{id}``        只回字符串
``POST /v1/system/knowledge/sync``              恒 ``scannedCount: 0``
``GET  /v1/system/knowledge/preview``           恒返回 ``[]``
``POST /v1/leader/knowledge/upload``            返回 ``documentId: 0``
``GET  /v1/leader/knowledge/uploads``           恒返回 ``[]``
============================  ==========================================

而前端有**完整的三个页面**（``EnterpriseKnowledge.vue`` 三个 tab、
``LeaderKnowledge.vue`` 批量上传台）⇒
**上传文档弹「成功」，刷新后什么都没了**。

⚠️ **表结构设计原则**
------------------------------------------------------------------
1. **文档 / 分块 / 关系 / 版本历史**四张表 —— 与前端契约一一对应；
2. **同步任务**单独一张表 —— 同步是「可观测的异步动作」，
   必须留下「扫了多少 / 发布了多少 / 失败在哪」的痕迹；
3. **不臆造前端用不到的列** —— 避免「建了一堆没人用的字段」这种反向技术债。

⚠️ **关于「向量库」**
------------------------------------------------------------------
本系统的向量检索**不依赖外部向量数据库**（Qdrant / Milvus 等）：
分块、校验值、检索打分都落在 ``knowledge_chunk`` 表里，
用**纯 Python 的 BM25 + 字面权重**打分 ⇒ 零外部依赖、零部署负担。
向量库接入点在 :mod:`backend.app.services.knowledge_service`
的 ``vector_engine_name()`` / ``index_status`` 字段上留好了位置。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now

# ═══════════════════════════════════════════════════════════════════════════
# 知识文档
# ═══════════════════════════════════════════════════════════════════════════


class KnowledgeDocumentRow(Base):
    """知识文档（一篇文档的**当前版本**）。

    ⚠️ **版本历史的存法**
    ``knowledge_document`` 只保留**当前版本**；
    每次换版/停用/恢复/删除都往 ``knowledge_document_history`` 追加一行。
    ⇒ 「版本历史」页天然是 append-only 的，不需要额外逻辑。
    """

    __tablename__ = "knowledge_document"
    __table_args__ = (
        UniqueConstraint("knowledge_id", name="uq_knowledge_document_knowledge_id"),
        Index("ix_knowledge_document_scope", "scope_type", "scope_org_id"),
        Index("ix_knowledge_document_status", "status"),
        Index("ix_knowledge_document_source", "source_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 知识编号（对外唯一标识，形如 ``K-20261127-0001``）。
    knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    #: HISTORICAL_PLAN / JOB_DESCRIPTION / DEPARTMENT_GOAL / LEADER_REQUIREMENT /
    #: PROJECT_MATERIAL / POLICY / ACCEPTANCE_STANDARD / REFERENCE_CASE /
    #: UPLOADED_REFERENCE
    knowledge_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: DRAFT / IN_REVIEW / APPROVED / PUBLISHED / DEPRECATED / DISABLED / DELETED
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PUBLISHED")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="V1.0")
    #: 编写人（本地 ``employee.id``）。
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 审核人（本地 ``employee.id``；可空）。
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: NORMATIVE（正式规范）/ REFERENCE（参考资料）/ EXAMPLE（案例）
    binding: Mapped[str] = mapped_column(String(32), nullable=False, default="REFERENCE")
    #: JSON 数组：适用的部门 ID（飞书 department_id 字符串）。
    department_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: JSON 数组：适用的角色码。
    role_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: JSON 数组：适用角色（MANAGEMENT / EMPLOYEE）。
    audience_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: JSON 数组：适用业务（MONTH_PLAN / WEEK_PLAN / …）。
    applies_to: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: GLOBAL / DEPARTMENT / PROJECT
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, default="GLOBAL")
    #: 指定组织（部门 / 项目组 ID；``scope_type == GLOBAL`` 时为空）。
    scope_org_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 指定组织的显示名（冗余存一份，避免列表页 N+1 查组织）。
    scope_org_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    effective_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    expiry_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: 源文件相对路径（相对 ``settings.knowledge_upload_dir``）。
    source_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    #: VAULT（Obsidian Vault 同步）/ UPLOAD（页面上传）
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="UPLOAD")
    original_file_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: 内容 SHA-256 —— 幂等去重 + 篡改检测。
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 管理员手动停用（与 ``status == DISABLED`` 区分：
    #: 前者是「人为停用」，后者是「流程状态」）。
    manually_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    relation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: READY / PENDING / FAILED / NOT_REQUIRED
    index_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: JSON 数组：用过的 embedding 模型名。
    embedding_models: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)


# ═══════════════════════════════════════════════════════════════════════════
# 知识分块
# ═══════════════════════════════════════════════════════════════════════════


class KnowledgeChunkRow(Base):
    """知识分块（一篇文档切出来的段落）。

    ⚠️ **检索就发生在这张表上**
    分块 + ``checksum`` + ``heading_path`` 足以支撑 BM25 打分与引用回溯，
    **不需要外部向量库** ⇒ 零部署负担。
    """

    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        Index("ix_knowledge_chunk_document", "document_id", "chunk_no"),
        Index("ix_knowledge_chunk_checksum", "checksum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: 段序号（从 1 开始）。
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 章节路径（形如 ``一级标题 / 二级标题``）。
    heading_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 本段内容 SHA-256。
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 段落的词频表（JSON）—— 检索打分直接用，不在查询时重复分词。
    term_freq: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: 段长（字符数）—— BM25 长度归一化用。
    content_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ═══════════════════════════════════════════════════════════════════════════
# 知识关系（图谱）
# ═══════════════════════════════════════════════════════════════════════════


class KnowledgeRelationRow(Base):
    """文档之间的关系（引用 / 依赖 / 继承 …）。

    ⚠️ **为什么单独一张表而不是 JSON 列**
    关系是**双向可查**的（「谁引用了我」也是常用查询），
    放 JSON 列就只能扫全表。
    """

    __tablename__ = "knowledge_relation"
    __table_args__ = (
        Index("ix_knowledge_relation_document", "document_id"),
        Index("ix_knowledge_relation_target", "target_knowledge_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: 关系类型（REFERENCE / DEPENDS_ON / SUPERSEDES …）。
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="REFERENCE")
    #: 关系目标（对方的 ``knowledge_id``）。
    target_knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ═══════════════════════════════════════════════════════════════════════════
# 版本与操作历史
# ═══════════════════════════════════════════════════════════════════════════


class KnowledgeDocumentHistoryRow(Base):
    """知识文档的版本与操作历史（append-only）。

    ⚠️ **append-only 是刻意的**
    「谁在什么时候把哪篇文档停用了」必须**永久留痕** ——
    这是审计要求，不允许被后续操作覆盖。
    """

    __tablename__ = "knowledge_document_history"
    __table_args__ = (
        Index("ix_knowledge_history_document", "document_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="UPLOAD")
    original_file_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: CURRENT / VERSION_REPLACED / DISABLED / ENABLED / DELETED
    operation: Mapped[str] = mapped_column(String(32), nullable=False, default="CURRENT")
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    operator_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ═══════════════════════════════════════════════════════════════════════════
# 同步任务
# ═══════════════════════════════════════════════════════════════════════════


class KnowledgeSyncRunRow(Base):
    """知识库同步任务（一次同步 = 一行）。

    ⚠️ **为什么必须留痕**
    同步可能部分失败（「扫到 120 篇，发布 118 篇，失败 2 篇」）。
    没有这张表 ⇒ 管理员**无法知道**哪些文档没同步上。
    """

    __tablename__ = "knowledge_sync_run"
    __table_args__ = (
        Index("ix_knowledge_sync_run_started", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: MANUAL / SCHEDULED
    trigger_source: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    #: RUNNING / SUCCESS / PARTIAL / FAILED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    vault_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    scanned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: JSON 数组：失败原因。
    errors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


__all__ = [
    "KnowledgeChunkRow",
    "KnowledgeDocumentHistoryRow",
    "KnowledgeDocumentRow",
    "KnowledgeRelationRow",
    "KnowledgeSyncRunRow",
]
