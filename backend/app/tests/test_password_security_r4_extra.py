"""R4 补充测试 —— 覆盖本轮新增/修正的行为。

本文件补的是 ``test_password_security.py`` 之外的**缺口**：

1. **测试账号（TEST_USERS）也走哈希路径**
   —— 过去是 ``user_info["password"] != password`` 明文比对；
   本轮改为导入时算哈希、登录时哈希比对。
2. **管理员重置密码真的落库**
   —— 过去是空壳 ``{"initialPassword": ""}``，什么都没做。
3. **改密接口校验 Token 版本**
   —— 管理员重置密码后 token_version 递增，此时旧 Token 必须失效，
   否则「重置密码」形同虚设。
4. **密码强度口径与前端一致（8–32 位）**
   —— 前端 ChangePassword.vue / ChangePasswordDialog.vue 都是 8–32 位。
5. **存量弱口令迁移不能被强度规则挡住**
   —— BUG-R4-01：``hash_password`` 无条件跑长度校验，
   导致 6 位的 ``"123456"`` 直接抛异常 → 迁移被 except 吞掉 →
   **哈希没写进去、强制改密没置位**，弱口令永久可用。

⚠️ 本文件**不**重复 ``test_password_security.py`` 已覆盖的用例。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import Base, Employee
from backend.app.main import app
from backend.app.routes import auth
from backend.app.routes.auth import TEST_USERS, _generate_token
from backend.app.routes.v1.system_users import _generate_initial_password
from backend.app.services import password_hasher


@pytest.fixture
def api():
    """共享连接的应用 + 会话（与 test_password_security.py 同款）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


@pytest.fixture
def client(api):
    return api[0]


@pytest.fixture
def db(api):
    return api[1]


def _auth_headers(db: Session, employee_id: str, token_version: int = 1) -> dict:
    token, _ = _generate_token(employee_id, "u", token_version)
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════
# 0. 「谁能重置他人密码」的权限闸（2026-11-26 补）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def admin_user(db: Session) -> str:
    """造一个**系统管理员**（有 ``MANAGE_EMPLOYEES`` 权限位）。

    ⚠️ **为什么要专门造一个管理员**
    ``POST /system/users/{id}/reset-password`` 现在要求
    ``MANAGE_EMPLOYEES``（只有 HR / 系统管理员有）。
    ⇒ 下面几个「重置密码真的落库」的测试，**调用者必须是管理员**，
    否则会被 403 挡掉（那是**正确**行为，不是 bug）。

    ⚠️ **不要用被重置的那个人自己当调用者** ——
    那正是这个闸要挡的越权路径。
    """
    from backend.app.domain.models import SystemAdmin

    admin = Employee(
        id="R4_ADMIN",
        name="R4 管理员",
        login_account="r4_admin",
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    db.add(admin)
    db.add(SystemAdmin(employee_id="R4_ADMIN", active=True))
    db.commit()
    return "R4_ADMIN"


# ═══════════════════════════════════════════════════════════════════════════
# 1. 测试账号走哈希路径
# ═══════════════════════════════════════════════════════════════════════════


def test_test_users_store_hash_not_plaintext():
    """TEST_USERS 里必须只有哈希，不能有明文口令字段。

    ⚠️ 这**不是**真正的安全提升（明文仍在源码常量里，测试账号必须能被登录），
    真实价值是：测试账号与真实员工走**同一条** verify_password 校验路径，
    不会因「测试账号走明文分支」而掩盖真实账号的哈希缺陷。
    """
    for username, info in TEST_USERS.items():
        assert "password" not in info, f"{username} 仍存明文口令字段"
        assert "password_hash" in info, f"{username} 缺哈希字段"
        assert info["password_hash"].startswith("pbkdf2_sha256$")


def test_test_users_verify_with_hash():
    """哈希串能验出正确口令、验不出错误口令。

    ⚠️ **口令来源（2026-11-24 变更）**
    源码里**不再有**明文口令常量（`_TEST_USER_PLAINTEXT` 已删除）
    ⇒ 口令只能来自环境变量 ``TEST_USER_PASSWORDS``（由根 conftest 注入）。
    """
    import json
    import os

    plaintexts = json.loads(os.environ[auth._TEST_USER_PLAINTEXT_ENV])
    for username, info in TEST_USERS.items():
        assert password_hasher.verify_password(
            plaintexts[username], info["password_hash"]
        ), f"{username} 正确口令验不过"
        assert not password_hasher.verify_password(
            "definitely-wrong", info["password_hash"]
        ), f"{username} 错误口令居然能验过"


def test_test_user_login_works_via_hash_path(client: TestClient):
    """测试账号仍能正常登录（哈希化不能把自动化测试打瞎）。"""
    resp = client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["accessToken"]


def test_test_user_login_rejects_wrong_password(client: TestClient):
    """错误口令仍然被拒。"""
    resp = client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_no_plaintext_password_comparison_in_auth_route():
    """源码里不得再出现「明文口令直接比对」的**可执行代码**。

    这是 R4 的核心断言之一：任何 ``xxx["password"] != password`` 形式的
    明文比对都会让「哈希保护」形同虚设。

    ⚠️ 只看**代码**，不看注释 —— 注释里可以（也应该）写明
    「过去就是明文比对」这类历史说明，否则这条断言会被注释自己绊倒。
    """
    import io
    import pathlib
    import tokenize

    source = pathlib.Path(auth.__file__).read_text(encoding="utf-8")

    # 用 tokenize 剥掉注释与字符串字面量，只留真正的代码 token。
    code_only: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            code_only.append(tok.string)
    except tokenize.TokenError:  # pragma: no cover - 源码损坏时兜底
        pytest.skip("auth.py 无法 tokenize")

    code = " ".join(code_only)
    offenders = [
        pattern
        for pattern in ('"password"] !=', "'password'] !=", '"password" !=', "'password' !=")
        if pattern in code
    ]
    assert not offenders, f"仍有明文口令比对：{offenders}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 管理员重置密码（system_users）
# ═══════════════════════════════════════════════════════════════════════════


def test_generate_initial_password_is_random_and_valid():
    """初始口令随机、可通过强度校验、长度 12。"""
    a = _generate_initial_password()
    b = _generate_initial_password()
    assert a != b, "初始口令必须每次都不同"
    assert len(a) == 12
    password_hasher.validate_password_strength(a)  # 不抛异常即通过


def test_generate_initial_password_avoids_ambiguous_chars():
    """初始口令不含易混淆字符（0/O/1/l/I）。"""
    for _ in range(50):
        pwd = _generate_initial_password()
        assert not set(pwd) & set("0O1lI"), f"含易混淆字符：{pwd}"


def test_reset_password_writes_hash_and_revokes_tokens(
    client: TestClient, db: Session, admin_user: str
):
    """管理员重置密码 → 落哈希、置强制改密、吊销旧 Token。

    之前该端点是空壳（返回 ``{"initialPassword": ""}``），用户的老密码**依然能用**。

    ⚠️ **调用者是管理员，不是被重置的人** —— 该端点现在有权限闸
    （见 ``admin_user`` fixture 的说明）。
    """
    emp = Employee(
        id="R4_RESET_001",
        name="R4 待重置",
        login_account="r4_reset",
        employment_status="active",
        account_status="enabled",
        password_hash=password_hasher.hash_password("original-pass"),
        token_version=1,
    )
    db.add(emp)
    db.commit()

    admin_headers = _auth_headers(db, admin_user)
    resp = client.post(
        "/v1/system/users/R4_RESET_001/reset-password", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    initial = resp.json()["data"]["initialPassword"]
    assert initial, "必须返回初始口令"

    db.expire_all()
    refreshed = db.get(Employee, "R4_RESET_001")
    assert refreshed.password_hash is not None
    assert refreshed.password_hash.startswith("pbkdf2_sha256$")
    # 初始口令只返回一次，库里只有哈希。
    assert initial not in (refreshed.password_hash or "")
    assert password_hasher.verify_password(initial, refreshed.password_hash)
    # 强制下次登录改密。
    assert refreshed.force_change_password is True
    # 旧 Token 全部失效。
    assert refreshed.token_version == 2
    # ⚠️ 管理员自己的 Token **不受影响**（被重置的是别人）。
    assert client.get("/v1/auth/me", headers=admin_headers).status_code == 200


def test_reset_password_old_password_no_longer_works(
    client: TestClient, db: Session, admin_user: str
):
    """重置后，旧口令不能再登录。"""
    emp = Employee(
        id="R4_RESET_002",
        name="R4 待重置2",
        login_account="r4_reset2",
        employment_status="active",
        account_status="enabled",
        password_hash=password_hasher.hash_password("original-pass"),
        token_version=1,
    )
    db.add(emp)
    db.commit()

    headers = _auth_headers(db, admin_user)
    assert client.post(
        "/v1/system/users/R4_RESET_002/reset-password", headers=headers
    ).status_code == 200

    resp = client.post(
        "/v1/auth/login", json={"username": "r4_reset2", "password": "original-pass"}
    )
    assert resp.status_code == 401, "重置后旧口令仍能登录"


def test_reset_password_404_for_unknown_user(
    client: TestClient, db: Session, admin_user: str
):
    """对不存在的用户重置密码 → 404（而不是假装成功）。"""
    emp = Employee(
        id="R4_RESET_003",
        name="R4 管理员",
        login_account="r4_reset3",
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    db.add(emp)
    db.commit()

    headers = _auth_headers(db, admin_user)
    resp = client.post(
        "/v1/system/users/NOT_EXIST_USER/reset-password", headers=headers
    )
    assert resp.status_code == 404


def test_普通员工不能重置他人密码(client: TestClient, db: Session):
    """🔴 **越权回归**：普通员工重置**别人**密码 → 403。

    ⚠️ **这是 2026-11-26 实测到并修掉的真实越权后门**：
    该端点原先只校验了登录态 ⇒ 任何员工都能把任意人（含 HR / 管理员）
    的密码改成攻击者已知口令，并**直接拿到明文初始口令** ⇒ 完整账号接管。

    ⇒ 必须用**动作权限位** ``MANAGE_EMPLOYEES`` 挡住，
    而不是靠前端菜单串（那是可见性标识，不是动作鉴权）。
    """
    attacker = Employee(
        id="R4_ATTACKER",
        name="R4 普通员工",
        login_account="r4_attacker",
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    victim = Employee(
        id="R4_VICTIM",
        name="R4 受害者",
        login_account="r4_victim",
        employment_status="active",
        account_status="enabled",
        password_hash=password_hasher.hash_password("original-pass"),
        token_version=1,
    )
    db.add_all([attacker, victim])
    db.commit()

    headers = _auth_headers(db, "R4_ATTACKER")
    resp = client.post(
        "/v1/system/users/R4_VICTIM/reset-password", headers=headers
    )
    assert resp.status_code == 403, f"普通员工竟能重置他人密码：{resp.text}"

    # ⚠️ 受害者的密码**必须没被改**。
    db.expire_all()
    refreshed = db.get(Employee, "R4_VICTIM")
    assert password_hasher.verify_password("original-pass", refreshed.password_hash)
    assert refreshed.token_version == 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. 改密接口的 Token 版本校验
# ═══════════════════════════════════════════════════════════════════════════


def test_change_password_rejects_stale_token_version(client: TestClient, db: Session):
    """Token 版本落后于库里 → 401。

    场景：管理员重置了该用户密码（token_version 从 1 递增到 2），
    但用户手里还握着版本 1 的旧 Token。此时他若能改密，
    就等于绕过了「重置密码」这个动作。
    """
    emp = Employee(
        id="R4_STALE_001",
        name="R4 旧令牌",
        login_account="r4_stale",
        employment_status="active",
        account_status="enabled",
        password_hash=password_hasher.hash_password("original-pass"),
        token_version=2,  # 库里已是 2
    )
    db.add(emp)
    db.commit()

    # 签发的是 ver=1 的 Token（落后）
    stale_headers = _auth_headers(db, "R4_STALE_001", token_version=1)
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "brand-new-pass"},
        headers=stale_headers,
    )
    assert resp.status_code == 401, resp.text


def test_change_password_rejects_exited_employee(client: TestClient, db: Session):
    """离职员工不能改密。"""
    emp = Employee(
        id="R4_EXITED_001",
        name="R4 已离职",
        login_account="r4_exited",
        employment_status="exited",
        account_status="enabled",
        password_hash=password_hasher.hash_password("original-pass"),
        token_version=1,
    )
    db.add(emp)
    db.commit()

    headers = _auth_headers(db, "R4_EXITED_001")
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "brand-new-pass"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_change_password_rejects_disabled_account(client: TestClient, db: Session):
    """禁用账号不能改密。"""
    emp = Employee(
        id="R4_DISABLED_001",
        name="R4 已禁用",
        login_account="r4_disabled",
        employment_status="active",
        account_status="disabled",
        password_hash=password_hasher.hash_password("original-pass"),
        token_version=1,
    )
    db.add(emp)
    db.commit()

    headers = _auth_headers(db, "R4_DISABLED_001")
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "brand-new-pass"},
        headers=headers,
    )
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 4. 强度规则与前端对齐（8–32 位）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("password", ["short7c", "a" * 33, "x" * 200])
def test_new_password_outside_8_to_32_rejected(password: str):
    """新设密码超出 8–32 位 → 拒绝（与前端表单规则一致）。"""
    with pytest.raises(password_hasher.PasswordStrengthError):
        password_hasher.validate_password_strength(password)


@pytest.mark.parametrize("password", ["abcd1234", "A" * 32, "x" * 8])
def test_new_password_within_8_to_32_accepted(password: str):
    """8–32 位（含边界）→ 通过。"""
    password_hasher.validate_password_strength(password)


def test_frontend_and_backend_password_length_rules_agree():
    """后端长度规则必须与前端表单规则**完全一致**。

    前端：frontend/src/views/ChangePassword.vue
          frontend/src/views/profile/ChangePasswordDialog.vue
    两者都是 8–32 位。后端若用别的数字，会出现「前端放行后端拒绝」
    或「前端拒绝后端放行」的口径分裂。
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3]
    frontend_dir = root / "frontend" / "src" / "views"

    found: set[tuple[int, int]] = set()
    for name in ("ChangePassword.vue", "profile/ChangePasswordDialog.vue"):
        path = frontend_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # 形如 "新密码长度为8至32位"
        for match in re.finditer(r"(\d+)\s*至\s*(\d+)\s*位", text):
            found.add((int(match.group(1)), int(match.group(2))))

    assert found, "未在前端找到密码长度规则（前端结构可能变了，需同步核对）"
    for low, high in found:
        assert password_hasher.MIN_PASSWORD_LENGTH == low, (
            f"前端下限 {low} 与后端 {password_hasher.MIN_PASSWORD_LENGTH} 不一致"
        )
        assert password_hasher.MAX_PASSWORD_LENGTH == high, (
            f"前端上限 {high} 与后端 {password_hasher.MAX_PASSWORD_LENGTH} 不一致"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. 存量弱口令迁移（BUG-R4-01 回归）
# ═══════════════════════════════════════════════════════════════════════════


def test_hash_password_can_migrate_weak_legacy_password():
    """enforce_strength=False → 6 位弱口令也能哈希（迁移专用）。"""
    hashed = password_hasher.hash_password("123456", enforce_strength=False)
    assert hashed.startswith("pbkdf2_sha256$")
    assert password_hasher.verify_password("123456", hashed)


def test_hash_password_rejects_weak_password_by_default():
    """默认走强度校验 → 6 位口令被拒。"""
    with pytest.raises(password_hasher.PasswordStrengthError):
        password_hasher.hash_password("123456")


def test_legacy_migration_actually_writes_hash(client: TestClient, db: Session):
    """弱口令登录后哈希**真的**写进了库，且强制改密标记**真的**置位。

    这是 BUG-R4-01 的回归断言：
    旧实现里 hash_password 无条件跑长度校验 → 6 位口令抛异常 →
    except 吞掉 → **哈希没写进去、强制改密没置位**，
    结果弱口令永久可用，迁移形同虚设（而且看起来"成功"了）。
    """
    legacy = Employee(
        id="R4_WEAK_001",
        name="R4 弱口令",
        login_account="r4_weak",
        employment_status="active",
        account_status="enabled",
        password_hash=None,
        token_version=1,
    )
    db.add(legacy)
    db.commit()

    resp = client.post(
        "/v1/auth/login", json={"username": "r4_weak", "password": "123456"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["forceChangePassword"] is True

    db.expire_all()
    refreshed = db.get(Employee, "R4_WEAK_001")
    assert refreshed.password_hash is not None, "弱口令哈希没写进去"
    assert refreshed.password_hash.startswith("pbkdf2_sha256$")
    assert refreshed.force_change_password is True, "强制改密标记没置位"


def test_legacy_migration_allows_weak_password_to_change_it(client: TestClient, db: Session):
    """弱口令登录后能用它改成强口令 —— 迁移路径必须走得通。"""
    legacy = Employee(
        id="R4_WEAK_002",
        name="R4 弱口令2",
        login_account="r4_weak2",
        employment_status="active",
        account_status="enabled",
        password_hash=None,
        token_version=1,
    )
    db.add(legacy)
    db.commit()

    login = client.post(
        "/v1/auth/login", json={"username": "r4_weak2", "password": "123456"}
    )
    assert login.status_code == 200, login.text
    token = login.json()["data"]["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "123456", "newPassword": "brand-new-pass"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    # 新口令可用，旧口令失效。
    assert client.post(
        "/v1/auth/login", json={"username": "r4_weak2", "password": "brand-new-pass"}
    ).status_code == 200
    assert client.post(
        "/v1/auth/login", json={"username": "r4_weak2", "password": "123456"}
    ).status_code == 401
