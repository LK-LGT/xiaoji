"""0022: 统一考核周期格式为紧凑 ``YYYYMM``（BUG-R6-26）

Revision ID: 0022_normalize_period_format
Revises: 0021_org_change_queue_dedup_uq
Create Date: 2026-11-17

背景
----
系统里存在**两种周期格式**，语义相同、写法不同：

============================  ==============  ================
字段                           库内格式        示例
============================  ==============  ================
``AssessmentTask.period``      ``YYYYMM``     ``202611``
``MetricValue.period``         **混存**       ``202611`` / ``2026-11``
``MonthPlan.plan_month``       ``YYYY-MM``    ``2026-11``
============================  ==============  ================

后果
----
``MetricValue.period`` 里两种格式**混存**，于是：

- 算分时 ``MetricValue.period == period``（``period`` 来自任务，是 ``YYYYMM``）
  对 ``YYYY-MM`` 那几行**恒为 False** → 指标"缺数据不参与算分"
  → 可能直接抛 ``ScoringError: 所有指标均缺少可用数据``；
- 同一份指标数据被拆成两行 → 重复累加或漏算；
- 任何按周期做的唯一性判断、报表聚合都会**口径分叉**。

历史上有过「两种格式都查一遍」的兜底（``review_service._build_metric_context``），
但那是**补丁不是修复** —— 新写入的行仍可能落成另一种格式。

本次改动
--------
1. **写入侧**（``evidence_service.add_metric_value``）统一归一化到 ``YYYYMM``
   —— 那是算分集的唯一入口，归一化放在这里最有效。
2. **存量数据**（本迁移）把 ``metric_value.period`` 里已经是 ``YYYY-MM``
   的行改成 ``YYYYMM``。
3. ``MonthPlan.plan_month`` **不动** —— 那是月计划自己的业务字段，
   改成 ``YYYYMM`` 会牵动大量前端逻辑与历史数据，收益不抵风险。
   读取侧统一用 ``period_match_keys`` 做**双格式兼容比对**。

⚠️ **潜在的唯一键冲突**
------------------------
``metric_value`` 上有 ``UNIQUE(employee_id, metric_code, period)``。
把 ``2026-11`` 改成 ``202611`` 时，如果**同一员工同一指标**已经存在
一行 ``202611``，就会撞唯一约束。

所以这里**不能盲目 UPDATE** —— 必须：

1. 先找出「归一化后会撞车」的组；
2. 每组只保留**最后写入**（``as_of`` 最新）的那行，其余删除；
3. 再做归一化更新。

被删掉的行是**重复数据**（同一指标同一周期的两份记录），
保留最新的一份在语义上等价于「取最新值」，不丢信息。

幂等
----
- 已是 ``YYYYMM`` 的行不进「待改」集合 → 重复执行无副作用；
- 冲突清理只针对「归一化后会撞车」的组，第二次跑时组已不存在 → 无副作用。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_normalize_period_format"
down_revision: str | Sequence[str] | None = "0021_org_change_queue_dedup_uq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "metric_value"


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists() -> bool:
    try:
        return TABLE in _inspector().get_table_names()
    except Exception:
        return False


def _columns() -> set[str]:
    try:
        return {c["name"] for c in _inspector().get_columns(TABLE)}
    except Exception:
        return set()


def _is_compact(value: object) -> bool:
    text = str(value or "")
    return len(text) == 6 and text.isdigit()


def _to_compact(value: object) -> str:
    text = str(value or "")
    return text[:4] + text[5:] if len(text) == 7 and text[4] == "-" else text


def upgrade() -> None:
    """把 ``metric_value.period`` 的 ``YYYY-MM`` 行归一化为 ``YYYYMM``。"""
    if not _table_exists():
        print(f"[0022] 表 {TABLE} 不存在，跳过")
        return

    cols = _columns()
    if "period" not in cols:
        print(f"[0022] {TABLE} 无 period 列，跳过")
        return

    bind = op.get_bind()

    # ── 1) 找出所有「带横杠」的行 ─────────────────────────────────
    dashed_rows = bind.execute(
        sa.text(
            f"SELECT rowid, employee_id, metric_code, period, as_of "
            f"FROM {TABLE} WHERE period LIKE '%-%'"
        )
    ).fetchall() if _has_rowid(bind) else []

    if not dashed_rows:
        # rowid 不可用（PG）→ 改用主键方式查询
        pk = _primary_key(bind)
        if not pk:
            print("[0022] 无法确定主键，跳过")
            return
        dashed_rows = bind.execute(
            sa.text(
                f"SELECT {pk}, employee_id, metric_code, period, as_of "
                f"FROM {TABLE} WHERE period LIKE '%-%'"
            )
        ).fetchall()

    if not dashed_rows:
        print("[0022] 没有 YYYY-MM 格式的 period，无需归一化")
        return

    print(f"[0022] 发现 {len(dashed_rows)} 行 YYYY-MM 格式的 period")

    # ── 2) 处理冲突：归一化后会撞 (employee_id, metric_code, period) 唯一键 ──
    #    每组保留 as_of 最新的一行，其余删除（重复数据，保留最新等价于取最新值）。
    groups: dict[tuple[str, str, str], list[tuple]] = {}
    for row in dashed_rows:
        row_id, employee_id, metric_code, period, as_of = row[0], row[1], row[2], row[3], row[4]
        compact = _to_compact(period)
        groups.setdefault((str(employee_id), str(metric_code), compact), []).append(
            (row_id, as_of)
        )

    pk = _primary_key(bind)
    if pk is None:
        print("[0022] 无法确定主键，跳过")
        return

    deleted = 0
    for (employee_id, metric_code, compact), entries in groups.items():
        # 同组内：已是紧凑格式的行 + 待转换的行
        existing_compact = bind.execute(
            sa.text(
                f"SELECT {pk}, as_of FROM {TABLE} "
                f"WHERE employee_id = :emp AND metric_code = :code AND period = :compact"
            ),
            {"emp": employee_id, "code": metric_code, "compact": compact},
        ).fetchall()

        if not existing_compact:
            continue  # 不冲突，直接转

        # 冲突组：把所有候选行按 as_of 排序，保留最新一条，其余删除
        candidates = [(row_id, as_of) for row_id, as_of in existing_compact]
        candidates.extend(entries)
        # as_of 可能为 None —— 排最后
        candidates.sort(key=lambda pair: (pair[1] is None, str(pair[1] or "")))

        keep_id = candidates[-1][0]
        for row_id, _as_of in candidates[:-1]:
            bind.execute(
                sa.text(f"DELETE FROM {TABLE} WHERE {pk} = :rid"),
                {"rid": row_id},
            )
            deleted += 1
        # 保留的那行统一写成紧凑格式
        bind.execute(
            sa.text(f"UPDATE {TABLE} SET period = :compact WHERE {pk} = :rid"),
            {"compact": compact, "rid": keep_id},
        )

    if deleted:
        print(f"[0022] 清理重复行 {deleted} 条（同一指标同一周期保留了最新一条）")

    # ── 3) 把剩余的带横杠行直接转换 ─────────────────────────────────
    result = bind.execute(
        sa.text(
            f"UPDATE {TABLE} SET period = substr(period, 1, 4) || substr(period, 6, 2) "
            f"WHERE period LIKE '%-%'"
        )
    )
    converted = result.rowcount if result.rowcount and result.rowcount > 0 else 0
    print(f"[0022] 归一化完成：转换 {converted} 行，清理重复 {deleted} 行")


def downgrade() -> None:
    """反向：**不做**任何事。

    ⚠️ 为什么不可逆：
    ``YYYYMM`` → ``YYYY-MM`` 的反向转换是**有损**的 —— 原本就以 ``YYYYMM``
    存储的行无法与「本来就是 ``YYYY-MM``、本次被转换过来」的行区分开。
    强行回转会破坏那些本来就正确的数据。

    周期格式归一化属于「向前修复」，回退到混存状态没有意义。
    """
    print("[0022] 周期格式归一化不可逆（回退会破坏原本正确的数据），跳过")


def _has_rowid(bind) -> bool:
    """SQLite 有 ``rowid``；PG 没有。"""
    try:
        bind.execute(sa.text("SELECT rowid FROM metric_value LIMIT 1"))
        return True
    except Exception:
        return False


def _primary_key(bind) -> str | None:
    """取表的主键列名。"""
    try:
        inspector = _inspector()
        pk = inspector.get_pk_constraint(TABLE)
        columns = pk.get("constrained_columns") or []
        return columns[0] if columns else None
    except Exception:
        return None
