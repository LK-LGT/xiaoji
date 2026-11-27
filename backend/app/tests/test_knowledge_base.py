"""🔴 **企业知识库「假成功」端点回归**（2026-11-27）。

⚠️ **本文件锁住的缺陷**
------------------------------------------------------------------
2026-11-27 全面审计发现**企业知识库的 12 个端点全是空壳**：

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

⚠️ **断言策略**
每条都**同时**断言「响应结构对」+「**库里真的有**」，
只断言响应结构是抓不住的 —— 空壳的响应结构也是对的。

⚠️ **契约修复也锁在这里**
``POST /v1/leader/knowledge/upload`` 原先**必填** ``title``，
而前端 ``uploadLeaderKnowledgeApi`` **不传 title** ⇒ 必定 422。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.knowledge_models import (
    KnowledgeChunkRow,
    KnowledgeDocumentHistoryRow,
    KnowledgeDocumentRow,
    KnowledgeSyncRunRow,
)
from backend.app.domain.models import Base, Department, Employee, SystemAdmin
from backend.app.main import app
from backend.app.routes.auth import _generate_token

#: 一份**真实可解析**的 Markdown 知识文档。
MARKDOWN_DOC = """# 员工月计划编写规范

本规范用于统一月计划的填写口径，使目标可以检查和验收。

## 一、计划目标填写规则

适用条件：所有月度重点任务。

填写要求：必须包含完成时间、数量或验收结果。

判断依据：计划字段与对应成果证明。

## 二、扣分口径

未按期交付的任务每延迟一天扣减十分之一分值，扣分上限为该项分值的三分之一。
"""

#: 一份**明显不该入库**的文档（不支持的格式）。
BINARY_DOC = b"\x00\x01\x02\x03"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def org(db):
    db.add_all([
        Department(id="D_ROOT", name="总部", parent_id=None, active=True),
        Department(id="D_OTHER", name="剪辑组", parent_id="D_ROOT", active=True),
    ])
    db.add_all([
        Employee(id="E_LEADER", name="主管", department_id="D_OTHER",
                 employment_status="active", account_status="enabled", token_version=1),
        Employee(id="E_EMP", name="员工甲", department_id="D_OTHER", manager_id="E_LEADER",
                 employment_status="active", account_status="enabled", token_version=1),
    ])
    # ⚠️ **E_LEADER 授予系统管理员**
    #   知识库的写操作（上传 / 换版 / 停用 / 删除 / 同步）需要
    #   ``MANAGE_KNOWLEDGE`` 权限位 ⇒ 必须显式授权。
    db.add(SystemAdmin(employee_id="E_LEADER", active=True))
    db.commit()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _headers(employee_id: str = "E_LEADER", token_version: int = 1) -> dict:
    token, _ = _generate_token(employee_id, "u", token_version)
    return {"Authorization": f"Bearer {token}"}


def _upload(client, *, name="规范.md", content=MARKDOWN_DOC.encode("utf-8"),
            employee="E_LEADER", **form):
    """走**领导上传**端点入库一份文档。"""
    payload = {
        "audienceCodes": "EMPLOYEE",
        "scopeType": "GLOBAL",
        "knowledgeType": "POLICY",
        "appliesTo": "MONTH_PLAN",
        "bindingLevel": "NORMATIVE",
    }
    payload.update(form)
    return client.post(
        "/v1/leader/knowledge/upload",
        files={"file": (name, content, "text/markdown")},
        data=payload,
        headers=_headers(employee),
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. 上传入库 —— 真的落库吗
# ═══════════════════════════════════════════════════════════════════════════


class TestLeaderUpload:
    def test_上传真的落库并切块(self, client, db, org):
        resp = _upload(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        # ⚠️ 空壳特征：documentId 恒为 0
        assert data["documentId"] > 0, "documentId 不能是 0"
        assert data["knowledgeId"], "必须有知识编号"
        assert data["chunkCount"] > 0, "文档必须被切出至少一段"
        assert data["vectorCount"] == data["chunkCount"], "每段都要有可检索索引"

        row = db.get(KnowledgeDocumentRow, data["documentId"])
        assert row is not None, "库里没有这个文档 ⇒ 假成功"
        assert row.chunk_count == data["chunkCount"]
        assert row.index_status == "READY"
        assert row.checksum, "必须有校验值"
        assert row.source_path, "必须记录源文件路径"
        assert row.original_file_name == "规范.md"

        # 分块真的落库了
        chunks = db.execute(
            select(KnowledgeChunkRow).where(KnowledgeChunkRow.document_id == row.id)
        ).scalars().all()
        assert len(chunks) == data["chunkCount"], "分块没落库"
        assert all(chunk.content for chunk in chunks), "分块内容不能为空"
        assert all(chunk.checksum for chunk in chunks), "分块必须有校验值"

    def test_上传真的把文件写到磁盘(self, client, db, org):
        from backend.app.services import knowledge_service

        resp = _upload(client, name="制度.md")
        assert resp.status_code == 200, resp.text
        document_id = resp.json()["data"]["documentId"]

        row = db.get(KnowledgeDocumentRow, document_id)
        path = knowledge_service.resolve_stored_path(row.source_path)
        assert path.is_file(), "源文件没有落盘 ⇒ 重启后内容就没了"
        assert path.read_bytes() == MARKDOWN_DOC.encode("utf-8")

    def test_上传写入版本历史(self, client, db, org):
        resp = _upload(client)
        document_id = resp.json()["data"]["documentId"]

        history = db.execute(
            select(KnowledgeDocumentHistoryRow).where(
                KnowledgeDocumentHistoryRow.document_id == document_id
            )
        ).scalars().all()
        assert history, "首次入库必须留一条历史"
        assert history[0].operation == "CURRENT"
        assert history[0].operator_name == "主管"

    def test_重复上传产生两篇不同文档(self, client, db, org):
        first = _upload(client)
        second = _upload(client)
        assert first.status_code == 200 and second.status_code == 200
        first_id = first.json()["data"]["documentId"]
        second_id = second.json()["data"]["documentId"]
        assert first_id != second_id, "两次上传必须是两篇文档"

        rows = db.execute(select(KnowledgeDocumentRow)).scalars().all()
        assert len(rows) == 2
        # 知识编号必须唯一
        assert len({row.knowledge_id for row in rows}) == 2, "知识编号撞号了"

    def test_不支持的格式必须报错而不是假成功(self, client, db, org):
        resp = _upload(client, name="evil.exe", content=BINARY_DOC)
        assert resp.status_code == 422, resp.text
        assert db.execute(select(KnowledgeDocumentRow)).scalars().all() == [], \
            "解析失败却留下了脏数据"

    def test_空文档必须报错(self, client, db, org):
        resp = _upload(client, name="空.md", content=b"")
        assert resp.status_code == 422, resp.text

    def test_缺少适用角色必须报错(self, client, db, org):
        resp = _upload(client, audienceCodes="")
        assert resp.status_code == 422, resp.text
        assert db.execute(select(KnowledgeDocumentRow)).scalars().all() == []

    def test_缺少适用业务必须报错(self, client, db, org):
        resp = _upload(client, appliesTo="")
        assert resp.status_code == 422, resp.text

    def test_指定范围时必须选组织(self, client, db, org):
        resp = _upload(client, scopeType="DEPARTMENT", scopeOrgId="")
        assert resp.status_code == 422, resp.text

    def test_指定部门时记录部门名(self, client, db, org):
        resp = _upload(client, scopeType="DEPARTMENT", scopeOrgId="D_OTHER")
        assert resp.status_code == 200, resp.text
        row = db.get(KnowledgeDocumentRow, resp.json()["data"]["documentId"])
        assert row.scope_type == "DEPARTMENT"
        assert row.scope_org_id == "D_OTHER"
        assert row.scope_org_name == "剪辑组"


# ═══════════════════════════════════════════════════════════════════════════
# 1b. 写操作必须校验权限
# ═══════════════════════════════════════════════════════════════════════════


class TestKnowledgeWritePermission:
    """⚠️ **知识文档是 AI 检索的依据来源** ⇒ 写操作必须校验权限位。

    否则**任何登录员工**都能停用 / 删除知识文档 ⇒ 直接改变 AI 的回答依据。
    """

    def test_普通员工不能停用文档(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        resp = client.post(
            f"/v1/system/knowledge/documents/{document_id}/disable",
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text
        row = db.get(KnowledgeDocumentRow, document_id)
        assert row.manually_disabled is False, "越权请求不该改到数据"

    def test_普通员工不能删除文档(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        resp = client.delete(
            f"/v1/system/knowledge/documents/{document_id}",
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text
        row = db.get(KnowledgeDocumentRow, document_id)
        assert row.status != "DELETED", "越权请求不该删掉文档"

    def test_普通员工不能换版本(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        resp = client.post(
            f"/v1/system/knowledge/documents/{document_id}/versions",
            files={"file": ("新版.md", "# 新\n\n内容\n".encode(), "text/markdown")},
            data={"version": "V2.0", "changeNote": ""},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text

    def test_普通员工不能触发同步(self, client, db, org):
        resp = client.post("/v1/system/knowledge/sync", headers=_headers("E_EMP"))
        assert resp.status_code == 403, resp.text

    def test_普通员工可以读(self, client, db, org):
        """⚠️ 读接口**不设闸** —— 知识是 AI 回答的依据，员工侧本来就要用。"""
        _upload(client)
        resp = client.get("/v1/system/knowledge/documents", headers=_headers("E_EMP"))
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. 列表 / 详情 / 历史 —— 真的能读回来吗
# ═══════════════════════════════════════════════════════════════════════════


class TestReadEndpoints:
    def test_文档列表返回真实文档(self, client, db, org):
        _upload(client)
        resp = client.get("/v1/system/knowledge/documents", headers=_headers())
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        assert len(rows) == 1
        assert rows[0]["chunkCount"] > 0
        assert rows[0]["owner"] == "主管", "负责人应该是真实姓名"

    def test_文档详情返回真实分块(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        resp = client.get(
            f"/v1/system/knowledge/documents/{document_id}", headers=_headers()
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["document"] is not None, "文档详情不能是 None"
        assert data["document"]["knowledgeId"]
        assert len(data["chunks"]) > 0, "详情必须带出分块"
        assert all(chunk["content"] for chunk in data["chunks"])
        assert isinstance(data["relations"], list)

    def test_文档不存在必须404而不是返回None(self, client, db, org):
        resp = client.get("/v1/system/knowledge/documents/99999", headers=_headers())
        assert resp.status_code == 404, resp.text

    def test_版本历史真的能读出来(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        resp = client.get(
            f"/v1/system/knowledge/documents/{document_id}/history", headers=_headers()
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        assert len(rows) >= 1
        assert rows[0]["operation"] == "CURRENT"
        assert rows[0]["operatorName"] == "主管"

    def test_状态接口反映真实数据(self, client, db, org):
        _upload(client)
        resp = client.get("/v1/system/knowledge/status", headers=_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["chunkCount"] > 0, "状态里的分块数恒为 0 ⇒ 假状态"
        assert data["documentCounts"], "文档分类统计不能为空"
        assert sum(data["documentCounts"].values()) == 1
        assert data["vectorAvailable"] is True
        assert data["vectorEngine"]

    def test_领导只能看到自己上传的(self, client, db, org):
        _upload(client, employee="E_LEADER")
        resp = client.get("/v1/leader/knowledge/uploads", headers=_headers("E_LEADER"))
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 1

        other = client.get("/v1/leader/knowledge/uploads", headers=_headers("E_EMP"))
        assert other.status_code == 200, other.text
        assert other.json()["data"] == [], "不该看到别人的入库记录"


# ═══════════════════════════════════════════════════════════════════════════
# 3. 停用 / 恢复 / 删除 —— 真的改状态吗
# ═══════════════════════════════════════════════════════════════════════════


class TestDocumentActions:
    def test_停用真的改了状态并退出检索(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]

        resp = client.post(
            f"/v1/system/knowledge/documents/{document_id}/disable", headers=_headers()
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["data"]
        assert result["action"] == "DISABLE"
        assert result["message"], "必须给出人话说明"

        row = db.get(KnowledgeDocumentRow, document_id)
        assert row.manually_disabled is True, "停用没落库 ⇒ 假成功"
        assert row.status == "DISABLED"

        # 停用后不该再被检索到
        hits = client.get(
            "/v1/system/knowledge/preview",
            params={"appliesTo": "MONTH_PLAN", "query": "扣分"},
            headers=_headers(),
        )
        assert hits.json()["data"] == [], "停用的文档还在检索结果里"

    def test_恢复真的改了状态并重新可检索(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        client.post(f"/v1/system/knowledge/documents/{document_id}/disable", headers=_headers())

        resp = client.post(
            f"/v1/system/knowledge/documents/{document_id}/enable", headers=_headers()
        )
        assert resp.status_code == 200, resp.text

        row = db.get(KnowledgeDocumentRow, document_id)
        assert row.manually_disabled is False
        assert row.status == "PUBLISHED"
        assert row.vector_count == row.chunk_count, "恢复后索引数要回来"

    def test_重复停用必须报错(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        client.post(f"/v1/system/knowledge/documents/{document_id}/disable", headers=_headers())
        again = client.post(
            f"/v1/system/knowledge/documents/{document_id}/disable", headers=_headers()
        )
        assert again.status_code == 409, again.text

    def test_删除真的软删并留历史(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]

        resp = client.delete(
            f"/v1/system/knowledge/documents/{document_id}", headers=_headers()
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["data"]
        assert result["action"] == "DELETE"

        row = db.get(KnowledgeDocumentRow, document_id)
        assert row is not None, "删除应该软删（保留历史），不该物理删"
        assert row.status == "DELETED"
        assert row.manually_disabled is True

        # 分块被清掉
        chunks = db.execute(
            select(KnowledgeChunkRow).where(KnowledgeChunkRow.document_id == document_id)
        ).scalars().all()
        assert chunks == [], "删除后索引必须清理"

        # 历史留痕
        history = db.execute(
            select(KnowledgeDocumentHistoryRow).where(
                KnowledgeDocumentHistoryRow.document_id == document_id
            )
        ).scalars().all()
        assert any(item.operation == "DELETED" for item in history)

        # 列表里不再出现
        listing = client.get("/v1/system/knowledge/documents", headers=_headers())
        assert all(item["id"] != document_id for item in listing.json()["data"])

    def test_删除不存在的文档必须404(self, client, db, org):
        resp = client.delete("/v1/system/knowledge/documents/99999", headers=_headers())
        assert resp.status_code == 404, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 4. 换版本 —— 真的换了吗
# ═══════════════════════════════════════════════════════════════════════════


class TestVersionReplace:
    def test_换版本真的换内容并归档旧版(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]

        new_content = "# 新版规范\n\n## 修订内容\n\n本版新增了扣分上限的说明。\n".encode()
        resp = client.post(
            f"/v1/system/knowledge/documents/{document_id}/versions",
            files={"file": ("新版.md", new_content, "text/markdown")},
            data={"version": "V2.0", "changeNote": "补充扣分上限"},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["data"]
        assert result["chunkCount"] > 0, "新版本必须重新切块"

        row = db.get(KnowledgeDocumentRow, document_id)
        assert row.version == "V2.0", "版本号没变 ⇒ 假成功"
        assert row.checksum != "", "校验值必须更新"

        # 分块内容确实换了
        chunks = db.execute(
            select(KnowledgeChunkRow).where(KnowledgeChunkRow.document_id == document_id)
        ).scalars().all()
        assert any("扣分上限" in chunk.content for chunk in chunks), "新内容没进索引"

        # 旧版归档
        history = client.get(
            f"/v1/system/knowledge/documents/{document_id}/history", headers=_headers()
        ).json()["data"]
        operations = [item["operation"] for item in history]
        assert "VERSION_REPLACED" in operations, "旧版本没有归档"
        assert "CURRENT" in operations

    def test_换版本解析失败必须报错且不破坏旧版(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        before = db.get(KnowledgeDocumentRow, document_id)

        resp = client.post(
            f"/v1/system/knowledge/documents/{document_id}/versions",
            files={"file": ("坏.exe", b"\x00\x01", "application/octet-stream")},
            data={"version": "V9.9", "changeNote": "坏文件"},
            headers=_headers(),
        )
        assert resp.status_code == 422, resp.text

        after = db.get(KnowledgeDocumentRow, document_id)
        assert after.version == before.version, "失败的换版不该改版本号"
        assert after.checksum == before.checksum

    def test_换版本缺版本号必须报错(self, client, db, org):
        document_id = _upload(client).json()["data"]["documentId"]
        resp = client.post(
            f"/v1/system/knowledge/documents/{document_id}/versions",
            files={"file": ("新版.md", "# x\n\n内容\n".encode(), "text/markdown")},
            data={"version": "", "changeNote": ""},
            headers=_headers(),
        )
        assert resp.status_code == 422, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 5. 检索 —— 真的能搜到吗
# ═══════════════════════════════════════════════════════════════════════════


class TestRetrieval:
    def test_检索能命中已入库内容(self, client, db, org):
        _upload(client)
        resp = client.get(
            "/v1/system/knowledge/preview",
            params={"appliesTo": "MONTH_PLAN", "query": "扣分上限"},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        hits = resp.json()["data"]
        assert hits, "入库了却检索不到 ⇒ 检索是假的"
        assert hits[0]["knowledgeId"]
        assert hits[0]["content"]
        assert hits[0]["score"] > 0
        assert hits[0]["citation"], "命中必须带引用"

    def test_检索不到时如实返回空数组(self, client, db, org):
        _upload(client)
        resp = client.get(
            "/v1/system/knowledge/preview",
            params={"appliesTo": "MONTH_PLAN", "query": "完全不相关的量子力学引文"},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == [], "查不到必须返回空，不能瞎编"

    def test_空查询返回空数组(self, client, db, org):
        _upload(client)
        resp = client.get(
            "/v1/system/knowledge/preview",
            params={"appliesTo": "MONTH_PLAN", "query": "   "},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == []

    def test_适用范围不匹配的文档不进召回(self, client, db, org):
        _upload(client, appliesTo="RESULT")
        resp = client.get(
            "/v1/system/knowledge/preview",
            params={"appliesTo": "MONTH_PLAN", "query": "扣分"},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == [], "适用范围不匹配却召回了"


# ═══════════════════════════════════════════════════════════════════════════
# 6. 同步 —— 没配 Vault 时必须明确失败
# ═══════════════════════════════════════════════════════════════════════════


class TestVaultSync:
    def test_没配Vault时同步必须明确失败(self, client, db, org, monkeypatch):
        from backend.app.core.config import settings

        monkeypatch.setattr(settings, "knowledge_vault_path", "")
        resp = client.post("/v1/system/knowledge/sync", headers=_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # ⚠️ 空壳特征：恒 SUCCESS + scannedCount 0
        assert data["status"] == "FAILED", "没配 Vault 却报成功 ⇒ 管理员被骗"
        assert data["errors"], "失败必须给出原因"
        assert data["finishedAt"]

        # 同步任务必须留痕
        runs = db.execute(select(KnowledgeSyncRunRow)).scalars().all()
        assert runs, "同步没留痕"
        assert runs[0].status == "FAILED"

    def test_配置Vault后真的同步入库(self, client, db, org, tmp_path, monkeypatch):
        from backend.app.core.config import settings

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "规范.md").write_text(MARKDOWN_DOC, encoding="utf-8")
        monkeypatch.setattr(settings, "knowledge_vault_path", str(vault))

        resp = client.post("/v1/system/knowledge/sync", headers=_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "SUCCESS", data
        assert data["scannedCount"] == 1
        assert data["publishedCount"] == 1
        assert data["failedCount"] == 0

        rows = db.execute(
            select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.source_type == "VAULT")
        ).scalars().all()
        assert len(rows) == 1, "Vault 文档没入库"
        assert rows[0].chunk_count > 0

    def test_Vault路径不存在时必须失败(self, client, db, org, tmp_path, monkeypatch):
        from backend.app.core.config import settings

        monkeypatch.setattr(settings, "knowledge_vault_path", str(tmp_path / "nope"))
        resp = client.post("/v1/system/knowledge/sync", headers=_headers())
        data = resp.json()["data"]
        assert data["status"] == "FAILED"
        assert data["errors"]


# ═══════════════════════════════════════════════════════════════════════════
# 7. 解析器单元
# ═══════════════════════════════════════════════════════════════════════════


class TestParser:
    def test_markdown按标题切块并保留面包屑(self):
        from backend.app.services import knowledge_parser

        blocks = knowledge_parser.parse_markdown(MARKDOWN_DOC)
        assert len(blocks) >= 2
        paths = [path for path, _content in blocks]
        assert any("员工月计划编写规范" in path for path in paths)
        assert any("扣分口径" in path for path in paths)

    def test_不支持的扩展名抛解析错误(self):
        from backend.app.services import knowledge_parser

        with pytest.raises(knowledge_parser.KnowledgeParseError):
            knowledge_parser.parse_document("x.exe", b"\x00")

    def test_超长段落按句子边界切(self):
        from backend.app.services import knowledge_parser

        long_text = "。".join(["这是一句很长很长的测试内容用于验证切块逻辑"] * 200) + "。"
        blocks = [("标题", long_text)]
        chunks = knowledge_parser.chunk_blocks(blocks, max_chars=400)
        assert len(chunks) > 1
        assert all(len(chunk.content) <= 400 * 2 for chunk in chunks)

    def test_词频表非空(self):
        from backend.app.services import knowledge_parser

        tokens = knowledge_parser.tokenize("月计划扣分规则")
        assert tokens
        assert sum(tokens.values()) > 0
