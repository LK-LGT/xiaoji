"""端到端验证脚本 —— 在真实数据库上跑 A1/A2 审批链路。

⚠️ 两个必须注意的坑（本次验证踩到的）：

1. `get_db` 覆盖必须用 **generator** 形式。若覆盖成 `get_sessionmaker`（一个 class），
   FastAPI 会去解析它的 `__init__` 签名，把 `local_sessionmaker` / `local_kw`
   当成必填 query 参数 → 422。

2. 覆盖用的 generator **必须 commit**。真实的 `get_db` 在请求结束时
   `db.commit()`；若覆盖版不提交，请求返回 200 但数据全部回滚 ——
   表现为「接口说成功、库里没变化」。

3. `get_current_user` 覆盖也不能塞一个已关闭会话的 Employee 实例，
   否则访问 name / department_id 时触发 `DetachedInstanceError`。
"""

import sys
from pathlib import Path

# ⚠️ **必须让脚本能从仓库根目录直接运行**。
#
#   Python 把**脚本所在目录**（``backend/scripts/``）设为 ``sys.path[0]``，
#   所以直接跑 ``python backend/scripts/verify_plan_approval_e2e.py`` 时，
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

# 审批人必须是**有管理范围**的人（直属主管 / 部门负责人）。
# 普通员工的管理范围为空，待批列表理应为空 —— 那是正确行为，不是 bug。
approver = None
owner_id = None
for candidate in db.execute(select(Employee)).scalars().all():
    ctx = build_access_context(db, candidate)
    reports = [r for r in ctx.direct_report_ids if r != candidate.id]
    if reports:
        approver = candidate
        owner_id = reports[0]
        break

if approver is None or owner_id is None:
    print("SKIP: 库中没有带直属下属的员工，无法验证审批链路")
    raise SystemExit(0)

print(f"审批人: {approver.name} ({approver.id[:8]}) | 计划归属人: {owner_id[:8]}")

# ── 清理上次运行的历史残留 ─────────────────────────────────────────────────
# ⚠️ **脚本必须可重复运行**。
#
#   `month_plan` 上有 ``UNIQUE(owner_id, plan_month)`` 约束，
#   本脚本用固定的 ``plan_month="2099-07"`` 建计划 ——
#   第二次运行就会撞唯一约束直接崩掉。
#
#   验证工具必须随时能重跑，所以先把上一轮自己造的数据清干净。
_VERIFY_MONTH = "2099-07"

_old_plans = db.execute(
    select(MonthPlan).where(
        MonthPlan.owner_id == owner_id,
        MonthPlan.plan_month == _VERIFY_MONTH,
    )
).scalars().all()
for _old in _old_plans:
    # ⚠️ PlanApproval 是**多态**的（month / week 共用一张表），
    #    必须按 plan_id 精确定位 —— 否则会误删别人的审批流水。
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
    title="端到端验证",
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
with TestClient(app) as c:
    r = c.get("/v1/leader/month-plan-approvals")
    data = r.json().get("data", [])
    hit = [x for x in data if x["id"] == pid]
    print("LIST   ", r.status_code, "| 条数", len(data), "| 含本次计划", bool(hit))
    if hit:
        it = hit[0]
        print(
            "  字段 |", it["employeeName"], "|", it["orgName"], "|",
            it["planYear"], it["planMonth"], "|", it["status"],
        )
    else:
        ok = False
        print("  !! 待批列表里找不到本次计划（可见范围问题）")

    d = c.get(f"/v1/leader/month-plan-approvals/{pid}")
    print("DETAIL ", d.status_code, "| id 对上", d.json()["data"]["plan"]["id"] == pid)

    p = c.post(
        f"/v1/leader/month-plan-approvals/{pid}/approve",
        json={"comment": "端到端验证通过", "versionNo": 1},
    )
    print("APPROVE", p.status_code, "|", p.json()["data"].get("status"))
    if p.status_code != 200:
        ok = False

    again = c.post(f"/v1/leader/month-plan-approvals/{pid}/approve", json={"comment": "再来一次"})
    print("REPEAT ", again.status_code, "(预期 409)")
    if again.status_code != 409:
        ok = False

    bad = c.post("/v1/leader/month-plan-approvals/不存在的ID/approve", json={})
    print("404    ", bad.status_code, "(预期 404)")
    if bad.status_code != 404:
        ok = False

db2 = get_sessionmaker()()
rows = db2.execute(select(PlanApproval)).scalars().all()
print("流水   | 行数", len(rows))
if rows:
    print("  ", rows[0].plan_type, rows[0].action, "| comment:", rows[0].comment)
else:
    ok = False

p2 = db2.get(MonthPlan, pid)
print(
    "落库   | 状态", p2.status, "| approved_at 有值", p2.approved_at is not None,
    "| comment", p2.approval_comment,
)
if p2.status != PlanStatus.APPROVED.value:
    ok = False
db2.close()

print("RESULT ", "✅ 全部通过" if ok else "❌ 有失败项")
