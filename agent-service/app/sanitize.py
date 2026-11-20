"""模型输出红线后处理（P3-2 压测驱动）。

## 为什么需要这个

真模型压测（22 个真实用例 × 3 轮）暴露了两个**真实缺陷**：

| 缺陷 | 现象 | 检出率 |
|---|---|---|
| **分数泄漏** | 模型把证据 `impact` 里的「个人绩效分：95」抄进评语 | 10 处 / 3 轮 |
| **编造引用 ID** | 模型给考勤编了个不存在的 ID（`attendance-2026-11-14`） | 3 例 / 3 轮 |

⚠️ 这两个都不是「模型乱说」——
  - 分数**确实在原文里**（`result_evidence_materialization._build_impact` 写的）
  - 引用 ID 是模型**为了让格式统一**自己造的
  所以**不能靠"骂模型"解决**，必须在输出侧做**确定性清洗**。

## 原则

1. **只删，不改写** —— 清洗后的文本仍是模型的原意，不引入新事实
2. **可追溯** —— 每处清洗都记进 `warnings`，前端能提示用户
3. **宁缺勿错** —— 删掉可疑片段，而不是留着赌它是真的

## 边界

⚠️ 只处理**已知的、可确定性识别**的违规形态。
   「模型是否在说假话」这件事本身不由这里判断 —— 那是 grounding 的职责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SanitizeResult:
    """清洗结果。"""

    text: str
    #: 被删掉的片段（供审计/提示）
    removed: list[str] = field(default_factory=list)
    #: 是否发生过清洗
    changed: bool = False


# ---------------------------------------------------------------------------
# 1. 分数泄漏
# ---------------------------------------------------------------------------

# ⚠️ 匹配「绩效分：95」「个人绩效分 95」「绩效分为 95 分」这类**带数值**的表述。
#
# ⚠️ 为什么只匹配「带数值」的：
#   「不要复述分数」「不涉及总分计算」这类**免责/元话语**不含数值，
#   删了反而会把合规声明删掉 —— 反而制造幻觉。
_SCORE_WITH_VALUE = re.compile(
    r"[（(\[【]?\s*"
    r"(?:个人)?(?:绩效分|考核分|得分|总分|最终分|最终得分|综合得分)"
    r"\s*(?:为|是|[:：])?\s*"
    r"\d+(?:\.\d+)?\s*(?:分|%)?"
    r"\s*[）)\]】]?"
)


def _balance_parens(text: str) -> str:
    """把括号配平 —— 删掉**不成对的多余右括号**。

    ⚠️ 为什么需要：删除「（绩效分 95，审批已给出意见）」这种片段时，
       如果只删掉「绩效分 95」而留下「（，审批已给出意见）」，或者
       把整个括号内容删了却留下右括号「）」——
       评语里就会留下悬空括号，渲染出来很难看。

    做法：从左到右扫，维护**栈**；
      - 遇到左括号 → 压栈
      - 遇到右括号 → 栈空则**丢弃这个右括号**（多余），否则弹栈
    最后把栈里剩下的左括号（没闭合的）也删掉。
    """
    pairs = {"）": "（", ")": "(", "】": "【", "]": "["}
    openers = {"（", "(", "【", "["}
    stack: list[str] = []
    out: list[str] = []

    for ch in text:
        if ch in openers:
            stack.append(ch)
            out.append(ch)
        elif ch in pairs:
            if stack and stack[-1] == pairs[ch]:
                stack.pop()
                out.append(ch)
            else:
                # 多余的右括号 → 丢弃
                continue
        else:
            out.append(ch)

    # 栈里剩下的左括号 → 没闭合，删掉
    result = "".join(out)
    for opener in stack:
        result = result.replace(opener, "", 1)
    return result


def strip_score_leak(text: str) -> SanitizeResult:
    """删掉评语里的分数表述。

    ⚠️ 只删「分数 + 数值」的整体片段 —— 保留句子其余部分。
       例：「完成率 100%，个人绩效分 95」→ 「完成率 100%」
    """
    if not text:
        return SanitizeResult(text=text)

    removed: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        removed.append(match.group(0).strip())
        # 保留分隔符 —— 直接删会让句子黏连
        return ""

    cleaned = _SCORE_WITH_VALUE.sub(_sub, text)

    # ⚠️ 什么都没删 → **原样返回**（含首尾空白）。
    if not removed:
        return SanitizeResult(text=text, removed=[], changed=False)

    # 清掉删除后留下的悬空标点与多余空白
    #
    # ⚠️ **不能**用 `\s{2,}` → " " 折叠空白 ——
    #    `\s` 包含 `\n`，会把 Markdown 的换行全压成一行，
    #    评语就变成一坨。必须只折叠**行内**空白（空格/制表符）。
    cleaned = re.sub(r"[，,；;]\s*(?=[，。；、）)\]]|$)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[：:]\s*(?=[，。；、）)\]])", "", cleaned)

    # ⚠️ **括号配平** —— 这是最容易出的错。
    #    删掉「（绩效分 95，审批已给出意见）」里的分数时，若只删内层，
    #    就会剩下悬空的「）」。
    #    做法：先把「空括号」删掉，再把**不成对的多余右括号**删掉。
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned)
    cleaned = _balance_parens(cleaned)

    cleaned = cleaned.strip()

    return SanitizeResult(text=cleaned, removed=removed, changed=bool(removed))


# ---------------------------------------------------------------------------
# 2. 编造的引用 ID
# ---------------------------------------------------------------------------

# 模型自造的考勤占位符：`attendance-2026-11-14` / `attendance_anomalies` / `attendance-anomaly`
_ATTENDANCE_PLACEHOLDER = re.compile(
    r"\battendance[_\-][A-Za-z0-9_\-]+", re.IGNORECASE
)

# 引用标记：`[evidence: xxx]` / `（证据：xxx）` / `（evidence xxx）` / `[xxx]`
_CITATION_MARKER = re.compile(
    r"[（(\[【]?\s*(?:evidence|证据|evidence_id|evidence id)\s*[:：]?\s*"
    r"[A-Za-z0-9_\-]+"
    r"\s*[）)\]】]?",
    re.IGNORECASE,
)


def strip_fake_references(text: str, legal_refs: list[str]) -> SanitizeResult:
    """删掉模型编造的引用 ID。

    ⚠️ 合法引用的**格式**（如 `[evidence: dc3c789f-...]`）保留 ——
       那是可追溯性，不是幻觉。
       只有**不在合法集合里**的才删。
    """
    if not text:
        return SanitizeResult(text=text)

    legal = {r.lower() for r in legal_refs}
    removed: list[str] = []

    # ① 先处理考勤占位符 —— 它连「证据」标记都不是，是模型自造的
    def _attendance_sub(match: re.Match[str]) -> str:
        removed.append(match.group(0))
        return ""

    cleaned = _ATTENDANCE_PLACEHOLDER.sub(_attendance_sub, text)

    # ② 再检查引用标记里的 ID 是否合法
    def _citation_sub(match: re.Match[str]) -> str:
        raw = match.group(0)
        # ⚠️ 抠 ID 时**不能**用 `[A-Za-z0-9_\-]+$` ——
        #    匹配串结尾可能是 `]`（如 `[evidence: <uuid>]`），
        #    `$` 锚在括号之后就永远抠不出来。
        #    正确做法：把结尾的括号剥掉，再抠 ID。
        core = raw.rstrip("）)\\]】】" + " \t")
        found = re.search(r"[A-Za-z0-9_\-]+$", core)
        if not found:
            return raw
        ref = found.group(0)
        if legal and ref.lower() not in legal:
            removed.append(ref)
            return ""
        return raw

    cleaned = _CITATION_MARKER.sub(_citation_sub, cleaned)

    # ⚠️ 什么都没删 → **原样返回**（含首尾空白）。
    #    清洗器擅自改动合规内容会让人不再信任它。
    if not removed:
        return SanitizeResult(text=text, removed=[], changed=False)

    cleaned = re.sub(r"[，,；;]\s*(?=[，。；、）)\]]|$)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = cleaned.strip()

    return SanitizeResult(text=cleaned, removed=removed, changed=bool(removed))


# ---------------------------------------------------------------------------
# 3. 编造的引文（幻觉引文）
# ---------------------------------------------------------------------------

# ⚠️ 模型给结论加引号时，会把「自己的做法/概括」也放进引号。
#
#   真实案例（2026-11-20 第三轮压测才出现 —— 模型非确定性）：
#     原文只说：「联调过程无阻塞缺陷，**问题均已闭环**」
#     模型却写：「继续坚持『**问题当天处理完毕**』的做法，以缩短联调周期」
#                ↑ 加了引号 = 暗示这是**有出处**的原话
#     —— 而「当天处理完毕」这个**具体做法**原文里根本没有。
#
#   这比编造数字更隐蔽：数字会露馅，具体做法听起来完全合理。
#
# 判定口径（与压测检测器一致）：
#   引文片段必须能在**原文**（证据 summary + impact + 考勤）里找到，
#   或者属于「模型对原文的改写」（锚定实词 ≥2）。
#   都不满足 → 判定为编造 → **降级为无引号的普通陈述**（不删内容）。

_QUOTE_PAIRS = (
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
)

#: 至少要锚定这么多**原文实词**才认为是「改写」而不是「编造」
_PARAPHRASE_MIN_ANCHORS = 2


def _norm(text: str) -> str:
    """归一化：去空白 + 小写。"""
    return re.sub(r"\s+", "", text).lower()


def _anchors(fragment: str, source_texts: list[str]) -> set[str]:
    """片段里能在原文找到的「实词锚点」。

    实词 = 原文里出现过的、长度 ≥2 的**连续中文词块**。
    ⚠️ 只取原文里有的词 —— 模型新造的词（如「当天」「处理完毕」）不会有锚点。
    """
    norm = _norm(fragment)
    joined = _norm("".join(source_texts))
    out: set[str] = set()
    for size in (4, 3, 2):
        for i in range(len(norm) - size + 1):
            term = norm[i : i + size]
            if term in joined:
                out.add(term)
    return out


def _is_paraphrase(fragment: str, source_texts: list[str]) -> bool:
    """片段是否只是原文的改写（锚定词够多 → 不算编造）。"""
    anchors = _anchors(fragment, source_texts)
    if len(anchors) >= _PARAPHRASE_MIN_ANCHORS:
        return True
    return False


def strip_fabricated_quotes(
    text: str, source_texts: list[str] | None = None
) -> SanitizeResult:
    """把**编造的引文**降级为普通陈述。

    ⚠️ **只降级，不删除** ——
       删掉整句会让人看不懂评语；去掉引号则保留内容，
       同时不再暗示「这是原话」。

    ⚠️ **合规引文必须原样保留** ——
       模型如实引用审批意见「成果内容与证据符合要求，同意通过」，
       或改写「问题即发现即闭环」，都不能动。

    Args:
        source_texts: 模型能看到的**全部原文**（证据 summary + impact + 考勤摘要）。
            为空则**不做任何处理**（宁缺勿错：没有原文就无法判定）。
    """
    if not text or not source_texts:
        return SanitizeResult(text=text)

    removed: list[str] = []

    def _handle(match: re.Match[str]) -> str:
        fragment = match.group(1)
        norm = _norm(fragment)
        if not norm:
            return match.group(0)
        # ① 原文里就有 → 保留
        if any(norm in _norm(src) for src in source_texts):
            return match.group(0)
        # ② 是原文的改写（锚定词够多）→ 保留
        if _is_paraphrase(fragment, source_texts):
            return match.group(0)
        # ③ 编造 → 去掉引号，降级为普通陈述
        removed.append(fragment)
        return fragment

    cleaned = text
    for opener, closer in _QUOTE_PAIRS:
        pattern = re.compile(
            re.escape(opener) + r"([^" + re.escape(opener + closer) + r"]{4,120})" + re.escape(closer)
        )
        cleaned = pattern.sub(_handle, cleaned)

    if not removed:
        return SanitizeResult(text=text, removed=[], changed=False)

    return SanitizeResult(text=cleaned, removed=removed, changed=True)


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------


def sanitize_comment(
    text: str,
    legal_refs: list[str] | None = None,
    source_texts: list[str] | None = None,
) -> SanitizeResult:
    """对评语做全部红线清洗。

    顺序：**先删假引用 → 再降级编造引文 → 最后删分数**。
    ⚠️ 顺序不能反 —— 分数文本里可能含 `[...]`，先删分数会让引用标记残缺，
       反而匹配不上「非法引用」规则。

    ⚠️ **没发生清洗时必须原样返回** ——
       包括首尾空白。评语是 Markdown，首尾多一个换行不影响渲染，
       但「清洗器擅自改了没问题的内容」会让人不再信任它的输出。

    Args:
        legal_refs: 合法证据 ID 集合（不在集合里的引用会被删掉）。
        source_texts: 模型能看到的**全部原文** —— 用于判定引文是编造还是如实引用。
            为空 → **不做引文判定**（宁缺勿错）。
    """
    result = strip_fake_references(text, legal_refs or [])
    text, removed_refs = result.text, list(result.removed)

    quote_result = strip_fabricated_quotes(text, source_texts)
    text, removed_quotes = quote_result.text, list(quote_result.removed)

    score_result = strip_score_leak(text)
    changed = result.changed or quote_result.changed or score_result.changed

    if not changed:
        # 一个字都不动
        return SanitizeResult(text=text, removed=[], changed=False)

    return SanitizeResult(
        text=score_result.text,
        removed=removed_refs + removed_quotes + score_result.removed,
        changed=True,
    )
