"""从真实库抽取压测用例，导出成 JSON。

压测要喂**真实历史数据**给真模型 —— 合成数据测不出「模型会不会编造」。

⚠️ 数据模型要点（踩过才知道）：
- `evidence.record_id` **全库为 NULL** —— 证据是按 `employee_id` 挂的，
  不是按考核记录挂的。所以 join 必须走 `employee_id`。
- `evidence.status` 取值是 `approved`（不是 `active`）。
- `metric_value` 才有 value/target/source/as_of —— 指标口径从这儿取。

输出：`backend/data/_pressure_cases.json`（不入 git）
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path("backend/data/perf.db")
OUT = Path("backend/data/_pressure_cases.json")
PERIOD = "202611"


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # --- 考核记录 ---
    records = cur.execute(
        "select id, employee_id, task_id, status, self_review_content "
        "from assessment_record where task_id like ?",
        (f"%{PERIOD}%",),
    ).fetchall()
    print(f"考核记录（{PERIOD}）: {len(records)}")

    # --- 员工名（评语里会用到，也做脱敏测试） ---
    names = {
        r["id"]: r["name"]
        for r in cur.execute("select id, name from employee").fetchall()
    }
    departments = {
        r["id"]: r["department_id"]
        for r in cur.execute("select id, department_id from employee").fetchall()
    }

    # --- 指标值 ---
    metric_rows = cur.execute(
        "select employee_id, metric_code, value, target, unit, source, as_of "
        "from metric_value where period = ?",
        (PERIOD,),
    ).fetchall()
    metrics_by_emp: dict[str, list[dict]] = {}
    for r in metric_rows:
        metrics_by_emp.setdefault(r["employee_id"], []).append({
            "code": r["metric_code"],
            "value": r["value"],
            "target": r["target"],
            "unit": r["unit"] or "",
            "source": r["source"] or "",
            "as_of": r["as_of"] or "",
        })
    print(f"指标值（{PERIOD}）: {len(metric_rows)}")

    # --- 证据（按 employee_id 挂） ---
    ev_rows = cur.execute(
        "select id, employee_id, kind, summary, impact, source, as_of, usage, visibility "
        "from evidence where status = 'approved'"
    ).fetchall()
    evidence_by_emp: dict[str, list[dict]] = {}
    for r in ev_rows:
        evidence_by_emp.setdefault(r["employee_id"], []).append({
            "id": r["id"],
            "kind": r["kind"],
            "summary": r["summary"] or "",
            "impact": r["impact"] or "",
            "source": r["source"] or "",
            "as_of": r["as_of"] or "",
            "usage": r["usage"] or "scoring",
            "visibility": r["visibility"] or "share_with_manager",
        })
    print(f"证据: {len(ev_rows)}")

    # --- 组装用例 ---
    cases = []
    for rec in records:
        emp = rec["employee_id"]
        metrics = metrics_by_emp.get(emp, [])
        evidence = evidence_by_emp.get(emp, [])
        if not metrics or not evidence:
            continue  # 没有指标或没有证据 → 测不出幻觉
        # 考勤是 reference_only —— 单独放，不与评分证据混（决策②）
        attendance = [e for e in evidence if e["usage"] == "reference_only"]
        scoring = [e for e in evidence if e["usage"] != "reference_only"]
        if not scoring:
            continue
        cases.append({
            "record_id": rec["id"],
            "employee_id": emp,
            "task_id": rec["task_id"],
            "status": rec["status"],
            "employee_name": names.get(emp, ""),
            "department_id": departments.get(emp, ""),
            "self_review_content": rec["self_review_content"] or "",
            "metrics": metrics,
            "evidence": scoring[:6],
            "attendance": attendance[:4],
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n导出 {len(cases)} 个用例 → {OUT}")
    print(f"  含考勤异常用例: {sum(1 for c in cases if c['attendance'])}")
    print(f"  含自评文本用例: {sum(1 for c in cases if c['self_review_content'])}")
    print(f"  平均证据数: {sum(len(c['evidence']) for c in cases) / max(1, len(cases)):.1f}")

    # 打印一个样例，便于人工核对
    if cases:
        sample = cases[0]
        print("\n=== 样例用例 ===")
        print(json.dumps(sample, ensure_ascii=False, indent=2)[:1500])

    conn.close()


if __name__ == "__main__":
    main()
