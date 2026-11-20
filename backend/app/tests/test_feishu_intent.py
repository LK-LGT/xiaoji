"""飞书意图识别的分层路由测试。

⚠️ **为什么这组测试重要**
------------------------
意图识别曾经是「关键词表 = 判据」——
命中就答，不命中就回「请发送 xxx」的提示卡。

那个设计在真实使用里必然崩：员工说的是口语，
关键词表永远穷举不完，于是大量提问被当成听不懂。

现在改成**分层路由**：

    ① 写操作指令 → 关键词可靠（副作用大，宁可漏不可错）
    ② 绩效快捷通道 → 命中就省一次模型往返
    ③ 兜底 → 交给 L3 模型自己判断

⇒ 这里重点测两件事：
    A. **口语化问法能被识别**（不再漏判）
    B. **写操作意图不被兜底抢走**（副作用安全）
"""

from __future__ import annotations

import pytest

from backend.app.services import feishu_bot, feishu_intent
from backend.app.services.feishu_scheme import INTENT as SCHEME_INTENT

# ===========================================================================
# ① 写操作指令 —— 优先级最高，不能被兜底抢走
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "取消",
        "算了",
        "不填了",
        "先不填了",
        "不问了",
    ],
)
def test_cancel_wins(text):
    """取消关键词 → cancel。"""
    assert feishu_bot.detect_intent(text) == feishu_intent.INTENT_CANCEL


@pytest.mark.parametrize(
    "text",
    ["填月计划", "填月度计划", "写计划", "填表", "帮我填月计划"],
)
def test_month_plan_detected(text):
    """填月计划关键词 → month_plan。"""
    assert feishu_bot.detect_intent(text) == feishu_bot.INTENT_MONTH_PLAN


def test_month_plan_wins_over_performance_words():
    """「填月计划时顺便看看我的绩效」→ 必须是 month_plan。

    ⚠️ 写操作的**对象**错了后果是写入错误草稿，
    比答不出来严重得多 ⇒ 写操作优先。
    """
    text = "填月计划时顺便看看我的绩效得分"
    assert feishu_bot.detect_intent(text) == feishu_bot.INTENT_MONTH_PLAN


def test_cancel_wins_over_month_plan():
    """「算了不填月计划了」→ 必须是 cancel。

    ⚠️ 这里有个真实的坑：``CANCEL_KEYWORDS`` 含「算了」，
    而 ``MONTH_PLAN_KEYWORDS`` 含「月计划」——
    **取消必须在前面检查**，否则「算了不填月计划了」
    会同时命中两个关键词，走去填表而不是取消。
    """
    assert feishu_bot.detect_intent("算了不填月计划了") == feishu_intent.INTENT_CANCEL


def test_scheme_intent_still_detected():
    """考核填报（方案）意图不受影响。"""
    assert feishu_bot.detect_intent("考核填报") == SCHEME_INTENT


# ===========================================================================
# ② 绩效快捷通道 —— 口语化问法不能漏
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        # ── 早期版本漏掉的典型口语 ──
        "我这个月分数怎么算出来的",
        "分数怎么算",
        "我表现怎么样",
        "上个月我拿了多少分",
        "我被扣了吗",
        "咱部门这月整体如何",
        "我那几项指标达成得咋样",
        "我够格吗",
        "我是什么评级",
        # ── 早期版本能命中的 ──
        "我的绩效指标怎么算",
        "我的绩效得分是多少",
        "我的评分是怎么算出来的",
        "我的绩效被扣分了吗",
        "部门统计",
        "团队统计怎么样",
        "我的指标达成率是多少",
        "我的绩效 2026-11 得分",
    ],
)
def test_colloquial_performance_questions_detected(text):
    """口语化问法必须命中，不能漏。

    ⚠️ 这批就是**真实漏判过的问法** ——
    早期版本只认 7 个词，下面一半都判成「听不懂」。
    """
    assert feishu_bot.detect_intent(text) == feishu_intent.INTENT_PERFORMANCE_QA


# ===========================================================================
# ③ 兜底 —— 没命中不是「拒绝」
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "今天中午吃什么",
        "今天天气不错",
        "帮我写个周报",
        "你是谁",
        "哈哈哈",
        "在吗",
    ],
)
def test_unknown_returns_none_not_rejection(text):
    """没命中 → ``none``。

    ⚠️ ``none`` **不代表拒绝** —— 它代表「交给模型兜底」。
    路由层（``feishu_bot`` 的消息处理）会把 ``none``
    改写成 ``performance_qa``，让 L3 自己判断能不能答。

    保留 ``none`` 这个取值而不是直接返回问答，
    是为了让 ``detect_intent`` 保持**纯分类**语义、可单测。
    """
    assert feishu_bot.detect_intent(text) == feishu_intent.INTENT_NONE


def test_empty_text_returns_none():
    assert feishu_bot.detect_intent("") == feishu_intent.INTENT_NONE
    assert feishu_bot.detect_intent("   ") == feishu_intent.INTENT_NONE


def test_is_performance_related_is_loose():
    """``is_performance_related`` 是宽松判断，只用于日志与说明。"""
    assert feishu_intent.is_performance_related("我的绩效怎么样") is True
    assert feishu_intent.is_performance_related("今天天气不错") is False
    assert feishu_intent.is_performance_related("") is False


# ===========================================================================
# 词表质量
# ===========================================================================


def test_performance_keywords_have_no_empty_entries():
    """词表里不能有空串 —— 空串会让 ``keyword in text`` 恒为真。"""
    for keyword in feishu_intent.PERFORMANCE_KEYWORDS:
        assert keyword.strip(), "词表里不能有空关键词"
        assert keyword == keyword.strip(), f"关键词有多余空白: {keyword!r}"


def test_cancel_keywords_do_not_collide_with_performance():
    """取消词不能出现在绩效词表里。

    ⚠️ 如果「算了」同时是绩效词，路由顺序一旦调整就可能答非所问。
    """
    overlap = set(feishu_intent.CANCEL_KEYWORDS) & set(feishu_intent.PERFORMANCE_KEYWORDS)
    assert not overlap, f"取消词与绩效词重叠: {overlap}"


def test_backend_aliases_still_exported():
    """``feishu_bot`` 的旧别名必须还在（几十处调用方在用）。"""
    assert feishu_bot.INTENT_NONE == feishu_intent.INTENT_NONE
    assert feishu_bot.INTENT_MONTH_PLAN == feishu_intent.INTENT_MONTH_PLAN
    assert feishu_bot.INTENT_PERFORMANCE_QA == feishu_intent.INTENT_PERFORMANCE_QA
    assert feishu_bot.detect_intent is feishu_intent.detect_intent
