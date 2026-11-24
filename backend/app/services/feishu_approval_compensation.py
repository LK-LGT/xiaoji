"""飞书审批回流的**定时补偿**（FS-7）。

⚠️ **为什么需要主动拉取**
------------------------------------
审批回流原本依赖飞书 webhook 事件推送（``apply_feishu_approval_event``）。
但 webhook **不是可靠的**：

- 飞书侧偶发丢事件（服务抖动、网络分区）；
- 事件投递失败后**不会补发**；
- 本服务重启期间的事件**永久丢失**。

⇒ 结果是：员工在飞书点了「通过」，但本系统的计划**一直卡在审批中**。
⇒ 必须有**主动轮询**兜底。

⚠️ **与 webhook 互补，不是替代**
------------------------------------
两条路径最终都走 ``apply_feishu_approval_event``，
靠 ``dedup_key``（``feishu:{type}:{id}:{action}:{ver}:{instance}``）幂等 ——
⇒ 同一个事件被两条路径各处理一次，也**只会写一条流水**。

⚠️ **为什么要有「陈旧阈值」
------------------------------------
轮询不能太频繁 —— 飞书对审批接口有频控，而且每个待审批实例
都要打一次 ``GET /approval/v4/instances/{id}``。

⇒ 只有**超过阈值没更新**的绑定才值得去拉。
⇒ 阈值的作用是：**在「及时性」与「频控」之间取平衡**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.plan_models import PlanFeishuBinding

logger = logging.getLogger(__name__)

#: 飞书审批实例的**终态** —— 到了这些状态就不必再轮询了。
#:
#: ⚠️ 与 ``feishu_approval_sync.FEISHU_STATUS_TO_ACTION`` 的键保持一致。
#:    终态之外（PENDING / APPROVING 等）都还在流程中，需要继续盯着。
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"APPROVED", "REJECTED", "CANCELED", "WITHDRAWN"}
)

#: 默认陈旧阈值（秒）—— 超过这个时间没更新才去拉。
#:
#: ⚠️ **为什么默认 300 秒（5 分钟）**
#:    审批从提交到通过通常几分钟到几十分钟。5 分钟的延迟用户感知不到，
#:    但能把轮询频控压到很低（12 次/小时/实例）。
DEFAULT_STALE_SECONDS = 300

#: 单轮最多补偿多少个实例。
#:
#: ⚠️ **必须有上限** —— 否则一次性积压了几百个待审批实例时，
#:    一轮全部拉完会触发飞书频控，且这一轮会跑很久
#:    （下一轮又被推迟，形成恶性循环）。
#:    ⇒ 剩下的分到下一轮。
DEFAULT_MAX_PER_RUN = 50


@dataclass
class CompensationResult:
    """一轮补偿的结果。"""

    scanned: int = 0
    compensated: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "compensated": self.compensated,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": list(self.errors),
        }


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def find_stale_bindings(
    db: Session,
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    limit: int = DEFAULT_MAX_PER_RUN,
    now: datetime | None = None,
) -> list[PlanFeishuBinding]:
    """找出**需要补偿**的绑定行。

    判据（三条**同时**满足）：

    1. ``instance_code`` 非空 —— 没有飞书实例就无从拉起；
    2. ``instance_status`` 不是终态 —— 已终态的不用再管；
    3. ``updated_at`` 早于「现在 - 陈旧阈值」—— 刚更新过的别重复拉。

    ⚠️ **为什么用 ``updated_at`` 而不是 ``created_at``**
       ``updated_at`` 带 ``onupdate`` —— 每次回流更新绑定状态时它会跟着走。
       ⇒ 一个实例被补偿成功后，``updated_at`` 前移 ⇒ 下一轮不再重复拉。
       ⇒ 这正是「陈旧阈值」能自愈的原因。
    """
    reference = now or _now()
    cutoff = reference - timedelta(seconds=max(0, stale_seconds))

    stmt = select(PlanFeishuBinding).where(
        PlanFeishuBinding.instance_code.is_not(None),
        PlanFeishuBinding.instance_code != "",
        PlanFeishuBinding.updated_at <= cutoff,
    )
    # ⚠️ **先取陈旧的，再过滤终态** —— 终态过滤放到 Python 侧，
    #    因为不同飞书审批模板可能有非标准终态字符串，
    #    在 SQL 里写死 `NOT IN (...)` 反而容易漏。
    rows = list(db.execute(stmt.order_by(PlanFeishuBinding.updated_at.asc())).scalars().all())

    result: list[PlanFeishuBinding] = []
    for row in rows:
        status = (row.instance_status or "").upper()
        if status in TERMINAL_STATUSES:
            continue
        result.append(row)
        if len(result) >= max(1, limit):
            break
    return result


def compensate_once(
    db: Session,
    *,
    client: Any,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    limit: int = DEFAULT_MAX_PER_RUN,
) -> CompensationResult:
    """跑一轮补偿。

    :param client: 提供 ``fetch_approval_instance(instance_id)`` 的飞书客户端。
    :returns: 本轮统计。

    ⚠️ **单个实例失败不影响其他实例** —— 一个坏实例不该拖垮整轮。
    """
    from . import feishu_approval_sync

    result = CompensationResult()
    bindings = find_stale_bindings(
        db, stale_seconds=stale_seconds, limit=limit
    )
    result.scanned = len(bindings)
    if not bindings:
        return result

    for binding in bindings:
        instance_code = (binding.instance_code or "").strip()
        if not instance_code:
            result.skipped += 1
            continue
        try:
            outcome, detail = feishu_approval_sync.sync_feishu_approval_instance(
                db,
                plan_type=binding.plan_type,
                plan_id=binding.plan_id,
                instance_id=instance_code,
                client=client,
            )
        except Exception as exc:  # noqa: BLE001 - 单个失败必须隔离
            # ⚠️ **必须捕获所有异常** —— 否则一个实例失败会中断整轮，
            #    后面所有实例都得不到补偿。
            result.failed += 1
            message = f"{binding.plan_type}/{binding.plan_id}: {exc}"
            result.errors.append(message)
            logger.warning(
                "飞书审批补偿失败：plan=%s/%s instance=%s err=%s",
                binding.plan_type,
                binding.plan_id,
                instance_code,
                exc,
            )
            # ⚠️ **回滚这一条的改动** —— 不能让一条失败污染整个事务。
            db.rollback()
            continue

        result.compensated += 1
        status = str(detail.get("status") or "")
        if status in TERMINAL_STATUSES:
            result.updated += 1
        else:
            result.skipped += 1

    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 提交失败不能炸掉调度器
        db.rollback()
        result.failed += 1
        result.errors.append(f"提交失败: {exc}")
        logger.warning("飞书审批补偿提交失败", exc_info=True)

    logger.info(
        "飞书审批补偿完成：扫描=%s 补偿=%s 更新=%s 跳过=%s 失败=%s",
        result.scanned,
        result.compensated,
        result.updated,
        result.skipped,
        result.failed,
    )
    return result


__all__ = [
    "CompensationResult",
    "DEFAULT_MAX_PER_RUN",
    "DEFAULT_STALE_SECONDS",
    "TERMINAL_STATUSES",
    "compensate_once",
    "find_stale_bindings",
]
