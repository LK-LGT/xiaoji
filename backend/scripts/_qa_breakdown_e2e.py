"""评分构成工具端到端验证（真后端 + 真 agent-service + 真模型）。

⚠️ **验证什么**
--------------
Step 4 新增了 ``get_scoring_breakdown``。本脚本验证：

1. 网页端问「这分数**怎么算的**」→ AI **真的**调了 breakdown 工具；
2. 回答里出现了**权重、等级、贡献分**这些 breakdown 独有的字段；
3. 回答里的算式与**算分引擎的真实输出**一致（不是模型编的）；
4. 引用非空且都能对上来源。

⇒ 第 3 条最关键：AI 说「贡献 60 分」必须与系统算出来的一致，
   否则就是在编数据。

用法::

    python backend/scripts/_qa_breakdown_e2e.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def login(username: str, password: str) -> str:
    resp = httpx.post(
        f"{BASE}/v1/auth/login",
        json={"username": username, "password": password},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()["data"]["accessToken"]


def main() -> int:
    print("=" * 70)
    print("评分构成工具端到端验证")
    print("=" * 70)

    token = login("employee", "employee123")
    headers = {"Authorization": f"Bearer {token}"}

    # ── 找到一个有冻结配置的任务 ─────────────────────────────
    print("\n[1] 找可用任务")
    resp = httpx.get(f"{BASE}/v1/qa/tasks", headers=headers, timeout=15.0)
    resp.raise_for_status()
    tasks = resp.json()["data"]["items"]
    check("任务列表非空", len(tasks) > 0)

    # 挑一个「明细可用」的任务（有 metrics）
    target = None
    for task in tasks:
        resp = httpx.post(
            f"{BASE}/v1/qa/ask",
            headers=headers,
            json={"question": "列出你的指标达成情况", "task_id": task["task_id"]},
            timeout=90.0,
        )
        if resp.status_code != 200:
            continue
        body = resp.json()
        metrics = body.get("data", {}).get("get_metric_values", {})
        if metrics.get("available") and metrics.get("metrics"):
            target = (task, body)
            break

    if target is None:
        print("  ! 没有明细可用的任务 —— 跳过")
        print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
        return 0 if FAIL == 0 else 1

    task, _ = target
    print(f"  使用任务：{task.get('label')} ({task['task_id']})")

    # ── 问「怎么算的」 ───────────────────────────────────────
    print("\n[2] 问「这分数是怎么算出来的」")
    resp = httpx.post(
        f"{BASE}/v1/qa/ask",
        headers=headers,
        json={
            "question": "我这个月的分数是怎么算出来的？各项指标各贡献了多少分？",
            "task_id": task["task_id"],
        },
        timeout=90.0,
    )
    resp.raise_for_status()
    body = resp.json()

    check("未降级", body["degraded"] is False, body["answer"][:100])
    check("引用非空", len(body["citations"]) > 0, str(body["citations"]))
    check("引用都对得上来源", set(body["citations"]) <= set(body["sources"]))

    # ① AI 真的调了 breakdown 工具
    tool_names = [c.get("tool") for c in body.get("tool_calls", [])]
    print(f"  工具调用：{tool_names}")
    check("调用了 get_scoring_breakdown",
          "get_scoring_breakdown" in tool_names, str(tool_names))

    # ② breakdown 数据被预取了
    breakdown = body.get("data", {}).get("get_scoring_breakdown", {})
    check("breakdown 数据已预取", breakdown.get("available") is True, str(breakdown)[:120])
    check("breakdown 有 items", len(breakdown.get("items", [])) > 0)

    # ③ 回答里出现 breakdown 独有的字段
    answer = body["answer"]
    item = breakdown.get("items", [{}])[0]
    if item:
        level = str(item.get("level") or "")
        contribution = item.get("contribution")
        print(f"  首项：{item.get('metric_name')} 等级={level} 贡献={contribution}")
        check("回答提到了等级或贡献分",
              (level and level in answer) or (contribution is not None and str(contribution) in answer),
              answer[:200])

    # ④ **算式必须与算分引擎一致** —— 不能是模型编的
    print("\n[3] 核对算式与算分引擎")
    from sqlalchemy import select

    from backend.app.core.db import get_sessionmaker
    from backend.app.domain.models import AssessmentRecord
    from backend.app.services import review_service

    with get_sessionmaker()() as db:
        record = db.scalars(
            select(AssessmentRecord).where(
                AssessmentRecord.task_id == task["task_id"],
            ).limit(1)
        ).first()
        if record is None:
            print("  · 找不到考核记录 —— 跳过引擎核对")
        else:
            expected = review_service.compute_score(db, record_id=record.id)
            print(f"  引擎总分：{expected.total}")
            for ref in expected.per_metric:
                print(f"    - {ref['metric_name']} 权重={ref['normalized_weight']} "
                      f"等级={ref['level']} 贡献={ref['contribution']}")
                # AI 的回答里出现的贡献分必须与引擎一致
                if ref["contribution"]:
                    check(f"{ref['metric_name']} 贡献分与引擎一致",
                          str(ref["contribution"]) in answer,
                          f"引擎={ref['contribution']} 回答未提及")
            check("回答没有出现与引擎不同的总分",
                  str(expected.total) in answer or not expected.total,
                  f"引擎={expected.total}")

    print("\n  --- AI 回答 ---")
    for line in answer.splitlines()[:15]:
        print(f"  | {line}")
    print("  ---------------")

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
