"""R4 —— 密码哈希 / 强制改密 / 登出吊销 的回归测试。

背景
----
盘点项 R4：登录逻辑是 ``if password != "123456"`` —— **全员共用明文口令**，
等于没设密码；``/change-password`` 是**空实现**（直接返回成功）。
盘点项 R14：``/logout`` 只返回「登出成功」，服务端 Token 依然有效。

本文件覆盖：

1. **哈希服务** —— 加盐、恒定时间比较、强度校验、格式兼容；
2. **登录** —— 哈希校验、弱口令渐进迁移、错误密码拒绝；
3. **改密** —— 真落库、旧密码校验、强度校验、吊销旧 Token；
4. **登出** —— 真的让 Token 失效。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.main import app
from backend.app.core.db import get_db
from backend.app.domain.models import Base, Employee
from backend.app.routes.auth import _generate_token
from backend.app.services import password_hasher


@pytest.fixture
def api():
    """一套共享连接的应用 + 会话。

    ⚠️ SQLite 的「同线程」限制：``TestClient`` 在**另一个线程**里跑端点，
    若引擎没开 ``check_same_thread=False`` 会报
    「SQLite objects created in a thread can only be used in that same thread」。
    所以这里用 ``StaticPool``（所有连接共享同一个内存库），
    并把 ``get_db`` 依赖指向同一个 session —— 端点读写的是同一份数据。
    """
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
    """只要 client 的测试用这个。"""
    return api[0]


@pytest.fixture
def db(api):
    """只要 db 的测试用这个（与 client 共享同一个会话）。"""
    return api[1]


# ═══════════════════════════════════════════════════════════════════════════
# 1. 哈希服务（纯函数，不需要 DB）
# ═══════════════════════════════════════════════════════════════════════════


def test_hash_password_produces_self_describing_string():
    """哈希串必须自描述：算法$轮数$盐$哈希，四个字段。"""
    h = password_hasher.hash_password("correct horse battery")
    parts = h.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) >= 600_000
    assert parts[2] and parts[3]


def test_same_password_hashes_differently():
    """同密码两次哈希必须不同（随机盐）—— 否则彩虹表可反查。"""
    a = password_hasher.hash_password("same-password")
    b = password_hasher.hash_password("same-password")
    assert a != b
    # 但两者都能校验通过。
    assert password_hasher.verify_password("same-password", a)
    assert password_hasher.verify_password("same-password", b)


def test_verify_password_accepts_correct_and_rejects_wrong():
    h = password_hasher.hash_password("right-password")
    assert password_hasher.verify_password("right-password", h) is True
    assert password_hasher.verify_password("wrong-password", h) is False


@pytest.mark.parametrize(
    "stored",
    [None, "", "garbage", "pbkdf2_sha256$notanint$c2FsdA==$aGFzaA==", "a$b$c$d"],
)
def test_verify_password_returns_false_for_malformed_stored_hash(stored):
    """脏数据不能导致 500 —— 一律按校验失败处理。"""
    assert password_hasher.verify_password("whatever", stored) is False


def test_verify_password_rejects_empty_password():
    h = password_hasher.hash_password("valid-password")
    assert password_hasher.verify_password("", h) is False


@pytest.mark.parametrize("password", ["", "12345", "x" * 33])
def test_hash_password_enforces_length_bounds(password):
    """过短/过长都要拒绝（上限防止 PBKDF2 计算量失控）。"""
    with pytest.raises(password_hasher.PasswordStrengthError):
        password_hasher.hash_password(password)


def test_needs_rehash_detects_missing_or_outdated():
    assert password_hasher.needs_rehash(None) is True
    assert password_hasher.needs_rehash("") is True
    assert password_hasher.needs_rehash("garbage") is True
    # 轮数低于阈值 → 需重算。
    assert password_hasher.needs_rehash("pbkdf2_sha256$1000$c2FsdA==$aGFzaA==") is True
    # 当前参数 → 不需重算。
    current = password_hasher.hash_password("valid-password")
    assert password_hasher.needs_rehash(current) is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. 登录
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def employee_with_password(db: Session):
    """一个已设置哈希密码的在职员工。"""
    emp = Employee(
        id="R4_EMP_001",
        name="R4 测试员工",
        login_account="r4_emp",
        employment_status="active",
        account_status="enabled",
        password_hash=password_hasher.hash_password("original-pass"),
        token_version=1,
    )
    db.add(emp)
    db.commit()
    return emp


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )


def test_login_accepts_hashed_password(client: TestClient, employee_with_password):
    """哈希密码正确 → 登录成功。"""
    resp = _login(client, "r4_emp", "original-pass")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["accessToken"]


def test_login_rejects_wrong_password(client: TestClient, employee_with_password):
    """密码错误 → 401，且**不泄露**账号是否存在。"""
    resp = _login(client, "r4_emp", "wrong-pass")
    assert resp.status_code == 401
    assert "用户名或密码错误" in resp.text


def test_login_never_returns_plaintext_password(client: TestClient, employee_with_password):
    """响应里绝不能出现密码或哈希本体。"""
    resp = _login(client, "r4_emp", "original-pass")
    body = resp.text
    assert "original-pass" not in body
    assert "pbkdf2_sha256" not in body


def test_legacy_plaintext_login_migrates_to_hash(
    client: TestClient, db: Session, employee_with_password
):
    """⚠️ 渐进迁移：明文口令登录成功后**立即补写哈希**并强制改密。

    这是 R4 迁移的核心路径 —— 不能锁死在职用户，也不能让弱口令一直可用。
    """
    legacy = Employee(
        id="R4_EMP_LEGACY",
        name="R4 老账号",
        login_account="r4_legacy",
        employment_status="active",
        account_status="enabled",
        password_hash=None,  # 还没迁移
        token_version=1,
    )
    db.add(legacy)
    db.commit()

    resp = _login(client, "r4_legacy", "123456")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["forceChangePassword"] is True

    db.expire_all()
    refreshed = db.get(Employee, "R4_EMP_LEGACY")
    assert refreshed.password_hash is not None
    assert refreshed.password_hash.startswith("pbkdf2_sha256$")
    assert refreshed.force_change_password is True


def test_legacy_plaintext_no_longer_works_after_migration(
    client: TestClient, db: Session
):
    """迁移完成后，弱口令立即失效 —— 库里已有哈希就不能再走明文路径。"""
    emp = Employee(
        id="R4_EMP_MIG",
        name="R4 已迁移账号",
        login_account="r4_mig",
        employment_status="active",
        account_status="enabled",
        password_hash=password_hasher.hash_password("strong-password"),
        token_version=1,
    )
    db.add(emp)
    db.commit()

    resp = _login(client, "r4_mig", "123456")
    assert resp.status_code == 401


def test_employee_without_hash_and_wrong_password_is_rejected(client: TestClient, db: Session):
    """无哈希 + 密码不等于明文兼容值 → 401。"""
    emp = Employee(
        id="R4_EMP_NOHASH",
        name="R4 无哈希",
        login_account="r4_nohash",
        employment_status="active",
        account_status="enabled",
        password_hash=None,
        token_version=1,
    )
    db.add(emp)
    db.commit()

    assert _login(client, "r4_nohash", "anything-else").status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 2.5 BUG-R4-01 / BUG-R6-16 回归
#
# BUG-R4-01：存量弱口令明文是 ``"123456"``（**6 位**），
#   而 ``password_hasher.MIN_PASSWORD_LENGTH = 8``。
#   原实现 ``_upgrade_legacy_password`` 直接调 ``hash_password(password)``
#   → 抛 PasswordStrengthError → 被 ``except Exception`` 吞掉 → rollback
#   → **哈希没写进去、force_change_password 也没置位**。
#   后果：弱口令永久可用，「强制首次改密」形同虚设。
#
# BUG-R6-16：补写哈希后，弱口令**仍能登录**（哈希能验过），
#   只靠前端 ``forceChangePassword`` 挡在业务页外 —— 绕过前端直接打 API 就畅通无阻。
#   现在弱口令在「强制改密」期间直接判失败。
# ═══════════════════════════════════════════════════════════════════════════


def test_hash_password_can_migrate_weak_legacy_password():
    """BUG-R4-01：``enforce_strength=False`` 时允许哈希 6 位弱口令。

    否则存量账号的渐进迁移会直接抛 PasswordStrengthError。
    """
    # 默认（用户新设密码）仍必须拒绝弱口令
    with pytest.raises(password_hasher.PasswordStrengthError):
        password_hasher.hash_password("123456")

    # 迁移场景允许
    hashed = password_hasher.hash_password("123456", enforce_strength=False)
    assert hashed.startswith("pbkdf2_sha256$")
    assert password_hasher.verify_password("123456", hashed)


def test_legacy_migration_writes_hash_for_six_char_password(
    client: TestClient, db: Session
):
    """BUG-R4-01 端到端：6 位弱口令登录后，**哈希必须真的写进库**。

    这是 BUG-R4-01 的核心断言 —— 原实现这里会静默失败。
    """
    legacy = Employee(
        id="R4_EMP_WEAK6",
        name="R4 六位弱口令",
        login_account="r4_weak6",
        employment_status="active",
        account_status="enabled",
        password_hash=None,
        token_version=1,
    )
    db.add(legacy)
    db.commit()

    resp = _login(client, "r4_weak6", "123456")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["forceChangePassword"] is True

    db.expire_all()
    refreshed = db.get(Employee, "R4_EMP_WEAK6")
    assert refreshed.password_hash is not None, "BUG-R4-01：弱口令迁移后哈希未写入"
    assert refreshed.password_hash.startswith("pbkdf2_sha256$")
    assert password_hasher.verify_password("123456", refreshed.password_hash)
    assert refreshed.force_change_password is True
    assert refreshed.password_set_at is not None


def test_legacy_password_still_usable_until_changed(
    client: TestClient, db: Session
):
    """⚠️ BUG-R6-18：迁移后、改密前，弱口令**仍可登录**（不能锁死用户）。

    曾经的错误设计：迁移完就把弱口令从登录口堵死。
    结果是 —— 用户在改密前离开（关浏览器 / 断网 / 换设备），
    下次弱口令被拒 → 拿不到登录态 → 到不了改密页 → **永久锁死**。

    正确防线是 ``force_change_password``：前端强制跳改密页，
    业务接口也应据此拦截；弱口令在**改密成功后**自然失效。
    """
    legacy = Employee(
        id="R4_EMP_WEAK6B",
        name="R4 六位弱口令B",
        login_account="r4_weak6b",
        employment_status="active",
        account_status="enabled",
        password_hash=None,
        token_version=1,
    )
    db.add(legacy)
    db.commit()

    # 首次登录 → 触发迁移
    resp = _login(client, "r4_weak6b", "123456")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["forceChangePassword"] is True

    db.expire_all()
    refreshed = db.get(Employee, "R4_EMP_WEAK6B")
    assert refreshed.password_hash is not None
    assert refreshed.force_change_password is True

    # 迁移完成但**尚未改密** → 弱口令仍可登录（否则用户会被锁死）
    resp = _login(client, "r4_weak6b", "123456")
    assert resp.status_code == 200, (
        "BUG-R6-18：迁移后未改密就把弱口令堵死 → 用户在改密前离开会永久锁死"
    )
    assert resp.json()["data"]["forceChangePassword"] is True


def test_legacy_password_stops_working_after_change(
    client: TestClient, db: Session
):
    """改密成功后，弱口令**彻底失效**。"""
    legacy = Employee(
        id="R4_EMP_WEAK6D",
        name="R4 六位弱口令D",
        login_account="r4_weak6d",
        employment_status="active",
        account_status="enabled",
        password_hash=None,
        token_version=1,
    )
    db.add(legacy)
    db.commit()

    resp = _login(client, "r4_weak6d", "123456")
    assert resp.status_code == 200
    token = resp.json()["data"]["accessToken"]

    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "123456", "newPassword": "New-Strong-Pass-2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    # 弱口令失效
    assert _login(client, "r4_weak6d", "123456").status_code == 401
    # 新口令可用
    assert _login(client, "r4_weak6d", "New-Strong-Pass-2026").status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 3. 改密
# ═══════════════════════════════════════════════════════════════════════════


def _auth_headers(db: Session, employee_id: str) -> dict:
    emp = db.get(Employee, employee_id)
    token, _ = _generate_token(emp.id, emp.login_account or emp.id, emp.token_version)
    return {"Authorization": f"Bearer {token}"}


def test_change_password_updates_hash(client: TestClient, db: Session, employee_with_password):
    """改密后库里存的是**新密码的哈希**，且能用新密码登录。"""
    headers = _auth_headers(db, "R4_EMP_001")
    # ⚠️ 先把「改密前的哈希」取成**字符串**存下来。
    #    `employee_with_password` 与 `db.get(...)` 返回的是**同一个对象实例**
    #    （同一 identity map），改密后它的属性会同步变成新值 ——
    #    若直接拿它跟刷新后的对象比，等于自己跟自己比，断言恒为 False。
    before_hash = employee_with_password.password_hash

    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "brand-new-pass"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["reloginRequired"] is True

    db.expire_all()
    refreshed = db.get(Employee, "R4_EMP_001")
    assert refreshed.password_hash != before_hash
    assert password_hasher.verify_password("brand-new-pass", refreshed.password_hash)
    assert not password_hasher.verify_password("original-pass", refreshed.password_hash)
    assert refreshed.force_change_password is False
    assert refreshed.password_set_at is not None


def test_change_password_rejects_wrong_old_password(
    client: TestClient, db: Session, employee_with_password
):
    """旧密码不对 → 401，不改任何数据。"""
    headers = _auth_headers(db, "R4_EMP_001")
    before = employee_with_password.password_hash
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "not-the-old-one", "newPassword": "brand-new-pass"},
        headers=headers,
    )
    assert resp.status_code == 401
    db.expire_all()
    assert db.get(Employee, "R4_EMP_001").password_hash == before


def test_change_password_rejects_weak_new_password(
    client: TestClient, db: Session, employee_with_password
):
    """新密码太短 → 400。"""
    headers = _auth_headers(db, "R4_EMP_001")
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "123"},
        headers=headers,
    )
    assert resp.status_code == 400


def test_change_password_rejects_same_password(
    client: TestClient, db: Session, employee_with_password
):
    """新旧相同 → 400。"""
    headers = _auth_headers(db, "R4_EMP_001")
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "original-pass"},
        headers=headers,
    )
    assert resp.status_code == 400


def test_change_password_requires_auth(client: TestClient, db: Session):
    """没登录 → 401。"""
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "brand-new-pass"},
    )
    assert resp.status_code == 401


def test_change_password_revokes_old_tokens(
    client: TestClient, db: Session, employee_with_password
):
    """改密后旧 Token 立即失效（token_version 递增）。"""
    old_headers = _auth_headers(db, "R4_EMP_001")
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "brand-new-pass"},
        headers=old_headers,
    )
    assert resp.status_code == 200

    # 旧 Token 再用 → 401。
    me = client.get("/v1/auth/me", headers=old_headers)
    assert me.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 4. 登出吊销（R14）
# ═══════════════════════════════════════════════════════════════════════════


def test_logout_invalidates_token(client: TestClient, db: Session, employee_with_password):
    """登出后 Token 立即失效 —— 这是 R14 的核心断言。"""
    headers = _auth_headers(db, "R4_EMP_001")
    assert client.get("/v1/auth/me", headers=headers).status_code == 200

    resp = client.post("/v1/auth/logout", headers=headers)
    assert resp.status_code == 200

    assert client.get("/v1/auth/me", headers=headers).status_code == 401


def test_logout_is_idempotent(client: TestClient, db: Session, employee_with_password):
    """重复登出不报错。"""
    headers = _auth_headers(db, "R4_EMP_001")
    client.post("/v1/auth/logout", headers=headers)
    resp = client.post("/v1/auth/logout", headers=headers)
    assert resp.status_code == 200


def test_logout_without_credentials_is_ok(client: TestClient):
    """没有凭据也返回成功（幂等语义）。"""
    assert client.post("/v1/auth/logout").status_code == 200


def test_logout_with_stale_token_does_not_kill_current_session(
    client: TestClient, db: Session, employee_with_password
):
    """⚠️ BUG-R6-17：拿**已失效**的旧 Token 调 logout，不能连带踢掉当前会话。

    场景：前端 ``changePassword`` 成功后调 ``authStore.logout()``，
    但改密接口已经把 ``token_version`` 递增过了 —— 这个 logout 带着
    一个**已失效**的 Token。若后端照旧递增版本号，就会把
    「其它设备上正在正常使用的会话」一起踢下线。

    正确行为：Token 版本与库里不一致时，logout 幂等返回成功，
    但**不动** ``token_version``。
    """
    headers = _auth_headers(db, "R4_EMP_001")

    # 先改密 —— token_version 1 → 2，old_headers 里的 Token 随即失效
    resp = client.post(
        "/v1/auth/change-password",
        json={"oldPassword": "original-pass", "newPassword": "brand-new-pass"},
        headers=headers,
    )
    assert resp.status_code == 200

    db.expire_all()
    emp = db.get(Employee, "R4_EMP_001")
    version_after_change = int(emp.token_version or 1)
    assert version_after_change == 2

    # 用**已失效**的旧 Token 调 logout —— 不应再递增版本号
    resp = client.post("/v1/auth/logout", headers=headers)
    assert resp.status_code == 200

    db.expire_all()
    emp = db.get(Employee, "R4_EMP_001")
    assert int(emp.token_version or 1) == version_after_change, (
        "BUG-R6-17：失效 Token 的 logout 仍递增了 token_version，"
        "会把其它设备上的正常会话一起踢下线"
    )

    # 当前（改密后重新签发）的会话仍然可用
    fresh_headers = _auth_headers(db, "R4_EMP_001")
    assert client.get("/v1/auth/me", headers=fresh_headers).status_code == 200
