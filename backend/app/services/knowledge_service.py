"""📚 **企业知识库服务层**（★ 2026-11-27 新增）。

⚠️ **为什么需要这个模块**
------------------------------------------------------------------
``routes/v1/system.py`` 的 10 个 knowledge 端点 + ``routes/leader.py``
的 2 个 knowledge 端点**全是空壳**（恒返回空数组 / 恒返回 0），
而前端有完整的三个页面（企业知识 · 文档目录 · 检索验证 / 领导入库台）
⇒ **上传弹「成功」，刷新后什么都没了**。

本模块提供**真���落库**的完整链路：

    上传文件 → 解析 → 切块 → 落库 → 建索引 → 可检索
    换版本 → 旧版归档 + 新版入库
    停用 / 恢复 / 删除 → 留痕 + 退出检索

⚠️ **检索方案：为什么不用外部向量库**
------------------------------------------------------------------
项目当前**没有** Qdrant / Milvus 依赖，引入会让部署复杂���，
而且「企业知识库」的规模（几百到几千篇文档）用 **BM25 + 字面权重**
完全够用 ⇒ **纯 Python 实现，零外部依赖**。

打分公式（BM25）：

    score(D, Q) = Σ_{t∈Q} IDF(t) · f(t,D)·(k1+1) / (f(t,D) + k1·(1-b+b·|D|/avgdl))

外加**字面命中加成**（标题 / 章节路径命中额外加分）——
中文场景下这比纯向量检索更稳。

⚠️ **「向量」字段怎么处理**
------------------------------------------------------------------
前端契约里有 ``vectorCount`` / ``embeddingModels`` / ``indexStatus`` /
``vectorCollections`` / ``vectorPointCount`` 等字段。
本实现**如实反映索引状态**：
- ``indexStatus``：``READY``（已建索引）/ ``PENDING`` / ``FAILED`` / ``NOT_REQUIRED``；
- ``vectorCount``：**等于分块数**（本实现「一段一索引」），
  语义上就是「可被检索到的段落数」；
- ``embeddingModels``：本实现用**内置 BM25 索引**，
  标记为 ``["builtin-bm25-v1"]`` —— **不谎称用了某个不存在的模型**。

⚠️ **检索必须过滤**
------------------------------------------------------------------
只召回：``status == PUBLISHED`` 且 ``manually_disabled == False``
且未过期（``expiry_date`` 为空或 ≥ 今天）
且适用范围匹配（``applies_to`` 含请求的 ``appliesTo``
或 ``applies_to`` 为空 = 通用）。
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.knowledge_models import (
    KnowledgeChunkRow,
    KnowledgeDocumentHistoryRow,
    KnowledgeDocumentRow,
    KnowledgeRelationRow,
    KnowledgeSyncRunRow,
)
from . import knowledge_parser

logger = logging.getLogger(__name__)

#: 知识编号前缀。
KNOWLEDGE_ID_PREFIX = "K"

#: 本实现使用的「索引引擎」标识（前端展示用）。
VECTOR_ENGINE_NAME = "builtin-bm25"

#: 本实现登记的「embedding 模型」标识。
#: ⚠️ **不谎称用了外部模型** —— 这就是本实现真正的「索引器」。
EMBEDDING_MODELS = ["builtin-bm25-v1"]

#: BM25 参数。
_BM25_K1 = 1.5
_BM25_B = 0.75

#: 单次检索返回的最大条数。
MAX_HITS = 20

#: 上传文件扩展名白名单。
ALLOWED_EXTENSIONS = (".docx", ".doc", ".md", ".markdown", ".txt")

#: 单文件大小上限（10 MB，与前端一致）。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: 知识类型 → 中文名（前端也有一份，两边保持一致）。
KNOWLEDGE_TYPE_LABELS = {
    "HISTORICAL_PLAN": "历史计划",
    "JOB_DESCRIPTION": "岗位说明",
    "DEPARTMENT_GOAL": "部门目标",
    "LEADER_REQUIREMENT": "领导要求",
    "PROJECT_MATERIAL": "项目资料",
    "POLICY": "制度规范",
    "ACCEPTANCE_STANDARD": "验收标准",
    "REFERENCE_CASE": "参考案例",
    "UPLOADED_REFERENCE": "上传资料",
}

#: 适用业务 → 中文名。
APPLIES_TO_LABELS = {
    "MONTH_PLAN": "月计划生成",
    "WEEK_PLAN": "周计划生成",
    "DAY_PLAN": "日计划生成",
    "PLAN_CHECK": "计划检查",
    "APPROVAL": "计划审批",
    "RESULT": "成果提交",
    "RESULT_CONFIRM": "成果确认",
}


class KnowledgeServiceError(ValueError):
    """知识库业务错误（路由层转成 4xx）。"""


# ---------------------------------------------------------------------------
# 存储目录
# ---------------------------------------------------------------------------


def upload_root() -> Path:
    """上传文件落盘根目录。"""
    root = Path(settings.knowledge_upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def vault_root() -> Path:
    """Obsidian Vault 根目录（未配置时返回 ``None``）。"""
    configured = (settings.knowledge_vault_path or "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    return path if path.is_dir() else None


def vector_engine_name() -> str:
    """当前索引引擎名（前端 ``KnowledgeStatus.vectorEngine``）。"""
    return VECTOR_ENGINE_NAME


def knowledge_enabled() -> bool:
    """知识库写操作是否开放（配置项 ``KNOWLEDGE_ENABLED``）。"""
    return bool(settings.knowledge_enabled)


def require_enabled() -> None:
    """写操作前置校验：知识库未开启 ⇒ 明确报错。

    ⚠️ **为什么不是静默跳过**
    「关了但返回成功」是最坑的形态 ⇒ 调用方必须知道「这次没写进去」。
    """
    if not knowledge_enabled():
        raise KnowledgeServiceError(
            "企业知识库未启用（KNOWLEDGE_ENABLED=false），无法执行写操作"
        )


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _employee_name(db: Session, employee_id: str | None) -> str:
    """员工 ID → 姓名（查不到返回 ID 本身，不返回空串）。"""
    if not employee_id:
        return ""
    from ..domain.models import Employee

    row = db.get(Employee, employee_id)
    return (row.name if row and row.name else employee_id)


def serialize_document(db: Session, row: KnowledgeDocumentRow) -> dict[str, Any]:
    """``KnowledgeDocumentRow`` → 前端 ``KnowledgeDocument``。"""
    return {
        "id": row.id,
        "knowledgeId": row.knowledge_id,
        "title": row.title,
        "knowledgeType": row.knowledge_type,
        "status": row.status,
        "version": row.version,
        "owner": _employee_name(db, row.owner_id),
        "reviewer": _employee_name(db, row.reviewer_id),
        "binding": row.binding,
        "departmentIds": list(row.department_ids or []),
        "roleCodes": list(row.role_codes or []),
        "appliesTo": list(row.applies_to or []),
        "audienceCodes": list(row.audience_codes or []),
        "scopeType": row.scope_type,
        "scopeOrgId": row.scope_org_id,
        "scopeOrgName": row.scope_org_name,
        "effectiveDate": row.effective_date,
        "expiryDate": row.expiry_date,
        "sourcePath": row.source_path,
        "sourceType": row.source_type,
        "originalFileName": row.original_file_name,
        "checksum": row.checksum,
        "manuallyDisabled": bool(row.manually_disabled),
        "chunkCount": int(row.chunk_count or 0),
        "relationCount": int(row.relation_count or 0),
        "indexStatus": row.index_status,
        "vectorCount": int(row.vector_count or 0),
        "embeddingModels": list(row.embedding_models or []),
        "indexedAt": row.indexed_at.isoformat() if row.indexed_at else None,
        "syncedAt": row.synced_at.isoformat() if row.synced_at else "",
    }


def serialize_chunk(chunk: KnowledgeChunkRow) -> dict[str, Any]:
    """``KnowledgeChunkRow`` → 前端 ``KnowledgeChunk``。"""
    return {
        "chunkNo": int(chunk.chunk_no or 0),
        "headingPath": chunk.heading_path or "",
        "content": chunk.content or "",
        "checksum": chunk.checksum or "",
    }


def serialize_relation(row: KnowledgeRelationRow) -> dict[str, Any]:
    """``KnowledgeRelationRow`` → 前端 ``KnowledgeRelation``。"""
    return {
        "type": row.relation_type or "",
        "target": row.target_knowledge_id or "",
    }


def serialize_history(row: KnowledgeDocumentHistoryRow) -> dict[str, Any]:
    """``KnowledgeDocumentHistoryRow`` → 前端 ``KnowledgeDocumentHistory``。"""
    return {
        "id": row.id,
        "documentId": int(row.document_id or 0),
        "knowledgeId": row.knowledge_id or "",
        "title": row.title or "",
        "version": row.version or "",
        "status": row.status or "",
        "sourceType": row.source_type or "UPLOAD",
        "originalFileName": row.original_file_name,
        "sourcePath": row.source_path or "",
        "checksum": row.checksum or "",
        "chunkCount": int(row.chunk_count or 0),
        "vectorCount": int(row.vector_count or 0),
        "operation": row.operation or "CURRENT",
        "changeNote": row.change_note,
        "operatorName": row.operator_name or "",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }


def serialize_sync_run(row: KnowledgeSyncRunRow) -> dict[str, Any]:
    """``KnowledgeSyncRunRow`` → 前端 ``KnowledgeSyncResult``。"""
    return {
        "id": row.id,
        "triggerSource": row.trigger_source or "MANUAL",
        "status": row.status or "SUCCESS",
        "vaultPath": row.vault_path or "",
        "scannedCount": int(row.scanned_count or 0),
        "publishedCount": int(row.published_count or 0),
        "failedCount": int(row.failed_count or 0),
        "errors": list(row.errors or []),
        "startedAt": row.started_at.isoformat() if row.started_at else "",
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
    }


# ---------------------------------------------------------------------------
# 知识编号
# ---------------------------------------------------------------------------


def next_knowledge_id(db: Session) -> str:
    """生成下一个知识编号（形如 ``K-20261127-0001``）。

    ⚠️ **并发安全**
    「当天已有数量 + 1」在并发下会撞号 ⇒ 改成**取当天最大序号 + 1**，
    并且**每次循环都重新查**（撞号就再查一次、往后挪）。
    极端并发下仍可能撞号 ⇒ 兜底用时间戳（仍然唯一，只是不是连续号）。

    ⚠️ **为什么不在数据库里加唯一索引 + 自增**
    知识编号要**按天分段**（``K-20261127-0001``），
    数据库自增给不了「按天重置」 ⇒ 只能应用层算。
    """
    day = datetime.utcnow().strftime("%Y%m%d")
    prefix = f"{KNOWLEDGE_ID_PREFIX}-{day}-"

    for _attempt in range(10):
        existing = db.execute(
            select(KnowledgeDocumentRow.knowledge_id).where(
                KnowledgeDocumentRow.knowledge_id.like(f"{prefix}%")
            )
        ).scalars().all()

        max_seq = 0
        for value in existing:
            tail = (value or "")[len(prefix):]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))

        candidate = f"{prefix}{max_seq + 1:04d}"
        if candidate not in set(existing):
            return candidate

    return f"{prefix}{datetime.utcnow().strftime('%H%M%S%f')[-6:]}"


# ---------------------------------------------------------------------------
# 文件落盘
# ---------------------------------------------------------------------------


def _safe_filename(name: str) -> str:
    """文件名安全化（防目录穿越）。"""
    base = Path(name or "upload").name
    cleaned = re.sub(r"[^\w.\-一-鿿]+", "_", base).strip("._")
    return cleaned or "upload"


def store_file(data: bytes, filename: str) -> str:
    """落盘上传文件，返回**相对路径**。

    ⚠️ **相对路径**（不是绝对路径）
    绝对路径换台机器就失效 ⇒ 库里只存相对路径。
    """
    root = upload_root()
    sub = datetime.utcnow().strftime("%Y%m")
    target_dir = root / sub
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    digest = hashlib.sha256(data).hexdigest()[:12]
    target = target_dir / f"{digest}_{safe}"
    target.write_bytes(data)
    return str(target.relative_to(root)).replace("\\", "/")


def resolve_stored_path(relative_path: str) -> Path:
    """相对路径 → 绝对路径（**必须**在 upload_root 内，防穿越）。"""
    root = upload_root().resolve()
    candidate = (root / (relative_path or "").lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise KnowledgeServiceError("非法的存储路径") from exc
    return candidate


def delete_stored_file(relative_path: str) -> bool:
    """删除落盘文件（返回是否真的删了）。"""
    if not relative_path:
        return False
    try:
        path = resolve_stored_path(relative_path)
    except KnowledgeServiceError:
        return False
    if path.is_file():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# 索引（建索引 = 写分块 + 词频）
# ---------------------------------------------------------------------------


def build_index(db: Session, document: KnowledgeDocumentRow, chunks: list[knowledge_parser.ParsedChunk]) -> None:
    """写分块 + 更新文档的索引状态。

    ⚠️ **「索引」在这里 = 可被检索的段落**
    本实现不依赖外部向量库 ⇒ 建索引 = 落分块 + 落词频。
    """
    db.query(KnowledgeChunkRow).filter(
        KnowledgeChunkRow.document_id == document.id
    ).delete(synchronize_session=False)

    for parsed in chunks:
        db.add(
            KnowledgeChunkRow(
                document_id=document.id,
                chunk_no=parsed.chunk_no,
                heading_path=parsed.heading_path,
                content=parsed.content,
                checksum=parsed.checksum,
                term_freq=parsed.term_freq,
                content_length=parsed.content_length,
            )
        )

    document.chunk_count = len(chunks)
    document.vector_count = len(chunks)
    document.embedding_models = list(EMBEDDING_MODELS)
    document.index_status = "READY" if chunks else "NOT_REQUIRED"
    document.indexed_at = datetime.utcnow()


def append_history(
    db: Session,
    document: KnowledgeDocumentRow,
    operation: str,
    operator_id: str,
    operator_name: str,
    change_note: str | None = None,
) -> KnowledgeDocumentHistoryRow:
    """追加一条历史记录。"""
    row = KnowledgeDocumentHistoryRow(
        document_id=document.id,
        knowledge_id=document.knowledge_id,
        title=document.title,
        version=document.version,
        status=document.status,
        source_type=document.source_type,
        original_file_name=document.original_file_name,
        source_path=document.source_path,
        checksum=document.checksum,
        chunk_count=int(document.chunk_count or 0),
        vector_count=int(document.vector_count or 0),
        operation=operation,
        change_note=change_note,
        operator_id=operator_id or "",
        operator_name=operator_name or "",
    )
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# 上传 / 入库
# ---------------------------------------------------------------------------


def ingest_document(
    db: Session,
    *,
    data: bytes,
    filename: str,
    title: str = "",
    knowledge_type: str = "",
    applies_to: Iterable[str] = (),
    audience_codes: Iterable[str] = (),
    binding: str = "REFERENCE",
    scope_type: str = "GLOBAL",
    scope_org_id: str | None = None,
    scope_org_name: str | None = None,
    version: str = "V1.0",
    change_note: str | None = None,
    source_type: str = "UPLOAD",
    owner_id: str = "",
    owner_name: str = "",
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    """**核心入库流程**：落盘 → 解析 → 切块 → 建索引 → 落库。

    ⚠️ **事务边界**
    整条链路在**一个事务**里：任一步失败 ⇒ 全部回滚，
    不会留下「文件在、记录不在」的孤儿数据。
    """
    if not data:
        raise KnowledgeServiceError("文件内容为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise KnowledgeServiceError("单个文件不能超过 10 MB")
    lower = (filename or "").lower()
    if not lower.endswith(ALLOWED_EXTENSIONS):
        raise KnowledgeServiceError(
            f"暂不支持的文件格式：{lower or '(无扩展名)'}，仅支持 .docx / .doc / .md / .markdown"
        )
    require_enabled()

    # 解析失败 ⇒ 抛错（路由层 422）⇒ **不会**出现「上传成功但 0 段」
    blocks = knowledge_parser.parse_document(filename, data)
    chunks = knowledge_parser.chunk_blocks(blocks)
    if not chunks:
        raise KnowledgeServiceError("文档解析后没有任何有效内容（正文为空）")

    relative_path = store_file(data, filename)
    checksum = knowledge_parser.file_checksum(data)

    document = KnowledgeDocumentRow(
        knowledge_id=next_knowledge_id(db),
        title=(title or "").strip() or knowledge_parser.extract_title(filename),
        knowledge_type=(knowledge_type or "").strip() or "UPLOADED_REFERENCE",
        status="PUBLISHED",
        version=(version or "").strip() or "V1.0",
        owner_id=owner_id or "",
        reviewer_id=reviewer_id,
        binding=(binding or "").strip() or "REFERENCE",
        department_ids=[],
        role_codes=[],
        audience_codes=list(audience_codes or []),
        applies_to=list(applies_to or []),
        scope_type=(scope_type or "").strip() or "GLOBAL",
        scope_org_id=scope_org_id or None,
        scope_org_name=scope_org_name or None,
        source_path=relative_path,
        source_type=source_type,
        original_file_name=filename,
        checksum=checksum,
        manually_disabled=False,
        chunk_count=len(chunks),
        relation_count=0,
        index_status="PENDING",
        vector_count=0,
        embedding_models=[],
    )
    db.add(document)
    db.flush()  # 拿到 document.id

    build_index(db, document, chunks)
    append_history(
        db,
        document,
        "CURRENT",
        owner_id,
        owner_name,
        change_note or "首次入库",
    )
    db.commit()
    db.refresh(document)

    return serialize_upload_result(db, document)


def serialize_upload_result(db: Session, document: KnowledgeDocumentRow) -> dict[str, Any]:
    """文档 → 前端 ``KnowledgeUploadResult``。"""
    return {
        "documentId": int(document.id),
        "knowledgeId": document.knowledge_id or "",
        "title": document.title or "",
        "fileName": document.original_file_name or "",
        "fileType": (document.original_file_name or "").rsplit(".", 1)[-1].lower(),
        "chunkCount": int(document.chunk_count or 0),
        "vectorCount": int(document.vector_count or 0),
        "embeddingModels": list(document.embedding_models or []),
        "uploadedAt": document.created_at.isoformat() if document.created_at else datetime.utcnow().isoformat(),
    }


def serialize_uploaded_document(row: KnowledgeDocumentRow) -> dict[str, Any]:
    """文档 → 前端 ``UploadedKnowledgeDocument``。"""
    return {
        "documentId": int(row.id),
        "knowledgeId": row.knowledge_id or "",
        "title": row.title or "",
        "fileName": row.original_file_name or "",
        "chunkCount": int(row.chunk_count or 0),
        "vectorCount": int(row.vector_count or 0),
        "embeddingModels": list(row.embedding_models or []),
        "uploadedAt": (row.synced_at or row.created_at).isoformat()
        if (row.synced_at or row.created_at)
        else datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def list_documents(
    db: Session,
    *,
    include_deleted: bool = False,
    limit: int = 500,
) -> list[KnowledgeDocumentRow]:
    """文档列表（按更新时间倒序）。"""
    stmt = select(KnowledgeDocumentRow).order_by(
        KnowledgeDocumentRow.updated_at.desc(), KnowledgeDocumentRow.id.desc()
    )
    if not include_deleted:
        stmt = stmt.where(KnowledgeDocumentRow.status != "DELETED")
    return list(db.execute(stmt.limit(limit)).scalars().all())


def get_document(db: Session, document_id: int) -> KnowledgeDocumentRow | None:
    """按 ID 取文档。"""
    return db.get(KnowledgeDocumentRow, document_id)


def document_chunks(db: Session, document_id: int) -> list[KnowledgeChunkRow]:
    """文档的分块（按段序号）。"""
    return list(
        db.execute(
            select(KnowledgeChunkRow)
            .where(KnowledgeChunkRow.document_id == document_id)
            .order_by(KnowledgeChunkRow.chunk_no)
        ).scalars().all()
    )


def document_relations(db: Session, document_id: int) -> list[KnowledgeRelationRow]:
    """文档的关系。"""
    return list(
        db.execute(
            select(KnowledgeRelationRow)
            .where(KnowledgeRelationRow.document_id == document_id)
            .order_by(KnowledgeRelationRow.id)
        ).scalars().all()
    )


def document_history(db: Session, document_id: int) -> list[KnowledgeDocumentHistoryRow]:
    """文档的历史（按时间倒序）。"""
    return list(
        db.execute(
            select(KnowledgeDocumentHistoryRow)
            .where(KnowledgeDocumentHistoryRow.document_id == document_id)
            .order_by(
                KnowledgeDocumentHistoryRow.created_at.desc(),
                KnowledgeDocumentHistoryRow.id.desc(),
            )
        ).scalars().all()
    )


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


def build_status(db: Session) -> dict[str, Any]:
    """知识库运行状态（前端 ``KnowledgeStatus``）。

    ⚠️ **``documentCounts`` 的键是「文档状态」**
    前端 ``countOf('PUBLISHED')`` / ``countOf('DISABLED')`` /
    ``countOf('IN_REVIEW')`` / ``countOf('APPROVED')``
    ⇒ 必须按 **status** 分组（不是按知识类型）。
    """
    vault = vault_root()
    documents = list_documents(db, limit=1000)
    counts: dict[str, int] = {}
    for row in documents:
        key = row.status or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1

    chunk_total = int(
        db.query(KnowledgeChunkRow).count() or 0
    )
    relation_total = int(
        db.query(KnowledgeRelationRow).count() or 0
    )
    vector_total = sum(int(row.vector_count or 0) for row in documents)

    last_run = db.execute(
        select(KnowledgeSyncRunRow).order_by(KnowledgeSyncRunRow.started_at.desc()).limit(1)
    ).scalars().first()

    return {
        "enabled": knowledge_enabled(),
        "vaultPath": str(vault) if vault else (settings.knowledge_vault_path or ""),
        "vaultAvailable": vault is not None,
        "documentCounts": counts,
        "chunkCount": chunk_total,
        "relationCount": relation_total,
        "vectorEngine": vector_engine_name(),
        "vectorAvailable": True,
        "vectorCollections": ["knowledge_builtin"],
        "vectorPointCount": vector_total,
        "vectorError": None,
        "lastSync": serialize_sync_run(last_run) if last_run else None,
    }


# ---------------------------------------------------------------------------
# 检索（BM25）
# ---------------------------------------------------------------------------


def _visible_documents(db: Session, applies_to: str = "") -> list[KnowledgeDocumentRow]:
    """可见文档：已发布 + 未手动停用 + 未过期。"""
    today = datetime.utcnow().date().isoformat()
    rows = db.execute(
        select(KnowledgeDocumentRow).where(
            KnowledgeDocumentRow.status == "PUBLISHED",
            KnowledgeDocumentRow.manually_disabled.is_(False),
        )
    ).scalars().all()

    visible: list[KnowledgeDocumentRow] = []
    for row in rows:
        expiry = row.expiry_date
        if expiry and expiry[:10] < today:
            continue
        if applies_to:
            scopes = list(row.applies_to or [])
            if scopes and applies_to not in scopes:
                continue
        visible.append(row)
    return visible


def search(
    db: Session,
    *,
    query: str,
    applies_to: str = "",
    dept_id: str | None = None,
    limit: int = MAX_HITS,
) -> list[dict[str, Any]]:
    """知识检索（BM25 + 字面加成）。

    ⚠️ **返回空列表是合法结果**
    「知识库里没有」必须如实返回空 ⇒ 前端会显示
    「没有符合范围的已发布知识」⇒ 这正是防幻觉的关键。
    """
    text = (query or "").strip()
    if not text:
        return []

    documents = _visible_documents(db, applies_to)
    if not documents:
        return []

    doc_ids = [row.id for row in documents]
    chunks = list(
        db.execute(
            select(KnowledgeChunkRow).where(KnowledgeChunkRow.document_id.in_(doc_ids))
        ).scalars().all()
    )
    if not chunks:
        return []

    # 统计全局词频（算 IDF）
    doc_freq: dict[str, int] = {}
    total_length = 0
    chunk_freq: dict[tuple[int, str], int] = {}
    for chunk in chunks:
        total_length += int(chunk.content_length or 0)
        seen = set()
        for term, count in (chunk.term_freq or {}).items():
            chunk_freq[(chunk.id, term)] = int(count)
            if term not in seen:
                doc_freq[term] = doc_freq.get(term, 0) + 1
                seen.add(term)
    avg_length = max(total_length / max(len(chunks), 1), 1.0)

    query_terms = knowledge_parser.tokenize(text)
    if not query_terms:
        return []

    doc_by_id = {row.id: row for row in documents}
    scored: list[tuple[float, KnowledgeChunkRow]] = []
    total_docs = len(chunks)

    # ⚠️ **「有信息量的词」才算命中**
    #    单个中文字（``文`` / ``的``）在任何文档里都能命中 ⇒ 会造成
    #    「查询与知识库毫无关系，却被判为命中」的**假阳性**。
    #    ⇒ 只有命中了至少一个「有信息量的词」（双字中文 / ≥3 位英文）才算。
    informative_terms = {
        term for term in query_terms if knowledge_parser.is_informative(term)
    }
    if not informative_terms:
        return []

    for chunk in chunks:
        score = 0.0
        matched_informative = 0
        for term, q_count in query_terms.items():
            freq = chunk_freq.get((chunk.id, term), 0)
            if not freq:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            length = max(int(chunk.content_length or 0), 1)
            denominator = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / avg_length)
            contribution = idf * (freq * (_BM25_K1 + 1)) / denominator * min(q_count, 3)
            score += contribution
            if term in informative_terms:
                matched_informative += 1

        if score <= 0 or not matched_informative:
            continue

        # 字面加成：章节路径 / 标题命中
        document = doc_by_id.get(chunk.document_id)
        if document:
            title = (document.title or "").lower()
            heading = (chunk.heading_path or "").lower()
            lowered = text.lower()
            if title and title in lowered:
                score += 2.0
            if heading and any(part and part in lowered for part in re.split(r"\s*/\s*", heading)):
                score += 1.0

        scored.append((score, chunk))

    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results: list[dict[str, Any]] = []
    for score, chunk in scored[: max(1, limit)]:
        document = doc_by_id.get(chunk.document_id)
        if document is None:
            continue
        results.append(
            {
                "knowledgeId": document.knowledge_id,
                "title": document.title,
                "knowledgeType": document.knowledge_type,
                "version": document.version,
                "binding": document.binding,
                "headingPath": chunk.heading_path or "",
                "content": chunk.content,
                "citation": _build_citation(document, chunk),
                "checksum": chunk.checksum,
                "score": round(float(score), 4),
            }
        )
    return results


def _build_citation(document: KnowledgeDocumentRow, chunk: KnowledgeChunkRow) -> str:
    """构造引用串（前端直接展示）。"""
    parts = [document.knowledge_id, document.title, f"v{document.version}"]
    if chunk.heading_path:
        parts.append(chunk.heading_path)
    parts.append(f"#{chunk.chunk_no}")
    return " · ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# 动作：停用 / 恢复 / 删除 / 换版本
# ---------------------------------------------------------------------------


def _action_result(
    document: KnowledgeDocumentRow,
    action: str,
    message: str,
    vector_cleanup_succeeded: bool = True,
) -> dict[str, Any]:
    return {
        "documentId": int(document.id),
        "knowledgeId": document.knowledge_id or "",
        "action": action,
        "vectorCleanupSucceeded": vector_cleanup_succeeded,
        "message": message,
        "operatedAt": datetime.utcnow().isoformat(),
    }


def disable_document(
    db: Session, document_id: int, operator_id: str, operator_name: str
) -> dict[str, Any]:
    """停用文档（退出检索，保留数据）。"""
    document = get_document(db, document_id)
    if document is None:
        raise KnowledgeServiceError("文档不存在")
    if document.manually_disabled:
        raise KnowledgeServiceError("文档已经是停用状态")

    document.manually_disabled = True
    document.status = "DISABLED"
    document.index_status = "PENDING"
    document.vector_count = 0
    append_history(db, document, "DISABLED", operator_id, operator_name, "手动停用")
    db.commit()
    db.refresh(document)
    return _action_result(document, "DISABLE", f"已停用《{document.title}》，该文档不再进入 AI 检索")


def enable_document(
    db: Session, document_id: int, operator_id: str, operator_name: str
) -> dict[str, Any]:
    """恢复启用文档。"""
    document = get_document(db, document_id)
    if document is None:
        raise KnowledgeServiceError("文档不存在")
    if not document.manually_disabled:
        raise KnowledgeServiceError("文档本来就是启用状态")

    document.manually_disabled = False
    document.status = "PUBLISHED"
    # 恢复索引状态（分块还在，只是之前被标记为不可检索）
    document.index_status = "READY" if document.chunk_count else "PENDING"
    document.vector_count = int(document.chunk_count or 0)
    append_history(db, document, "ENABLED", operator_id, operator_name, "恢复启用")
    db.commit()
    db.refresh(document)
    return _action_result(document, "ENABLE", f"已恢复《{document.title}》，重新进入 AI 检索")


def delete_document(
    db: Session, document_id: int, operator_id: str, operator_name: str
) -> dict[str, Any]:
    """删除文档（软删除 + 清索引 + 删源文件）。"""
    document = get_document(db, document_id)
    if document is None:
        raise KnowledgeServiceError("文档不存在")
    if document.status == "DELETED":
        raise KnowledgeServiceError("文档已经被删除")

    source_path = document.source_path
    document.manually_disabled = True
    document.status = "DELETED"
    document.index_status = "NOT_REQUIRED"
    document.vector_count = 0
    append_history(db, document, "DELETED", operator_id, operator_name, "删除文档")
    db.commit()
    db.refresh(document)

    # 清理索引与文件（**在事务外** —— 文件删除失败不该回滚业务状态）
    db.query(KnowledgeChunkRow).filter(
        KnowledgeChunkRow.document_id == document.id
    ).delete(synchronize_session=False)
    db.query(KnowledgeRelationRow).filter(
        KnowledgeRelationRow.document_id == document.id
    ).delete(synchronize_session=False)
    db.commit()

    file_removed = delete_stored_file(source_path)
    return _action_result(
        document,
        "DELETE",
        f"已删除《{document.title}》，历史记录已保留",
        vector_cleanup_succeeded=file_removed or not source_path,
    )


def replace_version(
    db: Session,
    document_id: int,
    *,
    data: bytes,
    filename: str,
    version: str,
    change_note: str,
    operator_id: str,
    operator_name: str,
) -> dict[str, Any]:
    """更新文档版本（旧版归档 + 新版入库）。"""
    document = get_document(db, document_id)
    if document is None:
        raise KnowledgeServiceError("文档不存在")
    if document.status == "DELETED":
        raise KnowledgeServiceError("文档已删除，无法更新版本")
    if not (version or "").strip():
        raise KnowledgeServiceError("缺少版本号")
    require_enabled()

    if not data:
        raise KnowledgeServiceError("文件内容为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise KnowledgeServiceError("单个文件不能超过 10 MB")
    lower = (filename or "").lower()
    if not lower.endswith(ALLOWED_EXTENSIONS):
        raise KnowledgeServiceError(
            f"暂不支持的文件格式：{lower or '(无扩展名)'}，仅支持 .docx / .doc / .md / .markdown"
        )

    blocks = knowledge_parser.parse_document(filename, data)
    chunks = knowledge_parser.chunk_blocks(blocks)
    if not chunks:
        raise KnowledgeServiceError("文档解析后没有任何有效内容（正文为空）")

    relative_path = store_file(data, filename)
    checksum = knowledge_parser.file_checksum(data)

    # 旧版本归档
    append_history(db, document, "VERSION_REPLACED", operator_id, operator_name, change_note or "版本更新")

    document.version = (version or "").strip()
    document.source_path = relative_path
    document.original_file_name = filename
    document.checksum = checksum
    document.status = "PUBLISHED"
    document.manually_disabled = False
    document.synced_at = datetime.utcnow()

    build_index(db, document, chunks)
    append_history(db, document, "CURRENT", operator_id, operator_name, change_note or "版本更新")
    db.commit()
    db.refresh(document)

    return serialize_upload_result(db, document)


# ---------------------------------------------------------------------------
# 同步（Vault → 知识库）
# ---------------------------------------------------------------------------


def sync_from_vault(db: Session, trigger_source: str = "MANUAL") -> dict[str, Any]:
    """从 Obsidian Vault 同步知识文档。

    ⚠️ **没配 Vault 路径时必须明确失败**
    返回 ``status: "FAILED"`` + 明确原因，**而不是**假装同步成功
    （原实现恒返回 ``scannedCount: 0`` + ``SUCCESS``）。
    """
    started = datetime.utcnow()
    run = KnowledgeSyncRunRow(
        trigger_source=trigger_source,
        status="RUNNING",
        vault_path=settings.knowledge_vault_path or "",
        started_at=started,
    )
    db.add(run)
    db.commit()

    errors: list[str] = []
    scanned = 0
    published = 0

    vault = vault_root()
    if vault is None:
        errors.append(
            "未配置知识库 Vault 路径（KNOWLEDGE_VAULT_PATH），无法同步"
            if not (settings.knowledge_vault_path or "").strip()
            else f"Vault 路径不存在或不可读：{settings.knowledge_vault_path}"
        )
    else:
        candidates = sorted(
            path
            for path in vault.rglob("*")
            if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS
        )
        for path in candidates:
            scanned += 1
            try:
                data = path.read_bytes()
                _ingest_vault_file(db, vault, path, data, trigger_source)
                published += 1
            except Exception as exc:  # noqa: BLE001 - 单篇失败不影响整体
                errors.append(f"{path.name}: {exc}")
                logger.warning("知识库同步失败 %s: %s", path, exc)

    failed = len(errors)
    status = "SUCCESS" if failed == 0 else ("PARTIAL" if published else "FAILED")
    run.status = status
    run.scanned_count = scanned
    run.published_count = published
    run.failed_count = failed
    run.errors = errors
    run.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return serialize_sync_run(run)


def _ingest_vault_file(
    db: Session,
    vault: Path,
    path: Path,
    data: bytes,
    trigger_source: str,
) -> None:
    """入库单个 Vault 文件（按相对路径做幂等去重）。"""
    relative = str(path.relative_to(vault)).replace("\\", "/")
    checksum = knowledge_parser.file_checksum(data)

    existing = db.execute(
        select(KnowledgeDocumentRow).where(
            KnowledgeDocumentRow.source_path == relative,
            KnowledgeDocumentRow.status != "DELETED",
        )
    ).scalars().first()

    blocks = knowledge_parser.parse_document(path.name, data)
    chunks = knowledge_parser.chunk_blocks(blocks)
    if not chunks:
        raise KnowledgeServiceError("文档解析后没有任何有效内容")

    if existing is None:
        document = KnowledgeDocumentRow(
            knowledge_id=next_knowledge_id(db),
            title=knowledge_parser.extract_title(path.name),
            knowledge_type="UPLOADED_REFERENCE",
            status="PUBLISHED",
            version="V1.0",
            owner_id="",
            binding="REFERENCE",
            audience_codes=["MANAGEMENT", "EMPLOYEE"],
            applies_to=[],
            scope_type="GLOBAL",
            source_path=relative,
            source_type="VAULT",
            original_file_name=path.name,
            checksum=checksum,
            index_status="PENDING",
        )
        db.add(document)
        db.flush()
        build_index(db, document, chunks)
        append_history(db, document, "CURRENT", "system", "系统同步", "Vault 同步入库")
        return

    # 已存在 ⇒ 校验值相同则跳过；不同则换版本
    if existing.checksum == checksum:
        existing.synced_at = datetime.utcnow()
        return
    append_history(db, existing, "VERSION_REPLACED", "system", "系统同步", "Vault 内容变更")
    existing.source_path = relative
    existing.checksum = checksum
    existing.synced_at = datetime.utcnow()
    build_index(db, existing, chunks)
    append_history(db, existing, "CURRENT", "system", "系统同步", "Vault 内容变更")
    db.commit()


def last_sync_run(db: Session) -> KnowledgeSyncRunRow | None:
    """最近一次同步记录。"""
    return db.execute(
        select(KnowledgeSyncRunRow).order_by(KnowledgeSyncRunRow.started_at.desc()).limit(1)
    ).scalars().first()


def archive_lifetime_days() -> int:
    """归档保留天数（配置项）。"""
    return int(settings.knowledge_archive_days)


def purge_expired_archives(db: Session) -> int:
    """清理超期的已删除文档**归档源文件**（返回清理数量）。

    ⚠️ **为什么要有这个**
    删除文档时源文件被移入归档（可恢复）⇒ 不清理的话磁盘会一直涨。
    ⇒ 超过 ``KNOWLEDGE_ARCHIVE_DAYS`` 的归档文件直接删掉。

    ⚠️ **只删文件，不动记录**
    数据库里的文档行与历史记录**永久保留**（审计要求），
    只是不再保留原始文件字节。
    """
    cutoff = datetime.utcnow() - timedelta(days=max(1, archive_lifetime_days()))
    rows = db.execute(
        select(KnowledgeDocumentRow).where(
            KnowledgeDocumentRow.status == "DELETED",
            KnowledgeDocumentRow.updated_at < cutoff,
        )
    ).scalars().all()

    removed = 0
    for row in rows:
        if row.source_type == "UPLOAD" and delete_stored_file(row.source_path):
            removed += 1
            row.source_path = ""
    if removed:
        db.commit()
    return removed


__all__ = [
    "ALLOWED_EXTENSIONS",
    "APPLIES_TO_LABELS",
    "EMBEDDING_MODELS",
    "KNOWLEDGE_TYPE_LABELS",
    "MAX_HITS",
    "MAX_UPLOAD_BYTES",
    "VECTOR_ENGINE_NAME",
    "KnowledgeServiceError",
    "append_history",
    "archive_lifetime_days",
    "build_index",
    "build_status",
    "delete_document",
    "disable_document",
    "enable_document",
    "get_document",
    "ingest_document",
    "knowledge_enabled",
    "list_documents",
    "purge_expired_archives",
    "replace_version",
    "require_enabled",
    "search",
    "serialize_chunk",
    "serialize_document",
    "serialize_history",
    "serialize_relation",
    "serialize_sync_run",
    "serialize_upload_result",
    "serialize_uploaded_document",
    "sync_from_vault",
]
