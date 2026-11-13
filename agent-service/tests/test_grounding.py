"""来源锚定（Grounding）的测试。

核心要证明的：**幻觉引文会被抓住**。

这比 reviewops 的 FairnessReport 强一个量级 —— 后者只统计"有多少结论没引用证据"，
前者验证"引用的引文是不是真的在原文里"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent-service"
sys.path.insert(0, str(AGENT))

from app.grounding import (  # noqa: E402
    CoverageStatus,
    DimensionStatus,
    GroundingService,
    Rule,
    SourceReference,
    normalize_for_containment,
    normalize_for_match,
    redact_obj,
    redact_text,
)
from app.perf_rules import (  # noqa: E402
    build_grounding_service,
    build_performance_rules,
    relevant_for_rule,
    required_for_rule,
)


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------


def test_redact_mobile():
    assert "13812345678" not in redact_text("我的手机号是13812345678")
    assert "138****5678" in redact_text("我的手机号是13812345678")


def test_redact_id_card():
    assert "110101" in redact_text("身份证110101199001011234")
    assert "199001011234" not in redact_text("身份证110101199001011234")


def test_redact_email():
    assert "***@example.com" in redact_text("联系 zhangsan@example.com")


def test_redact_employee_no():
    out = redact_text("员工编号: E-001 绩效良好")
    assert "E-001" not in out
    assert "员工编号:***" in out


def test_redact_obj_sensitive_keys():
    out = redact_obj({"mobile": "13812345678", "name": "李晓", "nested": {"email": "a@b.com"}})
    assert out["mobile"] == "***"
    assert out["name"] == "李晓"
    assert out["nested"]["email"] == "***"


def test_redact_preserves_ordinary_numbers():
    """普通数字不能被误伤 —— GMV 1250000 这种必须保留。"""
    assert redact_text("GMV 达成 1250000 元") == "GMV 达成 1250000 元"


# ---------------------------------------------------------------------------
# 引文匹配
# ---------------------------------------------------------------------------


def test_normalize_handles_fullwidth_and_case():
    """全角转半角、大小写归一。"""
    assert normalize_for_match("ＧＭＶ") == "gmv"
    assert normalize_for_match("１２５") == "125"
    assert normalize_for_match("GMV  125") == "gmv 125"


def test_normalize_containment_ignores_whitespace():
    """宽松匹配：忽略空白差异。"""
    assert normalize_for_containment("ＧＭＶ 125") == normalize_for_containment("gmv125")


def test_quote_must_appear_in_source():
    svc = build_grounding_service()
    sources = [
        SourceReference(id="SRC_0001", path="businessData.metrics[0].value", label="GMV", content="1250000")
    ]
    # 引文真实存在
    assert svc._appears_in_any("1250000", sources)
    # 引文是编造的
    assert not svc._appears_in_any("9999999", sources)


# ---------------------------------------------------------------------------
# 核心：维度锚定
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc():
    return build_grounding_service()


@pytest.fixture()
def rules():
    return build_performance_rules()


@pytest.fixture()
def business_data():
    return {
        "metrics": [
            {
                "value": 1250000,
                "target": 1200000,
                "ratio": 1.0417,
                "ratio_level": "B",
                "source": "api:ecommerce",
                "as_of": "2026-10-31",
                "evidence_ids": "e1,e2",
            }
        ],
        "evidence": [
            {"summary": "双11大促活动 GMV 超额完成 4.2%", "impact": "带来新增客户 300 人"},
            {"summary": "转化率从 3.3% 提升到 3.8%", "impact": "优化了详情页排序"},
        ],
        "attendance": [
            {"summary": "迟到 3 次（迟到异常仅供参考，不自动扣分）"},
        ],
        "rubric": [
            {"levels_json": "A: >=1.05, B: 0.95~1.05, C: <0.95"},
        ],
    }


def test_prepare_builds_closed_source_catalog(svc, rules, business_data):
    """模型只能引用本次请求提供的来源。"""
    req = svc.prepare("perf_review", rules, business_data)
    paths = [s.path for s in req.source_catalog]

    assert any("metrics[0].value" in p for p in paths)
    assert any("evidence[0].summary" in p for p in paths)
    # 来源 id 唯一且连续
    assert len({s.id for s in req.source_catalog}) == len(req.source_catalog)
    # id 格式
    assert req.source_catalog[0].id.startswith("SRC_")


def test_hallucinated_quote_is_downgraded(svc, rules, business_data):
    """★ 核心：模型编造的引文 → 维度降级为 UNKNOWN，不是被静默接受。"""
    req = svc.prepare("perf_review", rules, business_data)

    # 模型声称结论成立，引文是编造的
    raw = {
        "analysisDimensions": [
            {
                "rule_id": "PERF_RESULT",
                "status": "PASS",
                "conclusion": "GMV 达成目标",
                "quote": "本月完成了 500 万销售额",  # ← 编造
                "basis": "后台数据",
                "references": ["SRC_0001"],
                "confidence": 0.9,
            }
        ]
    }

    result = svc.validate(req, raw)
    dim = next(d for d in result.dimensions if d.rule_id == "PERF_RESULT")

    assert dim.status == DimensionStatus.UNKNOWN.value
    assert "未通过原文真实性校验" in dim.basis
    assert result.grounded is False
    assert result.downgraded_count > 0


def test_real_quote_passes(svc, rules, business_data):
    """引文真实存在且覆盖完整 → 结论被采信。

    PERF_RESULT 要求覆盖 value + target 两个来源（"覆盖完整"才允许判 PASS）。
    """
    req = svc.prepare("perf_review", rules, business_data)
    value_src = next(s for s in req.source_catalog if "metrics[0].value" in s.path)
    target_src = next(s for s in req.source_catalog if "metrics[0].target" in s.path)

    raw = {
        "analysisDimensions": [
            {
                "rule_id": "PERF_RESULT",
                "status": "PASS",
                "conclusion": "GMV 达成目标",
                "quote": value_src.content,
                "basis": "业务层算出的达成率 1.0417，落在 B 区间",
                "references": [value_src.id, target_src.id],
                "confidence": 0.95,
            }
        ]
    }

    result = svc.validate(req, raw)
    dim = next(d for d in result.dimensions if d.rule_id == "PERF_RESULT")
    assert dim.status == DimensionStatus.PASS.value
    assert dim.quote == value_src.content
    assert dim.references == sorted([value_src.id, target_src.id])


def test_irrelevant_source_rejected(svc, rules, business_data):
    """引用了与该维度无关的字段 → 不能形成结论。"""
    req = svc.prepare("perf_review", rules, business_data)
    # 找一个考勤来源（与 PERF_RESULT 无关）
    att = next((s for s in req.source_catalog if "attendance[0]" in s.path), None)
    if att is None:
        pytest.skip("样本中无考勤来源")

    raw = {
        "analysisDimensions": [
            {
                "rule_id": "PERF_RESULT",
                "status": "PASS",
                "conclusion": "达成目标",
                "quote": att.content,
                "basis": "看考勤",
                "references": [att.id],
            }
        ]
    }
    result = svc.validate(req, raw)
    dim = next(d for d in result.dimensions if d.rule_id == "PERF_RESULT")
    assert dim.status == DimensionStatus.UNKNOWN.value
    assert "无关的字段" in dim.basis


def test_incomplete_coverage_blocks_pass(svc, rules, business_data):
    """判定"通过"必须覆盖该维度所需的全部关键字段（value + target）。"""
    req = svc.prepare("perf_review", rules, business_data)
    value_src = next(s for s in req.source_catalog if "metrics[0].value" in s.path)

    raw = {
        "analysisDimensions": [
            {
                "rule_id": "PERF_RESULT",
                "status": "PASS",
                "conclusion": "达成",
                "quote": value_src.content,
                "basis": "看了实测值",
                "references": [value_src.id],  # ← 缺 target
            }
        ]
    }
    result = svc.validate(req, raw)
    dim = next(d for d in result.dimensions if d.rule_id == "PERF_RESULT")
    assert dim.status == DimensionStatus.UNKNOWN.value
    assert "未覆盖" in dim.basis


def test_risk_without_validated_issue_rejected(svc, rules, business_data):
    """模型标了风险但问题没通过校验 → 不采信该风险判定。"""
    req = svc.prepare("perf_review", rules, business_data)

    raw = {
        "analysisDimensions": [
            {
                "rule_id": "PERF_EVIDENCE",
                "status": "RISK",
                "conclusion": "证据不足",
                "quote": "编造的引文",
                "basis": "感觉",
                "references": ["SRC_0001"],
            }
        ],
        "issues": [],  # ← 没有对应问题
    }
    result = svc.validate(req, raw)
    dim = next(d for d in result.dimensions if d.rule_id == "PERF_EVIDENCE")
    assert dim.status == DimensionStatus.UNKNOWN.value
    assert "未通过规则编号或原文引用校验" in dim.basis


def test_missing_dimension_downgrades(svc, rules, business_data):
    """模型没返回的维度 → UNKNOWN，且给出原因。"""
    req = svc.prepare("perf_review", rules, business_data)
    result = svc.validate(req, {"analysisDimensions": []})
    assert len(result.dimensions) == len(rules)
    assert all(d.status == DimensionStatus.UNKNOWN.value for d in result.dimensions)


def test_all_dimensions_returned_even_when_model_omits(svc, rules, business_data):
    """模型只返回一个维度，其余也必须出现在结果里（不能静默丢失）。"""
    req = svc.prepare("perf_review", rules, business_data)
    result = svc.validate(req, {"analysisDimensions": []})
    assert {d.rule_id for d in result.dimensions} == {r.id for r in rules}


# ---------------------------------------------------------------------------
# 问题锚定
# ---------------------------------------------------------------------------


def test_issue_with_hallucinated_quote_dropped(svc, rules, business_data):
    """引文编造的问题直接丢弃（不是降级）—— 问题是"有或没有"，不是程度。"""
    req = svc.prepare("perf_review", rules, business_data)
    raw = {
        "issues": [
            {
                "rule_id": "PERF_EVIDENCE",
                "code": "EVIDENCE_MISSING",
                "title": "缺少成果证据",
                "field": "businessData.evidence[0].summary",
                "quote": "这段话原文里根本不存在",
                "basis": "看起来没有",
                "references": ["SRC_0001"],
                "confidence": 0.8,
            }
        ]
    }
    result = svc.validate(req, raw)
    assert len(result.issues) == 0


def test_issue_with_real_quote_kept(svc, rules, business_data):
    req = svc.prepare("perf_review", rules, business_data)
    ev = next(s for s in req.source_catalog if "evidence[0].summary" in s.path)

    raw = {
        "issues": [
            {
                "rule_id": "PERF_EVIDENCE",
                "code": "EVIDENCE_WEAK",
                "title": "证据表述笼统",
                "field": ev.path,
                "quote": ev.content,
                "basis": "缺少可验证的数据",
                "references": [ev.id],
                "confidence": 0.8,
            }
        ]
    }
    result = svc.validate(req, raw)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.quote == ev.content
    assert issue.severity == "HIGH"


def test_invalid_reference_id_drops_whole_group(svc, rules, business_data):
    """引用 id 无效 → 整组引用作废（避免"半真半假"）。"""
    req = svc.prepare("perf_review", rules, business_data)
    ev = next(s for s in req.source_catalog if "evidence[0].summary" in s.path)

    raw = {
        "issues": [
            {
                "rule_id": "PERF_EVIDENCE",
                "quote": ev.content,
                "basis": "x",
                "references": [ev.id, "SRC_9999"],  # ← 一个无效
            }
        ]
    }
    result = svc.validate(req, raw)
    assert len(result.issues) == 0


# ---------------------------------------------------------------------------
# 风险冲突
# ---------------------------------------------------------------------------


def test_pass_blocked_when_evidence_risk_conflicts(svc, rules, business_data):
    """证据维度发现实质风险 → 业绩维度不得同时判定为通过。

    注意：PERF_RESULT 只接受指标字段作为来源，不接受证据字段。
    所以这里必须让业绩维度引用**指标来源**，同时让证据维度产出有效问题。
    """
    req = svc.prepare("perf_review", rules, business_data)
    ev = next(s for s in req.source_catalog if "evidence[0].summary" in s.path)
    value_src = next(s for s in req.source_catalog if "metrics[0].value" in s.path)
    target_src = next(s for s in req.source_catalog if "metrics[0].target" in s.path)

    raw = {
        "issues": [
            {
                "rule_id": "PERF_EVIDENCE",
                "quote": ev.content,
                "basis": "证据只有一句话，无法核验",
                "references": [ev.id],
                "confidence": 0.9,
            }
        ],
        "analysisDimensions": [
            {
                "rule_id": "PERF_RESULT",
                "status": "PASS",
                "conclusion": "达成目标",
                "quote": value_src.content,
                "basis": "业务层算出的达成率 1.0417",
                # PERF_RESULT 需要覆盖 value + target 两个来源
                "references": [value_src.id, target_src.id],
            }
        ],
    }
    result = svc.validate(req, raw)

    # 证据问题本身有效（引文真实）
    assert len(result.issues) == 1

    dim = next(d for d in result.dimensions if d.rule_id == "PERF_RESULT")
    assert dim.status == DimensionStatus.UNKNOWN.value
    assert "实质风险" in dim.basis


# ---------------------------------------------------------------------------
# 验收项覆盖
# ---------------------------------------------------------------------------


def test_coverage_requires_real_evidence_quote(svc, rules):
    """验收项：引文必须真实存在，否则 UNKNOWN。"""
    data = {"acceptanceCriteria": [{"id": "AC_1", "text": "需提交后台截图"}]}
    req = svc.prepare("perf_review", rules, data)

    raw = {
        "acceptanceCoverage": [
            {
                "criterion_id": "AC_1",
                "status": "PROVEN",
                "basis": "已提交",
                "evidence_quote": "完全不存在的引文",
                "evidence_references": ["SRC_0001"],
            }
        ]
    }
    result = svc.validate(req, raw)
    cov = result.coverage[0]
    assert cov.status == CoverageStatus.UNKNOWN.value
    assert "未通过原文真实性校验" in cov.basis


def test_coverage_proven_when_evidence_real(svc, rules):
    data = {
        "acceptanceCriteria": [{"id": "AC_1", "text": "需提交后台截图"}],
        "evidence": [{"summary": "已上传后台 GMV 截图"}],
    }
    req = svc.prepare("perf_review", rules, data)
    ev = next(s for s in req.source_catalog if "evidence[0].summary" in s.path)

    raw = {
        "acceptanceCoverage": [
            {
                "criterion_id": "AC_1",
                "status": "PROVEN",
                "basis": "截图已上传",
                "evidence_quote": ev.content,
                "evidence_references": [ev.id],
            }
        ]
    }
    result = svc.validate(req, raw)
    cov = result.coverage[0]
    assert cov.status == CoverageStatus.PROVEN.value
    assert cov.evidence_quote == ev.content


# ---------------------------------------------------------------------------
# 降级语义
# ---------------------------------------------------------------------------


def test_null_model_output_returns_safe_result(svc, rules, business_data):
    """模型完全没返回 → 安全的空结果，不是异常。"""
    req = svc.prepare("perf_review", rules, business_data)
    result = svc.validate(req, None)
    assert result.grounded is False
    assert result.dimensions == []
    assert "未返回可核验结论" in result.summary


def test_grounded_result_serializable(svc, rules, business_data):
    """结果可序列化（要进 AiTrace 和 API 响应）。"""
    req = svc.prepare("perf_review", rules, business_data)
    result = svc.validate(req, {"analysisDimensions": []})
    d = result.to_dict()
    assert "dimensions" in d and "issues" in d and "coverage" in d
    import json

    json.dumps(d, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 规则语义
# ---------------------------------------------------------------------------


def test_rule_semantics_wired():
    """绩效规则语义被正确注入。"""
    assert required_for_rule("PERF_RESULT", "businessData.metrics[0].value")
    assert not required_for_rule("PERF_RESULT", "businessData.attendance[0].summary")
    assert relevant_for_rule("PERF_EVIDENCE", "businessData.evidence[0].summary")
    assert not relevant_for_rule("PERF_EVIDENCE", "businessData.metrics[0].value")
