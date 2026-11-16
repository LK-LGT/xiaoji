"""修复身份映射与员工在职状态的不一致（R17 收尾）。

问题
----
``FeishuIdentity.active`` 表示「这个飞书身份当前可用于解析员工」，
但历史上它**没有**跟随员工的 ``employment_status`` / ``account_status``
一起更新。于是出现这类组合：

    employee.employment_status = 'inactive'
    employee.account_status    = 'disabled'
    feishu_identity.active     = 1     ← 错误

危害
----
``FeishuIdentityService.resolve_employee()`` 原本只按 ``identity.active``
过滤，**不看**员工状态 → 会把一个**已禁用**的员工解析出来。
该方法被免密登录、身份绑定、审批回流按人匹配等多处调用。

修复
----
1. **代码层**（已随本修复提交，见 ``organization_sync`` 与
   ``feishu_identity_service``）：
   - ``_sync_identity_activation()``：同步到的员工，身份可用性跟随在职状态；
   - ``_reconcile_identity_activation()``：全量 + 快照完整时做全库对齐；
   - ``resolve_employee()``：加防线，禁用/离职员工一律解析不到。
2. **数据层**（本脚本）：把历史遗留的错配行修正过来。

安全约束
--------
- **默认 dry-run**，只报告不修改；必须显式加 ``--apply`` 才落库。
- 每次修改都写 ``feishu_identity_audit`` 审计记录（action=``align_identity_activation``）。
- **只改 ``active`` 标志，不动 ``employee_id`` 绑定关系** ——
  员工复职/解冻后能自动恢复，不必人工重新绑定。
- 支持 ``--backup`` 生成库文件备份。

用法
----
    python backend/scripts/fix_identity_activation.py               # 预演
    python backend/scripts/fix_identity_activation.py --apply       # 执行
    python backend/scripts/fix_identity_activation.py --apply --backup
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.app.domain.models import (  # noqa: E402
    Employee,
    FeishuIdentity,
    FeishuIdentityAudit,
)
from backend.app.services.offboarding_policy import should_disable_identity  # noqa: E402

DEFAULT_DB = REPO_ROOT / "backend" / "data" / "perf.db"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _resolve_db_url(db_path: Path) -> str:
    return f"sqlite:///{db_path.as_posix()}"


def _sqlite_backup(src: Path, dst: Path) -> None:
    """用 SQLite 官方 backup API 做一致性快照。

    ⚠️ **为什么不能用 ``shutil.copy2``**：
    SQLite 开着 WAL 模式时，最近提交的数据可能还在 ``-wal`` 文件里，
    并没有写回主 ``.db`` 文件。此时直接复制主文件会**丢掉 WAL 里的数据**，
    拿到的备份是个过期快照 —— 用它恢复会回退掉已提交的事务。

    ``Connection.backup()`` 会把主库和 WAL 的全部已提交内容一并写入目标库。
    """
    import sqlite3

    for suffix in ("-wal", "-shm"):
        Path(str(dst) + suffix).unlink(missing_ok=True)

    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def analyse(session) -> dict:
    """统计当前的不一致状况（只读）。"""
    identities = session.execute(
        select(FeishuIdentity).where(FeishuIdentity.active.is_(True))
    ).scalars().all()
    employees = {
        row.id: row
        for row in session.execute(select(Employee)).scalars().all()
    }

    to_disable: list[FeishuIdentity] = []
    dangling: list[FeishuIdentity] = []
    for row in identities:
        employee = employees.get(row.employee_id)
        if employee is None:
            # 员工已不存在 → 悬空身份，必须停用
            dangling.append(row)
            continue
        if should_disable_identity(employee.employment_status):
            to_disable.append(row)

    # 反向：在职员工却所有身份都被停用 → 可恢复
    # ⚠️ 条件必须是「员工**在职**」而不是「员工该被停用」——
    #    后者会把离职员工反而列入恢复名单，逻辑完全反了。
    by_employee: dict[str, list[FeishuIdentity]] = {}
    for row in session.execute(select(FeishuIdentity)).scalars().all():
        by_employee.setdefault(row.employee_id, []).append(row)
    to_restore: list[FeishuIdentity] = []
    for employee_id, employee in employees.items():
        if should_disable_identity(employee.employment_status):
            # 该停用的员工不在这里处理（由 to_disable 负责停用）
            continue
        rows = by_employee.get(employee_id) or []
        if rows and not any(r.active for r in rows):
            to_restore.extend(rows)

    return {
        "to_disable": to_disable,
        "dangling": dangling,
        "to_restore": to_restore,
        "employees": employees,
        "total_active": len(identities),
    }


def report(analysis: dict) -> None:
    to_disable = analysis["to_disable"]
    dangling = analysis["dangling"]
    to_restore = analysis["to_restore"]
    employees = analysis["employees"]

    print("=" * 72)
    print("身份映射一致性检查")
    print("=" * 72)
    print(f"当前 active 身份行数: {analysis['total_active']}")

    print(f"\n[1] 员工已离职/冻结，身份却 active —— 需停用: {len(to_disable)} 条")
    seen: set[str] = set()
    for row in to_disable:
        if row.employee_id in seen:
            continue
        seen.add(row.employee_id)
        emp = employees.get(row.employee_id)
        name = emp.name if emp else "?"
        status = emp.employment_status if emp else "?"
        print(
            f"    emp={row.employee_id:12} {str(name)[:14]:16} "
            f"status={status:9} type={row.identity_type:9} value={row.identity_value}"
        )

    print(f"\n[2] 员工已不存在，身份悬空 —— 需停用: {len(dangling)} 条")
    for row in dangling[:10]:
        print(f"    emp={row.employee_id:12} type={row.identity_type:9} value={row.identity_value}")

    print(f"\n[3] 员工在职，但身份全被停用 —— 可恢复: {len(to_restore)} 条")
    for row in to_restore[:10]:
        emp = employees.get(row.employee_id)
        name = emp.name if emp else "?"
        print(f"    emp={row.employee_id:12} {str(name)[:14]:16} type={row.identity_type:9} value={row.identity_value}")

    total_fix = len(to_disable) + len(dangling) + len(to_restore)
    print(f"\n合计需要修正: {total_fix} 条")


def apply_fixes(session, analysis: dict, actor_id: str) -> int:
    """执行修正并写审计记录。"""
    employees = analysis["employees"]
    fixed = 0

    for row in analysis["to_disable"] + analysis["dangling"]:
        if not row.active:
            # ⚠️ 已经是停用状态 —— 不重复写审计，也不重复计数。
            #    这是幂等性的保证：重复执行不会产生额外变更。
            continue
        employee = employees.get(row.employee_id)
        reason = (
            "员工已不存在，身份悬空"
            if employee is None
            else f"员工状态为 {employee.employment_status}，身份应停用"
        )
        old_active = row.active
        row.active = False
        row.unbound_at = _now()
        session.add(
            FeishuIdentityAudit(
                identity_id=row.id,
                identity_type=row.identity_type,
                identity_value=row.identity_value,
                employee_id=row.employee_id,
                action="align_identity_activation",
                old_value_json={"active": old_active},
                new_value_json={"active": False},
                reason=reason,
                actor_id=actor_id,
            )
        )
        fixed += 1

    for row in analysis["to_restore"]:
        if row.active:
            continue
        old_active = row.active
        row.active = True
        row.unbound_at = None
        session.add(
            FeishuIdentityAudit(
                identity_id=row.id,
                identity_type=row.identity_type,
                identity_value=row.identity_value,
                employee_id=row.employee_id,
                action="align_identity_activation",
                old_value_json={"active": old_active},
                new_value_json={"active": True},
                reason="员工已恢复在职，身份应重新可用",
                actor_id=actor_id,
            )
        )
        fixed += 1

    session.commit()
    return fixed


def verify(session) -> int:
    """复核：修复后还有多少不一致。"""
    identities = session.execute(
        select(FeishuIdentity).where(FeishuIdentity.active.is_(True))
    ).scalars().all()
    employees = {
        row.id: row
        for row in session.execute(select(Employee)).scalars().all()
    }
    remaining = 0
    for row in identities:
        employee = employees.get(row.employee_id)
        if employee is None or should_disable_identity(employee.employment_status):
            remaining += 1
    return remaining


def main() -> int:
    parser = argparse.ArgumentParser(
        description="修复身份映射与员工在职状态的不一致",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正落库（默认只预演，不做任何修改）",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="修改前备份数据库文件",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"数据库文件路径（默认 {DEFAULT_DB}）",
    )
    parser.add_argument(
        "--actor-id",
        default="system:fix_identity_activation",
        help="审计记录里的操作人",
    )
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.exists():
        print(f"[错误] 数据库文件不存在: {db_path}")
        return 1

    print(f"目标数据库: {db_path}")
    if not args.apply:
        print("模式: 预演（不会修改任何数据）\n")

    if args.apply and args.backup:
        backup = db_path.with_suffix(db_path.suffix + ".bak")
        _sqlite_backup(db_path, backup)
        print(f"已备份: {backup}\n")

    engine = create_engine(_resolve_db_url(db_path))
    session = sessionmaker(bind=engine)()

    try:
        analysis = analyse(session)
        report(analysis)

        if not args.apply:
            total = (
                len(analysis["to_disable"])
                + len(analysis["dangling"])
                + len(analysis["to_restore"])
            )
            print(f"\n预演结束：将修正 {total} 条。加 --apply 可实际执行。")
            return 0

        fixed = apply_fixes(session, analysis, args.actor_id)
        print(f"\n已修正 {fixed} 条身份绑定。")

        remaining = verify(session)
        print(f"复核：仍不一致 {remaining} 条" + ("✅" if remaining == 0 else "⚠️"))

        # 输出修复后的对账口径
        conflicts = session.execute(
            select(func.count())
            .select_from(Employee)
            .where(Employee.employment_status != "active")
        ).scalar_one()
        active_identities = session.execute(
            select(func.count())
            .select_from(FeishuIdentity)
            .where(FeishuIdentity.active.is_(True))
        ).scalar_one()
        print(f"当前 active 身份行数: {active_identities}")
        print(f"当前非在职员工数: {conflicts}")
        return 0
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
