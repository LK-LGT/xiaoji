"""★ **按飞书真实组织架构开通「部门工作量」板块**（2026-11-25）。

⚠️ **这个脚本做什么**
--------------------
把 ``dept_workload``（部门工作量）板块**开通给飞书同步过来的真实部门**，
并按**真实岗位**配置 ``pan_workload_policy`` 的角色：

- **reporter** —— 要填报的人（普通岗位）
- **reviewer** —— 只核对的人（岗位名带「主管 / 总经理 / 总监」等）
- **none**     —— 不参与（**不写行** ⇒ 默认不参与）

⚠️ **为什么不写死部门清单**
----------------------------
本脚本**从库里读飞书同步过来的部门树**，按**业务域**归类：
⇒ 飞书新增部门后重跑本脚本即可 ⇒ 不会漏、也不会写错部门 ID。

⚠️ **指标留空**
----------------
用户明确要求「各岗位填什么指标，后续做实际的开发和填入」
⇒ 本脚本**不预设任何指标码** ⇒ ``metric_codes`` 留空
⇒ 卡片不会出现任何填报项，等业务定了指标再配。

⚠️ **⚠️⚠️ 关键：留空指标 ⇒ 非剪辑板块不会套用视频默认指标**
--------------------------------------------------------------
``pan_workload_policy`` 没配指标时，历史上会回退到**视频默认指标**
（普通视频/OOTD/智能体）⇒ 仓储部分拣员会收到「今天剪了几条视频」。
2026-11-25 已把默认值改成**按板块**给（见 ``pan_workload_policy``），
⇒ 非剪辑板块**没有默认指标** ⇒ 留空就是「指标待定」。

⚠️ **用法**
------------
```powershell
# ① 体检（只读，不写库）—— 先看会开什么
python backend/scripts/seed_department_workload.py --dry-run

# ② 实际开通
python backend/scripts/seed_department_workload.py --confirm
```
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# ⚠️ **必须引导到「仓库根」并用 ``backend.app.*`` 导入**（2026-11-26 修复）
#
# 原来这里引导到 ``backend/`` 并用 ``from app.core.db import ...``。
# 那在**单独跑脚本**时没问题，但从**仓库根**跑 pytest 时会炸：
#
#   ModuleNotFoundError: No module named 'app.core'
#
# 根因：仓库根的 ``conftest.py`` 把 ``agent-service/`` 追加进 ``sys.path``，
# 而 ``agent-service/app/`` 里**也有一个叫 ``app`` 的包** ⇒
# ``import app`` 解析到 **agent-service 的 app**，不是 ``backend/app``。
# ⇒ ``app.core`` 自然找不到。
#
# 而根 conftest 之所以把 agent-service 放进 sys.path，是为了让
# ``tests._test_secret`` 能解析 —— 代价就是顶层 ``app`` 被**抢占**。
#
# ⇒ 结论：后端脚本**一律用 ``backend.app.*`` 全路径**，
# 并把**仓库根**（``parents[2]``）放进 ``sys.path``。
# 这样无论从哪个目录跑、sys.path 上有什么，都不会有歧义。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.domain.models import Department, Employee  # noqa: E402
from backend.app.domain.pan_workload_models import (  # noqa: E402
    DepartmentModule,
    PanWorkloadPolicy,
)
from backend.app.services.department_modules import DEPT_WORKLOAD_MODULE  # noqa: E402
from backend.app.services.pan_workload_policy import (  # noqa: E402
    ROLE_NONE,
    ROLE_REPORTER,
    ROLE_REVIEWER,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed_department_workload")


# ═════════════════════════════════════════════════════════════════════════
# ★ **业务域划分** —— 按飞书真实部门树归类
# ═════════════════════════════════════════════════════════════════════════
#
# ⚠️ **为什么按「业务域」而不是按部门逐个开**
#   30 个部门逐个配 ⇒ 配置量大且容易漏；
#   业务域（仓储物流/客服售后/电商运营/商品供应链/职能支持）
#   ⇒ 与真实组织结构对应，改动小、可读。
#
# ⚠️ **父部门开通 ⇒ 子部门继承**（祖先继承，见 ``department_modules``）
#   ⇒ 只需开通**业务域的根部门**，子树自动继承。

#: 业务域 → 飞书真实部门名（**根部门**，子树自动继承）
BUSINESS_DOMAINS: dict[str, list[str]] = {
    "仓储物流": ["仓储部"],
    "客服售后": ["客服部"],
    "电商运营": ["运营中心"],
    "商品供应链": ["商品中心", "生产部", "采购部"],
    "职能支持": ["AI技术部", "人事行政部", "财务部", "总经办"],
}

# ⚠️ **哪些业务域要排除某些部门**
#:
#: ⚠️ **短视频必须排除**
#:   短视频挂在「运营中心」下 ⇒ 开通「运营中心」会**连带继承**给它。
#:   而短视频已经有自己的 ``pan_workload`` 板块（含网盘采集）
#:   ⇒ 两边都开 ⇒ 剪辑会看到**两个**入口 ⇒ 困惑。
#:
#: ⇒ 因此「电商运营」域**不通过父部门开通**，
#:   改为**逐个子部门开通**（运营中心本身不开），短视频自然被排除。
DOMAINS_WITH_EXCLUSIONS: dict[str, list[str]] = {
    "电商运营": ["短视频"],
}


# ═════════════════════════════════════════════════════════════════════════
# ★ **岗位 → 角色** 的判定
# ═════════════════════════════════════════════════════════════════════════
#
# ⚠️ **为什么按岗位名判定**
#   用户要求：「普通岗位=填报，带主管/经理/组长/总监字样的=核对」。
#   岗位名是**飞书同步过来的**（稳定），
#   人会流动（今天李婵雨，明天佟瑶调岗）⇒ 按岗位配，不按人配。

#: 岗位名里出现这些词 ⇒ **reviewer**（只核对，不替下属填）
REVIEWER_KEYWORDS: tuple[str, ...] = (
    "主管",
    "经理",
    "组长",
    "总监",
    "总经理",
    "负责人",
)

#: 岗位名里出现这些词 ⇒ **none**（不参与这块业务）
NONE_KEYWORDS: tuple[str, ...] = (
    "主播",  # 短视频主播已确认「不用管」
)


def role_for_position(position: str) -> str:
    """按岗位名判定角色。

    ⚠️ **判定顺序不能反**
    ------------------------
    ① ``none`` 优先 —— 「主播」这类**明确不参与**的岗位，
       不能因为名字里含「主管」之类而被误判成 reviewer。
    ② 含「主管/经理/…」⇒ reviewer
    ③ 其它 ⇒ reporter
    """
    pos = (position or "").strip()
    if not pos:
        # ⚠️ **没岗位 ⇒ 不参与**（默认关闭哲学）
        return ROLE_NONE
    if any(keyword in pos for keyword in NONE_KEYWORDS):
        return ROLE_NONE
    if any(keyword in pos for keyword in REVIEWER_KEYWORDS):
        return ROLE_REVIEWER
    return ROLE_REPORTER


# ═════════════════════════════════════════════════════════════════════════
# 工具
# ═════════════════════════════════════════════════════════════════════════


def build_children(departments: list[Department]) -> dict[str, list[str]]:
    """部门树 → ``{父部门 id: [子部门 id, ...]}``。"""
    children: dict[str, list[str]] = {}
    for dept in departments:
        if dept.parent_id:
            children.setdefault(str(dept.parent_id), []).append(str(dept.id))
    return children


def descendants(dept_id: str, children: dict[str, list[str]]) -> list[str]:
    """某部门的**全部后代**（含自己）。"""
    result = [dept_id]
    stack = [dept_id]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in result:
                result.append(child)
                stack.append(child)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="按飞书真实架构开通部门工作量板块")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="真的写库（不加这个只做体检，不改任何数据）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读体检（默认行为）",
    )
    args = parser.parse_args()

    sessionmaker = get_sessionmaker()
    db = sessionmaker()

    try:
        departments = list(
            db.scalars(select(Department).where(Department.active.is_(True))).all()
        )
        by_id = {str(d.id): d for d in departments}
        by_name = {d.name: d for d in departments}
        children = build_children(departments)

        # ── ① 找出要开通的部门 ──────────────────────────────
        target_ids: dict[str, str] = {}  # {部门名: 业务域}
        skipped_names: set[str] = set()

        for domain, roots in BUSINESS_DOMAINS.items():
            for root_name in roots:
                root = by_name.get(root_name)
                if root is None:
                    logger.warning(f"⚠️  部门不存在（跳过）：{root_name}")
                    continue
                # ⚠️ **排除项要连带排除其子树**
                skip_ids: set[str] = set()
                for name in DOMAINS_WITH_EXCLUSIONS.get(domain, []):
                    skipped = by_name.get(name)
                    if skipped is not None:
                        for sid in descendants(str(skipped.id), children):
                            skip_ids.add(sid)
                            skipped_names.add(by_id[sid].name)

                for dept_id in descendants(str(root.id), children):
                    if dept_id in skip_ids:
                        continue
                    target_ids[by_id[dept_id].name] = domain

        # ── ② 打印体检结果 ─────────────────────────────────
        print("=" * 74)
        print("按飞书真实架构开通「部门工作量」板块 · 体检")
        print("=" * 74)

        print("\n【① 按业务域划分】")
        for domain in BUSINESS_DOMAINS:
            opened = sorted(name for name, dom in target_ids.items() if dom == domain)
            skip = DOMAINS_WITH_EXCLUSIONS.get(domain, [])
            skip_note = f"，**排除**：{'、'.join(skip)}" if skip else ""
            print(f"  ▸ {domain}{skip_note}")
            for name in opened:
                print(f"      · {name:16} id={by_name[name].id}")
            if not opened:
                print("      （无）")

        print("\n【② 已开通板块的部门（现状）】")
        existing = {
            str(row.department_id)
            for row in db.scalars(
                select(DepartmentModule).where(
                    DepartmentModule.module_code == DEPT_WORKLOAD_MODULE,
                    DepartmentModule.active.is_(True),
                )
            ).all()
        }
        print(f"  当前已开通：{len(existing)} 个")
        for dept in departments:
            if str(dept.id) in existing:
                print(f"    · {dept.name}")

        # ── ③ 岗位口径 ─────────────────────────────────────
        print("\n【③ 岗位口径（部门 × 岗位 → 角色）】")
        print("  ⚠️ **指标留空**（用户要求后续再定）⇒ 非剪辑板块不会套用视频默认指标")

        plan_modules: list[tuple[str, str]] = []
        plan_policies: list[tuple[str, str, str]] = []

        for name in sorted(target_ids):
            dept = by_name[name]
            emps = list(
                db.scalars(
                    select(Employee).where(
                        Employee.department_id == dept.id,
                        Employee.employment_status == "active",
                        Employee.account_status == "enabled",
                    )
                ).all()
            )
            if not emps:
                continue
            if str(dept.id) not in existing:
                plan_modules.append((str(dept.id), name))

            positions: dict[str, str] = {}
            for emp in emps:
                pos = (emp.position or "").strip()
                if not pos:
                    continue
                positions.setdefault(pos, role_for_position(pos))

            for pos, role in sorted(positions.items()):
                if role == ROLE_NONE:
                    # ⚠️ **不参与 ⇒ 不写行**（默认关闭）
                    continue
                plan_policies.append((str(dept.id), pos, role))
                print(f"  · {name:16} / {pos:14} → {role}")

        if skipped_names:
            print("\n【④ 故意排除的部门】")
            for name in sorted(skipped_names):
                print(f"  · {name:16}（已有专属板块，不重复开）")

        # ── ④ 写库 ─────────────────────────────────────────
        if not args.confirm:
            print("\n" + "=" * 74)
            print("结论：🔍 体检完成（**未写库**）")
            print(f"  将开通板块的部门：{len(plan_modules)} 个")
            print(f"  将配置岗位口径：{len(plan_policies)} 条")
            print("  实际开通请加 --confirm：")
            print("      python backend/scripts/seed_department_workload.py --confirm")
            print("=" * 74)
            return 0

        # 板块开通
        for dept_id, name in plan_modules:
            row = db.scalars(
                select(DepartmentModule).where(
                    DepartmentModule.department_id == dept_id,
                    DepartmentModule.module_code == DEPT_WORKLOAD_MODULE,
                )
            ).first()
            if row is None:
                db.add(
                    DepartmentModule(
                        department_id=dept_id,
                        module_code=DEPT_WORKLOAD_MODULE,
                        active=True,
                        note="2026-11-25 按飞书真实架构开通",
                    )
                )
                print(f"  ✅ 开通板块：{name}")
            elif not row.active:
                row.active = True
                print(f"  ✅ 启用板块：{name}")
            else:
                print(f"  ➖ 已开通：{name}")

        # 岗位口径
        for dept_id, position, role in plan_policies:
            row = db.scalars(
                select(PanWorkloadPolicy).where(
                    PanWorkloadPolicy.department_id == dept_id,
                    PanWorkloadPolicy.position == position,
                )
            ).first()
            if row is None:
                db.add(
                    PanWorkloadPolicy(
                        department_id=dept_id,
                        position=position,
                        role=role,
                        # ⚠️ **指标留空** —— 后续业务定了再补
                        metric_codes="",
                        active=True,
                        note="2026-11-25 按真实岗位开通（指标待定）",
                    )
                )
                print(f"  ✅ 岗位口径：{position} → {role}")
            elif row.role != role or not row.active:
                row.role = role
                row.active = True
                print(f"  ✅ 更新岗位口径：{position} → {role}")
            else:
                print(f"  ➖ 岗位口径已存在：{position} → {role}")

        db.commit()

        print("\n" + "=" * 74)
        print("✅ 已开通")
        print(f"  板块：{DEPT_WORKLOAD_MODULE}")
        print(f"  部门：{len(plan_modules)} 个")
        print(f"  岗位口径：{len(plan_policies)} 条")
        print("\n  ⚠️ **指标是空的** ⇒ 卡片暂时没有填报项")
        print("     业务定了「各岗位填什么」后，配置 ``metric_codes`` 即可生效。")
        print("=" * 74)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
