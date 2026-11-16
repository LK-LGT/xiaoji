"""组织架构变更事件消费者（R18）。

把队列里的飞书通讯录事件转成**实际的本地数据变更**。

处理策略
--------
| 事件 | 动作 |
|------|------|
| ``user_created`` / ``user_updated`` | 回查飞书拿**完整**用户 → upsert 员工 |
| ``user_deleted`` | 本地置为离职（``exited``）+ 禁用账号 + 停用身份绑定 |
| ``department_created`` / ``department_updated`` | 回查飞书拿**完整**部门 → upsert 部门 |
| ``department_deleted`` | 本地停用部门（``active=False``） |

⚠️ **为什么要回查，不能直接用事件载荷**
-----------------------------------------
飞书事件载荷**字段不完整**：

- 用户事件可能只有 ``user_id`` / ``name``，缺 ``open_id`` / ``union_id``
  —— 直接用会导致**身份绑定丢失**，员工登录不了；
- 部门事件可能缺 ``parent_department_id``
  —— 直接用会把**父部门关系清掉**，组织架构塌了。

所以事件只作为「**这里有个东西变了**」的**信号**，
真正落库的数据一律回查飞书拿最新的完整值。

⚠️ **事件不是唯一数据源**
-------------------------
飞书事件推送**可能丢失**。所以分页游标全量对账仍是兜底，
保证最终一致。事件路径只是「快路径」，让变更秒级可见。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import (
    Department,
    Employee,
    FeishuIdentity,
    OrganizationSyncBatch,
    OrgChangeQueue,
)
from .feishu_client import FeishuClient
from .org_change_queue import (
    EVENT_DEPARTMENT_CREATED,
    EVENT_DEPARTMENT_DELETED,
    EVENT_DEPARTMENT_UPDATED,
    EVENT_USER_CREATED,
    EVENT_USER_DELETED,
    EVENT_USER_UPDATED,
    claim_pending_events,
    mark_failed,
    mark_processed,
    release_stale_processing,
)
from .organization_sync import OrganizationSyncService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OrgChangeConsumer:
    """事件队列消费者。

    ⚠️ **单条串行处理**：事件可能乱序到达
    （先「改名」后「移动」，或反过来）。并行处理会让最终状态取决于
    线程调度顺序，而不是事件的真实先后。所以这里**刻意单条串行**。
    """

    def __init__(
        self,
        db: Session,
        feishu_client: FeishuClient,
        *,
        triggered_by: str | None = "system:org_change_consumer",
    ) -> None:
        self.db = db
        self.feishu_client = feishu_client
        self.triggered_by = triggered_by

    # ── 入口 ────────────────────────────────────────────────────────

    def process_pending(self, limit: int = 50) -> dict[str, int]:
        """处理待处理事件。

        Returns:
            ``{"processed": n, "failed": n, "dead_letter": n}``
        """
        # ⚠️ 先把僵死的 processing 重置 —— 上一轮崩溃留下的记录。
        released = release_stale_processing(self.db)

        rows = claim_pending_events(self.db, limit=limit)
        processed = 0
        failed = 0
        dead_letter = 0

        for row in rows:
            try:
                self._handle(row)
                mark_processed(self.db, row)
                processed += 1
            except Exception as exc:
                logger.warning(
                    "处理组织变更事件失败: event_id=%s type=%s resource=%s error=%s",
                    row.id,
                    row.event_type,
                    row.resource_id,
                    exc,
                )
                new_status = mark_failed(self.db, row, str(exc))
                failed += 1
                if new_status == "dead_letter":
                    dead_letter += 1

        return {
            "processed": processed,
            "failed": failed,
            "dead_letter": dead_letter,
            "released_stale": released,
        }

    # ── 分发 ────────────────────────────────────────────────────────

    def _handle(self, row: OrgChangeQueue) -> None:
        """按事件类型分发。"""
        if row.event_type in (EVENT_USER_CREATED, EVENT_USER_UPDATED):
            self._handle_user_upsert(row)
        elif row.event_type == EVENT_USER_DELETED:
            self._handle_user_deleted(row)
        elif row.event_type in (EVENT_DEPARTMENT_CREATED, EVENT_DEPARTMENT_UPDATED):
            self._handle_department_upsert(row)
        elif row.event_type == EVENT_DEPARTMENT_DELETED:
            self._handle_department_deleted(row)
        else:
            # 未知事件类型 —— 当作已处理，不阻塞队列。
            logger.info("忽略未知事件类型: %s", row.event_type)

    # ── 员工 ────────────────────────────────────────────────────────

    def _handle_user_upsert(self, row: OrgChangeQueue) -> None:
        """员工新增/变更 → 回查飞书拿完整数据后 upsert。"""
        user = self.feishu_client.fetch_employee(row.resource_id)
        if not user:
            # ⚠️ 回查返回空 —— 可能员工已被删除（事件乱序：先删后更新）。
            #    按删除处理，不能静默跳过。
            logger.info("回查用户为空，按删除处理: %s", row.resource_id)
            self._handle_user_deleted(row)
            return

        # ⚠️ 事件载荷里的 user_id 才是权威（回查可能返回别的 user_id_type）。
        user.setdefault("user_id", row.resource_id)
        if not user.get("user_id"):
            user["user_id"] = row.resource_id

        batch = self._ensure_batch(row)
        service = OrganizationSyncService(
            self.db,
            self.feishu_client,
            mode="incremental",
            triggered_by=self.triggered_by,
            trigger_type="event",
        )
        # ⚠️ `defer_manager=False`：事件级更新要能立即写上级。
        #    上级不在本地时 `_upsert_employee` 内部会留空，不会炸。
        service._upsert_employee(batch, user, defer_manager=False)
        self.db.commit()

    def _handle_user_deleted(self, row: OrgChangeQueue) -> None:
        """员工离职/被删 → 离职 + 禁用 + 停用身份绑定。

        ⚠️ **身份绑定必须一起停用** —— 否则
        ``resolve_employee()`` 会把一个已离职员工解析出来（安全风险）。
        """
        employee = self.db.execute(
            select(Employee).where(
                (Employee.id == row.resource_id)
                | (Employee.feishu_user_id == row.resource_id)
            )
        ).scalars().first()
        if employee is None:
            # 本地没有这个人 —— 无事可做。
            return

        if employee.employment_status != "exited":
            employee.employment_status = "exited"
            employee.feishu_deleted_at = _now()
        if employee.account_status != "disabled":
            employee.account_status = "disabled"
            employee.token_version = (employee.token_version or 1) + 1

        # 停用该员工的全部身份绑定。
        identities = self.db.execute(
            select(FeishuIdentity).where(
                FeishuIdentity.employee_id == employee.id,
                FeishuIdentity.active.is_(True),
            )
        ).scalars().all()
        for identity in identities:
            identity.active = False
            identity.unbound_at = _now()

        self.db.commit()

    # ── 部门 ────────────────────────────────────────────────────────

    def _handle_department_upsert(self, row: OrgChangeQueue) -> None:
        """部门新增/变更 → 回查飞书拿完整数据后 upsert。"""
        dept = self.feishu_client.fetch_department(row.resource_id)
        if not dept:
            logger.info("回查部门为空，按删除处理: %s", row.resource_id)
            self._handle_department_deleted(row)
            return

        dept.setdefault("department_id", row.resource_id)
        if not dept.get("department_id"):
            dept["department_id"] = row.resource_id

        batch = self._ensure_batch(row)
        service = OrganizationSyncService(
            self.db,
            self.feishu_client,
            mode="incremental",
            triggered_by=self.triggered_by,
            trigger_type="event",
        )
        service._upsert_department(batch, dept)
        self.db.commit()

    def _handle_department_deleted(self, row: OrgChangeQueue) -> None:
        """部门删除 → 本地停用。

        ⚠️ **不删员工** —— 员工可能还在别的部门；
        员工的离职判定交给全量对账（快照完整时才判）。
        """
        dept = self.db.get(Department, row.resource_id)
        if dept is None:
            return
        dept.active = False
        dept.feishu_deleted_at = _now()
        self.db.commit()

    # ── 批次 ────────────────────────────────────────────────────────

    def _ensure_batch(self, row: OrgChangeQueue) -> OrganizationSyncBatch:
        """为事件处理创建（或复用）一个批次。

        ⚠️ **为什么事件也要有批次**：事件处理会改数据，
        必须能被审计追溯 —— 「这条员工记录是谁、什么时候改的」。
        """
        if row.batch_id:
            batch = self.db.get(OrganizationSyncBatch, row.batch_id)
            if batch is not None:
                return batch

        from .organization_sync import create_sync_batch

        batch = create_sync_batch(
            self.db,
            mode="incremental",
            triggered_by=self.triggered_by,
            trigger_type="event",
            phase_detail="事件驱动增量",
        )
        row.batch_id = batch.id
        self.db.commit()
        return batch
