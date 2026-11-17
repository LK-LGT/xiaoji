"""飞书机器人会话域的 SQLAlchemy 模型（FS-3）。

设计要点
--------
1. **会话（``bot_session``）是状态的唯一载体** —— 机器人与员工的对话上下文
   （当前意图、已填字段、关联的计划草稿）全部落在这里。
   为什么落库而不是放内存：飞书的对话是**异步**的（员工发消息 → 机器人回卡片 →
   员工点卡片 → 机器人收到回调），中间可能隔几分钟，也可能跨进程。
   放内存的话，**进程一重启会话就丢了**，员工看到的就是「我明明填了一半」。

2. **会话与员工通过 ``open_id`` 绑定，但不存飞书 token** ——
   只存 ``open_id`` / ``employee_id`` 这类**标识**，不存任何凭证。

3. **卡片动作只写草稿，不改状态**（Runbook §4.2 约束 C4）——
   机器人填表写的是 ``month_plan`` 的**草稿**，
   提交仍然走 L2 正常状态机（``planning.submit_month_plan``）。
   机器人的存在**不绕过**任何审批与状态流转规则。

4. **幂等靠 ``dedup_key`` 唯一约束** —— 飞书会重推事件（网络抖动、我们返回慢），
   同一 ``event_id`` / ``card_action_id`` 处理两次就会产生两条草稿。
   唯一约束在**数据库层**挡住，而不是靠应用层先查后写（那有竞态）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class BotSession(Base):
    """机器人会话。一个员工在一个飞书应用下有**一条活跃会话**。

    状态机（``state``）::

        idle ──"填月计划"──> collecting ──卡片提交──> completed
          ▲                    │
          └────「取消」/ 超时 ─┘

    ⚠️ ``state`` 只表达**流程阶段**，不表达业务状态 ——
    业务状态永远以 ``month_plan.status`` 为准。
    """

    __tablename__ = "bot_session"
    __table_args__ = (
        # 同一员工 + 同一应用只能有一条活跃会话
        UniqueConstraint("open_id", "app_id", name="uq_bot_session_open_id_app"),
        Index("ix_bot_session_state", "state"),
        Index("ix_bot_session_employee", "employee_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: 飞书 open_id（app 维度）—— 用来定位「谁在跟机器人说话」
    open_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    #: 飞书 user_id —— 机器人回消息时要用（open_id 也行，user_id 更稳）
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 飞书应用 ID —— 同一用户在不同应用下 open_id 不同，必须带 app 维度
    app_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 本系统员工 ID —— 解析不到员工时为空（机器人会提示先绑定）
    employee_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=True
    )

    #: 会话状态：idle / collecting / completed
    state: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    #: 当前意图（month_plan / week_plan / day_plan / none）
    intent: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    #: 目标月份（YYYY-MM）
    plan_month: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: 关联的计划草稿 ID —— 卡片提交后写入草稿，回执里给「去提交」链接
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 收集到的字段（JSON）—— 卡片交互过程中逐步累积
    collected: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: 最近一条飞书消息 ID —— 幂等去重用
    last_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, nullable=False)
    #: 会话过期时间 —— 超过就该重开（避免半年前的会话被唤醒）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    employee = relationship("Employee")


class BotMessage(Base):
    """机器人消息流水（收发两侧都记）。

    为什么要有这张表
    ----------------
    - **排障**：员工说「机器人没回我」，得能看到到底发出去没有；
    - **幂等**：飞书重推同一个事件时，靠 ``dedup_key`` 挡住重复处理；
    - **审计**：谁在什么时候让机器人做了什么。

    ⚠️ **只存载荷摘要，不存飞书 token**。
    """

    __tablename__ = "bot_message"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_bot_message_dedup_key"),
        Index("ix_bot_message_session", "session_id"),
        Index("ix_bot_message_direction", "direction"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    session_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("bot_session.id"), nullable=True
    )
    #: 方向：inbound（员工→机器人）/ outbound（机器人→员工）
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 消息类型：text / card / card_action / system
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    #: 幂等键（飞书 event_id / message_id / card action token）
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    #: 载荷摘要（JSON）—— 已脱敏，不含任何 token
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: 处理结果：ok / ignored / failed
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


__all__ = ["BotMessage", "BotSession"]
