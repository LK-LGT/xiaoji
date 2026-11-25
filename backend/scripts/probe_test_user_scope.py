"""🔎 测试账号「管辖范围」实测（2026-11-24，B2 验收）。

⚠️ **为什么这是 probe 脚本而不是单元测试**
``backend/app/tests/test_test_user_aliases.py`` 里已经用**自建数据**
锁住了「两个总经理角色相同但范围不同」这个性质（可复现，CI 可跑）。

但 B2 的**真实**依据是**生产库里的实际数据** ——
杨飞（`g426c684`）与别嘧咪（`939931ba`）在真实组织架构里
分别管着多少部门、多少汇报线。

⚠️ **为什么不能写成单元测试**
pytest **不保证**连着真实生产库 ——
CI 用的是 ``data/_ci_local.db``（空库）。
⇒ 在单元测试里查真实员工 ⇒ **本地绿 / CI 红**（最坏的一类失败）。
⇒ 真实数据比对放这里，**人工跑**。

用法：
    python backend/scripts/probe_test_user_scope.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.domain.models import Employee  # noqa: E402
from backend.app.services.access_control import (  # noqa: E402
    build_access_context,
    can_access_employee,
)

#: 别名 → 真实员工 ID（与 ``backend/app/routes/auth.py::_TEST_USER_META`` 一致）
ALIASES = {
    "admin": "b8663c7c",
    "yangfei": "g426c684",
    "biyumi": "939931ba",
    "employee": "2aaf32ea",
}


def main() -> int:
    db = get_sessionmaker()()
    try:
        print("=" * 72)
        print("测试账号实测 —— 角色 / 权限 / 管辖范围")
        print("=" * 72)

        contexts = {}
        for alias, emp_id in ALIASES.items():
            emp = db.get(Employee, emp_id)
            if emp is None:
                print(f"\n【{alias}】{emp_id} —— ⚠️ 库里查不到")
                continue

            ctx = build_access_context(db, emp)
            contexts[alias] = (emp, ctx)

            dept = emp.department.name if emp.department else "-"
            print(f"\n【{alias}】{emp_id} {emp.name}")
            print(f"  岗位        : {emp.position or '-'}")
            print(f"  部门        : {dept}")
            print(f"  角色        : {sorted(ctx.roles)}")
            print(f"  权限数      : {len(ctx.permissions)}")
            print(f"  直属下属    : {len(ctx.direct_report_ids)} 人")
            print(f"  汇报线下属  : {len(ctx.reporting_line_ids)} 人")
            print(f"  管理部门数 : {len(ctx.managed_department_ids)} 个")

        # ⚠️ **核心**：两个总经理必须「角色相同、范围不同」
        print()
        print("=" * 72)
        print("核心断言：两个总经理")
        print("=" * 72)

        if "yangfei" not in contexts or "biyumi" not in contexts:
            print("⚠️ 真实库里缺人或未连生产库 ⇒ 跳过该断言")
            return 0

        yang, ctx_yang = contexts["yangfei"]
        bie, ctx_bie = contexts["biyumi"]

        same_roles = ctx_yang.roles == ctx_bie.roles
        diff_depts = ctx_yang.managed_department_ids != ctx_bie.managed_department_ids
        diff_lines = ctx_yang.reporting_line_ids != ctx_bie.reporting_line_ids

        print(f"  角色集相同        : {same_roles}  ({sorted(ctx_yang.roles)})")
        print(
            f"  管理部门不同      : {diff_depts}  "
            f"({len(ctx_yang.managed_department_ids)} vs "
            f"{len(ctx_bie.managed_department_ids)})"
        )
        print(
            f"  汇报线不同        : {diff_lines}  "
            f"({len(ctx_yang.reporting_line_ids)} vs "
            f"{len(ctx_bie.reporting_line_ids)})"
        )

        # ⚠️ 「能不能看到对方的人」—— 业务上真正的区别
        if "employee" in contexts:
            emp, _ = contexts["employee"]
            yang_sees = can_access_employee(ctx_yang, emp, db)
            bie_sees = can_access_employee(ctx_bie, emp, db)
            print(
                f"  杨飞能看到李凯    : {yang_sees}   "
                f"别嘧咪能看到李凯: {bie_sees}"
            )
            if yang_sees == bie_sees:
                print("  ⚠️ 两人对李凯的可见性相同 ⇒ 区分度不足")

        print()
        if same_roles and diff_depts and diff_lines:
            print("✅ 两人角色相同、管辖范围不同 ⇒ 两个测试账号确实可区分")
            return 0
        print("❌ 断言不成立 ⇒ 请复核真实组织架构数据")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
