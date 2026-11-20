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
    #
    # ⚠️ **周期筛选不能用 `task_id LIKE '%202611%'`**（实测踩到的坑）：
    #   task_id 的格式**不统一** ——
    #     · `TASK_20261113150104_202611`  （系统生成，含周期码）
    #     · `fab4fa09-28a2-4ba0-a912-...` （UUID 格式，不含周期码）
    #     · `R6E2E_TASK_209906`            （R6 闭环脚本生成，含的是年份月日）
    #   用 LIKE 过滤会把**唯一有可打分数据的那几个人**（李凯、栗月椿）
    #   全部滤掉，只剩两个只有考勤证据的员工 —— 样本直接归零。
    #
    #   正确做法：**按人取全部记录**，用 `metric_value.period` 判断周期归属。
    #   考核记录与周期不是强绑定（record_id 里也只是拼接，不是权威字段）。
    records = cur.execute(
        "select id, employee_id, task_id, status, self_review_content "
        "from assessment_record"
    ).fetchall()
    print(f"考核记录（全库）: {len(records)}")

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
    #
    # ⚠️ **样本代表性**（实测踩到的坑）：
    #   库里的分布是 —— 85 名员工共 185 条 `reference_only`（全是考勤），
    #   但只有 **2 名员工**有 `usage='scoring'` 的证据；其中**只有 1 人**
    #   同时有指标值。所以朴素写法（同一人多条 assessment_record）会
    #   产出「22 个用例其实是同一个人」的假多样性。
    #
    #   对策：
    #   ① 按 **employee × task** 去重 —— 同一人同一任务的多次提交
    #      只取一条（它们的内容高度重复，留着只会虚增样本量）；
    #   ② **没有指标也要留** —— 栗月椿有 11 条 scoring 证据但没指标，
    #      这正是「证据多、指标缺」的边界场景，容易触发幻觉，丢掉很可惜；
    #   ③ 打印覆盖率，让人一眼看出样本有多窄。
    cases = []
    seen_pairs: set[tuple[str, str]] = set()
    for rec in records:
        emp = rec["employee_id"]
        pair = (emp, rec["task_id"])
        if pair in seen_pairs:
            continue  # 同一人同一任务 → 只留一条
        seen_pairs.add(pair)

        metrics = metrics_by_emp.get(emp, [])
        evidence = evidence_by_emp.get(emp, [])
        # 考勤是 reference_only —— 单独放，不与评分证据混（决策②）
        attendance = [e for e in evidence if e["usage"] == "reference_only"]
        scoring = [e for e in evidence if e["usage"] != "reference_only"]
        if not scoring:
            continue  # 既无指标也无评分证据 → 测不出幻觉
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

    emp_set = {c["employee_id"] for c in cases}
    print(f"\n导出 {len(cases)} 个用例 → {OUT}")
    print(f"  覆盖员工: {len(emp_set)} 人  ({', '.join(sorted(emp_set))})")
    print(f"  含考勤异常用例: {sum(1 for c in cases if c['attendance'])}")
    print(f"  含自评文本用例: {sum(1 for c in cases if c['self_review_content'])}")
    print(f"  平均证据数: {sum(len(c['evidence']) for c in cases) / max(1, len(cases)):.1f}")

    # --- 覆盖率诚实披露 ---
    #
    # ⚠️ 不披露的话，「2 个用例 100% 干净」会被误读成
    #    「覆盖充分、模型很可靠」；实际上样本只有 1~2 个人。
    total_emp = cur.execute("select count(distinct employee_id) from evidence").fetchone()[0]
    scoring_emp = cur.execute(
        "select count(distinct employee_id) from evidence where usage = 'scoring'"
    ).fetchone()[0]
    print("\n--- ⚠️ 样本覆盖率（务必连同结果一起看）---")
    print(f"  全库有证据的员工: {total_emp} 人")
    print(f"  其中有 scoring 证据的: {scoring_emp} 人")
    print(f"  本次压测覆盖: {len(emp_set)} 人  "
          f"(= {len(emp_set) / max(1, total_emp) * 100:.1f}% 全库 / "
          f"{len(emp_set) / max(1, scoring_emp) * 100:.1f}% 可打分人群)")
    if len(emp_set) < 5:
        print("  ⚠️ 样本极窄：检出率低**不代表模型可靠**，只代表"
              "「在少数几个样本上没出错」。")
        print("     扩样本需要真实业务数据灌库（当前 85 人的证据几乎全是"
              "reference_only 考勤类）。")

    # 打印一个样例，便于人工核对
    if cases:
        sample = cases[0]
        print("\n=== 样例用例 ===")
        print(json.dumps(sample, ensure_ascii=False, indent=2)[:1500])

    conn.close()


if __name__ == "__main__":
    main()
