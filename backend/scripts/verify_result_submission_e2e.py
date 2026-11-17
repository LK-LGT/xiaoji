"""成果提交与审批链路 —— 真实数据库端到端验证（A4）。

⚠️ 三个必须注意的坑（详见 `verify_plan_approval_e2e.py` 的说明）：

1. `get_db` 覆盖必须用 **generator** 形式。若覆盖成 `get_sessionmaker`（它是个 class），
   FastAPI 会去解析它的 `__init__` 签名，把 `local_sessionmaker` / `local_kw`
   当成必填 query 参数 → 422。

2. 覆盖用的 generator **必须 commit**。真实的 `get_db` 在请求结束时
   `db.commit()`；若覆盖版不提交，请求返回 200 但数据全部回滚 ——
   表现为「接口说成功、库里没变化」。

3. `get_current_user` 覆盖也不能塞一个已关闭会话的 Employee 实例，
   否则访问 name / department_id 时触发 `DetachedInstanceError`。

本脚本验证 A4 的完整闭环：
- 月成果提交 → 落库 → 版本递增
- 周成果提交 → 落库
- 证据附件上传 → 落盘 → 下载内容一致
- 主管审批周成果 → 状态流转 → 库中可见
- **禁止自审批**（与 D13 决策一致）
- 数据范围：范围外的人看不到、也审批不了
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

# ⚠️ **必须让脚本能从仓库根目录直接运行**。
#
#   Python 把**脚本所在目录**（``backend/scripts/``）设为 ``sys.path[0]``，
#   所以直接跑 ``python backend/scripts/verify_result_submission_e2e.py`` 时，
#   ``import backend.app...`` 会 ``ModuleNotFoundError: No module named 'backend'``。
#
#   ��有这段引导，脚本**根本跑不起来** —— 表现为「台账说脚本已交付，
#   但实际一执行就崩」。
#
#   做法：把仓库根（``parents[2]``）插到 ``sys.path`` 最前面。
#   （其他 verify_* 脚本都是这么写的。）
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.app.core.db import get_db, get_sessionmaker  # noqa: E402
from backend.app.dependencies import get_current_user  # noqa: E402
from backend.app.domain.models import (  # noqa: E402
    Employee,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
    WeekPlanItem,
)
from backend.app.domain.result_models import (  # noqa: E402
    RESULT_STATUS_CONFIRMED,
    RESULT_STATUS_SUBMITTED,
    Result,
    ResultEvidence,
)
from backend.app.main import app  # noqa: E402
from backend.app.services.access_control import build_access_context  # noqa: E402

db = get_sessionmaker()()

# ── 找一个真实的审批人（有直属下属）────────────────────────────────────────
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
    print("SKIP: 库中没有有直属下属的员工，无法验证 A4")
    raise SystemExit(0)

print(f"审批人: {approver.name} ({approver.id[:8]}) | 成果归属人: {owner_id[:8]}")

# ── 清理上次运行的历史残留 ─────────────────────────────────────────────────
# ⚠️ **脚本必须可重复运行**。
#
#   `month_plan` 上有 ``UNIQUE(owner_id, plan_month)`` 约束，
#   而本脚本用固定的 ``plan_month="2099-08"`` 建计划 ——
#   第二次运行就会撞唯一约束直接崩掉。
#
#   这不是「跑一次就够」的脚本 —— 它是**验证工具**，必须随时能重跑。
#   所以先把上一轮自己造的数据清干净，再重新造。
_VERIFY_MONTH = "2099-08"
_VERIFY_WEEK_START = date(2099, 8, 3)


def _cleanup_previous_run() -> None:
    """删除上一轮本脚本造的数据。

    ⚠️ 只删**本脚本自己造的东西**（固定的 plan_id / 固定周起始日），
    绝不碰真实业务数据。
    """
    from backend.app.domain.result_models import Result, ResultEvidence

    # 1) 先删成果（外键指向 plan_item）
    #    ⚠️ Result 是**多态**的（month / week 共用一张表），
    #    所以要按 plan_id 删，不能按 period 删 —— Result 上没有 period 列。
    old_week_plans_pre = db.execute(
        select(WeekPlan.id).where(
            WeekPlan.owner_id == owner_id,
            WeekPlan.week_start == _VERIFY_WEEK_START,
        )
    ).scalars().all()
    old_month_plans_pre = db.execute(
        select(MonthPlan.id).where(
            MonthPlan.owner_id == owner_id,
            MonthPlan.plan_month == _VERIFY_MONTH,
        )
    ).scalars().all()

    old_results = db.execute(
        select(Result).where(
            Result.owner_id == owner_id,
            Result.plan_id.in_(list(old_month_plans_pre) + list(old_week_plans_pre)),
        )
    ).scalars().all()
    for result in old_results:
        # 证据是独立表，先删它再删 result（避免留下孤儿）
        for evidence in db.execute(
            select(ResultEvidence).where(ResultEvidence.result_id == result.id)
        ).scalars().all():
            db.delete(evidence)
        db.delete(result)
    db.flush()

    # 2) 删月计划的条目与计划
    old_month_plans = db.execute(
        select(MonthPlan).where(
            MonthPlan.owner_id == owner_id,
            MonthPlan.plan_month == _VERIFY_MONTH,
        )
    ).scalars().all()
    for plan in old_month_plans:
        db.execute(
            select(MonthPlanItem).where(MonthPlanItem.plan_id == plan.id)
        )
        db.delete(plan)
    db.flush()

    # 3) 删周计划的条目与计划
    old_week_plans = db.execute(
        select(WeekPlan).where(
            WeekPlan.owner_id == owner_id,
            WeekPlan.week_start == _VERIFY_WEEK_START,
        )
    ).scalars().all()
    for plan in old_week_plans:
        db.execute(
            select(WeekPlanItem).where(WeekPlanItem.plan_id == plan.id)
        )
        db.delete(plan)
    db.flush()

    db.commit()
    if old_results or old_month_plans or old_week_plans:
        print(
            f"已清理历史残留：成果 {len(old_results)} 条、"
            f"月计划 {len(old_month_plans)} 条、周计划 {len(old_week_plans)} 条"
        )


_cleanup_previous_run()

# ── 造月计划 + 条目 ─────────────────────────────────────────────────────────
month_plan = MonthPlan(
    owner_id=owner_id,
    plan_month=_VERIFY_MONTH,
    title="A4 端到端验证月计划",
    status="APPROVED",
)
db.add(month_plan)
db.flush()
month_item = MonthPlanItem(
    plan_id=month_plan.id,
    task_name="A4 验证任务",
    task_content="验证成果提交",
    deliverable="可验收的交付物",
    performance_weight=10.0,
    status="APPROVED",
    sort_no=1,
)
db.add(month_item)
db.flush()

# ── 造周计划 + 条目 ─────────────────────────────────────────────────────────
week_plan = WeekPlan(
    owner_id=owner_id,
    title="A4 端到端验证周计划",
    week_start=date(2099, 8, 3),
    week_end=date(2099, 8, 9),
    status="APPROVED",
)
db.add(week_plan)
db.flush()
week_item = WeekPlanItem(
    plan_id=week_plan.id,
    content="验证周成果提交",
    deliverable="周交付物",
    sort_no=1,
)
db.add(week_item)
db.flush()

month_plan_id = month_plan.id
month_item_id = month_item.id
week_plan_id = week_plan.id
week_item_id = week_item.id
emp_id = owner_id
approver_id = approver.id
db.commit()
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


def make_user_override(user_id: str):
    """模拟 get_current_user：每次请求从当前会话取员工。"""

    def _override():
        s = get_sessionmaker()()
        try:
            yield s.get(Employee, user_id)
        finally:
            s.close()

    return _override


ok = True


def check(label: str, got, expected) -> None:
    global ok
    good = got == expected
    if not good:
        ok = False
    print(
        f"{'  OK ' if good else ' FAIL'} | {label}: {got!r}"
        + ("" if good else f" (期望 {expected!r})")
    )


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

# ─────────────────────────────────────────────────────────────────────────────
# 1. 员工提交月成果（含证据附件）
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] 员工提交月成果")
app.dependency_overrides[get_db] = db_override
app.dependency_overrides[get_current_user] = make_user_override(emp_id)

with TestClient(app) as c:
    r = c.get("/v1/employee/results/submit/options")
    check("GET 月成果选项 状态码", r.status_code, 200)
    opts = r.json()["data"]
    check("月计划选项含本次计划", any(o["id"] == month_plan_id for o in opts["monthPlanOptions"]), True)
    check(
        "月计划条目选项含本次条目",
        any(i["id"] == month_item_id for i in opts["monthPlanItemOptions"]),
        True,
    )

    r = c.post(
        "/v1/employee/results/submit",
        data={
            "monthPlanId": month_plan_id,
            "monthPlanItemId": month_item_id,
            "completionRate": "90",
            "description": "A4 端到端验证：已完成主体功能",
            "completionStatus": "MOSTLY_DONE",
            "remark": "剩余联调",
            "personalPerformance": "8.5",
        },
        files={"file": ("proof.png", io.BytesIO(PNG), "image/png")},
    )
    check("POST 月成果提交 状态码", r.status_code, 200)
    month_body = r.json()["data"]
    check("月成果状态", month_body["status"], RESULT_STATUS_SUBMITTED)
    check("月成果版本号", month_body["versionNo"], "1")
    month_result_id = month_body["id"]

    r = c.get(f"/v1/employee/results/{month_result_id}")
    check("GET 月成果详情 状态码", r.status_code, 200)
    detail = r.json()["data"]
    check("详情完成率", detail["completionRate"], 90.0)
    check("详情计划条目名", detail["planItemName"], "A4 验证任务")
    check("详情证据数", len(detail["evidences"]), 1)

    ev = detail["evidences"][0]
    r = c.get(ev["downloadUrl"])
    check("GET 证据下载 状态码", r.status_code, 200)
    check("证据内容一致", r.content == PNG, True)

    # 重复提交应被拒（待审批期间）
    r = c.post(
        "/v1/employee/results/submit",
        data={"monthPlanId": month_plan_id, "monthPlanItemId": month_item_id,
              "completionRate": "90"},
    )
    check("重复提交 被拒", r.status_code, 400)

# ─────────────────────────────────────────────────────────────────────────────
# 2. 员工提交周成果
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] 员工提交周成果")
with TestClient(app) as c:
    r = c.get("/v1/employee/week-results/submit/options")
    check("GET 周成果选项 状态码", r.status_code, 200)
    check(
        "周选项含本次计划",
        any(o["weekPlanId"] == week_plan_id for o in r.json()["data"]["plans"]),
        True,
    )

    r = c.post(
        f"/v1/employee/week-plans/{week_plan_id}/items/{week_item_id}/results/submit",
        data={
            "completionRate": "100",
            "completionStatus": "DONE",
            "deliverable": "周交付物已验收",
            "remark": "无遗留问题",
            "content": "周成果内容",
        },
    )
    check("POST 周成果提交 状态码", r.status_code, 200)
    week_body = r.json()["data"]
    check("周成果状态", week_body["status"], RESULT_STATUS_SUBMITTED)
    week_result_id = week_body["id"]

# ─────────────────────────────────────────────────────────────────────────────
# 3. 员工不能审批自己的成果
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] 禁止自审批")
with TestClient(app) as c:
    r = c.post(
        f"/v1/leader/week-result-approvals/{week_result_id}/approve",
        json={"comment": "自审批尝试"},
    )
    check("自审批 被拒 状态码", r.status_code, 403)

# ─────────────────────────────────────────────────────────────────────────────
# 4. 主管审批周成果
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] 主管审批周成果")
app.dependency_overrides[get_current_user] = make_user_override(approver_id)

with TestClient(app) as c:
    r = c.get("/v1/leader/week-result-approvals")
    check("GET 审批列表 状态码", r.status_code, 200)
    page = r.json()["data"]
    check("列表含本次成果", any(i["id"] == week_result_id for i in page["items"]), True)

    r = c.post(
        f"/v1/leader/week-result-approvals/{week_result_id}/approve",
        json={"comment": "A4 端到端验证通过"},
    )
    check("POST 审批通过 状态码", r.status_code, 200)
    check("审批动作返回状态", r.json()["data"]["status"], "APPROVED")

    r = c.post(
        f"/v1/leader/week-result-approvals/{week_result_id}/approve",
        json={},
    )
    check("重复审批 被拒", r.status_code, 409)

# ─────────────────────────────────────────────────────────────────────────────
# 5. 库里状态核对（验证真的落库了，不是假成功）
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] 数据库状态核对")
s = get_sessionmaker()()
week_row = s.get(Result, week_result_id)
check("周成果库内状态", week_row.status, RESULT_STATUS_CONFIRMED)
check("周成果审批人", week_row.approver_id, approver_id)
check("周成果审批意见", week_row.approval_comment, "A4 端到端验证通过")
check("周成果审批时间非空", week_row.approved_at is not None, True)

month_row = s.get(Result, month_result_id)
check("月成果库内状态", month_row.status, RESULT_STATUS_SUBMITTED)
check("月成果完成率", month_row.completion_rate, 90.0)
check("月成果计划类型", month_row.plan_type, "month")

ev_row = s.execute(
    select(ResultEvidence).where(ResultEvidence.result_id == month_result_id)
).scalar_one()
check("证据落库 文件名", ev_row.file_name, "proof.png")
check("证据落库 校验和长度", len(ev_row.checksum), 64)
check("证据落库 状态", ev_row.status, "uploaded")

from backend.app.services import result_submission

check("证据文件已落盘", result_submission.evidence_path(ev_row).exists(), True)
s.close()

# ─────────────────────────────────────────────────────────────────────────────
# 6. 数据范围：范围外的人看不到也审批不了
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] 数据范围隔离")
s = get_sessionmaker()()
outsider = None
for candidate in s.execute(select(Employee)).scalars().all():
    if candidate.id in (emp_id, approver_id):
        continue
    ctx = build_access_context(s, candidate)
    if owner_id not in ctx.direct_report_ids and owner_id not in ctx.managed_department_ids:
        outsider = candidate
        break
s.close()

if outsider is None:
    print("  SKIP | 库中没有管理范围外的员工，跳过隔离验证")
else:
    app.dependency_overrides[get_current_user] = make_user_override(outsider.id)
    with TestClient(app) as c:
        r = c.get("/v1/leader/week-result-approvals")
        check("范围外 GET 审批列表 状态码", r.status_code, 200)
        check(
            "范围外 看不到该成果",
            any(i["id"] == week_result_id for i in r.json()["data"]["items"]),
            False,
        )
        r = c.post(
            f"/v1/leader/week-result-approvals/{week_result_id}/approve",
            json={},
        )
        check("范围外 审批 被拒", r.status_code, 403)

app.dependency_overrides.pop(get_db, None)
app.dependency_overrides.pop(get_current_user, None)

print("\n" + "=" * 60)
print("A4 端到端验证：" + ("全部通过 ✅" if ok else "存在失败 ❌"))
print("=" * 60)
raise SystemExit(0 if ok else 1)
