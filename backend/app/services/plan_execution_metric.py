"""计划执行率指标 + 计划统计 —— A7。

背景
----
A6 打通的是「成果 → 绩效证据」的**证据链**；A7 要补的是**指标链**：
把「员工实际干得怎么样」变成算分引擎能直接消费的 ``MetricValue``。

两个交付物
----------
1. **计划执行率指标**（``metric_code = "plan_execution_rate"``）
   —— 员工月度计划执行率，**按条目绩效权重加权**，写入算分集。
2. **计划统计服务**（``GET /planning/stats``）
   —— 把原来恒返回四个 0 的硬编码端点接上真实统计。

口径决策（2026-11-16 确认）
--------------------------
| 决策点 | 口径 |
|---|---|
| 计算时机 | **审批通过时自动重算并写入**（与 A6 物化同点位） |
| 计算公式 | **按条目绩效权重加权**：Σ(完成率×权重)/Σ权重；权重缺失时退化为简单平均 |
| 周期粒度 | **按月（YYYY-MM）** —— 与 A6 的证据周期、D6 决策一致 |
| 统计范围 | **按数据范围（ACL）**：HR/管理员看全公司，部门负责人看管理范围，主管看直属下属，员工看自己 |

为什么公式选「加权」而不是「简单平均」
------------------------------------
月计划条目带 ``performance_weight``（绩效权重，A1 交付时就存在）——
它就是「这条任务值多少」。简单平均会让「一条占 60% 权重的核心任务只完成 30%」
和「十条边角料全部 100%」得到同样的分数，明显违背直觉。

**退化规则**：所有条目的权重都是 0（没填）时，退化为**简单平均** ——
宁可给一个粗糙但可解释的数，也不要因为除零给出 0 分（0 分会被规则引擎
当成「完全没干活」，是错误信号）。

只统计**已审批通过**的成果
------------------------
草稿 / 待审 / 被驳回的成果**不计入**执行率 —— 它们还没被主管认可，
计入等于让员工用「还没通过的成果」刷高自己的分。
这与 A6 的物化口径一致（只有 ``confirmed`` 才进绩效域）。

⚠️ 一个容易忽略的坑：同一计划条目**多次提交**会有多个版本
----------------------------------------------------------------
``Result.version_no`` 递增，只有**最新已审批**的版本算数。
如果把历史版本一起平均，执行率会被旧版本稀释（甚至被更早的低分版本拉低）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.models import (
    DayPlan,
    Employee,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
    WeekPlanItem,
)
from ..domain.result_models import (
    RESULT_PLAN_TYPE_MONTH,
    RESULT_PLAN_TYPE_WEEK,
    RESULT_STATUS_CONFIRMED,
    Result,
)
from .period import period_match_keys, to_dashed_period

logger = logging.getLogger(__name__)


# ===========================================================================
# 常量
# ===========================================================================

#: 计划执行率指标编码。
PLAN_EXECUTION_RATE_CODE = "plan_execution_rate"

#: 计划执行率指标名称。
PLAN_EXECUTION_RATE_NAME = "计划执行率"

#: 指标来源标识（写入 ``MetricValue.source``）。
METRIC_SOURCE_PLAN = "plan_result"

#: 指标单位。
METRIC_UNIT_PERCENT = "%"

#: 指标目标值（百分比指标的目标通常是 100）。
METRIC_TARGET_PERCENT = 100.0

#: 日计划「待抽检」状态的判定：主状态机是 submitted 且未记录抽检结论。
#: 与 ``services.plan_approval.list_pending_day_plans`` 保持一致。
_DAY_PLAN_PENDING_REVIEW = "submitted"


def _now() -> datetime:
    """当前 UTC 时间（naive）。"""
    return datetime.now().replace(tzinfo=None)


# ===========================================================================
# 结果对象
# ===========================================================================


@dataclass(frozen=True)
class ExecutionRate:
    """某员工某周期的计划执行率计算结果。"""

    employee_id: str
    period: str
    #: 加权执行率（0~100）。无有效成果时为 None。
    value: float | None
    #: 参与计算的成果条数。
    counted_results: int
    #: 参与计算的条目权重之和（用于判断是否走了退化平均）。
    total_weight: float
    #: 周期内已审批成果总数（不论权重是否有效）。
    approved_results: int
    #: 该周期内该员工应提交的成果条目数（按计划条目总数口径）。
    expected_items: int

    @property
    def is_degraded(self) -> bool:
        """是否走了「无权重 → 简单平均」的退化路径。"""
        return self.total_weight <= 0 and self.counted_results > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "employeeId": self.employee_id,
            "period": self.period,
            "value": self.value,
            "countedResults": self.counted_results,
            "totalWeight": self.total_weight,
            "approvedResults": self.approved_results,
            "expectedItems": self.expected_items,
            "degraded": self.is_degraded,
        }


@dataclass(frozen=True)
class PlanningStats:
    """``GET /planning/stats`` 的统计结果。"""

    pending_day_plans: int
    overdue_pending_day_plans: int
    current_month_results: int
    closure_rate: str
    #: 附加明细（前端契约目前只用上面四个字段，以下是给未来用的增量信息）。
    period: str
    confirmed_results: int
    rejected_results: int
    plan_execution_rate: float | None
    scoped_employee_count: int
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """前端契约 ``PlanningStats``（四个字段）+ 增量明细。"""
        return {
            # ── 前端契约字段（不可改名） ──
            "pendingDayPlans": self.pending_day_plans,
            "overduePendingDayPlans": self.overdue_pending_day_plans,
            "currentMonthResults": self.current_month_results,
            "closureRate": self.closure_rate,
            # ── 增量明细 ──
            "period": self.period,
            "confirmedResults": self.confirmed_results,
            "rejectedResults": self.rejected_results,
            "planExecutionRate": self.plan_execution_rate,
            "scopedEmployeeCount": self.scoped_employee_count,
            "detail": self.detail,
        }


# ===========================================================================
# 内部：成果与条目查询
# ===========================================================================


def _latest_confirmed_results(
    db: Session,
    employee_id: str,
    period: str,
) -> list[Result]:
    """取某员工某周期内**每个计划条目的最新已审批成果**。

    ⚠️ 两个必须做对的细节：

    1. **只取每个条目的最新版本** —— 历史版本一起平均会把执行率稀释。
    2. **先按版本号定「最新版」，再判断它是否已审批** —— 不能反过来。
       反过来的话，最新版被驳回时会**回退到旧的已审批版本**，
       相当于「这次被否了，但上次批过的算我头上」——
       这不是业务本意，员工可以靠多提交几次刷高执行率。

    月成果按 ``month_plan.plan_month`` 归属；周成果按
    ``week_plan.week_start`` 所在月归属（**D6 决策：按周起始月**）。
    """
    # ── 月成果 ──
    # ⚠️ **BUG-R6-26**：``month_plan.plan_month`` 库里存 ``YYYY-MM``，
    #    而本函数的 ``period`` 是紧凑 ``YYYYMM`` —— 直接 ``==`` 会恒为 False，
    #    静默查出 0 条成果（不报错，只是结果凭空消失）。
    #    所以这里用 ``period_match_keys`` 给出两种写法一起比。
    month_keys = period_match_keys(period)
    month_stmt = (
        select(Result)
        .join(MonthPlan, Result.plan_id == MonthPlan.id)
        .where(
            Result.owner_id == employee_id,
            Result.plan_type == RESULT_PLAN_TYPE_MONTH,
            MonthPlan.plan_month.in_(month_keys),
        )
    )
    # ── 周成果 ──
    week_stmt = (
        select(Result)
        .join(WeekPlan, Result.plan_id == WeekPlan.id)
        .where(
            Result.owner_id == employee_id,
            Result.plan_type == RESULT_PLAN_TYPE_WEEK,
        )
    )
    week_rows = list(db.execute(week_stmt).scalars().all())
    # 周成果要按「周起始月」过滤，不能直接下推到 SQL（week_start 是日期）
    # ⚠️ 同上：``_week_start_month`` 返回 ``YYYY-MM``，
    #    这里必须跟同一口径比（不能拿 ``YYYYMM`` 去比 ``YYYY-MM``）。
    period_prefix = to_dashed_period(period) or period
    week_rows = [
        r
        for r in week_rows
        if r.plan_id and _week_start_month(db, r.plan_id) == period_prefix
    ]

    rows = list(db.execute(month_stmt).scalars().all()) + week_rows

    # 先定每个条目的最新版本（**不看状态**），再筛已审批
    latest: dict[tuple[str, str], Result] = {}
    for row in rows:
        key = (row.plan_type, str(row.plan_item_id or row.plan_id))
        current = latest.get(key)
        if current is None or (row.version_no or 0) > (current.version_no or 0):
            latest[key] = row

    return [row for row in latest.values() if row.status == RESULT_STATUS_CONFIRMED]


def _week_start_month(db: Session, plan_id: str) -> str | None:
    """周计划起始月（``YYYY-MM``）。"""
    plan = db.get(WeekPlan, plan_id)
    if plan is None or plan.week_start is None:
        return None
    return f"{plan.week_start.year:04d}-{plan.week_start.month:02d}"


def _item_weight(db: Session, result: Result) -> float:
    """成果所属计划条目的绩效权重。

    月成果 → ``MonthPlanItem.performance_weight``；
    周成果 → ``WeekPlanItem.performance_weight``（周条目可能为空）。
    """
    if not result.plan_item_id:
        return 0.0

    if result.plan_type == RESULT_PLAN_TYPE_MONTH:
        item = db.get(MonthPlanItem, result.plan_item_id)
    elif result.plan_type == RESULT_PLAN_TYPE_WEEK:
        item = db.get(WeekPlanItem, result.plan_item_id)
    else:
        return 0.0

    if item is None:
        return 0.0
    return float(getattr(item, "performance_weight", 0.0) or 0.0)


def _expected_item_count(db: Session, employee_id: str, period: str) -> int:
    """该周期内该员工的应提交条目数（月 + 周）。

    「应提交」的判定：月计划条目（归属 ``plan_month``）
    + 周计划条目（归属 ``week_start`` 所在月）。

    ⚠️ **BUG-R6-26**：``plan_month`` 库里是 ``YYYY-MM``，
    ``period`` 是紧凑 ``YYYYMM`` —— 必须两种写法都比。
    """
    month_keys = period_match_keys(period)
    month_items = db.execute(
        select(MonthPlanItem.id)
        .join(MonthPlan, MonthPlanItem.plan_id == MonthPlan.id)
        .where(MonthPlan.owner_id == employee_id, MonthPlan.plan_month.in_(month_keys))
    ).scalars().all()

    week_items = 0
    period_prefix = to_dashed_period(period) or period
    week_plans = db.execute(
        select(WeekPlan.id, WeekPlan.week_start).where(
            WeekPlan.owner_id == employee_id,
        )
    ).all()
    for plan_id, week_start in week_plans:
        if week_start is None:
            continue
        if f"{week_start.year:04d}-{week_start.month:02d}" != period_prefix:
            continue
        count = db.execute(
            select(func.count(WeekPlanItem.id)).where(WeekPlanItem.plan_id == plan_id)
        ).scalar_one()
        week_items += int(count or 0)

    return len(list(month_items)) + week_items


# ===========================================================================
# 计划执行率计算
# ===========================================================================


def compute_execution_rate(
    db: Session,
    employee_id: str,
    period: str,
) -> ExecutionRate:
    """计算某员工某周期的计划执行率。

    公式::

        执行率 = Σ(完成率 × 条目权重) / Σ(条目权重)

    **退化规则**：所有条目权重均为 0 时 → 退化为简单平均
    （Σ完成率 / 条数）。

    无任何已审批成果时 ``value`` 为 ``None``（**不是 0**）——
    0 会被规则引擎当成「完全没干活」，而「还没提交」是另一回事。
    """
    results = _latest_confirmed_results(db, employee_id, period)
    approved = len(results)
    expected = _expected_item_count(db, employee_id, period)

    if not results:
        return ExecutionRate(
            employee_id=employee_id,
            period=period,
            value=None,
            counted_results=0,
            total_weight=0.0,
            approved_results=0,
            expected_items=expected,
        )

    weighted_sum = 0.0
    total_weight = 0.0
    plain_sum = 0.0

    for row in results:
        rate = float(row.completion_rate or 0.0)
        weight = _item_weight(db, row)
        weighted_sum += rate * weight
        total_weight += weight
        plain_sum += rate

    if total_weight > 0:
        value = weighted_sum / total_weight
    else:
        # 退化：没有权重 → 简单平均
        value = plain_sum / len(results)

    # 完成率理论上在 0~100，越界说明数据异常 —— 夹紧并记日志
    clamped = max(0.0, min(100.0, value))
    if abs(clamped - value) > 1e-9:
        logger.warning(
            "员工 %s %s 的计划执行率 %.2f 越界，已夹紧到 %.2f",
            employee_id,
            period,
            value,
            clamped,
        )

    return ExecutionRate(
        employee_id=employee_id,
        period=period,
        value=clamped,
        counted_results=len(results),
        total_weight=total_weight,
        approved_results=approved,
        expected_items=expected,
    )


def refresh_execution_rate_metric(
    db: Session,
    employee_id: str,
    period: str,
) -> tuple[float | None, bool]:
    """重算并写入 ``MetricValue``（计划执行率）。

    ⚠️ 与 A6 的口径呼应：**不绕过 ``evidence_service.add_metric_value``** ——
    那是算分集的唯一入口，绕过它会破坏「算分唯一真值」这条铁律。

    Returns:
        ``(写入的值, 是否实际写入)``。无有效成果时值为 ``None`` 且不写入。
    """
    from . import evidence_service

    rate = compute_execution_rate(db, employee_id, period)
    if rate.value is None:
        # 没有已审批成果 → **不写** MetricValue。
        # 写一个 0 值会被规则引擎当成「执行率 0%」，是错误信号。
        logger.debug("员工 %s %s 无已审批成果，跳过执行率写入", employee_id, period)
        return None, False

    evidence_service.add_metric_value(
        db,
        employee_id=employee_id,
        metric_code=PLAN_EXECUTION_RATE_CODE,
        period=period,
        value=rate.value,
        target=METRIC_TARGET_PERCENT,
        unit=METRIC_UNIT_PERCENT,
        source=METRIC_SOURCE_PLAN,
        source_ref=f"{employee_id}|{period}|plan_execution_rate",
        as_of=_now(),
    )
    logger.info(
        "员工 %s %s 的计划执行率 = %.2f%%（%d 条成果，权重和 %.2f%s）",
        employee_id,
        period,
        rate.value,
        rate.counted_results,
        rate.total_weight,
        "，已退化为简单平均" if rate.is_degraded else "",
    )
    return rate.value, True


def ensure_metric_definition(db: Session) -> None:
    """确保「计划执行率」指标定义存在（幂等）。

    指标定义表的 ``calculation`` / ``data_source`` 必须非空（决策③读入校验项）。
    """
    from ..domain.models import Metric

    existing = db.execute(
        select(Metric).where(Metric.code == PLAN_EXECUTION_RATE_CODE)
    ).scalar_one_or_none()
    if existing is not None:
        return

    db.add(
        Metric(
            id=f"METRIC_{PLAN_EXECUTION_RATE_CODE}",
            code=PLAN_EXECUTION_RATE_CODE,
            name=PLAN_EXECUTION_RATE_NAME,
            unit=METRIC_UNIT_PERCENT,
            calculation=(
                "sum(result.completion_rate * item.performance_weight) "
                "/ sum(item.performance_weight)"
            ),
            data_source="plan_result",
            caliber=(
                "按计划条目的绩效权重加权计算月度计划执行率；"
                "只统计已审批通过的成果，权重全为 0 时退化为简单平均。"
            ),
            owner="计划管理",
            is_attendance=False,
            active=True,
        )
    )
    db.flush()
    logger.info("已创建计划执行率指标定义：%s", PLAN_EXECUTION_RATE_CODE)


# ===========================================================================
# 数据范围
# ===========================================================================


def resolve_scope_employee_ids(
    db: Session,
    context: Any,
) -> list[str]:
    """按数据范围解析统计覆盖的员工 ID 列表。

    与 ``services.access_control`` 的角色判定保持一致：

    - **HR / 管理员** → 全公司活跃员工
    - **部门负责人** → 管理范围（所属部门及下级部门）内的活跃员工
    - **主管** → 直属下属
    - **普通员工** → 只有自己

    ⚠️ 主管同时也是员工 —— 但管理工作台看的是「下属的情况」，
    所以主管的 scope **只含直属下属，不含自己**（自己那一栏由员工端
    ``/employee/dashboard`` 负责）。
    """
    employee_id = str(context.employee_id)
    roles = set(getattr(context, "roles", ()) or ())
    direct_reports = {str(x) for x in getattr(context, "direct_report_ids", ()) or ()}
    managed_depts = {str(x) for x in getattr(context, "managed_department_ids", ()) or ()}

    # ── HR / 管理员：全公司 ──
    if "hr" in roles or "admin" in roles:
        rows = db.execute(
            select(Employee.id).where(Employee.employment_status == "active")
        ).scalars().all()
        return sorted({str(r) for r in rows})

    # ── 部门负责人：管理范围内全部员工 ──
    if "department_leader" in roles and managed_depts:
        dept_ids = sorted(managed_depts)
        rows = db.execute(
            select(Employee.id).where(
                Employee.department_id.in_(dept_ids),
                Employee.employment_status == "active",
            )
        ).scalars().all()
        scope = {str(r) for r in rows}
        # 兼岗员工可能在别的部门挂靠 —— 用 employee_department 补齐
        from ..domain.models import EmployeeDepartment

        extra = db.execute(
            select(EmployeeDepartment.employee_id).where(
                EmployeeDepartment.department_id.in_(dept_ids)
            )
        ).scalars().all()
        scope |= {str(r) for r in extra}
        return sorted(scope)

    # ── 主管：直属下属 ──
    if direct_reports:
        return sorted(direct_reports)

    # ── 普通员工：只有自己 ──
    return [employee_id]


# ===========================================================================
# 统计
# ===========================================================================


def _pending_day_plan_count(db: Session, employee_ids: Sequence[str]) -> int:
    """待审（待抽检）日计划数。

    口径与 ``plan_approval.list_pending_day_plans`` 一致：
    ``status == submitted`` 且 ``review_status IS NULL``。
    """
    if not employee_ids:
        return 0
    stmt = select(DayPlan.id).where(
        DayPlan.owner_id.in_(list(employee_ids)),
        DayPlan.status == _DAY_PLAN_PENDING_REVIEW,
        DayPlan.review_status.is_(None),
    )
    return len(list(db.execute(stmt).scalars().all()))


def _overdue_day_plan_count(db: Session, employee_ids: Sequence[str]) -> int:
    """逾期未审日计划数。

    逾期判定与 ``plan_approval.serialize_day_plan_item`` 一致：
    ``approval_due_at`` 已过期且尚未记录抽检结论。
    """
    if not employee_ids:
        return 0
    now = _now()
    stmt = select(DayPlan.id).where(
        DayPlan.owner_id.in_(list(employee_ids)),
        DayPlan.status == _DAY_PLAN_PENDING_REVIEW,
        DayPlan.review_status.is_(None),
        DayPlan.approval_due_at.isnot(None),
        DayPlan.approval_due_at < now,
    )
    return len(list(db.execute(stmt).scalars().all()))


def _period_result_counts(
    db: Session,
    employee_ids: Sequence[str],
    period: str,
) -> dict[str, int]:
    """周期内成果计数（按状态）。

    月成果按 ``plan_month`` 归属；周成果按 ``week_start`` 所在月（D6）。
    """
    if not employee_ids:
        return {"total": 0, "confirmed": 0, "rejected": 0, "submitted": 0}

    ids = list(employee_ids)

    # ⚠️ **BUG-R6-26**：两种格式都要比（见 ``_latest_confirmed_results`` 的说明）。
    month_keys = period_match_keys(period)
    period_prefix = to_dashed_period(period) or period

    month_stmt = (
        select(Result.status, Result.id)
        .join(MonthPlan, Result.plan_id == MonthPlan.id)
        .where(
            Result.owner_id.in_(ids),
            Result.plan_type == RESULT_PLAN_TYPE_MONTH,
            MonthPlan.plan_month.in_(month_keys),
        )
    )
    month_rows = db.execute(month_stmt).all()

    week_stmt = (
        select(Result.status, Result.id, Result.plan_id)
        .join(WeekPlan, Result.plan_id == WeekPlan.id)
        .where(
            Result.owner_id.in_(ids),
            Result.plan_type == RESULT_PLAN_TYPE_WEEK,
        )
    )
    week_rows = [
        r
        for r in db.execute(week_stmt).all()
        if _week_start_month(db, r.plan_id) == period_prefix
    ]

    counts = {"total": 0, "confirmed": 0, "rejected": 0, "submitted": 0}
    for status, _id in list(month_rows) + list(week_rows):
        counts["total"] += 1
        if status == RESULT_STATUS_CONFIRMED:
            counts["confirmed"] += 1
        elif status == "rejected":
            counts["rejected"] += 1
        elif status == "submitted":
            counts["submitted"] += 1
    return counts


def _closure_rate(confirmed: int, expected: int) -> str:
    """计划执行闭环率（百分比字符串）。

    口径：**已审批成果数 / 应提交条目数**。
    应提交数为 0 时返回 ``"0%"``（无可闭环，不算错）。
    """
    if expected <= 0:
        return "0%"
    rate = confirmed / expected * 100
    return f"{rate:.1f}%"


def build_planning_stats(
    db: Session,
    context: Any,
    *,
    period: str | None = None,
) -> PlanningStats:
    """构造 ``GET /planning/stats`` 的统计数据。

    Args:
        db: 数据库会话。
        context: 访问上下文（``access_control.build_access_context`` 的结果）。
        period: 统计周期；缺省取当前月。支持 ``YYYY-MM`` / ``YYYYMM`` 两种入参。

    ⚠️ **BUG-R6-26**：统计口径内部用**紧凑 ``YYYYMM``**（权威格式）。
    对外返回的 ``period`` 字段同样用紧凑格式 —— 前端有统一的格式化函数，
    不需要后端再分叉出一种写法。
    """
    from .period import normalize_period

    if period:
        period_text = normalize_period(period) or ""
    else:
        today = date.today()
        period_text = f"{today.year:04d}{today.month:02d}"
    employee_ids = resolve_scope_employee_ids(db, context)

    pending = _pending_day_plan_count(db, employee_ids)
    overdue = _overdue_day_plan_count(db, employee_ids)
    counts = _period_result_counts(db, employee_ids, period_text)

    # 应提交条目数 = scope 内每个员工该周期的应提交条目数之和
    expected_items = sum(
        _expected_item_count(db, emp_id, period_text) for emp_id in employee_ids
    )

    # 执行率：scope 内各员工执行率的加权平均（按各自有效成果条数）
    execution_rates: list[tuple[float, int]] = []
    for emp_id in employee_ids:
        rate = compute_execution_rate(db, emp_id, period_text)
        if rate.value is not None and rate.counted_results > 0:
            execution_rates.append((rate.value, rate.counted_results))

    if execution_rates:
        total_counted = sum(c for _, c in execution_rates)
        overall_rate = (
            sum(v * c for v, c in execution_rates) / total_counted if total_counted else None
        )
    else:
        overall_rate = None

    return PlanningStats(
        pending_day_plans=pending,
        overdue_pending_day_plans=overdue,
        current_month_results=counts["total"],
        closure_rate=_closure_rate(counts["confirmed"], expected_items),
        period=period_text,
        confirmed_results=counts["confirmed"],
        rejected_results=counts["rejected"],
        plan_execution_rate=overall_rate,
        scoped_employee_count=len(employee_ids),
        detail={
            "submittedResults": counts["submitted"],
            "expectedItems": expected_items,
            "scopedEmployeeIds": employee_ids,
        },
    )


# ===========================================================================
# 审批联动
# ===========================================================================


def refresh_after_result_approval(db: Session, result: Result) -> float | None:
    """成果审批通过后重算该员工该周期的计划执行率。

    与 A6 的物化**同点位**（``approve_result`` 内），但**独立失败**：
    执行率算不出来不应该让审批失败。

    Returns:
        写入的执行率；无有效成果时为 ``None``。
    """
    from . import result_evidence_materialization as materialization

    period = materialization.resolve_period(db, result)
    if not period:
        logger.warning("成果 %s 无法解析周期，跳过执行率重算", result.id)
        return None

    try:
        value, _written = refresh_execution_rate_metric(db, result.owner_id, period)
        return value
    except Exception:  # noqa: BLE001 —— 执行率是附加产物，绝不能拖垮审批
        logger.exception("成果 %s 审批后重算计划执行率失败（不影响审批）", result.id)
        return None
