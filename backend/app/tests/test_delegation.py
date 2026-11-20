"""P1-4 acceptance: 代理授权 —— 有效期硬闸门 / 不放大权限 / 可撤销。"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    _now,
)
from backend.app.services import access_control
from backend.app.services import delegation_service as svc


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([Department(id="D", name="业务", leader_id="M"), Department(id="HR", name="人事")])
    s.flush()
    s.add(HrDepartmentConfig(department_id="HR", active=True))
    s.add_all([
        Employee(id="M", name="主管M", department_id="D", account_status="enabled", employment_status="active"),
        Employee(id="P", name="受托人P", department_id="D", manager_id="M", account_status="enabled", employment_status="active"),
        Employee(id="E1", name="员工E1", department_id="D", manager_id="M", account_status="enabled", employment_status="active"),
        Employee(id="E2", name="员工E2", department_id="D", manager_id="M", account_status="enabled", employment_status="active"),
        Employee(id="H", name="HR", department_id="HR", account_status="enabled", employment_status="active"),
    ])
    s.commit()
    yield s
    s.close()
    engine.dispose()


def _delegate(db, *, delegate="P", scope="direct_reports", value="", days=7, delegator="M"):
    now = _now()
    return svc.create_delegation(
        db,
        delegator_id=delegator,
        delegate_id=delegate,
        scope_type=scope,
        scope_value=value,
        reason="休假委托",
        starts_at=now,
        ends_at=now + timedelta(days=days),
    )


def test_direct_reports_scope(db):
    """默认范围 = 委托人的直属下属。"""
    row = _delegate(db)
    db.commit()
    assert row.scope_type == "direct_reports"
    targets = svc.delegated_employee_ids(db, delegate_id="P")
    # P 本身也是 M 的直属下属，因此也在快照范围内
    assert targets == {"E1", "E2", "P"}


def test_delegate_gains_access_within_validity(db):
    """有效期内，受托人获得委托范围内的访问权。"""
    _delegate(db)
    db.commit()
    context = access_control.build_access_context(db, db.get(Employee, "P"))
    assert context.can_view_score_of("E1")
    assert context.can_view_score_of("E2")
    # 范围外的人仍然无权
    assert not context.can_view_score_of("H")


def test_expired_delegation_loses_access(db):
    """到期后权限自动消失 —— 不依赖任何后台撤销任务。"""
    now = _now()
    svc.create_delegation(
        db,
        delegator_id="M",
        delegate_id="P",
        scope_type="direct_reports",
        reason="已过期",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
    )
    db.commit()
    context = access_control.build_access_context(db, db.get(Employee, "P"))
    assert not context.can_view_score_of("E1")
    assert svc.delegated_employee_ids(db, delegate_id="P") == set()


def test_not_yet_started_delegation_is_inactive(db):
    """尚未生效的委托不生效。"""
    now = _now()
    svc.create_delegation(
        db,
        delegator_id="M",
        delegate_id="P",
        scope_type="direct_reports",
        reason="未来生效",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db.commit()
    assert svc.delegated_employee_ids(db, delegate_id="P") == set()


def test_revoked_delegation_loses_access(db):
    """主动撤销后权限立即消失。"""
    row = _delegate(db)
    db.commit()
    assert svc.delegated_employee_ids(db, delegate_id="P")
    svc.revoke_delegation(db, delegation_id=row.id, actor_id="M")
    db.commit()
    assert svc.delegated_employee_ids(db, delegate_id="P") == set()


def test_cannot_delegate_to_self(db):
    """不能委托给自己。"""
    with pytest.raises(svc.DelegationError, match="不能委托给自己"):
        svc.create_delegation(
            db, delegator_id="M", delegate_id="M", scope_type="direct_reports",
            starts_at=_now(), ends_at=_now() + timedelta(days=1),
        )


def test_explicit_scope_cannot_amplify_permission(db):
    """显式白名单不能放大委托人的权限。"""
    with pytest.raises(svc.DelegationError, match="无权管理"):
        svc.create_delegation(
            db,
            delegator_id="M",
            delegate_id="P",
            scope_type="explicit",
            scope_value="H",
            starts_at=_now(),
            ends_at=_now() + timedelta(days=1),
        )


def test_explicit_scope_within_permission_is_allowed(db):
    """白名单在权限范围内时可创建。"""
    _delegate(db, scope="explicit", value="E1")
    db.commit()
    assert svc.delegated_employee_ids(db, delegate_id="P") == {"E1"}


def test_duplicate_delegation_is_rejected(db):
    """相同范围的重复委托被拒。"""
    _delegate(db)
    db.commit()
    with pytest.raises(svc.DelegationError, match="已存在"):
        _delegate(db)


def test_duration_limit(db):
    """单次委托时长有上限。"""
    now = _now()
    with pytest.raises(svc.DelegationError, match="不能超过"):
        svc.create_delegation(
            db,
            delegator_id="M",
            delegate_id="P",
            scope_type="direct_reports",
            starts_at=now,
            ends_at=now + timedelta(days=200),
        )


def test_end_before_start_is_rejected(db):
    """结束时间早于开始时间被拒。"""
    now = _now()
    with pytest.raises(svc.DelegationError, match="必须晚于"):
        svc.create_delegation(
            db,
            delegator_id="M",
            delegate_id="P",
            scope_type="direct_reports",
            starts_at=now,
            ends_at=now - timedelta(days=1),
        )


def test_unavailable_delegate_is_rejected(db):
    """离职受托人不可被委托。"""
    db.get(Employee, "P").employment_status = "exited"
    db.commit()
    with pytest.raises(svc.DelegationError, match="不可用"):
        _delegate(db)


def test_empty_scope_is_rejected(db):
    """范围内无人的委托被拒。"""
    db.get(Employee, "E1").manager_id = None
    db.get(Employee, "E2").manager_id = None
    # 受托人 P 也移出 M 的管理范围，否则范围非空
    db.get(Employee, "P").manager_id = None
    db.commit()
    with pytest.raises(svc.DelegationError, match="没有员工"):
        _delegate(db)


def test_department_scope(db):
    """部门范围委托。"""
    _delegate(db, scope="department")
    db.commit()
    targets = svc.delegated_employee_ids(db, delegate_id="P")
    assert "E1" in targets
    assert "E2" in targets
