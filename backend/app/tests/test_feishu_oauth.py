"""飞书 OAuth 授权底座测试（FS-1）。

覆盖 Runbook `17` §2.3 要求的 7 个 OAuth 用例，外加：

- **token 隔离**（RK3 回归）—— user / tenant token 交替调用 20 次不串味
- **飞书 token 不外泄** —— 响应里绝不出现 user_access_token
- **禁用/离职员工拒绝签发**

⚠️ 测试全部用 ``MockTransport`` 注入录制响应 —— **不打真实飞书 API**。
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import Base, Employee, FeishuIdentity
from backend.app.main import app
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.feishu_oauth import FeishuOAuthError, FeishuOAuthService

# ─────────────────────────────────────────────────────
# 固定测试数据
# ─────────────────────────────────────────────────────

TEST_USER_ID = "u_test_001"
TEST_OPEN_ID = "ou_test_open_001"
TEST_UNION_ID = "on_test_union_001"
TEST_REDIRECT = "https://perf.example.com/feishu/callback"
TEST_APP_ID = "cli_test_app"
TEST_APP_SECRET = "test_secret"

# 录制响应
APP_TOKEN_RESP = {
    "status_code": 200,
    "json": {"code": 0, "app_access_token": "app-token-xxx", "expire": 7200},
}
USER_TOKEN_RESP = {
    "status_code": 200,
    "json": {
        "code": 0,
        "access_token": "user-token-xxx",
        "refresh_token": "refresh-xxx",
        "expires_in": 7200,
        "token_type": "Bearer",
        "user_id": TEST_USER_ID,
    },
}
USER_INFO_RESP = {
    "status_code": 200,
    "json": {
        "code": 0,
        # ⚠️ 官方「获取用户信息」接口的 data 是**扁平**的用户信息，
        #    没有多包一层 "user"。
        #    见 GET /open-apis/authen/v1/user_info
        "data": {
            "user_id": TEST_USER_ID,
            "open_id": TEST_OPEN_ID,
            "union_id": TEST_UNION_ID,
            "name": "测试员工",
        },
    },
}


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def employee(db_session):
    emp = Employee(
        id="emp_test_001",
        name="测试员工",
        login_account="tester",
        feishu_user_id=TEST_USER_ID,
        feishu_open_id=TEST_OPEN_ID,
        feishu_union_id=TEST_UNION_ID,
        employment_status="active",
        account_status="enabled",
    )
    db_session.add(emp)
    db_session.commit()
    return emp


@pytest.fixture
def identity(db_session, employee):
    row = FeishuIdentity(
        employee_id=employee.id,
        identity_type="open_id",
        identity_value=TEST_OPEN_ID,
        active=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture(autouse=True)
def oauth_settings(monkeypatch):
    """把 OAuth 相关配置指到测试值。"""
    monkeypatch.setattr(settings, "feishu_app_id", TEST_APP_ID)
    monkeypatch.setattr(settings, "feishu_app_secret", TEST_APP_SECRET)
    monkeypatch.setattr(settings, "feishu_oauth_redirect_uris", TEST_REDIRECT)
    monkeypatch.setattr(settings, "feishu_oauth_state_secret", "state-secret-for-test")
    monkeypatch.setattr(settings, "feishu_oauth_state_ttl_seconds", 600)


def make_client(recordings: dict) -> FeishuClient:
    return FeishuClient(
        app_id=TEST_APP_ID,
        app_secret=TEST_APP_SECRET,
        transport=MockTransport(recordings),
        sleep=lambda _s: None,
    )


# ===========================================================================
# 用例 1：state 一次性
# ===========================================================================


def test_state_is_single_use(db_session):
    """state 校验通过后立即作废 —— 第二次使用必须失败。"""
    client = make_client({})
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    # 第一次：成功
    service.consume_state(state)
    # 第二次：必须失败（防重放）
    with pytest.raises(FeishuOAuthError) as exc:
        service.consume_state(state)
    assert exc.value.code == "state_consumed"


# ===========================================================================
# 用例 2：state 过期
# ===========================================================================


def test_state_expires(db_session, monkeypatch):
    """state 超过 TTL 后必须被拒绝。"""
    client = make_client({})
    service = FeishuOAuthService(client, db_session)

    # ⚠️ 先把真实时间存下来**再**打补丁 ——
    #    ``monkeypatch.setattr("...feishu_oauth.time.time", ...)`` 改的是
    #    **全局 time 模块**，所以补丁里的 lambda 必须引用一个**已捕获**的
    #    原函数，否则会自我递归（RecursionError）。
    real_time = time.time

    state = service.issue_state(TEST_REDIRECT)

    # 把「当前时间」**往前推** 1 小时（TTL 是 600 秒）。
    # ⚠️ 方向不能搞反：校验算的是 ``now - issued_at > ttl``，
    #    必须让 now **变大**。若把 now 调小，差值是负数，
    #    反而「没过期」—— 测试会假通过。
    monkeypatch.setattr(
        "backend.app.services.feishu_oauth.time.time",
        lambda: real_time() + 3600,
    )
    with pytest.raises(FeishuOAuthError) as exc:
        service.consume_state(state)
    assert exc.value.code == "state_expired"


# ===========================================================================
# 用例 3：redirect 不匹配 / 不在白名单
# ===========================================================================


def test_redirect_not_in_whitelist(db_session):
    """白名单外的 redirect_uri 必须被拒（开放重定向防护）。"""
    client = make_client({})
    service = FeishuOAuthService(client, db_session)

    with pytest.raises(FeishuOAuthError) as exc:
        service.issue_state("https://evil.example.com/steal")
    assert exc.value.code == "redirect_not_allowed"


def test_state_redirect_mismatch_rejected(db_session):
    """state 里的 redirect_uri 被篡改 → 签名校验失败。"""
    client = make_client({})
    service = FeishuOAuthService(client, db_session)
    state = service.issue_state(TEST_REDIRECT)

    # 篡改 state 中的 redirect_uri 部分
    parts = state.split(".")
    tampered = ".".join(parts[:2] + ["ZXZpbC5leGFtcGxlLmNvbQ"] + parts[3:])

    with pytest.raises(FeishuOAuthError) as exc:
        service.consume_state(tampered)
    assert exc.value.code == "state_invalid"


def test_state_tampered_nonce_rejected(db_session):
    """改 nonce → 签名对不上。"""
    client = make_client({})
    service = FeishuOAuthService(client, db_session)
    state = service.issue_state(TEST_REDIRECT)

    parts = state.split(".")
    tampered = ".".join(["tampered_nonce"] + parts[1:])

    with pytest.raises(FeishuOAuthError) as exc:
        service.consume_state(tampered)
    assert exc.value.code == "state_invalid"


# ===========================================================================
# 用例 4：code 换 token 成功
# ===========================================================================


def test_exchange_code_success(db_session, identity):
    """正常流程：code → user_access_token → 用户信息 → 员工。"""
    recordings = {
        "POST /auth/v3/app_access_token/internal": [APP_TOKEN_RESP],
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }
    client = make_client(recordings)
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    result = service.exchange_code_for_employee("valid-code", state)

    assert result.employee_id == "emp_test_001"
    assert result.open_id == TEST_OPEN_ID
    assert result.real_name == "测试员工"


def test_exchange_uses_login_user_info_endpoint_not_contact_lookup(db_session, identity):
    """⚠️⚠️ **F2 回归**：免登必须用「获取用户信息」接口，**不能**用通讯录接口。

    官方给登录用户准备的接口：

        GET /open-apis/authen/v1/user_info
        Authorization: Bearer <user_access_token>

    **不需要 user_id** —— 直接返回当前登录用户的身份。

    ⚠️ 之前用的是通讯录接口 ``/contact/v3/users/{user_id}``，
    那个接口路径里必须有 user_id，而 OAuth 令牌响应**不含 user_id**
    ⇒ 必然抛「缺少 user_id」，**免登永远走不到最后一步**（2026-11-17 真机实测）。

    ⚠️ 同时确保不再带 ``user_id_type`` —— 那是通讯录接口的参数，
    登录用户接口不需要（带上也无意义）。
    """
    recordings = {
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }
    client = make_client(recordings)
    transport = client._http_client._transport  # type: ignore[union-attr]
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    service.exchange_code_for_employee("valid-code", state)

    user_calls = [
        req for req in transport.calls if "/authen/v1/user_info" in req.url.path  # type: ignore[union-attr]
    ]
    assert user_calls, "应调用「获取用户信息」接口"
    assert not [
        req for req in transport.calls if "/contact/v3/users" in req.url.path  # type: ignore[union-attr]
    ], "免登不应调用通讯录接口 —— 它要求先知道 user_id"


def test_exchange_works_without_user_id_in_token_response(db_session, identity):
    """⚠️⚠️ **F2 回归**：令牌响应里**没有** user_id 也必须能完成免登。

    OAuth 令牌响应的官方字段只有：
    ``access_token`` / ``refresh_token`` / ``expires_in`` / ``scope`` / ``token_type``
    —— **没有 user_id**。

    ⇒ 身份解析**必须**依赖 ``/authen/v1/user_info`` 返回的 ``open_id``，
    不能依赖令牌响应里的 user_id。
    """
    token_resp = {
        "status_code": 200,
        "json": {
            "code": 0,
            "access_token": "user-token-xxx",
            "expires_in": 7200,
            "token_type": "Bearer",
            # ⚠️ 故意**不给** user_id
        },
    }
    recordings = {
        "POST /authen/v2/oauth/token": [token_resp],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }
    client = make_client(recordings)
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    result = service.exchange_code_for_employee("valid-code", state)

    assert result.employee_id == "emp_test_001"
    assert result.open_id == TEST_OPEN_ID


def test_exchange_uses_user_token_not_tenant_token(db_session, identity):
    """⚠️ RK3 回归：查询用户信息必须用**用户态** token，不是租户态。"""
    recordings = {
        "POST /auth/v3/app_access_token/internal": [APP_TOKEN_RESP],
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
        "POST /auth/v3/tenant_access_token/internal": [
            {"status_code": 200, "json": {"code": 0, "tenant_access_token": "tenant-yyy", "expire": 7200}}
        ],
    }
    client = make_client(recordings)
    transport = client._http_client._transport  # type: ignore[union-attr]
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    service.exchange_code_for_employee("valid-code", state)

    user_call = next(
        req for req in transport.calls if "/authen/v1/user_info" in req.url.path  # type: ignore[union-attr]
    )
    auth = user_call.headers.get("Authorization")
    assert auth == "Bearer user-token-xxx", "查询用户信息必须用用户态 token"
    assert "tenant-yyy" not in str(auth)


# ===========================================================================
# 用例 5/6：禁用 / 离职员工拒绝签发
# ===========================================================================


@pytest.mark.parametrize(
    ("employment_status", "account_status"),
    [
        ("exited", "disabled"),  # 离职（同步逻辑：离职必置 disabled）
        ("inactive", "disabled"),  # 冻结/未激活（同步逻辑：冻结必置 disabled）
        ("active", "disabled"),  # 账号被禁用
        ("exited", "enabled"),  # 异常组合：离职但账号还开着
    ],
)
def test_inactive_employee_cannot_login(
    db_session, identity, employment_status, account_status
):
    """离职 / 冻结 / 禁用账号**不能**通过免登拿到 token。

    ⚠️ 前三种组合是组织架构同步会产生的**真实**状态：
    ``exited`` 与 ``inactive`` 都会把 ``account_status`` 置为 ``disabled``。
    最后一种是防御性组合 —— 即使数据被手工改坏，也不该放行。
    """
    emp = db_session.get(Employee, "emp_test_001")
    emp.employment_status = employment_status
    emp.account_status = account_status
    db_session.commit()

    recordings = {
        "POST /auth/v3/app_access_token/internal": [APP_TOKEN_RESP],
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }
    client = make_client(recordings)
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    with pytest.raises(FeishuOAuthError) as exc:
        service.exchange_code_for_employee("valid-code", state)
    assert exc.value.code == "identity_not_bound"


# ===========================================================================
# ⚠️ 免登失败原因的**细粒度诊断**（2026-11-17 新增）
# ===========================================================================


def _exchange_and_get_error(db_session, identity):
    """跑一次免登交换，返回抛出的 ``FeishuOAuthError``。"""
    recordings = {
        "POST /auth/v3/app_access_token/internal": [APP_TOKEN_RESP],
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }
    client = make_client(recordings)
    service = FeishuOAuthService(client, db_session)
    state = service.issue_state(TEST_REDIRECT)
    with pytest.raises(FeishuOAuthError) as exc:
        service.exchange_code_for_employee("valid-code", state)
    return exc.value


def test_leaver_gets_distinct_reason_not_generic_unbound(db_session, identity):
    """⚠️ **离职**的人必须拿到专门的提示，不能是笼统的「尚未绑定」。

    为什么重要：
    「已离职」被当成「没绑定」→ 有人会去反复跑组织架构同步（**没用**），
    或者反过来去催人绑定（人已离职，**不该**绑定）。
    两种都会把排查带偏。
    """
    emp = db_session.get(Employee, "emp_test_001")
    emp.employment_status = "exited"
    emp.account_status = "disabled"
    db_session.commit()

    err = _exchange_and_get_error(db_session, identity)
    assert err.code == "identity_not_bound"
    assert err.reason == "exited"
    assert "离职" in err.message
    assert "同步" not in err.message, "离职的人不该被引导去跑同步"


def test_disabled_account_gets_distinct_reason(db_session, identity):
    """⚠️ **在职但账号被停用** → 与离职区分开（处理方式不同：解封 vs 离职流程）。"""
    emp = db_session.get(Employee, "emp_test_001")
    emp.employment_status = "active"
    emp.account_status = "disabled"
    db_session.commit()

    err = _exchange_and_get_error(db_session, identity)
    assert err.reason == "disabled"
    assert "停用" in err.message
    assert "离职" not in err.message


def test_not_synced_gets_sync_guidance(db_session):
    """⚠️ **飞书里根本没这个人** → 该引导去跑同步（而不是说「去绑定」）。"""
    emp = Employee(id="emp_other", name="别人", login_account="other")
    db_session.add(emp)
    db_session.commit()

    err = _exchange_and_get_error(db_session, None)
    assert err.reason == "not_synced"
    assert "同步" in err.message


def test_ambiguous_binding_gets_distinct_reason(db_session, identity):
    """⚠️ **不同身份键指向不同员工**（数据冲突）→ 该人工解绑，且绝不放行。

    ⚠️ 怎么才会发生：
    数据库对 ``(identity_type, identity_value)`` 有 UNIQUE 约束，
    所以**同一个 open_id 不可能绑到两个人**（DB 层就挡住了）。
    但**跨类型**可能错位 —— 例如 ``open_id`` 指向员工 A、
    而 ``union_id`` 指向员工 B（换绑写了一半 / 同步并发导致）。

    此时 ``resolve_employee`` 因 ``len(employee_ids) != 1`` 返回 None
    ⇒ 落入 ambiguous 分支 ⇒ **绝不能放行**（放行等于随便挑一个人登录）。
    """
    other = Employee(id="emp_dup", name="另一个人", login_account="dup")
    db_session.add(other)
    db_session.commit()
    # 不同类型、不同值 → 不触发 UNIQUE 约束，但会指向不同员工
    db_session.add(
        FeishuIdentity(
            employee_id="emp_dup",
            identity_type="union_id",
            identity_value="on_test_union_002",
            active=True,
        )
    )
    db_session.commit()

    from backend.app.services.feishu_identity_service import FeishuIdentityService

    svc = FeishuIdentityService(db_session)
    result = svc.diagnose_identity(
        open_id=TEST_OPEN_ID, union_id="on_test_union_002"
    )
    assert result["status"] == "ambiguous"
    assert len(result["matched_employee_ids"]) == 2
    assert result["employee"] is None, "冲突时绝不能返回某个员工（会被当成放行）"

    # ⚠️ resolve_employee 同样必须拒绝 —— 这是安全边界
    assert svc.resolve_employee(open_id=TEST_OPEN_ID, union_id="on_test_union_002") is None


def test_diagnose_identity_resolves_when_healthy(db_session, identity):
    """⚠️ 正常路径下诊断必须返回 ``resolved``，且给出员工。"""
    from backend.app.services.feishu_identity_service import FeishuIdentityService

    result = FeishuIdentityService(db_session).diagnose_identity(open_id=TEST_OPEN_ID)
    assert result["status"] == "resolved"
    assert result["employee"] is not None
    assert result["employee"].id == "emp_test_001"


def test_frozen_employee_gets_disabled_reason(db_session, identity):
    """⚠️⚠️ **冻结/未激活**（飞书 ``is_activated=False``）—— 真实库里的主场景。

    实测本地 93 人里有 13 人是这种状态：
        account_status=disabled + employment_status=inactive
    且飞书侧 ``is_exited=False`` / ``is_frozen=False`` / ``is_activated=False``
    ⇒ **不是离职**，是「停用/未加入企业」。

    ⇒ 提示必须是「已停用」而不是「已离职」——
       说成离职会让人去走复职流程（那是**永久**禁用），而实际解冻后就能恢复。

    ⚠️ ``employment_status=inactive`` 的账号 ``account_status`` 也是 disabled，
       所以诊断落 ``disabled`` 分支（账号被停用）—— 这是对的。
    """
    emp = db_session.get(Employee, "emp_test_001")
    emp.employment_status = "inactive"
    emp.account_status = "disabled"
    db_session.commit()

    err = _exchange_and_get_error(db_session, identity)
    assert err.reason == "disabled"
    assert "停用" in err.message
    assert "离职" not in err.message, "冻结 ≠ 离职，不能引导去走复职流程"


def test_exited_employee_gets_exited_reason(db_session, identity):
    """⚠️ **真离职**（飞书 ``is_exited=True``）→ 提示「已离职」，且需人工复职。"""
    emp = db_session.get(Employee, "emp_test_001")
    emp.employment_status = "exited"
    emp.account_status = "disabled"
    db_session.commit()

    err = _exchange_and_get_error(db_session, identity)
    assert err.reason == "exited"
    assert "离职" in err.message


def test_diagnose_identity_reports_inactive_binding(db_session, identity):
    """⚠️ 身份行存在但 ``active=False``、员工却在职且启用 → ``inactive_binding``。

    这是**不同步**的信号（同步逻辑漏了，或并发写导致），
    与「未同步」的处理方式不同：重跑同步可能能修好。
    """
    emp = db_session.get(Employee, "emp_test_001")
    emp.employment_status = "active"
    emp.account_status = "enabled"
    identity.active = False
    db_session.commit()

    from backend.app.services.feishu_identity_service import FeishuIdentityService

    result = FeishuIdentityService(db_session).diagnose_identity(open_id=TEST_OPEN_ID)
    assert result["status"] == "inactive_binding"

    err = _exchange_and_get_error(db_session, identity)
    assert err.reason == "inactive_binding"
    assert "同步" in err.message


def test_identity_not_bound_error_carries_reason_through_route(db_session):
    """⚠️ 路由层必须把 ``reason`` 透出（结构化 detail）。

    少了它，前端只能显示通用文案 —— 那正是本次要修的问题。
    """
    from fastapi.testclient import TestClient

    from backend.app.main import app
    from backend.app.routes import feishu_auth

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[feishu_auth.get_feishu_client] = lambda: make_client(
        {
            "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
            "GET /authen/v1/user_info": [USER_INFO_RESP],
        }
    )
    client = TestClient(app)
    try:
        service = FeishuOAuthService(make_client({}), db_session)
        state = service.issue_state(TEST_REDIRECT)

        resp = client.post(
            "/v1/feishu/auth/exchange",
            json={"code": "valid-code", "state": state},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert isinstance(detail, dict), "detail 必须是结构化的，才能带上 reason"
        assert detail["reason"] == "not_synced"
        assert "message" in detail
    finally:
        app.dependency_overrides.clear()


# ===========================================================================
# 用例 7：open_id 未绑定
# ===========================================================================


def test_unbound_open_id_rejected(db_session):
    """没有绑定记录的 open_id → 明确报错，不签发 token。"""
    emp = Employee(id="emp_other", name="别人", login_account="other")
    db_session.add(emp)
    db_session.commit()

    recordings = {
        "POST /auth/v3/app_access_token/internal": [APP_TOKEN_RESP],
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }
    client = make_client(recordings)
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    with pytest.raises(FeishuOAuthError) as exc:
        service.exchange_code_for_employee("valid-code", state)
    assert exc.value.code == "identity_not_bound"


# ===========================================================================
# RK3：token 隔离（核心回归）
# ===========================================================================


def test_user_and_tenant_tokens_do_not_contaminate(db_session):
    """⚠️⚠️ **RK3 核心回归**。

    用户态 token 与租户态 token 必须是**完全独立**的槽位。

    若共用 ``_access_token``，那么「谁后调用谁中毒」——
    组织架构同步、审批回流会**随机 401**，且是间歇性故障，极难排查。

    本测试交替调用 20 次，断言：
      1. 每次请求带的 Authorization 都对；
      2. 用户态 token **没有**污染租户态缓存；
      3. 租户态 token **没有**污染用户态缓存。
    """
    tenant_resp = {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
    }
    user_resp = {
        "status_code": 200,
        "json": {
            "code": 0,
            "data": {"open_id": TEST_OPEN_ID, "user_id": TEST_USER_ID},
        },
    }

    transport = MockTransport(
        {
            "POST /auth/v3/tenant_access_token/internal": [tenant_resp] * 25,
            "GET /authen/v1/user_info": [user_resp] * 25,
            # ⚠️ 这条是**客户端层**的隔离测试 —— 它要验证「显式传 token 的调用」
            #    不会被租户态 token 污染，所以两个端点都要覆盖。
            f"GET /contact/v3/users/{TEST_USER_ID}": [user_resp] * 25,
            "GET /contact/v3/departments/0/children": [
                {"status_code": 200, "json": {"code": 0, "data": {"items": []}}}
            ]
            * 25,
        }
    )
    client = FeishuClient(
        app_id=TEST_APP_ID,
        app_secret=TEST_APP_SECRET,
        transport=transport,
        sleep=lambda _s: None,
    )

    for _ in range(20):
        # 租户态调用
        client._request("GET", "/contact/v3/departments/0/children")
        # 用户态调用（显式 token）
        client._request_with_token(
            "GET",
            f"/contact/v3/users/{TEST_USER_ID}",
            params={"user_id_type": "user_id"},
            token="explicit-user-token",
        )

    user_calls = [req for req in transport.calls if "/contact/v3/users" in req.url.path]
    tenant_calls = [
        req for req in transport.calls if "/contact/v3/departments" in req.url.path
    ]

    assert len(user_calls) == 20
    assert len(tenant_calls) == 20

    for req in user_calls:
        assert req.headers.get("Authorization") == "Bearer explicit-user-token"
    for req in tenant_calls:
        assert req.headers.get("Authorization") == "Bearer tenant-token"

    # 缓存槽位必须互相独立
    assert client._access_token == "tenant-token"
    assert client._user_token is None


def test_tenant_token_not_overwritten_by_user_flow(db_session, identity):
    """⚠️ RK3：走完一遍用户态流程后，租户态 token 必须仍是原值。"""
    tenant_resp = {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": "tenant-original", "expire": 7200},
    }
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [tenant_resp] * 5,
        "POST /auth/v3/app_access_token/internal": [APP_TOKEN_RESP] * 5,
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP] * 5,
        "GET /authen/v1/user_info": [USER_INFO_RESP] * 5,
    }
    client = make_client(recordings)
    service = FeishuOAuthService(client, db_session)

    # 先取一次租户态 token
    assert client._get_access_token() == "tenant-original"

    # 再走用户态流程
    state = service.issue_state(TEST_REDIRECT)
    service.exchange_code_for_employee("valid-code", state)

    # 租户态 token 必须没被污染
    assert client._access_token == "tenant-original"
    assert client._get_access_token() == "tenant-original"


# ===========================================================================
# 飞书 token 不外泄
# ===========================================================================


def test_feishu_token_never_leaks_to_response(db_session, identity, monkeypatch):
    """⚠️ 响应体里绝不能出现飞书的 user_access_token。"""
    from fastapi.testclient import TestClient

    recordings = {
        "POST /auth/v3/app_access_token/internal": [APP_TOKEN_RESP],
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }

    # ⚠️ 用 ``app.dependency_overrides`` 注入 mock client ——
    #    不能用 ``monkeypatch.setattr(feishu_auth, "get_feishu_client", ...)``：
    #    路由里写的是 ``Depends(get_feishu_client)``，
    #    FastAPI 在**导入时**就把函数对象抓进依赖图了，
    #    之后再改模块属性对已注册的路由**不生效**。
    from backend.app.routes import feishu_auth

    app.dependency_overrides[feishu_auth.get_feishu_client] = lambda: make_client(
        recordings
    )
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    try:
        # 先拿一个合法 state（直接调服务层 —— state 存储是模块级共享的，
        # 所以这里签发的 state，路由里新建的 service 实例也能消费到）
        service = FeishuOAuthService(make_client({}), db_session)
        state = service.issue_state(TEST_REDIRECT)

        resp = client.post(
            "/v1/feishu/auth/exchange",
            json={"code": "valid-code", "state": state},
        )
        assert resp.status_code == 200, resp.text
        body = resp.text
        assert "user-token-xxx" not in body, "飞书 user_access_token 绝不能出现在响应里"
        assert "app-token-xxx" not in body, "飞书 app_access_token 绝不能出现在响应里"
        # 本系统 token 应该有
        assert "accessToken" in body
    finally:
        app.dependency_overrides.clear()


def test_auth_url_endpoint_requires_configured_redirect(db_session, monkeypatch):
    """未配置 redirect_uri 白名单 → 503（不放行）。"""
    from fastapi.testclient import TestClient

    from backend.app.routes import feishu_auth

    monkeypatch.setattr(settings, "feishu_oauth_redirect_uris", "")
    app.dependency_overrides[feishu_auth.get_feishu_client] = lambda: make_client({})
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    try:
        resp = client.get(f"/v1/feishu/auth/url?redirect_uri={TEST_REDIRECT}")
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_auth_url_rejects_whitelist_miss(db_session, monkeypatch):
    """redirect_uri 不在白名单 → 400。"""
    from fastapi.testclient import TestClient

    from backend.app.routes import feishu_auth

    app.dependency_overrides[feishu_auth.get_feishu_client] = lambda: make_client({})
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    try:
        resp = client.get(
            "/v1/feishu/auth/url?redirect_uri=https://evil.example.com/steal"
        )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()


# ===========================================================================
# 客户端层：token 获取失败的处理
# ===========================================================================


def test_exchange_user_token_failure_raises(db_session):
    """飞书返回 code != 0 → 抛 FeishuTokenError，不是静默通过。"""
    from backend.app.services.feishu_client import FeishuTokenError

    recordings = {
        "POST /auth/v3/app_access_token/internal": [
            {"status_code": 200, "json": {"code": 0, "app_access_token": "app-x", "expire": 7200}}
        ],
        "POST /authen/v2/oauth/token": [
            {"status_code": 200, "json": {"code": 20021, "msg": "code invalid"}}
        ],
    }
    client = make_client(recordings)
    with pytest.raises(FeishuTokenError):
        client.exchange_user_token("bad-code")


def test_client_get_user_info_requires_token(db_session):
    """缺 token / 缺 user_id → 明确报错。"""
    from backend.app.services.feishu_client import FeishuTokenError

    client = make_client({})
    with pytest.raises(FeishuTokenError):
        client.get_user_info("", TEST_USER_ID)
    with pytest.raises(FeishuTokenError):
        client.get_user_info("token", "")


def test_http_client_uses_base_url():
    """冒烟：客户端 BASE_URL 与端点常量正确。"""
    assert FeishuClient.BASE_URL == "https://open.feishu.cn/open-apis"
    assert FeishuClient.TOKEN_ENDPOINT == "/auth/v3/tenant_access_token/internal"
    assert issubclass(httpx.HTTPError, Exception)


# ===========================================================================
# ⚠️⚠️ 2026-11-17 真机 20029 回归（两个官方契约参数名）
# ===========================================================================


def test_authorize_url_uses_official_client_id_and_response_type(db_session):
    """⚠️⚠️ **20029 回归**：授权 URL 必须用官方参数名。

    官方「获取授权码」接口明确写的是：

        https://accounts.feishu.cn/open-apis/authen/v1/authorize
            ?client_id=<App ID>
            &response_type=code
            &redirect_uri=...

    之前实现写成了 ``app_id=`` 且**漏了** ``response_type``。

    ⚠️ **症状极具误导性**：飞书不报「缺少参数」，而是渲染错误页
    「重定向 URL 有误，请联系应用管理员……错误码：20029」
    —— 于是所有人都会去反复核对后台 redirect_uri 白名单（而后台是对的）。

    实测（2026-11-17）：``app_id`` 与 ``client_id`` 两种写法**都**报 20029，
    所以真正触发 20029 的是后台白名单为空。但参数名本身仍然必须改对 ——
    官方文档是唯一权威，写错就是错。
    """
    from urllib.parse import parse_qs, urlparse

    service = FeishuOAuthService(make_client({}), db_session)
    url = service.build_authorize_url(TEST_REDIRECT, "state-abc")
    params = parse_qs(urlparse(url).query)

    assert "client_id" in params, "必须用 client_id（官方参数名），不能用 app_id"
    assert "app_id" not in params, "app_id 不是本接口的参数名 —— 会导致授权页报错"
    assert params["client_id"] == [TEST_APP_ID]
    assert params["response_type"] == ["code"], "response_type=code 是必填"
    assert params["redirect_uri"] == [TEST_REDIRECT]
    assert params["state"] == ["state-abc"]


def test_authorize_url_redirect_uri_is_url_encoded(db_session):
    """⚠️ redirect_uri 必须经过 URL 编码（官方文档明确要求）。

    不编码时 ``://`` 和 ``/`` 会破坏 query 的解析 ——
    飞书拿到的 redirect_uri 被截断 → 白名单匹配失败 → 20029。
    """
    from urllib.parse import parse_qs, urlparse

    service = FeishuOAuthService(make_client({}), db_session)
    url = service.build_authorize_url("http://localhost:5180/feishu/callback", "s1")
    raw_query = urlparse(url).query
    params = parse_qs(raw_query)

    assert params["redirect_uri"] == ["http://localhost:5180/feishu/callback"]
    assert "://" not in raw_query.split("redirect_uri=")[1].split("&")[0], (
        "redirect_uri 必须编码后再拼进 query"
    )


def test_exchange_user_token_uses_official_oauth_endpoint_and_credentials(db_session):
    """⚠️⚠️ **OAuth 令牌端点回归**。

    官方「获取 user_access_token」是：

        POST /authen/v2/oauth/token
        { grant_type, client_id, client_secret, code, redirect_uri }

    之前用的是 ``POST /authen/v1/oidc/access_token`` + ``app_access_token``
    —— 那是 OIDC 用户信息风格端点，不是 OAuth 授权码流程的令牌端点。
    """
    recordings = {
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
    }
    client = make_client(recordings)
    transport = client._http_client._transport  # type: ignore[union-attr]

    client.exchange_user_token("valid-code", redirect_uri=TEST_REDIRECT)

    token_call = next(
        req for req in transport.calls if "oauth/token" in req.url.path  # type: ignore[union-attr]
    )
    body = json.loads(token_call.content)

    assert body["grant_type"] == "authorization_code"
    assert body["client_id"] == TEST_APP_ID, "必须用 client_id + client_secret 认证"
    assert body["client_secret"] == TEST_APP_SECRET
    assert body["code"] == "valid-code"
    assert body["redirect_uri"] == TEST_REDIRECT, "redirect_uri 必须与发起授权时一致"
    assert "app_access_token" not in body, "OAuth 端点不接受 app_access_token"


def test_exchange_user_token_error_uses_oauth_error_fields(db_session):
    """⚠️ OAuth 端点用 ``error`` / ``error_description`` 表达失败，不是 ``msg``。

    只读 ``msg`` 会让错误信息变成一句无用的兜底文案 —— 用户看到
    「飞书用户 Token 换取失败」而不知道真实原因。
    """
    from backend.app.services.feishu_client import FeishuTokenError

    recordings = {
        "POST /authen/v2/oauth/token": [
            {
                "status_code": 400,
                "json": {
                    "code": 20003,
                    "error": "invalid_grant",
                    "error_description": "authorization code not found",
                },
            }
        ],
    }
    client = make_client(recordings)
    with pytest.raises(FeishuTokenError) as exc:
        client.exchange_user_token("bad-code")
    assert "authorization code not found" in str(exc.value)


def test_oauth_service_passes_redirect_uri_to_token_exchange(db_session, identity):
    """⚠️ 服务层必须把 state 里的 redirect_uri 传给 token 端点。

    飞书会校验它与发起授权时是否一致，不一致报 20071。
    """
    recordings = {
        "POST /authen/v2/oauth/token": [USER_TOKEN_RESP],
        "GET /authen/v1/user_info": [USER_INFO_RESP],
    }
    client = make_client(recordings)
    transport = client._http_client._transport  # type: ignore[union-attr]
    service = FeishuOAuthService(client, db_session)

    state = service.issue_state(TEST_REDIRECT)
    service.exchange_code_for_employee("valid-code", state)

    token_call = next(
        req for req in transport.calls if "oauth/token" in req.url.path  # type: ignore[union-attr]
    )
    body = json.loads(token_call.content)
    assert body.get("redirect_uri") == TEST_REDIRECT


# ===========================================================================
# 配置面回归（FS-1 收尾）
# ===========================================================================


def test_oauth_config_surface_has_no_dead_settings():
    """⚠️ **配置面不得有「定义了但没人读」的项**。

    为什么这条重要：
    一个**配了也不生效**的配置项比没有更糟 —— 运维会以为「配了就生效」，
    配完发现没反应，转而去查代码，浪费时间还可能得出错误结论。

    FS-1 收尾时实测发现过一个这样的死配置：
    ``feishu_oauth_frontend_callback`` —— 定义在 ``config.py``，
    但 ``/v1/feishu/auth/url`` 的 ``redirect_uri`` 是**请求参数**
    （走白名单校验），前端传什么就跳回什么，那个值**根本没人读**。
    已删除，并在 config.py 里留了「不要再加回来」的警示注释。

    本测试扫描 ``Settings`` 的全部字段，任何**只在 config.py 里出现、
    在业务代码里从不被读取**的配置项都会失败。
    """
    import re
    from pathlib import Path

    from backend.app.core import config as config_mod

    backend_root = Path(__file__).resolve().parents[2]
    app_dir = backend_root / "app"

    # 扫描业务代码（排除 config.py 自身与测试目录 —— 配置项"被定义"不算"被使用"）
    source_files = [
        p
        for p in app_dir.rglob("*.py")
        if "tests" not in p.parts and p.name != "config.py"
    ]
    sources = "\n".join(p.read_text(encoding="utf-8") for p in source_files)

    # 找出所有以 feishu_ 开头的配置字段
    field_pattern = re.compile(r"^\s{4}(feishu_\w+)\s*:\s*", re.MULTILINE)
    declared = set(field_pattern.findall(config_mod.__file__ and Path(config_mod.__file__).read_text(encoding="utf-8")))

    # 这些字段由框架/其他模块直接消费，不在本测试的「业务代码」范围内
    exempt = {
        "feishu_app_id",
        "feishu_app_secret",
        "feishu_tenant_id",
        "feishu_encrypt_key",
    }

    dead = []
    for field in sorted(declared - exempt):
        # 配置项有两种合法的「被消费」写法，**两种都要认**：
        #   1. 属性访问：settings.<field>
        #   2. getattr 访问：getattr(settings, "<field>", ...)
        #      —— 后者在 offboarding_policy.py 里就是这么写的，
        #      只认属性访问会把它误判成死配置（假阳性）。
        if re.search(rf"settings\.{re.escape(field)}\b", sources):
            continue
        if re.search(rf"""getattr\(\s*settings\s*,\s*["']{re.escape(field)}["']""", sources):
            continue
        dead.append(field)

    assert not dead, (
        f"发现「定义了但无人读取」的死配置项: {dead} —— "
        "要么接上业务逻辑，要么从 config.py 删除（配了不生效比没有更糟）"
    )


def test_oauth_config_items_are_documented_in_env_example():
    """OAuth 配置项必须在 ``.env.example`` 里有对应说明。

    运维要照着 ``.env.example`` 配环境 —— 配置项只存在于代码里，
    等于让运维去读 Python 源码才能知道要配什么。
    """
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[2]
    env_example = backend_root / ".env.example"
    assert env_example.exists(), "backend/.env.example 不存在"

    content = env_example.read_text(encoding="utf-8")
    for env_key in (
        "FEISHU_OAUTH_REDIRECT_URIS",
        "FEISHU_OAUTH_STATE_SECRET",
        "FEISHU_OAUTH_STATE_TTL_SECONDS",
        "FEISHU_OAUTH_SCOPES",
    ):
        assert env_key in content, f".env.example 缺少 OAuth 配置项说明: {env_key}"
