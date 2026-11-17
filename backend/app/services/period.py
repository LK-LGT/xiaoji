"""考核周期（period）格式归一化工具。

**背景**：系统里存在两种周期格式，且语义相同、格式不同：

| 来源 | 格式 | 示例 |
|---|---|---|
| `AssessmentTask.period` | 紧凑 `YYYYMM` | `202611` |
| 考勤 `Evidence.source_ref` | 带横杠 `YYYY-MM` | `2026-11` |

本模块提供唯一的归一化入口，避免各处各写一套字符串拼接导致口径漂移。

约定：
- **紧凑格式** `YYYYMM` 是考核任务侧的权威格式（落库字段 `AssessmentTask.period`）。
- **带横杠格式** `YYYY-MM` 是考勤证据侧的格式（`Evidence.source_ref` 的周期段）。
- 任何跨表比较都必须先经过本模块转换。
"""

from __future__ import annotations

import re
from datetime import date

# 紧凑格式：6 位数字，如 202611
_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})$")
# 带横杠格式：YYYY-MM
_DASHED_RE = re.compile(r"^(\d{4})-(\d{2})$")
# 日期格式：YYYY-MM-DD（取前 7 位当 YYYY-MM）
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-\d{2}$")


def normalize_period(period: str | None) -> str | None:
    """把任意受支持的周期格式归一化为紧凑格式 `YYYYMM`。

    Args:
        period: 周期字符串。支持 ``202611``、``2026-11``、``2026-11-01`` 三种格式。

    Returns:
        紧凑格式 `YYYYMM`；无法解析时返回 None。

    Examples:
        >>> normalize_period("202611")
        '202611'
        >>> normalize_period("2026-11")
        '202611'
        >>> normalize_period("2026-11-01")
        '202611'
        >>> normalize_period("abc") is None
        True
    """
    if not period:
        return None
    text = str(period).strip()

    match = _COMPACT_RE.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}{month:02d}"
        return None

    match = _DASHED_RE.match(text) or _DATE_RE.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}{month:02d}"
        return None

    return None


def to_dashed_period(period: str | None) -> str | None:
    """把周期转换为带横杠格式 `YYYY-MM`（考勤证据侧格式）。

    Examples:
        >>> to_dashed_period("202611")
        '2026-11'
        >>> to_dashed_period("2026-11")
        '2026-11'
    """
    compact = normalize_period(period)
    if compact is None:
        return None
    return f"{compact[:4]}-{compact[4:]}"


def period_date_range(period: str | None) -> tuple[date, date] | None:
    """把周期解析为该月的起止日期（含首尾）。

    Examples:
        >>> period_date_range("202611")
        (date(2026, 11, 1), date(2026, 11, 30))
    """
    compact = normalize_period(period)
    if compact is None:
        return None
    year, month = int(compact[:4]), int(compact[4:])
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1).replace(day=1)
        from datetime import timedelta

        end = end - timedelta(days=1)
    return start, end


def periods_overlap(period_a: str | None, period_b: str | None) -> bool:
    """判断两个周期是否为同一周期（格式不同也能正确比较）。"""
    left = normalize_period(period_a)
    right = normalize_period(period_b)
    if left is None or right is None:
        return False
    return left == right


def period_match_keys(period: str | None) -> tuple[str, ...]:
    """返回该周期在库里可能出现的**所有**写法（用于 SQL ``IN`` 比对）。

    ⚠️ 为什么需要这个：
    ``month_plan.plan_month`` 在库里存的是 **``YYYY-MM``**（历史数据），
    而考核周期侧的权威格式是 **``YYYYMM``**。
    直接拿 ``YYYYMM`` 去 ``==`` 一个 ``YYYY-MM`` 的列 → **恒为 False**，
    静默查出 0 条 —— 这种 bug 最难发现（不报错，只是结果凭空消失）。

    所以凡是「拿周期去和库里某个周期列比对」的地方，
    都要用本函数给出**全部候选写法**，而不是只给一个。

    Examples:
        >>> period_match_keys("202611")
        ('202611', '2026-11')
        >>> period_match_keys("2026-11")
        ('2026-11', '202611')
        >>> period_match_keys("bad")
        ()
    """
    compact = normalize_period(period)
    if compact is None:
        return ()
    dashed = f"{compact[:4]}-{compact[4:]}"
    return (compact, dashed)
