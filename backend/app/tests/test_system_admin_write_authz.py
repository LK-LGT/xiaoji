"""🔴 **系统管理端「越权 + 假成功」回归**（2026-11-26）。

⚠️ **本文件锁住的两个真实缺陷**
------------------------------------------------------------------

**缺陷 1（P0 越权后门）**
``routes/v1/system_users.py`` 与 ``routes/v1/system_ai.py`` 原先**只有路由级
``dependencies=[Depends(get_current_user)]``** ⇒ **任何登录用户**都能：

- ``POST /v1/system/users/{id}/reset-password``
  → 把**任意人**（含 HR / 管理员）的密码改成攻击者已知口令，
    并**直接拿到明文初始口令** ⇒ **完整账号接管**；
- ``PUT /v1/system/users/{id}`` / ``POST /v1/system/users/{id}/status``
  → 改 / 禁用别人的账号；
- ``POST /v1/system/ai/configs`` → 写入 **AI 供应商 API Key**；
- ``POST /v1/system/ai/configs/{id}/enable`` → 切换**全局启用**的模型。

**缺陷 2（假成功）**
``register_employee`` / ``update_user`` / ``change_user_status`` /
``import_users`` / AI 配置端点全是**空壳** ——
返回结构完整的假对象，**数据库里什么都没变**，
而前端弹「已创建」「已保存」「配置已新增」⇒ 验收的人会以为系统跑通了。

⚠️ **断言要能抓到「修一半」**
------------------------------------------------------------------
每条都**同时**断言「无权时被拒」+「有权时能过」，
确保测的是**差异**而不是端点坏了。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    SystemAdmin,
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
    """组织 + 三个角色。

    ⚠️ **角色靠机制产生，不靠名字**：
    - HR ← ``HrDepartmentConfig``（``D_HR``）
    - 系统管理员 ← ``SystemAdmin`` 表
    - 普通员工 ← 什么都不是
    """
    db.add_all([
        Department(id="D_ROOT", name="总部", parent_id=None, active=True),
        Department(id="D_HR", name="人事行政部", parent_id="D_ROOT", active=True),
        Department(id="D_OTHER", name="其它部门", parent_id="D_ROOT", active=True),
    ])
    db.add_all([
        Employee(id="E_ADMIN", name="系统管理员", department_id="D_HR",
                 employment_status="active", account_status="enabled", token_version=1),
        Employee(id="E_HR", name="HR", department_id="D_HR",
                 employment_status="active", account_status="enabled", token_version=1),
        Employee(id="E_EMP", name="普通员工", department_id="D_OTHER",
                 employment_status="active", account_status="enabled", token_version=1),
        Employee(id="E_VICTIM", name="受害者", department_id="D_OTHER",
                 employment_status="active", account_status="enabled", token_version=1),
    ])
    db.flush()
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.add(SystemAdmin(employee_id="E_ADMIN", active=True))
    db.commit()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _headers(employee_id: str, token_version: int = 1) -> dict:
    token, _ = _generate_token(employee_id, "u", token_version)
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════
# 1. 员工管理 —— 越权
# ═══════════════════════════════════════════════════════════════════════════


class TestEmployeeEndpointAuthz:
    """🔴 员工管理的写操作必须要求「员工管理」权限。"""

    def test_普通员工不能重置他人密码(self, client, db, org):
        """🔴 **核心回归**：普通员工重置管理员密码 ⇒ 账号接管。

        ⚠️ 修复前：HTTP 200 + 返回明文初始口令
        ⇒ 攻击者拿到口令即可用管理员身份登录。
        """
        resp = client.post(
            "/v1/system/users/E_ADMIN/reset-password",
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text
        assert "initialPassword" not in resp.text

    def test_普通员工重置密码后目标口令未变(self, client, db, org):
        """⚠️ **配对**：不仅状态码要对，**数据也不能被动过**。"""
        client.post(
            "/v1/system/users/E_ADMIN/reset-password",
            headers=_headers("E_EMP"),
        )
        db.expire_all()
        target = db.get(Employee, "E_ADMIN")
        assert target.token_version == 1, "越权请求竟然改了 token_version"

    def test_普通员工不能改他人档案(self, client, db, org):
        resp = client.put(
            "/v1/system/users/E_VICTIM",
            json={"realName": "被改了", "positionName": "黑客"},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text
        db.expire_all()
        assert db.get(Employee, "E_VICTIM").name == "受害者"

    def test_普通员工不能停用他人账号(self, client, db, org):
        resp = client.post(
            "/v1/system/users/E_VICTIM/status",
            json={"status": 0},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text
        db.expire_all()
        assert db.get(Employee, "E_VICTIM").account_status == "enabled"

    def test_普通员工不能注册员工(self, client, db, org):
        resp = client.post(
            "/v1/system/users/register",
            json={"realName": "黑客", "mobile": "13800000000", "deptId": "D_OTHER", "roleId": "employee"},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text
        assert db.query(Employee).filter(Employee.name == "黑客").count() == 0

    def test_普通员工不能批量导入(self, client, db, org):
        resp = client.post(
            "/v1/system/users/import",
            files={"file": ("u.csv", b"name,mobile\nA,13800000001\n", "text/csv")},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text

    def test_普通员工不能导出员工清单(self, client, db, org):
        resp = client.get("/v1/system/users/export", headers=_headers("E_EMP"))
        assert resp.status_code == 403, resp.text

    def test_HR可以重置密码(self, client, db, org):
        """⚠️ **配对** —— 证明上一条不是「端点全坏了」。"""
        resp = client.post(
            "/v1/system/users/E_VICTIM/reset-password",
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["initialPassword"]

    def test_系统管理员可以重置密码(self, client, db, org):
        """⚠️ **配对** —— 管理员是配置维护者，必须能操作。"""
        resp = client.post(
            "/v1/system/users/E_VICTIM/reset-password",
            headers=_headers("E_ADMIN"),
        )
        assert resp.status_code == 200, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 2. 员工管理 —— 假成功
# ═══════════════════════════════════════════════════════════════════════════


class TestEmployeeEndpointReallyWrites:
    """🔴 端点必须**真的改数据库**。"""

    def test_注册员工真的建号(self, client, db, org):
        resp = client.post(
            "/v1/system/users/register",
            json={
                "realName": "新同事",
                "mobile": "13800000002",
                "deptId": "D_OTHER",
                "positionName": "剪辑",
                "roleId": "employee",
            },
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["userId"], "返回的 userId 不能是 0（空壳特征）"
        assert data["username"], "必须返回真实登录账号"
        assert data["initialPassword"], "必须返回随机初始口令"
        assert data["departmentName"] == "其它部门"

        # ⚠️ **落库验证** —— 响应对了还不够，库里必须有。
        row = db.get(Employee, data["userId"])
        assert row is not None, "库里没有这个员工 ⇒ 假成功"
        assert row.name == "新同事"
        assert row.mobile == "13800000002"
        assert row.department_id == "D_OTHER"
        assert row.position == "剪辑"
        assert row.login_account == data["username"]
        assert row.force_change_password is True
        assert row.password_hash and row.password_hash.startswith("pbkdf2_sha256$")

    def test_注册的口令不落明文(self, client, db, org):
        """🔴 明文口令**只**在响应里出现一次，库里只有哈希。"""
        resp = client.post(
            "/v1/system/users/register",
            json={"realName": "口令测试", "mobile": "13800000003", "deptId": "D_OTHER", "roleId": "employee"},
            headers=_headers("E_HR"),
        )
        plaintext = resp.json()["data"]["initialPassword"]
        row = db.get(Employee, resp.json()["data"]["userId"])
        assert plaintext not in (row.password_hash or "")
        assert plaintext not in (row.login_account or "")

    def test_注册手机号非法返回422(self, client, db, org):
        resp = client.post(
            "/v1/system/users/register",
            json={"realName": "手机号错", "mobile": "123", "deptId": "D_OTHER", "roleId": "employee"},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 422, resp.text

    def test_注册未知角色返回422(self, client, db, org):
        resp = client.post(
            "/v1/system/users/register",
            json={"realName": "角色错", "mobile": "13800000004", "deptId": "D_OTHER", "roleId": "super_god"},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 422, resp.text

    def test_更新员工真的落库(self, client, db, org):
        resp = client.put(
            "/v1/system/users/E_VICTIM",
            json={"realName": "新名字", "positionName": "运营", "deptId": "D_HR"},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 200, resp.text
        # ⚠️ 响应**不能**是空壳（realName 不能是空串）
        assert resp.json()["data"]["realName"] == "新名字"

        db.expire_all()
        row = db.get(Employee, "E_VICTIM")
        assert row.name == "新名字"
        assert row.position == "运营"
        assert row.department_id == "D_HR"

    def test_更新不存在的用户返回404(self, client, db, org):
        resp = client.put(
            "/v1/system/users/NOPE",
            json={"realName": "x"},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 404, resp.text

    def test_停用账号真的落库并吊销Token(self, client, db, org):
        resp = client.post(
            "/v1/system/users/E_VICTIM/status",
            json={"status": 0},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == 0

        db.expire_all()
        row = db.get(Employee, "E_VICTIM")
        assert row.account_status == "disabled"
        assert row.token_version == 2, "停用后旧 Token 必须失效"

    def test_停用后旧Token无法访问(self, client, db, org):
        """🔴 停用必须**真的**挡住旧 Token —— 否则等于没停用。"""
        stale = _headers("E_VICTIM", token_version=1)
        client.post(
            "/v1/system/users/E_VICTIM/status",
            json={"status": 0},
            headers=_headers("E_HR"),
        )
        resp = client.get("/v1/system/users", headers=stale)
        assert resp.status_code == 401, resp.text

    def test_不能停用自己(self, client, db, org):
        resp = client.post(
            "/v1/system/users/E_HR/status",
            json={"status": 0},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 422, resp.text

    def test_导入真的建号并统计成功失败(self, client, db, org):
        """🔴 导入必须**逐行真的建号**，并如实统计。"""
        csv_body = (
            "姓名,手机号,员工编号,部门ID,直属负责人ID,岗位\n"
            "导入甲,13800000010,10000001,D_OTHER,,剪辑\n"
            "导入乙,138,10000002,D_OTHER,,剪辑\n"
        ).encode()
        resp = client.post(
            "/v1/system/users/import",
            files={"file": ("u.csv", csv_body, "text/csv")},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] == 2
        assert data["success"] == 1, f"成功数不对：{data}"
        assert data["failed"] == 1
        assert data["errors"], "失败行必须给出原因"

        assert db.query(Employee).filter(Employee.name == "导入甲").count() == 1
        assert db.query(Employee).filter(Employee.name == "导入乙").count() == 0

    def test_导入模板不再404(self, client, db, org):
        """🔴 「下载模板」按钮原来必然 404。"""
        resp = client.get("/v1/system/users/import-template", headers=_headers("E_HR"))
        assert resp.status_code == 200, resp.text
        assert "text/csv" in resp.headers["content-type"]

    def test_导出不再404且含真实员工(self, client, db, org):
        """🔴 「导出」按钮原来必然 404。"""
        resp = client.get("/v1/system/users/export", headers=_headers("E_HR"))
        assert resp.status_code == 200, resp.text
        body = resp.content.decode("utf-8-sig")
        assert "E_VICTIM" in body
        assert "受害者" in body


# ═══════════════════════════════════════════════════════════════════════════
# 3. AI 配置 —— 越权 + 假成功
# ═══════════════════════════════════════════════════════════════════════════


class TestAiConfigAuthz:
    """🔴 AI 模型配置是**系统级配置**（含供应商凭据）。"""

    def test_普通员工不能读模型配置(self, client, db, org):
        resp = client.get("/v1/system/ai/configs", headers=_headers("E_EMP"))
        assert resp.status_code == 403, resp.text

    def test_普通员工不能写模型配置(self, client, db, org):
        resp = client.post(
            "/v1/system/ai/configs",
            json={"configName": "恶意配置", "modelName": "m", "baseUrl": "http://evil", "apiKey": "sk-evil"},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text

    def test_普通员工不能启用模型配置(self, client, db, org):
        resp = client.post("/v1/system/ai/configs/1/enable", headers=_headers("E_EMP"))
        assert resp.status_code == 403, resp.text

    def test_普通员工不能看调用日志(self, client, db, org):
        """⚠️ 调用日志含「谁问了什么」⇒ 属审计信息。"""
        resp = client.get("/v1/system/ai/call-logs", headers=_headers("E_EMP"))
        assert resp.status_code == 403, resp.text

    def test_普通员工不能发布提示词(self, client, db, org):
        resp = client.post(
            "/v1/system/ai/prompts/publish",
            json={"sceneCode": "plan", "versionNo": "1", "systemPrompt": "恶意"},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 403, resp.text

    def test_HR可以读模型配置(self, client, db, org):
        resp = client.get("/v1/system/ai/configs", headers=_headers("E_HR"))
        assert resp.status_code == 200, resp.text


class TestAiConfigReallyPersists:
    """🔴 AI 配置必须**真的存住**。"""

    def test_新增配置真的落库(self, client, db, org):
        resp = client.post(
            "/v1/system/ai/configs",
            json={
                "configName": "主力模型",
                "providerCode": "OPENAI_COMPATIBLE",
                "baseUrl": "https://api.example.com/v1",
                "modelName": "qwen-max",
                "apiKey": "sk-secret-value-123",
            },
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["id"] > 0, "返回的 id 不能是 0（空壳特征）"

        listing = client.get("/v1/system/ai/configs", headers=_headers("E_HR")).json()["data"]
        assert len(listing) == 1
        assert listing[0]["configName"] == "主力模型"

    def test_APIKey永不回传(self, client, db, org):
        """🔴 **最关键**：API Key 绝不能出现在任何响应里。"""
        client.post(
            "/v1/system/ai/configs",
            json={"configName": "c", "modelName": "m", "baseUrl": "u", "apiKey": "sk-secret-value-123"},
            headers=_headers("E_HR"),
        )
        listing = client.get("/v1/system/ai/configs", headers=_headers("E_HR"))
        assert "sk-secret-value-123" not in listing.text
        # ⚠️ 只暴露「是否已配置」
        assert listing.json()["data"][0]["apiKeyConfigured"] is True

    def test_APIKey在库里是密文(self, client, db, org):
        """🔴 库里也不能有明文。"""
        from backend.app.domain.models import AiModelConfigRow

        client.post(
            "/v1/system/ai/configs",
            json={"configName": "c", "modelName": "m", "baseUrl": "u", "apiKey": "sk-secret-value-123"},
            headers=_headers("E_HR"),
        )
        row = db.execute(select(AiModelConfigRow)).scalars().first()
        assert row is not None
        assert "sk-secret-value-123" not in (row.api_key_enc or "")
        assert row.api_key_enc.startswith("v1:")

    def test_启用配置真的落库且唯一(self, client, db, org, monkeypatch):
        """🔴 「全局只能有一条启用」必须真的成立。

        2026-11-27 起 enable 会先推送给 agent-service；本测试只关心
        「数据库里启用态唯一」，因此把推送替换为 no-op。
        """
        from backend.app.services import ai_model_config_service

        monkeypatch.setattr(
            ai_model_config_service, "apply_runtime_config", lambda row: {"status": "applied"}
        )
        first = client.post(
            "/v1/system/ai/configs",
            json={"configName": "A", "modelName": "m", "baseUrl": "u", "apiKey": "sk-a"},
            headers=_headers("E_HR"),
        ).json()["data"]
        second = client.post(
            "/v1/system/ai/configs",
            json={"configName": "B", "modelName": "m", "baseUrl": "u", "apiKey": "sk-b"},
            headers=_headers("E_HR"),
        ).json()["data"]

        client.post(f"/v1/system/ai/configs/{first['id']}/enable", headers=_headers("E_HR"))
        client.post(f"/v1/system/ai/configs/{second['id']}/enable", headers=_headers("E_HR"))

        listing = client.get("/v1/system/ai/configs", headers=_headers("E_HR")).json()["data"]
        enabled = [c for c in listing if c["status"] == "ENABLED"]
        assert len(enabled) == 1, f"启用态不唯一：{listing}"
        assert enabled[0]["id"] == second["id"]

    def test_发布提示词真的落库(self, client, db, org):
        resp = client.post(
            "/v1/system/ai/prompts/publish",
            json={"sceneCode": "plan", "versionNo": "v1", "systemPrompt": "你是计划助手"},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["id"] > 0

        listing = client.get("/v1/system/ai/prompts", headers=_headers("E_HR")).json()["data"]
        assert len(listing) == 1
        assert listing[0]["systemPrompt"] == "你是计划助手"

    def test_提示词缺场景返回422(self, client, db, org):
        resp = client.post(
            "/v1/system/ai/prompts/publish",
            json={"versionNo": "v1"},
            headers=_headers("E_HR"),
        )
        assert resp.status_code == 422, resp.text


class TestSecretBox:
    """🔴 敏感值信封加密。"""

    def test_加解密往返(self):
        from backend.app.services import secret_box

        envelope = secret_box.encrypt_secret("sk-plain")
        assert envelope != "sk-plain"
        assert secret_box.decrypt_secret(envelope) == "sk-plain"

    def test_密文每次不同(self):
        """🔴 相同明文必须产生不同密文（nonce 随机）。"""
        from backend.app.services import secret_box

        assert secret_box.encrypt_secret("x") != secret_box.encrypt_secret("x")

    def test_密文被篡改会失败(self):
        from backend.app.services import secret_box

        envelope = secret_box.encrypt_secret("sk-plain")
        tampered = envelope[:-4] + ("AAAA" if not envelope.endswith("AAAA") else "BBBB")
        with pytest.raises(ValueError):
            secret_box.decrypt_secret(tampered)

    def test_空值不产生密文(self):
        from backend.app.services import secret_box

        assert secret_box.encrypt_secret("") == ""
        assert secret_box.decrypt_secret("") == ""
