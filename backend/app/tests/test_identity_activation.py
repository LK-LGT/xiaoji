"""身份映射可用性 ↔ 员工在职状态的一致性测试（R17 收尾）。

背景
----
``FeishuIdentity.active`` 表示「该飞书身份当前可用于解析员工」，
但历史上它**没有**跟随员工的 ``employment_status`` 一起更新。

真实库中就存在这种错配：13 名 ``inactive``/``disabled`` 员工
持有 39 条 active 身份绑定。

危害
----
``FeishuIdentityService.resolve_employee()`` 原本只按 ``identity.active``
过滤，**不看**员工状态 → 会把一个**已禁用**的员工解析出来。
该方法被免密登录、身份绑定、审批回流按人匹配等多处调用。

本文件覆盖：
1. 同步时身份可用性跟随在职状态（停用 / 恢复）；
2. 全量同步时全库对齐历史遗留错配；
3. ``resolve_employee()`` 的防线 —— 禁用/离职员工解析不到；
4. 「停用 ≠ 解除绑定」—— 复职后能自动恢复。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import (
    Base,
    Employee,
    FeishuIdentity,
    OrganizationSyncBatch,
)
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.feishu_identity_service import FeishuIdentityService
from backend.app.services.organization_sync import OrganizationSyncService


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def token_response(token: str = "token-1", expire: int = 7200):
    return {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": token, "expire": expire},
    }


def emp(user_id: str, name: str = "张三", dept_id: str = "d1", **status):
    base = {"is_activated": True, "is_exited": False}
    base.update(status)
    return {
        "user_id": user_id,
        "open_id": f"o-{user_id}",
        "union_id": f"un-{user_id}",
        "name": name,
        "department_ids": [dept_id],
        "leader_user_id": None,
        "email": f"{user_id}@example.com",
        "status": base,
    }


def _departments_response(items):
    return {"status_code": 200, "json": {"code": 0, "data": {"items": items}}}


def _users_response(items):
    return {"status_code": 200, "json": {"code": 0, "data": {"items": items}}}


def dept(dept_id: str, name: str = "部门", parent: str = "0"):
    return {
        "department_id": dept_id,
        "name": name,
        "parent_department_id": parent,
        "status": 1,
    }


def make_client(recordings):
    return FeishuClient(
        app_id="app-1",
        app_secret="secret-1",
        transport=MockTransport(recordings),
        sleep=lambda _: None,
    )


def seed_employee(db, employee_id: str, *, status: str = "active",
                  account_status: str = "enabled") -> Employee:
    """造一个员工并绑定 3 条 active 身份（模拟同步后的正常状态）。"""
    employee = Employee(
        id=employee_id,
        name=f"员工{employee_id}",
        employment_status=status,
        account_status=account_status,
        feishu_user_id=employee_id,
        token_version=1,
    )
    db.add(employee)
    for identity_type, value in (
        ("user_id", employee_id),
        ("open_id", f"o-{employee_id}"),
        ("union_id", f"un-{employee_id}"),
    ):
        db.add(
            FeishuIdentity(
                employee_id=employee_id,
                identity_type=identity_type,
                identity_value=value,
                active=True,
            )
        )
    db.commit()
    return employee


# ═══════════════════════════════════════════════════════════════════════════
# 一、同步时身份可用性跟随在职状态
# ═══════════════════════════════════════════════════════════════════════════


def test_exited_employee_identity_is_deactivated(db):
    """⚠️ 离职员工 → 其身份绑定被停用。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1", is_exited=True)])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    service.sync_all()

    rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all()
    assert rows, "应有身份记录"
    assert all(not r.active for r in rows), "离职员工的身份应全部停用"


def test_frozen_employee_identity_is_deactivated(db):
    """⚠️ 冻结员工 → 其身份绑定被停用。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1", is_frozen=True)])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    service.sync_all()

    rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all()
    assert all(not r.active for r in rows), "冻结员工的身份应停用"


def test_active_employee_identity_stays_active(db):
    """在职员工的身份**不能**被误停用。"""
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    service.sync_all()

    rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all()
    assert rows
    assert all(r.active for r in rows), "在职员工的身份应保持可用"


def test_unfreeze_restores_identity_automatically(db):
    """⚠️ 解冻 → 身份**自动恢复可用**（不必人工重新绑定）。

    这正是「停用 ≠ 解除绑定」的价值：
    绑定关系保留，只是暂时不可用于解析。
    """
    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1", is_frozen=True)])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    service.sync_all()

    rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all()
    assert all(not r.active for r in rows), "冻结后应停用"

    # 解冻后再同步一次
    recordings["GET /contact/v3/users"] = [_users_response([emp("u1")])]
    service2 = OrganizationSyncService(db, make_client(recordings), mode="full")
    service2.sync_all()

    rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all()
    assert all(r.active for r in rows), "解冻后身份应自动恢复"
    # ⚠️ 绑定关系没变 —— employee_id 不变
    assert all(r.employee_id == "u1" for r in rows)


def test_exited_employee_identity_not_restored_on_rejoin(db):
    """⚠️ 离职（exited）→ 即使远端又出现，**也不自动恢复**身份。

    离职后复职必须走人工确认绑定流程。
    """
    seed_employee(db, "u1", status="exited", account_status="disabled")
    # 身份已被停用（模拟离职同步后的状态）
    for row in db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all():
        row.active = False
    db.commit()

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    service.sync_all()

    rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u1")
    ).scalars().all()
    # 离职后复职：账号仍禁用，身份不应被自动激活
    employee = db.get(Employee, "u1")
    assert employee.account_status == "disabled", "离职后复职需人工确认"
    assert not any(r.active for r in rows), "离职后复职不应自动激活身份"


# ═══════════════════════════════════════════════════════════════════════════
# 二、全库对齐历史遗留错配
# ═══════════════════════════════════════════════════════════════════════════


def test_full_sync_reconciles_legacy_mismatched_identities(db):
    """⚠️ 全量同步应修正「本次没覆盖到」的员工的错配身份。

    这是真实库 39 条冲突的修复路径。
    """
    # u_legacy 不在本次同步范围内（远端只同步 u1）
    seed_employee(db, "u_legacy", status="inactive", account_status="disabled")

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    result = service.sync_all()

    legacy_rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u_legacy")
    ).scalars().all()
    assert legacy_rows
    assert all(not r.active for r in legacy_rows), "历史错配应被全库对齐修正"
    assert result["reconciliation"]["identities_fixed"] == 3


def test_incremental_mode_does_not_reconcile_identities(db):
    """⚠️ 增量模式**不做**全库对齐 —— 快照不完整时不能误判。"""
    seed_employee(db, "u_legacy", status="inactive", account_status="disabled")

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="incremental")
    result = service.sync_all()

    legacy_rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u_legacy")
    ).scalars().all()
    assert all(r.active for r in legacy_rows), "增量模式不应改动未覆盖员工的身份"
    assert result["reconciliation"]["identities_fixed"] == 0


def test_incomplete_snapshot_does_not_reconcile_identities(db):
    """⚠️ 快照不完整时**不做**全库对齐 —— 否则会误停用。"""
    seed_employee(db, "u_legacy", status="inactive", account_status="disabled")

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        # 部门员工拉取失败 → 快照不完整
        "GET /contact/v3/users": [
            {"status_code": 400, "json": {"code": 99992402, "msg": "field validation failed"}}
        ],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    result = service.sync_all()

    legacy_rows = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "u_legacy")
    ).scalars().all()
    assert all(r.active for r in legacy_rows), "快照不完整不应做全库对齐"
    assert result["reconciliation"]["identities_fixed"] == 0


def test_reconciliation_reports_zero_conflicts_after_fix(db):
    """⚠️ 修复后对账的 conflict_count 应降到 0。"""
    seed_employee(db, "u_legacy", status="inactive", account_status="disabled")

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    result = service.sync_all()

    recon = result["reconciliation"]
    inactive_conflicts = [
        c for c in recon["conflicts"]
        if c["type"] == "inactive_employee_has_active_identity"
    ]
    assert not inactive_conflicts, f"不应再有失效员工持有 active 身份: {inactive_conflicts}"


def test_dangling_identity_of_missing_employee_is_deactivated(db):
    """员工已不存在 → 悬空身份被停用。"""
    # 造一个悬空身份（employee_id 指向不存在的员工）
    db.add(
        FeishuIdentity(
            employee_id="ghost",
            identity_type="user_id",
            identity_value="ghost_uid",
            active=True,
        )
    )
    db.commit()

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [token_response()],
        "GET /contact/v3/departments/0/children": [
            _departments_response([dept("d1")])
        ],
        "GET /contact/v3/users": [_users_response([emp("u1")])],
    }
    service = OrganizationSyncService(db, make_client(recordings), mode="full")
    service.sync_all()

    ghost = db.execute(
        select(FeishuIdentity).where(FeishuIdentity.employee_id == "ghost")
    ).scalar_one()
    assert not ghost.active, "悬空身份应被停用"


# ═══════════════════════════════════════════════════════════════════════════
# 三、resolve_employee 防线
# ═══════════════════════════════════════════════════════════════════════════


def test_resolve_employee_rejects_disabled_employee(db):
    """⚠️ 防线：已禁用员工解析不到 —— 即使身份还是 active。"""
    seed_employee(db, "u1", status="inactive", account_status="disabled")
    identity_service = FeishuIdentityService(db)

    # ⚠️ 故意让身份保持 active（模拟历史错配未清理）
    assert identity_service.resolve_employee(user_id="u1") is None


def test_resolve_employee_rejects_exited_employee(db):
    """⚠️ 防线：已离职员工解析不到。"""
    seed_employee(db, "u1", status="exited", account_status="disabled")
    identity_service = FeishuIdentityService(db)

    assert identity_service.resolve_employee(user_id="u1") is None


def test_resolve_employee_accepts_active_employee(db):
    """在职且启用的员工正常解析。"""
    seed_employee(db, "u1", status="active", account_status="enabled")
    identity_service = FeishuIdentityService(db)

    resolved = identity_service.resolve_employee(user_id="u1")
    assert resolved is not None
    assert resolved.id == "u1"


def test_resolve_employee_accepts_by_open_id(db):
    """open_id 也能正常解析。"""
    seed_employee(db, "u1", status="active", account_status="enabled")
    identity_service = FeishuIdentityService(db)

    resolved = identity_service.resolve_employee(open_id="o-u1")
    assert resolved is not None
    assert resolved.id == "u1"


def test_resolve_employee_rejects_disabled_with_null_account_status(db):
    """⚠️ account_status 为空时不应误伤 —— 按未禁用处理。

    ⚠️ 历史上有些员工的 account_status 是 NULL（列是 NOT NULL 但
    早期数据可能为空）。若把 NULL 当成「禁用」，会把在职员工
    全部挡在门外 —— 那比原来的问题更糟。
    """
    employee = Employee(
        id="u1",
        name="员工",
        employment_status="active",
        account_status=None,
        feishu_user_id="u1",
    )
    db.add(employee)
    db.add(
        FeishuIdentity(
            employee_id="u1",
            identity_type="user_id",
            identity_value="u1",
            active=True,
        )
    )
    db.commit()

    identity_service = FeishuIdentityService(db)
    resolved = identity_service.resolve_employee(user_id="u1")
    assert resolved is not None, "account_status 为空不应被判为禁用"


# ═══════════════════════════════════════════════════════════════════════════
# 四、清理脚本
# ═══════════════════════════════════════════════════════════════════════════


def test_fix_script_detects_and_fixes_mismatches(tmp_path):
    """清理脚本应识别并修正错配，且保持在职员工不受影响。"""
    from backend.scripts import fix_identity_activation as fixer

    db_path = tmp_path / "fix_test.db"
    target_engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(target_engine)
    target_session = sessionmaker(bind=target_engine)()

    # 造错配数据：u_bad 已离职但身份 active；u_good 在职
    target_session.add_all([
        Employee(
            id="u_bad",
            name="离职员工",
            employment_status="inactive",
            account_status="disabled",
            feishu_user_id="u_bad",
        ),
        Employee(
            id="u_good",
            name="在职员工",
            employment_status="active",
            account_status="enabled",
            feishu_user_id="u_good",
        ),
    ])
    target_session.flush()
    for eid in ("u_bad", "u_good"):
        target_session.add(
            FeishuIdentity(
                employee_id=eid,
                identity_type="user_id",
                identity_value=eid,
                active=True,
            )
        )
    target_session.commit()
    target_session.close()

    analysis_session = sessionmaker(bind=target_engine)()
    analysis = fixer.analyse(analysis_session)
    assert len(analysis["to_disable"]) == 1, "应只识别出离职员工那一条"

    fixed = fixer.apply_fixes(analysis_session, analysis, "test-actor")
    assert fixed == 1
    analysis_session.commit()

    check = sessionmaker(bind=target_engine)()
    bad = check.execute(
        select(FeishuIdentity).where(
            FeishuIdentity.employee_id == "u_bad", FeishuIdentity.active.is_(True)
        )
    ).scalars().all()
    good = check.execute(
        select(FeishuIdentity).where(
            FeishuIdentity.employee_id == "u_good", FeishuIdentity.active.is_(True)
        )
    ).scalars().all()
    assert not bad, "离职员工身份应被停用"
    assert good, "在职员工身份不应受影响"
    assert fixer.verify(check) == 0
    check.close()
    target_engine.dispose()


def test_fix_script_is_idempotent(tmp_path):
    """⚠️ 重复执行不应二次修改 —— 幂等。"""
    from backend.scripts import fix_identity_activation as fixer

    db_path = tmp_path / "fix_idem.db"
    target_engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(target_engine)
    session = sessionmaker(bind=target_engine)()
    session.add(
        Employee(
            id="u_bad",
            name="离职员工",
            employment_status="inactive",
            account_status="disabled",
            feishu_user_id="u_bad",
        )
    )
    session.flush()
    session.add(
        FeishuIdentity(
            employee_id="u_bad",
            identity_type="user_id",
            identity_value="u_bad",
            active=True,
        )
    )
    session.commit()

    first = fixer.apply_fixes(session, fixer.analyse(session), "actor")
    assert first == 1

    second = fixer.apply_fixes(session, fixer.analyse(session), "actor")
    assert second == 0, "已修正的不应再改"
    assert fixer.verify(session) == 0
    session.close()
    target_engine.dispose()
