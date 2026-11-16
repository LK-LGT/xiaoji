"""0012: 补齐迁移路径缺失的唯一约束（ORM 已声明，迁移未建）

Revision ID: 0012_backfill_missing_unique_constraints
Revises: 0011_sync_batch_idempotency_unique
Create Date: 2026-11-16

背景
----
自检轮 P3 加了「schema 漂移检测」（``backend/scripts/check_schema_drift.py``），
比对 **ORM 建库**（``create_all``）与 **迁移建库**（``alembic upgrade head``）
的唯一约束。守卫第一次跑就报出 5 处「ORM 有 / 迁移库没有」：

    rubric                (weight_set_id, metric_code)
    weight_set            (department_id, position)
    config_snapshot       (task_id)
    attendance_record     (employee_id, work_date, shift_seq)
    self_review           (dedup_key)

这 5 个约束 **ORM 模型里一直有**（``__table_args__`` / ``unique=True``），
但 **迁移链从没建过** —— ``0001_baseline.py`` 建这几张表时只声明了主键。

后果
----
- ``rubric(weight_set_id, metric_code)`` 是"一套权重下每个指标只能有一条 rubric"
  的保证。没有它 → 同一指标能配多套等级区间 → 算分时取到哪套不确定，
  同一份数据两次算分可能得到不同结果。
- ``weight_set(department_id, position)`` 保证一个部门+岗位只有一套权重。
- ``config_snapshot(task_id)`` 保证一个考核任务只有一份冻结快照 ——
  重复快照会让"算分引用哪份配置"变得不确定。
- ``attendance_record`` 保证同一人同一天同一班次只有一条考勤记录。
- ``self_review(dedup_key)`` 保证自评事件去重。

⚠️ 为什么"真实运行库"没出事：
本地 ``backend/data/perf.db`` 是用 ``Base.metadata.create_all()`` 建的，
所以它**一直有**这些约束。CI 用的却是纯 Alembic 建库 ——
也就是说 **CI 环境与真实环境行为不一致**，而漂移在两条路径都"看起来正常"，
自然没人发现。

本迁移把 5 个约束补到迁移路径上，让两条路径**完全一致**。

幂等
----
每条都先检查再动手；已有则跳过。建约束前先查重（约束缺失期间可能已写入
重复行），去重时保留**最早**的一行。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_backfill_missing_unique_constraints"
down_revision: str | Sequence[str] | None = "0011_sync_batch_idempotency_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 表名 -> (约束名, 列组合, 排序依据列)
CONSTRAINTS: list[tuple[str, str, list[str], list[str]]] = [
    (
        "rubric",
        "uq_rubricitem",
        ["weight_set_id", "metric_code"],
        ["weight_set_id", "metric_code"],
    ),
    (
        "weight_set",
        "uq_weightscope",
        ["department_id", "position"],
        ["department_id", "position"],
    ),
    (
        "config_snapshot",
        "uq_config_snapshot_task",
        ["task_id"],
        ["task_id"],
    ),
    (
        "attendance_record",
        "uq_attendancerecord",
        ["employee_id", "work_date", "shift_seq"],
        ["employee_id", "work_date", "shift_seq"],
    ),
    (
        "self_review",
        "uq_self_review_dedup",
        ["dedup_key"],
        ["dedup_key"],
    ),
    # ── 反向：0001_baseline.py 声明了、但 ORM 漏掉的那些 ──
    # 这些约束在**迁移链里本来就有**（建表时声明的），
    # 缺的是 ORM 侧的声明 —— 已在 models.py 补齐。
    # 这里一并列出，保证**任何建库路径**都拿得到这组约束。
    (
        "assessment_record",
        "uq_assessmentrecord_task_employee",
        ["task_id", "employee_id"],
        ["task_id", "employee_id"],
    ),
    (
        "self_review",
        "uq_self_review_record",
        ["record_id"],
        ["record_id"],
    ),
    (
        "manager_review",
        "uq_manager_review_record",
        ["record_id"],
        ["record_id"],
    ),
    (
        "rating_draft",
        "uq_rating_draft_record",
        ["record_id"],
        ["record_id"],
    ),
    (
        "comment_draft",
        "uq_comment_draft_record",
        ["record_id"],
        ["record_id"],
    ),
    (
        "rollup",
        "uq_rollup_scope_period",
        ["scope", "scope_id", "period"],
        ["scope", "scope_id", "period"],
    ),
]


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table: str) -> bool:
    try:
        return table in _inspector().get_table_names()
    except Exception:
        return False


def _columns(table: str) -> list[str]:
    try:
        return [c["name"] for c in _inspector().get_columns(table)]
    except Exception:
        return []


def _has_unique_on(table: str, columns: list[str]) -> bool:
    """该表是否已有这组列的唯一约束/唯一索引。"""
    insp = _inspector()
    try:
        for uc in insp.get_unique_constraints(table):
            if sorted(c["column_name"] for c in uc.get("column_names", [])) == sorted(columns):
                return True
    except Exception:
        pass
    try:
        for idx in insp.get_indexes(table):
            if not idx.get("unique"):
                continue
            if sorted(idx.get("column_names") or []) == sorted(columns):
                return True
    except Exception:
        pass
    return False


def _dedupe(bind, table: str, columns: list[str], order_by: list[str]) -> None:
    """去掉重复行，保留最早的一行。

    ⚠️ 注意 ``WHERE`` 子句里的列要用 ``AND`` 连接 —— 用 ``,`` 会拼出
    ``WHERE a, b IS NOT NULL`` 这种非法 SQL（语法错误 near ","）。
    """
    key_expr = ", ".join(columns)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in columns)
    rows = bind.execute(
        sa.text(
            f"SELECT {key_expr}, COUNT(*) FROM {table} "
            f"WHERE {not_null} GROUP BY {key_expr} HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for row in rows:
        key = row[:-1]
        count = row[-1]
        where = " AND ".join(f"{c} = :v{i}" for i, c in enumerate(columns))
        params = {f"v{i}": v for i, v in enumerate(key)}
        order = ", ".join(f"{c} ASC" for c in order_by)
        keep = bind.execute(
            sa.text(f"SELECT id FROM {table} WHERE {where} ORDER BY {order}"), params
        ).fetchall()[0][0]
        bind.execute(
            sa.text(f"DELETE FROM {table} WHERE {where} AND id <> :keep"),
            {**params, "keep": keep},
        )
        print(f"[0012] {table} 去重 {dict(zip(columns, key, strict=True))}: 保留 {keep}，删除 {count - 1} 行")


def upgrade() -> None:
    bind = op.get_bind()

    for table, name, columns, order_by in CONSTRAINTS:
        if not _table_exists(table):
            continue
        existing = _columns(table)
        if any(c not in existing for c in columns):
            print(f"[0012] {table} 缺少列 {columns}，跳过")
            continue
        if _has_unique_on(table, columns):
            print(f"[0012] {table} 已有 {columns} 的唯一约束，跳过")
            continue

        _dedupe(bind, table, columns, order_by)

        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_unique_constraint(name, columns)
        print(f"[0012] {table} 已补唯一约束 {name} {columns}")


def downgrade() -> None:
    for table, name, columns, _ in CONSTRAINTS:
        if not _table_exists(table):
            continue
        if not _has_unique_on(table, columns):
            continue
        with op.batch_alter_table(table, schema=None) as batch_op:
            try:
                batch_op.drop_constraint(name, type_="unique")
            except Exception:
                pass
        print(f"[0012] {table} 已移除唯一约束 {name}")
