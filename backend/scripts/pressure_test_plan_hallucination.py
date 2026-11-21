"""计划场景真模型幻觉压测（Step 6 计划助手工具化）。

## 为什么需要这个

T07 真机验证（2026-11-20）只跑了**单轮**，且**没有幻觉判定口径** ——
总结场景那套六类检测器不能直接套用（输出形状、数据源、数字语义都不同）。

上线前必须知道两件事：

1. **真模型在计划场景到底会编造什么**
2. **多轮之间的输出是否稳定**（模型有非确定性）

本脚本同时回答这两个问题。

## 压什么

对**真实员工**（真实库里真有月计划的人）反复调用计划助手：

| 模式 | 意图 | 检查重点 |
|---|---|---|
| ``generate`` | 「下个月做订单中心重构」 | 编造历史计划、编造人名、考核数字泄漏 |
| ``check`` | 体检已有计划 | 编造历史（把别人的事说成自己的）、编造引文 |

判定基准 = **工具实际返回的历史计划**（``get_my_plan_history`` /
``get_department_plan_history`` 的真实输出）。

## 运行

    python backend/scripts/pressure_test_plan_hallucination.py --rounds 3

⚠️ 会**真实调用模型 API**，注意 token 消耗。
用 ``--rounds`` 控制轮数，用 ``--employees`` 限制人数。

## 输出

- 终端：每个员工一行 + 分类统计
- ``backend/data/_plan_pressure_report.json``：完整明细（含模型原文，供人工复核）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
# ⚠️ 后端应用包（``backend.app.*``）也要能 import ——
#    脚本运行时 CWD 不确定，显式把仓库根加进 sys.path。
sys.path.insert(0, str(ROOT))

from plan_hallucination_detector import detect_plan_hallucination  # noqa: E402

AGENT_ENV = ROOT / "agent-service" / ".env"
BACKEND_ENV = ROOT / "backend" / ".env"
REPORT = ROOT / "backend" / "data" / "_plan_pressure_report.json"

AGENT_URL = os.getenv("AGENT_SERVICE_URL", "http://127.0.0.1:8800")

#: 全部缺陷类别 —— 统计与退出码都按这张表
DEFECT_KEYS = (
    "assessment_number_leak",
    "score_leak",
    "fabricated_history",
    "fabricated_quotes",
    "fabricated_facts",
    "fabricated_numbers",
    "illegal_references",
    "fabricated_person_names",
)

#: 必须为 0 的红线 —— 出现即判失败
RED_LINE_KEYS = (
    "assessment_number_leak",
    "score_leak",
    "fabricated_history",
    "fabricated_person_names",
)


def _load_env(path: Path) -> None:
    """读 env 文件（**文件优先** —— 真实配置比环境变量准）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


_load_env(AGENT_ENV)
_load_env(BACKEND_ENV)
SECRET = os.getenv("AGENT_SHARED_SECRET", "")


# ─────────────────────────────────────────────────────────────────────────────
# 取数：真实员工 + 真实历史计划
# ─────────────────────────────────────────────────────────────────────────────


def _open_db():
    """打开**真实库**（只读用途）。"""
    from backend.app.core.db import get_sessionmaker  # noqa: PLC0415

    return get_sessionmaker()()


def collect_employees(limit: int) -> list[dict]:
    """挑出**真实有月计划**的员工。

    ⚠️ 为什么要「真有计划」：没有历史计划的员工，
    工具返回空 → 模型无从编造 → 压不出东西。
    只有**有历史**的员工，模型才有机会「编造历史」——
    而那正是计划场景最危险的幻觉（冒领别人的工作）。
    """
    from sqlalchemy import func, select  # noqa: PLC0415

    from backend.app.domain.models import Employee, MonthPlan  # noqa: PLC0415

    db = _open_db()
    try:
        rows = db.execute(
            select(
                Employee.id,
                Employee.name,
                Employee.department_id,
                func.count(MonthPlan.id).label("plan_count"),
            )
            .join(MonthPlan, MonthPlan.owner_id == Employee.id)
            .group_by(Employee.id)
            .order_by(func.count(MonthPlan.id).desc())
            .limit(limit)
        ).all()
        return [
            {
                "employee_id": r[0],
                "employee_name": r[1],
                "department_id": r[2],
                "plan_count": r[3],
            }
            for r in rows
        ]
    finally:
        db.close()


def load_histories(employee_id: str) -> tuple[dict, dict]:
    """取该员工的「我的历史」与「部门历史」—— **工具的真实返回**。

    ⚠️ 必须用**工具层同一个函数**取数 ——
    否则判定基准和模型看到的数据不一致，
    会出现「模型如实引用，却被判编造」的假阳性。
    """
    from backend.app.services import plan_tool_service  # noqa: PLC0415

    db = _open_db()
    try:
        my = plan_tool_service.load_my_plan_history(
            db, employee_id=employee_id, months=6, plan_type="month"
        )
        dept = plan_tool_service.load_department_plan_history(
            db, employee_id=employee_id, months=6, limit=40
        )
        return my, dept
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 调用模型
# ─────────────────────────────────────────────────────────────────────────────


def post(path: str, payload: dict, timeout: int = 180) -> dict:
    request = urllib.request.Request(
        f"{AGENT_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SECRET}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def issue_tool_token(employee: dict) -> tuple[str, list[str]]:
    """\u7b7e\u53d1\u8ba1\u5212\u57df\u7684\u5de5\u5177\u4ee4\u724c\u3002

    \u26a0\ufe0f \u5fc5\u987b\u7528**\u540e\u7aef\u771f\u5b9e\u7684\u7b7e\u53d1\u51fd\u6570** \u2014\u2014
    \u4ee4\u724c\u91cc\u7684 allow \u767d\u540d\u5355\u51b3\u5b9a\u6a21\u578b\u80fd\u8c03\u54ea\u4e9b\u5de5\u5177\uff0c
    \u7b7e\u53d1\u903b\u8f91\u9519\u4e86\u5c31\u4e0d\u662f\u300c\u771f\u5b9e\u94fe\u8def\u300d\u4e86\u3002

    \u26a0\ufe0f\u26a0\ufe0f **scope \u5fc5\u987b\u6309\u771f\u5b9e\u94fe\u8def\u7b97** \u2014\u2014
    \u5b9e\u6d4b\u8e29\u5751\uff08\u672c\u6b21\u538b\u6d4b\u7b2c\u4e00\u7248\uff09\uff1a\u811a\u672c\u56fa\u5b9a\u7528 ``scope="self"``\uff0c
    \u800c ``get_department_plan_history`` **\u8981\u6c42 ``scope="department"``** \u2192 403 \u2192
    \u6240\u6709\u90e8\u95e8\u5de5\u5177\u8c03\u7528\u5168\u90e8\u5931\u8d25\uff08TOOL_FAILED\uff09\u3002

    \u540e\u679c\u662f**\u538b\u6d4b\u7ed3\u8bba\u5931\u771f**\uff1a\u6a21\u578b\u62ff\u4e0d\u5230\u90e8\u95e8\u53c2\u8003\u6750\u6599\uff0c
    \u5c31\u6ca1\u6709\u673a\u4f1a\u7f16\u9020\u90e8\u95e8\u5386\u53f2 \u2192 \u300c\u96f6\u68c0\u51fa\u300d\u662f\u5047\u7684\u3002

    \u6240\u4ee5\u8fd9\u91cc\u590d\u7528**\u540e\u7aef\u771f\u5b9e\u7684 scope \u8ba1\u7b97\u51fd\u6570**\u3002
    """
    from backend.app.core.config import settings  # noqa: PLC0415
    from backend.app.services.agent_tool_token import (  # noqa: PLC0415
        issue_agent_tool_token,
    )

    # \u26a0\ufe0f \u4e0e ``plan_ai_service._scope_for`` \u4fdd\u6301\u4e00\u81f4
    if settings.plan_ai_department_context and employee.get("department_id"):
        scope = "department"
    else:
        scope = "self"

    allow = ["get_my_plan_history"]
    if scope == "department":
        allow.append("get_department_plan_history")

    token = issue_agent_tool_token(
        employee_id=employee["employee_id"],
        task_id="PLAN_PRESSURE",
        allow=allow,
        scope=scope,
    )
    return token, allow


#: 压测用的意图（覆盖不同计划类型）
INTENTS = [
    ("generate", "下个月推进订单中心重构，拆分支付与库存子域"),
    ("generate", "继续做需求评审相关的工作，并输出交付物"),
    ("check", "体检这份月计划，看看有没有重复或缺失交付物"),
]


def run_one_round(employee: dict, my: dict, dept: dict, mode: str, intent: str) -> dict:
    """跑一轮压测：调模型 → 判定 → 返回结果。"""
    token, allowed = issue_tool_token(employee)

    payload = {
        "mode": mode,
        "intent_text": intent,
        "context": {
            "scope": "self",
            "plan_type": "month",
            "period": "",
        },
        "tool_token": token,
        "allowed_tools": allowed,
        "trace_id": employee["employee_id"][:32],
        "current_form": {},
        "instruction": "",
    }

    try:
        resp = post("/v1/agent/plan", payload)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # pragma: no cover
            detail = ""
        return {"error": f"HTTP {exc.code}: {detail}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"error": str(exc)}

    items = resp.get("items") or []
    findings = resp.get("findings") or []
    warnings = resp.get("warnings") or []
    missing = resp.get("missing_context") or []

    issues = detect_plan_hallucination(
        items,
        findings,
        my_history=my,
        dept_history=dept,
        extra_texts=[str(w) for w in warnings] + [str(m) for m in missing],
    )

    return {
        "mode": mode,
        "intent": intent,
        "items": items,
        "findings": findings,
        "warnings": warnings,
        "missing_context": missing,
        "tool_calls": resp.get("tool_calls") or [],
        "degraded": bool(resp.get("degraded")),
        "issues": issues,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3, help="每个员工跑几轮")
    parser.add_argument("--employees", type=int, default=5, help="最多取几个员工")
    parser.add_argument("--out", default=str(REPORT))
    args = parser.parse_args()

    if not SECRET:
        print("[FAIL] 未找到 AGENT_SHARED_SECRET")
        return 1

    employees = collect_employees(args.employees)
    if not employees:
        print("[FAIL] 真实库里没有带月计划的员工 —— 无从压测")
        return 1

    print("=" * 78)
    print("计划场景真模型幻觉压测")
    print(f"员工数：{len(employees)}   轮数：{args.rounds}   模型：{os.getenv('AGENT_MODEL_NAME', '?')}")
    print("=" * 78)

    results: list[dict] = []
    totals: Counter = Counter()
    started = time.time()

    for employee in employees:
        my, dept = load_histories(employee["employee_id"])
        for round_index in range(args.rounds):
            mode, intent = INTENTS[round_index % len(INTENTS)]
            outcome = run_one_round(employee, my, dept, mode, intent)

            record = {
                "employee_id": employee["employee_id"],
                "employee_name": employee["employee_name"],
                "round": round_index + 1,
                **outcome,
            }
            results.append(record)

            if "error" in outcome:
                print(f"[{employee['employee_name'][:6]} R{round_index + 1}] ❌ {outcome['error']}")
                totals["request_failed"] += 1
                continue

            issues = outcome["issues"]
            found = {k: len(issues.get(k, [])) for k in DEFECT_KEYS if issues.get(k)}
            red_line = [k for k in RED_LINE_KEYS if issues.get(k)]

            for key, count in found.items():
                totals[key] += 1
                totals[f"{key}_count"] += count

            # ⚠️ **降级轮不算「有内容」** ——
            #    模型降级时没有产出内容，既不会演示也不会编造。
            #    把它计入「干净轮次」= **虚假报告安全性**。
            if outcome.get("degraded"):
                totals["degraded"] += 1
                print(
                    f"[{employee['employee_name'][:6]} R{round_index + 1}] "
                    f"⏩ 降级（无产出，不计入样本）"
                )
                continue

            if red_line:
                print(
                    f"[{employee['employee_name'][:6]} R{round_index + 1}] "
                    f"❌ 红线 {red_line}"
                )
                for key in red_line:
                    for detail in issues[key]:
                        print(f"         {detail}")
            elif found:
                print(
                    f"[{employee['employee_name'][:6]} R{round_index + 1}] "
                    f"⚠️  {found}"
                )
            else:
                totals["clean"] += 1
                print(f"[{employee['employee_name'][:6]} R{round_index + 1}] ✅ 无检出")

    elapsed = time.time() - started

    # ── 汇总 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    total_rounds = len(results)
    print(f"总轮次：{total_rounds}   耗时：{elapsed:.1f}s")
    degraded_rounds = totals["degraded"]
    valid_rounds = total_rounds - degraded_rounds
    print(f"有效样本：{valid_rounds}   降级无产出：{degraded_rounds}")
    print(f"干净轮次：{totals['clean']} / {valid_rounds}")

    print("\n--- 红线（必须为 0）---")
    red_line_total = 0
    for key in RED_LINE_KEYS:
        n = totals[key]
        red_line_total += n
        mark = "✅" if n == 0 else "❌"
        print(f"  {mark} {key}：{n} 例 / {totals[f'{key}_count']} 处")

    print("\n--- 其他幻觉类 ---")
    for key in DEFECT_KEYS:
        if key in RED_LINE_KEYS:
            continue
        n = totals[key]
        mark = "✅" if n == 0 else "⚠️ "
        print(f"  {mark} {key}：{n} 例 / {totals[f'{key}_count']} 处")

    if totals["request_failed"]:
        print(f"\n  ⚠️  请求失败：{totals['request_failed']} 轮")

    # ── 写报告 ───────────────────────────────────────────────────────────
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "rounds": args.rounds,
                "employees": len(employees),
                "elapsed_seconds": round(elapsed, 1),
                "totals": dict(totals),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n报告已写入：{out_path}")

    # ── 退出码 ───────────────────────────────────────────────────────────
    if totals["request_failed"]:
        print("\n❌ 有请求失败 —— 结论不可信，先查链路")
        return 1
    if degraded_rounds:
        print(
            f"\n⚠️  {degraded_rounds} 轮降级（无产出内容），未计入有效样本"
        )
    if red_line_total:
        print("\n❌ 检出红线问题 —— 必须修")
        return 1
    print("\n✅ 未检出红线问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
