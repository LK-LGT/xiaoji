"""draft 端点的红线清洗集成测试（P3-2）。

用**压测抓到的真实模型输出**做端到端验证。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sanitize import sanitize_comment  # noqa: E402

# ⚠️ 这段 reason 是压测里 DeepSeek **真的**写出来的（用例 4）
REAL_REASON = (
    "本期计划执行率实际值为 100.0%，达到目标值 100.0%，比值 1.0，"
    "按 level_scale 落入 A 档（≥1.0）。证据显示两项计划任务均已完成且完成率为 100%："
    "一是完成客户需求调研与方案设计，方案通过技术评审并获得客户确认满足业务诉求"
    "（绩效分 95，审批已给出意见）；二是完成方案落地实施与联调，"
    "联调过程无阻塞缺陷，问题均已闭环，成果内容与证据符合要求并获审批通过（绩效分 100）。"
    "综合两项计划的完成情况，判定为 A。"
)

LEGAL = [
    "dc3c789f-b931-4073-9672-6165b30f0dd9",
    "1d45edbc-f585-4524-b9e0-6be982f54474",
]


def test_real_reason_score_leak_removed():
    """真实模型输出里的两处分数必须被清掉。"""
    result = sanitize_comment(REAL_REASON, legal_refs=LEGAL)
    assert "绩效分" not in result.text, result.text
    assert "绩效分 95" not in result.text
    assert "绩效分 100" not in result.text
    # 业务内容必须保留
    assert "100%" in result.text
    assert "A 档" in result.text
    assert "技术评审" in result.text
    assert "方案落地实施与联调" in result.text
    assert result.removed, "应记录被删片段"


def test_real_reason_no_empty_paren():
    """删完分数后不能留下悬空括号「（）」。"""
    result = sanitize_comment(REAL_REASON, legal_refs=LEGAL)
    assert "（）" not in result.text, result.text
    assert "(  )" not in result.text, result.text


def test_real_reason_no_double_punctuation():
    """删完不能留下「，，」这种重复标点。"""
    result = sanitize_comment(REAL_REASON, legal_refs=LEGAL)
    for bad in ("，，", "；；", "，。", "、，"):
        assert bad not in result.text, f"{bad} -> {result.text}"


def test_real_reason_keeps_percentages():
    """达成率 100% 是业务事实，必须留着。

    ⚠️ 注意原文里既有「100.0%」也有「100%」——
       所以不能简单 count("100%")，否则「100.0%」里的「100%」匹配不上。
    """
    result = sanitize_comment(REAL_REASON, legal_refs=LEGAL)
    assert result.text.count("100.0%") == 2, result.text
    assert "完成率为 100%" in result.text, result.text
    assert "比值 1.0" in result.text, result.text


# ---------------------------------------------------------------------------
# 编造引文 —— 2026-11-20 第三轮压测抓到的真实模型输出
# ---------------------------------------------------------------------------

# ⚠️ 这段评语是压测里 DeepSeek **真的**写出来的（第三轮才出现 —— 模型非确定性）。
REAL_COMMENT_WITH_FABRICATED_QUOTE = (
    "## 个人月度总结（2026 年 11 月）\n\n"
    "### 二、主要工作成果\n"
    "1. **方案实施与联调**：方案已完成实施，并与相关方完成联调验证"
    "（附件 integration-record.pdf）；联调过程中无阻塞性缺陷，"
    "所有问题均已处理完毕，完成率为 100%（evidence: 1d45edbc-f585-4524-b9e0-6be982f54474）。\n\n"
    "### 四、下月改进方向\n"
    "1. 在实施与联调环节，继续坚持「问题当天处理完毕」的做法，以缩短联调周期"
    "（evidence: 1d45edbc-f585-4524-b9e0-6be982f54474）。\n"
)

#: 模型能看到的原文（证据 summary + impact）
REAL_SOURCES = [
    "完成客户需求调研与方案设计；完成率 100%；附件：requirement-research-report.pdf",
    "完成方案落地实施与联调；完成率 100%；附件：integration-record.pdf",
    "完成情况：方案已完成落地实施，与相关方完成联调验证，联调过程无阻塞缺陷，"
    "问题均已闭环。；个人绩效分：100；审批意见：成果内容与证据符合要求，同意通过。",
]


def test_real_fabricated_quote_downgraded():
    """模型编的「问题当天处理完毕」→ 去掉引号，**内容保留**。"""
    result = sanitize_comment(
        REAL_COMMENT_WITH_FABRICATED_QUOTE,
        legal_refs=LEGAL,
        source_texts=REAL_SOURCES,
    )
    assert "「问题当天处理完毕」" not in result.text, result.text
    assert "问题当天处理完毕" in result.text, "内容要保留，只是降级"
    assert result.removed, "应记录被降级的片段"


def test_real_fabricated_quote_keeps_legal_citation():
    """降级引文时**不能**误伤合法引用 ID。"""
    result = sanitize_comment(
        REAL_COMMENT_WITH_FABRICATED_QUOTE,
        legal_refs=LEGAL,
        source_texts=REAL_SOURCES,
    )
    assert "1d45edbc-f585-4524-b9e0-6be982f54474" in result.text, result.text


def test_real_fabricated_quote_keeps_rest_of_comment():
    """只动那一句，其余内容（含 Markdown 结构）不能变。"""
    result = sanitize_comment(
        REAL_COMMENT_WITH_FABRICATED_QUOTE,
        legal_refs=LEGAL,
        source_texts=REAL_SOURCES,
    )
    assert "## 个人月度总结（2026 年 11 月）" in result.text, result.text
    assert "### 二、主要工作成果" in result.text, result.text
    assert "integration-record.pdf" in result.text, result.text
    assert "完成率为 100%" in result.text, result.text
