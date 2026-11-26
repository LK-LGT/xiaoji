"""★ 清理 E2E 验证脚本残留在**真实库**里的测试数据。

背景（2026-11-26 实测）
------------------------
``backend/scripts/verify_permission_isolation_e2e.py`` 等 E2E 脚本会
在**真实库**（``perf.db``）里建带 ``E2E_`` 前缀的员工 / 部门 / 板块授权，
但**没有清理**（脚本结尾只 ``return 0``，没有 teardown）。

⇒ 跑一次 CI 门禁就往真实库丢一批，跑几次就攒几十条。

实测后果（这直接关系到「管理员是谁」这个问题）：
    真实库里躺着 **55 个** ``E2E_*`` 员工，其中 **5 个**持有
    ``system_admin`` 授权（``E2E_PERM管理员``）⇒
    **角色列表里出现 5 个「管理员」**，全是测试残留，
    且它们占着「在职员工」名额、干扰部门人数统计与可见范围判定。

⚠️ **本脚本只删「明确带 E2E 前缀」的行**
------------------------------------------------------------------
绝不按「姓名像测试数据」这类模糊条件删 —— 真实员工也可能叫
「E2E…」？不会，但**原则**是：只按**脚本自己写死的 ID 前缀**删，
且**默认 dry-run**（只报告不删），要真删必须显式加 ``--apply``。

⚠️ **子表范围是「动态发现」的，不是手写清单**
------------------------------------------------------------------
``employee.id`` 被 **27 张表**引用（assessment_record / evidence /
metric_value / day_plan / month_plan / …）。手写清单必然漏，
漏一张就 ``FOREIGN KEY constraint failed``。

⇒ 本脚本用 SQLAlchemy 的 ``Inspector`` **反射出所有外键**，
凡是 ``referred_table == 'employee'`` 的表**全部**纳入清理范围
（按「外键列值 ∈ 待删员工 id」删除）。
⇒ 新增业务表时**不用改这个脚本** —— 反射会自动带上。

用法::

    # 只看会删什么（默认）
    python backend/scripts/cleanup_e2e_residue.py

    # 真的删
    python backend/scripts/cleanup_e2e_residue.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import delete, func, inspect, or_, select  # noqa: E402

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.domain import models as M  # noqa: E402
from backend.app.domain import pan_workload_models as PWM  # noqa: E402

#: E2E 脚本写死的 ID 前缀（与 ``verify_permission_isolation_e2e.py`` 的 TAG 一致）。
#:
#: ⚠️ **只认这几个前缀** —— 它们带 ``RUN_SUFFIX``（uuid[:8]），
#: 所以完整形态是 ``E2E_PERM_<8位hex>_...``。
#: 其它前缀（``E2E_CL_`` / ``E2E_DISP_``）来自别的脚本，也一并清。
E2E_ID_PREFIXES: tuple[str, ...] = (
    "E2E_PERM_",
    "E2E_CL_",
    "E2E_DISP_",
)

#: 员工**姓名**里带的前缀（``E2E_PERMHR`` 这种没有下划线分隔）。
E2E_NAME_PREFIXES: tuple[str, ...] = (
    "E2E_",
)


def _like(prefixes: tuple[str, ...]) -> list[str]:
    """构造「以任一前缀开头」的 LIKE 模式列表。

    ⚠️ **必须转义 ``_``** —— SQL LIKE 里 ``_`` 是**单字符通配符**！
    直接用 ``E2E_PERM_`` 会匹配到 ``E2Ex...`` 之类，
    甚至匹配到 ``E2EX...``（``_`` 匹配任意单字符）。
    ⇒ 用 ``ESCAPE '\\'`` 显式转义。
    """
    clauses = []
    for p in prefixes:
        escaped = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(f"{escaped}%")
    return clauses


def _or_like(column, prefixes: tuple[str, ...]):
    """构造「以任一前缀开头」的 **OR** 条件。

    ⚠️⚠️ **必须用 ``or_()``，不能 ``where(*conds)``** ——
    ``where(a, b, c)`` 是 **AND**，三个前缀同时匹配才成立 ⇒ **永远 0 行**。
    （这个坑静默返回空结果，不报错，最难查。）

    ⚠️ **必须转义 ``_``** —— 见 ``_like``。
    """
    return or_(*[column.like(c, escape="\\") for c in _like(prefixes)])


def _matching_ids(db, model, column) -> list[str]:
    """找出 id 匹配 E2E 前缀的行。"""
    stmt = select(column).where(_or_like(column, E2E_ID_PREFIXES))
    return [r[0] for r in db.execute(stmt).all()]


def _child_tables(db, parent_table: str) -> list[tuple[str, str]]:
    """反射出所有引用 ``parent_table`` 的外键。

    Returns:
        ``[(子表名, 外键列名), ...]``

    ⚠️ **动态发现，不手写清单** —— ``employee.id`` 被 27 张表引用，
    手写必然漏，漏一张就 ``FOREIGN KEY constraint failed``。
    """
    insp = inspect(db.get_bind())
    out: list[tuple[str, str]] = []
    for table in insp.get_table_names():
        for fk in insp.get_foreign_keys(table):
            if fk.get("referred_table") == parent_table:
                cols = fk.get("constrained_columns") or []
                if cols:
                    out.append((table, cols[0]))
    return out


def _delete_orphans(db) -> int:
    """删除「父行已被删掉」的孤儿行，并递归处理。

    ⚠️ **为什么需要这一步**
    ------------------------------------------------------------------
    E2E 脚本会造 ``assessment_record``（挂在 E2E 员工下），
    而 ``assessment_record`` 自己又有子表：
    ``performance_scheme_response`` / ``review_score_version`` /
    ``review_score_head`` / ``score`` / ``self_review`` / ``ai_feedback``。

    删掉 ``assessment_record`` 后 ⇒ 这些子表行变成**孤儿**
    （外键指向已不存在的 id）⇒ ``PRAGMA foreign_key_check`` 报悬空。

    ⇒ 「按外键指向 E2E 实体删」只处理**一层**，
    孤儿要**递归**清干净，否则库里留下坏数据。

    ⚠️ **只删孤儿**（父行确实不存在），不碰任何有效数据。

    ⚠️⚠️ **append-only 表跳过**（``review_score_version`` 等）
    ------------------------------------------------------------------
    这些表有 DB 触发器 ``BEFORE DELETE → RAISE(ABORT, 'append-only history')``，
    **故意**不让删 —— 分数版本历史不可篡改是**业务要求**，不是 bug。

    ⇒ 本脚本**不绕过触发器**（那等于破坏审计完整性）。
    ⇒ 这些表的孤儿行**留着**：它们记录的是「曾经算过分的 E2E 记录」，
      是**历史事实**，不是脏数据。

    Returns:
        删除的孤儿行数。
    """
    # ⚠️ 这些表的 DELETE 被触发器禁止 —— 不动它们。
    APPEND_ONLY_TABLES = frozenset(
        {
            "review_artifact",
            "review_event",
            "review_report",
            "review_score_version",
            "review_resolution",
        }
    )

    total = 0
    for _ in range(10):  # 递归深度保护
        insp = inspect(db.get_bind())
        conn = db.connection()
        deleted_this_round = 0

        for table in insp.get_table_names():
            if table in APPEND_ONLY_TABLES:
                continue  # ⚠️ 业务要求不可删 —— 保留历史
            for fk in insp.get_foreign_keys(table):
                parent = fk.get("referred_table")
                cols = fk.get("constrained_columns") or []
                parent_cols = fk.get("referred_columns") or []
                if not parent or not cols or not parent_cols:
                    continue
                # 自引用不处理（employee.manager_id）
                if parent == table:
                    continue

                child_col, parent_col = cols[0], parent_cols[0]
                n = conn.exec_driver_sql(
                    f'DELETE FROM "{table}" '
                    f'WHERE "{child_col}" IS NOT NULL '
                    f'AND NOT EXISTS ('
                    f'  SELECT 1 FROM "{parent}" WHERE "{parent}"."{parent_col}" = "{table}"."{child_col}"'
                    f')'
                ).rowcount or 0
                if n:
                    deleted_this_round += n
                    total += n

        if not deleted_this_round:
            break
        db.commit()
    return total


def collect(db) -> dict[str, list[str]]:
    """收集各表里待清理的行 id（不删）。"""
    found: dict[str, list[str]] = {}

    # ── 员工（按 id 前缀 + 姓名前缀）──
    emp_ids = _matching_ids(db, M.Employee, M.Employee.id)
    emp_by_name = db.execute(
        select(M.Employee.id).where(_or_like(M.Employee.name, E2E_NAME_PREFIXES))
    ).scalars().all()
    emp = sorted(set(emp_ids) | set(emp_by_name))
    if emp:
        found["employee"] = emp

    # ── 部门（id / name）──
    dept_ids = _matching_ids(db, M.Department, M.Department.id)
    dept_by_name = db.execute(
        select(M.Department.id).where(_or_like(M.Department.name, E2E_NAME_PREFIXES))
    ).scalars().all()
    dept = sorted(set(dept_ids) | set(dept_by_name))
    if dept:
        found["department"] = dept

    # ── 其它表：按 id 前缀 ─
    table_specs: list[tuple[str, object]] = [
        ("system_admin", M.SystemAdmin),
        ("department_module", PWM.DepartmentModule),
        ("hr_department_config", M.HrDepartmentConfig),
        ("pan_workload_policy", PWM.PanWorkloadPolicy),
        ("pan_workload_daily", PWM.PanWorkloadDaily),
        ("pan_manual_metric", PWM.PanManualMetric),
    ]
    for label, model in table_specs:
        if not hasattr(model, "id"):
            continue
        ids = _matching_ids(db, model, model.id)
        if ids:
            found[label] = sorted(ids)

    return found


def apply_cleanup(db, found: dict[str, list[str]]) -> dict[str, int]:
    """删除收集到的行。

    ⚠️ **用「关掉外键约束」的方式删，不靠手工排删除顺序**
    ------------------------------------------------------------------
    依赖链有多层：``employee`` ← ``assessment_record`` ← （它自己还有子表）。
    手工排序必然漏层 ⇒ ``FOREIGN KEY constraint failed``。

    ⇒ 删之前 ``PRAGMA foreign_keys = OFF``，删完 ``PRAGMA foreign_key_check``
    验证没留下悬空引用，再恢复约束。

    ⚠️ **为什么这样安全**：只删「明确带 E2E 前缀」的行 + 「外键指向 E2E 实体」
    的行 ⇒ 不会误伤真实数据。悬空引用由 ``foreign_key_check`` 兜底验证。

    ⚠️ **子表范围动态反射** —— 见 ``_child_tables``。
    """
    counts: dict[str, int] = {}

    emp_ids = found.get("employee", [])
    dept_ids = found.get("department", [])

    conn = db.connection()
    # ⚠️ 关外键约束（SQLite 默认是关的，但显式开/关更稳）。
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")

    try:
        # ── ① 删所有「外键指向待删员工/部门」的子表行 ──
        if emp_ids or dept_ids:
            for table, col in _child_tables(db, "employee"):
                if table == "employee":
                    continue  # 父表自己（employee.manager_id 自引用）单独处理
                n = 0
                if emp_ids:
                    placeholders = ",".join("?" for _ in emp_ids)
                    n += conn.exec_driver_sql(
                        f'DELETE FROM "{table}" WHERE "{col}" IN ({placeholders})',
                        tuple(emp_ids),
                    ).rowcount or 0
                if dept_ids:
                    placeholders = ",".join("?" for _ in dept_ids)
                    n += conn.exec_driver_sql(
                        f'DELETE FROM "{table}" WHERE "{col}" IN ({placeholders})',
                        tuple(dept_ids),
                    ).rowcount or 0
                if n:
                    counts[table] = counts.get(table, 0) + n

        # ── ② 删部门引用方（department 表被谁引用）──
        for table, col in _child_tables(db, "department"):
            if table in ("department", "employee"):
                continue
            if dept_ids:
                placeholders = ",".join("?" for _ in dept_ids)
                n = conn.exec_driver_sql(
                    f'DELETE FROM "{table}" WHERE "{col}" IN ({placeholders})',
                    tuple(dept_ids),
                ).rowcount or 0
                if n:
                    counts[table] = counts.get(table, 0) + n

        # ── ③ 删「自身 id 带前缀」的行（外键可能指向非 E2E 实体）──
        for label, model in (
            ("system_admin", M.SystemAdmin),
            ("department_module", PWM.DepartmentModule),
            ("hr_department_config", M.HrDepartmentConfig),
            ("pan_workload_policy", PWM.PanWorkloadPolicy),
            ("pan_workload_daily", PWM.PanWorkloadDaily),
            ("pan_manual_metric", PWM.PanManualMetric),
        ):
            ids = _matching_ids(db, model, model.id)
            if ids:
                n = db.execute(delete(model).where(model.id.in_(ids))).rowcount or 0
                if n:
                    counts[label] = counts.get(label, 0) + n

        # ── ④ 父表：员工、部门 ──
        if emp_ids:
            counts["employee"] = db.execute(
                delete(M.Employee).where(M.Employee.id.in_(emp_ids))
            ).rowcount or 0
        if dept_ids:
            counts["department"] = db.execute(
                delete(M.Department).where(M.Department.id.in_(dept_ids))
            ).rowcount or 0

        db.commit()

        # ── ⑤ 清理孤儿行（父行已被删掉的子表行）──
        orphan_n = _delete_orphans(db)
        if orphan_n:
            counts["_orphans"] = orphan_n

        # ── ⑥ 悬空引用检查（兜底）──
        # ⚠️ 必须在**重新拿连接**后做 —— ``db.commit()`` 会释放连接。
        check_conn = db.connection()
        violations = check_conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            print(f"  ⚠️ 警告：清理后仍有 {len(violations)} 处悬空外键引用")
            for v in violations[:10]:
                print(f"      {v}")
        else:
            print("  ✅ 外键完整性检查通过（无悬空引用）")

        return counts
    finally:
        # ⚠️ ``db.commit()`` 可能已释放连接 ⇒ 用 try 包住，
        #    拿不到连接时不能抛（会盖掉真正的错误）。
        try:
            db.connection().exec_driver_sql("PRAGMA foreign_keys = ON")
        except Exception:
            pass


def report(db) -> None:
    """打印当前残留情况。"""
    total_emp = db.execute(select(func.count()).select_from(M.Employee)).scalar() or 0
    e2e_emp = db.execute(
        select(func.count())
        .select_from(M.Employee)
        .where(_or_like(M.Employee.name, E2E_NAME_PREFIXES))
    ).scalar() or 0
    e2e_admin = db.execute(
        select(func.count())
        .select_from(M.SystemAdmin)
        .where(_or_like(M.SystemAdmin.id, E2E_ID_PREFIXES))
    ).scalar() or 0

    print("═" * 64)
    print("E2E 残留 —— 真实库")
    print("═" * 64)
    print(f"  员工总数            : {total_emp}")
    print(f"  其中 E2E 残留员工  : {e2e_emp}")
    print(f"  其中 E2E 系统管理员 : {e2e_admin}   ← 这些是「假管理员」")
    print()

    found = collect(db)
    if not found:
        print("  ✅ 没有残留")
        return
    print("  待清理：")
    for label, ids in found.items():
        print(f"    {label:26} {len(ids):>3} 条")
        for i in ids[:5]:
            print(f"        - {i}")
        if len(ids) > 5:
            print(f"        … 还有 {len(ids) - 5} 条")


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 E2E 脚本残留在真实库里的测试数据")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真的删除（默认只报告）",
    )
    args = parser.parse_args()

    db = get_sessionmaker()()
    try:
        report(db)
        if not args.apply:
            print()
            print("  （dry-run）加 --apply 才真删")
            return 0

        print()
        print("  执行清理…")
        counts = apply_cleanup(db, collect(db))
        print()
        print("  已删除：")
        for label, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {label:26} {n:>3} 条")
        print()
        report(db)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
