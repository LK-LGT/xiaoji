"""飞书身份映射服务。

职责：
- 飞书 user_id / open_id / union_id 与本地员工建立唯一映射；
- 人工绑定、解绑、换绑与冲突检测；
- 离职员工账号禁用、Token 版本失效；
- 映射变更审计。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.models import Employee, FeishuIdentity, FeishuIdentityAudit
from ..exceptions import DataValidationError, DuplicateRecord, RecordNotFound


IDENTITY_TYPES = {"user_id", "open_id", "union_id"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit(
    db: Session,
    *,
    action: str,
    actor_id: str | None,
    identity_id: str | None = None,
    identity_type: str | None = None,
    identity_value: str | None = None,
    employee_id: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    db.add(
        FeishuIdentityAudit(
            identity_id=identity_id,
            identity_type=identity_type,
            identity_value=identity_value,
            employee_id=employee_id,
            action=action,
            old_value_json=old_value,
            new_value_json=new_value,
            reason=reason,
            actor_id=actor_id,
        )
    )


class IdentityConflictError(DataValidationError):
    """身份绑定冲突。

    语义上是「资源状态冲突」而不是「请求参数非法」，
    因此状态码为 409 而不是 422。
    """

    def __init__(self, message: str, conflicts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.conflicts = conflicts
        self.status_code = 409


class FeishuIdentityService:
    """身份映射领域服务。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def bind(
        self,
        *,
        employee_id: str,
        identity_type: str,
        identity_value: str,
        actor_id: str | None,
        reason: str | None = None,
        allow_rebind: bool = False,
    ) -> FeishuIdentity:
        """人工绑定一个飞书身份。

        默认禁止静默换绑；必须显式 allow_rebind=True。
        """
        identity_type = identity_type.strip().lower()
        identity_value = identity_value.strip()
        if identity_type not in IDENTITY_TYPES:
            raise DataValidationError(
                f"不支持的身份类型：{identity_type}", field="identity_type"
            )
        if not identity_value:
            raise DataValidationError("身份值不能为空", field="identity_value")

        employee = self.db.get(Employee, employee_id)
        if employee is None:
            raise RecordNotFound("Employee", employee_id)

        existing = self.db.execute(
            select(FeishuIdentity).where(
                FeishuIdentity.identity_type == identity_type,
                FeishuIdentity.identity_value == identity_value,
            )
        ).scalar_one_or_none()

        if existing and existing.active:
            if existing.employee_id != employee_id:
                if not allow_rebind:
                    raise IdentityConflictError(
                        "该飞书身份已绑定其他员工",
                        [
                            {
                                "identity_type": identity_type,
                                "identity_value": identity_value,
                                "employee_id": existing.employee_id,
                                "conflict": "identity_bound_to_other_employee",
                            }
                        ],
                    )
                old_value = {
                    "employee_id": existing.employee_id,
                    "active": existing.active,
                    "bound_by": existing.bound_by,
                }
                existing.active = False
                existing.unbound_at = _now()
                _audit(
                    self.db,
                    action="unbind_for_rebind",
                    actor_id=actor_id,
                    identity_id=existing.id,
                    identity_type=identity_type,
                    identity_value=identity_value,
                    employee_id=existing.employee_id,
                    old_value=old_value,
                    new_value={"active": False},
                    reason=reason,
                )
            else:
                return existing

        # 一个员工同一身份类型只能有一个有效值。
        old_for_employee = self.db.execute(
            select(FeishuIdentity).where(
                FeishuIdentity.employee_id == employee_id,
                FeishuIdentity.identity_type == identity_type,
                FeishuIdentity.active.is_(True),
            )
        ).scalars().first()
        if old_for_employee and old_for_employee.identity_value != identity_value:
            old_for_employee.active = False
            old_for_employee.unbound_at = _now()
            _audit(
                self.db,
                action="replace_employee_identity",
                actor_id=actor_id,
                identity_id=old_for_employee.id,
                identity_type=identity_type,
                identity_value=old_for_employee.identity_value,
                employee_id=employee_id,
                old_value={"identity_value": old_for_employee.identity_value, "active": True},
                new_value={"active": False},
                reason=reason,
            )

        row = existing
        if row is None:
            row = FeishuIdentity(
                employee_id=employee_id,
                identity_type=identity_type,
                identity_value=identity_value,
            )
            self.db.add(row)
        else:
            row.employee_id = employee_id
            row.active = True
            row.unbound_at = None
            row.bound_by = actor_id
            row.bound_at = _now()
        self.db.flush()

        _audit(
            self.db,
            action="bind",
            actor_id=actor_id,
            identity_id=row.id,
            identity_type=identity_type,
            identity_value=identity_value,
            employee_id=employee_id,
            new_value={"employee_id": employee_id, "active": True},
            reason=reason,
        )
        return row

    def unbind(
        self,
        *,
        identity_id: str,
        actor_id: str,
        reason: str,
    ) -> None:
        row = self.db.get(FeishuIdentity, identity_id)
        if row is None:
            raise RecordNotFound("FeishuIdentity", identity_id)
        if not row.active:
            return
        row.active = False
        row.unbound_at = _now()
        self.db.flush()
        _audit(
            self.db,
            action="unbind",
            actor_id=actor_id,
            identity_id=row.id,
            identity_type=row.identity_type,
            identity_value=row.identity_value,
            employee_id=row.employee_id,
            old_value={"active": True},
            new_value={"active": False},
            reason=reason,
        )

    def bind_many(
        self,
        *,
        employee_id: str,
        identities: dict[str, str],
        actor_id: str | None,
        reason: str | None = None,
    ) -> list[FeishuIdentity]:
        rows: list[FeishuIdentity] = []
        for identity_type, identity_value in identities.items():
            if identity_value:
                rows.append(
                    self.bind(
                        employee_id=employee_id,
                        identity_type=identity_type,
                        identity_value=identity_value,
                        actor_id=actor_id,
                        reason=reason,
                    )
                )
        return rows

    def resolve_employee(self, **identity_values: str) -> Employee | None:
        """按一个或多个身份字段解析唯一有效员工。"""
        clauses = []
        for identity_type, value in identity_values.items():
            if identity_type not in IDENTITY_TYPES or not value:
                continue
            clauses.append(
                (FeishuIdentity.identity_type == identity_type)
                & (FeishuIdentity.identity_value == value)
                & (FeishuIdentity.active.is_(True))
            )
        if not clauses:
            return None
        rows = self.db.execute(
            select(FeishuIdentity).where(or_(*clauses))
        ).scalars().all()
        employee_ids = {row.employee_id for row in rows}
        if len(employee_ids) != 1:
            return None
        return self.db.get(Employee, next(iter(employee_ids)))

    def detect_conflicts(self) -> list[dict[str, Any]]:
        """检测一人多账号、账号多身份及失效员工绑定冲突。"""
        conflicts: list[dict[str, Any]] = []

        rows = self.db.execute(
            select(FeishuIdentity).where(FeishuIdentity.active.is_(True))
        ).scalars().all()
        by_employee_type: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            by_employee_type.setdefault((row.employee_id, row.identity_type), []).append(
                row.identity_value
            )
        for (employee_id, identity_type), values in by_employee_type.items():
            if len(values) > 1:
                conflicts.append(
                    {
                        "type": "multiple_active_identities",
                        "employee_id": employee_id,
                        "identity_type": identity_type,
                        "values": values,
                    }
                )

        by_value: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            by_value.setdefault((row.identity_type, row.identity_value), []).append(
                row.employee_id
            )
        for (identity_type, value), employee_ids in by_value.items():
            if len(set(employee_ids)) > 1:
                conflicts.append(
                    {
                        "type": "identity_bound_to_multiple_employees",
                        "identity_type": identity_type,
                        "identity_value": value,
                        "employee_ids": sorted(set(employee_ids)),
                    }
                )

        for row in rows:
            employee = self.db.get(Employee, row.employee_id)
            if employee and employee.employment_status != "active":
                conflicts.append(
                    {
                        "type": "inactive_employee_has_active_identity",
                        "employee_id": row.employee_id,
                        "identity_type": row.identity_type,
                        "identity_value": row.identity_value,
                    }
                )
        return conflicts

    def list_unbound(self) -> list[dict[str, Any]]:
        """列出尚无有效身份映射的飞书员工。"""
        rows = self.db.execute(
            select(Employee).where(
                Employee.feishu_user_id.is_not(None),
                ~select(FeishuIdentity.id)
                .where(
                    FeishuIdentity.employee_id == Employee.id,
                    FeishuIdentity.active.is_(True),
                )
                .exists(),
            )
        ).scalars().all()
        return [
            {
                "employee_id": row.id,
                "name": row.name,
                "feishu_user_id": row.feishu_user_id,
                "feishu_open_id": row.feishu_open_id,
                "feishu_union_id": row.feishu_union_id,
                "employment_status": row.employment_status,
            }
            for row in rows
        ]

    def disable_account(
        self,
        *,
        employee_id: str,
        actor_id: str | None,
        reason: str,
    ) -> None:
        """禁用员工账号并使历史 Token 失效。"""
        employee = self.db.get(Employee, employee_id)
        if employee is None:
            raise RecordNotFound("Employee", employee_id)
        old_value = {
            "account_status": employee.account_status,
            "token_version": employee.token_version,
            "employment_status": employee.employment_status,
        }
        employee.account_status = employee.account_status or "enabled"
        employee.token_version = employee.token_version or 1
        employee.account_status = "disabled"
        employee.token_version += 1
        self.db.flush()
        _audit(
            self.db,
            action="disable_account",
            actor_id=actor_id,
            employee_id=employee_id,
            old_value=old_value,
            new_value={
                "account_status": employee.account_status,
                "token_version": employee.token_version,
            },
            reason=reason,
        )

    def set_login_account(
        self,
        *,
        employee_id: str,
        login_account: str,
        actor_id: str,
        reason: str,
    ) -> None:
        employee = self.db.get(Employee, employee_id)
        if employee is None:
            raise RecordNotFound("Employee", employee_id)
        normalized = login_account.strip().lower()
        if not normalized:
            raise DataValidationError("登录账号不能为空", field="login_account")
        conflict = self.db.execute(
            select(Employee).where(
                Employee.login_account == normalized,
                Employee.id != employee_id,
            )
        ).scalar_one_or_none()
        if conflict:
            raise DuplicateRecord("Employee", "login_account", normalized)
        old_value = {"login_account": employee.login_account}
        employee.login_account = normalized
        self.db.flush()
        _audit(
            self.db,
            action="set_login_account",
            actor_id=actor_id,
            employee_id=employee_id,
            old_value=old_value,
            new_value={"login_account": normalized},
            reason=reason,
        )
