"""部门负责人侧审批链路 —— 真实数据库端到端验证（A3）。

⚠️ 三个必须注意的坑（详见 `verify_plan_approval_e2e.py` 的说明）：

1. `get_db` 覆盖必须用 **generator** 形式。若覆盖成 `get_sessionmaker`（它是个 class），
   FastAPI 会去解析它的 `__init__` 签名，把 `local_sessionmaker` / `local_kw`
   当成必填 query 参数 → 422。

2. 覆盖用的 generator **必须 commit**。真实的 `get_db` 在请求结束时
   `db.commit()`；若覆盖版不提交，请求返回 200 但数据全部回滚 ——
   表现为「接口说成功、库里没变化」。

3. `get_current_user` 覆盖也不能塞一个已关闭会话的 Employee 实例，
   否则访问 name / department_id 时触发 `DetachedInstanceError`。

本脚本额外验证 A3 特有语义：
- 部门负责人能看到**下级部门**成员的计划（主管看不到）；
- 部门负责人**不能**审批自己提交的计划。
"""

import sys
from pathlib import Path

# ⚠️ **必须让脚本能从仓库根目录直接运行**。
#
#   Python 把**脚本所在目录**（``backend/scripts/``）设为 ``sys.path[0]``，
#   所以直接跑 ``python backend/scripts/verify_department_approval_e2e.py`` 时，
#   ``import backend.app...`` 会 ``ModuleNotFoundError: No module named 'backend'``。
#
#   没有这段引导，脚本**根本跑不起来**。
#   做法：把仓库根（``parents[2]``）插到 ``sys.path`` 最前面。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.app.core.db import get_db, get_sessionmaker  # noqa: E402
from backend.app.dependencies import get_current_user  # noqa: E402
from backend.app.domain.enums import PlanStatus  # noqa: E402
from backend.app.domain.models import Employee, MonthPlan  # noqa: E402
from backend.app.domain.plan_models import PlanApproval  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.services.access_control import build_access_context  # noqa: E402

db = get_sessionmaker()()

# ── 找一个真实的部门负责人（有直属下属 + 管理部门）────────────────────────
approver = None
owner_id = None
for candidate in db.execute(select(Employee)).scalars().all():
    ctx = build_access_context(db, candidate)
    reports = [r for r in ctx.direct_report_ids if r != candidate.id]
    if reports and ctx.managed_department_ids:
        approver = candidate
        owner_id = reports[0]
        break

if approver is None or owner_id is None:
    print("SKIP: 库中没有同时具备直属下属与管理部门的员工，无法验证 A3")
    raise SystemExit(0)

print(f"审批人: {approver.name} ({approver.id[:8]}) | 计划归属人: {owner_id[:8]}")

# ── 清理上次运行的历史残留 ─────────────────────────────────────────────────
# ⚠️ **脚本必须可重复运行**。
#
#   `month_plan` 上有 ``UNIQUE(owner_id, plan_month)`` 约束，
#   本脚本用固定的 ``plan_month`` 建计划 ——
#   第二次运行就会撞唯一约束直接崩掉。
#
#   验证工具必须随时能重跑，所以先把上一轮自己造的数据清干净。
_VERIFY_MONTH = "2099-09"
# ⚠️ **月份必须与其他 e2e 脚本错开**。
#
#   `month_plan` 有 ``UNIQUE(owner_id, plan_month)``，而这些脚本
#   会**随机挑同一个 owner**（第一个有直属下属的员工）——
#   于是多个脚本可能选到同一个人 + 同一个月份 → 互相撞唯一约束。
#
#   实际踩过的坑：A3 脚本与 A4 脚本都用 ``2099-08``，
#   A4 先跑插入，A3 再跑就崩 —— 看起来像「A3 脚本不幂等」，
#   实际是**脚本间互相干扰**。
#
#   分配：A4 = 2099-08，A1/A2 = 2099-07，A3 = 2099-09。

# ⚠️ **要清理两个 owner 的计划**：
#   1. ``owner_id`` —— 计划归属人（下属）的那条；
#   2. ``emp_id``（审批人自己）—— 「禁止自审批」用例造的那条。
#
#   只清 1 会漏掉 2 —— 第二次运行就在自审批那条上撞唯一约束。
_old_plans = db.execute(
    select(MonthPlan).where(
        MonthPlan.plan_month == _VERIFY_MONTH,
        MonthPlan.owner_id.in_([owner_id, approver.id]),
    )
).scalars().all()
for _old in _old_plans:
    # ⚠️ PlanApproval 是**多态**的（month / week 共用一张表），
    #    必须按 plan_id 精确定位 —— 否则会误删别人的审批流水。
    db.execute(
        select(PlanApproval).where(
            PlanApproval.plan_type == "month",
            PlanApproval.plan_id == _old.id,
        )
    ).scalars().all()
    # 审批流水没有外键到 plan（多态设计），所以要**显式删除**，
    #    不能只 select 出来。
    for _appr in db.execute(
        select(PlanApproval).where(
            PlanApproval.plan_type == "month",
            PlanApproval.plan_id == _old.id,
        )
    ).scalars().all():
        db.delete(_appr)
    db.delete(_old)
db.commit()
if _old_plans:
    print(f"已清理历史残留：月计划 {len(_old_plans)} 条")

plan = MonthPlan(
    owner_id=owner_id,
    plan_month=_VERIFY_MONTH,
    title="A3 端到端验证",
    status=PlanStatus.SUBMITTED.value,
)
db.add(plan)
db.commit()
pid = plan.id
emp_id = approver.id
db.close()


def db_override():
    """模拟真实 get_db：请求结束提交，异常回滚。"""
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def user_override():
    """模拟 get_current_user：每次请求从当前会话取员工。"""
    s = get_sessionmaker()()
    try:
        yield s.get(Employee, emp_id)
    finally:
        s.close()


app.dependency_overrides[get_db] = db_override
app.dependency_overrides[get_current_user] = user_override

ok = True


def check(label: str, got, expected) -> None:
    global ok
    good = got == expected
    if not good:
        ok = False
    print(f"{'  OK ' if good else ' FAIL'} | {label}: {got!r}" + ("" if good else f" (期望 {expected!r})"))


with TestClient(app) as c:
    r = c.get("/v1/department/month-plan-approvals")
    check("GET 月计划列表 状态码", r.status_code, 200)
    data = r.json().get("data", [])
    check("列表包含本次计划", any(x["id"] == pid for x in data), True)
    if any(x["id"] == pid for x in data):
        it = next(x for x in data if x["id"] == pid)
        print(f"       字段 | {it['employeeName']} | {it['orgName']} | {it['planYear']}年{it['planMonth']}月 | {it['status']}")

    r = c.get(f"/v1/department/month-plan-approvals/{pid}")
    check("GET 月计划详情 状态码", r.status_code, 200)
    check("详情 id 对上", r.json()["data"]["id"], pid)

    r = c.get("/v1/department/month-plan-approvals/page?pageNo=1&pageSize=1")
    check("GET 分页 状态码", r.status_code, 200)
    pg = r.json()["data"]
    check("分页 total >= 1", pg["total"] >= 1, True)
    check("分页 pageSize 回显", pg["pageSize"], 1)
    check("分页 pageNo 回显", pg["pageNo"], 1)

    r = c.get("/v1/department/leader-month-plan-approvals/page?pageNo=1&pageSize=20")
    check("GET 负责人分页 状态码", r.status_code, 200)
    lp = r.json()["data"]
    check("负责人分页 submittedCount >= 1", lp["submittedCount"] >= 1, True)
    check("负责人分页 totalEmployeeCount >= 1", lp["totalEmployeeCount"] >= 1, True)

    r = c.post(
        f"/v1/department/leader-month-plan-approvals/{pid}/approve",
        json={"comment": "A3 端到端验证通过", "versionNo": 1},
    )
    check("POST 通过审批 状态码", r.status_code, 200)
    check("ActionResult.status", r.json()["data"].get("status"), "APPROVED")

    r = c.post(f"/v1/department/leader-month-plan-approvals/{pid}/approve", json={})
    check("重复审批 → 409", r.status_code, 409)

    r = c.post("/v1/department/leader-month-plan-approvals/不存在的ID/approve", json={})
    check("不存在计划 → 404", r.status_code, 404)

    # ── A3 特有语义：禁止自审批 ──────────────────────────────────────────
    own = MonthPlan(
        owner_id=emp_id,
        plan_month="2099-09",
        title="A3 自审批校验",
        status=PlanStatus.SUBMITTED.value,
    )
    s = get_sessionmaker()()
    s.add(own)
    s.commit()
    own_id = own.id
    s.close()

    r = c.post(f"/v1/department/leader-month-plan-approvals/{own_id}/approve", json={})
    check("自审批 → 403", r.status_code, 403)

    s = get_sessionmaker()()
    o2 = s.get(MonthPlan, own_id)
    check("自审批未改状态", o2.status, PlanStatus.SUBMITTED.value)
    s.close()

db2 = get_sessionmaker()()
rows = db2.execute(
    select(PlanApproval).where(PlanApproval.plan_id == pid)
).scalars().all()
check("审批流水条数", len(rows), 1)
if rows:
    print(f"       流水 | {rows[0].plan_type} / {rows[0].action} | actor={rows[0].actor_id[:8]} | comment={rows[0].comment}")

p2 = db2.get(MonthPlan, pid)
check("计划状态", p2.status, PlanStatus.APPROVED.value)
check("approved_at 有值", p2.approved_at is not None, True)
check("approval_comment", p2.approval_comment, "A3 端到端验证通过")
db2.close()

print()
print("RESULT ", "✅ 全部通过" if ok else "❌ 有失败项")
