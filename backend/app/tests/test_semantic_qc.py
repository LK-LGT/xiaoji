"""P2-1 acceptance: 评语语义质检 / 风险识别 / 面谈提纲（仅建议，不改分不写业务表）。"""
from backend.app.services import semantic_qc as svc

# ── 语义质检 ────────────────────────────────────────────────────────────────

def test_empty_comment_is_high_severity():
    """空评语是 HIGH 问题，且不再继续做措辞检查。"""
    issues = svc.review_comment_semantics(comment="", citation_count=0)
    assert len(issues) == 1
    assert issues[0].code == "COMMENT_EMPTY"
    assert issues[0].severity == "HIGH"
    assert issues[0].rule_id == "R-CMT-000"


def test_vague_phrase_detected():
    """空洞措辞被识别。"""
    issues = svc.review_comment_semantics(
        comment="本期工作认真，表现良好，总体不错。", citation_count=1, has_gap_section=True
    )
    codes = {i.code for i in issues}
    assert "COMMENT_VAGUE_PHRASE" in codes


def test_labeling_language_is_high():
    """标签化表述是 HIGH 问题。"""
    issues = svc.review_comment_semantics(
        comment="该员工态度差，能力也不合格。", citation_count=1, has_gap_section=True
    )
    labeling = [i for i in issues if i.code == "COMMENT_LABELING_LANGUAGE"]
    assert labeling
    assert labeling[0].severity == "HIGH"


def test_unsupported_claim_only_when_no_citation():
    """无据推断：没引用才报；有引用不报。"""
    issues = svc.review_comment_semantics(
        comment="他可能因为项目延期被客户投诉。", citation_count=0, has_gap_section=True
    )
    assert any(i.code == "COMMENT_UNSUPPORTED_CLAIM" for i in issues)

    issues = svc.review_comment_semantics(
        comment="他可能因为项目延期被客户投诉。", citation_count=2, has_gap_section=True
    )
    assert not any(i.code == "COMMENT_UNSUPPORTED_CLAIM" for i in issues)


def test_no_conclusion_when_only_numbers():
    """只有数字没有结论 → 提示。"""
    issues = svc.review_comment_semantics(
        comment="GMV 达成 120%，销售额 100 万，出勤 98%。",
        citation_count=1, has_gap_section=True, metric_count=2,
    )
    assert any(i.code == "COMMENT_NO_CONCLUSION" for i in issues)


def test_conclusion_present_suppresses_no_conclusion():
    """有结论表达 → 不报「没有结论」。"""
    issues = svc.review_comment_semantics(
        comment="GMV 达成 120%，说明本期销售目标已超额完成。",
        citation_count=1, has_gap_section=True, metric_count=2,
    )
    assert not any(i.code == "COMMENT_NO_CONCLUSION" for i in issues)


def test_missing_gap_section_is_low():
    """缺少「不足与改进」是 LOW，不阻塞流程。"""
    issues = svc.review_comment_semantics(comment="本期整体达标。", citation_count=1)
    gap = [i for i in issues if i.code == "COMMENT_NO_GAP_SECTION"]
    assert gap
    assert gap[0].severity == "LOW"


def test_no_citation_flagged():
    """无引用被标记。"""
    issues = svc.review_comment_semantics(comment="本期整体达标。", citation_count=0)
    assert any(i.code == "COMMENT_NO_CITATION" for i in issues)


def test_every_issue_has_basis_and_confidence():
    """每条问题都必须有依据与置信度 —— 这是硬约束。"""
    issues = svc.review_comment_semantics(
        comment="表现良好，态度差，可能是别人说的。", citation_count=0
    )
    assert issues
    for issue in issues:
        assert issue.basis
        assert issue.rule_id
        assert 0.0 < issue.confidence <= 1.0


def test_semantic_review_is_deterministic():
    """确定性：同样输入 → 同样输出。"""
    kwargs = dict(comment="本期表现良好，工作认真。", citation_count=0)
    a = [i.to_dict() for i in svc.review_comment_semantics(**kwargs)]
    b = [i.to_dict() for i in svc.review_comment_semantics(**kwargs)]
    assert a == b


# ── 风险识别 ────────────────────────────────────────────────────────────────

def test_low_achievement_risk():
    """半数以上指标低于 80% → 风险信号。"""
    risks = svc.identify_risks(
        per_metric=[
            {"metric_code": "A", "ratio": 0.5},
            {"metric_code": "B", "ratio": 0.6},
            {"metric_code": "C", "ratio": 1.1},
        ],
        evidence_count=3,
    )
    codes = {r.code for r in risks}
    assert "RISK_LOW_ACHIEVEMENT" in codes


def test_all_metrics_low_is_high_severity():
    """全部指标低 → HIGH。"""
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "A", "ratio": 0.3}, {"metric_code": "B", "ratio": 0.4}],
        evidence_count=3,
    )
    low = [r for r in risks if r.code == "RISK_LOW_ACHIEVEMENT"]
    assert low[0].severity == "HIGH"


def test_good_performance_no_low_achievement_risk():
    """达成良好 → 不报低达成风险。"""
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "A", "ratio": 1.1}, {"metric_code": "B", "ratio": 1.3}],
        evidence_count=3,
    )
    assert not any(r.code == "RISK_LOW_ACHIEVEMENT" for r in risks)


def test_insufficient_basis_risk():
    """有指标但无证据 → 依据不足风险。"""
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "A", "ratio": 1.0}], evidence_count=0
    )
    codes = {r.code for r in risks}
    assert "RISK_INSUFFICIENT_BASIS" in codes


def test_confirmed_without_basis_is_high():
    """评级已确认但无证据 → HIGH（结算风险）。"""
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "A", "ratio": 1.0}],
        evidence_count=0,
        confirmed_rating="B",
    )
    confirmed = [r for r in risks if r.code == "RISK_CONFIRMED_WITHOUT_BASIS"]
    assert confirmed
    assert confirmed[0].severity == "HIGH"


def test_attendance_cluster_risk():
    """考勤异常 ≥3 → 风险信号，但明确不自动扣分。"""
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "A", "ratio": 1.0}],
        evidence_count=2,
        attendance_anomaly_count=4,
    )
    cluster = [r for r in risks if r.code == "RISK_ATTENDANCE_CLUSTER"]
    assert cluster
    assert "不自动扣分" in cluster[0].suggestion


def test_high_variance_risk():
    """指标离散度过大 → 风险。"""
    risks = svc.identify_risks(
        per_metric=[
            {"metric_code": "A", "ratio": 0.2},
            {"metric_code": "B", "ratio": 1.4},
            {"metric_code": "C", "ratio": 1.2},
        ],
        evidence_count=3,
    )
    assert any(r.code == "RISK_HIGH_VARIANCE" for r in risks)


def test_comment_quality_risk_cross_links():
    """评语高危语义问题 → 转化为申诉风险。"""
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "A", "ratio": 1.0}],
        evidence_count=2,
        comment="该员工态度差，能力不合格。",
        citation_count=1,
    )
    quality = [r for r in risks if r.code == "RISK_COMMENT_QUALITY"]
    assert quality
    assert quality[0].evidence  # 指回具体的语义问题 code


def test_no_risk_when_all_healthy():
    """一切正常 → 无风险信号。"""
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "A", "ratio": 1.0}, {"metric_code": "B", "ratio": 1.1}],
        evidence_count=5,
        comment="本期 GMV 达成 120%，说明销售目标超额完成。",
        citation_count=3,
        attendance_anomaly_count=0,
    )
    assert risks == []


# ── 面谈提纲 ────────────────────────────────────────────────────────────────

def test_outline_generates_questions():
    """提纲生成问题清单。"""
    issues = svc.review_comment_semantics(comment="本期表现良好。", citation_count=0)
    risks = svc.identify_risks(
        per_metric=[{"metric_code": "GMV", "metric_name": "销售额", "value": 80, "target": 100, "ratio": 0.8}],
        evidence_count=1,
        comment="本期表现良好。",
        citation_count=0,
    )
    outline = svc.build_interview_outline(
        semantic_issues=issues, risks=risks,
        per_metric=[{"metric_code": "GMV", "metric_name": "销售额", "value": 80, "target": 100, "ratio": 0.8}],
        employee_name="张三",
    )
    assert outline["questionCount"] == len(outline["questions"])
    assert outline["questionCount"] > 0
    assert outline["employeeName"] == "张三"
    assert "建议" in outline["disclaimer"]


def test_outline_questions_have_purpose_and_basis():
    """每个问题都要有提问目的与依据 —— 面谈问题同样可追溯。"""
    issues = svc.review_comment_semantics(comment="该员工态度差。", citation_count=0)
    outline = svc.build_interview_outline(
        semantic_issues=issues, risks=[],
        per_metric=[{"metric_code": "A", "metric_name": "指标A", "value": 50, "target": 100, "ratio": 0.5}],
    )
    for q in outline["questions"]:
        assert q["question"]
        assert q["purpose"]
        assert q["basis"]


def test_outline_always_has_next_period_goal():
    """提纲总有下期规划收尾。"""
    outline = svc.build_interview_outline(
        semantic_issues=[], risks=[],
        per_metric=[{"metric_code": "A", "metric_name": "指标A", "value": 100, "target": 100, "ratio": 1.0}],
    )
    assert any(q["id"] == "q_next_period_goal" for q in outline["questions"])


def test_outline_priority_sorted():
    """高优先级问题排在前面。"""
    issues = svc.review_comment_semantics(comment="该员工态度差，能力不合格。", citation_count=0)
    outline = svc.build_interview_outline(
        semantic_issues=issues, risks=[],
        per_metric=[{"metric_code": "A", "metric_name": "指标A", "value": 30, "target": 100, "ratio": 0.3}],
    )
    priorities = [q["priority"] for q in outline["questions"]]
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    assert priorities == sorted(priorities, key=lambda p: order[p])


def test_outline_is_deterministic():
    """确定性：同样输入 → 同样提纲。"""
    kwargs = dict(
        semantic_issues=svc.review_comment_semantics(comment="表现良好。", citation_count=0),
        risks=[],
        per_metric=[{"metric_code": "A", "metric_name": "指标A", "value": 50, "target": 100, "ratio": 0.5}],
    )
    assert svc.build_interview_outline(**kwargs) == svc.build_interview_outline(**kwargs)


# ── 汇总入口 ────────────────────────────────────────────────────────────────

def test_analyze_returns_all_three_sections():
    """一站式入口返回三个部分。"""
    out = svc.analyze(
        comment="本期表现良好，工作认真。",
        per_metric=[{"metric_code": "A", "metric_name": "指标A", "value": 50, "target": 100, "ratio": 0.5}],
        evidence_count=0,
        citation_count=0,
        employee_name="张三",
    )
    assert "semanticIssues" in out
    assert "risks" in out
    assert "interviewOutline" in out
    assert out["overallRisk"] in ("LOW", "MEDIUM", "HIGH")
    assert "不构成评价结论" in out["disclaimer"]


def test_analyze_with_empty_input_is_safe():
    """空输入不报错，保守返回。"""
    out = svc.analyze()
    assert out["semanticIssues"]
    assert out["overallRisk"] in ("LOW", "MEDIUM", "HIGH")
    assert out["interviewOutline"]["questionCount"] > 0


def test_analyze_does_not_touch_business_state():
    """P2-1 只产出建议 —— 服务层不引用任何业务表模型。"""
    import inspect
    source = inspect.getsource(svc)
    for forbidden in ("db.add", "db.flush", "db.commit", "Score(", "RatingDraft(", "CommentDraft("):
        assert forbidden not in source, f"P2-1 服务层不应出现 {forbidden}"
