"""飞书端「每日填报提醒」推送服务（2026-11-23）。

⚠️ **为什么需要主动推送**
------------------------
剪辑同事每天要报「完播率 / 留存率」。**没有人催 = 没人报** ⇒
主管永远拿不到核对依据 ⇒ 整条链路空转。

⇒ 每天定时给剪辑本人推一张「**表格 + 表单**」卡片：
- **表格**（``table`` 元素）展示今天要填什么、当前已填到哪 ——
  员工一眼看到全貌。
- **表单**（``form`` 元素）带输入框 ——
  员工**直接在同一张卡里填完提交**，不用跳到网页。

⚠️ **真机实测结论（2026-11-23）**
--------------------------------
飞书卡片 JSON 2.0 里，``table`` 元素**内部**放不了 ``input``（纯展示），
但 ``table`` 与 ``form`` **作为兄弟元素并列是允许的** ——
探针脚本：``backend/scripts/_probe_feishu_card_table_form.py``
⇒ 这才有了「一张卡既像表格又能填」的形态。

⚠️ **归属安全（约束 C3）**
------------------------
卡片可以**转发**。若不校验，点卡片的人可能替别人报数据。
⇒ 表单回调必须校验：
1. **会话归属** —— ``open_id`` 必须与会话记录一致；
2. **板块隔离** —— 提交人所在部门必须开通了「剪辑工作量」；
3. **目标人** —— 卡片里带的 ``employee_id`` 必须等于提交人自己
   （个人卡）或在提交人的管理范围内（主管代填）。

⚠️ **主管汇总卡**
----------------
主管收到的是**只读**的「谁还没填」清单 ——
⚠️ **绝不能**给主管一张能替下属填的表单卡：
那会让「A 的产出记到 B 头上」重新变成可能。
⇒ 主管卡只有表格 + 引导链接，填报必须由员工本人提交。
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Employee
from ..domain.pan_workload_models import PanManualMetric
from .daily_metric_specs import COUNT_METRICS, RATE_METRICS
from .department_modules import PAN_WORKLOAD_MODULE, has_module

logger = logging.getLogger(__name__)

#: ★ **每日填报要填的指标** —— **按角色分**（2026-11-23 角色倒转）
#:
#: ⚠️ **为什么不再是「剪辑填两个比率」**
#: 业务确认：**剪辑只填数量**（普通视频 / OOTD / 智能体），
#: **完播率 / 视频留存率由主管填** ⇒
#: ⇒ 同一块业务里两个角色填不同的指标。
#:
#: ⚠️ **具体填哪些指标由「部门 × 岗位 口径表」决定**
#: （``pan_workload_policy.metric_codes``），这里只是**默认值**。

#: 剪辑（填报人）默认要填的 —— 三条数量
DAILY_METRICS: tuple[tuple[str, str], ...] = tuple(
    (spec.code, spec.label) for spec in COUNT_METRICS
)

#: 主管（审阅人）默认要填的 —— 两个比率
LEADER_METRICS: tuple[tuple[str, str], ...] = tuple(
    (spec.code, spec.label) for spec in RATE_METRICS
)

#: 指标码 → 中文名。
METRIC_LABELS: dict[str, str] = {
    **{code: label for code, label in DAILY_METRICS},
    **{code: label for code, label in LEADER_METRICS},
}


def format_percent(value: float | None) -> str:
    """把 0~1 的比率格式化成百分数文本。"""
    if value is None:
        return "—"
    if 0 <= value <= 1:
        return f"{value * 100:.1f}%"
    return f"{value}"


def format_count(value: float | None) -> str:
    """把条数格式化成「8 条」文本。"""
    if value is None:
        return "—"
    return f"{value:.0f} 条"


def _is_count_metric(code: str) -> bool:
    """该指标是不是**数量类**（条数）。"""
    from .pan_manual_metric_service import COUNT_METRIC_CODES

    return code in COUNT_METRIC_CODES


def format_metric(code: str, value: float | None) -> str:
    """按指标类型格式化数值。"""
    if value is None:
        return "—"
    if _is_count_metric(code):
        return format_count(value)
    return format_percent(value)


def existing_metric_map(
    db: Session, employee_id: str, day: str
) -> dict[str, float]:
    """查某人某天已填的指标 → ``{指标码: 数值}``。

    ⚠️ **只认 ``scope="day"``** —— 逐文件填报（``scope="file"``）
    是另一套口径，不能当成「这一天的日填报已完成」。
    """
    rows = db.execute(
        select(PanManualMetric).where(
            PanManualMetric.employee_id == employee_id,
            PanManualMetric.metric_date == day,
            PanManualMetric.scope == "day",
        )
    ).scalars().all()
    return {row.metric_code: float(row.metric_value) for row in rows}


def missing_metrics(
    db: Session,
    employee_id: str,
    day: str,
    metrics: tuple[tuple[str, str], ...] | None = None,
) -> list[tuple[str, str]]:
    """返回当天**还没填**的指标列表。

    ⚠️ **已填的指标不再重复要求** —— 员工改数据用「覆盖」语义，
    不该被系统当成「没填」天天催。

    ⚠️ **``metrics`` 决定「该填哪些」** —— 默认取剪辑的三条数量指标。
    """
    metrics = metrics if metrics is not None else DAILY_METRICS
    filled = existing_metric_map(db, employee_id, day)
    return [
        (code, label)
        for code, label in metrics
        if code not in filled
    ]


def build_daily_status_rows(
    db: Session,
    employee_id: str,
    day: str,
    metrics: tuple[tuple[str, str], ...] | None = None,
) -> list[list[str]]:
    """表格的行数据 —— 今天的填报状态。"""
    metrics = metrics if metrics is not None else DAILY_METRICS
    filled = existing_metric_map(db, employee_id, day)
    rows: list[list[str]] = []
    for code, label in metrics:
        value = filled.get(code)
        rows.append([
            label,
            format_metric(code, value),
            "已填" if value is not None else "待填",
        ])
    return rows


def build_leader_status_rows(
    db: Session,
    employee_ids: list[str],
    day: str,
    metrics: tuple[tuple[str, str], ...] | None = None,
) -> list[list[str]]:
    """主管汇总表的行数据 —— 每人一行的填报状态。

    ⚠️ **默认看「条数」指标**（剪辑填的那些）——
    主管要看的是「谁还没报条数」。
    """
    metrics = metrics if metrics is not None else DAILY_METRICS
    employees = list(
        db.scalars(
            select(Employee).where(Employee.id.in_(employee_ids))
        ).all()
    ) if employee_ids else []
    # ⚠️ **按姓名排序** —— 主管要一眼看清谁没填，顺序必须稳定可预期。
    employees.sort(key=lambda e: (e.name or "", e.id))

    rows: list[list[str]] = []
    for employee in employees:
        filled = existing_metric_map(db, employee.id, day)
        done = [
            label for code, label in metrics if code in filled
        ]
        pending = [
            label for code, label in metrics if code not in filled
        ]
        rows.append([
            employee.name or employee.id,
            "、".join(done) if done else "—",
            "、".join(pending) if pending else "无",
        ])
    return rows


def editors_for_push(db: Session, day: str) -> list[Employee]:
    """今天该收到填报提醒的剪辑同事。

    ⚠️ **筛选口径（四重，全部按真实库口径）**
    1. **在职 + 账号启用**；
    2. **所在部门开通了「剪辑工作量」板块**（**板块隔离**）；
    3. **该部门该岗位的口径是 ``reporter``**（要填报）——
       ⚠️ **口径来自 ``pan_workload_policy`` 表，不硬编码**；
    4. **绑定了飞书身份** —— 没有 open_id 根本发不出去。

    ⚠️ **为什么第 3 条不能省（2026-11-23 用户投诉）**
    --------------------------------------------------
    短视频部（实测库）有 5 个人、**3 种角色**::

        巫宜珊 主播 → /口播成品，业务确认**不用管**
        刘旭   运营 → 部门 leader，只审
        张洁   编导 → 剪辑的直属上级，只审
        李婵雨 剪辑 → **要填**
        佟瑶   剪辑 → **要填**

    ⇒ 只按「板块开通了就全推」⇒ 主播/运营/编导**天天收到**
      跟他们无关的填报提醒。

    ⚠️ **为什么不按网盘绑定表（``pan_account_binding``）推导**
    ------------------------------------------------
    绑定表里还有「巫宜珊 / 口播成品」——
    而业务已确认**口播成品不用管** ⇒ 按绑定表推会多推 1 个人。

    ⚠️ **为什么不把岗位名单硬编码在 ``config.py``**
    ------------------------------------------------
    岗位会增减、口径会变（哪天让主播也报数据）⇒ 硬编码要改代码发版。
    ⇒ 口径落在 ``pan_workload_policy`` 表里，改完立即生效。
    """
    from . import pan_workload_policy as policy

    rows = db.scalars(
        select(Employee).where(
            Employee.employment_status == "active",
            Employee.account_status == "enabled",
        )
    ).all()

    result: list[Employee] = []
    for employee in rows:
        if not employee.feishu_open_id and not employee.feishu_user_id:
            continue
        # ⚠️ **板块守卫** —— 与飞书端填报、REST API 用同一套判定。
        if not has_module(db, employee, PAN_WORKLOAD_MODULE):
            continue
        # ⚠️ **岗位口径** —— 只推「要填报」的岗位。
        #    口径来自 ``pan_workload_policy`` 表（部门 × 岗位），
        #    ⚠️ **不硬编码岗位名** —— 岗位会增减、口径会变 ⇒ 要发版就晚了。
        if not policy.resolve_policy(
            db, employee.department_id or "", employee.position or ""
        ).is_reporter:
            continue
        result.append(employee)
    result.sort(key=lambda e: (e.name or "", e.id))
    return result


def day_label(day: str) -> str:
    """把 ``2026-11-23`` 变成人读的 ``11月23日（周一）``。"""
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        return day
    weekday = "一二三四五六日"[parsed.weekday()]
    return f"{parsed.month}月{parsed.day}日（周{weekday}）"


__all__ = [
    "DAILY_METRICS",
    "LEADER_METRICS",
    "METRIC_LABELS",
    "build_daily_status_rows",
    "build_leader_status_rows",
    "day_label",
    "editors_for_push",
    "existing_metric_map",
    "format_count",
    "format_metric",
    "format_percent",
    "missing_metrics",
]
