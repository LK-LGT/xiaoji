"""问答核心逻辑（HTTP 端点与飞书机器人共用）。

⚠️ **为什么要抽成服务**
--------------------
`/v1/qa/ask`（网页端）与飞书机器人的问答入口需要**完全一致**的
行为：同一套权限收敛、同一套引用校验、同一套降级策略。

如果飞书侧另写一份，就会出现「网页说能查、飞书说查不到」这类
**行为漂移** —— 用户会认为功能有 bug。所以这里把核心逻辑抽出来，
两个入口都调它。

⚠️ **不做什么**
--------------
这里**不复用 HTTP 自调用**（`httpx.post("http://localhost:8000/v1/qa/ask")`）。
原因有二：
  ① 飞书侧没有登录态 —— 自调用拿不到「当前用户是谁」，
     得再发明一套「用 open_id 换 token」的机制，纯属绕远路；
  ② 自调用会把内部失败翻译成 HTTP 状态码，**降级路径会丢**
     —— 网页端还能看到「AI 不可用 + 原始数据」，
     飞书端就只能看到一个 500。

⇒ 直接用员工身份调用本服务。

⚠️ **为什么叫 `qa_chat_service` 而不是 `qa_service`**
----------------------------------------------------
`services/qa_service.py` **已经存在**，提供的是另一套东西
（``query_self_result`` / ``query_self_progress`` 等**结构化查询**）。

⇒ 名字必须区分开：
  - ``qa_service`` —— 旧的**结构化查询**服务（已被 ``main.py`` 使用）
  - ``qa_chat_service`` —— 新的**自然语言问答**服务（本文件）

⚠️ 同名模块会**互相覆盖**（Python 同名模块只留一个），
   一旦撞名，旧接口会突然「没有这个属性」——
   所以这里刻意用不同的名字。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..domain.models import AiTrace, AssessmentTask, Employee
from . import agent_tool_service, peer_score_service, privacy, qa_authorization
from .agent_tool_token import issue_agent_tool_token
from .conversation import ConversationState, resolve_slots
from .review_service import AgentClient

logger = logging.getLogger(__name__)

#: ⚠️ P0-9：写操作被拦截时的**统一文案**。
#:
#: ⚠️ 为什么放在**共享内核**里
#:    网页端与飞书端调的是**同一个函数** ——
#:    拦截点与文案都在这里，才能保证**两端一致**。
#:    否则用户在飞书看到"请去后台"、在网页看到"未知错误"，
#:    会以为系统坏了而反复重试。
QA_WRITE_DENIED_MESSAGE = "AI 问答不提供绩效修改功能。"


class AgentQAResponse(BaseModel):
    """L3 智能体的问答响应（契约）。

    ⚠️ `extra="forbid"` —— L3 多返回一个字段就报错。
    这是**故意**的：多出来的字段通常意味着契约漂移，
    静默忽略会让「上游改了结构」变成查不出来的灵异问题。
    """

    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=16000)
    citations: list[str] = Field(default_factory=list, max_length=200)
    sources: list[str] = Field(default_factory=list, max_length=200)
    degraded: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    agent_status: dict[str, str] = Field(default_factory=dict)


@dataclass
class QaResult:
    """一次问答的结果（两个入口共用同一形状）。

    ⚠️ `degraded` 与 `dropped_citations` 是**两件不同的事**：
      - `degraded` → AI 整体不可用，回答是兜底文案
      - `dropped_citations` > 0 → AI 回答**可用**，只是部分引用没能核验
    """

    answer: str
    citations: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False
    follow_up_questions: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str = ""
    dropped_citations: int = 0
    period: str = ""
    task_id: str = ""
    task_name: str = ""
    scope: str = "self"
    completeness: float = 0.0
    #: ★ P0-5：本次解析出的目标人（授权通过后的那个）。
    subject_id: str = ""
    #: ★ P0-5：目标人姓名 —— 跨主体继承时要在回答里显名。
    subject_name: str = ""
    #: ★ P0-5：周期是否**继承**自上一轮（回答里要标注"沿用上一轮"）。
    period_inherited: bool = False
    #: ★ P0-5：主体是否**继承**自上一轮。
    subject_inherited: bool = False


async def ask_question_async(
    db: Session,
    employee: Employee,
    *,
    question: str,
    task_id: str,
    period: str | None = None,
    subject_id: str | None = None,
    conversation: ConversationState | None = None,
) -> QaResult:
    """回答一个问题。

    权限收敛、引用校验、降级策略与网页端**完全一致** ——
    因为它们调的是同一个函数。

    ⚠️ ``subject_id``（P0-4b）：本次查询的**目标人**。
       缺省 = 提问者本人（既有行为不变）。
       非空时会被**授权判定**（``qa_authorization.authorize_qa_subject``），
       判定通过后写入**签名令牌**的 ``subj`` ——
       工具层只认令牌里的目标人，**不接受请求体指定**。

    ⚠️ ``conversation``（P0-5）：多轮上下文。
       显式指定的 ``period`` / ``subject_id`` **优先**；
       没说的从 ``conversation`` 继承。

    ⚠️ **继承 ≠ 授权** ——
       继承来的主体仍会走 ``authorize_qa_subject`` 判定。
       ⇒ 会话状态**不能**成为越权通道。

    Raises:
        HTTPException: 权限不足 / 任务不存在 / 周期不一致。
            这些是**用户能改正**的错误（换个任务、换个月份），
            所以直接抛 —— 飞书侧会把它转成错误卡片。
    """
    started = time.perf_counter()

    # ── P0-9：写操作意图拦截（**共享内核**）──────────────────
    # ⚠️ **为什么放在这里而不是各入口**
    #    网页端（``routes/qa.py``）与飞书端（``feishu_qa_ai``）都调**这个函数**
    #    ⇒ 放这里才能保证**两端行为一致**。
    #    否则用户在飞书被拦、在网页不被拦（或者反过来）⇒ 用户会以为系统坏了。
    #
    # ⚠️ **这是快速路径，不是安全边界**
    #    真正的边界是「工具层不提供写工具」——
    #    即使用户说「帮我把分数往上抬一点」绕过了关键词，
    #    模型也调不到任何写工具。
    #
    # ⚠️ **为什么要在这一层拦**
    #    否则用户问「帮我把分数改成95分」会得到一个**答非所问**的读数回答，
    #    他会以为系统不支持改分 → 换个说法再试 → 白费好几轮。
    write_hit = qa_authorization.detect_write_action(question)
    if write_hit is not None:
        logger.info("问答拦截写操作意图：命中=%s", write_hit)
        raise HTTPException(403, QA_WRITE_DENIED_MESSAGE)

    # ── P0-5：槽位合并（显式 > 文本解析 > 继承）────────────
    # ⚠️ **必须在 resolve_scope 之前** ——
    #    继承来的周期/主体要参与授权判定与工具白名单裁剪。
    # ⚠️ **用模块级 `resolve_slots`，不能用参数名 `conversation`** ——
    #    参数 `conversation` 遮蔽了模块名 ⇒ `conversation.resolve_slots` 会炸。
    slots = resolve_slots(
        question,
        conversation,
        explicit_period=period,
        explicit_subject_id=subject_id,
    )
    # ⚠️ 用 ``qa_authorization.resolve_scope``（不是旧的 ``qa_permission``）——
    #    它多了目标人授权判定与按目标人类型裁剪工具白名单。
    scope = qa_authorization.resolve_scope(
        db, employee, task_id, slots.period, subject_id=slots.subject_id
    )
    task = db.get(AssessmentTask, task_id)
    # ⚠️ 目标人 —— 授权通过后的那个（可能是同部门平级同事）。
    target_employee_id = scope.subject_id or employee.id

    # ── 预取结构化数据（L2 侧算，AI 只解读）──────────────────
    structured: dict[str, Any] = {}
    for tool in scope.allowed_tools:
        try:
            if tool == "get_metric_values":
                value = agent_tool_service.load_metric_values(db, employee_id=target_employee_id, task=task)
            elif tool == "get_scoring_breakdown":
                value = agent_tool_service.load_scoring_breakdown(db, employee_id=target_employee_id, task=task)
            elif tool == "get_peer_score":
                # ⚠️ peer 专用路径：字段白名单裁剪（不含评语/证据/自评）
                value = peer_score_service.load_peer_score(db, employee_id=target_employee_id, task=task)
            elif tool == "get_evidence":
                # 不扩大可见性：预取默认使用普通员工视角。
                value = agent_tool_service.load_evidence(db, employee_id=target_employee_id, task=task, usage=None)
            else:
                value = agent_tool_service.load_department_stats(
                    db, department_id=employee.department_id, task_id=task.id,
                )
            structured[tool] = privacy.redact_obj(value)
        except Exception:
            structured[tool] = {"available": False}

    # ── 调 L3 ────────────────────────────────────────────────
    employee_id = employee.id
    trace_id = str(uuid4())
    payload: dict[str, Any] = {
        "question": privacy.redact_text(question),
        "context": {"period": scope.period, "scope": scope.scope, "task_id": task_id},
        "allowed_tools": list(scope.allowed_tools),
        "trace_id": trace_id,
    }

    # ⚠️ 结束读事务，等待 L3 时不持有数据库连接或 SQLite 事务。
    #    L3 会回调本进程取工具数据 —— 若这里还持有事务，
    #    SQLite 会因写锁冲突而报 "database is locked"。
    db.rollback()

    response: AgentQAResponse | None = None
    try:
        payload["tool_token"] = issue_agent_tool_token(
            employee_id=employee_id,
            task_id=task_id,
            allow=scope.allowed_tools,
            scope=scope.scope,
            # ⚠️ P0-4b：目标人写进**签名令牌** ——
            #    工具层只认令牌里的 subj，不接受请求体指定。
            #
            # ⚠️ **查自己时不带 subj** ——
            #    「没有 subj」是「目标即本人」的既定约定；
            #    带上本人的 id 虽无害，但会让下游误判成"查他人"分支。
            subject_id=(
                scope.subject_id
                if scope.subject_id and scope.subject_id != employee_id
                else None
            ),
            reason=scope.authorization_reason,
        )
        response = AgentQAResponse.model_validate(await AgentClient().qa(payload))
        # ⚠️ 引用必须**全部**在 sources 内 —— 否则整份回答丢弃。
        if not response.answer.strip() or set(response.citations) - set(response.sources):
            raise ValueError("无效问答结果")
    except Exception:
        response = None

    degraded = response is None or response.degraded
    answer = (
        response.answer
        if response
        else "AI 问答暂不可用，下方为系统查询数据，也可到汇总报表查看明细。"
    )

    # 数据完整度由 L2 已成功预取的数据源比例给出，**不接受模型自报**。
    available = sum(v.get("available") is not False for v in structured.values())
    total_tools = len(scope.allowed_tools)

    # 引用缺口：模型引用了不存在的来源（已被 agent-service 剔除）。
    # ⚠️ 这**不等于**降级 —— 工具正常、回答可读，只是引用有缺口。
    agent_status = response.agent_status if response else {}
    dropped_citations = int(agent_status.get("dropped_citations") or 0)

    # ⚠️ 目标人姓名 —— 跨主体继承时要在回答里显名（「他」要变成「张三」）。
    #    只在**查他人**时取名 —— 查自己不需要显名。
    subject_name = ""
    if scope.subject_id and scope.subject_id != employee.id:
        subject_row = db.get(Employee, scope.subject_id)
        subject_name = subject_row.name if subject_row else ""

    result = QaResult(
        answer=answer,
        citations=response.citations if response else [],
        sources=response.sources if response else [],
        data=structured,
        degraded=degraded,
        follow_up_questions=[],
        tool_calls=response.tool_calls if response else [],
        trace_id=trace_id,
        dropped_citations=dropped_citations,
        period=scope.period,
        task_id=task_id,
        task_name=task.name if task else "",
        scope=scope.scope,
        completeness=(available / total_tools) if total_tools else 0.0,
        subject_id=scope.subject_id or employee.id,
        subject_name=subject_name,
        period_inherited=slots.period_inherited,
        subject_inherited=slots.subject_inherited,
    )

    _record_trace(db, trace_id, result, started)
    return result


def _record_trace(db: Session, trace_id: str, result: QaResult, started: float) -> None:
    """记 AI 调用流水。

    ⚠️ 失败**不能影响主流程** —— 流水只是审计，不是业务必需。
    """
    try:
        with db.begin_nested():
            db.add(
                AiTrace(
                    id=trace_id,
                    agent="qa",
                    status="degraded" if result.degraded else "ok",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    output_json={
                        "degraded": result.degraded,
                        "tool_count": len(result.tool_calls),
                        "dropped_citations": result.dropped_citations,
                        "scope": result.scope,
                    },
                    error="QA_DEGRADED" if result.degraded else None,
                )
            )
            db.flush()
    except Exception:
        pass


__all__ = [
    "QaResult",
    "ask_question_async",
]
