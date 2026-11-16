"""同步游标管理 —— 让「增量同步」真正省掉重复拉取（R17 / 需求2）。

⚠️ 为什么要单独一个模块
----------------------
原来的游标**只写不读**：``_save_cursor`` 把部门 ID 写进
``organization_sync_cursor`` 表，但下一次同步从头开始拉，
游标只是「看起来在用」的摆设。

本模块把游标变成**真的能被消费**的状态机。

飞书 API 的真实约束（重要）
---------------------------
飞书通讯录开放接口**没有**「按更新时间拉取变更」的端点：

- ``GET /contact/v3/departments/0/children`` —— 分页拉部门树，参数只有
  ``page_token`` / ``page_size``；
- ``GET /contact/v3/users`` —— 分页拉员工，参数只有
  ``page_token`` / ``page_size`` / ``department_id``。

要拿到「谁变了」只有两条路：
1. **订阅事件**（``contact.user.updated_v3`` 等）—— 真正的事件驱动，
   但要求应用订阅 + 开放平台配置，且事件推送可能丢；
2. **分页游标 + 幂等 upsert**（本模块）—— 拉取时把分页进度持久化，
   中断后续传；拉到的实体按飞书稳定 ID upsert，**没变化的不动**。

本模块实现第 2 条。它不能把「拉取量」降到零，但能：
- 批次中断后**从断点续传**，不重拉已成功的页；
- 每次同步**只处理游标之后的部门**，已同步过的部门跳过；
- 全量与增量共用同一套游标语义，切换模式不会丢位置。

游标状态机
----------
每个资源类型（``department`` / ``employee``）独立维护一条游标：

    ┌─────────────┐   全量拉完    ┌──────────────┐
    │ checkpoint  │ ───────────▶ │  cursor_value │
    │ 已处理到的  │              │  最后处理位置 │
    │ 逻辑位置    │              └──────────────┘
    └─────────────┘
          │ 增量拉取
          ▼
    ┌─────────────┐   该部门完成  ┌──────────────┐
    │ page_token  │ ───────────▶ │  checkpoint   │
    │ 当前页游标  │              │  推进到下一部门│
    └─────────────┘              └──────────────┘

**关键约束**：只有**资源处理成功后**才推进游标。
失败时保留旧位置 —— 下次从断点重来，而不是跳过失败的数据。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import OrganizationSyncCursor

logger = logging.getLogger(__name__)

# 资源类型常量 —— 与 `organization_sync_cursor.resource_type` 对应。
RESOURCE_DEPARTMENT = "department"
RESOURCE_EMPLOYEE = "employee"


@dataclass(frozen=True)
class CursorState:
    """一个资源的游标状态快照。"""

    resource_type: str
    #: 上次成功处理到的**逻辑位置**（部门 ID / 员工 ID）。
    checkpoint_value: str | None = None
    #: 上次拉取时的**分页游标**（飞书 `page_token`）。
    page_token: str | None = None
    #: 上次是否构成完整快照。完整快照才能判定「远端缺失 = 删除」。
    snapshot_complete: bool = False
    #: 推进该游标的批次 ID。
    last_batch_id: str | None = None

    @property
    def has_checkpoint(self) -> bool:
        return bool(self.checkpoint_value)

    @property
    def has_page_token(self) -> bool:
        return bool(self.page_token)


class CursorManager:
    """游标的读写与推进。

    封装「读游标 → 判断从哪继续 → 处理成功后推进」这套语义，
    让 ``organization_sync`` 不必到处直接操作游标表。
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ─────────────────────────────────────────────────────────────
    # 读
    # ─────────────────────────────────────────────────────────────

    def read(self, resource_type: str) -> CursorState:
        """读取游标状态；不存在时返回空状态。"""
        row = self.db.execute(
            select(OrganizationSyncCursor).where(
                OrganizationSyncCursor.resource_type == resource_type
            )
        ).scalar_one_or_none()
        if row is None:
            return CursorState(resource_type=resource_type)
        return CursorState(
            resource_type=row.resource_type,
            checkpoint_value=row.checkpoint_value,
            page_token=row.page_token,
            snapshot_complete=bool(row.snapshot_complete),
            last_batch_id=row.last_batch_id,
        )

    def read_all(self) -> dict[str, CursorState]:
        """读取全部资源类型的游标（供状态接口展示）。"""
        rows = self.db.execute(select(OrganizationSyncCursor)).scalars().all()
        return {
            row.resource_type: CursorState(
                resource_type=row.resource_type,
                checkpoint_value=row.checkpoint_value,
                page_token=row.page_token,
                snapshot_complete=bool(row.snapshot_complete),
                last_batch_id=row.last_batch_id,
            )
            for row in rows
        }

    # ─────────────────────────────────────────────────────────────
    # 写
    # ─────────────────────────────────────────────────────────────

    def _row(self, resource_type: str) -> OrganizationSyncCursor:
        row = self.db.execute(
            select(OrganizationSyncCursor).where(
                OrganizationSyncCursor.resource_type == resource_type
            )
        ).scalar_one_or_none()
        if row is None:
            row = OrganizationSyncCursor(resource_type=resource_type)
            self.db.add(row)
            self.db.flush()
        return row

    def save_page_token(
        self,
        resource_type: str,
        page_token: str | None,
        *,
        batch_id: str | None = None,
    ) -> None:
        """持久化**分页游标**（拉取中断后的续传点）。

        ⚠️ 传空字符串表示「已拉完最后一页」—— 必须把旧 token **清掉**，
        否则下次续传会拿一个**已失效**的 `page_token` 去请求 → 400。
        """
        row = self._row(resource_type)
        row.page_token = page_token or None
        if batch_id:
            row.last_batch_id = batch_id
        row.updated_at = _now()
        self.db.flush()

    def advance_checkpoint(
        self,
        resource_type: str,
        checkpoint_value: str | None,
        *,
        batch_id: str | None = None,
        snapshot_complete: bool | None = None,
        clear_page_token: bool = True,
    ) -> None:
        """推进**逻辑位置**游标（该资源处理成功后调用）。

        ⚠️ 只有处理成功才推进 —— 失败保留旧位置，下次从断点重来。
        """
        row = self._row(resource_type)
        if checkpoint_value is not None:
            row.checkpoint_value = checkpoint_value
            row.cursor_value = checkpoint_value
        if snapshot_complete is not None:
            row.snapshot_complete = snapshot_complete
        if clear_page_token:
            # 位置推进后，旧的 page_token 已无意义（它指向更早的页）。
            row.page_token = None
        if batch_id:
            row.last_batch_id = batch_id
        row.updated_at = _now()
        self.db.flush()

    def reset(self, resource_type: str) -> None:
        """重置游标（下次从头开始）。"""
        row = self.db.execute(
            select(OrganizationSyncCursor).where(
                OrganizationSyncCursor.resource_type == resource_type
            )
        ).scalar_one_or_none()
        if row is None:
            return
        row.cursor_value = None
        row.page_token = None
        row.checkpoint_value = None
        row.snapshot_complete = False
        row.updated_at = _now()
        self.db.flush()

    def commit(self) -> None:
        """提交游标变更。

        游标写入必须与业务数据**分开提交** —— 否则业务回滚时
        游标却已推进，下次就会跳过没写成功的数据。
        """
        try:
            self.db.commit()
        except Exception as exc:  # pragma: no cover - 游标写入失败不应阻断主流程
            self.db.rollback()
            logger.warning("提交同步游标失败: %s", _safe_error(exc))

    # ─────────────────────────────────────────────────────────────
    # 增量判定
    # ─────────────────────────────────────────────────────────────

    def departments_to_process(
        self,
        departments: list[dict[str, Any]],
        *,
        mode: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按游标切分部门：**待处理** 与 **已处理（跳过）**。

        Args:
            departments: 本次拉到的全量部门。
            mode: ``full`` 全部处理；``incremental`` 只处理游标之后的。

        Returns:
            ``(to_process, skipped)``

        ⚠️ 顺序很重要：飞书部门树的返回顺序**不保证稳定**
        （子树展开顺序可能变），所以不能简单地「跳过前 N 个」，
        必须按 **checkpoint 之后的部门 ID 集合**来判断。

        ⚠️ 如果 checkpoint 指向的部门**已经不在**远端了
        （部门被删了），那就**不能**按「之后」来判断 ——
        此时退化为「处理全部」，宁可多同步也不要漏同步。
        """
        if mode != "incremental":
            return list(departments), []

        state = self.read(RESOURCE_DEPARTMENT)
        if not state.has_checkpoint:
            return list(departments), []

        checkpoint = str(state.checkpoint_value)
        ids = [str(d.get("department_id") or "") for d in departments]
        if checkpoint not in ids:
            # 游标指向的部门已从远端消失 —— 位置失效，无法据此判断增量。
            # ⚠️ 宁可全量重同步，也不能因为「找不到起点」就漏掉整棵树。
            logger.warning(
                "增量游标指向的部门已不存在，退化为全量处理: checkpoint=%s", checkpoint
            )
            return list(departments), []

        idx = ids.index(checkpoint)
        # checkpoint 之后的部分 = 待处理；checkpoint 及之前 = 已处理。
        # （checkpoint 本身在上一轮已成功处理过。）
        to_process = [d for d in departments[idx + 1 :]]
        skipped = [d for d in departments[: idx + 1]]
        return to_process, skipped


def _now():
    from datetime import datetime

    return datetime.now(UTC).replace(tzinfo=None)


def _safe_error(exc: Exception) -> str:
    """脱敏后的错误消息（异常里可能含 App Secret / Token）。"""
    text = str(exc)
    for marker in ("app_secret", "tenant_access_token", "Authorization"):
        if marker in text:
            return f"{type(exc).__name__}: <已脱敏>"
    return text[:500]


__all__ = [
    "RESOURCE_DEPARTMENT",
    "RESOURCE_EMPLOYEE",
    "CursorManager",
    "CursorState",
]
