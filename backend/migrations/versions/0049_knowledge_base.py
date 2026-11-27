"""★ 企业知识库 —— 知识文档落库（2026-11-27）。

Revision ID: 0049_knowledge_base
Revises: 0048_department_config_persistence

背景
----
``routes/v1/system.py`` 的 **10 个 knowledge 端点** +
``routes/leader.py`` 的 **2 个 knowledge 端点**原先**全是空壳**：

======================================  ==========================================
端点                                     原行为
======================================  ==========================================
``GET  /v1/system/knowledge/status``     恒 ``chunkCount: 0``
``GET  /v1/system/knowledge/documents``  恒 ``[]``
``GET  /v1/system/knowledge/documents/{id}``      恒 ``document: None``
``GET  /v1/system/knowledge/documents/{id}/history`` 恒 ``[]``
``POST /v1/system/knowledge/documents/{id}/versions`` 回显但**不落库**
``POST /v1/system/knowledge/documents/{id}/disable`` 只回字符串
``POST /v1/system/knowledge/documents/{id}/enable``  只回字符串
``DELETE /v1/system/knowledge/documents/{id}``      只回字符串
``POST /v1/system/knowledge/sync``        恒 ``scannedCount: 0`` + ``SUCCESS``
``GET  /v1/system/knowledge/preview``     恒 ``[]``
``POST /v1/leader/knowledge/upload``      恒 ``documentId: 0``
``GET  /v1/leader/knowledge/uploads``     恒 ``[]``
======================================  ==========================================

而前端**有完整的三个页面**（企业知识三 tab / 领导入库台）
⇒ **上传弹「成功」，刷新后什么都没了**。

⚠️ 表结构
---------
五张表：

=========================  ==================================================
表                         用途
=========================  ==================================================
``knowledge_document``      文档当前版本
``knowledge_chunk``         文档分块（检索就发生在这里）
``knowledge_relation``      文档间关系（图谱）
``knowledge_document_history`` 版本与操作历史（append-only）
``knowledge_sync_run``      同步任务留痕
=========================  ==================================================

⚠️ **向量不落库**
本系统的检索用**内置 BM25 索引**（纯 Python，零外部依赖），
分块 + 词频（``term_freq`` JSON）已经足够 ⇒ **不需要向量列**。
"""

import sqlalchemy as sa
from alembic import op

revision = "0049_knowledge_base"
down_revision = "0048_department_config_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 知识文档 ──────────────────────────────────────────────────
    op.create_table(
        "knowledge_document",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("knowledge_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("knowledge_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PUBLISHED"),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="V1.0"),
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("reviewer_id", sa.String(length=64), nullable=True),
        sa.Column("binding", sa.String(length=32), nullable=False, server_default="REFERENCE"),
        sa.Column("department_ids", sa.JSON(), nullable=False),
        sa.Column("role_codes", sa.JSON(), nullable=False),
        sa.Column("audience_codes", sa.JSON(), nullable=False),
        sa.Column("applies_to", sa.JSON(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False, server_default="GLOBAL"),
        sa.Column("scope_org_id", sa.String(length=128), nullable=True),
        sa.Column("scope_org_name", sa.String(length=256), nullable=True),
        sa.Column("effective_date", sa.String(length=16), nullable=True),
        sa.Column("expiry_date", sa.String(length=16), nullable=True),
        sa.Column("source_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(length=16), nullable=False, server_default="UPLOAD"),
        sa.Column("original_file_name", sa.String(length=256), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("manually_disabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("index_status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("vector_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_models", sa.JSON(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_id", name="uq_knowledge_document_knowledge_id"),
    )
    op.create_index("ix_knowledge_document_knowledge_id", "knowledge_document", ["knowledge_id"])
    op.create_index("ix_knowledge_document_scope", "knowledge_document", ["scope_type", "scope_org_id"])
    op.create_index("ix_knowledge_document_status", "knowledge_document", ["status"])
    op.create_index("ix_knowledge_document_source", "knowledge_document", ["source_type"])

    # ── 知识分块 ──────────────────────────────────────────────────
    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heading_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("checksum", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("term_freq", sa.JSON(), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_chunk_document_id", "knowledge_chunk", ["document_id"])
    op.create_index("ix_knowledge_chunk_document", "knowledge_chunk", ["document_id", "chunk_no"])
    op.create_index("ix_knowledge_chunk_checksum", "knowledge_chunk", ["checksum"])

    # ── 知识关系 ──────────────────────────────────────────────────
    op.create_table(
        "knowledge_relation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False, server_default="REFERENCE"),
        sa.Column("target_knowledge_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_relation_document_id", "knowledge_relation", ["document_id"])
    op.create_index("ix_knowledge_relation_document", "knowledge_relation", ["document_id"])
    op.create_index("ix_knowledge_relation_target", "knowledge_relation", ["target_knowledge_id"])

    # ── 版本与操作历史 ────────────────────────────────────────────
    op.create_table(
        "knowledge_document_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(length=16), nullable=False, server_default="UPLOAD"),
        sa.Column("original_file_name", sa.String(length=256), nullable=True),
        sa.Column("source_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("checksum", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vector_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operation", sa.String(length=32), nullable=False, server_default="CURRENT"),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("operator_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("operator_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_document_history_document_id", "knowledge_document_history", ["document_id"])
    op.create_index("ix_knowledge_history_document", "knowledge_document_history", ["document_id", "created_at"])

    # ── 同步任务 ──────────────────────────────────────────────────
    op.create_table(
        "knowledge_sync_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trigger_source", sa.String(length=16), nullable=False, server_default="MANUAL"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="RUNNING"),
        sa.Column("vault_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_sync_run_started", "knowledge_sync_run", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_sync_run_started", table_name="knowledge_sync_run")
    op.drop_table("knowledge_sync_run")
    op.drop_index("ix_knowledge_history_document", table_name="knowledge_document_history")
    op.drop_index("ix_knowledge_document_history_document_id", table_name="knowledge_document_history")
    op.drop_table("knowledge_document_history")
    op.drop_index("ix_knowledge_relation_target", table_name="knowledge_relation")
    op.drop_index("ix_knowledge_relation_document", table_name="knowledge_relation")
    op.drop_index("ix_knowledge_relation_document_id", table_name="knowledge_relation")
    op.drop_table("knowledge_relation")
    op.drop_index("ix_knowledge_chunk_checksum", table_name="knowledge_chunk")
    op.drop_index("ix_knowledge_chunk_document", table_name="knowledge_chunk")
    op.drop_index("ix_knowledge_chunk_document_id", table_name="knowledge_chunk")
    op.drop_table("knowledge_chunk")
    op.drop_index("ix_knowledge_document_source", table_name="knowledge_document")
    op.drop_index("ix_knowledge_document_status", table_name="knowledge_document")
    op.drop_index("ix_knowledge_document_scope", table_name="knowledge_document")
    op.drop_index("ix_knowledge_document_knowledge_id", table_name="knowledge_document")
    op.drop_table("knowledge_document")
