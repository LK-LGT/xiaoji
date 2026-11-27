"""🔴 **知识库读写权限回归**（2026-11-27）。

⚠️ **本文件锁住的缺陷**
------------------------------------------------------------------
知识库接入真实服务后，权限只做了一半：

- ``POST /v1/leader/knowledge/upload`` 只校验「已登录」
  ⇒ 任何员工都能上传知识文档（AI 检索依据）。

权限口径：

- 领导端上传 / 我的上传：主管 / 部门负责人 / HR / 管理员；
- 系统端知识库**读与预览**：有意对所有登录员工开放
  （知识是 AI 回答的依据，员工侧本来就要用）；
- 系统端停用 / 删除 / 换版 / 同步：HR / 管理员（``MANAGE_KNOWLEDGE``）。

⚠️ 主管**不能**因为能上传就获得删除/停用任意文档的权限。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
)
from backend.app.main import app
from backend.app.routes.auth import _generate_token


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
    db.add_all(
        [
            Department(id="D_ROOT", name="总部", parent_id=None, active=True),
            Department(id="D_A", name="A 部门", parent_id="D_ROOT", leader_id="E_LEAD", active=True),
            Department(id="D_HR", name="人事行政部", parent_id="D_ROOT", active=True),
        ]
    )
    db.add_all(
        [
            Employee(
                id="E_LEAD", name="部门负责人", department_id="D_A",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_MGR", name="主管", department_id="D_A",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_EMP", name="员工", department_id="D_A", manager_id="E_MGR",
                employment_status="active", account_status="enabled", token_version=1,
            ),
            Employee(
                id="E_HR", name="HR", department_id="D_HR",
                employment_status="active", account_status="enabled", token_version=1,
            ),
        ]
    )
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _headers(employee_id: str) -> dict:
    token, _ = _generate_token(employee_id, "u", 1)
    return {"Authorization": f"Bearer {token}"}


def _upload(client, employee_id: str):
    return client.post(
        "/v1/leader/knowledge/upload",
        data={
            "audienceCodes": "EMPLOYEE",
            "appliesTo": "MONTH_PLAN",
            "knowledgeType": "POLICY",
            "bindingLevel": "REFERENCE",
            "scopeType": "GLOBAL",
        },
        files={
            "file": (
                "authz-smoke.txt",
                "知识库权限测试文档。关键词：权限测试。".encode(),
                "text/plain",
            )
        },
        headers=_headers(employee_id),
    )


# ---------------------------------------------------------------------------
# 领导端上传
# ---------------------------------------------------------------------------


def test_普通员工不能上传知识文档(client, org, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_upload_dir", str(tmp_path / "knowledge"))
    resp = _upload(client, "E_EMP")
    assert resp.status_code == 403, resp.text


def test_主管可以上传知识文档(client, org, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_upload_dir", str(tmp_path / "knowledge"))
    resp = _upload(client, "E_MGR")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["documentId"]


def test_部门负责人可以上传知识文档(client, org, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_upload_dir", str(tmp_path / "knowledge"))
    resp = _upload(client, "E_LEAD")
    assert resp.status_code == 200, resp.text


def test_普通员工看上传列表只能看到自己的_空列表(client, org):
    resp = client.get("/v1/leader/knowledge/uploads", headers=_headers("E_EMP"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# 系统端知识库读 / 预览 / 删除
# ---------------------------------------------------------------------------


def test_普通员工可以读知识库_读接口有意开放(client, org):
    resp = client.get("/v1/system/knowledge/documents", headers=_headers("E_EMP"))
    assert resp.status_code == 200, resp.text


def test_普通员工可以检索知识库_读接口有意开放(client, org):
    resp = client.get(
        "/v1/system/knowledge/preview",
        params={"appliesTo": "MONTH_PLAN", "query": "权限测试"},
        headers=_headers("E_EMP"),
    )
    assert resp.status_code == 200, resp.text


def test_HR可以读知识库并检索(client, org):
    listing = client.get("/v1/system/knowledge/documents", headers=_headers("E_HR"))
    assert listing.status_code == 200, listing.text

    preview = client.get(
        "/v1/system/knowledge/preview",
        params={"appliesTo": "MONTH_PLAN", "query": "权限测试"},
        headers=_headers("E_HR"),
    )
    assert preview.status_code == 200, preview.text


def test_主管可以上传但不能删除文档(client, org, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_upload_dir", str(tmp_path / "knowledge"))
    uploaded = _upload(client, "E_MGR")
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["data"]["documentId"]

    denied = client.delete(
        f"/v1/system/knowledge/documents/{document_id}",
        headers=_headers("E_MGR"),
    )
    assert denied.status_code == 403, denied.text

    allowed = client.delete(
        f"/v1/system/knowledge/documents/{document_id}",
        headers=_headers("E_HR"),
    )
    assert allowed.status_code == 200, allowed.text
