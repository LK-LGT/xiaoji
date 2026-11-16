"""工作日规则服务 —— 日历口径的唯一真值（A5）。

定位
----
把「工作日规则」从硬编码假数据升级为**可配置、可版本化、可消费**的领域能力。

核心能力
--------
1. **CRUD + 版本化**：同一「日期 + 类型 + 归属」保存调整时**新增一版**，
   ``version_no`` 递增，旧版本保留可追溯。
2. **规则解析**（``resolve_rule``）：给定日期 + 员工，返回该天生效的规则。
   优先级：**个人规则 > 全局规则 > 默认推导**。
3. **日历生成**（``build_calendar``）：批量生成一段日期的日历，
   对齐前端 ``EmployeeWorkdayCalendarItem`` 契约。
4. **默认规则推导**：没有显式配置的日期，按「周一~周五 = WORKDAY、
   周六周日 = WEEKEND」推导。落库反而要维护成千上万行。

⚠️ **个人规则优先级**：张三当天请假，应该覆盖全局的「工作日」规则；
但张三当天**没有**个人规则时，仍然按全局 / 默认推导。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.workday_models import (
    PERSONAL_RULE_TYPES,
    RULE_STATUS_DISABLED,
    RULE_STATUS_ENABLED,
    RULE_TYPE_BUSINESS_TRIP,
    RULE_TYPE_HOLIDAY,
    RULE_TYPE_LEAVE,
    RULE_TYPE_SPECIAL_SHIFT,
    RULE_TYPE_WEEKEND,
    RULE_TYPE_WORKDAY,
    RULE_TYPES,
    WorkdayRule,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# 领域异常
# ===========================================================================


class WorkdayRuleError(Exception):
    """工作日规则相关错误。"""

    def __init__(self, message: str, *, code: str = "WORKDAY_RULE_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class WorkdayRuleNotFound(WorkdayRuleError):
    """规则不存在。"""

    def __init__(self, message: str = "工作日规则不存在") -> None:
        super().__init__(message, code="WORKDAY_RULE_NOT_FOUND")


class WorkdayRuleConflict(WorkdayRuleError):
    """规则冲突（日期 / 类型 / 归属非法，或版本号冲突）。"""

    def __init__(self, message: str = "工作日规则冲突") -> None:
        super().__init__(message, code="WORKDAY_RULE_CONFLICT")


# ===========================================================================
# 内部工具
# ===========================================================================


def _now() -> datetime:
    """当前 UTC 时间（naive），与 `services.planning._now` 同口径。"""
    return datetime.now().utcnow()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _date_str(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _as_int(value: Any) -> int:
    """任意主键 → int（前端 TS 契约用 number）。失败回退 0。"""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _parse_date(value: Any) -> date:
    """解析 `YYYY-MM-DD`。非法抛 `WorkdayRuleConflict`。"""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise WorkdayRuleConflict(f"日期格式非法，应为 YYYY-MM-DD：{value}") from exc


def _parse_month(value: Any) -> tuple[date, date]:
    """把 `YYYY-MM` 解析成该月的首尾日期（含首尾）。"""
    text = str(value or "").strip()[:7]
    try:
        year, month = int(text[:4]), int(text[5:7])
    except (ValueError, IndexError) as exc:
        raise WorkdayRuleConflict(f"月份格式非法，应为 YYYY-MM：{value}") from exc
    if not 1 <= month <= 12:
        raise WorkdayRuleConflict(f"月份非法：{value}")
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _validate_rule_type(rule_type: str) -> str:
    text = str(rule_type or "").strip().upper()
    if text not in RULE_TYPES:
        raise WorkdayRuleConflict(f"非法的规则类型：{rule_type}")
    return text


def _validate_scope(rule_type: str, employee_id: str | None) -> None:
    """个人规则类型必须绑定员工；全局类型不应绑员工。"""
    if rule_type in PERSONAL_RULE_TYPES and not employee_id:
        raise WorkdayRuleConflict(f"{rule_type} 类型必须指定员工")
    if rule_type not in PERSONAL_RULE_TYPES and employee_id:
        raise WorkdayRuleConflict(f"{rule_type} 是全局规则，不能指定员工")


# ===========================================================================
# 默认规则推导
# ===========================================================================


def default_rule_for(day: date) -> dict[str, Any]:
    """没有显式配置时的默认规则。

    周一~周五 = 工作日（强制填报），周六周日 = 周末（不强制）。
    """
    is_weekend = day.weekday() >= 5
    if is_weekend:
        return {
            "ruleType": RULE_TYPE_WEEKEND,
            "forceReport": False,
            "description": "",
            "explicit": False,
            "ruleId": None,
            "versionNo": None,
        }
    return {
        "ruleType": RULE_TYPE_WORKDAY,
        "forceReport": True,
        "description": "",
        "explicit": False,
        "ruleId": None,
        "versionNo": None,
    }


# ===========================================================================
# 查询
# ===========================================================================


def _current_rules(
    db: Session,
    *,
    rule_date: date | None = None,
    start: date | None = None,
    end: date | None = None,
    rule_type: str | None = None,
    employee_id: str | None = None,
    status: int | None = RULE_STATUS_ENABLED,
) -> list[WorkdayRule]:
    """按条件取规则（含历史版本）。"""
    stmt = select(WorkdayRule)
    if rule_date is not None:
        stmt = stmt.where(WorkdayRule.rule_date == rule_date)
    if start is not None:
        stmt = stmt.where(WorkdayRule.rule_date >= start)
    if end is not None:
        stmt = stmt.where(WorkdayRule.rule_date <= end)
    if rule_type:
        stmt = stmt.where(WorkdayRule.rule_type == rule_type)
    if employee_id is not None:
        stmt = stmt.where(WorkdayRule.employee_id == employee_id)
    if status is not None:
        stmt = stmt.where(WorkdayRule.status == status)
    stmt = stmt.order_by(
        WorkdayRule.rule_date,
        WorkdayRule.rule_type,
        WorkdayRule.employee_id,
        WorkdayRule.version_no.desc(),
    )
    return list(db.execute(stmt).scalars().all())


def _latest_by_key(rules: Sequence[WorkdayRule]) -> dict[tuple, WorkdayRule]:
    """按 (日期, 类型, 归属) 取版本号最大的一条。"""
    latest: dict[tuple, WorkdayRule] = {}
    for rule in rules:
        key = (rule.rule_date, rule.rule_type, rule.employee_id or "")
        current = latest.get(key)
        if current is None or rule.version_no > current.version_no:
            latest[key] = rule
    return latest


def list_rules(
    db: Session,
    *,
    month: str | None = None,
    rule_type: str | None = None,
    status: int | None = None,
    employee_id: str | None = None,
    scope: str = "global",
) -> list[WorkdayRule]:
    """列出规则（只返回每个 key 的当前生效版本）。

    ``scope``：
    - ``global`` —— 只看全局规则（``employee_id IS NULL``）
    - ``employee`` —— 只看某个员工的个人规则
    - ``all`` —— 全部
    """
    start = end = None
    if month:
        start, end = _parse_month(month)

    stmt = select(WorkdayRule)
    if start is not None:
        stmt = stmt.where(WorkdayRule.rule_date >= start)
        stmt = stmt.where(WorkdayRule.rule_date <= end)
    if rule_type:
        stmt = stmt.where(WorkdayRule.rule_type == _validate_rule_type(rule_type))
    if status is not None:
        stmt = stmt.where(WorkdayRule.status == status)

    if scope == "global":
        stmt = stmt.where(WorkdayRule.employee_id.is_(None))
    elif scope == "employee":
        stmt = stmt.where(WorkdayRule.employee_id == (employee_id or ""))

    stmt = stmt.order_by(
        WorkdayRule.rule_date,
        WorkdayRule.rule_type,
        WorkdayRule.employee_id,
        WorkdayRule.version_no.desc(),
    )
    rows = list(db.execute(stmt).scalars().all())
    return list(_latest_by_key(rows).values())


def fetch_rule(db: Session, rule_id: int | str) -> WorkdayRule:
    """按 id 取规则。"""
    rule = db.get(WorkdayRule, _as_int(rule_id))
    if rule is None:
        raise WorkdayRuleNotFound()
    return rule


# ===========================================================================
# 写入（版本化）
# ===========================================================================


def _next_version_no(
    db: Session,
    rule_date: date,
    rule_type: str,
    employee_id: str | None,
) -> int:
    """同一「日期 + 类型 + 归属」下的下一版版本号。"""
    stmt = select(WorkdayRule.version_no).where(
        WorkdayRule.rule_date == rule_date,
        WorkdayRule.rule_type == rule_type,
    )
    if employee_id is None:
        stmt = stmt.where(WorkdayRule.employee_id.is_(None))
    else:
        stmt = stmt.where(WorkdayRule.employee_id == employee_id)
    versions = list(db.execute(stmt).scalars().all())
    return (max(versions) + 1) if versions else 1


def create_rule(
    db: Session,
    *,
    rule_date: Any,
    rule_type: str,
    force_report: bool = False,
    description: str = "",
    status: int = RULE_STATUS_ENABLED,
    employee_id: str | None = None,
) -> WorkdayRule:
    """创建一条工作日规则（首次创建，版本号 1）。

    同「日期 + 类型 + 归属」已存在规则时，**不覆盖** —— 提示走更新接口。
    """
    day = _parse_date(rule_date)
    rtype = _validate_rule_type(rule_type)
    scope_id = str(employee_id) if employee_id else None
    _validate_scope(rtype, scope_id)

    existing = _latest_by_key(
        _current_rules(
            db,
            rule_date=day,
            rule_type=rtype,
            employee_id=scope_id,
            status=None,
        )
    )
    if (day, rtype, scope_id or "") in existing:
        raise WorkdayRuleConflict("该日期与类型已存在规则，请使用编辑功能生成新版本")

    rule = WorkdayRule(
        rule_date=day,
        rule_type=rtype,
        employee_id=scope_id,
        force_report=bool(force_report),
        description=description or "",
        status=int(status),
        version_no=_next_version_no(db, day, rtype, scope_id),
    )
    db.add(rule)
    db.flush()
    return rule


def update_rule(
    db: Session,
    rule_id: int | str,
    *,
    rule_date: Any | None = None,
    rule_type: str | None = None,
    force_report: bool | None = None,
    description: str | None = None,
    status: int | None = None,
    employee_id: str | None = None,
) -> WorkdayRule:
    """更新工作日规则 —— **生成新版本**，历史版本不被覆盖。

    与 A4 的 ``result`` 表不同：这里的版本化是**同一张表里新增一行**，
    旧行保留（``status`` 保持原值）。查询时取版本号最大的那条。
    """
    current = fetch_rule(db, rule_id)

    day = _parse_date(rule_date) if rule_date is not None else current.rule_date
    rtype = (
        _validate_rule_type(rule_type) if rule_type is not None else current.rule_type
    )
    scope_id = (
        (str(employee_id) if employee_id else None)
        if employee_id is not None
        else current.employee_id
    )
    _validate_scope(rtype, scope_id)

    # 旧版本停用（保留可追溯，但不再生效）
    current.status = RULE_STATUS_DISABLED
    db.flush()

    new_rule = WorkdayRule(
        rule_date=day,
        rule_type=rtype,
        employee_id=scope_id,
        force_report=(
            bool(force_report) if force_report is not None else current.force_report
        ),
        description=(
            description if description is not None else current.description
        ),
        status=int(status) if status is not None else RULE_STATUS_ENABLED,
        version_no=_next_version_no(db, day, rtype, scope_id),
    )
    db.add(new_rule)
    db.flush()
    return new_rule


def change_status(db: Session, rule_id: int | str, status: int) -> WorkdayRule:
    """启用 / 停用规则（不改版本号 —— 这是开关，不是内容调整）。"""
    rule = fetch_rule(db, rule_id)
    rule.status = int(status)
    db.flush()
    return rule


def delete_rule(db: Session, rule_id: int | str) -> None:
    """删除规则（连同该 key 的全部历史版本）。"""
    rule = fetch_rule(db, rule_id)
    key = (rule.rule_date, rule.rule_type, rule.employee_id or "")
    stmt = select(WorkdayRule).where(
        WorkdayRule.rule_date == key[0],
        WorkdayRule.rule_type == key[1],
    )
    if key[2]:
        stmt = stmt.where(WorkdayRule.employee_id == key[2])
    else:
        stmt = stmt.where(WorkdayRule.employee_id.is_(None))
    for row in db.execute(stmt).scalars().all():
        db.delete(row)
    db.flush()


# ===========================================================================
# 规则解析（日历口径）
# ===========================================================================


def resolve_rule(
    db: Session,
    day: date,
    employee_id: str | None = None,
) -> dict[str, Any]:
    """解析某天对某员工生效的日历规则。

    优先级：**个人规则 > 全局规则 > 默认推导**。

    返回结构对齐前端 ``EmployeeWorkdayCalendarItem``：
    ``{date, ruleType, forceReport, description, ruleId, versionNo, explicit, missingRequired}``

    ⚠️ ``missingRequired`` 需要「当天有没有填日计划」才能算，
    此处只填 **False**；由调用方（dashboard / day-plan detail）结合
    日计划数据补齐。
    """
    rules = _current_rules(
        db,
        rule_date=day,
        status=RULE_STATUS_ENABLED,
    )
    latest = _latest_by_key(rules)

    # 1) 个人规则优先
    if employee_id:
        personal = latest.get((day, RULE_TYPE_LEAVE, employee_id))
        if personal is None:
            personal = latest.get((day, RULE_TYPE_BUSINESS_TRIP, employee_id))
        if personal is not None:
            return _to_calendar_item(day, personal, explicit=True)

    # 2) 全局规则：按"特殊 > 节假日 > 周末 > 工作日"的语义优先级
    global_priority = (
        RULE_TYPE_SPECIAL_SHIFT,
        RULE_TYPE_HOLIDAY,
        RULE_TYPE_WEEKEND,
        RULE_TYPE_WORKDAY,
    )
    for rtype in global_priority:
        rule = latest.get((day, rtype, ""))
        if rule is not None:
            return _to_calendar_item(day, rule, explicit=True)

    # 3) 默认推导
    item = default_rule_for(day)
    item["date"] = day.isoformat()
    item["missingRequired"] = False
    return item


def _to_calendar_item(
    day: date, rule: WorkdayRule, *, explicit: bool
) -> dict[str, Any]:
    """规则行 → 日历项。"""
    return {
        "date": day.isoformat(),
        "ruleType": rule.rule_type,
        "forceReport": bool(rule.force_report),
        "description": rule.description or "",
        "ruleId": _as_int(rule.id),
        "versionNo": rule.version_no,
        "explicit": explicit,
        "missingRequired": False,
    }


def build_calendar(
    db: Session,
    start: date,
    end: date,
    employee_id: str | None = None,
) -> list[dict[str, Any]]:
    """生成一段日期区间的日历（对齐前端 ``EmployeeWorkdayCalendarItem[]``）。

    为避免逐日查库，这里一次性把区间内的规则全查出来再在内存里归并。
    """
    if end < start:
        start, end = end, start

    rules = _current_rules(
        db,
        start=start,
        end=end,
        status=RULE_STATUS_ENABLED,
    )
    latest = _latest_by_key(rules)

    # 按 (日期, 类型, 归属) 建索引，便于逐日查
    calendar: list[dict[str, Any]] = []
    day = start
    while day <= end:
        item = _resolve_from_index(day, employee_id, latest)
        calendar.append(item)
        day += timedelta(days=1)
    return calendar


def _resolve_from_index(
    day: date,
    employee_id: str | None,
    latest: dict[tuple, WorkdayRule],
) -> dict[str, Any]:
    """用预取的规则索引解析某天（避免 N+1 查询）。"""
    if employee_id:
        for rtype in (RULE_TYPE_LEAVE, RULE_TYPE_BUSINESS_TRIP):
            rule = latest.get((day, rtype, employee_id))
            if rule is not None:
                return _to_calendar_item(day, rule, explicit=True)

    for rtype in (
        RULE_TYPE_SPECIAL_SHIFT,
        RULE_TYPE_HOLIDAY,
        RULE_TYPE_WEEKEND,
        RULE_TYPE_WORKDAY,
    ):
        rule = latest.get((day, rtype, ""))
        if rule is not None:
            return _to_calendar_item(day, rule, explicit=True)

    item = default_rule_for(day)
    item["date"] = day.isoformat()
    item["missingRequired"] = False
    return item


def count_missing_required(
    db: Session,
    start: date,
    end: date,
    employee_id: str,
    existing_plan_dates: set[date],
) -> int:
    """统计「应填未填」的强制填报日数量。

    ``existing_plan_dates`` —— 员工已有的日计划日期集合。
    """
    missing = 0
    for item in build_calendar(db, start, end, employee_id):
        if not item["forceReport"]:
            continue
        day = date.fromisoformat(item["date"])
        if day not in existing_plan_dates:
            missing += 1
    return missing


# ===========================================================================
# 序列化
# ===========================================================================


def serialize_rule(rule: WorkdayRule) -> dict[str, Any]:
    """`WorkdayRule`（前端 `frontend/src/api/system.ts`）。"""
    return {
        "id": _as_int(rule.id),
        "ruleDate": _date_str(rule.rule_date) or "",
        "ruleType": rule.rule_type,
        "forceReport": bool(rule.force_report),
        "description": rule.description or "",
        "status": rule.status,
        "versionNo": rule.version_no,
        "updatedAt": _iso(rule.updated_at) or "",
    }
