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

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.db import get_db, get_sessionmaker
from backend.app.dependencies import get_current_user
from backend.app.domain.enums import PlanStatus
from backend.app.domain.models import Employee, MonthPlan
from backend.app.domain.plan_models import PlanApproval
from backend.app.main import app
from backend.app.services.access_control import build_access_context

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

plan = MonthPlan(
    owner_id=owner_id,
    plan_month="2099-07",
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
