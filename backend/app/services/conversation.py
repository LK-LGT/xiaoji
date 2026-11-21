"""P0-5：问答的多轮会话上下文。

⚠️ **这个模块解决什么问题**
--------------------------
用户问「上个月我多少分」，下一句问「那扣分原因呢」——
第二句里**没有**月份、**没有**主体（「我」是默认的）。
没有上下文，「那扣分原因呢」就不知道该查哪个月。

⇒ 本模块把**上一轮的槽位**（周期、主体、话题）记下来，
   下一轮**继承**它 —— 显式说的优先，没说的继承。

⚠️ **三条不可让步的规则**
--------------------------
1. **继承 ≠ 授权**
   继承来的主体**仍要过** ``authorize_qa_subject``。
   ⇒ 会话状态**不能**成为越权通道。
   （用户上午能查张三，下午张三调岗/离职了，就要查不到了。）

2. **摘要必须丢弃原话**
   摘要会进入后续轮次的上下文。若保留原话，
   用户某一轮输入的**指令文本**会被后续轮次当成"上下文指令"执行。
   ⇒ 摘要只保留**结构化槽位 + 结论**，不含任何可执行文本。

3. **空闲 ⇒ 周期与主体一并清空**
   否则用户上午问「张三的分数」，下午问「扣分原因」，
   会拿到张三的数据 —— 语义上答非所问，且用户没预期。

⚠️ **为什么用 JSON 存槽位而不是多列**
------------------------------------
槽位结构会演进（新增 topic、聚焦指标等）。
JSON 免去频繁 DDL —— 加个槽位不用改表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .period import normalize_period

#: ⚠️ **空闲清空阈值**（秒）—— 超过这么久没有交互就清掉周期与主体。
#:
#: 默认 1800 秒（30 分钟）。这是**对话态**的空闲阈值，
#: 与 ``BotSession.expires_at``（会话 TTL，默认 120 分钟）是**两回事**：
#:   - TTL（120 分钟）：整个机器人会话还能不能接着聊
#:   - 空闲（30 分钟）：上下文里的"主体/周期"还算不算数
#:
#: ⚠️ 为什么主体要和周期一起清：
#:    只清周期不清主体 ⇒ 「上个月的扣分原因」变成「**这个月**的扣分原因」（错）
#:    只清主体不清周期 ⇒ 「上个月」保持，但主体回到自己（更安全但语义不一致）
#:    ⇒ 一起清，最符合直觉。
IDLE_RESET_SECONDS = 30 * 60

#: 对话话题分类 —— 决定下一轮**默认**用哪个工具。
#:
#: ⚠️ 这些只是**默认值**，显式指定优先。
TOPICS: tuple[str, ...] = (
    "score",      # 多少分
    "breakdown",  # 怎么算的
    "deduction",  # 为什么扣分 / 少了多少
    "evidence",   # 证据 / 材料
    "rule",       # 规则 / 权重怎么定的
    "peer",       # 问同事
)

#: ⚠️ 话题关键词 —— 顺序即优先级（先匹配到的赢）。
#:
#: ⚠️ "peer" 放在最后 —— 「同事」这个词太泛，
#:    若放在前面，「我同事的规则是什么」会被判成 peer 而不是 rule。
TOPIC_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("breakdown", ("怎么算", "怎么得出", "构成", "明细", "拆解", "算式", "权重多少", "每项")),
    ("deduction", ("扣分", "为什么少", "扣了", "少了", "丢了", "被扣", "少了几", "怎么降")),
    ("evidence", ("证据", "材料", "依据", "附件", "支撑", "凭证")),
    ("rule", ("规则", "方案", "标准", "怎么定", "权重怎么", "评分标准", "评级标准")),
    ("peer", ("同事", "同部门", "他们", "他", "她", "大家")),
    ("score", ("多少分", "得分", "分数", "考得", "成绩", "评价", "评级", "等级")),
)

#: 周期关键词 —— 「上个月」「这个月」这类相对表述。
#:
#: ⚠️ 相对表述依赖**当前时间** —— 凌晨 0 点问「上个月」与月末问「上个月」
#:    指向同一个月，但实现上要按提问时刻计算，不能写死。
_RELATIVE_PERIOD_PATTERNS: tuple[tuple[str, int], ...] = (
    ("上个月", -1),
    ("上月", -1),
    ("这个月", 0),
    ("本月", 0),
    ("当月", 0),
    ("上个月份", -1),
)

#: 绝对周期正则 —— ``2026-11`` / ``2026年11月`` / ``202611``
_ABSOLUTE_PERIOD_RE = re.compile(r"(20\d{2})\s*[-/年.]?\s*(\d{1,2})\s*月?")

#: 裸月份正则 —— ``11月``（不带年份）。
#:
#: ⚠️ 必须能匹配「2026年11月」里的"11月"（以便在绝对周期失败时兜底），
#:    但**匹配顺序**保证绝对周期优先。
_BARE_MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月")

#: ⚠️ **相对月份**的正则（"3个月前"）—— 只支持近 24 个月。
_N_MONTHS_AGO_RE = re.compile(r"(\d{1,2})\s*个?月前")


def _safe_int(value: Any, default: int = 0) -> int:
    """容错地转 int —— 坏数据不该让整个问答挂掉。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """容错地转 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ContextBudget:
    """上下文预算 —— 防膨胀。

    ⚠️ 这是一道**独立**的闸门，与 ``ToolUsingAgent`` 已有的
    ``max_message_chars`` / ``max_steps`` 并列 ——
    那些限制的是**单轮**，这里限制的是**整个会话累积**。
    """

    max_total_tokens: int = 8000
    max_history_turns: int = 4
    max_summary_tokens: int = 400


@dataclass(frozen=True)
class Turn:
    """一轮对话的**结构化**记录。

    ⚠️ **不存用户原话** —— 只存槽位与结论。
    原话一旦进入后续上下��，就可能被当成"上下文指令"执行。
    """

    #: 本轮的话题
    topic: str = ""
    #: 本轮解析出的周期
    period: str = ""
    #: 本轮的目标人
    subject_id: str = ""
    #: 本轮目标人姓名（用于回答里显名）
    subject_name: str = ""
    #: 本轮的回答结论（截断后）
    conclusion: str = ""
    #: 本轮是否命中越权
    denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "period": self.period,
            "subject_id": self.subject_id,
            "subject_name": self.subject_name,
            "conclusion": self.conclusion[:200],
            "denied": self.denied,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Turn:
        return cls(
            topic=str(raw.get("topic") or ""),
            period=str(raw.get("period") or ""),
            subject_id=str(raw.get("subject_id") or ""),
            subject_name=str(raw.get("subject_name") or ""),
            conclusion=str(raw.get("conclusion") or "")[:200],
            denied=bool(raw.get("denied")),
        )


@dataclass
class ConversationState:
    """问答会话的上下文状态。"""

    session_id: str = ""
    asker_id: str = ""
    #: 当前周期（紧凑格式 YYYYMM）
    period: str | None = None
    #: 当前目标人
    subject_id: str | None = None
    #: 目标人姓名 —— 跨主体继承时要在回答里显名
    subject_name: str = ""
    #: 目标人类型：self / peer / report / ...
    subject_type: str = "self"
    #: 当前话题
    topic: str | None = None
    #: 当前任务
    task_id: str | None = None
    #: 最近几轮（结构化）
    recent_turns: list[Turn] = field(default_factory=list)
    #: 累计轮次
    turn_count: int = 0
    #: 最后一次交互时间（epoch 秒）—— 用于空闲清空
    last_active_at: float = 0.0
    #: 本轮是否**继承了**周期（回答里要提示"沿用上一轮"）
    inherited_period: bool = False
    #: 本轮是否**继承了**主体
    inherited_subject: bool = False

    # ── 持久化 ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """转成 JSON 可序列化的结构（存进 ``BotSession.qa_state``）。"""
        return {
            "session_id": self.session_id,
            "asker_id": self.asker_id,
            "period": self.period,
            "subject_id": self.subject_id,
            "subject_name": self.subject_name,
            "subject_type": self.subject_type,
            "topic": self.topic,
            "task_id": self.task_id,
            "recent_turns": [t.to_dict() for t in self.recent_turns],
            "turn_count": self.turn_count,
            "last_active_at": self.last_active_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ConversationState:
        """从 JSON 还原。⚠️ **容错** —— 坏数据不该让整个问答挂掉。"""
        if not isinstance(raw, dict):
            return cls()
        turns_raw = raw.get("recent_turns")
        turns: list[Turn] = []
        if isinstance(turns_raw, list):
            for item in turns_raw:
                if isinstance(item, dict):
                    try:
                        turns.append(Turn.from_dict(item))
                    except Exception:
                        continue
        return cls(
            session_id=str(raw.get("session_id") or ""),
            asker_id=str(raw.get("asker_id") or ""),
            period=raw.get("period") or None,
            subject_id=raw.get("subject_id") or None,
            subject_name=str(raw.get("subject_name") or ""),
            subject_type=str(raw.get("subject_type") or "self"),
            topic=raw.get("topic") or None,
            task_id=raw.get("task_id") or None,
            recent_turns=turns,
            # ⚠️ 容错 —— 数字字段用 try 包住。
            #    坏数据不该让整个问答挂掉（机器人链路里这会表现为"机器人不回话"）。
            turn_count=_safe_int(raw.get("turn_count")),
            last_active_at=_safe_float(raw.get("last_active_at")),
        )

    # ── 行为 ────────────────────────────────────────────────

    def resolve_slots(
        self,
        question: str,
        *,
        now: datetime | None = None,
        explicit_period: str | None = None,
        explicit_subject_id: str | None = None,
    ) -> ResolvedSlots:
        """合并本轮槽位（显式 > 文本解析 > 继承）。

        ⚠️ 这是 ``resolve_slots`` 的**方法版** ——
           把自己作为"上一轮状态"传进去，调用方少写一个参数。
        """
        return resolve_slots(
            question,
            self,
            now=now,
            explicit_period=explicit_period,
            explicit_subject_id=explicit_subject_id,
        )


@dataclass(frozen=True)
class ResolvedSlots:
    """槽位解析结果。"""

    period: str | None = None
    subject_id: str | None = None
    topic: str | None = None
    #: 周期是否来自继承
    period_inherited: bool = False
    #: 主体是否来自继承
    subject_inherited: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# 槽位解析
# ═══════════════════════════════════════════════════════════════════════════


def parse_period(text: str, now: datetime | None = None) -> str | None:
    """从自然语言里解析周期（紧凑格式 YYYYMM）。

    支持：
    - 绝对：「2026-11」「2026年11月」「202611」
    - 相对：「上个月」「本月」「3个月前」

    ⚠️ 相对周期依赖**提问时刻** —— 必须传入 ``now``，
    不要用固定的"当前月"，否则跨月时答案会漂。

    Args:
        text: 用户原话。
        now: 提问时刻（默认取当前时间）。

    Returns:
        紧凑周期 ``YYYYMM``；解析不到返回 ``None``。
    """
    if not text:
        return None
    now = now or datetime.now()

    # ① 绝对周期 —— 优先，因为它最明确
    match = _ABSOLUTE_PERIOD_RE.search(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return normalize_period(f"{year:04d}{month:02d}")

    # ② "N 个月前"
    match = _N_MONTHS_AGO_RE.search(text)
    if match:
        months = int(match.group(1))
        # ⚠️ 限制在近 24 个月 —— 更远的月份用户基本不会问，
        #    且限制了正则被构造数字攻击的可能。
        if 1 <= months <= 24:
            total = now.year * 12 + (now.month - 1) - months
            year, month = divmod(total, 12)
            return normalize_period(f"{year:04d}{month + 1:02d}")

    # ②' 裸月份（「11月」不带年份）—— 用**当前年**。
    #
    # ⚠️ 必须放在"绝对周期"之后 ——
    #    「2026年11月」里也有"11月"，先匹配裸月份会丢掉年份。
    match = _BARE_MONTH_RE.search(text)
    if match:
        month = int(match.group(1))
        if 1 <= month <= 12:
            return normalize_period(f"{now.year:04d}{month:02d}")

    # ③ 相对表述（"上个月"/"本月"）
    for keyword, offset in _RELATIVE_PERIOD_PATTERNS:
        if keyword in text:
            total = now.year * 12 + (now.month - 1) + offset
            year, month = divmod(total, 12)
            return normalize_period(f"{year:04d}{month + 1:02d}")

    return None


def detect_topic(text: str) -> str | None:
    """识别问题的话题。

    ⚠️ 返回 ``None`` 表示"没识别出来" ——
    调用方可以继承上一轮的话题，或用默认话题。
    """
    if not text:
        return None
    for topic, keywords in TOPIC_PATTERNS:
        if any(keyword in text for keyword in keywords):
            return topic
    return None


def resolve_slots(
    question: str,
    state: ConversationState | None,
    *,
    now: datetime | None = None,
    explicit_period: str | None = None,
    explicit_subject_id: str | None = None,
) -> ResolvedSlots:
    """合并槽位 —— **显式 > 继承**。

    ⚠️ **优先级**（不可调换）：
    ① 显式传入的 ``explicit_period`` / ``explicit_subject_id``（接口参数）
    ② 从**本轮问题文本**里解析出来的
    ③ 从**上一轮状态**继承的

    ⚠️ 继承来的主体**只解决"查谁"**，不解决"能不能查" ——
       授权判定在 ``qa_authorization.authorize_qa_subject`` 里做。

    Args:
        question: 本轮问题。
        state: 上一轮状态（可为 None）。
        now: 提问时刻。
        explicit_period: 接口显式指定的周期。
        explicit_subject_id: 接口显式指定的目标人。

    Returns:
        ``ResolvedSlots`` —— 解析后的周期 / 主体 / 话题及继承标记。
    """
    # ── 周期 ────────────────────────────────────────────────
    period: str | None = None
    period_inherited = False
    if explicit_period:
        period = normalize_period(explicit_period)
    if period is None:
        period = parse_period(question, now)
    if period is None and state is not None:
        period = state.period
        period_inherited = period is not None

    # ── 主体 ────────────────────────────────────────────────
    # ⚠️ 本轮文本里**不解析主体** —— 主体来自接口参数（经授权判定）
    #    或上一轮继承。文本抽名是 P0-8 的事（需要候选集收窄）。
    subject_id: str | None = None
    subject_inherited = False
    if explicit_subject_id:
        subject_id = explicit_subject_id
    elif state is not None:
        subject_id = state.subject_id
        subject_inherited = subject_id is not None

    # ── 话题 ────────────────────────────────────────────────
    topic = detect_topic(question)
    if topic is None and state is not None:
        topic = state.topic

    return ResolvedSlots(
        period=period,
        subject_id=subject_id,
        topic=topic,
        period_inherited=period_inherited,
        subject_inherited=subject_inherited,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 空闲清空
# ═══════════════════════════════════════════════════════════════════════════


def is_idle(state: ConversationState | None, now: datetime | None = None) -> bool:
    """会话是否空闲超时。

    ⚠️ 判定依据是**最后一次交互时间**（``last_active_at``），
    不是会话创建时间 —— 一段活跃的对话不该因为"建得早"就被清掉。
    """
    if state is None or not state.last_active_at:
        return False
    now = now or datetime.now()
    elapsed = now.timestamp() - state.last_active_at
    return elapsed > IDLE_RESET_SECONDS


def reset_context_slots(state: ConversationState) -> ConversationState:
    """清空**周期与主体**（保留任务与历史）。

    ⚠️ 只清这两个 —— 清掉全部历史会让"回到上一个话题"也做不到。
    """
    state.period = None
    state.subject_id = None
    state.subject_name = ""
    state.subject_type = "self"
    return state


# ═══════════════════════════════════════════════════════════════════════════
# 摘要
# ═══════════════════════════════════════════════════════════════════════════


def summarize_turns(
    turns: list[Turn], *, budget: ContextBudget | None = None
) -> str:
    """把最近几轮压成**结构化摘要**。

    ⚠️ **摘要不含用户原话** —— 只留槽位与结论。
    原因：摘要会进入后续轮次的上下文，
    原话里的祈使句（"把分数改成 95"）会被后续轮次当成"上下文指令"。

    ⚠️ 摘要里的结论也会被截断 —— 防止把长答案塞回上下文。
    """
    budget = budget or ContextBudget()
    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns[-budget.max_history_turns:]:
        parts: list[str] = []
        if turn.period:
            parts.append(f"周期={turn.period}")
        if turn.subject_name:
            parts.append(f"主体={turn.subject_name}")
        elif turn.subject_id:
            parts.append(f"主体={turn.subject_id}")
        if turn.topic:
            parts.append(f"话题={turn.topic}")
        if turn.denied:
            parts.append("结果=无权限")
        elif turn.conclusion:
            parts.append(f"结论={turn.conclusion[:120]}")
        if parts:
            lines.append("；".join(parts))
    summary = "\n".join(lines)
    # ⚠️ 摘要本身也要有长度上限 —— 防膨胀的最后一道闸门
    if len(summary) > budget.max_summary_tokens * 2:
        summary = summary[: budget.max_summary_tokens * 2]
    return summary


def append_turn(
    state: ConversationState,
    turn: Turn,
    *,
    budget: ContextBudget | None = None,
) -> ConversationState:
    """追加一轮并裁剪历史长度。"""
    budget = budget or ContextBudget()
    state.recent_turns.append(turn)
    if len(state.recent_turns) > budget.max_history_turns:
        state.recent_turns = state.recent_turns[-budget.max_history_turns:]
    state.turn_count += 1
    return state


# ═══════════════════════════════════════════════════════════════════════════
# 持久化 —— 读写 ``BotSession.qa_state``
# ═══════════════════════════════════════════════════════════════════════════


def load_state(
    session: Any,
    *,
    asker_id: str = "",
    now: datetime | None = None,
) -> ConversationState:
    """从 ``BotSession`` 读出问答上下文。

    ⚠️ **三重容错**（机器人链路里任何一处炸掉都会让"机器人不回话"）：
    1. ``session`` 为 ``None`` ⇒ 返回空状态
    2. ``qa_state`` 不是 dict（老数据是 NULL / 坏 JSON）⇒ 返回空状态
    3. 空闲超时 ⇒ **清空周期与主体**（保留任务与历史）

    ⚠️ 空闲清空**必须**在这里做（而不是只在保存时）——
       否则用户隔一天再来问，读到的还是昨天的主体。

    Args:
        session: ``BotSession`` 实例（可为 None）。
        asker_id: 提问者 id —— 若与状态里的不一致（换了人用同一会话）
            ⇒ 也要清空（一个会话绑一个员工，理论上不会发生，
            但脏数据/测试库里出现过）。
        now: 当前时刻。
    """
    if session is None:
        return ConversationState(asker_id=asker_id)

    raw = getattr(session, "qa_state", None)
    state = ConversationState.from_dict(raw if isinstance(raw, dict) else None)

    # ⚠️ asker 变了 ⇒ 上下文作废 —— 不能让 A 的上下文被 B 继承
    if asker_id and state.asker_id and state.asker_id != asker_id:
        return ConversationState(
            session_id=str(getattr(session, "id", "") or ""),
            asker_id=asker_id,
        )

    if not state.asker_id:
        state.asker_id = asker_id
    if not state.session_id:
        state.session_id = str(getattr(session, "id", "") or "")

    # ⚠️ 空闲 ⇒ 周期与主体一并清空（话题保留 —— 换个主体继续问同类问题是常见的）
    if is_idle(state, now):
        reset_context_slots(state)
        # ⚠️ 历史也清掉 —— 否则摘要里还带着上一个主体的名字
        state.recent_turns = []
        state.topic = None

    return state


def save_state(
    session: Any,
    state: ConversationState,
    *,
    task_id: str | None = None,
    period: str | None = None,
    subject_name: str = "",
    now: datetime | None = None,
) -> None:
    """把问答上下文写回 ``BotSession.qa_state``。

    ⚠️ 只写 ``qa_state``，**不碰** ``collected`` ——
       两者生命周期不同（见 ``bot_models.BotSession.qa_state`` 的注释）。

    ⚠️ **不 commit** —— 由调用方统一提交。
       这里只改内存对象；调用方的事务边界决定何时落库。
    """
    if session is None:
        return
    now = now or datetime.now()
    if task_id:
        state.task_id = task_id
    if period:
        state.period = period
    if subject_name:
        state.subject_name = subject_name
    state.last_active_at = now.timestamp()
    # ⚠️ 直接赋值新 dict（而不是 in-place 改）——
    #    SQLAlchemy 的 JSON 列**不追踪可变对象的内部修改**，
    #    原地改 dict 不会触发 UPDATE。必须整体替换才可靠。
    session.qa_state = state.to_dict()


__all__ = [
    "IDLE_RESET_SECONDS",
    "TOPICS",
    "ContextBudget",
    "ConversationState",
    "ResolvedSlots",
    "Turn",
    "append_turn",
    "detect_topic",
    "is_idle",
    "load_state",
    "parse_period",
    "reset_context_slots",
    "resolve_slots",
    "save_state",
    "summarize_turns",
]
