"""飞书机器人会话服务（FS-3）。

职责
----
1. **意图路由** —— 员工发一条消息，机器人判断他想干什么（填月计划？查状态？）。
2. **会话管理** —— 创建 / 推进 / 结束会话（落库，见 ``domain.bot_models``）。
3. **写草稿** —— 卡片回调里把填好的内容写成 ``month_plan`` **草稿**。

三条硬约束（Runbook §4.2）
--------------------------
**C3 卡片回调必须校验 session 归属**
    ``open_id`` 必须与 session 记录一致。否则 A 的卡片回调能被 B 触发 ——
    B 点了「保存草稿」，草稿却写进了 A 的名下。这不是理论风险：
    卡片是可以转发的。

**C4 只写草稿，不改状态**
    机器人的全部写操作都落在**草稿**上。提交仍然走 L2 正常状态机
    （``planning.submit_month_plan``）。机器人**不绕过**任何审批规则。

**C5 测试 fixture 用飞书官方原始报文**
    见 ``tests/test_feishu_bot.py`` 的 fixture —— 不要照着实现自造 payload。

幂等
----
飞书会重推事件。本模块靠 ``bot_message.dedup_key`` 的**唯一约束**挡住重复处理，
而不是靠「先查后写」（那有竞态）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.bot_models import BotMessage, BotSession
from ..domain.models import Employee, MonthPlan, MonthPlanItem
from ..services import feishu_card
from ..services.feishu_client import FeishuClient
from ..services.planning import PlanStateError, normalize_month, upsert_month_plan_draft

logger = logging.getLogger(__name__)


class FeishuBotError(Exception):
    """机器人业务异常。"""

    def __init__(self, message: str, *, code: str = "FEISHU_BOT_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ===========================================================================
# 常量
# ===========================================================================

#: 会话状态
STATE_IDLE = "idle"
STATE_COLLECTING = "collecting"
STATE_COMPLETED = "completed"

# ⚠️ 意图识别已抽到 ``feishu_intent`` 模块（分层路由 + 领域词库）。
#    这里保留别名是为了**不破坏**既有 import —— 几十处调用方用
#    ``feishu_bot.INTENT_NONE`` / ``feishu_bot.detect_intent``。
#
# ⚠️⚠️ **块级 noqa 只作用于整块**
#   把「E402,F401」写在 ``from ... import (`` 那行，
#   **会同时抑制整块的 F401** ——
#   所以这里不需要给每个名字单独加 noqa。
#   （曾经被删过两次：``INTENT_CANCEL`` / ``INTENT_PERFORMANCE_QA``
#    是**公开 API**，路由层要用。⇒ 绝不能让 ruff --fix 碰这一块。）
from .feishu_intent import (  # noqa: E402,F401
    CANCEL_KEYWORDS,
    INTENT_CANCEL,
    INTENT_MONTH_PLAN,
    INTENT_NONE,
    INTENT_PERFORMANCE_QA,
    MONTH_PLAN_KEYWORDS,
    PERFORMANCE_KEYWORDS,
    detect_intent,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _session_ttl() -> timedelta:
    minutes = max(1, int(settings.feishu_bot_session_ttl_minutes or 120))
    return timedelta(minutes=minutes)


# ===========================================================================
# 意图识别
# ===========================================================================


def extract_plan_month(text: str) -> str:
    """从消息里提取月份（YYYY-MM）。

    支持「2026-11」「2026年11月」「11月」三种写法。
    提取不到时返回空串（由调用方用当前月兜底）。
    """
    import re

    if not text:
        return ""

    # 2026-11 或 2026/11
    match = re.search(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})", text)
    if match:
        return normalize_month(f"{match.group(1)}-{int(match.group(2)):02d}")

    # 11月（不带年份）—— 用当前年
    match = re.search(r"(?<!\d)(\d{1,2})\s*月", text)
    if match:
        month = int(match.group(1))
        if 1 <= month <= 12:
            return normalize_month(f"{datetime.now(UTC).year}-{month:02d}")

    return ""


# ===========================================================================
# 会话管理
# ===========================================================================


def get_or_create_session(
    db: Session,
    *,
    open_id: str,
    app_id: str = "",
    user_id: str = "",
) -> BotSession:
    """按 ``open_id`` 取会话，没有就建一条。

    ⚠️ **并发下可能抛 IntegrityError** —— 两个事件同时到达时，
    两条都查不到 → 都想插入 → 一条被唯一约束挡住。
    捕获后重新查询即可（见调用方）。
    """
    session = db.execute(
        select(BotSession).where(
            BotSession.open_id == open_id,
            BotSession.app_id == app_id,
        )
    ).scalar_one_or_none()
    if session is not None:
        return session

    session = BotSession(
        open_id=open_id,
        app_id=app_id,
        user_id=user_id or None,
        state=STATE_IDLE,
        intent=INTENT_NONE,
        collected={},
        expires_at=_now() + _session_ttl(),
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(BotSession).where(
                BotSession.open_id == open_id,
                BotSession.app_id == app_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    return session


def bind_employee(db: Session, session: BotSession, employee: Employee) -> None:
    """把会话绑定到本系统员工。"""
    session.employee_id = employee.id
    session.updated_at = _now()
    db.flush()


def is_session_expired(session: BotSession) -> bool:
    """会话是否过期。"""
    if session.expires_at is None:
        return False
    return session.expires_at < _now()


# ===========================================================================
# 幂等
# ===========================================================================


def is_duplicate(db: Session, dedup_key: str) -> bool:
    """该事件是否已处理过。

    ⚠️ 这是**查询**（快路径），真正的防线在 ``record_message`` 的
    唯一约束上 —— 应用层先查后写有竞态，两条并发事件都能查到「不存在」。
    """
    if not dedup_key:
        return False
    row = db.execute(
        select(BotMessage.id).where(BotMessage.dedup_key == dedup_key)
    ).first()
    return row is not None


def record_message(
    db: Session,
    *,
    dedup_key: str,
    direction: str,
    message_type: str = "text",
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
    status: str = "ok",
    error_message: str | None = None,
) -> bool:
    """记一条消息流水。

    Returns:
        True 表示写入成功；False 表示**已存在**（重复事件）。
    """
    if not dedup_key:
        return True
    existing = db.execute(
        select(BotMessage.id).where(BotMessage.dedup_key == dedup_key)
    ).first()
    if existing is not None:
        return False

    row = BotMessage(
        session_id=session_id,
        direction=direction,
        message_type=message_type,
        dedup_key=dedup_key,
        payload=payload,
        status=status,
        error_message=error_message,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    return True


# ===========================================================================
# 写草稿（约束 C4 —— 只写草稿，不改状态）
# ===========================================================================


def apply_card_form_to_draft(
    db: Session,
    session: BotSession,
    form_values: dict[str, Any],
) -> dict[str, Any]:
    """把卡片表单值写成月计划**草稿**。

    ⚠️ **绝不改业务状态** —— 这里只调 ``upsert_month_plan_draft``，
    它保持 ``status = "draft"``。提交由 L2 状态机负责。

    Args:
        db: 数据库会话。
        session: 机器人会话。
        form_values: 卡片回调里带回的表单值。

    Returns:
        ``{"plan_id": ..., "plan_month": ..., "item_count": ...}``。
    """
    if session.employee_id is None:
        raise FeishuBotError("会话未绑定员工，无法写入草稿", code="NO_EMPLOYEE")

    plan_month = normalize_month(session.plan_month or _current_month())
    # A completed card is single-use even if Feishu assigns a new event ID.
    if session.state == STATE_COMPLETED and session.plan_id:
        return {"plan_id": session.plan_id, "plan_month": plan_month, "item_count": 1}
    if session.expires_at:
        expires = session.expires_at.replace(tzinfo=UTC) if session.expires_at.tzinfo is None else session.expires_at
        if expires <= datetime.now(UTC):
            raise FeishuBotError("卡片已过期，请重新发送填月计划", code="SESSION_EXPIRED")
    values = _normalize_form_values(form_values)

    task_name = values.get("task_name", "")
    if not task_name:
        raise FeishuBotError("任务名称不能为空", code="EMPTY_TASK_NAME")

    payload = {
        "planMonth": plan_month,
        "title": values.get("title") or task_name,
        "items": [
            {
                "taskType": "REGULAR",
                "taskName": task_name,
                "taskContent": values.get("task_content", ""),
                "deliverable": values.get("deliverable", ""),
                "quality": values.get("quality", ""),
                "performanceWeight": values.get("performance_weight", 0.0),
                "estimatedValue": values.get("estimated_value", 0.0),
            }
        ],
    }

    existing_plan = db.scalar(select(MonthPlan).where(
        MonthPlan.owner_id == session.employee_id,
        MonthPlan.plan_month == plan_month,
    ))
    if existing_plan is not None:
        # The planning API replaces the complete item list. Preserve all fields
        # and IDs when appending the one task supplied by this card.
        field_map = {
            "taskType": "task_type", "taskName": "task_name",
            "taskContent": "task_content", "deliverable": "deliverable",
            "quality": "quality", "performanceWeight": "performance_weight",
            "estimatedValue": "estimated_value", "sortNo": "sort_no",
        }
        old_items = []
        for item in db.scalars(select(MonthPlanItem).where(MonthPlanItem.plan_id == existing_plan.id)):
            raw = {key: getattr(item, attr) for key, attr in field_map.items()}
            raw["id"] = item.id
            for key, attr in (("estimatedStartDate", "estimated_start_date"), ("estimatedEndDate", "estimated_end_date")):
                value = getattr(item, attr)
                raw[key] = value.isoformat() if value else None
            old_items.append(raw)
        payload["items"] = old_items + payload["items"]
        payload["title"] = existing_plan.title

    try:
        plan = upsert_month_plan_draft(db, session.employee_id, existing_plan.id if existing_plan else None, payload)
    except PlanStateError as exc:
        raise FeishuBotError(
            f"当前月计划状态（{exc.current}）不允许编辑，请先在系统里处理",
            code="PLAN_NOT_EDITABLE",
        ) from exc

    session.plan_id = plan.id
    session.plan_month = plan_month
    session.state = STATE_COMPLETED
    session.collected = values
    session.updated_at = _now()
    db.flush()

    return {
        "plan_id": plan.id,
        "plan_month": plan_month,
        "item_count": 1,
    }


def _normalize_form_values(form_values: dict[str, Any]) -> dict[str, Any]:
    """归一化卡片表单值。

    ⚠️ 三个官方文档里写明的形态陷阱：
    1. 飞书回传的表单值**全是字符串**（``"12.5"`` / ``"abc"``）；
    2. **多选组件回传的是数组**（``{"selectDemo1": ["a", "b"]}``）；
    3. 日期选择器回传的是 ``"2024-04-01 +0800"`` 这种带时区的串。

    字符串化的数组会变成 ``"['a', 'b']"`` —— 直接写进数据库就是脏数据，
    所以这里显式处理数组（取首项 / 拼成逗号分隔）。

    转不动的（``"abc"`` 当数字）→ 当 0，不抛异常 ——
    填表场景下不该因为一个字段格式不对就整单失败。
    """
    normalized: dict[str, Any] = {}
    for key, raw in (form_values or {}).items():
        value = raw
        if isinstance(value, list):
            # 多选组件：数组 → 逗号分隔字符串
            parts = [str(v).strip() for v in value if str(v).strip()]
            value = ",".join(parts)
        elif isinstance(value, str):
            value = value.strip()
        normalized[key] = value

    for numeric_key in ("performance_weight", "estimated_value"):
        value = normalized.get(numeric_key, 0)
        try:
            normalized[numeric_key] = float(value) if value not in (None, "") else 0.0
        except (TypeError, ValueError):
            normalized[numeric_key] = 0.0

    return normalized


def _current_month() -> str:
    return normalize_month(datetime.now(UTC).strftime("%Y-%m"))


# ===========================================================================
# 卡片发送
# ===========================================================================


def send_card(
    client: FeishuClient,
    *,
    receive_id: str,
    card: dict[str, Any],
    receive_id_type: str = "open_id",
) -> dict[str, Any]:
    """发卡片给用户。"""
    return client.send_card_message(
        receive_id,
        card,
        receive_id_type=receive_id_type,
    )


def build_month_plan_card_for_session(
    session: BotSession,
    *,
    plan_month: str,
) -> dict[str, Any]:
    """给会话构造「填月计划」表单卡片。"""
    card = feishu_card.build_month_plan_form_card(
        plan_month=plan_month,
        session_id=session.id,
        fields=session.collected or {},
    )
    def bind_generation(node):
        if isinstance(node, dict):
            value = node.get("value")
            if isinstance(value, dict) and value.get("session_id") == session.id:
                value["generation"] = session.last_message_id or ""
            for child in node.values():
                bind_generation(child)
        elif isinstance(node, list):
            for child in node:
                bind_generation(child)
    bind_generation(card)
    return card


__all__ = [
    "CANCEL_KEYWORDS",
    "FeishuBotError",
    "INTENT_MONTH_PLAN",
    "INTENT_NONE",
    "MONTH_PLAN_KEYWORDS",
    "STATE_COLLECTING",
    "STATE_COMPLETED",
    "STATE_IDLE",
    "apply_card_form_to_draft",
    "bind_employee",
    "build_month_plan_card_for_session",
    "detect_intent",
    "extract_plan_month",
    "get_or_create_session",
    "is_duplicate",
    "is_session_expired",
    "record_message",
    "send_card",
]
