"""来源锚定（Grounding）—— 把"结论必须能对应到证据"从统计级升级到原文级。

这是从 `enterprise-plan-results` 的 `AiGroundingService` 吸收并**用 Python 重新实现**的核心能力
（该项目无 LICENSE，故只读其设计语义，不复制代码）。

核心机制（与 Java 版语义一致）：

1. **封闭来源目录**：模型只能引用本次请求提供的来源，不能引入新事实
2. **引文原文校验**：模型给出的 quote 必须在来源原文中**真实存在**，否则降级
3. **规则相关性校验**：引用的来源必须与该检查维度相关
4. **必需来源覆盖校验**：判定"通过"必须覆盖该维度所需的全部关键字段
5. **风险冲突校验**：关键字段存在实质风险时，不得同时判定为"通过"

降级语义：不通过校验的结论 **不删除，而是降级为 UNKNOWN**（"现有资料不足以形成可靠结论"）。
—— 保留可追溯性，同时不让未经验证的结论冒充结论。

---

与 reviewops 的 FairnessReport 的区别：

| | reviewops FairnessReport | 本模块 |
|---|---|---|
| 检查什么 | 有多少结论**没引用**证据 | 引用的引文**是不是真的**在原文里 |
| 强度 | 统计级 | 原文级 |
| 能否识破幻觉引文 | ❌ | ✅ |
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# 脱敏 —— 与 AiRedactionService 语义一致
# ---------------------------------------------------------------------------

MOBILE_RE = re.compile(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[0-9Xx])(?![0-9Xx])")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
EMPLOYEE_NO_RE = re.compile(r"(?i)(员工编号|employeeNo|employee_no)\s*[:：=]?\s*[A-Za-z0-9_-]+")

SENSITIVE_KEYS = ("mobile", "phone", "email", "employeeno", "employee_no")


def redact_text(text: str | None) -> str:
    """脱敏文本。保留可读的掩码，不记录被移除的值。"""
    if not text:
        return ""
    value = MOBILE_RE.sub(r"\1****\2", text)
    value = ID_CARD_RE.sub(r"\1********\2", value)
    value = EMAIL_RE.sub(r"\1***\2", value)
    value = EMPLOYEE_NO_RE.sub("员工编号:***", value)
    return value


def redact_obj(obj: Any) -> Any:
    """递归脱敏对象。敏感键的值整体替换为 ***。"""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if any(s in k.lower() for s in SENSITIVE_KEYS):
                out[k] = "***"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


# ---------------------------------------------------------------------------
# 归一化 —— 用于引文匹配
# ---------------------------------------------------------------------------


def normalize_for_match(value: str | None) -> str:
    """归一化，用于引文匹配。

    处理三类差异：
    - 全角/半角（含 NFKC 归一：ＧＭＶ → GMV，１２５ → 125）
    - 大小写
    - 空白（压缩连续空格）

    注意：NFKC 会把全角数字转半角，但**不会**自动插入空格。
    所以 "ＧＭＶ１２５" → "gmv125"，而 "GMV 125" → "gmv 125"，
    两者仍然不等 —— 这是正确的：引文匹配不应过度模糊，
    否则会漏掉"数字对但文字不对"的伪造。
    """
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def normalize_for_containment(value: str | None) -> str:
    """更宽松的归一化 —— 去掉所有空白后再比。

    用于"引文是源文本子串"的宽松判定（模型可能在中文与数字间插入空格）。
    """
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", "", normalized).lower()


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


class DimensionStatus(StrEnum):
    PASS = "PASS"
    RISK = "RISK"
    UNKNOWN = "UNKNOWN"


class CoverageStatus(StrEnum):
    PROVEN = "PROVEN"
    PARTIAL = "PARTIAL"
    UNPROVEN = "UNPROVEN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceReference:
    """来源目录里的一条。id 是模型唯一能引用的句柄。"""

    id: str
    path: str
    label: str
    content: str


@dataclass(frozen=True)
class Rule:
    """一条检查规则。"""

    id: str
    title: str
    text: str = ""

    @property
    def key(self) -> str:
        return self.id.strip()


@dataclass
class GroundedDimension:
    """一个检查维度的最终结论（校验后）。"""

    rule_id: str
    title: str
    status: str
    conclusion: str
    quote: str
    basis: str
    confidence: float
    references: list[str] = field(default_factory=list)


@dataclass
class GroundedIssue:
    """一个识别出的问题（校验后）。"""

    code: str
    source: str
    severity: str
    field: str
    title: str
    rule_id: str
    quote: str
    basis: str
    suggestion: str
    confidence: float
    references: list[str] = field(default_factory=list)


@dataclass
class GroundedCoverage:
    """一个验收项的举证情况。"""

    criterion_id: str
    criterion: str
    status: str
    basis: str
    evidence_quote: str
    confidence: float
    evidence_references: list[str] = field(default_factory=list)


@dataclass
class GroundingRequest:
    """一次锚定请求。"""

    biz_type: str
    rules: list[Rule]
    business_data: dict[str, Any]
    source_catalog: list[SourceReference] = field(default_factory=list)
    # rule_id → 该规则必须覆盖的来源 id
    required_references: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class GroundingResult:
    """锚定结果。"""

    summary: str
    dimensions: list[GroundedDimension] = field(default_factory=list)
    issues: list[GroundedIssue] = field(default_factory=list)
    coverage: list[GroundedCoverage] = field(default_factory=list)
    grounded: bool = True
    downgraded_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "grounded": self.grounded,
            "downgraded_count": self.downgraded_count,
            "dimensions": [
                {
                    "rule_id": d.rule_id,
                    "title": d.title,
                    "status": d.status,
                    "conclusion": d.conclusion,
                    "quote": d.quote,
                    "basis": d.basis,
                    "confidence": d.confidence,
                    "references": d.references,
                }
                for d in self.dimensions
            ],
            "issues": [
                {
                    "code": i.code,
                    "source": i.source,
                    "severity": i.severity,
                    "field": i.field,
                    "title": i.title,
                    "rule_id": i.rule_id,
                    "quote": i.quote,
                    "basis": i.basis,
                    "suggestion": i.suggestion,
                    "confidence": i.confidence,
                    "references": i.references,
                }
                for i in self.issues
            ],
            "coverage": [
                {
                    "criterion_id": c.criterion_id,
                    "criterion": c.criterion,
                    "status": c.status,
                    "basis": c.basis,
                    "evidence_quote": c.evidence_quote,
                    "confidence": c.confidence,
                    "evidence_references": c.evidence_references,
                }
                for c in self.coverage
            ],
        }


# ---------------------------------------------------------------------------
# 锚定服务
# ---------------------------------------------------------------------------


class GroundingService:
    """来源锚定服务。

    核心：把模型的原始输出，逐条对照封闭来源目录做真实性校验。
    校验不通过的结论降级为 UNKNOWN，**不删除**（保留可追溯性）。
    """

    def __init__(
        self,
        *,
        required_for_rule: Any = None,
        relevant_for_rule: Any = None,
        risk_invalidates_pass: Any = None,
        severity_for_rule: Any = None,
    ) -> None:
        # 这四个函数是"规则语义"，由调用方按业务注入（绩效场景的规则与计划场景不同）
        self._required_for_rule = required_for_rule or (lambda rule_id, path: False)
        self._relevant_for_rule = relevant_for_rule or (lambda rule_id, path: False)
        self._risk_invalidates_pass = risk_invalidates_pass or (lambda risk, passed: False)
        self._severity_for_rule = severity_for_rule or (lambda rule_id: "MEDIUM")

    # -- 公开入口 --

    def prepare(
        self,
        biz_type: str,
        rules: list[Rule],
        business_data: dict[str, Any],
    ) -> GroundingRequest:
        """构建封闭来源目录。模型只能引用这里列出来的来源。"""
        sources: list[SourceReference] = []
        self._collect_sources(business_data, "businessData", sources)

        required: dict[str, list[str]] = {}
        for rule in rules:
            source_ids = [
                s.id for s in sources if self._required_for_rule(rule.key, s.path)
            ]
            if source_ids:
                required[rule.key] = source_ids

        return GroundingRequest(
            biz_type=biz_type,
            rules=rules,
            business_data=business_data,
            source_catalog=sources,
            required_references=required,
        )

    def validate(
        self,
        request: GroundingRequest,
        raw: dict[str, Any] | None,
    ) -> GroundingResult:
        """校验模型输出。逐维度、逐问题、逐验收项。"""
        if raw is None:
            return GroundingResult(
                summary="AI 模型未返回可核验结论。", grounded=False, downgraded_count=1
            )

        sources_by_id = {s.id: s for s in request.source_catalog}
        allowed_rule_ids = {r.key for r in request.rules}
        rules_by_id = {r.key: r for r in request.rules}

        downgraded = 0

        # --- 问题 ---
        issues: list[GroundedIssue] = []
        for raw_issue in raw.get("issues") or []:
            validated = self._validate_issue(raw_issue, allowed_rule_ids, sources_by_id, request)
            if validated is None:
                downgraded += 1
                continue
            issues.append(validated)

        # --- 验收项覆盖 ---
        coverage = self._validate_coverage(
            request.business_data, raw.get("acceptanceCoverage") or [], sources_by_id, downgraded
        )
        downgraded += sum(
            1 for c in coverage if c.status == CoverageStatus.UNKNOWN.value
        )

        # --- 维度 ---
        dimensions = self._validate_dimensions(
            request, rules_by_id, raw.get("analysisDimensions") or [], issues, coverage, sources_by_id
        )
        downgraded += sum(
            1 for d in dimensions if d.status == DimensionStatus.UNKNOWN.value
        )

        return GroundingResult(
            summary="AI 已按检查规则逐项分析，以下结论均已通过来源原文校验。",
            dimensions=dimensions,
            issues=issues,
            coverage=coverage,
            grounded=downgraded == 0,
            downgraded_count=downgraded,
        )

    # -- 内部：维度校验 --

    def _validate_dimensions(
        self,
        request: GroundingRequest,
        rules_by_id: dict[str, Rule],
        raw_dimensions: list[dict[str, Any]],
        issues: list[GroundedIssue],
        coverage: list[GroundedCoverage],
        sources_by_id: dict[str, SourceReference],
    ) -> list[GroundedDimension]:
        # 模型只允许每个规则返回一次
        raw_by_rule: dict[str, dict[str, Any]] = {}
        for item in raw_dimensions:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or item.get("ruleId") or "").strip()
            if rule_id in rules_by_id:
                raw_by_rule.setdefault(rule_id, item)

        issue_rule_ids = {i.rule_id for i in issues}
        has_unproven_coverage = any(
            c.status in (CoverageStatus.PARTIAL.value, CoverageStatus.UNPROVEN.value)
            for c in coverage
        )

        result: list[GroundedDimension] = []
        for rule_id, rule in rules_by_id.items():
            raw = raw_by_rule.get(rule_id)

            # 模型没返回这个维度
            if raw is None or not str(raw.get("basis") or "").strip():
                result.append(self._unknown(rule, "模型未返回该检查维度的可核验结论。"))
                continue

            status = str(raw.get("status") or "UNKNOWN").upper()
            if status not in {s.value for s in DimensionStatus}:
                status = DimensionStatus.UNKNOWN.value

            # 模型标了风险，但对应问题没通过校验 → 不能采信
            if status == DimensionStatus.RISK.value and rule_id not in issue_rule_ids:
                result.append(
                    self._unknown(
                        rule,
                        "模型标记了风险，但对应问题未通过规则编号或原文引用校验。",
                    )
                )
                continue

            if status == DimensionStatus.UNKNOWN.value:
                result.append(
                    GroundedDimension(
                        rule_id=rule_id,
                        title=rule.title,
                        status=DimensionStatus.UNKNOWN.value,
                        conclusion=str(raw.get("conclusion") or "").strip() or "现有资料不足以形成可靠结论。",
                        quote="",
                        basis=str(raw.get("basis") or "").strip(),
                        confidence=self._confidence(raw.get("confidence")),
                        references=[],
                    )
                )
                continue

            # 解析引用
            cited = self._resolve_all(raw.get("references") or [], sources_by_id)
            if not cited:
                result.append(
                    self._unknown(rule, "模型给出的检查依据未通过原文真实性校验。")
                )
                continue

            # 引用了与本维度无关的来源 → 不能形成结论
            if any(not self._relevant_for_rule(rule_id, s.path) for s in cited):
                result.append(
                    self._unknown(rule, "模型引用了与本检查维度无关的字段，不能形成可靠结论。")
                )
                continue

            # 引文必须在原文里真实存在
            grounded_quote = self._verified_quote(
                str(raw.get("quote") or ""), cited, allow_pass_fallback=(status == "PASS")
            )
            if not grounded_quote:
                result.append(
                    self._unknown(rule, "模型给出的检查依据未通过原文真实性校验。")
                )
                continue

            # 判定 PASS 必须覆盖该维度所需的全部关键字段
            if status == DimensionStatus.PASS.value and not self._covers_required_sources(
                rule_id, request, cited
            ):
                result.append(
                    self._unknown(
                        rule,
                        "模型只核对了部分内容，未覆盖本维度所需的全部关键字段，不能判定为通过。",
                    )
                )
                continue

            # 关键字段存在实质风险时，不得同时判定为通过
            if status == DimensionStatus.PASS.value and self._conflicts_with_substantive_risk(
                rule_id, issues
            ):
                result.append(
                    self._unknown(
                        rule,
                        "本维度依赖的关键字段已在其他检查项中发现实质风险，不能同时判定为通过。",
                    )
                )
                continue

            # 验收项覆盖不完整（PARTIAL / UNPROVEN）时，不得判定为通过 ——
            # 覆盖不全意味着还有关键事实没被核验，通过结论缺乏依据。
            # ⚠️ 这条守卫原本写了但**没接上**（变量算了却没用，ruff F841），
            #    等于「有覆盖缺口也能判通过」。本轮补上。
            if status == DimensionStatus.PASS.value and has_unproven_coverage:
                result.append(
                    self._unknown(
                        rule,
                        "本次验收项覆盖不完整，存在未充分核验的内容，不能判定为通过。",
                    )
                )
                continue

            result.append(
                GroundedDimension(
                    rule_id=rule_id,
                    title=rule.title,
                    status=status,
                    conclusion=str(raw.get("conclusion") or "").strip() or rule.title,
                    quote=grounded_quote,
                    basis=str(raw.get("basis") or "").strip(),
                    confidence=self._confidence(raw.get("confidence")),
                    references=sorted({s.id for s in cited}),
                )
            )

        return result

    # -- 内部：问题校验 --

    def _validate_issue(
        self,
        raw: Any,
        allowed_rule_ids: set[str],
        sources_by_id: dict[str, SourceReference],
        request: GroundingRequest,
    ) -> GroundedIssue | None:
        if not isinstance(raw, dict):
            return None
        rule_id = str(raw.get("rule_id") or raw.get("ruleId") or "").strip()
        basis = str(raw.get("basis") or "").strip()
        quote = str(raw.get("quote") or "").strip()

        if rule_id not in allowed_rule_ids or not basis or not quote:
            return None

        cited = self._resolve_all(raw.get("references") or [], sources_by_id)
        if not cited:
            return None

        # 引用了无关字段 → 丢弃
        if any(not self._relevant_for_rule(rule_id, s.path) for s in cited):
            return None

        # 引文必须真实存在
        grounded_quote = self._verified_quote(quote, cited, allow_pass_fallback=False)
        if not grounded_quote:
            return None

        field_path = str(raw.get("field") or "").strip()
        grounded_field = next(
            (s.path for s in cited if s.path == field_path), cited[0].path
        )

        return GroundedIssue(
            code=str(raw.get("code") or "AI_SEMANTIC_RISK").strip(),
            source=str(raw.get("source") or "AI").strip(),
            severity=self._severity_for_rule(rule_id),
            field=grounded_field,
            title=str(raw.get("title") or "AI 识别到语义风险").strip(),
            rule_id=rule_id,
            quote=grounded_quote,
            basis=basis,
            suggestion=str(raw.get("suggestion") or "").strip(),
            confidence=self._confidence(raw.get("confidence")),
            references=sorted({s.id for s in cited}),
        )

    # -- 内部：验收项覆盖校验 --

    def _validate_coverage(
        self,
        business_data: dict[str, Any],
        raw_coverage: list[dict[str, Any]],
        sources_by_id: dict[str, SourceReference],
        downgraded: int,
    ) -> list[GroundedCoverage]:
        criteria = self._acceptance_criteria(business_data)
        if not criteria:
            return []

        raw_by_criterion: dict[str, dict[str, Any]] = {}
        for item in raw_coverage:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("criterion_id") or item.get("criterionId") or "").strip()
            if cid in criteria:
                raw_by_criterion.setdefault(cid, item)

        result: list[GroundedCoverage] = []
        for cid, text in criteria.items():
            raw = raw_by_criterion.get(cid)
            if raw is None or not str(raw.get("basis") or "").strip():
                result.append(
                    GroundedCoverage(
                        criterion_id=cid,
                        criterion=text,
                        status=CoverageStatus.UNKNOWN.value,
                        basis="模型未返回可核验的逐项判断。",
                        evidence_quote="",
                        confidence=1.0,
                        evidence_references=[],
                    )
                )
                continue

            status = str(raw.get("status") or "UNKNOWN").upper()
            if status not in {s.value for s in CoverageStatus}:
                status = CoverageStatus.UNKNOWN.value

            cited = self._resolve_all(
                raw.get("evidence_references") or raw.get("evidenceReferences") or [],
                sources_by_id,
            )
            requires_evidence = status != CoverageStatus.UNKNOWN.value
            evidence_quote = str(raw.get("evidence_quote") or raw.get("evidenceQuote") or "").strip()

            if requires_evidence and (
                not cited
                or not evidence_quote
                or not self._appears_in_any(evidence_quote, cited)
            ):
                result.append(
                    GroundedCoverage(
                        criterion_id=cid,
                        criterion=text,
                        status=CoverageStatus.UNKNOWN.value,
                        basis="模型给出的证据引用未通过原文真实性校验。",
                        evidence_quote="",
                        confidence=1.0,
                        evidence_references=[],
                    )
                )
                continue

            result.append(
                GroundedCoverage(
                    criterion_id=cid,
                    criterion=text,
                    status=status,
                    basis=str(raw.get("basis") or "").strip(),
                    evidence_quote=evidence_quote if requires_evidence else "",
                    confidence=self._confidence(raw.get("confidence")),
                    evidence_references=sorted({s.id for s in cited}),
                )
            )

        return result

    # -- 内部：工具方法 --

    def _resolve_all(
        self, reference_ids: list[str], sources_by_id: dict[str, SourceReference]
    ) -> list[SourceReference]:
        """解析引用。任何一个 id 无效 → 整组无效（避免"半真半假"的引用集）。"""
        if not reference_ids:
            return []
        out: list[SourceReference] = []
        for rid in reference_ids:
            source = sources_by_id.get(str(rid).strip())
            if source is None:
                return []
            out.append(source)
        return out

    def _appears_in_any(self, quote: str, sources: list[SourceReference]) -> bool:
        """引文是否出现在任一来源的原文中。

        同时比对原文与脱敏后文本 —— 因为给模型看的是脱敏版本，
        模型引的可能是脱敏后的样子。

        两级判定：
        1. 严格：归一化后互为子串（保留空白差异）
        2. 宽松：去空白后互为子串（容忍模型插入/省略空格）
        """
        normalized_quote = normalize_for_match(quote)
        if not normalized_quote:
            return False

        loose_quote = normalize_for_containment(quote)
        if not loose_quote:
            return False

        for source in sources:
            for candidate in (source.content, redact_text(source.content)):
                if not candidate:
                    continue
                strict = normalize_for_match(candidate)
                if strict and (strict in normalized_quote or normalized_quote in strict):
                    return True
                loose = normalize_for_containment(candidate)
                if loose and (loose in loose_quote or loose_quote in loose):
                    return True
        return False

    def _verified_quote(
        self, quote: str, sources: list[SourceReference], *, allow_pass_fallback: bool
    ) -> str:
        """校验并返回可信引文。

        **关键语义（比 Java 版更严格）**：

        1. 模型给了引文，且引文原样出现在某来源 → 采信
        2. 模型给了引文，且引文是某来源的子串 → 采信该来源（模型可能截短了）
        3. 模型给了引文，但校验不过 → **返回空串（校验失败）**
           —— 绝不用兜底替换。否则编造的引文会被静默换成真内容，校验形同虚设
        4. 模型**没给**引文，且允许兜底（PASS 类结论）→ 回退到主来源前 120 字
        5. 模型没给引文，且不允许兜底 → 空串
        """
        if not sources:
            return ""

        has_quote = bool(quote.strip())

        if has_quote:
            if self._appears_in_any(quote, sources):
                return quote.strip()

            # 引文是某来源的子串（模型可能截短了引文，或插入了空格）
            normalized_quote = normalize_for_containment(quote)
            embedded = [
                s
                for s in sources
                if s.content and normalize_for_containment(s.content) in normalized_quote
            ]
            if embedded:
                best = max(embedded, key=lambda s: len(redact_text(s.content)))
                return redact_text(best.content)

            # 给了但对不上 → 校验失败，不兜底
            return ""

        # 没给引文
        if not allow_pass_fallback:
            return ""

        preferred = next(
            (
                s
                for s in sources
                if s.path.endswith((".content", ".summary", ".description", ".result"))
            ),
            sources[0],
        )
        content = redact_text(preferred.content).strip()
        return content[:120] if len(content) > 120 else content

    def _covers_required_sources(
        self, rule_id: str, request: GroundingRequest, cited: list[SourceReference]
    ) -> bool:
        """判定"通过"是否覆盖了该维度所需的全部关键来源。"""
        required = request.required_references.get(rule_id) or []
        if not required:
            return True
        cited_ids = {s.id for s in cited}
        return set(required).issubset(cited_ids)

    def _conflicts_with_substantive_risk(
        self, rule_id: str, issues: list[GroundedIssue]
    ) -> bool:
        """关键字段存在实质风险时，不得同时判定为通过。"""
        substantive = {"MEDIUM", "HIGH", "BLOCKING"}
        for issue in issues:
            if issue.severity.upper() in substantive and self._risk_invalidates_pass(
                issue.rule_id, rule_id
            ):
                return True
        return False

    def _acceptance_criteria(self, business_data: dict[str, Any]) -> dict[str, str]:
        """从业务数据里取验收项定义。"""
        raw = business_data.get("acceptanceCriteria")
        if not isinstance(raw, list):
            return {}
        out: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip()
            text = str(item.get("text") or "").strip()
            if cid and text:
                out.setdefault(cid, text)
        return out

    def _unknown(self, rule: Rule, basis: str) -> GroundedDimension:
        return GroundedDimension(
            rule_id=rule.key,
            title=rule.title,
            status=DimensionStatus.UNKNOWN.value,
            conclusion="现有资料不足以形成可靠结论。",
            quote="",
            basis=basis,
            confidence=1.0,
            references=[],
        )

    def _confidence(self, value: Any) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, v))

    def _collect_sources(
        self, value: Any, path: str, result: list[SourceReference]
    ) -> None:
        """递归收集业务数据中的可引用来源。

        只收集标量内容（字符串/数字/布尔）—— 列表和对象作为结构，
        它们的叶子节点才成为来源。
        """
        if value is None:
            return

        # instruction 是给模型的指令，不是事实来源
        if path.endswith(".instruction"):
            return

        if isinstance(value, dict):
            for k, v in value.items():
                self._collect_sources(v, f"{path}.{k}", result)
            return

        if isinstance(value, list):
            for i, v in enumerate(value):
                self._collect_sources(v, f"{path}[{i}]", result)
            return

        if isinstance(value, (str, int, float, bool)):
            content = str(value).strip()
            if not content:
                return
            result.append(
                SourceReference(
                    id=f"SRC_{len(result) + 1:04d}",
                    path=path,
                    label=self._human_label(path),
                    content=content,
                )
            )

    def _human_label(self, path: str) -> str:
        """把路径转成人能读的标签。"""
        if path.startswith("businessData."):
            path = path[len("businessData.") :]
        return path.replace(".", " · ").replace("[", " 第").replace("]", " 项")
