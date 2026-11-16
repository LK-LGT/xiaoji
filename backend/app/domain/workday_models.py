"""工作日规则域模型 —— 日历口径的唯一真值。

背景
----
此前工作日规则**完全没有数据表**，`GET /system/workday-rules` 硬编码返回
1 条 `2024-01-01 元旦`，CRUD 端点返回的字段结构（``name``）还与前端契约
（``ruleDate`` / ``ruleType`` / ``forceReport``）**完全对不上**。

设计要点
--------
1. **全局规则 + 个人规则共用一张表**
   用 ``employee_id`` 区分：

   - ``employee_id IS NULL`` → **全局规则**（节假日、特殊排班）
   - ``employee_id = 'E_xxx'`` → **个人规则**（请假、出差）

   理由：两类规则的结构、版本化、生效逻辑完全一致，只有归属范围不同。
   分两张表会让查询与版本化逻辑翻倍。

2. **版本化，历史不被覆盖**
   同一 ``(rule_date, rule_type, employee_id)`` 保存调整时**新增一版**，
   ``version_no`` 递增，旧版本保留可追溯。
   前端 UI 明确提示："保存调整会生成新版本，历史规则不会被覆盖。"

   ⚠️ 与 A4 的 ``result`` 表不同：那里的"新版本"是**新行**；
   这里的版本化也要新行，但**需要能查到"当前生效的那一版"** ——
   同一 key 下 ``version_no`` 最大且 ``status=1`` 的那条。

3. **默认规则不落库**
   没有任何显式配置的日期，按「周一~周五 = WORKDAY，周六周日 = WEEKEND」
   推导（见 ``services/workday_rule.py``）。落库反而要维护成千上万行。
   前端契约里的 ``explicit`` 字段就是在表达"这天有没有显式配过"。

规则类型
--------
===========  ==========  ====================================
类型          范围        含义
===========  ==========  ====================================
WORKDAY      全局        工作日
WEEKEND      全局        周末
HOLIDAY      全局        法定节假日
SPECIAL_SHIFT 全局       特殊排班（调休上班、值班）
LEAVE        个人        请假
BUSINESS_TRIP 个人       出差
===========  ==========  ====================================
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Base, Employee, _now

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

RULE_TYPE_WORKDAY = "WORKDAY"
RULE_TYPE_WEEKEND = "WEEKEND"
RULE_TYPE_HOLIDAY = "HOLIDAY"
RULE_TYPE_LEAVE = "LEAVE"
RULE_TYPE_BUSINESS_TRIP = "BUSINESS_TRIP"
RULE_TYPE_SPECIAL_SHIFT = "SPECIAL_SHIFT"

#: 全部合法规则类型（对齐前端 `WorkdayRules.vue` 的 `ruleTypes`）。
RULE_TYPES: tuple[str, ...] = (
    RULE_TYPE_WORKDAY,
    RULE_TYPE_WEEKEND,
    RULE_TYPE_HOLIDAY,
    RULE_TYPE_LEAVE,
    RULE_TYPE_BUSINESS_TRIP,
    RULE_TYPE_SPECIAL_SHIFT,
)

#: 个人规则类型（必须绑定 `employee_id`）。
PERSONAL_RULE_TYPES: frozenset[str] = frozenset(
    {RULE_TYPE_LEAVE, RULE_TYPE_BUSINESS_TRIP}
)

#: 规则启用状态（前端用 number，不用布尔）。
RULE_STATUS_ENABLED = 1
RULE_STATUS_DISABLED = 0


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------


class WorkdayRule(Base):
    """工作日规则 —— 某一天的日历口径。

    一次记录 = "某天 / 某类型 / 某归属范围内" 的一条规则版本。

    示例：

    - ``2026-01-01 / HOLIDAY / (NULL)`` → 全局元旦，全员休息
    - ``2026-11-17 / LEAVE / E_A`` → 员工甲当天请假
    - ``2026-11-14 / SPECIAL_SHIFT / (NULL)`` → 调休上班，需强制填报

    ⚠️ **主键用整型自增**：前端契约 ``WorkdayRule.id: number``
    （`frontend/src/api/system.ts`），端点路径也按 ``/workday-rules/{id}``
    传数字。若用 UUID 字符串，前端拿到的 id 会在类型转换中变成 0，
    导致后续更新 / 删除全部 404。
    """

    __tablename__ = "workday_rule"
    __table_args__ = (
        # 同一「日期 + 类型 + 归属」下版本号唯一 —— 支撑版本化
        UniqueConstraint(
            "rule_date", "rule_type", "employee_id", "version_no",
            name="uq_workday_rule_date_type_employee_version",
        ),
        Index("ix_workday_rule_date", "rule_date"),
        Index("ix_workday_rule_employee", "employee_id", "rule_date"),
        Index("ix_workday_rule_status", "status", "rule_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 规则生效日期
    rule_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # 规则类型：WORKDAY / WEEKEND / HOLIDAY / LEAVE / BUSINESS_TRIP / SPECIAL_SHIFT
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # 归属范围：NULL = 全局规则；非空 = 该员工的个人规则
    employee_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=True, index=True
    )

    # 是否强制填报日计划。调休上班的周末应为 True；节假日通常为 False。
    force_report: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # 启用状态：1 启用 / 0 停用（停用的规则不参与计算）
    status: Mapped[int] = mapped_column(
        Integer, default=RULE_STATUS_ENABLED, nullable=False, server_default="1"
    )

    # 版本号：同一「日期 + 类型 + 归属」下递增，历史版本保留
    version_no: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default="1"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )

    employee: Mapped[Employee | None] = relationship()
