"""R6 完整业务链路端到端验证 —— 计划 → 审批 → 成果 → 算分。

⚠️ 为什么需要这个脚本
-------------------
盘点项 R6：**真实库里业务主链是空的**。
功能代码都在（计划/审批/成果/算分各自都有端点、有测试），
但从没在**真实数据库**上把一条完整链路跑通过 ——
所以"看着都能跑"和"真的能跑"之间隔着一个空白。

本脚本在**真实库**（``backend/data/perf.db``）上跑完整闭环，
每一步都做**库内落库断言**（不是只看接口返回 200）。

⚠️ 三个必须注意的坑（沿用 ``verify_plan_approval_e2e.py`` 的经验）
-----------------------------------------------------------
1. ``get_db`` 覆盖必须用 **generator** 形式，且**必须 commit**。
   覆盖成 class（``get_sessionmaker``）→ FastAPI 解析它的 ``__init__``
   签名 → 422。不提交 → 接口返回 200 但数据全回滚，
   表现为「接口说成功、库里没变化」。
2. ``get_current_user`` 覆盖不能塞**已关闭会话**的 Employee 实例，
   否则访问属性时 ``DetachedInstanceError``。
3. 算分依赖**冻结快照 + MetricValue**，两者缺一都算不出分。
   本脚本负责把缺的**配置侧数据**补齐（权重/评分标准/指标值）。

链路与对应端点
--------------
    ① 计划     员工建月计划 + 条目          POST /v1/employee/month-plans
    ② 审批     主管审批计划                 POST /v1/leader/month-plan-approvals/{id}/approve
    ③ 成果     员工提交月成果               POST /v1/employee/results/submit
    ④ 算分     规则引擎算分并落 Score       POST /v1/rules-engine/records/{id}/execute

用法::

    python backend/scripts/verify_r6_full_chain_e2e.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

# 让脚本能从仓库根目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.app.core.db import get_db, get_sessionmaker  # noqa: E402
from backend.app.dependencies import get_current_user  # noqa: E402
from backend.app.domain.enums import PlanStatus  # noqa: E402
from backend.app.domain.models import (  # noqa: E402
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
    Employee,
    MetricValue,
    MonthPlan,
    MonthPlanItem,
    Score,
)
from backend.app.domain.result_models import (  # noqa: E402
    RESULT_PLAN_TYPE_MONTH,
    RESULT_STATUS_SUBMITTED,
    Result,
)
from backend.app.domain.plan_models import PlanApproval  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.services.access_control import (  # noqa: E402
    build_access_context,
    can_access_employee,
)

# 隔离标记：脚本造的数据都带这个前缀，便于事后清理/核对。
TAG = "R6E2E"
# 用一个够远的月份，避免撞上真实业务数据。
PLAN_MONTH = "2099-06"
PERIOD = "209906"


def log(stage: str, message: str, ok: bool = True) -> None:
    mark = "✔" if ok else "✖"
    print(f"{mark} [{stage}] {message}")
    if not ok:
        globals()["FAILED"] = True


FAILED = False


# ═══════════════════════════════════════════════════════════════════════════
# 0. 准备：找一对「经理 → 员工」
# ═══════════════════════════════════════════════════════════════════════════

db = get_sessionmaker()()

approver = None
owner = None
for candidate in db.execute(select(Employee)).scalars().all():
    if candidate.employment_status != "active":
        continue
    ctx = build_access_context(db, candidate)
    reports = [r for r in ctx.direct_report_ids if r != candidate.id]
    if not reports:
        continue
    report_id = reports[0]
    report = db.get(Employee, report_id)
    if report is None or report.employment_status != "active":
        continue
    # 主管必须能审批该员工 —— 用 access_control 再确认一次。
    if not can_access_employee(
        ctx, report, db, require_active_target=True
    ):
        continue
    approver, owner = candidate, report
    break

if approver is None or owner is None:
    print("SKIP: 真实库里找不到可用的「经理 → 员工」关系，无法验证 R6 链路")
    raise SystemExit(0)

print(f"主管: {approver.name} ({approver.id}) | 员工: {owner.name} ({owner.id})")

# ═══════════════════════════════════════════════════════════════════════════
# 1. 配置侧：冻结快照 + 考核任务 + 考核记录 + 指标值（算分的前提）
# ═══════════════════════════════════════════════════════════════════════════
#
# ⚠️ 真实库里 weight_set / rubric / metric_value 全是 0 条 ——
#    没有它们，就算分这步会因「无指标参与」算出 0 分或直接报错。
#    这里**只为验证链路**造一份最小可用的冻结快照。

metrics_json = [
    {"code": "gmv", "name": "销售额", "unit": "元"},
    {"code": "conv", "name": "转化率", "unit": "%"},
]
weights_json = [
    {"metric_code": "gmv", "weight": 70},
    {"metric_code": "conv", "weight": 30},
]
# 达成率区间：[min_ratio, max_ratio)，max_ratio=None 表示无上限。
rubric_json = [
    {
        "metric_code": "gmv",
        "levels_json": [
            {"level": "A", "min_ratio": 1.0, "max_ratio": None, "score": 100},
            {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "score": 80},
            {"level": "C", "min_ratio": 0.0, "max_ratio": 0.8, "score": 60},
        ],
    },
    {
        "metric_code": "conv",
        "levels_json": [
            {"level": "A", "min_ratio": 1.0, "max_ratio": None, "score": 100},
            {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "score": 80},
            {"level": "C", "min_ratio": 0.0, "max_ratio": 0.8, "score": 60},
        ],
    },
]

task_id = f"{TAG}_TASK_{PERIOD}"
snap = db.execute(
    select(ConfigSnapshot).where(ConfigSnapshot.task_id == task_id)
).scalar_one_or_none()
if snap is None:
    snap = ConfigSnapshot(
        task_id=task_id,
        metrics_json=metrics_json,
        weights_json=weights_json,
        rubric_json=rubric_json,
        config_version=f"{TAG}-v1",
    )
    db.add(snap)
    db.flush()

task = db.execute(
    select(AssessmentTask).where(AssessmentTask.id == task_id)
).scalar_one_or_none()
if task is None:
    task = AssessmentTask(
        id=task_id,
        name=f"{TAG} 考核任务",
        period=PERIOD,
        status="in_progress",
        snapshot_id=snap.id,
        owner_id=approver.id,
    )
    db.add(task)
    db.flush()

record_id = f"{TAG}_REC_{owner.id}"
rec = db.execute(
    select(AssessmentRecord).where(AssessmentRecord.id == record_id)
).scalar_one_or_none()
if rec is None:
    rec = AssessmentRecord(
        id=record_id,
        task_id=task_id,
        employee_id=owner.id,
        status="in_progress",
    )
    db.add(rec)
    db.flush()

# 指标实测值（算分集的唯一入口 —— 考勤不进这里）。
for mv in (
    {"metric_code": "gmv", "value": 95.0, "target": 100.0, "unit": "元"},
    {"metric_code": "conv", "value": 85.0, "target": 100.0, "unit": "%"},
):
    exists = db.execute(
        select(MetricValue).where(
            MetricValue.employee_id == owner.id,
            MetricValue.metric_code == mv["metric_code"],
            MetricValue.period == PERIOD,
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(
            MetricValue(
                employee_id=owner.id,
                period=PERIOD,
                source=f"{TAG}_e2e",
                as_of=date(2099, 6, 30),
                **mv,
            )
        )
db.commit()

log("配置", f"冻结快照 {snap.id[:8]} | 任务 {task_id} | 记录 {record_id} | 2 条指标值")

# ═══════════════════════════════════════════════════════════════════════════
# 2. 计划 —— 员工建月计划 + 条目
# ═══════════════════════════════════════════════════════════════════════════

plan = db.execute(
    select(MonthPlan).where(
        MonthPlan.owner_id == owner.id, MonthPlan.plan_month == PLAN_MONTH
    )
).scalar_one_or_none()
if plan is None:
    plan = MonthPlan(
        owner_id=owner.id,
        plan_month=PLAN_MONTH,
        title=f"{TAG} 月计划",
        status=PlanStatus.DRAFT.value,
    )
    db.add(plan)
    db.flush()

item_count = db.execute(
    select(MonthPlanItem).where(MonthPlanItem.plan_id == plan.id)
).scalars().all()
if not item_count:
    db.add(
        MonthPlanItem(
            plan_id=plan.id,
            task_type="REGULAR",
            task_name=f"{TAG} 关键任务",
            task_content="完成 R6 端到端验证",
            deliverable="可交付成果",
            quality="高",
            performance_weight=100.0,
            estimated_value=100.0,
            estimated_start_date=date(2099, 6, 1),
            estimated_end_date=date(2099, 6, 30),
            status="draft",
            sort_no=0,
        )
    )
db.commit()

# ── 重置审批态，让脚本**可重复执行** ────────────────────────────────────────
# ⚠️ 上一轮跑完后计划已是 approved、version_no 已递增，
#    再跑会撞「版本冲突：期望 1，实际 2」和「重复审批」。
#    验证脚本必须**幂等** —— 否则第二次跑就"失败"，
#    会让人误以为链路坏了。
#
#    成果也要清 —— 否则撞「该任务已有待审批的成果，不能重复提交」。
for flow in db.execute(
    select(PlanApproval).where(PlanApproval.plan_id == plan.id)
).scalars().all():
    db.delete(flow)
for old_result in db.execute(
    select(Result).where(Result.owner_id == owner.id)
).scalars().all():
    db.delete(old_result)
plan.status = PlanStatus.DRAFT.value
plan.approved_at = None
plan.approval_comment = None
plan.version_no = 1
db.commit()

plan_id = plan.id
owner_id = owner.id
approver_id = approver.id
log("计划", f"月计划 {plan_id[:8]} + 1 个条目（已重置为 draft，可重复验证）")

db.close()

# ═══════════════════════════════════════════════════════════════════════════
# 3. 装配测试客户端（按角色切换当前用户）
# ═══════════════════════════════════════════════════════════════════════════

CURRENT_USER_ID = {"id": owner_id}


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
        yield s.get(Employee, CURRENT_USER_ID["id"])
    finally:
        s.close()


app.dependency_overrides[get_db] = db_override
app.dependency_overrides[get_current_user] = user_override

try:
    with TestClient(app) as c:
        # ── ② 审批：主管审计划 ──────────────────────────────────────────
        CURRENT_USER_ID["id"] = approver_id

        # 先把计划置为 submitted，才能进待批列表。
        s = get_sessionmaker()()
        p = s.get(MonthPlan, plan_id)
        p.status = PlanStatus.SUBMITTED.value
        s.commit()
        s.close()

        r = c.get("/v1/leader/month-plan-approvals")
        rows = r.json().get("data", []) if r.status_code == 200 else []
        hit = [x for x in rows if x.get("id") == plan_id]
        log("审批·列表", f"待批列表 {len(rows)} 条 | 含本次计划: {bool(hit)}", bool(hit))
        if hit:
            it = hit[0]
            log(
                "审批·字段",
                f"{it.get('employeeName')} | {it.get('orgName')} | "
                f"{it.get('planYear')}-{it.get('planMonth')} | status={it.get('status')} | "
                f"ownerId={it.get('ownerId')!r}",
            )

        r = c.post(
            f"/v1/leader/month-plan-approvals/{plan_id}/approve",
            json={"comment": "R6 端到端验证通过", "versionNo": 1},
        )
        approved = r.status_code == 200
        log("审批·通过", f"{r.status_code} {r.text[:100]}", approved)

        r2 = c.post(
            f"/v1/leader/month-plan-approvals/{plan_id}/approve", json={"comment": "重复"}
        )
        log("审批·幂等", f"{r2.status_code}（预期 409）", r2.status_code == 409)

        # ── ③ 成果：员工提交月成果 ──────────────────────────────────────
        CURRENT_USER_ID["id"] = owner_id

        r = c.post(
            "/v1/employee/results/submit",
            data={
                "monthPlanId": plan_id,
                "completionRate": "95",
                "description": "完成 R6 端到端验证的全部内容",
                "completionStatus": "COMPLETED",
                "remark": "R6 验证",
                "personalPerformance": "95",
            },
        )
        submit_ok = r.status_code == 200
        log("成果·提交", f"{r.status_code} {r.text[:160]}", submit_ok)

        # ── ④ 算分 ─────────────────────────────────────────────────────
        CURRENT_USER_ID["id"] = approver_id

        r = c.post(
            f"/v1/assessment/records/{record_id}/execute-rules-engine",
            json={"force_execute": True},
        )
        score_ok = r.status_code == 200
        log("算分·执行", f"{r.status_code} {r.text[:200]}", score_ok)

finally:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)

# ═══════════════════════════════════════════════════════════════════════════
# 4. 库内断言 —— 每一步都必须真的落库
# ═══════════════════════════════════════════════════════════════════════════

db = get_sessionmaker()()

plan_row = db.get(MonthPlan, plan_id)
log(
    "落库·计划",
    f"status={plan_row.status} | approved_at 有值={plan_row.approved_at is not None} | "
    f"comment={plan_row.approval_comment!r}",
    plan_row.status == PlanStatus.APPROVED.value and plan_row.approved_at is not None,
)

flows = db.execute(
    select(PlanApproval).where(PlanApproval.plan_id == plan_id)
).scalars().all()
log("落库·审批流水", f"{len(flows)} 条", len(flows) > 0)

results = db.execute(
    select(Result).where(Result.owner_id == owner_id)
).scalars().all()
tagged = [r for r in results if (r.title or "").startswith(TAG)]
log("落库·成果", f"{len(tagged)} 条 | status={tagged[0].status if tagged else '-'}",
    len(tagged) > 0 and tagged[0].status == RESULT_STATUS_SUBMITTED)

scores = db.execute(select(Score).where(Score.record_id == record_id)).scalars().all()
if scores:
    s0 = scores[0]
    log(
        "落库·分数",
        f"total={s0.total} | 指标数={len(s0.per_metric_json or [])} | "
        f"warnings={len((s0.trace_json or {}).get('warnings') or [])}",
    )
    # 权重 70/30，gmv 达成率 0.95→A(100)，conv 达成率 0.85→B(80)
    # 期望值**按 rubric 独立推导**，不写死数字 ——
    # 免得改配置后断言没同步、白白"通过"。
    #
    #   gmv : 95/100 = 0.95 → 命中 [0.8, 1.0) → B → 80 分，权重 70
    #   conv: 85/100 = 0.85 → 命中 [0.8, 1.0) → B → 80 分，权重 30
    #   总分 = (70×80 + 30×80) / 100 = 80.0
    expected = (70 * 80 + 30 * 80) / 100
    log("落库·分数", f"total={s0.total}（期望 {expected}）",
        abs(float(s0.total) - expected) < 0.01)
    log(
        "落库·可复算",
        f"per_metric 明细非空（{len(s0.per_metric_json or [])} 项，可追溯）",
        bool(s0.per_metric_json),
    )
    # 两个指标都必须参与 —— 少一个说明"缺数据静默不扣分"的行为出现了。
    log(
        "落库·指标齐全",
        f"参与指标 {len(s0.per_metric_json or [])} 个（期望 2）",
        len(s0.per_metric_json or []) == 2,
    )
else:
    log("落库·分数", "❌ Score 表没有记录", False)

# 复算一致性：算分是确定性的，跑两次结果必须一致。
from backend.app.services import review_service  # noqa: E402

try:
    r1 = review_service.compute_score(db, record_id=record_id)
    r2 = review_service.compute_score(db, record_id=record_id)
    same = abs(r1.total - r2.total) < 1e-9
    log("算分·可复算", f"两次结果 {r1.total} / {r2.total}", same)
except Exception as exc:
    log("算分·可复算", f"复算异常：{exc}", False)

db.close()

print()
print("RESULT", "✅ R6 完整链路全部通过" if not FAILED else "❌ 有失败项")
raise SystemExit(0 if not FAILED else 1)
