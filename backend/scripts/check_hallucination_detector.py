"""幻觉检测器的自检 —— 先证明「检测器本身能抓到」。

⚠️ 压测的前提是：**检测器准**。如果检测器抓不到已知编造，
那「检出率 0%」就毫无意义（可能只是检测器瞎了）。

这个文件用**人工构造的已知案例**验证检测器：
每个案例的期望结果写在 ``expect`` 里。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pressure_test_hallucination import detect_hallucination  # noqa: E402

EVIDENCE = [
    "完成客户需求调研与方案设计；完成率 100%；附件：requirement-research-report.pdf",
    "本季度交付 3 个需求，全部一次验收通过，无线上事故",
    # ⚠️ 真实证据（压测里的）—— 「问题均已闭环」是改写判定的依据
    "完成情况：联调过程中无阻塞缺陷，问题均已闭环，审批意见：同意通过",
]
METRICS = [
    {"code": "plan_execution_rate", "value": 100.0, "target": 100.0, "unit": "%"},
]
# ⚠️ **派生比率**（payload 里我们算好给模型的 `ratio`）
#
# 实测踩到的坑：模型写「比率为 0.95 / 0.85 / 0.75 / 0.88」是
# **完全忠实**的转述（payload 里就有这些值），但第一版检测器
# 只把 code/value/target/unit 算进「合法数字」→ 每轮误报 4 处。
#
# 这里必须带上 ratio —— 否则压测会把「模型正确转述」当成「模型编造」。
METRICS_WITH_RATIO = [
    {"code": "gmv", "value": 95.0, "target": 100.0, "unit": "元",
     "ratio": 0.95, "ratio_level": "B"},
    {"code": "conv", "value": 85.0, "target": 100.0, "unit": "%",
     "ratio": 0.85, "ratio_level": "B"},
    {"code": "response_hours", "value": 4.5, "target": 6.0, "unit": "小时",
     "ratio": 0.75, "ratio_level": "C"},
    {"code": "quality_score", "value": 88.0, "target": 100.0, "unit": "分",
     "ratio": 0.88, "ratio_level": "B"},
]
# ⚠️ **档位边界**（level_scale）——
#
# payload 里每个指标都带 level_scale，模型写「B 级区间（0.8≤比例<1.0）」
# 或「B 级区间（80%至100%）」是忠实引用档位定义。
# `0.8`/`1.0`/`0`/`999` 都必须算合法数字。
_LEVEL_SCALE = [
    {"level": "C", "min": 0, "max": 0.8},
    {"level": "B", "min": 0.8, "max": 1.0},
    {"level": "A", "min": 1.0, "max": 999},
]
METRICS_WITH_SCALE = [
    {**m, "level_scale": _LEVEL_SCALE} for m in METRICS_WITH_RATIO
]
LEGAL_REFS = ["e1", "e2"]

CASES = [
    (
        "原文里有的引文 → 不该报",
        ["他完成了「完成客户需求调研与方案设计」这项工作"],
        {"fabricated_quotes": 0},
    ),
    (
        "编造的引文 → 该报",
        ["他表示「获得了年度优秀员工称号，公司营业额增长 300%」"],
        # ⚠️ 这里有**两处**独立的编造：① 荣誉 ② 经营数据。
        #    期望值是 2 —— 计数必须按「独立编造点」算，不是按「引文条数」算。
        {"fabricated_quotes": 1, "fabricated_facts": 2},
    ),
    (
        "编造数字（原文没有 87%）→ 该报",
        ["本周期任务完成率为 87%，超出预期"],
        {"fabricated_numbers": 1},
    ),
    (
        "原文有的数字（100%）→ 不该报",
        ["本周期完成率 100%"],
        {"fabricated_numbers": 0},
    ),
    (
        "非法引用 ID → 该报",
        ["详见 00000000-0000-4000-8000-000000000000 这条记录"],
        {"illegal_references": 1},
    ),
    (
        "合法引用 ID → 不该报",
        ["详见 e1 这条记录"],
        {"illegal_references": 0},
    ),
    (
        "分数泄漏 → 必须报（红线）",
        ["本周期个人绩效分：95 分"],
        {"score_leak": 1},
    ),
    (
        "提到等级不算红线（那是评级建议）",
        ["建议评级为 A 级"],
        {"score_leak": 0},
    ),
    (
        "考勤被当扣分 → 必须报",
        ["迟到 1 次，扣分 2 分，拉低了整体得分"],
        {"attendance_penalized": 1},
    ),
    (
        "考勤仅作参考 → 不该报",
        ["本周期有迟到记录，考勤异常仅供参考，不自动扣分"],
        {"attendance_penalized": 0},
    ),
    # ------------------------------------------------------------------
    # ⚠️ 以下 5 例是**第一版压测暴露的真实误报**（人工逐条核对后加的）。
    #    没有它们，压测会 100% 误报 → 结论毫无价值。
    # ------------------------------------------------------------------
    (
        "UUID 片段不算编造数字",
        ["证据 dc3c789f-b931-4073-9672-6165b30f0dd9 显示已完成"],
        {"fabricated_numbers": 0},
    ),
    (
        "日期不算编造数字",
        ["本期存在缺卡 2 次记录（记录时间 2026-11-14）"],
        {"fabricated_numbers": 0},
    ),
    (
        "等级区间上界不算编造数字",
        ["落在 level_scale 中 A 级的取值区间，即从 1.0 到 999"],
        {"fabricated_numbers": 0},
    ),
    (
        "序数不算编造数字",
        ["两项计划任务均已完成，共 3 个需求"],
        {"fabricated_numbers": 0},
    ),
    (
        "日期时间戳不算非法引用 ID",
        ["记录 attendance:2026-11-14 07:52:40.388234 已归档"],
        {"illegal_references": 0},
    ),
    (
        "年月不算编造数字（标题里的上下文）",
        ["## 个人月度总结（2026年11月）"],
        {"fabricated_numbers": 0},
    ),
    (
        "方括号区间不算编造数字",
        ["落在 level_scale 中 A 档区间 [1.0, 999) 内"],
        {"fabricated_numbers": 0},
    ),
    (
        "分数数字只算泄漏，不重复算编造",
        ["客户确认满足业务诉求（个人绩效分 95，审批通过）"],
        {"fabricated_numbers": 0, "score_leak": 1},
    ),
    (
        "Markdown 列表序号不算编造数字",
        ["\n1. 第一项完成\n2. 第二项完成\n3. 沉淀文档模板"],
        {"fabricated_numbers": 0},
    ),
    (
        "引用 impact 里的审批意见 → 不算编造（需传 evidence_impacts）",
        ["审批意见为「成果内容与证据符合要求，同意通过」"],
        {"fabricated_quotes": 0},
    ),
    (
        "合规免责表述不算分数泄漏",
        ["本总结仅基于给定证据做事实性归纳，不涉及分数计算与总分复述"],
        {"score_leak": 0},
    ),
    (
        "考勤占位符不算非法证据引用",
        ["本月存在缺卡 2 次的情况（证据：attendance_anomalies）"],
        {"illegal_references": 0, "attendance_placeholder": 1},
    ),
    (
        "模型自己概括的流程不算编造引文",
        ["继续沿用本月「调研—设计—实施—联调」的闭环推进方式"],
        {"fabricated_quotes": 0},
    ),
    (
        "周期编码 202611 不算编造数字",
        ["## 个人月度总结（202611）"],
        {"fabricated_numbers": 0},
    ),
    (
        "en dash 区间不算编造数字",
        ["落在 level_scale 的 A 区间（1.0–999）"],
        {"fabricated_numbers": 0},
    ),
    (
        "裸 attendance 占位符不算非法引用",
        ["本月存在缺卡 2 次【attendance】"],
        {"illegal_references": 0, "attendance_placeholder": 1},
    ),
    (
        "模型对原文的改写不算编造引文",
        ["延续“问题即发现即闭环”的做法"],
        {"fabricated_quotes": 0},
    ),
    (
        # ⚠️ **真实压测抓到的**（第三轮才出现 —— 模型非确定性）：
        #   原文只说「问题均已闭环」，
        #   模型却编出「问题当天处理完毕」这个**具体做法**并放在引号里。
        #   —— 锚定词只有「问题」（1 个）→ 达不到改写门槛 → **真编造**，该报。
        "模型编造具体工作做法并放进引号 → 该报（真实案例）",
        ["继续坚持「问题当天处理完毕」的做法，以缩短联调周期"],
        {"fabricated_quotes": 1},
    ),
    (
        "「考勤仅作参考，不涉及自动扣分」不算考勤被当扣分",
        ["- 规范打卡流程，减少缺卡情况；考勤仅作参考，不涉及自动扣分。"],
        {"attendance_penalized": 0},
    ),
    (
        "「不会作为绩效扣分依据」不算考勤被当扣分",
        ["**考勤异常不自动扣分，仅作参考**，不会作为绩效扣分依据。"],
        {"attendance_penalized": 0},
    ),
    (
        "「该事项仅作参考，不涉及自动扣分」不算考勤被当扣分",
        ["- 针对缺卡情况规范日常打卡，减少考勤异常记录；该事项仅作参考，不涉及自动扣分。"],
        {"attendance_penalized": 0},
    ),
    (
        "带时间戳的日期不算编造数字",
        ["（该考勤记录未提供，时间戳 2026-11-14 07:52:40）"],
        {"fabricated_numbers": 0},
    ),
    (
        "不等式区间不算编造数字",
        ["落在 level_scale 中 A 档（1.0 ≤ 比值 < 999）区间"],
        {"fabricated_numbers": 0},
    ),
    (
        # ⚠️ **第四轮才暴露** —— 模型换了一种写法：
        #   「处于 A 档 min 1.0 至 max 999 区间」
        #   中间夹着「min」「至」「max」三段词 → 上一版区间正则匹配不到 → `999` 被当成编造。
        "min/max 区间不算编造数字",
        ["按 level_scale 落在 A 档（ratio 1.0，处于 A 档 min 1.0 至 max 999 区间）"],
        {"fabricated_numbers": 0},
    ),
    (
        "attendance:1 占位符不算非法引用",
        ["本月存在缺卡 2 次的考勤记录 [attendance:1]"],
        {"illegal_references": 0, "attendance_placeholder": 1},
    ),
    # ------------------------------------------------------------------
    # ⚠️ 以下是**真实压测抓到的误报**（第二组用例：栗月椿/李凯）
    #    —— 模型转述**我们给的派生比率**被判成「编造数字」。
    #    没有这些回归用例，修正会被后人改回去。
    # ------------------------------------------------------------------
    (
        "模型转述派生比率（0.95/0.85）不算编造数字",
        [
            "GMV 为 95 元，目标 100 元，比率为 0.95，按给定 level_scale 落在 B 档",
            "转化率为 85%，目标 100%，比率为 0.85，按给定 level_scale 落在 B 档",
        ],
        {"fabricated_numbers": 0},
        METRICS_WITH_RATIO,
    ),
    (
        "模型转述派生比率（0.75/0.88）不算编造数字",
        [
            "响应时长为 4.5 小时，目标 6 小时，按给定口径比率为 0.75，落在 level_scale 的 C 档",
            "质量，目标 100 分，比率为 0.88，按给定 level_scale 落在 B 档",
        ],
        {"fabricated_numbers": 0},
        METRICS_WITH_RATIO,
    ),
    (
        "派生比率 + 浮点尾数（0.8800000000000001）也不算编造数字",
        ["质量，目标 100 分，比率为 0.88，按给定 level_scale 落在 B 档"],
        {"fabricated_numbers": 0},
        [
            {"code": "quality_score", "value": 88.0, "target": 100.0, "unit": "分",
             "ratio": 0.8800000000000001, "ratio_level": "B"},
        ],
    ),
    (
        "档位边界（0.8/1.0/999）不算编造数字",
        [
            "GMV 为 95，目标 100，比率 0.95，落在 B 级区间（0.8≤比例<1.0）",
            "响应时长为 4.5 小时，比率 0.75，落在 C 级区间（0–0.8）",
        ],
        {"fabricated_numbers": 0},
        METRICS_WITH_SCALE,
    ),
    (
        # ⚠️ 模型把 0.8 写成 **80%** —— 语义完全等价，
        #    也是忠实引用档位定义，不该报。
        "档位边界写成百分比（80%）也不算编造数字",
        [
            "GMV实际为95元，目标为100元，达成率为95%，落在B级区间（80%至100%）内",
            "转化率实际为85%，目标为100%，达成率为85%，处于B级区间（80%至100%）",
        ],
        {"fabricated_numbers": 0},
        METRICS_WITH_SCALE,
    ),
    (
        "证据条数不算编造数字（模型说「6 条」是事实）",
        ["现有 6 条 plan_result 证据均显示验证周成果已提交、完成率为 100%"],
        {"fabricated_numbers": 0},
        METRICS_WITH_SCALE,
        ["evidence_count 6"],
    ),
    (
        # ⚠️ 这里**两处**都是编造：`87%` 和 `0.87` ——
        #    比率在合法集合里**不能掩盖**其它真编造的数字。
        "真编造的数字仍然要报（比率在也不能掩盖）",
        ["本月完成率为 87%，比率为 0.87，远超预期"],
        {"fabricated_numbers": 2},
        METRICS_WITH_RATIO,
    ),
    (
        # ⚠️ 「质量得分：实际 88 分」里的「得分：」是**指标名称**
        #    （quality_score 的中文表述），88 是证据里的原始指标值，
        #    不是绩效得分 → 不该报分数泄漏。
        "指标名称里的「得分：」不算分数泄漏",
        ["- 质量得分：实际 88 分，目标 100 分。"],
        {"score_leak": 0},
    ),
]


def main() -> int:
    passed = 0
    failed = 0
    print("=" * 70)
    print("幻觉检测器自检")
    print("=" * 70)

    for case in CASES:
        label, texts, expect = case[0], case[1], case[2]
        # ⚠️ 个别案例需要**不同的指标集**（如派生比率那组）——
        #    所以允许 CASES 第 4 项覆盖 metric_values。
        metrics = METRICS
        if len(case) > 3 and isinstance(case[3], list):
            metrics = case[3]
        # ⚠️ 第 5 项可覆盖「兜底来源」（证据条数等）
        extra = None
        if len(case) > 4 and isinstance(case[4], list):
            extra = case[4]
        issues = detect_hallucination(
            texts,
            evidence_summaries=EVIDENCE,
            metric_values=metrics,
            legal_refs=LEGAL_REFS,
            attendance_present=True,
            # ⚠️ 考勤次数也是原文 —— 少了它，「缺卡 2 次」会被误判成编造
            attendance_summaries=["缺卡 2 次（缺卡异常仅供参考，不自动扣分）"],
            # ⚠️ impact 也是原文 —— 少了它，引用审批意见会被误判成编造引文
            evidence_impacts=["完成情况：方案通过技术评审。审批意见：成果内容与证据符合要求，同意通过"],
            extra_source_texts=extra,
        )
        ok = True
        details = []
        for key, want in expect.items():
            got = len(issues.get(key, []))
            if got != want:
                ok = False
                details.append(f"{key}: 期望 {want} 实际 {got} -> {issues.get(key)}")
        if ok:
            passed += 1
            print(f"  [PASS] {label}")
        else:
            failed += 1
            print(f"  [FAIL] {label}")
            for d in details:
                print(f"         {d}")

    print("\n" + "=" * 70)
    print(f"检测器自检：{passed} 通过 / {failed} 失败")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
