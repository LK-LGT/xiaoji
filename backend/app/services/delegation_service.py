"""P1-4 —— 代理授权：主管请假/外出时的临时委托。

核心语义
--------
1. **委托人是权限来源，受托人是权限载体**：受托人在委托有效期内，
   对委托人原本有权管理的对象拥有**同等**管理权限。
2. **有效期是硬闸门**：``ends_at`` 之前有效，之后自动失效。
   不依赖后台任务去「撤销」—— 时间一到权限自然消失，不会残留。
3. **不放大权限**：受托人拿到的只是委托人的权限**交集**。
   委托人自己都无权管的人，委托后依然无权。
4. **可追溯**：创建与撤销都写审计日志。

作用域
------
``direct_reports``
    委托人原有的直属下属（默认）。委托时**快照**当时的下属集合，
    避免委托人换人后授权范围悄悄漂移。
``department``
    委托人管理的整个部门（按部门树展开）。
``explicit``
    显式员工 ID 白名单，逗号分隔。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Delegation, Employee, _now

logger = logging.getLogger(__name__)

#: 支持的作用域类型
SCOPE_TYPES = {"direct_reports", "department", "explicit"}

#: 委托的最大时长（天）—— 避免「一签三年」这种失控授权
MAX_DAYS = 90


class DelegationError(Exception):
    """代理授权失败。"""

    status_code = 400

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _direct_reports(db: Session, delegator_id: str) -> list[str]:
    """委托人当前的直属下属（在职）。"""
    rows = db.scalars(
        select(Employee.id).where(
            Employee.manager_id == delegator_id,
            Employee.employment_status == "active",
        )
    ).all()
    return list(rows)


def _managed_department_ids(db: Session, delegator_id: str) -> list[str]:
    """委托人管理的部门（按部门树展开）。"""
    from ..services.access_control import build_access_context

    delegator = db.get(Employee, delegator_id)
    if delegator is None:
        return []
    context = build_access_context(db, delegator)
    return sorted(context.managed_department_ids)


def _department_members(db: Session, departments: set[str]) -> set[str]:
    """部门内的在职员工。"""
    if not departments:
        return set()
    return set(
        db.scalars(
            select(Employee.id).where(
                Employee.department_id.in_(departments),
                Employee.employment_status == "active",
            )
        ).all()
    )


def resolve_scope(
    db: Session, *, delegator_id: str, scope_type: str, scope_value: str
) -> list[str]:
    """把作用域解析成**具体的员工 ID 集合**（快照语义）。

    Raises:
        DelegationError: 作用域类型非法或范围内无任何人。
    """
    if scope_type == "direct_reports":
        targets = _direct_reports(db, delegator_id)
    elif scope_type == "department":
        departments = _managed_department_ids(db, delegator_id)
        if not departments:
            raise DelegationError("委托人没有可委托的部门管理权限")
        targets = sorted(_department_members(db, set(departments)))
    elif scope_type == "explicit":
        targets = [x.strip() for x in (scope_value or "").split(",") if x.strip()]
        # 显式白名单也必须都是委托人有权限的人 —— 不能借委托放大权限
        allowed = set(_direct_reports(db, delegator_id))
        dept_members = _department_members(
            db, set(_managed_department_ids(db, delegator_id))
        )
        unknown = [t for t in targets if t not in allowed and t not in dept_members]
        if unknown:
            raise DelegationError(
                f"委托范围内包含委托人无权管理的员工：{', '.join(unknown)}", 403
            )
    else:
        raise DelegationError(f"不支持的委托范围类型：{scope_type}")

    if not targets:
        raise DelegationError("委托范围内没有员工")
    return targets


def create_delegation(
    db: Session,
    *,
    delegator_id: str,
    delegate_id: str,
    scope_type: str = "direct_reports",
    scope_value: str = "",
    reason: str = "",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    created_by: str | None = None,
) -> Delegation:
    """创建代理授权。

    Raises:
        DelegationError: 参数非法 / 自我委托 / 重复授权。
    """
    if delegator_id == delegate_id:
        raise DelegationError("不能委托给自己")
    if scope_type not in SCOPE_TYPES:
        raise DelegationError(f"不支持的委托范围类型：{scope_type}")

    delegator = db.get(Employee, delegator_id)
    delegate = db.get(Employee, delegate_id)
    if delegator is None or delegate is None:
        raise DelegationError("委托人或受托人不存在", 404)
    if delegate.employment_status != "active" or delegate.account_status != "enabled":
        raise DelegationError("受托人不可用（离职或停用）", 422)

    start = starts_at or _now()
    end = ends_at or _now()
    if end <= start:
        raise DelegationError("委托结束时间必须晚于开始时间", 422)
    if (end - start).days > MAX_DAYS:
        raise DelegationError(f"单次委托不能超过 {MAX_DAYS} 天", 422)

    # 解析并快照范围（显式白名单在此时校验权限交集）
    resolve_scope(
        db, delegator_id=delegator_id, scope_type=scope_type, scope_value=scope_value
    )

    existing = db.scalar(
        select(Delegation).where(
            Delegation.delegator_id == delegator_id,
            Delegation.delegate_id == delegate_id,
            Delegation.scope_type == scope_type,
            Delegation.scope_value == scope_value,
            Delegation.revoked_at.is_(None),
        )
    )
    if existing is not None:
        raise DelegationError("相同范围的委托已存在", 409)

    row = Delegation(
        delegator_id=delegator_id,
        delegate_id=delegate_id,
        scope_type=scope_type,
        scope_value=scope_value,
        reason=reason,
        starts_at=start,
        ends_at=end,
        created_by=created_by or delegator_id,
    )
    db.add(row)
    db.flush()
    return row


def revoke_delegation(db: Session, *, delegation_id: str, actor_id: str) -> Delegation:
    """撤销代理授权（到期前主动收回）。"""
    row = db.get(Delegation, delegation_id)
    if row is None:
        raise DelegationError("委托记录不存在", 404)
    if row.revoked_at is not None:
        raise DelegationError("该委托已撤销", 409)
    row.revoked_at = _now()
    db.flush()
    return row


def active_delegations(
    db: Session, *, delegate_id: str, now: datetime | None = None
) -> list[Delegation]:
    """取受托人当前生效的委托。

    生效条件：未撤销、已到开始时间、未过结束时间。
    """
    moment = now or _now()
    rows = db.scalars(
        select(Delegation).where(
            Delegation.delegate_id == delegate_id,
            Delegation.revoked_at.is_(None),
            Delegation.starts_at <= moment,
            Delegation.ends_at > moment,
        )
    ).all()
    return list(rows)


def delegated_employee_ids(
    db: Session, *, delegate_id: str, now: datetime | None = None
) -> set[str]:
    """受托人通过委托获得的员工范围（员工 ID 集合）。

    这是权限检查里唯一要用的入口 —— 委托到期后这个集合自动变小。
    """
    targets: set[str] = set()
    for row in active_delegations(db, delegate_id=delegate_id, now=now):
        resolved = resolve_scope(
            db,
            delegator_id=row.delegator_id,
            scope_type=row.scope_type,
            scope_value=row.scope_value,
        )
        targets.update(resolved)
    return targets


def serialize(row: Delegation) -> dict[str, Any]:
    """序列化为 API 形状。"""
    return {
        "id": row.id,
        "delegatorId": row.delegator_id,
        "delegateId": row.delegate_id,
        "scopeType": row.scope_type,
        "scopeValue": row.scope_value,
        "reason": row.reason,
        "startsAt": row.starts_at.isoformat(),
        "endsAt": row.ends_at.isoformat(),
        "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat(),
    }
