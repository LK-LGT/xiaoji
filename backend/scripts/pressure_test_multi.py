"""压测多轮跑 —— 因为模型是**非确定性**的，单轮结果没有统计意义。

跑 N 轮，统计每个问题的**用例级检出率**与**每轮检出数**，
给出均值与波动区间。这样才能回答「真模型到底会编造多少」。

用法：
    python backend/scripts/pressure_test_multi.py --rounds 3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "pressure_test_hallucination.py"
REPORT = ROOT / "backend" / "data" / "_pressure_report.json"

KEYS = (
    "fabricated_quotes",
    "fabricated_facts",
    "fabricated_numbers",
    "illegal_references",
    "score_leak",
    "attendance_penalized",
    "attendance_placeholder",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    per_round: list[dict] = []
    invalid_rounds = 0
    for index in range(1, args.rounds + 1):
        print(f"\n{'#' * 70}\n# 第 {index}/{args.rounds} 轮\n{'#' * 70}")
        cmd = [sys.executable, str(SCRIPT)]
        if args.limit:
            cmd += ["--limit", str(args.limit)]
        proc = subprocess.run(cmd, check=False, cwd=str(ROOT))
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        # ⚠️ 单轮失败（退出码 2 = 请求失败）→ 这一轮**无效**，
        #    不能计入「零幻觉占比」—— 否则又是假通过。
        if proc.returncode != 0 or not report.get("valid", True):
            invalid_rounds += 1
            print(f"⚠️  第 {index} 轮无效（退出码 {proc.returncode}）—— 不计入统计")
        per_round.append(report)

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("多轮汇总")
    print("=" * 78)
    if invalid_rounds:
        print(f"❌ 有 {invalid_rounds}/{len(per_round)} 轮无效（请求失败）—— 汇总不可信")
    case_count = per_round[0]["case_count"] if per_round else 0
    print(f"轮数：{len(per_round)}   每轮用例数：{case_count}")
    print(f"模型：{per_round[0]['model'] if per_round else '?'}")

    print("\n--- 每轮的检出例数 ---")
    header = "  ".join(f"第{i + 1}轮".rjust(8) for i in range(len(per_round)))
    print(f"{'问题类型':26} {header}   均值   波动")
    for key in KEYS:
        counts = []
        for report in per_round:
            n = sum(
                1 for r in report["results"]
                if (r.get("issues") or {}).get(key)
            )
            counts.append(n)
        mean = sum(counts) / len(counts)
        lo, hi = min(counts), max(counts)
        cells = "  ".join(str(c).rjust(8) for c in counts)
        print(f"{key:26} {cells}   {mean:5.1f}   {lo}~{hi}")

    clean_rounds = []
    for report in per_round:
        clean = sum(
            1 for r in report["results"] if not (r.get("issues") or {})
        )
        clean_rounds.append(clean)
    print(f"\n{'零问题用例数':26} " + "  ".join(str(c).rjust(8) for c in clean_rounds)
          + f"   {sum(clean_rounds) / len(clean_rounds):5.1f}"
          + f"   {min(clean_rounds)}~{max(clean_rounds)}")
    print(f"{'零问题占比':26} "
          + "  ".join(f"{c / max(1, case_count) * 100:7.1f}%" for c in clean_rounds))

    # 红线汇总
    print("\n--- 红线（三轮累计）---")
    for key, label in (
        ("fabricated_quotes", "编造引文"),
        ("fabricated_facts", "编造事实"),
        ("fabricated_numbers", "编造数字"),
        ("illegal_references", "非法证据引用"),
        ("score_leak", "分数泄漏"),
        ("attendance_penalized", "考勤被当扣分"),
    ):
        total = 0
        for report in per_round:
            total += report["totals"].get(f"{key}_count", 0)
        mark = "❌" if total else "✅"
        print(f"  {mark} {label:16} 共 {total} 处（{len(per_round)} 轮累计）")

    out = ROOT / "backend" / "data" / "_pressure_multi.json"
    out.write_text(json.dumps({
        "rounds": len(per_round),
        "case_count": case_count,
        "model": per_round[0]["model"] if per_round else "",
        "per_round_totals": [r["totals"] for r in per_round],
        "reports": per_round,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整数据 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
