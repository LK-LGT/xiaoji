"""★ P0-5：多轮问答链路的集成测试。

⚠️ **这个文件守的是"多轮对话真的能用"**
--------------------------------------
单元测试（``test_qa_conversation.py``）守的是**槽位逻辑**本身；
本文件守的是**接线**：

    第一轮「上个月我多少分」→ 第二轮「那扣分原因呢」
    ⇒ 第二轮**没有**月份，但必须查到**上个月**的数据

⚠️ **接线最容易出的错**
----------------------
1. 上下文**没传进**服务层 ⇒ 静默查了当月（用户以为查的是上个月）
2. 上下文**没写回** ⇒ 第二轮还是拿不到
3. 继承来的主体**没复核授权** ⇒ 越权通道
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.bot_models import BotSession
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    HrDepartmentConfig,
    Score,
)
from backend.app.services import conversation as conversation_mod
from backend.app.services import qa_chat_service
from backend.app.services.conversation import ConversationState


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
def seeded(db):
    for dept in (
        Department(id="D_ROOT", name="总部", active=True),
        Department(id="D_SALES", name="销售部", active=True),
    ):
        db.add(dept)
    db.flush()

    def emp(emp_id, name, dept_id, manager):
        db.add(Employee(
            id=emp_id, name=name, department_id=dept_id, manager_id=manager,
            employment_status="active", account_status="enabled",
        ))
        db.flush()

    emp("BOSS", "总裁", "D_ROOT", None)
    emp("MGR", "销售经理", "D_SALES", "BOSS")
    emp("A", "销售甲", "D_SALES", "MGR")
    emp("B", "销售乙", "D_SALES", "MGR")
    for emp_id, dept_id in (
        ("BOSS", "D_ROOT"), ("MGR", "D_SALES"), ("A", "D_SALES"), ("B", "D_SALES"),
    ):
        db.add(EmployeeDepartment(
            employee_id=emp_id, department_id=dept_id, is_primary=True
        ))
    db.add(HrDepartmentConfig(department_id="D_ROOT", active=True))

    # 两个周期的任务 —— 用来验证"继承的是上个月，不是当月"
    for period, total in (("202610", 70.0), ("202611", 88.0)):
        task = AssessmentTask(
            id=f"T_{period}", name=f"{period} 月度考核", period=period,
            department_id="D_SALES", status="published",
        )
        db.add(task)
        db.flush()
        record = AssessmentRecord(
            id=f"R_A_{period}", task_id=task.id, employee_id="A", status="scored",
        )
        db.add(record)
        db.flush()
        db.add(Score(
            id=f"S_A_{period}", record_id=record.id, total=total,
            per_metric_json=[], trace_json={"participating_count": 0},
        ))
        # ⚠️ B 也要有记录 —— 否则 peer 路径走不到
        #    （``resolve_scope`` 里没有 record ⇒ 没有工具 ⇒ 403）
        peer_record = AssessmentRecord(
            id=f"R_B_{period}", task_id=task.id, employee_id="B", status="scored",
        )
        db.add(peer_record)
        db.flush()
        db.add(Score(
            id=f"S_B_{period}", record_id=peer_record.id, total=75.0,
            per_metric_json=[], trace_json={"participating_count": 0},
        ))
    db.commit()


@pytest.fixture()
def bot_session(db):
    session = BotSession(
        id="S1", open_id="ou_x", app_id="app", state="idle", intent="none",
        collected={}, qa_state={},
    )
    db.add(session)
    db.commit()
    return session


@pytest.fixture()
def fake_llm(monkeypatch):
    """把 L3 换成可控的假实现 —— 只看 L2 的槽位解析结果。"""
    captured: list[dict] = []

    async def fake_qa(self, payload):
        captured.append(payload)
        return {
            "answer": "回答", "citations": [], "sources": [],
            "degraded": False, "tool_calls": [],
        }

    from backend.app.services.review_service import AgentClient

    monkeypatch.setattr(AgentClient, "__init__", lambda self: None)
    monkeypatch.setattr(AgentClient, "qa", fake_qa)
    return captured


def ask(db, employee, question, *, task_id="T_202611", period=None,
        state=None):
    return asyncio.run(qa_chat_service.ask_question_async(
        db, employee, question=question, task_id=task_id,
        period=period, conversation=state,
    ))


# ---------------------------------------------------------------------------
# 1. 周期继承
# ---------------------------------------------------------------------------


def test_second_turn_inherits_period(db, seeded, fake_llm) -> None:
    """★★ 「那扣分原因呢」要查**上一轮**的周期。

    ⚠️ 第二句里没有月份 —— 不继承就查不到。
    """
    employee = db.get(Employee, "A")
    state = ConversationState(asker_id="A")

    first = ask(db, employee, "202610 的绩效", task_id="T_202610", state=state)
    assert first.period == "202610"
    assert first.period_inherited is False

    # ⚠️ 第二句没有月份 ⇒ 继承
    state.period = first.period
    state.last_active_at = __import__("time").time()
    second = ask(db, employee, "那扣分原因呢", task_id="T_202610", state=state)
    assert second.period == "202610"
    assert second.period_inherited is True


def test_explicit_period_beats_inherited(db, seeded, fake_llm) -> None:
    """★ 显式周期优先于继承。

    ⚠️ 显式传 ``period=`` ⇒ **不是继承**（``period_inherited=False``）。
       这两者要分清 —— 前端要据此决定显不显示"沿用上一轮"。
    """
    employee = db.get(Employee, "A")
    state = ConversationState(asker_id="A", period="202609")
    result = ask(db, employee, "那扣分原因呢", task_id="T_202610",
                 period="202610", state=state)
    assert result.period == "202610"
    assert result.period_inherited is False


def test_period_parsed_from_text_beats_inherited(db, seeded, fake_llm) -> None:
    """★ 文本里的周期优先于继承。"""
    employee = db.get(Employee, "A")
    state = ConversationState(asker_id="A", period="202610")
    result = ask(db, employee, "202611 的绩效", task_id="T_202611", state=state)
    assert result.period == "202611"
    assert result.period_inherited is False


# ---------------------------------------------------------------------------
# 2. 主体继承 —— 必须复核授权
# ---------------------------------------------------------------------------


def test_inherited_subject_still_authorized(db, seeded, fake_llm) -> None:
    """★ 继承的平级同事仍能查到（同部门同级别）。"""
    employee = db.get(Employee, "A")
    state = ConversationState(asker_id="A", subject_id="B")
    # ⚠️ B 在 T_202610 没有记录 ⇒ 403/404 由 resolve_scope 判定
    result = ask(db, employee, "他多少分", task_id="T_202610", state=state)
    # 能走到返回 ⇒ 说明授权通过（否则会抛 HTTPException）
    assert result.subject_id == "B"
    assert result.subject_inherited is True


def test_inherited_cross_department_subject_denied(db, seeded, fake_llm) -> None:
    """★★ 继承的主体**仍要过授权** —— 会话状态不是越权通道。

    ⚠️ 这里把 subject_id 指向上级 MGR —— 必须被拒。
    """
    from fastapi import HTTPException

    employee = db.get(Employee, "A")
    state = ConversationState(asker_id="A", subject_id="MGR")
    with pytest.raises(HTTPException) as exc:
        ask(db, employee, "他多少分", task_id="T_202610", state=state)
    assert exc.value.status_code == 403


def test_inherited_subject_crosses_department_rejected(db, seeded, fake_llm) -> None:
    """★ 跨部门继承 ⇒ 拒绝。"""
    from fastapi import HTTPException

    db.add(Employee(
        id="X", name="市场甲", department_id="D_ROOT", manager_id="BOSS",
        employment_status="active", account_status="enabled",
    ))
    db.add(EmployeeDepartment(
        employee_id="X", department_id="D_ROOT", is_primary=True,
    ))
    db.commit()

    employee = db.get(Employee, "A")
    state = ConversationState(asker_id="A", subject_id="X")
    with pytest.raises(HTTPException) as exc:
        ask(db, employee, "他多少分", task_id="T_202610", state=state)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 3. 持久化闭环
# ---------------------------------------------------------------------------


def test_save_then_load_roundtrip(db, seeded, bot_session, fake_llm) -> None:
    """★★ 写回 → 读出 → 再问，能用上上一轮的周期。"""
    import time

    employee = db.get(Employee, "A")
    state = conversation_mod.load_state(bot_session, asker_id="A")
    assert state.period is None

    result = ask(db, employee, "202610 的绩效", task_id="T_202610", state=state)
    conversation_mod.save_state(
        bot_session, state, task_id=result.task_id, period=result.period,
    )
    db.commit()

    # ⚠️ 重新读（模拟下一次事件）
    reloaded = conversation_mod.load_state(bot_session, asker_id="A")
    assert reloaded.period == "202610"
    assert reloaded.task_id == "T_202610"
    # ⚠️ 没有超时 —— 上下文要保留
    assert reloaded.period == "202610"


def test_idle_session_drops_period(db, seeded, bot_session, fake_llm) -> None:
    """★★ 空闲超时 ⇒ 周期不再继承。

    ⚠️ 这是"答非所问"的主要来源 ——
    用户隔天问「扣分原因」，不该拿到上周的周期。
    """
    import time
    from datetime import timedelta

    employee = db.get(Employee, "A")
    state = conversation_mod.load_state(bot_session, asker_id="A")
    result = ask(db, employee, "202610 的绩效", task_id="T_202610", state=state)
    conversation_mod.save_state(
        bot_session, state, task_id=result.task_id, period=result.period,
    )
    db.commit()

    # ⚠️ 把 last_active_at 拨到 31 分钟前
    raw = dict(bot_session.qa_state)
    raw["last_active_at"] = time.time() - 31 * 60
    bot_session.qa_state = raw
    db.commit()

    reloaded = conversation_mod.load_state(bot_session, asker_id="A")
    assert reloaded.period is None


def test_conversation_state_survives_bot_session_json(db, seeded, bot_session) -> None:
    """★ ``qa_state`` 是 JSON 列 ⇒ 存进去能原样取出。"""
    state = ConversationState(
        asker_id="A", period="202610", subject_id="B", subject_name="销售乙",
    )
    conversation_mod.save_state(bot_session, state, period="202610")
    db.commit()
    db.expire_all()
    fresh = db.get(BotSession, "S1")
    assert fresh.qa_state["period"] == "202610"
    assert fresh.qa_state["subject_id"] == "B"
    assert fresh.qa_state["subject_name"] == "销售乙"


# ---------------------------------------------------------------------------
# 4. 不传上下文 ⇒ 行为不变
# ---------------------------------------------------------------------------


def test_no_conversation_means_no_inheritance(db, seeded, fake_llm) -> None:
    """★ 不传上下文 ⇒ 不继承任何东西（网页端既有行为不变）。"""
    employee = db.get(Employee, "A")
    result = ask(db, employee, "那扣分原因呢", task_id="T_202611", state=None)
    assert result.period == "202611"
    assert result.period_inherited is False
    assert result.subject_id == "A"
