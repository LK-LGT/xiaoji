"""★ **权限隔离** E2E 验证（CI-5 阶段 3 第三步）—— 真实库跑 V1–V8。

⚠️ **为什么要有这个脚本**
------------------------------------------------------------------
``docs/18-E2E验收清单-绩效闭环.md`` §5 列了 8 条「权限与隔离」规则（V1–V8），
核心是一条业务规则：

    「管理员不是万能模拟账号」——
    系统管理员是**真实员工账号**，只是被授予了【绩效配置管理员】权限。
    ⇒ 它的**数据范围**按真实身份（部门/汇报线）判定，
    **不因是管理员就放行**。
    ⇒ 而 **HR 可以跨部门**看绩效数据。

⚠️ **这条规则历史上真的被违反过**
------------------------------------------------------------------
2026-11-24 实测：管理员账号调 ``GET /v1/baidu/workload/summary``
返回**全公司所有剪辑**的数据（巫宜珊 / 佟瑶 / 李婵雨 …），
而该管理员在人事行政部，与短视频部**无任何汇报线关系**。

⇒ 修复方式是把「谁是 HR」「谁是系统管理员」抽到
``services/hr_admin_roles.py``，并让 ``services/pan_workload_scope.py``
成为**数据范围的唯一事实源**。

⚠️ **但「抽成共享服务」本身也会漂移**
------------------------------------------------------------------
``pan_workload.py``（剪辑板块）与 ``dept_workload.py``（部门板块）
是两个 router，**各自有一份** ``_scope_employee_ids``。

⇒ 其中一份写成了 ``if is_hr(...) or is_system_admin(...): return None``
   ⇒ **管理员在部门板块能看全公司，在剪辑板块不能**
   ⇒ **同一份业务规则，两个板块两种行为** —— 这就是「照抄两份实现」的经典后果。

⚠️ **本脚本就是来守这条的**
------------------------------------------------------------------
V4 / V6 会**同时**打两个板块的端点
⇒ 一旦两边行为不一致，**必挂**。

⚠️ **V7 / V8（菜单可见性）不在本脚本范围**
------------------------------------------------------------------
「侧边栏看不到『剪辑工作量』」是**前端**行为（``hasModule`` 闸门），
本脚本是后端 API 级验证 ⇒ 已在
``frontend/tests/module-submodule-workspace-stability.test.ts`` 等前端测试覆盖。

用法::

    python backend/scripts/verify_permission_isolation_e2e.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.core.db import get_db, get_sessionmaker  # noqa: E402
from backend.app.dependencies import get_current_user  # noqa: E402
from backend.app.domain.models import (  # noqa: E402
    Department,
    Employee,
    HrDepartmentConfig,
    SystemAdmin,
)
from backend.app.domain.pan_workload_models import (  # noqa: E402
    DepartmentModule,
    PanManualMetric,
    PanWorkloadDaily,
    PanWorkloadPolicy,
    PanWorkloadReview,
)
from backend.app.main import app  # noqa: E402

TAG = "E2E_PERM"

RUN_SUFFIX = uuid.uuid4().hex[:8]

HR_DEPT_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_H"
ADMIN_DEPT_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_A"
DEPT_A_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_A1"
DEPT_B_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_B1"
#: ⚠️ **V7 用** —— 这个部门**不开通**任何板块 ⇒ 菜单里不该有「剪辑工作量」
DEPT_C_ID = f"{TAG}_{RUN_SUFFIX}_DEPT_C1"

HR_ID = f"{TAG}_{RUN_SUFFIX}_HR"
ADMIN_ID = f"{TAG}_{RUN_SUFFIX}_ADMIN"
LEADER_A_ID = f"{TAG}_{RUN_SUFFIX}_LEADER_A"
EMP_A_ID = f"{TAG}_{RUN_SUFFIX}_EMP_A"
EMP_B_ID = f"{TAG}_{RUN_SUFFIX}_EMP_B"
EMP_C_ID = f"{TAG}_{RUN_SUFFIX}_EMP_C"

FAILED = False
PASSED: list[str] = []


def log(stage: str, message: str, ok: bool = True) -> None:
    global FAILED
    print(f"{'✔' if ok else '✖'} [{stage}] {message}")
    if ok:
        PASSED.append(f"[{stage}] {message}")
    else:
        FAILED = True


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


# ═══════════════════════════════════════════════════════════════════════════
# 准备：真实的组织结构
# ═══════════════════════════════════════════════════════════════════════════


def build_org(db) -> dict[str, Employee]:
    """搭一个**真实的多部门组织**。

    ⚠️ **必须有两个互不相干的部门**（A / B）
    ⇒ 否则「跨部门不可见」根本无从验证（数据都在同一个部门里）。

    ⚠️ **管理员放在第三个部门**（ADMIN_DEPT_ID）
    ⇒ 与 A / B **都没有汇报线关系**
    ⇒ 这才能验证「管理员不因是管理员就放行」。
    """
    db.add_all(
        [
            Department(id=HR_DEPT_ID, name=f"{TAG}人事"),
            Department(id=ADMIN_DEPT_ID, name=f"{TAG}行政"),
            Department(id=DEPT_A_ID, name=f"{TAG}短视频A", leader_id=LEADER_A_ID),
            Department(id=DEPT_B_ID, name=f"{TAG}仓储B"),
            Department(id=DEPT_C_ID, name=f"{TAG}客服C"),
        ]
    )
    db.flush()

    people = {
        "hr": Employee(id=HR_ID, name=f"{TAG}HR", department_id=HR_DEPT_ID, position="HR"),
        "admin": Employee(
            id=ADMIN_ID, name=f"{TAG}管理员", department_id=ADMIN_DEPT_ID, position="行政"
        ),
        "leader_a": Employee(
            id=LEADER_A_ID, name=f"{TAG}主管A", department_id=DEPT_A_ID, position="负责人"
        ),
        "emp_a": Employee(
            id=EMP_A_ID,
            name=f"{TAG}员工A",
            department_id=DEPT_A_ID,
            position="剪辑",
            manager_id=LEADER_A_ID,
        ),
        "emp_b": Employee(
            id=EMP_B_ID,
            name=f"{TAG}员工B",
            department_id=DEPT_B_ID,
            position="剪辑",
        ),
        # ⚠️ **C 部门员工** —— 该部门**不开通**任何板块（V7 用）
        "emp_c": Employee(
            id=EMP_C_ID,
            name=f"{TAG}员工C",
            department_id=DEPT_C_ID,
            position="客服",
        ),
    }
    db.add_all(people.values())

    # HR 部门配置 ⇒ 该部门成员是 HR
    db.add(HrDepartmentConfig(department_id=HR_DEPT_ID, active=True))

    # 系统管理员授权（注意：按 employee_id 关联，主键是另一个 UUID）
    db.add(SystemAdmin(id=f"{TAG}_{RUN_SUFFIX}_SA", employee_id=ADMIN_ID, active=True))

    # ⚠️⚠️ **必须开通板块，否则端点直接 403 —— 连数据都取不到**
    #
    # ⚠️ **这是本脚本第一版最大的坑**
    #   第一版没开通板块 ⇒ 所有请求都返回 403（板块守卫拦的）
    #   ⇒ 一看断言全是空集合，差点误判成「权限逻辑坏了」。
    #   ⇒ **403 有两种完全不同的含义**：
    #      ① 板块没开通（``has_module`` 守卫）
    #      ② 数据范围没权限（``scope_employee_ids`` 收敛）
    #   ⇒ 本脚本必须**先把板块开通**，否则根本测不到第②种。
    #
    # ⚠️ **只给 A / B 开通** —— C 部门（V7 用）**不开通**
    #   ⇒ 「看不到入口」才有东西可验。
    for dept_id in (DEPT_A_ID, DEPT_B_ID):
        db.add(
            DepartmentModule(
                id=f"{TAG}_{RUN_SUFFIX}_MOD_{dept_id[-4:]}",
                department_id=dept_id,
                module_code="pan_workload",
                active=True,
            )
        )
        db.add(
            DepartmentModule(
                id=f"{TAG}_{RUN_SUFFIX}_DMOD_{dept_id[-4:]}",
                department_id=dept_id,
                module_code="dept_workload",
                active=True,
            )
        )

    # ⚠️ **岗位口径表** —— 没有它，岗位角色算成 ``none``
    # ⇒ 「员工只看自己」这类断言就无从谈起（因为压根进不去）。
    for dept_id in (DEPT_A_ID, DEPT_B_ID):
        db.add(
            PanWorkloadPolicy(
                id=f"{TAG}_{RUN_SUFFIX}_POL_{dept_id[-4:]}_EMP",
                department_id=dept_id,
                position="剪辑",
                role="reporter",
                metric_codes="video_completion_rate,video_retention_rate",
                active=True,
            )
        )
        db.add(
            PanWorkloadPolicy(
                id=f"{TAG}_{RUN_SUFFIX}_POL_{dept_id[-4:]}_LEAD",
                department_id=dept_id,
                position="负责人",
                role="reviewer",
                metric_codes="video_completion_rate,video_retention_rate",
                active=True,
            )
        )

    db.commit()
    return people


def seed_workload_data(db) -> None:
    """给 A 部门员工和 B 部门员工各造一份**工作量数据**。

    ⚠️ **两个部门都要有数据**
    ⇒ 否则「B 部门数据消失了」和「B 部门本来就没数据」分不清。
    """
    from datetime import date

    db.add_all(
        [
            PanWorkloadDaily(
                id=f"{TAG}_{RUN_SUFFIX}_DA",
                employee_id=EMP_A_ID,
                business_date=date(2099, 1, 10),
                total_count=6,
                file_count=6,
                scanned_count=6,
                excluded_count=0,
                marker_breakdown={},
                anomaly_count={},
                status="auto",
            ),
            PanWorkloadDaily(
                id=f"{TAG}_{RUN_SUFFIX}_DB",
                employee_id=EMP_B_ID,
                business_date=date(2099, 1, 10),
                total_count=3,
                file_count=3,
                scanned_count=3,
                excluded_count=0,
                marker_breakdown={},
                anomaly_count={},
                status="auto",
            ),
            PanManualMetric(
                id=f"{TAG}_{RUN_SUFFIX}_MA",
                employee_id=EMP_A_ID,
                metric_date=date(2099, 1, 11),
                metric_code="SHOTS",
                metric_value=10.0,
                scope="day",
                target_key="",
                related_file_ids=[],
                source="manual",
                created_by=EMP_A_ID,
            ),
            PanManualMetric(
                id=f"{TAG}_{RUN_SUFFIX}_MB",
                employee_id=EMP_B_ID,
                metric_date=date(2099, 1, 11),
                metric_code="SHOTS",
                metric_value=5.0,
                scope="day",
                target_key="",
                related_file_ids=[],
                source="manual",
                created_by=EMP_B_ID,
            ),
        ]
    )
    db.commit()


def install_overrides(people: dict[str, Employee], db) -> None:
    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: people["current"]


# ═══════════════════════════════════════════════════════════════════════════
# 断言辅助
# ═══════════════════════════════════════════════════════════════════════════


def _summary_employee_ids(client) -> set[str]:
    """从剪辑板块总览里取出**出现过的员工 ID 集合**。

    ⚠️ **必须把 HTTP 状态码暴露出来**
    403 有两种完全不同的含义（板块没开通 / 数据范围没权限），
    一律返回空集合会让人**误判成「权限逻辑坏了」**。
    """
    response = client.get("/v1/baidu/workload/summary")
    if response.status_code != 200:
        raise AssertionError(
            f"剪辑板块总览返回 {response.status_code}：{response.text[:200]}"
        )
    payload = response.json().get("data", response.json())
    employees = payload.get("employees") or []
    ids = set()
    for entry in employees:
        if isinstance(entry, dict):
            eid = entry.get("employeeId") or entry.get("employee_id") or entry.get("id")
            if eid:
                ids.add(str(eid))
    return ids


def _dept_metric_employee_ids(client) -> set[str]:
    """从部门板块的「我填过的指标」里取出员工 ID 集合。"""
    response = client.get("/v1/dept/workload/my-metrics")
    if response.status_code != 200:
        raise AssertionError(
            f"部门板块指标列表返回 {response.status_code}：{response.text[:200]}"
        )
    payload = response.json().get("data", response.json())
    items = payload.get("items") or []
    return {str(x.get("employeeId") or x.get("employee_id")) for x in items if isinstance(x, dict)}


# ═══════════════════════════════════════════════════════════════════════════
# V1–V8
# ═══════════════════════════════════════════════════════════════════════════


def run_v1_employee_cannot_see_others(client, people) -> None:
    """**V1 员工只看自己**。"""
    section("V1 · 员工只看自己")
    people["current"] = people["emp_a"]

    ids = _summary_employee_ids(client)
    log("V1", f"剪辑板块只看得到自己（{sorted(ids)}）", ids == {EMP_A_ID})

    ids = _dept_metric_employee_ids(client)
    log("V1", f"部门板块只看得到自己（{sorted(ids)}）", ids == {EMP_A_ID})


def run_v2_leader_cannot_see_other_dept(client, people) -> None:
    """**V2 主管看不到外部门员工**。"""
    section("V2 · 主管看不到外部门员工")
    people["current"] = people["leader_a"]

    ids = _summary_employee_ids(client)
    log("V2", f"剪辑板块含自己下属（{EMP_A_ID in ids}）", EMP_A_ID in ids)
    log("V2", f"剪辑板块不含外部门（{EMP_B_ID not in ids}）", EMP_B_ID not in ids)

    ids = _dept_metric_employee_ids(client)
    log("V2", f"部门板块含自己下属（{EMP_A_ID in ids}）", EMP_A_ID in ids)
    log("V2", f"部门板块不含外部门（{EMP_B_ID not in ids}）", EMP_B_ID not in ids)


def run_v3_leader_cannot_resolve_other_dept(client, people, db) -> None:
    """**V3 主管不能裁定外部门工单**（双层：列表不返回 + 接口 403）。"""
    section("V3 · 主管不能裁定外部门工单")
    people["current"] = people["leader_a"]

    # 造一个 B 部门员工的工单
    from datetime import date

    review = PanWorkloadReview(
        id=f"{TAG}_{RUN_SUFFIX}_REVIEW_B",
        employee_id=EMP_B_ID,
        business_date=date(2099, 1, 12),
        status="pending",
        scope="day",
        claimed_count=8,
        system_count=6,
        final_count=6,
        reason="这天数不对",
        resolution="",
        related_file_ids=[],
        daily_snapshot={},
        created_by=EMP_B_ID,
    )
    db.add(review)
    db.commit()

    # ① 列表里**不出现**
    response = client.get("/v1/baidu/workload/reviews")
    listed = []
    if response.status_code == 200:
        payload = response.json().get("data", response.json())
        listed = payload.get("items") or payload.get("reviews") or []
    listed_ids = {str(x.get("employeeId") or x.get("employee_id")) for x in listed if isinstance(x, dict)}
    log("V3", f"列表不含外部门工单（{EMP_B_ID not in listed_ids}）", EMP_B_ID not in listed_ids)

    # ② 直接构造请求 ⇒ 403
    response = client.post(
        f"/v1/baidu/workload/reviews/{review.id}/resolve",
        json={"resolution": "approved", "final_count": 99, "resolution_note": "越权裁定"},
    )
    log("V3", f"裁定外部门工单被拒（{response.status_code}）", response.status_code == 403)

    # ③ 确认数据**没被改**（403 必须是「拒绝」而不是「改了一半」）
    db.expire_all()
    after = db.get(PanWorkloadReview, review.id)
    log("V3", f"工单数据未被篡改（final_count = {after.final_count}）", after.final_count == 6)


def run_v4_admin_has_no_cross_dept_scope(client, people) -> None:
    """**V4 系统管理员没有跨部门数据范围** ⭐ 本轮规则的核心。

    ⚠️ **两个板块都要验**
    管理员是**真实员工账号** ⇒ 数据范围按真实身份判定。
    ⇒ 「剪辑板块看不到」还不够 —— **部门板块也必须看不到**，
      否则就是「同一规则两个行为」（本脚本就是来守这个的）。
    """
    section("V4 · 系统管理员没有跨部门数据范围 ⭐")
    people["current"] = people["admin"]

    ids = _summary_employee_ids(client)
    log("V4", f"剪辑板块不含外部门员工（{EMP_A_ID not in ids}）", EMP_A_ID not in ids)
    log("V4", f"剪辑板块不含另一部门员工（{EMP_B_ID not in ids}）", EMP_B_ID not in ids)

    ids = _dept_metric_employee_ids(client)
    log("V4", f"部门板块不含外部门员工（{EMP_A_ID not in ids}）", EMP_A_ID not in ids)
    log("V4", f"部门板块不含另一部门员工（{EMP_B_ID not in ids}）", EMP_B_ID not in ids)


def run_v5_admin_can_manage_config(client, people) -> None:
    """**V5 管理员仍能做配置动作**（否则会死锁）。

    ⚠️ **这是 V4 的另一半** ——
    「数据范围收窄」不等于「把管理员阉了」。
    ⇒ 管理员要能看目录绑定（配置数据），只是看不到业务数据。
    """
    section("V5 · 管理员仍能做配置动作")
    people["current"] = people["admin"]

    response = client.get("/v1/baidu/workload/bindings")
    log("V5", f"管理员可读目录绑定（{response.status_code}）", response.status_code == 200)


def run_v6_hr_can_see_all(client, people) -> None:
    """**V6 HR 可以跨部门看**（与 V4 正好相反，构成对照）。"""
    section("V6 · HR 可跨部门看 ⭐")
    people["current"] = people["hr"]

    ids = _summary_employee_ids(client)
    log("V6", f"剪辑板块含 A 部门（{EMP_A_ID in ids}）", EMP_A_ID in ids)
    log("V6", f"剪辑板块含 B 部门（{EMP_B_ID in ids}）", EMP_B_ID in ids)

    ids = _dept_metric_employee_ids(client)
    log("V6", f"部门板块含 A 部门（{EMP_A_ID in ids}）", EMP_A_ID in ids)
    log("V6", f"部门板块含 B 部门（{EMP_B_ID in ids}）", EMP_B_ID in ids)


def run_v7_v8_module_visibility(db, people) -> None:
    """**V7 / V8 菜单可见性** —— 后端「下发菜单」口径。

    ⚠️ **真正的闸门有两道**
    ① 后端 ``build_module_menu`` 决定**下发哪些板块 → 子模块**
    ② 前端 ``hasModule`` 再拦一道（直接敲 URL 进不去）

    ⇒ 本脚本验第①道：**没开通板块的部门，后端根本不下发**。
    ⇒ 第②道由前端测试覆盖
    （``frontend/tests/department-module-isolation.test.ts``）。

    ⚠️ **角色维度也要过**（``module_role_for_employee``）
    岗位角色为 ``none`` ⇒ 子模块**不下发**
    ⇒ 同样是「看得见点进去空数据」的残留，必须守住。
    """
    section("V7 / V8 · 菜单下发口径")
    from backend.app.services.department_modules import (
        PAN_WORKLOAD_MODULE,
        build_module_menu,
        module_role_for_employee,
    )

    def module_codes(employee) -> set[str]:
        """这个员工**能看到的板块 / 子模块代码集合**。

        ⚠️ **菜单结构是两级**：``[{code, label, role, children: [...]}]``
        ⇒ 板块代码在**外层**，子模块代码在 ``children`` 里。
        ⚠️ **子模块还带权限串过滤** ⇒ 权限串必须传对，
        否则会误判成「板块没开通」。
        """
        menu = build_module_menu(db, employee, permissions=["pan:workload:view"])
        codes: set[str] = set()
        for group in menu or []:
            code = group.get("code")
            if code:
                codes.add(str(code))
            for item in group.get("children") or []:
                child = item.get("code")
                if child:
                    codes.add(str(child))
        return codes

    # V8：短视频部（开通了板块）⇒ 能看到「剪辑工作量」
    codes = module_codes(people["emp_a"])
    log("V8", f"短视频员工能看到剪辑工作量（{PAN_WORKLOAD_MODULE in codes}）", PAN_WORKLOAD_MODULE in codes)

    # V7：外部门（没开通板块）⇒ 看不到
    # ⚠️ 管理员/HR 例外（他们要配置，不能被挡住）⇒ 这里用 **C 部门**普通员工验
    codes = module_codes(people["emp_c"])
    log(
        "V7",
        f"未开通部门的员工看不到剪辑工作量（{PAN_WORKLOAD_MODULE not in codes}）",
        PAN_WORKLOAD_MODULE not in codes,
    )

    # ⚠️ **角色维度**：岗位角色为 none ⇒ 子模块不下发
    # C 部门「客服」岗位**没有**口径配置行 ⇒ 角色算成 none
    role = module_role_for_employee(db, people["emp_c"], PAN_WORKLOAD_MODULE)
    log("V7/V8", f"未配置岗位的角色 = {role}（应为 none）", role == "none")

    role = module_role_for_employee(db, people["emp_a"], PAN_WORKLOAD_MODULE)
    log("V7/V8", f"短视频剪辑岗位角色 = {role}", role in ("reporter", "reviewer"))


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    global db
    db = get_sessionmaker()()

    print("═" * 64)
    print("权限隔离 E2E 验证 —— V1–V8")
    print("═" * 64)

    people = build_org(db)
    seed_workload_data(db)
    install_overrides(people, db)
    client = TestClient(app)

    run_v1_employee_cannot_see_others(client, people)
    run_v2_leader_cannot_see_other_dept(client, people)
    run_v3_leader_cannot_resolve_other_dept(client, people, db)
    run_v4_admin_has_no_cross_dept_scope(client, people)
    run_v5_admin_can_manage_config(client, people)
    run_v6_hr_can_see_all(client, people)
    run_v7_v8_module_visibility(db, people)

    section("汇总")
    print("\n" + "═" * 64)
    if FAILED:
        print(f"结果：失败（通过 {len(PASSED)} 项，存在失败项）")
        print("═" * 64)
        return 1
    print(f"结果：全部通过（{len(PASSED)} 项）")
    print("═" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
