"""P2-1 —— 评语语义质检 + 风险识别 + 面谈提纲生成（**仅建议**）。

设计原则（继承全框架四条不可破原则）
----------------------------------------
1. **确定性优先**：本模块是 ``RULE_ONLY`` 模式 —— 同样的输入永远得到同样的输出。
   不接模型也能用，接了模型也只是在规则结果之上叠加一层交叉校验。
   这让「可复现 / 可测试 / 可审计」在 P2 这一层依然成立。
2. **只建议、不落业务表**：产出的是 ``RuleIssue`` 风格的**建议**，
   最终结论由 L2 业务层与人工确认产生。本模块**不写** Score、
   不改 ``confirmed_rating``、不推进任何状态机。
3. **每条结论必须带依据**：``rule_id`` + ``basis`` + ``confidence`` 三件套齐全。
   没有依据的「AI 觉得」不允许出现。
4. **可降级**：任何输入缺失都走保守分支 —— 少提建议，但绝不报错阻断流程。

三个能力
--------
- **语义质检**（``review_comment_semantics``）：检查评语是否空洞、是否只有
  数据没有结论、是否有无据推断、是否含情绪化/标签化表述、是否缺失改进项。
- **风险识别**（``identify_risks``）：基于指标达成率分布、证据覆盖度、
  评语质量交叉识别离职风险 / 绩效争议风险 / 评分依据不足风险。
- **面谈提纲**（``build_interview_outline``）：把质检问题 + 风险点转成
  可执行的面谈问题清单（每个问题带提问目的与追问方向）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


#: 风险等级排序（与 ``ai_review_engine`` 保持一致）
_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "BLOCKING": 3}

#: 空洞词 / 无信息量措辞 —— 出现即说明「写了等于没写」
_VAGUE_PHRASES: tuple[tuple[str, str], ...] = (
    ("表现良好", "R-CMT-001"),
    ("工作认真", "R-CMT-001"),
    ("态度积极", "R-CMT-001"),
    ("尽职尽责", "R-CMT-001"),
    ("总体不错", "R-CMT-001"),
    ("有待提高", "R-CMT-001"),
    ("各方面都", "R-CMT-001"),
    ("继续保持", "R-CMT-001"),
    ("再接再厉", "R-CMT-001"),
    ("积极向上", "R-CMT-001"),
)

#: 标签化 / 情绪化表述 —— 绩效评语不应给人贴标签
_LABELING_PHRASES: tuple[tuple[str, str], ...] = (
    ("态度差", "R-CMT-002"),
    ("能力差", "R-CMT-002"),
    ("不合格", "R-CMT-002"),
    ("拖后腿", "R-CMT-002"),
    ("没干劲", "R-CMT-002"),
    ("不行", "R-CMT-002"),
    ("垃圾", "R-CMT-002"),
    ("不务正业", "R-CMT-002"),
    ("责任心不强", "R-CMT-002"),
)

#: 无据推断信号词 —— 出现时**必须**有引用，否则是模型在编
_UNSUPPORTED_SIGNALS: tuple[tuple[str, str], ...] = (
    ("可能", "R-CMT-003"),
    ("大概", "R-CMT-003"),
    ("估计", "R-CMT-003"),
    ("应该是", "R-CMT-003"),
    ("据说", "R-CMT-003"),
    ("大家都觉得", "R-CMT-003"),
    ("显然", "R-CMT-003"),
    ("众所周知", "R-CMT-003"),
)


@dataclass
class SemanticIssue:
    """一条评语语义质检问题。"""

    code: str
    severity: str
    field: str
    title: str
    rule_id: str
    quote: str = ""
    basis: str = ""
    suggestion: str = ""
    confidence: float = 0.8
    source: str = "RULE"
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source": self.source,
            "severity": self.severity,
            "field": self.field,
            "title": self.title,
            "ruleId": self.rule_id,
            "quote": self.quote,
            "basis": self.basis,
            "suggestion": self.suggestion,
            "confidence": self.confidence,
            "references": self.references,
        }


@dataclass
class RiskSignal:
    """一个风险信号。"""

    code: str
    severity: str
    title: str
    rule_id: str
    basis: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.7
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "ruleId": self.rule_id,
            "basis": self.basis,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "suggestion": self.suggestion,
        }


# ---------------------------------------------------------------------------
# 1. 评语语义质检
# ---------------------------------------------------------------------------


def _iter_phrases(text: str, table: tuple[tuple[str, str], ...]):
    """按短语表命中，返回 ``(短语, 规则号)``。"""
    lowered = text.lower()
    for phrase, rule_id in table:
        if phrase.lower() in lowered:
            yield phrase, rule_id


def review_comment_semantics(
    *,
    comment: str,
    citation_count: int = 0,
    has_gap_section: bool = False,
    metric_count: int = 0,
) -> list[SemanticIssue]:
    """评语语义质检。

    Args:
        comment: 评语正文（markdown）。
        citation_count: 评语中引用的证据条数（来自 ``CommentDraft.citations``）。
        has_gap_section: 是否包含「不足 / 改进」章节。
        metric_count: 本次考核的指标数量 —— 用于判断「有指标却完全不提」。

    Returns:
        语义问题列表（可能为空）。
    """
    issues: list[SemanticIssue] = []
    text = (comment or "").strip()

    # ── R-CMT-000：评语为空 ──
    if not text:
        issues.append(
            SemanticIssue(
                code="COMMENT_EMPTY",
                severity="HIGH",
                field="comment",
                title="评语为空",
                rule_id="R-CMT-000",
                basis="《绩效评语规范》第 1.1 条：评语不得为空，须说明本期表现结论与依据。",
                suggestion="补充本期表现的核心结论与支撑事实。",
                confidence=0.95,
            )
        )
        # 空评语无需继续做措辞检查
        return issues

    # ── R-CMT-001：空洞措辞 ──
    vague_hits = list(_iter_phrases(text, _VAGUE_PHRASES))
    if vague_hits:
        phrases = [p for p, _ in vague_hits]
        rule_ids = sorted({rid for _, rid in vague_hits})
        issues.append(
            SemanticIssue(
                code="COMMENT_VAGUE_PHRASE",
                severity="MEDIUM",
                field="comment",
                title=f"评语包含空洞表述：{'、'.join(sorted(set(phrases)))}",
                rule_id=rule_ids[0],
                quote="、".join(sorted(set(phrases))),
                basis="《绩效评语规范》第 2.1 条：评语须给出具体事实与结论，禁用空洞评价词。",
                suggestion="把空洞评价替换为可核验的事实（做了什么、结果如何、影响是什么）。",
                confidence=0.75,
            )
        )

    # ── R-CMT-002：标签化 / 情绪化表述 ──
    labeling_hits = list(_iter_phrases(text, _LABELING_PHRASES))
    if labeling_hits:
        phrases = [p for p, _ in labeling_hits]
        rule_ids = sorted({rid for _, rid in labeling_hits})
        issues.append(
            SemanticIssue(
                code="COMMENT_LABELING_LANGUAGE",
                severity="HIGH",
                field="comment",
                title=f"评语含标签化表述：{'、'.join(sorted(set(phrases)))}",
                rule_id=rule_ids[0],
                quote="、".join(sorted(set(phrases))),
                basis="《绩效评语规范》第 3.2 条：评语须对事不对人，不得使用标签化、情绪化表述。",
                suggestion="改写为「具体行为 + 后果 + 期望」的事实陈述。",
                confidence=0.8,
            )
        )

    # ── R-CMT-003：无据推断且无引用 ──
    unsupported_hits = list(_iter_phrases(text, _UNSUPPORTED_SIGNALS))
    if unsupported_hits and citation_count == 0:
        phrases = [p for p, _ in unsupported_hits]
        rule_ids = sorted({rid for _, rid in unsupported_hits})
        issues.append(
            SemanticIssue(
                code="COMMENT_UNSUPPORTED_CLAIM",
                severity="HIGH",
                field="comment",
                title="评语含推断性表述但未引用任何证据",
                rule_id=rule_ids[0],
                quote="、".join(sorted(set(phrases))),
                basis="《绩效评语规范》第 2.3 条：涉及事实判断的表述须有可回溯的证据引用。",
                suggestion="补上对应证据引用，或删除无法溯源的推断。",
                confidence=0.7,
            )
        )

    # ── R-CMT-004：只有数字没有结论 ──
    # 判据：正文里出现数字，但没有出现任何结论性表达。
    if metric_count > 0 and re.search(r"\d", text):
        conclusion_markers = (
            "因此", "所以", "结论", "综上", "说明", "表明", "体现",
            "达到", "未达", "超出", "低于", "高于", "评为", "整体",
        )
        if not any(marker in text for marker in conclusion_markers):
            issues.append(
                SemanticIssue(
                    code="COMMENT_NO_CONCLUSION",
                    severity="MEDIUM",
                    field="comment",
                    title="评语只有数据罗列，未给出结论",
                    rule_id="R-CMT-004",
                    basis="《绩效评语规范》第 2.2 条：评语须在事实基础上给出明确结论。",
                    suggestion="在数据之后补一句结论（达成/未达成，优势在何处，短板在何处）。",
                    confidence=0.65,
                )
            )

    # ── R-CMT-005：缺少不足与改进 ──
    if not has_gap_section:
        issues.append(
            SemanticIssue(
                code="COMMENT_NO_GAP_SECTION",
                severity="LOW",
                field="comment",
                title="评语缺少「不足与改进」部分",
                rule_id="R-CMT-005",
                basis="《绩效评语规范》第 4.1 条：评语须包含下期改进方向。",
                suggestion="补充 1~2 条可执行的下期改进建议。",
                confidence=0.6,
            )
        )

    # ── R-CMT-006：无引用 ──
    if citation_count == 0:
        issues.append(
            SemanticIssue(
                code="COMMENT_NO_CITATION",
                severity="MEDIUM",
                field="citations",
                title="评语未引用任何证据",
                rule_id="R-CMT-006",
                basis="《绩效评语规范》第 2.4 条：评语结论须可回溯到证据材料。",
                suggestion="为关键结论挂上对应的证据 ID。",
                confidence=0.85,
            )
        )

    return issues


# ---------------------------------------------------------------------------
# 2. 风险识别
# ---------------------------------------------------------------------------


def identify_risks(
    *,
    per_metric: list[dict[str, Any]],
    evidence_count: int = 0,
    comment: str = "",
    citation_count: int = 0,
    attendance_anomaly_count: int = 0,
    confirmed_rating: str | None = None,
) -> list[RiskSignal]:
    """风险识别（离职 / 争议 / 依据不足 / 舆情）。

    只输出**统计性观察**，不下结论、不触发任何流程。
    """
    risks: list[RiskSignal] = []

    ratios = [
        float(pm.get("ratio"))
        for pm in per_metric
        if pm.get("ratio") is not None
    ]

    # ── R-RSK-001：低达成率（离职倾向的统计信号之一）──
    if ratios:
        low = [pm for pm in per_metric if _ratio_of(pm) is not None and _ratio_of(pm) < 0.8]
        if len(low) >= max(1, len(per_metric) // 2):
            risks.append(
                RiskSignal(
                    code="RISK_LOW_ACHIEVEMENT",
                    severity="HIGH" if len(low) == len(per_metric) else "MEDIUM",
                    title=f"{len(low)}/{len(per_metric)} 项指标达成率低于 80%",
                    rule_id="R-RSK-001",
                    basis="多指标持续低达成与离职风险存在统计相关性，需人工核实实际原因。",
                    evidence=[pm.get("metric_code", "") for pm in low],
                    confidence=0.6,
                    suggestion="核实是否存在目标设定偏高、协作阻塞或人员流失意向。",
                )
            )

    # ── R-RSK-002：达成率波动大 ──
    if len(ratios) >= 3:
        spread = max(ratios) - min(ratios)
        if spread >= 0.8:
            risks.append(
                RiskSignal(
                    code="RISK_HIGH_VARIANCE",
                    severity="MEDIUM",
                    title="指标达成率离散度偏大（极差 ≥ 0.8）",
                    rule_id="R-RSK-002",
                    basis="指标间达成率差距过大，通常意味着目标口径不一致或存在外部依赖阻塞。",
                    confidence=0.55,
                    suggestion="核对各指标目标设定口径是否一致。",
                )
            )

    # ── R-RSK-003：依据不足 ──
    if per_metric and evidence_count == 0:
        risks.append(
            RiskSignal(
                code="RISK_INSUFFICIENT_BASIS",
                severity="HIGH",
                title="有指标结果但无任何证据支撑",
                rule_id="R-RSK-003",
                basis="《绩效证据链要求》第 1.2 条：评分结论须有可回溯的证据支撑。",
                confidence=0.9,
                suggestion="要求补充证据后再确认评分。",
            )
        )

    # ── R-RSK-004：评语质量风险 → 申诉风险 ──
    semantic = review_comment_semantics(
        comment=comment,
        citation_count=citation_count,
        has_gap_section=False,
        metric_count=len(per_metric),
    )
    blocking = [i for i in semantic if i.severity == "HIGH"]
    if blocking:
        risks.append(
            RiskSignal(
                code="RISK_COMMENT_QUALITY",
                severity="MEDIUM",
                title="评语存在高severity语义问题，可能引发申诉",
                rule_id="R-RSK-004",
                basis="评语含标签化表述或无据推断时，员工申诉概率显著上升。",
                evidence=[i.code for i in blocking],
                confidence=0.6,
                suggestion="确认前先修订评语措辞并补齐引用。",
            )
        )

    # ── R-RSK-005：考勤异常集中 ──
    if attendance_anomaly_count >= 3:
        risks.append(
            RiskSignal(
                code="RISK_ATTENDANCE_CLUSTER",
                severity="MEDIUM",
                title=f"本期考勤异常 {attendance_anomaly_count} 条（≥3）",
                rule_id="R-RSK-005",
                basis="考勤异常集中出现时需核实是否存在系统性因素（出差、流程故障、组织调整）。",
                confidence=0.65,
                suggestion="考勤异常不自动扣分，但建议人工核实异常成因。",
            )
        )

    # ── R-RSK-006：评级已确认但证据不足 → 结算风险 ──
    if confirmed_rating and evidence_count == 0:
        risks.append(
            RiskSignal(
                code="RISK_CONFIRMED_WITHOUT_BASIS",
                severity="HIGH",
                title="评级已确认但缺少证据支撑",
                rule_id="R-RSK-006",
                basis="已确认评级若在申诉阶段无法举证，将直接转化为结算与法律风险。",
                confidence=0.85,
                suggestion="结算前补齐证据或走特批流程留痕。",
            )
        )

    return risks


def _ratio_of(pm: dict[str, Any]) -> float | None:
    ratio = pm.get("ratio")
    if ratio is None:
        return None
    try:
        return float(ratio)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 3. 面谈提纲生成
# ---------------------------------------------------------------------------


def build_interview_outline(
    *,
    semantic_issues: list[SemanticIssue],
    risks: list[RiskSignal],
    per_metric: list[dict[str, Any]],
    employee_name: str = "",
) -> dict[str, Any]:
    """生成面谈提纲（**提问清单**，不是答案）。

    每个问题带：提问目的、追问方向、依据引用 —— 保证面谈问题同样可追溯。
    """
    questions: list[dict[str, Any]] = []
    name = employee_name or "该员工"

    # ── 逐指标确认 ──
    for pm in per_metric:
        code = pm.get("metric_code", "")
        name_m = pm.get("metric_name", code)
        ratio = pm.get("ratio")
        if ratio is None:
            continue
        if float(ratio) < 0.8:
            questions.append(
                {
                    "id": f"q_metric_{code}_low",
                    "dimension": name_m,
                    "question": f"「{name_m}」本期实际 {pm.get('value')}，目标是 {pm.get('target')}，"
                               f"达成率 {ratio}。主要差距在哪里？",
                    "purpose": "让员工陈述差距归因，区分「目标偏高」与「执行不足」。",
                    "followUp": "追问差距中外部依赖、协作、资源三类因素各占多少。",
                    "basis": [f"Score.per_metric[{code}].ratio"],
                    "priority": "HIGH" if float(ratio) < 0.5 else "MEDIUM",
                }
            )
        elif float(ratio) >= 1.2:
            questions.append(
                {
                    "id": f"q_metric_{code}_high",
                    "dimension": name_m,
                    "question": f"「{name_m}」达成率 {ratio}，超出目标较多。可复用与可复制的是什么？",
                    "purpose": "识别超额完成的方法论，为团队沉淀可复制经验。",
                    "followUp": "追问该方法是否可迁移到其他指标 / 其他同事。",
                    "basis": [f"Score.per_metric[{code}].ratio"],
                    "priority": "MEDIUM",
                }
            )

    # ── 由语义问题生成「澄清类」问题 ──
    for issue in semantic_issues:
        if issue.code == "COMMENT_NO_CITATION":
            questions.append(
                {
                    "id": f"q_issue_{issue.rule_id.lower()}_cite",
                    "dimension": "证据支撑",
                    "question": "本期结论所依据的成果是哪几件？请按重要性排序说明。",
                    "purpose": "补齐证据链，把「印象分」转成「举证分」。",
                    "followUp": "追问每项成果的可核验形式（数据、文档、验收记录）。",
                    "basis": issue.rule_id,
                    "priority": "HIGH" if issue.severity == "HIGH" else "MEDIUM",
                }
            )
        elif issue.code == "COMMENT_VAGUE_PHRASE":
            questions.append(
                {
                    "id": f"q_issue_{issue.rule_id.lower()}_concrete",
                    "dimension": "具体表现",
                    "question": "如果用一件具体的事说明本期表现，你选哪一件？当时你做了什么、结果如何？",
                    "purpose": "用事实替换空洞评价词。",
                    "followUp": "追问这件事的可核验材料在哪里。",
                    "basis": issue.rule_id,
                    "priority": "MEDIUM",
                }
            )
        elif issue.code == "COMMENT_LABELING_LANGUAGE":
            questions.append(
                {
                    "id": f"q_issue_{issue.rule_id.lower()}_facts",
                    "dimension": "事实澄清",
                    "question": "我们一起把这段评语改写为具体行为 + 后果 + 期望，你看这样表述是否准确？",
                    "purpose": "当面校准评语措辞，避免标签化表述进入沟通。",
                    "followUp": "若员工不认同具体事实，先记录分歧点，不当场争论定性。",
                    "basis": issue.rule_id,
                    "priority": "HIGH",
                }
            )
        elif issue.code == "COMMENT_NO_CONCLUSION":
            questions.append(
                {
                    "id": f"q_issue_{issue.rule_id.lower()}_conclusion",
                    "dimension": "结论共识",
                    "question": "综合本期各项指标，你认为自己的整体表现处于什么水平？",
                    "purpose": "让员工先表达自评，与主管结论做差异对齐。",
                    "followUp": "追问自评与主管判断的差异来源。",
                    "basis": issue.rule_id,
                    "priority": "MEDIUM",
                }
            )

    # ── 由风险生成「风险类」问题 ──
    for risk in risks:
        if risk.code == "RISK_LOW_ACHIEVEMENT":
            questions.append(
                {
                    "id": "q_risk_low_achievement",
                    "dimension": "目标与阻力",
                    "question": "本期目标的设定是否与你的实际资源匹配？有哪些客观阻力没有被记录？",
                    "purpose": "识别目标设定问题与真实阻力，避免把系统性问题算到个人头上。",
                    "followUp": "追问若要达成下期目标，需要哪些支持与前置条件。",
                    "basis": risk.rule_id,
                    "priority": "HIGH",
                }
            )
        elif risk.code == "RISK_INSUFFICIENT_BASIS":
            questions.append(
                {
                    "id": "q_risk_insufficient_basis",
                    "dimension": "举证",
                    "question": "本期哪些工作可以留下可核验的记录？",
                    "purpose": "补齐证据缺口，降低后续申诉与结算风险。",
                    "followUp": "确认下期从何时开始同步留存过程材料。",
                    "basis": risk.rule_id,
                    "priority": "HIGH",
                }
            )
        elif risk.code == "RISK_CONFIRMED_WITHOUT_BASIS":
            questions.append(
                {
                    "id": "q_risk_confirmed_without_basis",
                    "dimension": "举证",
                    "question": "已确认的评级依据是哪几项？能否逐条说明证据？",
                    "purpose": "在结算前补齐举证链。",
                    "followUp": "若无法举证，记录分歧并走特批流程留痕。",
                    "basis": risk.rule_id,
                    "priority": "HIGH",
                }
            )
        elif risk.code == "RISK_ATTENDANCE_CLUSTER":
            questions.append(
                {
                    "id": "q_risk_attendance_cluster",
                    "dimension": "考勤与状态",
                    "question": "本期考勤异常集中，是否与工作安排、出差或流程问题有关？",
                    "purpose": "先核实成因再谈影响 —— 考勤异常不自动扣分。",
                    "followUp": "确认是否需要调整排班或流程。",
                    "basis": risk.rule_id,
                    "priority": "MEDIUM",
                }
            )
        elif risk.code == "RISK_COMMENT_QUALITY":
            questions.append(
                {
                    "id": "q_risk_comment_quality",
                    "dimension": "评语共识",
                    "question": "评语中提到的那几点，你是否认同？哪里需要补充或修正？",
                    "purpose": "让员工参与校准评语，降低申诉概率。",
                    "followUp": "记录员工提出的事实补充，作为评语修订依据。",
                    "basis": risk.rule_id,
                    "priority": "MEDIUM",
                }
            )
        elif risk.code == "RISK_HIGH_VARIANCE":
            questions.append(
                {
                    "id": "q_risk_high_variance",
                    "dimension": "口径",
                    "question": "不同指标的目标口径是否一致？有没有指标未真正参与结果？",
                    "purpose": "核对目标设定口径，排除指标虚高或虚低。",
                    "followUp": "追问哪个指标的权重/目标最需要调整。",
                    "basis": risk.rule_id,
                    "priority": "MEDIUM",
                }
            )

    # ── 结尾：下期目标 ──
    questions.append(
        {
            "id": "q_next_period_goal",
            "dimension": "下期规划",
            "question": f"下期你最想解决的一件事是什么？{name}希望获得什么支持？",
            "purpose": "把面谈收敛为一条可执行、可跟踪的下期承诺。",
            "followUp": "确认目标、衡量方式与检查节点。",
            "basis": ["InterviewGuide"],
            "priority": "MEDIUM",
        }
    )

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    questions.sort(key=lambda q: order.get(q.get("priority", "MEDIUM"), 3))

    return {
        "employeeName": employee_name,
        "purpose": "绩效面谈提纲（AI 生成建议，仅供主管参考与裁剪）",
        "disclaimer": "本提纲为 AI 建议，不含任何评价结论；提问顺序与取舍由主管决定。",
        "questionCount": len(questions),
        "questions": questions,
        "summary": _outline_summary(semantic_issues, risks),
    }


def _outline_summary(
    semantic_issues: list[SemanticIssue], risks: list[RiskSignal]
) -> str:
    high_issues = sum(1 for i in semantic_issues if i.severity == "HIGH")
    high_risks = sum(1 for r in risks if r.severity == "HIGH")
    if not semantic_issues and not risks:
        return "未发现语义或风险信号，建议按常规面谈流程进行。"
    return (
        f"共发现 {len(semantic_issues)} 项评语语义问题（高 {high_issues} 项）"
        f"与 {len(risks)} 项风险信号（高 {high_risks} 项）。"
    )


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------


def analyze(
    *,
    comment: str = "",
    per_metric: list[dict[str, Any]] | None = None,
    evidence_count: int = 0,
    citation_count: int = 0,
    has_gap_section: bool = False,
    attendance_anomaly_count: int = 0,
    confirmed_rating: str | None = None,
    employee_name: str = "",
) -> dict[str, Any]:
    """P2-1 一站式入口：质检 + 风险 + 提纲。

    返回统一形状，**不写任何业务表**。
    """
    per_metric = per_metric or []
    semantic_issues = review_comment_semantics(
        comment=comment,
        citation_count=citation_count,
        has_gap_section=has_gap_section,
        metric_count=len(per_metric),
    )
    risks = identify_risks(
        per_metric=per_metric,
        evidence_count=evidence_count,
        comment=comment,
        citation_count=citation_count,
        attendance_anomaly_count=attendance_anomaly_count,
        confirmed_rating=confirmed_rating,
    )
    outline = build_interview_outline(
        semantic_issues=semantic_issues,
        risks=risks,
        per_metric=per_metric,
        employee_name=employee_name,
    )
    overall = _overall_risk(semantic_issues, risks)
    return {
        "overallRisk": overall,
        "semanticIssues": [i.to_dict() for i in semantic_issues],
        "risks": [r.to_dict() for r in risks],
        "interviewOutline": outline,
        "disclaimer": (
            "AI 仅提供统计性观察与建议，不构成评价结论；"
            "所有条目均需人工复核后方可使用。"
        ),
    }


def _overall_risk(
    semantic_issues: list[SemanticIssue], risks: list[RiskSignal]
) -> str:
    levels = [i.severity for i in semantic_issues] + [r.severity for r in risks]
    if not levels:
        return "LOW"
    highest = max(levels, key=lambda s: _RISK_ORDER.get(s, 0))
    if highest == "BLOCKING":
        return "HIGH"
    return highest
