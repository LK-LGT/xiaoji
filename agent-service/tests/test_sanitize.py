"""红线清洗器单测（P3-2 压测驱动）。

⚠️ 这些用例全部来自**真实压测里抓到的模型输出** ——
   不是拍脑袋编的，是模型真的这么写的。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sanitize import (  # noqa: E402
    sanitize_comment,
    strip_fabricated_quotes,
    strip_fake_references,
    strip_score_leak,
)

LEGAL = [
    "dc3c789f-b931-4073-9672-6165b30f0dd9",
    "1d45edbc-f585-4524-b9e0-6be982f54474",
]

#: 模型能看到的**原文全集**（证据 summary + impact + 考勤摘要）
SOURCES = [
    "完成客户需求调研与方案设计；完成率 100%；附件：requirement-research-report.pdf",
    "完成方案落地实施与联调；完成率 100%；附件：integration-record.pdf",
    # ⚠️ impact 也是原文 —— 审批意见、完成情况
    "完成情况：已完成客户需求调研并输出方案设计文档，方案通过技术评审，"
    "客户确认满足业务诉求。；个人绩效分：95；审批意见：同意通过。",
    "完成情况：方案已完成落地实施，与相关方完成联调验证，联调过程无阻塞缺陷，"
    "问题均已闭环。；个人绩效分：100；审批意见：成果内容与证据符合要求，同意通过。",
    "缺卡 2 次",
]


# ---------------------------------------------------------------------------
# 分数泄漏
# ---------------------------------------------------------------------------


def test_strip_score_leak_removes_score():
    """模型抄了 impact 里的「个人绩效分 95」→ 必须删掉。"""
    text = (
        "证据一显示完成客户需求调研与方案设计，完成率 100%，"
        "客户确认满足业务诉求，个人绩效分 95；"
    )
    result = strip_score_leak(text)
    assert "个人绩效分" not in result.text, result.text
    assert "完成率 100%" in result.text, "不该删掉正常内容"
    assert result.removed, "应该记录被删的片段"
    assert result.changed


def test_strip_score_leak_keeps_percentage():
    """「完成率 100%」是达成率，不是绩效分 → 必须保留。"""
    text = "本月计划执行率达成 100%，符合目标要求。"
    result = strip_score_leak(text)
    assert "100%" in result.text
    assert not result.removed


def test_strip_score_leak_keeps_disclaimer():
    """模型的合规免责声明（含「分数」但无数值）→ 必须保留。"""
    text = "本总结仅基于给定证据做事实性归纳，不涉及分数计算与总分复述。"
    result = strip_score_leak(text)
    assert "不涉及分数计算" in result.text, result.text
    assert not result.removed


def test_strip_score_leak_keeps_level():
    """评级建议「建议评级为 A 级」→ 不是分数，保留。"""
    text = "综合达成情况，建议评级为 A 级。"
    result = strip_score_leak(text)
    assert "A 级" in result.text
    assert not result.removed


def test_strip_score_leak_handles_parentheses():
    """「（个人绩效分 95，审批通过）」→ 删分数但留括号里的其他内容。"""
    text = "客户确认满足业务诉求（个人绩效分 95，审批通过）"
    result = strip_score_leak(text)
    assert "个人绩效分" not in result.text, result.text
    assert "审批通过" in result.text, result.text


def test_strip_score_leak_multiple():
    """多处分数都要删干净。"""
    text = (
        "第一项个人绩效分 95；第二项个人绩效分为 100；"
        "总分 88 分；综合得分 92 分。"
    )
    result = strip_score_leak(text)
    assert "绩效分" not in result.text, result.text
    assert "总分" not in result.text, result.text
    assert "综合得分" not in result.text, result.text


# ---------------------------------------------------------------------------
# 编造引用 ID
# ---------------------------------------------------------------------------


def test_strip_fake_attendance_placeholder():
    """模型自造的 `attendance-2026-11-14` → 必须删。"""
    text = "本月存在缺卡 2 次（证据：attendance-2026-11-14）"
    result = strip_fake_references(text, LEGAL)
    assert "attendance-2026-11-14" not in result.text, result.text
    assert "缺卡 2 次" in result.text, "不该删掉正常内容"


def test_strip_fake_attendance_anomalies():
    """模型自造的 `attendance_anomalies` → 必须删。"""
    text = "本月记录到缺卡 2 次（证据：attendance_anomalies）。"
    result = strip_fake_references(text, LEGAL)
    assert "attendance_anomalies" not in result.text, result.text


def test_keeps_legal_reference():
    """合法引用 ID 的**格式**要保留 —— 那是可追溯性。"""
    text = "已完成需求调研 [evidence: dc3c789f-b931-4073-9672-6165b30f0dd9]。"
    result = strip_fake_references(text, LEGAL)
    assert "dc3c789f-b931-4073-9672-6165b30f0dd9" in result.text, result.text
    assert not result.removed


def test_strip_unknown_evidence_id():
    """引用了一个不在合法集合里的 UUID → 删掉。"""
    text = "详见 [evidence: 00000000-0000-4000-8000-000000000000]。"
    result = strip_fake_references(text, LEGAL)
    assert "00000000-0000-4000-8000-000000000000" not in result.text, result.text


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------


def test_sanitize_comment_both():
    """同时命中两条红线 → 都要处理。"""
    text = (
        "本月缺卡 2 次（证据：attendance-2026-11-14）；"
        "客户确认满足业务诉求，个人绩效分 95。"
    )
    result = sanitize_comment(text, legal_refs=LEGAL)
    assert "attendance-2026-11-14" not in result.text, result.text
    assert "个人绩效分" not in result.text, result.text
    assert "缺卡 2 次" in result.text
    assert "客户确认满足业务诉求" in result.text


def test_sanitize_comment_clean_input_unchanged():
    """合规输入**一个字都不该动**。"""
    text = (
        "## 个人月度总结（2026年11月）\n\n"
        "- 客户需求调研与方案设计已完成，完成率 100% "
        "[evidence: dc3c789f-b931-4073-9672-6165b30f0dd9]。\n"
        "- 考勤异常不自动扣分，仅作参考。\n"
    )
    result = sanitize_comment(text, legal_refs=LEGAL)
    assert result.text == text, result.text
    assert not result.changed


def test_sanitize_comment_empty():
    """空输入不能炸。"""
    result = sanitize_comment("", legal_refs=LEGAL)
    assert result.text == ""
    assert not result.changed


# ---------------------------------------------------------------------------
# 编造的引文（幻觉引文）
# ---------------------------------------------------------------------------


def test_strip_fabricated_quote_removes_quotes():
    """⚠️ **真实案例**（2026-11-20 第三轮压测抓到）。

    原文只说「问题均已闭环」，模型却编出「问题当天处理完毕」这个
    **具体做法**并放进引号 —— 暗示这是有出处的原话。

    期望：**去掉引号**（降级为普通陈述），**不删内容**。
    """
    text = "在实施与联调环节，继续坚持「问题当天处理完毕」的做法，以缩短联调周期。"
    result = strip_fabricated_quotes(text, SOURCES)
    assert "「问题当天处理完毕」" not in result.text, result.text
    assert "问题当天处理完毕" in result.text, "内容要保留，只是降级"
    assert result.removed == ["问题当天处理完毕"]


def test_strip_fabricated_quote_keeps_real_quote():
    """如实引用审批意见 → 引号**必须保留**。"""
    text = "第二项成果的审批意见明确为「成果内容与证据符合要求，同意通过」。"
    result = strip_fabricated_quotes(text, SOURCES)
    assert "「成果内容与证据符合要求，同意通过」" in result.text, result.text
    assert not result.removed
    assert not result.changed


def test_strip_fabricated_quote_keeps_paraphrase():
    """模型对原文的**改写**（「问题即发现即闭环」）→ 引号保留。"""
    text = "延续“问题即发现即闭环”的做法。"
    result = strip_fabricated_quotes(text, SOURCES)
    assert "“问题即发现即闭环”" in result.text, result.text
    assert not result.removed


def test_strip_fabricated_quote_without_sources_is_noop():
    """没有原文 → **不动**（宁缺勿错：无法判定就别改）。"""
    text = "继续坚持「问题当天处理完毕」的做法。"
    result = strip_fabricated_quotes(text, [])
    assert result.text == text
    assert not result.changed


def test_sanitize_comment_handles_fabricated_quote():
    """组合入口也要处理编造引文。"""
    text = (
        "### 四、下月改进方向\n"
        "1. 在实施与联调环节，继续坚持「问题当天处理完毕」的做法，"
        "以缩短联调周期（evidence: dc3c789f-b931-4073-9672-6165b30f0dd9）。\n"
    )
    result = sanitize_comment(text, legal_refs=LEGAL, source_texts=SOURCES)
    assert "「问题当天处理完毕」" not in result.text, result.text
    assert "问题当天处理完毕" in result.text
    # 合法引用要留着
    assert "dc3c789f-b931-4073-9672-6165b30f0dd9" in result.text
