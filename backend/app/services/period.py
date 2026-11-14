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


def normalize_period(period: str | None) -> str | None:
    """把任意受支持的周期格式归一化为紧凑格式 `YYYYMM`。

    Args:
        period: 周期字符串。支持 `202611` 与 `2026-11` 两种格式。

    Returns:
        紧凑格式 `YYYYMM`；无法解析时返回 None。

    Examples:
        >>> normalize_period("202611")
        '202611'
        >>> normalize_period("2026-11")
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

    match = _DASHED_RE.match(text)
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
