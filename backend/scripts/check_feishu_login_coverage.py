"""查飞书身份绑定覆盖率 —— **免登能不能用，全看这个**。

为什么需要这个脚本
------------------
免登链路是三步串联的：

    ① 飞书返回 open_id
    ② 本地按 open_id 找到员工（``FeishuIdentity`` 行，且 ``active=True``）
    ③ 该员工账号没被禁用（``Employee.account_status == "enabled"``）

⇒ **「我能登录」≠「大家都能登录」**。
  绑定率低时，只有绑定过的人能免登，其余人点「飞书登录」会得到
  「该飞书账号尚未绑定员工身份」。

⚠️ 排查时最容易犯的错：拿**一个人**试通了，就以为**全员**都能用。
  这个脚本给出真实分母。

用法
----
    python backend/scripts/check_feishu_login_coverage.py

⚠️ 需要能连上配置的数据库（默认 ``backend/data/perf.db``）。

退出码
------
    0  全员可免登
    1  有员工无法免登（输出明细）
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从仓库根目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import get_sessionmaker  # noqa: E402
from app.domain.models import Employee, FeishuIdentity  # noqa: E402
from sqlalchemy import func, select  # noqa: E402


def main() -> int:
    db = get_sessionmaker()()
    try:
        total = db.execute(select(func.count(Employee.id))).scalar() or 0
        print(f"员工总数 : {total}\n")

        if not total:
            print("⚠️ 库里还没有员工 —— 先跑一次组织架构同步。")
            return 1

        # ── 身份行统计 ──────────────────────────────────────────────
        print("=== 身份绑定行统计 ===")
        for active_only in (False, True):
            label = "仅 active" if active_only else "全部"
            q = select(
                FeishuIdentity.identity_type, func.count(FeishuIdentity.id)
            ).group_by(FeishuIdentity.identity_type)
            if active_only:
                q = q.where(FeishuIdentity.active.is_(True))
            rows = db.execute(q).all()
            counts = {t: c for t, c in rows}
            suffix = f"（{label}）"
            print(f"  {suffix}")
            for t in ("open_id", "union_id", "user_id"):
                print(f"    {t:10} : {counts.get(t, 0)}")
        print()

        # ── 免登可用性 ──────────────────────────────────────────────
        active_open = select(func.distinct(FeishuIdentity.employee_id)).where(
            FeishuIdentity.identity_type == "open_id"
        ).where(FeishuIdentity.active.is_(True))

        can_login = (
            db.execute(
                select(func.count(func.distinct(Employee.id)))
                .where(Employee.id.in_(active_open))
                .where(Employee.account_status == "enabled")
            ).scalar()
            or 0
        )
        disabled = (
            db.execute(
                select(func.count(func.distinct(Employee.id)))
                .where(Employee.id.in_(active_open))
                .where(Employee.account_status != "enabled")
            ).scalar()
            or 0
        )
        unbound = (
            db.execute(
                select(func.count(Employee.id)).where(Employee.id.notin_(active_open))
            ).scalar()
            or 0
        )

        print("=== 免登可用性 ===")
        print(f"  ✅ 能免登（有 active open_id + 账号启用）: {can_login} / {total}"
              f"  ({can_login * 100 / total:.1f}%)")
        print(f"  ❌ 无 active open_id（免登查不到人）       : {unbound}")
        print(f"  ⚠️  有 open_id 但账号被禁用                : {disabled}")
        print()

        # ── 明细 ────────────────────────────────────────────────────
        if unbound:
            print("=== 无 active open_id 的员工（抽样 15 个）===")
            rows = db.execute(
                select(
                    Employee.id,
                    Employee.name,
                    Employee.account_status,
                    Employee.employment_status,
                    Employee.login_account,
                )
                .where(Employee.id.notin_(active_open))
                .limit(15)
            ).all()
            for r in rows:
                print(
                    f"  {r[0]:22} {r[1]:14} account={r[2]:10} employment={r[3]:10} login={r[4]}"
                )
            if unbound > len(rows):
                print(f"  …… 共 {unbound} 人")
            print()
            # ⚠️⚠️ **不要笼统说「未同步」**（2026-11-17 更正）。
            #
            #   实测这批人**全部**已经同步到（身份行都在，只是 active=False）——
            #   真正的原因是**在职状态**决定了身份是否可用。
            #   说「去跑同步」会让人白跑一趟，方向完全错。
            #
            #   实际原因分布（按 employment_status）：
            #     inactive 冻结/未激活/未加入企业 → 临时状态，解冻后自动恢复
            #     exited   离职                  → 永久，需人工复职
            for status, count in db.execute(
                select(Employee.employment_status, func.count(Employee.id))
                .where(Employee.id.notin_(active_open))
                .group_by(Employee.employment_status)
            ).all():
                meaning = {
                    "exited": "离职 —— 永久禁用，需人工复职",
                    "inactive": "冻结/未激活/未加入企业 —— 临时，解冻后自动恢复",
                    "active": "在职但身份绑定失效 —— 重跑同步可能能修好",
                }.get(str(status), "其它状态")
                print(f"  · employment_status={status:10} : {count:3} 人 —— {meaning}")
            print()
            print("  💡 这些人点「飞书登录」会得到**针对性**提示（2026-11-17 已细分）：")
            print("     · 已离职     → 「该账号对应的员工已离职…」")
            print("     · 账号被停用 → 「该员工账号已被停用…」")
            print("     · 未同步     → 「该飞书账号未同步到系统…」")
            print()

        if disabled:
            print("=== 账号被禁用但身份仍 active 的员工（抽样 15 个）===")
            rows = db.execute(
                select(Employee.id, Employee.name, Employee.account_status)
                .where(Employee.id.in_(active_open))
                .where(Employee.account_status != "enabled")
                .limit(15)
            ).all()
            for r in rows:
                print(f"  {r[0]:22} {r[1]:14} status={r[2]}")
            if disabled > len(rows):
                print(f"  …… 共 {disabled} 人")
            print()
            print("  💡 这是**设计如此** —— 离职/停用账号不该能登录（安全边界）。")

        # ── 冲突检查 ────────────────────────────────────────────────
        conflicts = db.execute(
            select(
                FeishuIdentity.identity_value,
                func.count(func.distinct(FeishuIdentity.employee_id)),
            )
            .where(FeishuIdentity.identity_type == "open_id")
            .group_by(FeishuIdentity.identity_value)
            .having(func.count(func.distinct(FeishuIdentity.employee_id)) > 1)
        ).all()
        print("=== 同一 open_id 绑多人？（会 500）===")
        if conflicts:
            for value, count in conflicts:
                print(f"  ⚠️ open_id={value} 绑了 {count} 个人")
        else:
            print("  ✅ 无冲突")
        print()

        if unbound or disabled:
            return 1
        print("✅ 全员可免登")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
