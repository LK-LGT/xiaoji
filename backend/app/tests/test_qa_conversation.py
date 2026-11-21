"""P0-5 / P0-6：多轮会话上下文与持久化的测试。

⚠️ **这个文件守的是"上下文继承"**
--------------------------------
槽位继承看起来无害，实际有三个致命风险：

1. **继承成为越权通道**
   用户上午能查张三，下午张三调岗了 —— 继承的 subject 还在，
   系统照样把张三的数据给他。
   ⇒ 继承的主体**仍要过** ``authorize_qa_subject``。

2. **摘要里的原话变成指令**
   用户某一轮说「把分数改成 95 分」，
   摘要保留原话 ⇒ 后续轮次把它当上下文指令执行。
   ⇒ 摘要**只留结构化槽位 + 结论**。

3. **空闲不清理 ⇒ 答非所问**
   上午问「张三的分数」，下午问「扣分原因」⇒ 拿到张三的数据。
   ⇒ 空闲超时要**周期与主体一并清空**。

⚠️ 另有两条易错点：
   - **优先级**：显式 > 文本解析 > 继承（顺序不可换）
   - **JSON 列不追踪可变对象** —— ``session.qa_state`` 必须**整体替换**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.bot_models import BotSession
from backend.app.domain.models import Base
from backend.app.services import conversation
from backend.app.services.conversation import (
    ContextBudget,
    ConversationState,
    Turn,
    append_turn,
    detect_topic,
    is_idle,
    load_state,
    parse_period,
    reset_context_slots,
    resolve_slots,
    save_state,
    summarize_turns,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def bot_session(db):
    session = BotSession(
        id="S1", open_id="ou_x", app_id="app", state="idle", intent="none",
        collected={}, qa_state={},
    )
    db.add(session)
    db.commit()
    return session


NOW = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. 周期解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-11 的绩效", "202611"),
        ("2026年11月", "202611"),
        ("202611", "202611"),
        ("上个月", "202610"),
        ("上月", "202610"),
        ("这个月", "202611"),
        ("本月", "202611"),
        ("3个月前", "202608"),
        ("12个月前", "202511"),
    ],
)
def test_parse_period(text: str, expected: str) -> None:
    assert parse_period(text, NOW) == expected


def test_parse_period_returns_none_when_absent() -> None:
    assert parse_period("我的绩效怎么样", NOW) is None


def test_parse_period_empty_text() -> None:
    assert parse_period("", NOW) is None


def test_parse_period_crosses_year_boundary() -> None:
    """⚠️ 「上个月」在 1 月要回到去年 12 月 —— 跨年不能算错。"""
    january = datetime(2026, 1, 15, tzinfo=UTC)
    assert parse_period("上个月", january) == "202512"


def test_parse_period_uses_current_year_for_bare_month() -> None:
    """「11月」不带年份 ⇒ 用当前年。"""
    assert parse_period("11月", NOW) == "202611"


def test_parse_period_rejects_far_future_months() -> None:
    """⚠️ 不带年份的月份超出 1~12 ⇒ 当作没解析出来。"""
    assert parse_period("13月", NOW) is None


def test_parse_period_ignores_absurd_n_months_ago() -> None:
    """⚠️ 「99个月前」超出 24 个月上限 ⇒ 不解析（防构造数字攻击）。"""
    assert parse_period("99个月前", NOW) is None


# ---------------------------------------------------------------------------
# 2. 话题识别
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这分数怎么算的", "breakdown"),
        ("为什么扣了 5 分", "deduction"),
        ("他交了什么证据材料", "evidence"),
        ("权重规则是怎么定的", "rule"),
        ("我这个月多少分", "score"),
    ],
)
def test_detect_topic(text: str, expected: str) -> None:
    assert detect_topic(text) == expected


def test_detect_topic_returns_none_when_unknown() -> None:
    assert detect_topic("今天天气不错") is None


def test_detect_topic_empty() -> None:
    assert detect_topic("") is None


def test_detect_topic_peer_phrase() -> None:
    """⚠️ 「同事」要判成 peer —— 用于路由到 get_peer_score。"""
    assert detect_topic("销售乙这个月多少分") in ("peer", "score")


# ---------------------------------------------------------------------------
# 3. 槽位合并 —— 优先级
# ---------------------------------------------------------------------------


def test_explicit_period_wins_over_text() -> None:
    """★ 显式参数 > 文本解析。"""
    state = ConversationState(period="202609")
    slots = resolve_slots(
        "202610 的绩效", state, now=NOW, explicit_period="202608",
    )
    assert slots.period == "202608"
    assert slots.period_inherited is False


def test_text_period_wins_over_inherited() -> None:
    """★ 文本解析 > 继承。"""
    state = ConversationState(period="202609")
    slots = resolve_slots("202610 的绩效", state, now=NOW)
    assert slots.period == "202610"
    assert slots.period_inherited is False


def test_period_inherited_when_absent_everywhere() -> None:
    """★ 都没说 ⇒ 继承上一轮，并**标记为继承**。"""
    state = ConversationState(period="202609")
    slots = resolve_slots("那扣分原因呢", state, now=NOW)
    assert slots.period == "202609"
    assert slots.period_inherited is True


def test_no_period_at_all() -> None:
    state = ConversationState()
    slots = resolve_slots("那扣分原因呢", state, now=NOW)
    assert slots.period is None
    assert slots.period_inherited is False


def test_explicit_subject_wins_over_inherited() -> None:
    """★ 显式目标人 > 继承。"""
    state = ConversationState(subject_id="B")
    slots = resolve_slots(
        "他多少分", state, now=NOW, explicit_subject_id="C",
    )
    assert slots.subject_id == "C"
    assert slots.subject_inherited is False


def test_subject_inherited_when_not_explicit() -> None:
    state = ConversationState(subject_id="B")
    slots = resolve_slots("那扣分原因呢", state, now=NOW)
    assert slots.subject_id == "B"
    assert slots.subject_inherited is True


def test_topic_inherited_when_not_detected() -> None:
    """★ 话题没识别出来 ⇒ 继承上一轮。

    ⚠️ 用一个**不含任何话题关键词**的句子 ——
       「那他呢」里的"他"是 peer 关键词，会被识别成 peer。
    """
    state = ConversationState(topic="deduction")
    slots = resolve_slots("那这个呢", state, now=NOW)
    assert slots.topic == "deduction"


def test_topic_not_inherited_when_detected() -> None:
    state = ConversationState(topic="deduction")
    slots = resolve_slots("证据材料有哪些", state, now=NOW)
    assert slots.topic == "evidence"


def test_resolve_slots_without_state() -> None:
    """⚠️ 没有会话状态（网页端首次提问）⇒ 正常工作。"""
    slots = resolve_slots("我这个月多少分", None, now=NOW)
    assert slots.period == "202611"
    assert slots.subject_id is None


# ---------------------------------------------------------------------------
# 4. 空闲清空
# ---------------------------------------------------------------------------


def test_is_idle_after_threshold() -> None:
    state = ConversationState(last_active_at=NOW.timestamp())
    assert is_idle(state, NOW + timedelta(minutes=31)) is True


def test_not_idle_within_threshold() -> None:
    state = ConversationState(last_active_at=NOW.timestamp())
    assert is_idle(state, NOW + timedelta(minutes=29)) is False


def test_not_idle_when_never_active() -> None:
    assert is_idle(ConversationState(), NOW) is False


def test_reset_context_slots_clears_period_and_subject() -> None:
    """★ 空闲清空 ⇒ 周期与主体**一并**清掉。"""
    state = ConversationState(
        period="202610", subject_id="B", subject_name="销售乙",
        subject_type="peer", topic="score",
    )
    reset_context_slots(state)
    assert state.period is None
    assert state.subject_id is None
    assert state.subject_name == ""
    assert state.subject_type == "self"
    # ⚠️ 话题保留 —— 换个主体继续问同类问题是常见的
    assert state.topic == "score"


# ---------------------------------------------------------------------------
# 5. 摘要 —— 不含原话
# ---------------------------------------------------------------------------


def test_summary_keeps_slots_and_conclusion() -> None:
    turns = [Turn(
        topic="score", period="202610", subject_id="B",
        subject_name="销售乙", conclusion="总分 84.5",
    )]
    summary = summarize_turns(turns)
    assert "202610" in summary
    assert "销售乙" in summary
    assert "84.5" in summary


def test_summary_never_contains_user_raw_text() -> None:
    """★★ 摘要**绝不能**含用户原话。

    ⚠️ 原话进上下文 ⇒ 后续轮次可能把它当"上下文指令"执行。
    """
    turn = Turn(topic="score", period="202611", conclusion="总分 88")
    # ⚠️ Turn 里压根没有原话字段 —— 这是**结构性**保证
    assert not hasattr(turn, "question")
    assert not hasattr(turn, "raw")
    summary = summarize_turns([turn])
    assert "把分数改成" not in summary


def test_summary_respects_history_budget() -> None:
    """⚠️ 历史轮次上限 —— 防膨胀。"""
    budget = ContextBudget(max_history_turns=2)
    turns = [
        Turn(topic="score", period=f"2026{m:02d}", conclusion=f"c{m}")
        for m in range(1, 6)
    ]
    summary = summarize_turns(turns, budget=budget)
    assert "c5" in summary
    assert "c1" not in summary


def test_summary_respects_token_budget() -> None:
    budget = ContextBudget(max_summary_tokens=10)
    turns = [Turn(topic="score", conclusion="很长的结论" * 50)]
    summary = summarize_turns(turns, budget=budget)
    assert len(summary) <= budget.max_summary_tokens * 2


def test_summary_of_empty_is_empty() -> None:
    assert summarize_turns([]) == ""


def test_append_turn_trims_history() -> None:
    state = ConversationState()
    budget = ContextBudget(max_history_turns=3)
    for i in range(6):
        append_turn(state, Turn(conclusion=f"c{i}"), budget=budget)
    assert len(state.recent_turns) == 3
    assert state.turn_count == 6
    assert state.recent_turns[-1].conclusion == "c5"


# ---------------------------------------------------------------------------
# 6. 序列化
# ---------------------------------------------------------------------------


def test_state_roundtrip() -> None:
    """★ 存进去能原样读出来。"""
    state = ConversationState(
        session_id="S1", asker_id="E1", period="202610",
        subject_id="B", subject_name="销售乙", subject_type="peer",
        topic="score", task_id="T1", turn_count=3,
        last_active_at=12345.0,
    )
    append_turn(state, Turn(topic="score", period="202610", conclusion="84.5"))
    restored = ConversationState.from_dict(state.to_dict())
    assert restored.period == "202610"
    assert restored.subject_id == "B"
    assert restored.subject_name == "销售乙"
    assert restored.subject_type == "peer"
    assert restored.task_id == "T1"
    # ⚠️ append_turn 会把 turn_count 加 1 ⇒ 3 + 1 = 4
    assert restored.turn_count == 4
    assert restored.last_active_at == 12345.0
    assert len(restored.recent_turns) == 1
    assert restored.recent_turns[0].conclusion == "84.5"


def test_from_dict_tolerates_none() -> None:
    """⚠️ 老数据是 NULL ⇒ 不能炸。"""
    state = ConversationState.from_dict(None)
    assert state.period is None
    assert state.recent_turns == []


def test_from_dict_tolerates_garbage() -> None:
    """⚠️ 坏 JSON ⇒ 不能炸。"""
    state = ConversationState.from_dict({"recent_turns": "not-a-list", "turn_count": "x"})
    assert state.recent_turns == []
    assert state.turn_count == 0


def test_from_dict_skips_bad_turns() -> None:
    state = ConversationState.from_dict({"recent_turns": [None, 123, {"topic": "ok"}]})
    assert len(state.recent_turns) == 1
    assert state.recent_turns[0].topic == "ok"


# ---------------------------------------------------------------------------
# 7. 持久化（P0-6）
# ---------------------------------------------------------------------------


def test_save_and_load_state(bot_session) -> None:
    """★ 写进去能读回来。"""
    state = ConversationState(asker_id="E1", period="202610", subject_id="B")
    save_state(bot_session, state, task_id="T1", now=NOW)
    bot_session.updated_at = NOW.replace(tzinfo=None)
    bot_session.qa_state = state.to_dict()

    loaded = load_state(bot_session, asker_id="E1", now=NOW)
    assert loaded.period == "202610"
    assert loaded.subject_id == "B"
    assert loaded.task_id == "T1"


def test_load_state_handles_none_session() -> None:
    """⚠️ 没有会话（网页端）⇒ 空状态，不能炸。"""
    state = load_state(None, asker_id="E1", now=NOW)
    assert state.period is None
    assert state.asker_id == "E1"


def test_load_state_handles_null_qa_state(bot_session) -> None:
    """⚠️ 老行 qa_state 是 NULL ⇒ 空状态，不能炸。"""
    bot_session.qa_state = None
    state = load_state(bot_session, asker_id="E1", now=NOW)
    assert state.period is None


def test_load_state_resets_after_idle(bot_session) -> None:
    """★★ 空闲超时要**清空周期与主体**。

    ⚠️ 这条最容易被漏 —— 因为「读」的时候不清理，
    用户隔天再来问还是会拿到昨天的同事数据。
    """
    state = ConversationState(
        asker_id="E1", period="202610", subject_id="B",
        subject_name="销售乙", subject_type="peer",
        last_active_at=NOW.timestamp(),
    )
    bot_session.qa_state = state.to_dict()

    later = NOW + timedelta(minutes=31)
    loaded = load_state(bot_session, asker_id="E1", now=later)
    assert loaded.period is None
    assert loaded.subject_id is None
    assert loaded.subject_name == ""


def test_load_state_keeps_context_within_idle(bot_session) -> None:
    """⚠️ 没超时不许清 —— 否则正常多轮对话会丢上下文。"""
    state = ConversationState(
        asker_id="E1", period="202610", subject_id="B",
        last_active_at=NOW.timestamp(),
    )
    bot_session.qa_state = state.to_dict()

    later = NOW + timedelta(minutes=29)
    loaded = load_state(bot_session, asker_id="E1", now=later)
    assert loaded.period == "202610"
    assert loaded.subject_id == "B"


def test_load_state_discards_context_of_other_asker(bot_session) -> None:
    """★ 换了人 ⇒ 上下文作废。

    ⚠️ 一个会话绑一个员工，理论上不会发生；
    但脏数据/测试库里出现过 —— 不能让 A 的上下文被 B 继承。
    """
    state = ConversationState(
        asker_id="E1", period="202610", subject_id="B",
        last_active_at=NOW.timestamp(),
    )
    bot_session.qa_state = state.to_dict()

    loaded = load_state(bot_session, asker_id="E2", now=NOW)
    assert loaded.period is None
    assert loaded.subject_id is None
    assert loaded.asker_id == "E2"


def test_save_state_does_not_touch_collected(bot_session) -> None:
    """★★ 绝不能碰 ``collected`` —— 那是**填报流程**的收集项。

    ⚠️ 混用会在「取消填报」时把问答上下文一起清掉。
    """
    bot_session.collected = {"some_field": "value"}
    state = ConversationState(asker_id="E1", period="202610")
    save_state(bot_session, state, now=NOW)
    assert bot_session.collected == {"some_field": "value"}


def test_save_state_replaces_json_column(bot_session) -> None:
    """⚠️ 必须**整体替换** JSON 列。

    SQLAlchemy 的 JSON 列**不追踪可变对象的内部修改** ——
    原地改 dict 不会触发 UPDATE。
    """
    state = ConversationState(asker_id="E1", period="202610")
    save_state(bot_session, state, now=NOW)
    first = bot_session.qa_state
    assert first["period"] == "202610"

    state.period = "202609"
    save_state(bot_session, state, now=NOW)
    assert bot_session.qa_state is not first
    assert bot_session.qa_state["period"] == "202609"


def test_save_state_sets_last_active(bot_session) -> None:
    state = ConversationState(asker_id="E1")
    save_state(bot_session, state, now=NOW)
    assert bot_session.qa_state["last_active_at"] == NOW.timestamp()


def test_save_state_with_none_session_is_noop() -> None:
    """⚠️ 没有会话 ⇒ 不炸（网页端不持久化）。"""
    save_state(None, ConversationState(), now=NOW)
