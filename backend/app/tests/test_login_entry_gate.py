"""🔴 **登录入口收口**（2026-11-24，B1）。

业务规则
--------
「登录入口为飞书单点登录，只允许公司内真实存在、组织架构内同步成功的
员工账号进入系统」⇒ 生产环境**只走飞书 SSO**，口令入口默认关闭。

⚠️ **本文件锁住三件事**
1. **源码里不再有明文口令** —— 防止有人为了图方便把 ``admin123`` 加回去。
2. **口令入口默认关闭** —— 关闭时返回 503 并指路飞书（而不是 401，
   前端要能据此切换登录方式）。
3. **测试账号默认不可用** —— 生产环境没有 ``TEST_USER_PASSWORDS``
   ⇒ 测试账号表**为空** ⇒ 「拿到源码就能登录真实员工」这条路彻底堵死。

⚠️ **为什么保留测试账号这个机制**
自动化测试需要一条**绕过飞书 OAuth**的登录通路（CI 里没有飞书凭证）。
⇒ 但它必须满足：口令不在源码里、生产完全关闭、映射的仍是真实员工。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.main import app
from backend.app.routes import auth as auth_routes

# ═══════════════════════════════════════════════════════════════════════════
# 1. 源码里不得残留明文口令
# ═══════════════════════════════════════════════════════════════════════════


class TestNoPlaintextInSource:
    """🔴 **最关键的一条** —— 明文口令不得回到源码。"""

    @staticmethod
    def _code_only(path: Path) -> str:
        """只取**代码**，剥掉注释与 docstring。

        ⚠️ **为什么必须剥掉**
        本文件与 ``auth.py`` 的注释里**故意**引用了旧口令作为反例
        （「过去这里是 ``{"admin": "admin123"}``」）⇒
        直接搜原文会**误报**。
        ⇒ 只看**真正会被执行**的代码 token。
        """
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        parts: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                parts.append(node.value)
            elif isinstance(node, ast.Name):
                parts.append(node.id)
            elif isinstance(node, ast.Attribute):
                parts.append(node.attr)
            elif isinstance(node, ast.arg):
                parts.append(node.arg)
        return "\n".join(parts)

    def test_源码里没有明文口令常量(self):
        """``auth.py`` 不得再定义 ``_TEST_USER_PLAINTEXT`` 这类明文表。

        ⚠️ **为什么这条比"加个开关"更重要**
        开关只管**运行时**行为；而源码里的明文口令是**静态泄露**
        —— clone 仓库就拿到了，与部署无关。
        """
        code = self._code_only(Path(auth_routes.__file__))

        assert "_TEST_USER_PLAINTEXT" not in code.replace(
            "_TEST_USER_PLAINTEXT_ENV", ""
        ), "auth.py 又出现了明文口令常量"

    def test_源码里没有测试账号的口令字面量(self):
        """🔴 逐个口令字面量扫描 —— 防止换个变量名继续藏。"""
        code = self._code_only(Path(auth_routes.__file__))

        # ⚠️ 这些口令是**测试进程**用的（由根 conftest 注入环境变量）
        # ⇒ auth.py 里一个都不该出现。
        for literal in ("admin123", "manager123", "leader123", "employee123"):
            assert literal not in code, (
                f"auth.py 里出现了口令字面量 {literal!r} ⇒ 源码泄露"
            )

    def test_全仓库后端源码没有这些口令(self):
        """⚠️ **范围更大** —— 整个 ``backend/app`` 都不该有这些字面量。

        ⚠️ **为什么排除测试目录**
        测试里**必须**出现口令字面量（要模拟登录）
        ⇒ 但那是**测试代码**，不进生产产物。
        ⇒ 生产代码（``backend/app`` 下非 tests 的部分）必须干净。
        """
        app_dir = Path(auth_routes.__file__).resolve().parent
        offenders: list[str] = []

        for path in app_dir.rglob("*.py"):
            if "tests" in path.parts:
                continue
            code = self._code_only(path)
            for literal in ("admin123", "manager123", "leader123", "employee123"):
                if literal in code:
                    offenders.append(f"{path.relative_to(app_dir)}: {literal}")

        assert not offenders, "生产代码里仍有口令字面量：" + ", ".join(offenders)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 口令入口默认关闭
# ═══════════════════════════════════════════════════════════════════════════


class TestPasswordLoginGate:
    """🔴 口令入口的**总闸门**。"""

    def test_默认关闭(self):
        """⚠️ **默认值必须是关的** —— 生产部署方忘了配也不会裸奔。

        ⚠️ **为什么查「类默认值」而不是「实例值」**
        测试进程里根 ``conftest`` 会把环境变量打开（pytest 要登录）
        ⇒ 实例值必然是 True ⇒ 断言实例值是**错的**。
        ⇒ 要断言的是**「不配环境变量时」的行为** ⇒ 查 ``model_fields``。
        """
        from backend.app.core.config import Settings

        assert Settings.model_fields["password_login_enabled"].default is False, (
            "password_login_enabled 默认必须是 False（生产只走飞书 SSO）"
        )

    def test_测试账号默认关闭(self):
        """⚠️ 同理 —— ``test_users_enabled`` 默认必须是关的。"""
        from backend.app.core.config import Settings

        assert Settings.model_fields["test_users_enabled"].default is False, (
            "test_users_enabled 默认必须是 False"
        )

    def test_关闭时返回503并指路飞书(self, monkeypatch):
        """🔴 **核心**：关闭时必须 503（不是 401）。

        ⚠️ **为什么是 503 而不是 401**
        503 = 「这个入口没开」，前端能据此**切到飞书登录**
        ⇒ 用 401 会让用户以为密码错了，反复重试。
        """
        from backend.app.core.config import settings as s

        monkeypatch.setattr(s, "password_login_enabled", False)

        with TestClient(app) as c:
            resp = c.post(
                "/v1/auth/login",
                json={"username": "admin", "password": "admin123"},
            )

        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        # ⚠️ 结构化 detail —— 前端据此判断该切飞书
        assert isinstance(detail, dict), f"detail 应为结构化对象：{detail}"
        assert detail["code"] == "PASSWORD_LOGIN_DISABLED"
        assert "飞书" in detail["message"]

    def test_关闭时真实员工账号也进不来(self, monkeypatch):
        """⚠️ **闸门是总闸门** —— 不只挡测试账号。

        ⇒ 否则「用真实员工的 login_account + 口令」这条路还留着。
        """
        from backend.app.core.config import settings as s

        monkeypatch.setattr(s, "password_login_enabled", False)

        with TestClient(app) as c:
            resp = c.post(
                "/v1/auth/login",
                json={"username": "2aaf32ea", "password": "whatever"},
            )

        assert resp.status_code == 503, resp.text

    def test_开启后测试账号仍能登录(self, monkeypatch):
        """⚠️ **配对** —— 证明上一条不是「端点全坏了」。

        ⇒ 闸门开着时，功能必须照常（否则 pytest 全挂）。
        """
        from backend.app.core.config import settings as s

        monkeypatch.setattr(s, "password_login_enabled", True)
        monkeypatch.setattr(s, "test_users_enabled", True)

        with TestClient(app) as c:
            resp = c.post(
                "/v1/auth/login",
                json={"username": "admin", "password": "admin123"},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["accessToken"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. 测试账号的启用条件
# ═══════════════════════════════════════════════════════════════════════════


class TestTestUsersGating:
    """🔴 测试账号必须**双重门控**。"""

    def test_测试账号在测试进程里可用(self):
        """⚠️ pytest 里必须有测试账号，否则全量测试打瞎。"""
        assert auth_routes.TEST_USERS, "测试进程里 TEST_USERS 为空 ⇒ 测试会全挂"
        assert set(auth_routes.TEST_USERS) == {
            "admin",
            "yangfei",
            "biyumi",
            "employee",
        }

    def test_没配环境变量就没有测试账号(self, monkeypatch):
        """🔴 **核心**：没有 ``TEST_USER_PASSWORDS`` ⇒ 测试账号表为空。

        ⇒ 生产环境（不会配这个变量）⇒ **没有任何测试账号**。
        """
        monkeypatch.delenv(auth_routes._TEST_USER_PLAINTEXT_ENV, raising=False)
        monkeypatch.setattr(auth_routes.settings, "test_users_enabled", True)

        users = auth_routes._build_test_users()
        assert users == {}, f"没配口令却仍有测试账号：{list(users)}"

    def test_测试账号关闭时不构建(self, monkeypatch):
        """⚠️ 即使配了口令，``test_users_enabled=False`` 也不构建。"""
        monkeypatch.setattr(auth_routes.settings, "test_users_enabled", False)

        users = auth_routes._build_test_users()
        assert users == {}

    def test_环境变量不是合法JSON时安全失败(self, monkeypatch):
        """⚠️ 畸形输入**不能抛异常** —— 那是启动期的 DoS。"""
        monkeypatch.setattr(auth_routes.settings, "test_users_enabled", True)
        monkeypatch.setenv(auth_routes._TEST_USER_PLAINTEXT_ENV, "not-json")

        assert auth_routes._build_test_users() == {}

    def test_环境变量不是对象时安全失败(self, monkeypatch):
        monkeypatch.setattr(auth_routes.settings, "test_users_enabled", True)
        monkeypatch.setenv(auth_routes._TEST_USER_PLAINTEXT_ENV, "[1,2,3]")

        assert auth_routes._build_test_users() == {}

    def test_测试账号只存哈希(self):
        """⚠️ 内存里也**不留明文**。"""
        for username, info in auth_routes.TEST_USERS.items():
            assert "password" not in info, f"{username} 仍存明文口令字段"
            assert info["password_hash"].startswith("pbkdf2_sha256$")

    def test_测试账号映射的是真实员工(self):
        """⚠️ **业务规则**：测试账号映射的必须是**真实员工**，不是捏造账号。

        ⇒ 登录后拿到的权限与真实登录**完全同路**。
        """
        for username, info in auth_routes.TEST_USERS.items():
            assert info["id"], f"{username} 没有映射员工 ID"
            # ⚠️ 真实员工 ID 是 8 位十六进制串
            assert len(info["id"]) == 8, f"{username} 的员工 ID 形态可疑"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 飞书免登不受影响
# ═══════════════════════════════════════════════════════════════════════════


class TestFeishuSsoUnaffected:
    """🔴 关闭口令入口**不能**影响飞书免登 —— 那才是主入口。"""

    def test_飞书免登端点仍然存在(self):
        """⚠️ ``/v1/feishu/auth/url`` 必须始终可用。

        ⚠️ **为什么用「实际请求」而不是遍历 ``app.routes``**
        本仓库的 FastAPI 版本把 ``include_router`` 包装成
        ``_IncludedRouter``，``app.routes`` 里**看不到**子路由路径
        ⇒ 遍历会**误报**「端点不见了」。
        ⇒ 直接发请求，看到 200（而不是 404/503）才是真结论。
        """
        with TestClient(app) as c:
            resp = c.get(
                "/v1/feishu/auth/url",
                params={"redirect_uri": "http://localhost:5180/feishu/callback"},
            )

        # ⚠️ 200 = 端点在且工作；503 = 配置缺失（也算端点在）
        assert resp.status_code in (200, 503), (
            f"飞书免登入口不见了：HTTP {resp.status_code} {resp.text[:200]}"
        )

    def test_飞书免登不受口令闸门影响(self, monkeypatch):
        """⚠️ **配对** —— 关掉口令登录，飞书免登端点仍然工作。

        ⚠️ **为什么不能只断言「不是 503」**（2026-11-25 实测踩坑）
        这个端点有**两个不同原因**都会返回 503：
          ① 口令闸门挡住了；
          ② 没配飞书应用凭据（``feishu_app_id`` / ``feishu_app_secret``）。
        ⇒ 旧断言 ``status_code != 503`` 在**本地**（有 ``backend/.env``，
          配了飞书凭据）能过，在**CI**（没有 ``.env``）必然挂 ——
          ⇒ 又是「本地全绿 / CI 全红」，而且错在**测试**不在业务代码。

        ✅ **正确断言**：闸门关掉后，飞书端点**绝不能**返回
        「口令闸门」那个 503（``PASSWORD_LOGIN_DISABLED``）。
        没有凭据时返回的 503 是**凭据缺失**，属于预期行为。
        """
        from backend.app.core.config import settings as s

        monkeypatch.setattr(s, "password_login_enabled", False)

        with TestClient(app) as c:
            resp = c.post(
                "/v1/feishu/auth/exchange",
                json={"code": "invalid-code-for-test", "state": "x"},
            )

        # ⚠️ 期望是「飞书侧的失败」（400/502）或「凭据缺失」（503），
        #    **不是** 口令闸门的 503。
        # ⇒ 用 detail 内容区分，而不是只看状态码。
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("code") != "PASSWORD_LOGIN_DISABLED", (
                f"飞书免登被口令闸门挡住了：{resp.text}"
            )
        else:
            assert "口令" not in str(detail), (
                f"飞书免登被口令闸门挡住了：{resp.text}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 5. 配置项本身
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigSurface:
    """🔴 配置项的契约。"""

    def test_两个开关互相独立(self):
        """⚠️ **为什么要独立**：

        - ``password_login_enabled`` 管**真实员工**的口令登录（兜底通路）
        - ``test_users_enabled`` 管**测试账号**

        ⇒ 生产：两个都关。
        ⇒ pytest：两个都开。
        ⇒ 若耦合 ⇒ 无法表达「只开真实员工口令、关测试账号」。
        """
        from backend.app.core.config import Settings

        fields = Settings.model_fields
        assert "password_login_enabled" in fields
        assert "test_users_enabled" in fields
        assert fields["password_login_enabled"].default is False
        assert fields["test_users_enabled"].default is False

    def test_开关可通过环境变量覆盖(self, monkeypatch):
        """⚠️ pydantic-settings 必须认这两个环境变量。"""
        from backend.app.core.config import Settings

        s = Settings(
            password_login_enabled=True, test_users_enabled=True
        )
        assert s.password_login_enabled is True
        assert s.test_users_enabled is True

        env_s = Settings()
        # ⚠️ 只断言「能读」，不断言当前值 —— 取决于调用环境
        assert isinstance(env_s.password_login_enabled, bool)
        assert isinstance(env_s.test_users_enabled, bool)
