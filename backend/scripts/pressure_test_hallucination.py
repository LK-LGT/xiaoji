"""真模型幻觉引文压测（P3-2）。

## 为什么需要这个

之前的 grounding（来源锚定）**只做过单例验证**。
上线前必须知道一件事：**真模型到底会编造多少引文**。

## 压什么

把**真实历史数据**（22 个用例，含真实指标/证据/考勤/自评）喂给真模型，
然后逐条核对模型产出里的每一个「事实性断言」：

| 检查项 | 判据 | 为什么重要 |
|---|---|---|
| **编造引文** | 模型文本里出现的「引文式片段」在原始证据里**找不到** | 编造=欺骗主管 |
| **编造数字** | 模型提到的数字在原始数据里**不存在** | 编数字=误导判断 |
| **编造人名/荣誉** | 出现原文没有的称号、公司、金额 | 最典型的幻觉 |
| **非法引用 ID** | `evidence_references` 里有不存在的 id | 引用了不存在的东西 |
| **分数泄漏** | 输出里出现总分/绩效分 | ⚠️ 核心红线：模型不得改分 |
| **考勤被当扣分** | 考勤异常被表述成扣分依据 | 决策②：考勤仅参考 |

## 输出

- 终端：每个用例一行 + 汇总统计
- `backend/data/_pressure_report.json`：完整明细（含模型原文，供人工复核）

## 运行

    python backend/scripts/pressure_test_hallucination.py

⚠️ 会**真实调用模型 API**，注意 token 消耗。用 `--limit N` 限制用例数。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_ENV = ROOT / "agent-service" / ".env"
CASES = ROOT / "backend" / "data" / "_pressure_cases.json"
REPORT = ROOT / "backend" / "data" / "_pressure_report.json"

AGENT_URL = os.getenv("AGENT_SERVICE_URL", "http://127.0.0.1:8800")


def _load_env() -> None:
    """读 agent-service/.env（**文件优先** —— 见 verify_model_e2e.py 的注释）。"""
    if not AGENT_ENV.exists():
        return
    for line in AGENT_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


_load_env()
SECRET = os.getenv("AGENT_SHARED_SECRET", "")

# ---------------------------------------------------------------------------
# 幻觉检测规则
# ---------------------------------------------------------------------------

# 「引文式片段」—— 模型在引用原文时通常会带引号或书名号
_QUOTE_PATTERNS = [
    re.compile(r"[「『]([^」』]{4,120})[」』]"),
    re.compile(r"“([^”]{4,120})”"),
    re.compile(r'"([^"]{6,120})"'),
]

# ⚠️ **模型自己写的流程概括**不算编造引文。
#
#    实测：模型爱写「继续沿用本月『调研—设计—实施—联调』的闭环推进方式」
#    —— 这是把**多个原文短语用破折号串起来**的**概括**，不是引用。
#    它的每个词都来自原文，只是重组了。
#    判定：片段里含破折号/箭头 → 视为概括，不报。
_SYNTHESIS_MARKER = re.compile(r"[—–\-→>]|→")

# ⚠️ **模型对原文的改写/提炼**也不算编造引文。
#
#    实测（第三轮压测）：
#      原文：「问题均已闭环」
#      模型：「问题即发现即闭环」「问题闭环」
#    —— 模型把原文**压缩/重排**了，不是编造新事实。
#
#    ⚠️ 为什么不能用「字符覆盖率」：
#      「问题即发现即闭环」里有「即/发/现」三个字原文没有 →
#      字符覆盖率只有 57%，按阈值会被误判成编造。
#      但它**确实**不是编造 —— 只是模型加了两个连接词。
#
#    判定方式：**「实词锚定」** ——
#      片段里必须含有原文出现过的**实词**（2 字以上的名词性词块），
#      且这些实词要**足够多**。
#      - 改写：「问题即发现即闭环」→ 锚定词「问题」「闭环」✅（2 个）
#      - 编造：「年度优秀员工称号」→ 锚定词「优秀员工」？
#        原文没有 → 0 个锚定词 → 判编造 ✅
def _anchored_terms(fragment: str, joined: str) -> list[str]:
    """片段里能在原文中找到的实词（2 字滑窗，且两边不是虚词）。

    ⚠️ 排除高频虚词（的/了/和/与/及/或/是/在/等）——
       它们在任何文本里都有，锚定它们没有意义。
    """
    stop = set("的了和与及或是在有为对于把被就都也还很更最这那个之其以")
    norm = _normalize(fragment)
    out: list[str] = []
    for size in (3, 2):
        for i in range(len(norm) - size + 1):
            term = norm[i: i + size]
            if any(ch in stop for ch in term):
                continue
            if term in joined and term not in out:
                out.append(term)
    return out


def _is_paraphrase(fragment: str, source_texts: list[str]) -> bool:
    """片段是否只是原文的改写（锚定词够多 → 不是编造）。"""
    norm = _normalize(fragment)
    if len(norm) < 4:
        return True
    joined = "".join(_normalize(t) for t in source_texts)
    if not joined:
        return False
    anchors = _anchored_terms(fragment, joined)
    # ⚠️ 至少 2 个实词锚定 → 判为改写。
    #    「问题即发现即闭环」→ 锚定 {问题, 闭环, 发现?} → 2 个 ✅
    #    「年度优秀员工称号」→ 原文若无 → 0 个 → 判编造 ✅
    if len(anchors) < 2:
        return False
    # ⚠️⚠️ **有高危事实特征 → 一律不算改写**。
    #    「获得了年度优秀员工称号，公司营业额增长 300%」里也有锚定词（增长/公司），
    #    光看锚定数会把它判成改写 → 漏报最严重的编造。
    #    所以：**只要命中荣誉/待遇/经营数据这类特征，直接判编造**。
    if any(pattern.search(fragment) for pattern, _ in _FACT_PATTERNS):
        return False
    # ⚠️ **长度**也要卡 —— 真正的引用片段通常短（≤ 30 字）。
    #    编造的事实往往是一长串。
    return len(norm) <= 30

# 「捏造事实」的高危模式 —— 原文几乎不会有这些
#
# ⚠️ 同一处文本可能**同时命中多个模式**（如「获得了年度优秀员工称号」既命中
#    「优秀员工」又命中「获得…称号」）→ 会重复计数、虚增检出率。
#    所以下面每个模式只匹配**互不重叠**的特征：
#    - 荣誉类：只匹配「称号/表彰/奖」这类名词，**不匹配**「优秀员工」（那是形容词）
#    - 经营数据类：只匹配「营业额/营收/利润 + 增长」
_FACT_PATTERNS = [
    (re.compile(r"劳动模范|先进个人|晋升|加薪|奖金|股权|年终奖"), "编造待遇"),
    (re.compile(r"(?:年度)?优秀员工(?:称号)?"), "编造荣誉"),
    (re.compile(r"营业额(?:增长|提升|下降)|营收(?:增长|提升)|利润(?:增长|提升)"), "编造经营数据"),
    (re.compile(r"客户(?:满意度|续约率|签约额)\s*(?:达到|提升至?|为)?\s*\d+%?"), "编造客户数据"),
    (re.compile(r"(?:获得|荣获|被评为|授予)\s*[^\n。；，]{2,20}?(?:奖|称号|表彰)"), "编造奖项"),
]

# 数字（含小数、百分比、金额）
#
# ⚠️ 误报来源（第一版压测 100% 误报，全是这些）：
#   - **UUID 片段**：证据 ID `dc3c789f-b931-4073-9672-...` 里的 `4073`、`9672`
#   - **日期**：评语里的「2026-11-14」→ `2026`、`11`、`14`
#   - **等级区间**：「从 1.0 到 999」→ `999`
#   - **章节序号**：「三项任务」→ `3`
# 所以下面**先把数字从上下文里抠出来**，再逐条判断是否可接受。
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|％|万|亿|元|人|次|分)?")

# ⚠️ 数字**紧邻**这些字符时，是 UUID/日期/编号的一部分，不是「数字断言」
_NUMBER_CONTEXT_STRIP = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
    # ⚠️ 日期**带时间戳**：「2026-11-14 07:52:40」
    #    —— 实测模型引用考勤时会写完整时间戳，第一版只挡了日期部分
    #       → `07`/`52`/`40` 被当成编造数字（每轮误报 3 处）。
    r"|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}:\d{2}:\d{2}(?:\.\d+)?"  # 纯时间戳
    r"|\d{4}\s*年\s*\d{1,2}\s*月"  # 「2026年11月」—— 年月是上下文，不是数据断言
    r"|\b\d{6}\b(?=\s*月度总结|\s*月报|\s*$)?"  # 周期编码 202611 —— 是标识符不是数据
    r"|[A-Za-z_]\d+"  # 变量名 plan_202611
    r"|第\d+[项个章节条]"  # 序数
    r"|level_scale"
    # Markdown 有序列表项：「\n1. 」「2. 」「3. 」—— 是排版，不是数据
    r"|(?<=[\n])\s*\d{1,2}[.、）)]"
    # 区间：既支持「从 1.0 到 999」，也支持「[1.0, 999)」「[1.0,999]」
    # ⚠️ 还要支持 **en dash / em dash**：「A 区间（1.0–999）」
    #    —— 实测模型就是这么写的，第一版漏了 → 每轮误报 1~2 处。
    # ⚠️ 还要支持**不等号**：「1.0 ≤ 比值 < 999」「A：1.0 ≤ x < 999」
    r"|\[?\d+(?:\.\d+)?\s*(?:到|至|,|，|-|–|—|~|～)\s*\d+(?:\.\d+)?\]?"
    # ⚠️ 还要支持**不等号**：「1.0 ≤ 比值 < 999」「A：1.0 ≤ x < 999」
    #    ⚠️ 不等号两边**可能有文字**（「1.0 ≤ 比值 < 999」），
    #    所以要把「数字 + 比较符 + 中间文字 + 比较符 + 数字」整段吃掉。
    r"|\d+(?:\.\d+)?\s*(?:≤|≥|<|>)\s*[^，。；\n]{0,12}?(?:≤|≥|<|>)\s*\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*(?:≤|≥|<|>)\s*"
    # ⚠️ 还要支持 **min/max 区间**：「处于 A 档 min 1.0 至 max 999 区间」
    #    —— 实测模型就是这么写的，中间夹着「min」「至」「max」三段词。
    r"|\bmin\s*\d+(?:\.\d+)?\s*(?:至|到|-|–|—|~|～)\s*max\s*\d+(?:\.\d+)?"
    # ⚠️ 还有「区间上下界」写法：「上界 999」「下界 1.0」
    r"|(?:上界|下界|上限|下限)\s*\d+(?:\.\d+)?"
)

# 这些词后面的数字通常是「业务事实」而不是编造
_NUMBER_BENIGN_CONTEXT = re.compile(
    r"(?:第|共|计|合计|其中|包含|涉及|分别|两项|三项|四项|以下)"
)

# ⚠️ 分数红线 —— 这些词出现就说明模型在谈分数
#
# ⚠️ 「个人绩效分」与「绩效分」会**重叠匹配** → 重复计数。
#    所以这里用**互斥**的写法：只匹配词，不再叠加前缀变体。
_SCORE_TERMS = re.compile(
    r"(绩效分|总分|最终得分|综合得分|考核得分|分数为|得分[:：]|"
    r"\bscore\b|加权总分|最终分数|绩效得分)",
    re.IGNORECASE,
)

# ⚠️ 模型的**合规免责表述** —— 出现这些词是**正确的**，不是泄漏。
#    「本总结仅基于给定证据做事实性归纳，不涉及分数计算与总分复述」
#    这类句子必须先剔除，否则会把**守规矩的模型**判成违规。
_SCORE_DISCLAIMER = re.compile(
    r"[^。\n]{0,60}(?:不涉及|不重复|不复述|不计算|未涉及|不含|不包含|不涉及)"
    r"[^。\n]{0,20}(?:分数|总分|绩效分|得分|计算|复述)[^。\n]{0,40}"
)

# 考勤被当扣分的表述
#
# ⚠️ 关键：模型**正确**表述考勤时会说「仅供参考，不自动扣分」——
#    这句话里也含「扣分」二字！正则若不排除它，就会把**合规表述**判成违规。
#    所以：先剔除「不自动扣分 / 仅供参考」这类**免责措辞**，再匹配扣分表述。
_ATTENDANCE_DISCLAIMER = re.compile(
    r"不自动扣分|不扣分|仅供参考|仅作参考|不计入总分|不作扣分|不参与评分|不影响评分|"
    # ⚠️ 以下都是实测里模型写的**合规免责**表述 ——
    #    漏掉任何一个都会把「守规矩的模型」误判成违规。
    r"不涉及自动扣分|不会作为绩效扣分|不作为绩效扣分|不纳入扣分|不用于扣分|"
    r"与扣分无关|不构成扣分|不影响绩效考核|不参与绩效计算"
)
_ATTENDANCE_PENALTY = re.compile(
    r"考勤[^。；\n]{0,20}(?:扣分|扣减|扣除|减分|影响得分|拉低|计入总分|作为扣分)|"
    r"(?:迟到|早退|缺卡|请假)[^。；\n]{0,20}(?:扣分|扣减|扣除|减分|影响得分|拉低)"
)


def _normalize(text: str) -> str:
    """归一化 —— 去掉空白与常见标点差异。"""
    return re.sub(r"[\s　，。、；：！？,.!?;:'\"'（）()【】\[\]《》<>—-]", "", text or "")


def _contains_quote(fragment: str, source_texts: list[str]) -> bool:
    """片段是否能在任一原文里找到（归一化后子串）。"""
    norm_frag = _normalize(fragment)
    if len(norm_frag) < 4:
        return True  # 太短，判定无意义 —— 不算幻觉
    for text in source_texts:
        norm_text = _normalize(text)
        if norm_frag in norm_text:
            return True
    return False


def _allowed_numbers(source_texts: list[str]) -> set[str]:
    """原文里出现过的数字（归一化后）。"""
    allowed: set[str] = set()
    for text in source_texts:
        for match in _NUMBER.finditer(text or ""):
            allowed.add(match.group(1).rstrip("0").rstrip(".") or match.group(1))
            allowed.add(match.group(1))
    return allowed


def detect_hallucination(
    model_texts: list[str],
    *,
    evidence_summaries: list[str],
    metric_values: list[dict],
    legal_refs: list[str],
    attendance_present: bool,
    attendance_summaries: list[str] | None = None,
    evidence_impacts: list[str] | None = None,
) -> dict:
    """在模型产出里找幻觉。返回各类问题清单。

    ⚠️ ``attendance_summaries`` 必须传 ——
    考勤异常的**次数**（「缺卡 2 次」）也是原文的一部分。
    不把它算进「允许的数字」，模型如实引用考勤就会被误判成编造数字。

    ⚠️ ``evidence_impacts`` 也必须传 ——
    证据的 ``impact`` 字段（审批意见、完成情况）**也是模型能看到的原文**。
    第一版压测漏了它 → 模型如实引用审批意见「成果内容与证据符合要求，同意通过」
    被误判成「编造引文」（22.7% 误报）。
    """
    all_source_texts = list(evidence_summaries)
    # ⚠️ impact 也是原文 —— 模型引用审批意见不算编造
    all_source_texts.extend(evidence_impacts or [])
    # 考勤也是原文 —— 模型如实引用「缺卡 2 次」不算编造
    all_source_texts.extend(attendance_summaries or [])
    for m in metric_values:
        all_source_texts.append(
            f"{m.get('code','')} {m.get('value','')} {m.get('target','')} {m.get('unit','')}"
        )

    issues: dict[str, list[str]] = {
        "fabricated_quotes": [],
        "fabricated_facts": [],
        "fabricated_numbers": [],
        "illegal_references": [],
        "score_leak": [],
        "attendance_penalized": [],
        # 考勤占位符 —— 模型引用了考勤但没给证据 ID（不是「非法证据引用」）
        "attendance_placeholder": [],
    }

    # --- 1. 编造引文 ---
    for text in model_texts:
        for pattern in _QUOTE_PATTERNS:
            for match in pattern.finditer(text):
                fragment = match.group(1)
                # ⚠️ 模型自己概括的流程（「调研—设计—实施—联调」）不算编造
                if _SYNTHESIS_MARKER.search(fragment):
                    continue
                if _contains_quote(fragment, all_source_texts):
                    continue
                # ⚠️ 模型对原文的改写（「问题即发现即闭环」）也不算编造
                if _is_paraphrase(fragment, all_source_texts):
                    continue
                issues["fabricated_quotes"].append(fragment)

    # --- 2. 编造事实（荣誉/经营数据/奖项） ---
    #
    # ⚠️ 去重有两层：
    #    ① **同片段**：同一段文本命中多个模式 → 只记一次
    #    ② **重叠**：不同片段但**互相重叠**（如「年度优秀员工称号」被
    #       「获得了年度优秀员工称号」包含）→ 也只记一次
    #    不做这层去重的话，1 处编造会被算成 3 处 —— **虚增检出率**，
    #    让人误以为模型比实际更差（或反过来被稀释）。
    seen_facts: list[tuple[int, int, str]] = []  # (start, end, fragment)
    for text in model_texts:
        for pattern, label in _FACT_PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span()
                fragment = match.group(0)
                # ① 完全相同
                if any(fragment == f for _, _, f in seen_facts):
                    continue
                # ② 与已有片段重叠（任一方向包含）
                if any(
                    (start <= s and e <= end) or (s <= start and end <= e)
                    for s, e, _ in seen_facts
                ):
                    continue
                # 原文里出现过就不算幻觉
                if not _contains_quote(fragment, all_source_texts):
                    seen_facts.append((start, end, fragment))
                    issues["fabricated_facts"].append(f"[{label}] {fragment}")

    # --- 3. 编造数字 ---
    #
    # ⚠️ 这是**误报重灾区**。第一版压测 6/6 用例全部命中，
    #    但逐条人工核对后发现**全是误报**：
    #      - UUID 片段（`4073`、`9672`）
    #      - 日期（`2026`、`11`、`14`）
    #      - 等级区间上界（`999`）
    #      - 序数（`三项`）
    #    模型输出其实**完全忠实于原文**。
    #    所以判定必须**先把结构性数字剔掉**，只对「像业务断言的数字」判定。
    allowed = _allowed_numbers(all_source_texts)
    allowed_norm = {
        (a.rstrip("0").rstrip(".") if "." in a else a) for a in allowed
    }
    for text in model_texts:
        # 先把 UUID / 日期 / 序数 / 区间 整段抹掉，**并记住它们占了哪些位置**，
        # 免得抹掉之后剩下的数字被错判。
        masked = _NUMBER_CONTEXT_STRIP.sub(lambda m: " " * len(m.group(0)), text)
        for match in _NUMBER.finditer(masked):
            raw = match.group(1)
            unit = match.group(2) or ""
            # 归一：去掉小数尾巴
            norm = raw.rstrip("0").rstrip(".") if "." in raw else raw
            if not norm or norm in allowed or raw in allowed or norm in allowed_norm:
                continue
            # 序数语境（“共 3 项”“三项任务”）—— 不是数据断言
            context = masked[max(0, match.start() - 6):match.start()]
            if _NUMBER_BENIGN_CONTEXT.search(context):
                continue
            # 区间里的数字（“从 1.0 到 999”“[1.0, 999)”）—— level_scale 的边界，不是数据
            if "到" in masked[match.end():match.end() + 4]:
                continue
            # ⚠️ **分数数字**不算「编造数字」—— 它归 score_leak 管。
            #    否则同一处会既算「编造」又算「泄漏」，虚增两类计数。
            if _SCORE_TERMS.search(masked[max(0, match.start() - 12):match.start()]):
                continue
            issues["fabricated_numbers"].append(f"{raw}{unit}")

    # --- 4. 非法引用 ID ---
    #
    # ⚠️ 只有在**我们提供了合法 ID 集合**时才判非法。
    #    否则（legal_refs 为空）模型引的每个 id 都会被误判。
    if legal_refs:
        legal_set = set(legal_refs)
        for text in model_texts:
            # ⚠️ 必须先剔除 **UUID 之外的长数字串** ——
            #    `attendance:2026-11-14 07:52:40.388234` 这类
            #    「日期 时间戳」会命中 UUID 正则（十六进制段够长）→ 误报。
            scrubbed = re.sub(
                r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?", " ", text
            )
            # ⚠️ 考勤**没有证据 ID** —— 模型会自己编一个占位符
            #    （`attendance_anomalies`、`attendance-2026-11-14`、裸的 `attendance`）。
            #    这不是「非法引用证据」，而是「引用了考勤但没给 ID」——
            #    单独归类，不混进 illegal_references（那是证据 ID 的问题）。
            #
            # ⚠️ 要能匹配**裸** `attendance` —— 第三轮压测里模型写的是
            #    【attendance】，前面的 `[_\-]` 要求分隔符 → 漏掉了。
            # ⚠️ 还要能匹配**带冒号**的：「[attendance:1]」
            #    —— 模型会给考勤编个序号。
            for placeholder in re.findall(
                r"\battendance(?:[_\-:][A-Za-z0-9_\-:]+)?\b", scrubbed
            ):
                issues["attendance_placeholder"].append(placeholder)
            scrubbed = re.sub(
                r"\battendance(?:[_\-:][A-Za-z0-9_\-:]+)?\b", " ", scrubbed
            )
            for ref in re.findall(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                scrubbed,
            ):
                if ref not in legal_set:
                    issues["illegal_references"].append(ref)

    # --- 5. 分数泄漏（红线） ---
    #
    # ⚠️ 「个人绩效分：95」会命中两处：
    #    ① `_SCORE_TERMS` → "绩效分"
    #    ② `绩效分[：:]\s*\d+` → "绩效分：95"
    #    ②是①的**超集** → 同一处被算两次，虚增计数。
    #    所以：**先收集全部匹配，再按包含关系去重**（保留最长的那个）。
    score_hits: list[str] = []
    for text in model_texts:
        # ⚠️ 模型**合规声明**里也会出现「总分/分数」——
        #    「本总结仅基于给定证据做事实性归纳，不涉及分数计算与总分复述」
        #    这是**正确的免责表述**，不是泄漏。
        #    所以先把免责措辞整句剔除，再判定。
        cleaned = _SCORE_DISCLAIMER.sub("", text)
        for match in _SCORE_TERMS.finditer(cleaned):
            score_hits.append(match.group(0))
        # 证据 impact 里本身有「个人绩效分：95」—— 模型若**复述**它也是红线
        for match in re.finditer(r"绩效分[：:]\s*\d+", cleaned):
            score_hits.append(match.group(0))
    for hit in score_hits:
        # 已被某个更长的匹配覆盖 → 跳过
        if any(hit != other and hit in other for other in score_hits):
            continue
        if hit not in issues["score_leak"]:
            issues["score_leak"].append(hit)

    # --- 6. 考勤被当扣分 ---
    if attendance_present:
        for text in model_texts:
            # ⚠️⚠️ **必须先剔除免责措辞**，而且要**整句**剔除。
            #
            #    实测踩过的坑：模型写「考勤仅作参考，不涉及自动扣分」——
            #    如果只把「仅作参考」「不自动扣分」这几个词抠掉，
            #    剩下的「考勤，不涉及自动扣分」**又**命中了扣分正则 →
            #    把**合规表述**判成违规（每轮误报 1 例）。
            #
            #    正确做法：**整句**删掉含免责词的句子，
            #    再在剩下的句子里找扣分表述。
            for sentence in re.split(r"[。；\n]", text):
                if _ATTENDANCE_DISCLAIMER.search(sentence):
                    continue  # 整句合规，跳过
                for match in _ATTENDANCE_PENALTY.finditer(sentence):
                    issues["attendance_penalized"].append(match.group(0))

    return issues


# ---------------------------------------------------------------------------
# 调用真模型
# ---------------------------------------------------------------------------


def post(path: str, payload: dict, timeout: int = 180) -> dict:
    request = urllib.request.Request(
        f"{AGENT_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SECRET}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def build_draft_payload(case: dict) -> dict:
    """把用例转成 draft 接口的请求体。

    ⚠️ 指标口径**由我们按业务规则算好**再给模型 —— 模型不自己算比例。
    """
    metrics = []
    for m in case["metrics"]:
        value = float(m.get("value") or 0)
        target = float(m.get("target") or 0)
        ratio = (value / target) if target else None
        ratio_level = None
        if ratio is not None:
            if ratio >= 1.0:
                ratio_level = "A"
            elif ratio >= 0.8:
                ratio_level = "B"
            else:
                ratio_level = "C"
        metrics.append({
            "code": m["code"],
            "dimension": m["code"],
            "value": value,
            "target": target,
            "unit": m.get("unit", ""),
            "ratio": ratio,
            "ratio_level": ratio_level,
            "level_scale": [
                {"level": "C", "min": 0, "max": 0.8},
                {"level": "B", "min": 0.8, "max": 1.0},
                {"level": "A", "min": 1.0, "max": 999},
            ],
            "source": m.get("source", ""),
            "as_of": m.get("as_of", ""),
            "evidence_ids": [e["id"] for e in case["evidence"]],
        })

    return {
        "task_id": case["task_id"],
        "employee": {
            "id": case["employee_id"],
            "name": case.get("employee_name", ""),
        },
        "period": "202611",
        "metrics": metrics,
        "evidence": [
            {
                "id": e["id"],
                "kind": e["kind"],
                "summary": e["summary"],
                "impact": e.get("impact", ""),
                "source": e.get("source", ""),
                "as_of": e.get("as_of", ""),
                "usage": e.get("usage", "scoring"),
                "visibility": e.get("visibility", "share_with_manager"),
            }
            for e in case["evidence"]
        ],
        "attendance_anomalies": [
            {
                "type": a["kind"],
                "summary": a["summary"],
                "as_of": a.get("as_of", ""),
            }
            for a in case.get("attendance", [])
        ],
        "rubric": [{"dimension": m["code"], "scale": ["C", "B", "A"]} for m in case["metrics"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="限制用例数（0=全部）")
    parser.add_argument("--out", default=str(REPORT))
    args = parser.parse_args()

    if not SECRET:
        print("[FAIL] 未找到 AGENT_SHARED_SECRET")
        return 1
    if not CASES.exists():
        print(f"[FAIL] 用例文件不存在：{CASES}")
        print("      先跑：python backend/scripts/extract_pressure_cases.py")
        return 1

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[: args.limit]

    print("=" * 78)
    print("真模型幻觉引文压测（P3-2）")
    print(f"用例数：{len(cases)}   模型：{os.getenv('AGENT_MODEL_NAME', '?')}")
    print("=" * 78)

    results = []
    totals: Counter = Counter()
    started = time.time()

    for index, case in enumerate(cases, 1):
        payload = build_draft_payload(case)
        label = case["record_id"][-12:]
        try:
            resp = post("/v1/agent/draft", payload)
        except urllib.error.HTTPError as exc:
            # ⚠️ HTTP 错误要带上**响应体** —— 401 时只有 "Unauthorized"
            #    一行字，看不出是密钥不对还是路由不对。
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # pragma: no cover
                detail = ""
            print(f"[{index:2}/{len(cases)}] {label}  ❌ 请求失败：HTTP {exc.code} {detail}")
            results.append({
                "record_id": case["record_id"],
                "error": f"HTTP {exc.code}: {detail}",
            })
            totals["request_failed"] += 1
            continue
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[{index:2}/{len(cases)}] {label}  ❌ 请求失败：{exc}")
            results.append({
                "record_id": case["record_id"],
                "error": str(exc),
            })
            totals["request_failed"] += 1
            continue

        ratings = resp.get("ratings") or []
        comment = resp.get("comment_draft") or ""
        model_texts = [
            r.get("reason", "") for r in ratings if isinstance(r, dict)
        ] + [comment]

        legal_refs = [e["id"] for e in case["evidence"]]
        issues = detect_hallucination(
            model_texts,
            evidence_summaries=[e["summary"] for e in case["evidence"]],
            # ⚠️ impact 也是模型能看到的原文 —— 漏了会把「如实引用审批意见」误判成编造
            evidence_impacts=[e.get("impact", "") for e in case["evidence"]],
            metric_values=case["metrics"],
            legal_refs=legal_refs,
            attendance_present=bool(case.get("attendance")),
            attendance_summaries=[a["summary"] for a in case.get("attendance", [])],
        )

        # 模型声明的引用 ID 是否都合法
        declared = set(resp.get("citations") or [])
        illegal_declared = declared - set(legal_refs)

        count = sum(len(v) for v in issues.values())
        for key, values in issues.items():
            if values:
                totals[key] += 1
                totals[f"{key}_count"] += len(values)
        if illegal_declared:
            totals["illegal_references"] += 1
            totals["illegal_references_count"] += len(illegal_declared)
            issues["illegal_references"].extend(sorted(illegal_declared))

        if count == 0:
            totals["clean"] += 1
            flag = "✅"
        else:
            flag = "⚠️ "

        detail = " ".join(
            f"{k.replace('fabricated_', '').replace('_', '')}:{len(v)}"
            for k, v in issues.items() if v
        )
        print(
            f"[{index:2}/{len(cases)}] {label}  {flag}"
            f" degraded={str(resp.get('degraded')):5}"
            f" grounded={str(resp.get('fairness', {}).get('grounded')):5}"
            f"  {detail}"
        )

        results.append({
            "record_id": case["record_id"],
            "employee_id": case["employee_id"],
            "degraded": resp.get("degraded"),
            "agent_status": resp.get("agent_status"),
            "grounding": resp.get("fairness"),
            "citations": sorted(declared),
            "issues": {k: v for k, v in issues.items() if v},
            "model_ratings": ratings,
            "model_comment": comment,
        })

    elapsed = time.time() - started

    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("压测汇总")
    print("=" * 78)

    # ⚠️⚠️ **请求失败会让「检出率」完全失真** ——
    #    失败的用例不会被检查，也就不会计入「检出问题」，
    #    于是「干净占比 100%」—— 但那只是因为什么都没跑。
    #    第一版压测就栽在这：22 个用例全 401，报告却显示「零幻觉 100%」。
    #    所以这里**先判失败**，失败即中止，不给结论。
    failed = totals["request_failed"]
    if failed:
        print(f"❌ 请求失败 {failed} 例 —— 压测无效，不给结论。")
        print(f"   用例总数：{len(cases)}，成功：{len(cases) - failed}")
        first_error = next(
            (r.get("error") for r in results if r.get("error")), "未知错误"
        )
        print(f"   首个错误：{first_error}")
        print("\n   排查顺序：")
        print("   1. agent-service 是否在跑？curl http://127.0.0.1:8800/health")
        print("   2. AGENT_SHARED_SECRET 是否与 agent-service/.env 一致？")
        print("      ⚠️ 若 shell 里残留了旧的 AGENT_SHARED_SECRET，")
        print("         .env 不会覆盖它（env 优先级更高）→ 必然 401。")
        print("      解决：Remove-Item Env:AGENT_SHARED_SECRET 后重启服务。")

    clean = totals["clean"]
    print(f"用例总数        ：{len(cases)}")
    print(f"干净（零幻觉）  ：{clean}  ({clean / max(1, len(cases)) * 100:.1f}%)")
    print(f"检出问题        ：{len(cases) - clean}")
    print(f"请求失败        ：{failed}")
    print(f"耗时            ：{elapsed:.1f}s  （平均 {elapsed / max(1, len(cases)):.1f}s/例）")
    print("\n--- 各类问题检出率 ---")
    for key in (
        "fabricated_quotes",
        "fabricated_facts",
        "fabricated_numbers",
        "illegal_references",
        "score_leak",
        "attendance_penalized",
        "attendance_placeholder",
    ):
        n = totals[key]
        n_cases = len(cases)
        pct = n / n_cases * 100
        print(f"  {key:24} {n:3} 例  ({pct:5.1f}%)   共 {totals[f'{key}_count']} 处")

    # ⚠️ 红线：分数泄漏
    print("\n--- 红线检查 ---")
    if totals["score_leak"]:
        print(f"  ❌ 分数泄漏：{totals['score_leak']} 例 / "
              f"{totals['score_leak_count']} 处  ← 必须修")
    else:
        print("  ✅ 无分数泄漏")
    if totals["attendance_penalized"]:
        print(f"  ❌ 考勤被当扣分：{totals['attendance_penalized']} 例  ← 必须修")
    else:
        print("  ✅ 考勤未被当扣分")

    # ⚠️ 幻觉红线：编造引文/事实/数字 —— 这三类是「欺骗主管」的性质问题
    hallucination_total = (
        totals["fabricated_quotes"]
        + totals["fabricated_facts"]
        + totals["fabricated_numbers"]
    )
    print("\n--- 幻觉红线 ---")
    if hallucination_total:
        print(f"  ❌ 编造类幻觉：{hallucination_total} 例  ← 必须修")
    else:
        print("  ✅ 未检出编造引文 / 编造事实 / 编造数字")
    if totals["illegal_references"]:
        print(f"  ❌ 非法证据引用：{totals['illegal_references']} 例  ← 必须修")
    else:
        print("  ✅ 未检出非法证据引用")
    if totals["attendance_placeholder"]:
        print(f"  ⚠️  考勤占位符：{totals['attendance_placeholder']} 例"
              f"（模型引用了考勤但没给 ID —— 属体验问题，非幻觉）")

    report = {
        "model": os.getenv("AGENT_MODEL_NAME", "?"),
        "case_count": len(cases),
        "elapsed_seconds": round(elapsed, 1),
        "totals": dict(totals),
        "results": results,
        # ⚠️ 明确记录「本次压测是否有效」——
        #    避免下游脚本把「全失败」的报告当成「全通过」。
        "valid": failed == 0,
    }
    Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完整明细 → {args.out}")

    # ⚠️ 有请求失败 → 退出码非 0 —— 让 CI/脚本能发现「压测没真跑」
    if failed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
