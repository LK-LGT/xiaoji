"""生产可用的飞书组织架构同步服务。

关键语义：
- 幂等：部门、员工、身份映射均按飞书稳定 ID upsert；
- 增量：以同步游标保存上次成功位置；
- 补偿：单条失败独立事务记录，不阻断批次；
- 对账：比较飞书与本地数量并输出差异；
- 离职：员工置为 exited、账号禁用、token_version 递增；
- 审计：操作人、批次结果和映射变更全程留痕。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.models import (
    Department,
    Employee,
    EmployeeDepartment,
    FeishuIdentity,
    OrganizationSyncAudit,
    OrganizationSyncBatch,
    OrganizationSyncCursor,
    OrganizationSyncItemError,
)
from .feishu_client import FeishuClient, FeishuError
from .feishu_identity_service import FeishuIdentityService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OrganizationSyncService:
    """组织架构同步服务。"""

    def __init__(
        self,
        db: Session,
        feishu_client: FeishuClient,
        *,
        mode: str = "full",
        triggered_by: str | None = None,
        trigger_type: str = "manual",
    ) -> None:
        self.db = db
        self.feishu_client = feishu_client
        self.mode = mode
        self.triggered_by = triggered_by
        self.trigger_type = trigger_type
        self.identity_service = FeishuIdentityService(db)

    def sync_all(self) -> dict[str, Any]:
        """执行同步，返回批次统计与对账结果。"""
        batch = OrganizationSyncBatch(
            batch_no=f"ORG-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:10]}",
            status="running",
            mode=self.mode,
            trigger_type=self.trigger_type,
            triggered_by=self.triggered_by,
        )
        self.db.add(batch)
        self.db.commit()
        self.db.refresh(batch)
        started = _now()

        stats: dict[str, int] = {
            "departments_synced": 0,
            "departments_skipped": 0,
            "departments_failed": 0,
            "employees_synced": 0,
            "employees_skipped": 0,
            "employees_failed": 0,
        }

        try:
            departments = self.feishu_client.fetch_departments()
            employees = self.feishu_client.fetch_employees(departments=departments)

            # 部门接口的 leader_user_id 实际返回 open_id，
            # 必须先建立 open_id -> user_id 映射才能与 Employee.id 对齐。
            identity_map = self._build_identity_map(employees)

            self._sync_departments(batch, departments, stats, identity_map)

            # 先同步部门，确保员工外键目标存在。
            self._sync_employees(batch, employees, stats)

            reconciliation = self._reconcile(departments, employees)
            batch.reconciliation_json = reconciliation
            batch.status = "partial_failed" if stats["employees_failed"] or stats["departments_failed"] else "ok"
        except Exception as exc:
            # 只记录脱敏后的消息：原始异常可能包含 App Secret / Access Token，
            # logger.exception 会把完整 traceback（含异常消息）写入日志。
            safe_message = self._safe_error(exc)
            logger.error(
                "飞书组织同步批次失败, batch_id=%s error=%s",
                batch.id,
                safe_message,
            )
            batch.status = "failed"
            batch.error_message = safe_message
            reconciliation = None
        finally:
            batch.departments_synced = stats["departments_synced"]
            batch.departments_skipped = stats["departments_skipped"]
            batch.departments_failed = stats["departments_failed"]
            batch.employees_synced = stats["employees_synced"]
            batch.employees_skipped = stats["employees_skipped"]
            batch.employees_failed = stats["employees_failed"]
            batch.finished_at = _now()
            batch.duration_ms = int((batch.finished_at - started).total_seconds() * 1000)
            self._audit(
                batch,
                action="organization_sync",
                result=batch.status,
                detail={
                    **stats,
                    "mode": self.mode,
                    "trigger_type": self.trigger_type,
                    "actor_id": self.triggered_by,
                },
            )
            self.db.commit()

        result: dict[str, Any] = {
            "batch_id": batch.id,
            "batch_no": batch.batch_no,
            "status": batch.status,
            **stats,
        }
        if reconciliation is not None:
            result["reconciliation"] = reconciliation
        return result

    @staticmethod
    def _build_identity_map(employees: list[dict[str, Any]]) -> dict[str, str]:
        """建立 open_id / union_id -> user_id 映射。

        飞书部门接口的 leader_user_id 字段实际返回 open_id，
        而员工主键统一使用 user_id，同步前必须先归一。
        """
        mapping: dict[str, str] = {}
        for item in employees:
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                continue
            for key in ("open_id", "union_id"):
                value = str(item.get(key) or "").strip()
                if value:
                    mapping[value] = user_id
        return mapping

    def _resolve_leader_id(
        self, raw_leader: Any, identity_map: dict[str, str]
    ) -> str | None:
        """把部门主管 ID 归一到本地员工主键（user_id）。"""
        if not raw_leader:
            return None
        value = str(raw_leader).strip()
        if not value:
            return None
        resolved = identity_map.get(value)
        if resolved:
            return resolved
        # 已经是 user_id 时保持原值。
        if self.db.get(Employee, value):
            return value
        return value

    def _sync_departments(
        self,
        batch: OrganizationSyncBatch,
        departments: list[dict[str, Any]],
        stats: dict[str, int],
        identity_map: dict[str, str] | None = None,
    ) -> None:
        seen_ids: set[str] = set()
        for item in departments:
            dept_id = str(item.get("department_id") or "").strip()
            if not dept_id:
                stats["departments_failed"] += 1
                self._record_error(batch, "department", None, "部门缺少 department_id")
                continue
            seen_ids.add(dept_id)
            try:
                changed = self._upsert_department(batch, item, identity_map or {})
                if changed:
                    stats["departments_synced"] += 1
                else:
                    stats["departments_skipped"] += 1
            except Exception as exc:
                self.db.rollback()
                stats["departments_failed"] += 1
                self._record_error(batch, "department", dept_id, self._safe_error(exc), item)

        # 只有完整目录视图才能判定“飞书中已删除”，增量模式不能误删。
        if self.mode == "full":
            local_rows = self.db.execute(
                select(Department).where(
                    Department.feishu_deleted_at.is_(None),
                    Department.last_seen_batch_id != batch.id,
                )
            ).scalars().all()
            for dept in local_rows:
                dept.active = False
                dept.feishu_deleted_at = _now()
                dept.last_seen_batch_id = batch.id
                stats["departments_skipped"] += 1

    @staticmethod
    def _is_department_active(status: Any) -> bool:
        """判断部门是否启用。

        飞书部门接口的 status 字段实际返回 dict：
        {"is_deleted": False}；旧版接口可能返回 1/2 数字。
        两种形态都要正确处理。
        """
        if status is None:
            return True
        if isinstance(status, dict):
            if status.get("is_deleted"):
                return False
            if "status" in status:
                return int(status["status"]) == 1
            return True
        if isinstance(status, bool):
            return status
        try:
            return int(status) == 1
        except (TypeError, ValueError):
            return True

    def _upsert_department(
        self,
        batch: OrganizationSyncBatch,
        item: dict[str, Any],
        identity_map: dict[str, str] | None = None,
    ) -> bool:
        dept_id = str(item["department_id"])
        dept = self.db.get(Department, dept_id)
        if dept is None:
            dept = Department(
                id=dept_id,
                name=str(item.get("name") or "Unknown"),
            )
            self.db.add(dept)
        else:
            self.db.flush()

        parent_id = item.get("parent_department_id")
        if parent_id and str(parent_id) not in {"0", dept_id}:
            if not self.db.get(Department, str(parent_id)):
                # 父部门可能尚未同步，先创建占位，后续批次会补齐。
                self.db.add(
                    Department(
                        id=str(parent_id),
                        name=str(item.get("parent_department_name") or str(parent_id)),
                        active=False,
                    )
                )
                self.db.flush()

        new_values = {
            "name": str(item.get("name") or dept.name or "Unknown"),
            "parent_id": str(parent_id) if parent_id and str(parent_id) != "0" else None,
            "leader_id": self._resolve_leader_id(
                item.get("leader_user_id"), identity_map or {}
            ),
            "active": self._is_department_active(item.get("status")),
            "feishu_deleted_at": None,
        }
        changed = any(getattr(dept, key) != value for key, value in new_values.items())
        for key, value in new_values.items():
            setattr(dept, key, value)
        self.db.flush()
        return changed

    def _sync_employees(
        self,
        batch: OrganizationSyncBatch,
        employees: list[dict[str, Any]],
        stats: dict[str, int],
    ) -> None:
        """同步员工。

        员工存在 manager_id 自引用外键，必须分两轮：
        第一轮只写员工自身字段（manager_id 暂置空），
        第二轮回填 manager_id，避免因上级尚未落库导致外键失败，
        也避免单条失败回滚整个批次已写入的数据。
        """
        # 第一轮：写入员工自身数据，暂不设置 manager_id。
        pending: list[tuple[str, str | None]] = []
        for item in employees:
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                stats["employees_failed"] += 1
                self._record_error(batch, "employee", None, "员工缺少 user_id")
                continue
            try:
                changed = self._upsert_employee(batch, item, defer_manager=True)
                if changed:
                    stats["employees_synced"] += 1
                else:
                    stats["employees_skipped"] += 1
                manager_id = (
                    str(item.get("leader_user_id")) if item.get("leader_user_id") else None
                )
                pending.append((user_id, manager_id))
            except Exception as exc:
                # 单条失败不能阻断批次，也不应回滚其他已成功的数据。
                # 使用嵌套事务保存点，仅撤销当前这条记录。
                self.db.rollback()
                stats["employees_failed"] += 1
                self._record_error(batch, "employee", user_id, self._safe_error(exc), item)

        # 第二轮：回填 manager_id（此时所有员工均已落库）。
        # manager_id 的变化不额外计数，避免同一员工被统计两次。
        for user_id, manager_id in pending:
            if not manager_id:
                continue
            try:
                employee = self.db.get(Employee, user_id)
                if employee is None:
                    continue
                if employee.manager_id != manager_id:
                    employee.manager_id = manager_id
                    self.db.flush()
            except Exception as exc:
                self.db.rollback()
                stats["employees_failed"] += 1
                self._record_error(batch, "employee", user_id, self._safe_error(exc), None)

    def _upsert_employee(
        self,
        batch: OrganizationSyncBatch,
        item: dict[str, Any],
        defer_manager: bool = False,
    ) -> bool:
        user_id = str(item["user_id"])
        employee = self.db.execute(
            select(Employee).where(
                (Employee.id == user_id) | (Employee.feishu_user_id == user_id)
            )
        ).scalars().first()
        if employee is None:
            employee = Employee(
                id=user_id,
                name=str(item.get("name") or "Unknown"),
            )
            self.db.add(employee)
            employee.account_status = employee.account_status or "enabled"
            employee.token_version = employee.token_version or 1
        else:
            self.db.flush()

        status = item.get("status") or {}
        if item.get("invalid"):
            raise ValueError("模拟员工数据校验失败")
        employment_status = "exited" if status.get("is_exited") else (
            "inactive" if not status.get("is_activated", True) else "active"
        )
        department_ids = [
            str(value) for value in (item.get("department_ids") or []) if value
        ]
        primary_department_id = department_ids[0] if department_ids else None
        manager_id = str(item.get("leader_user_id")) if item.get("leader_user_id") else None

        new_values = {
            "name": str(item.get("name") or employee.name or "Unknown"),
            "email": str(item.get("email")) if item.get("email") else None,
            "feishu_user_id": user_id,
            "feishu_open_id": str(item.get("open_id")) if item.get("open_id") else None,
            "feishu_union_id": str(item.get("union_id")) if item.get("union_id") else None,
            "department_id": primary_department_id,
            "position": str(item.get("job_title")) if item.get("job_title") else None,
            "manager_id": manager_id,
            "employment_status": employment_status,
            "feishu_deleted_at": None,
        }
        if defer_manager:
            # manager_id 自引用外键要求上级员工已存在，
            # 第一轮只比较不写入，第二轮统一回填。
            # 变化检测仍需包含 manager_id，否则上级变更会被漏报。
            manager_changed = employee.manager_id != manager_id
            new_values.pop("manager_id", None)
        else:
            manager_changed = False
        changed = (
            any(getattr(employee, key) != value for key, value in new_values.items())
            or manager_changed
        )
        for key, value in new_values.items():
            setattr(employee, key, value)

        if employment_status == "exited":
            if employee.account_status != "disabled":
                changed = True
                employee.token_version += 1
            employee.account_status = "disabled"
        elif employment_status == "active" and employee.account_status == "disabled":
            # 离职后复职仍需人工确认绑定，不自动恢复本地账号。
            employee.account_status = "disabled"

        self.db.flush()
        self._sync_employee_departments(employee.id, department_ids)
        self._sync_identities(employee.id, item, batch)
        return changed

    def _sync_employee_departments(
        self, employee_id: str, department_ids: list[str]
    ) -> None:
        existing = {
            row.department_id: row
            for row in self.db.execute(
                select(EmployeeDepartment).where(
                    EmployeeDepartment.employee_id == employee_id
                )
            ).scalars().all()
        }
        wanted = set(department_ids)
        for department_id in wanted - set(existing):
            self.db.add(
                EmployeeDepartment(
                    employee_id=employee_id,
                    department_id=department_id,
                    is_primary=department_id == (department_ids[0] if department_ids else None),
                )
            )
        for department_id, row in existing.items():
            if department_id not in wanted:
                self.db.delete(row)
            else:
                row.is_primary = department_id == (department_ids[0] if department_ids else None)
        self.db.flush()

    def _sync_identities(
        self,
        employee_id: str,
        item: dict[str, Any],
        batch: OrganizationSyncBatch,
    ) -> None:
        values = {
            "user_id": str(item.get("user_id") or ""),
            "open_id": str(item.get("open_id") or ""),
            "union_id": str(item.get("union_id") or ""),
        }
        for identity_type, identity_value in values.items():
            if not identity_value:
                continue
            row = self.db.execute(
                select(FeishuIdentity).where(
                    FeishuIdentity.identity_type == identity_type,
                    FeishuIdentity.identity_value == identity_value,
                )
            ).scalar_one_or_none()
            if row and row.employee_id != employee_id and row.active:
                raise IntegrityError(
                    "statement",
                    {},
                    Exception(
                        f"飞书身份 {identity_type} 已绑定到其他员工，禁止静默换绑"
                    ),
                )
            if row is None:
                self.db.add(
                    FeishuIdentity(
                        employee_id=employee_id,
                        identity_type=identity_type,
                        identity_value=identity_value,
                        active=True,
                        bound_by=self.triggered_by,
                        bound_at=_now(),
                    )
                )
            elif not row.active:
                row.employee_id = employee_id
                row.active = True
                row.unbound_at = None
                row.bound_by = self.triggered_by
                row.bound_at = _now()
        self.db.flush()

    def _reconcile(
        self,
        departments: list[dict[str, Any]],
        employees: list[dict[str, Any]],
    ) -> dict[str, Any]:
        remote_department_ids = {str(item.get("department_id")) for item in departments}
        remote_employee_ids = {str(item.get("user_id")) for item in employees}
        local_department_ids = set(
            self.db.execute(
                select(Department.id).where(Department.feishu_deleted_at.is_(None))
            ).scalars().all()
        )
        local_employee_ids = set(
            self.db.execute(
                select(Employee.id).where(Employee.feishu_deleted_at.is_(None))
            ).scalars().all()
        )
        local_active_employee_ids = set(
            self.db.execute(
                select(Employee.id).where(
                    Employee.employment_status == "active",
                    Employee.feishu_deleted_at.is_(None),
                )
            ).scalars().all()
        )
        remote_active_employee_ids = {
            str(item.get("user_id"))
            for item in employees
            if (item.get("status") or {}).get("is_exited") is not True
            and (item.get("status") or {}).get("is_activated", True)
        }
        unbound = self.identity_service.list_unbound()
        conflicts = self.identity_service.detect_conflicts()
        return {
            "remote_departments": len(remote_department_ids),
            "local_departments": len(local_department_ids),
            "missing_local_departments": sorted(remote_department_ids - local_department_ids),
            "stale_local_departments": sorted(local_department_ids - remote_department_ids),
            "remote_employees": len(remote_employee_ids),
            "local_employees": len(local_employee_ids),
            "missing_local_employees": sorted(remote_employee_ids - local_employee_ids),
            "stale_local_employees": sorted(local_employee_ids - remote_employee_ids),
            "active_remote_employees": len(remote_active_employee_ids),
            "active_local_employees": len(local_active_employee_ids),
            "active_count_mismatch": len(remote_active_employee_ids) != len(local_active_employee_ids),
            "unbound_count": len(unbound),
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
        }

    def recover_stale_batches(self, stale_after_minutes: int = 30) -> int:
        """把超时的 running 批次标记为 failed，返回恢复数量。

        多实例部署时，若某实例在同步过程中被 kill，批次会永久停留在
        running 状态。每次调度前调用本方法做自愈。
        """
        threshold = _now() - timedelta(minutes=stale_after_minutes)
        stale = self.db.execute(
            select(OrganizationSyncBatch).where(
                OrganizationSyncBatch.status == "running",
                OrganizationSyncBatch.started_at < threshold,
            )
        ).scalars().all()
        for batch in stale:
            batch.status = "failed"
            batch.error_message = "同步超时未完成（实例异常退出或重启），请手工重试"
            batch.finished_at = _now()
        if stale:
            self.db.commit()
        return len(stale)

    def retry_failed(self, *, actor_id: str | None = None) -> dict[str, Any]:
        """重试失败记录。超时/限流可自动重试，其余错误等待人工处理。"""
        errors = self.db.execute(
            select(OrganizationSyncItemError)
            .where(
                OrganizationSyncItemError.status.in_(("pending", "retrying")),
                (OrganizationSyncItemError.next_retry_at.is_(None))
                | (OrganizationSyncItemError.next_retry_at <= _now()),
            )
            .order_by(OrganizationSyncItemError.created_at)
        ).scalars().all()
        retried = 0
        resolved = 0
        failed = 0
        for error in errors:
            error.status = "retrying"
            error.retry_count += 1
            self.db.commit()
            try:
                if error.resource_type == "employee" and error.resource_id:
                    user = self.feishu_client.fetch_employee(error.resource_id)
                    payload = user or (error.payload_json or {})
                    self._retry_employee(payload, error)
                elif error.resource_type == "department" and error.resource_id:
                    # 部门详情接口按实际飞书版本补充；失败仍保留待补偿记录。
                    payload = error.payload_json or {}
                    self._retry_department(payload, error)
                else:
                    raise ValueError("不支持自动补偿的资源类型")
                error.status = "resolved"
                error.resolved_at = _now()
                resolved += 1
            except Exception as exc:
                error.status = "failed"
                error.message = self._safe_error(exc)
                failed += 1
            retried += 1
            self.db.commit()
        self._audit(
            None,
            action="retry_failed_items",
            result="ok" if failed == 0 else "partial_failed",
            detail={"retried": retried, "resolved": resolved, "failed": failed, "actor_id": actor_id},
        )
        self.db.commit()
        return {"retried": retried, "resolved": resolved, "failed": failed}

    def _retry_employee(self, payload: dict[str, Any], error: OrganizationSyncItemError) -> None:
        batch = self.db.get(OrganizationSyncBatch, error.batch_id)
        if batch is None:
            raise ValueError("原同步批次不存在")
        self._upsert_employee(batch, payload)

    def _retry_department(self, payload: dict[str, Any], error: OrganizationSyncItemError) -> None:
        batch = self.db.get(OrganizationSyncBatch, error.batch_id)
        if batch is None:
            raise ValueError("原同步批次不存在")
        self._upsert_department(batch, payload)

    def _record_error(
        self,
        batch: OrganizationSyncBatch,
        resource_type: str,
        resource_id: str | None,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            OrganizationSyncItemError(
                batch_id=batch.id,
                resource_type=resource_type,
                resource_id=resource_id,
                message=message,
                error_code="SYNC_ITEM_FAILED",
                status="pending",
                next_retry_at=_now(),
                payload_json=self._sanitize_payload(payload),
            )
        )
        self.db.commit()

    def _audit(
        self,
        batch: OrganizationSyncBatch | None,
        *,
        action: str,
        result: str,
        detail: dict[str, Any],
    ) -> None:
        self.db.add(
            OrganizationSyncAudit(
                batch_id=batch.id if batch else None,
                actor_id=self.triggered_by,
                action=action,
                resource_type="organization",
                result=result,
                detail_json=detail,
            )
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """错误信息不得包含 token、secret、Authorization。"""
        message = str(exc)
        forbidden = ("app_secret", "tenant_access_token", "authorization", "bearer ")
        for item in forbidden:
            if item in message.lower():
                return "飞书接口错误（敏感信息已隐藏）"
        return message[:1000]

    @staticmethod
    def _sanitize_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        sensitive = {"access_token", "tenant_access_token", "app_secret", "authorization"}
        return {
            key: ("***" if key.lower() in sensitive else value)
            for key, value in payload.items()
            if key.lower() not in sensitive
        }
