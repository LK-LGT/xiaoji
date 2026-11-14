"""AI 复核规则引擎（``RULE_ONLY`` 模式）。

在没有接入真实模型之前，本模块用**确定性规则**产出复核结果：
同样的输入永远得到同样的输出（可复现、可测试），并且每条问题都带
``ruleId`` / ``basis`` / ``confidence``，保证 AI 输出仍然符合
「必须给依据」的硬约束。

⚠️ **边界（决策⑤）**：
- 本模块**只产出建议**，不写任何业务表，不决定分数、等级、状态。
- ``overallRisk`` 只是提示，最终结论由 L2 业务层与人工确认产出。

后续接入真实模型时，只需在复核结果里叠加模型结论，
规则部分作为兜底与交叉校验保留。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


#: 风险等级排序（用于取最大值）
_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "BLOCKING": 3}


@dataclass
class RuleIssue:
    """一条规则命中项。"""

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
        """转成前端 ``AiReviewIssue`` 形状。"""
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


# ---------------------------------------------------------------------------
# 计划类规则
# ---------------------------------------------------------------------------


def _rule_empty_content(issues: list[RuleIssue]) -> None:
    """R-PLAN-001：任务内容为空。"""
    issues.append(
        RuleIssue(
            code="PLAN_CONTENT_EMPTY",
            severity="HIGH",
            field="taskContent",
            title="任务内容为空",
            rule_id="R-PLAN-001",
            basis="《绩效计划填报规范》第 2.1 条：任务内容不得为空。",
            suggestion="补充可衡量的任务描述。",
            confidence=0.95,
        )
    )


def _rule_no_deliverable(issues: list[RuleIssue]) -> None:
    """R-PLAN-002：缺少交付物。"""
    issues.append(
        RuleIssue(
            code="PLAN_DELIVERABLE_MISSING",
            severity="MEDIUM",
            field="deliverable",
            title="未填写交付物",
            rule_id="R-PLAN-002",
            basis="《绩效计划填报规范》第 2.2 条：每条任务须有可验收的交付物。",
            suggestion="补充交付物形式（文档 / 报告 / 上线功能等）。",
            confidence=0.85,
        )
    )


def _rule_weight_overflow(issues: list[RuleIssue], total_weight: float) -> None:
    """R-PLAN-003：权重合计不为 100。"""
    if abs(total_weight - 100.0) < 0.01:
        return
    issues.append(
        RuleIssue(
            code="PLAN_WEIGHT_NOT_100",
            severity="MEDIUM" if total_weight > 0 else "HIGH",
            field="performanceWeight",
            title=f"绩效权重合计为 {total_weight:g}%，不等于 100%",
            rule_id="R-PLAN-003",
            basis="《绩效计划填报规范》第 3.1 条：同一计划内各任务权重合计须为 100%。",
            suggestion="调整各任务权重使其合计为 100%。",
            confidence=0.9,
        )
    )


def _rule_date_order(issues: list[RuleIssue], start: str, end: str) -> None:
    """R-PLAN-004：起止日期倒置。"""
    if not start or not end:
        return
    if start > end:
        issues.append(
            RuleIssue(
                code="PLAN_DATE_RANGE_INVALID",
                severity="HIGH",
                field="estimatedStartDate",
                title="计划起止日期倒置",
                rule_id="R-PLAN-004",
                basis="《绩效计划填报规范》第 2.3 条：计划开始日期不得晚于结束日期。",
                suggestion=f"当前开始 {start} 晚于结束 {end}，请修正。",
                confidence=0.9,
            )
        )


def _rule_content_too_short(issues: list[RuleIssue], content: str) -> None:
    """R-PLAN-005：任务内容过短。"""
    if len(content.strip()) < 10:
        issues.append(
            RuleIssue(
                code="PLAN_CONTENT_TOO_SHORT",
                severity="LOW",
                field="taskContent",
                title="任务内容过于简略",
                rule_id="R-PLAN-005",
                basis="《绩效计划填报规范》第 2.1 条附注：任务内容建议不少于 10 字以便验收。",
                suggestion="补充任务背景、动作与预期产出。",
                confidence=0.6,
            )
        )


def review_plan_content(
    *,
    items: list[dict[str, Any]],
    acceptance_standards: list[str] | None = None,
) -> tuple[list[RuleIssue], float]:
    """复核计划条目内容。

    Args:
        items: 计划条目列表（字段与前端 ``EmployeeMonthPlanItem`` 一致）。
        acceptance_standards: 该员工的验收标准列表。

    Returns:
        ``(issues, overall_risk)``。
    """
    issues: list[RuleIssue] = []
    total_weight = 0.0

    for item in items:
        content = str(item.get("taskContent") or item.get("content") or "").strip()
        deliverable = str(item.get("deliverable") or "").strip()

        if not content:
            _rule_empty_content(issues)
        else:
            _rule_content_too_short(issues, content)

        if not deliverable:
            _rule_no_deliverable(issues)

        try:
            total_weight += float(item.get("performanceWeight") or 0)
        except (TypeError, ValueError):
            pass

        _rule_date_order(
            issues,
            str(item.get("estimatedStartDate") or ""),
            str(item.get("estimatedEndDate") or ""),
        )

    if items:
        _rule_weight_overflow(issues, total_weight)

    if not items:
        issues.append(
            RuleIssue(
                code="PLAN_NO_ITEM",
                severity="HIGH",
                field="items",
                title="计划没有任何任务条目",
                rule_id="R-PLAN-006",
                basis="《绩效计划填报规范》第 2.0 条：计划至少包含一条任务条目。",
                suggestion="至少填报一条任务。",
                confidence=0.95,
            )
        )

    # 验收标准未覆盖
    if acceptance_standards:
        covered = _coverage(items, acceptance_standards)
        if covered < len(acceptance_standards):
            issues.append(
                RuleIssue(
                    code="PLAN_STANDARD_UNCOVERED",
                    severity="MEDIUM",
                    field="acceptanceStandards",
                    title=f"{len(acceptance_standards) - covered} 条验收标准未被计划覆盖",
                    rule_id="R-PLAN-007",
                    basis="《绩效计划与验收标准映射要求》第 1.2 条：计划应覆盖全部验收标准。",
                    suggestion="检查是否有验收标准遗漏对应的任务。",
                    confidence=0.7,
                )
            )

    return issues, _overall_risk(issues)


def _coverage(items: list[dict[str, Any]], standards: list[str]) -> int:
    """估算验收标准被覆盖的数量（关键词命中率，近似算法）。"""
    blob = " ".join(
        str(item.get("taskContent") or item.get("content") or "")
        + " "
        + str(item.get("deliverable") or "")
        for item in items
    )
    covered = 0
    for standard in standards:
        key = str(standard).strip()
        if not key:
            continue
        # 取最长的 2~4 个字作为关键词，降低误判
        token = key[:4] if len(key) >= 4 else key
        if token and token in blob:
            covered += 1
    return covered


# ---------------------------------------------------------------------------
# 成果类规则
# ---------------------------------------------------------------------------


def review_result_content(
    *,
    description: str,
    completion_rate: float | None = None,
    attachments_count: int = 0,
) -> tuple[list[RuleIssue], float]:
    """复核成果填报内容。"""
    issues: list[RuleIssue] = []

    if not description.strip():
        issues.append(
            RuleIssue(
                code="RESULT_DESCRIPTION_EMPTY",
                severity="HIGH",
                field="description",
                title="成果描述为空",
                rule_id="R-RES-001",
                basis="《绩效成果填报规范》第 1.1 条：成果描述不得为空。",
                suggestion="补充成果内容与产出说明。",
                confidence=0.95,
            )
        )
    elif len(description.strip()) < 20:
        issues.append(
            RuleIssue(
                code="RESULT_DESCRIPTION_TOO_SHORT",
                severity="MEDIUM",
                field="description",
                title="成果描述过于简略",
                rule_id="R-RES-002",
                basis="《绩效成果填报规范》第 1.1 条附注：成果描述建议不少于 20 字。",
                suggestion="补充成果背景、动作与量化结果。",
                confidence=0.7,
            )
        )

    if attachments_count == 0:
        issues.append(
            RuleIssue(
                code="RESULT_NO_ATTACHMENT",
                severity="MEDIUM",
                field="attachments",
                title="未上传成果附件",
                rule_id="R-RES-003",
                basis="《绩效成果填报规范》第 1.3 条：成果须附可核验的证明材料。",
                suggestion="上传文档、截图或链接等证明材料。",
                confidence=0.8,
            )
        )

    if completion_rate is not None and completion_rate > 100:
        issues.append(
            RuleIssue(
                code="RESULT_RATE_OUT_OF_RANGE",
                severity="HIGH",
                field="completionRate",
                title="完成率超过 100%",
                rule_id="R-RES-004",
                basis="《绩效成果填报规范》第 2.2 条：完成率取值范围为 0~100。",
                suggestion="修正完成率。",
                confidence=0.95,
            )
        )

    return issues, _overall_risk(issues)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def _overall_risk(issues: list[RuleIssue]) -> str:
    """按问题严重度取最高风险等级。"""
    if not issues:
        return "LOW"
    highest = max((issue.severity for issue in issues), key=lambda s: _RISK_ORDER.get(s, 0))
    if highest == "BLOCKING":
        return "HIGH"
    return highest


def build_review_result(
    issues: list[RuleIssue],
    *,
    summary: str = "",
    acceptance_coverage: list[dict[str, Any]] | None = None,
    analysis_dimensions: list[dict[str, Any]] | None = None,
    evidence_status: str = "UNKNOWN",
    declared_completion_rate: float | None = None,
) -> dict[str, Any]:
    """组装成前端 ``AiReviewResult`` 形状。"""
    overall = _overall_risk(issues)
    return {
        "overallRisk": overall,
        "summary": summary or _default_summary(issues, overall),
        "issues": [issue.to_dict() for issue in issues],
        "analysisDimensions": analysis_dimensions or [],
        "acceptanceCoverage": acceptance_coverage or [],
        "suggestedCompletionMin": None,
        "suggestedCompletionMax": None,
        "evidenceStatus": evidence_status,
        "declaredCompletionRate": declared_completion_rate,
        "completionCalculationBasis": None,
    }


def _default_summary(issues: list[RuleIssue], overall: str) -> str:
    """默认摘要。"""
    if not issues:
        return "规则复核未发现问题。"
    high = sum(1 for issue in issues if issue.severity in ("HIGH", "BLOCKING"))
    return f"规则复核发现 {len(issues)} 个问题（其中高风险 {high} 个），整体风险 {overall}。"
