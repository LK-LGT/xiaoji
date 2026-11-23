"""飞书机器人的 AI 绩效问答（复用网页端同一套问答内核）。

⚠️ **与 `feishu_qa` 的关系**
----------------------
- ``feishu_qa`` —— **规则型**回答：直接吐规则快照 + 指标值 + 证据 JSON。
  不依赖 AI，**永远可用**，但用户要自己从 JSON 里找答案。
- 本模块 —— **AI 型**回答：自然语言解读 + 引用来源徽章。
  可读性好，但依赖 L3 存活。

⇒ **两者是降级关系，不是竞争关系**：
AI 挂了 → 自动回落到 ``feishu_qa``，用户仍然拿得到数据。
这个回落是**硬要求** —— 飞书是员工的主入口，
AI 挂了就答不了问题 = 员工没法用了。

⚠️ **任务归属与选择**
--------------------
飞书侧没有「任务下拉框」—— 员工只发一句话。
所以要自己定位任务：

1. 按「月份 + 我的考核记录」找任务（复用 ``feishu_qa`` 的规则）；
2. 找到**唯一一个** → 直接用它；
3. 找到**多个** → 不能猜！返回一个「请选择任务」的卡片，
   把候选列出来让用户用序号回复。

⚠️ **不猜**是关键：猜错任务 = 答的是**别人的**绩效或者**错月份**的绩效，
比答不出来严重得多。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AssessmentRecord, AssessmentTask, Employee
from . import feishu_qa, qa_chat_service
from .conversation import ConversationState
from .period import period_match_keys

logger = logging.getLogger(__name__)

#: 卡片上「继续选任务」的动作名
#: ⚠️ 定义在 `feishu_card` —— 那里是所有动作名的**唯一**出处，
#:    避免「卡片发出去了但回调认不出动作」这种对不上的坑。
ACTION_SELECT_TASK = "qa_select_task"


@dataclass
class FeishuQaOutcome:
    """飞书问答的结果（成功 / 降级 / 需选择任务）。"""

    #: 展示给用户的正文
    text: str
    #: 是否走了降级路径（AI 不可用，用规则型回答）
    degraded: bool = False
    #: 数据来源徽章文案（引用可核验时才有）
    sources: list[str] = None  # type: ignore[assignment]
    #: 引用数量
    citation_count: int = 0
    #: 候选任务（需要用户选时非空）
    candidates: list[dict[str, Any]] = None  # type: ignore[assignment]
    #: 被剔除的无效引用数
    dropped_citations: int = 0
    #: ★ P0-6：本次实际查询的任务 ID —— 用于写回会话上下文。
    task_id: str = ""
    #: ★ P0-6：本次实际查询的周期 —— 用于写回会话上下文。
    period: str = ""
    #: ★ P0-8：本次实际查询的**目标人**（同事）—— 用于写回会话上下文。
    subject_id: str = ""
    #: ★ P0-8：目标人姓名（展示用）。
    subject_name: str = ""

    def __post_init__(self) -> None:
        if self.sources is None:
            self.sources = []
        if self.candidates is None:
            self.candidates = []


def list_candidate_tasks(
    db: Session,
    employee: Employee,
    period: str,
    *,
    subject_id: str | None = None,
) -> list[AssessmentTask]:
    """列出该员工在指定月份的全部考核任务。

    ⚠️ **只看自己的记录** —— 绝不按「消息里指定了谁」去找任务，
    否则能查到别人的绩效。

    ⚠️ **P0-8：``subject_id`` 非空时按目标人找任务** ——
       查同事时，要找的是**同事名下**的任务记录，
       而不是提问者自己的任务。
       ⚠️ **这里找得到 ≠ 有权查** ——
          真正的判定在 ``qa_authorization.authorize_qa_subject``（硬闸门）。
          本函数只负责「找到这个人在这个月参与了哪些任务」。
    """
    owner_id = subject_id or employee.id
    return list(
        db.scalars(
            select(AssessmentTask)
            .join(AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id)
            .where(
                AssessmentRecord.employee_id == owner_id,
                AssessmentTask.period.in_(period_match_keys(period)),
            )
            .order_by(AssessmentTask.created_at.desc())
        ).all()
    )


def _candidate_entries(tasks: list[AssessmentTask]) -> list[dict[str, Any]]:
    """把候选任务整理成「序号 + 可读名字」。

    ⚠️ 同名任务要能区分 —— 与网页端 `GET /v1/qa/tasks` 用同一套规则，
    否则用户看到一排「2026 年 11 月月度考核」，还是选不出来。
    """
    from ..routes.qa import _decorate_labels

    items = [
        {
            "task_id": task.id,
            "name": task.name,
            "period": task.period,
            "status": task.status,
            "scope": "self",
            "allowed_tools": [],
            "has_snapshot": bool(task.snapshot_id),
            "created_at": task.created_at.isoformat() if task.created_at else None,
        }
        for task in tasks
    ]
    items.sort(key=lambda x: (x["created_at"] or ""), reverse=True)
    _decorate_labels(items, db=None)
    return items


async def answer_ai(
    db: Session,
    employee: Employee,
    question: str,
    period: str,
    *,
    task_id: str | None = None,
    subject_id: str | None = None,
    conversation: ConversationState | None = None,
) -> FeishuQaOutcome:
    """AI 问答（带规则型回落）。

    ⚠️ **不抛异常给用户**（除了权限/账号这类无法继续的情况）——
    AI 链路故障对用户来说只是「回答变朴素了」，不该看到红色报错。

    ⚠️ ``conversation``（P0-5）：多轮上下文。
       显式传入的 ``period`` 优先；没传则从上下文继承。

    ⚠️ ``subject_id``（P0-8）：本次查询的**目标人**（同事）。
       缺省 = 提问者本人（既有行为不变）。
       ⚠️ **这里不做权限判定** —— 判定在
          ``qa_authorization.authorize_qa_subject``（服务层硬闸门），
          本函数只是把目标人**透传**下去。
       ⚠️ 调用方**必须**先经过 ``qa_subject_matcher`` 收窄候选集，
          否则会把「想查谁就查谁」的能力暴露给飞书用户。

    Args:
        task_id: 指定任务。为空时按月份自动定位；
            若定位到**多个**任务则返回「需选择」的结果（``candidates`` 非空）。
        subject_id: 目标人 ID（同事）。为空 = 查自己。
        conversation: 会话上下文（由调用方从 ``BotSession.qa_state`` 读出）。

    Raises:
        HTTPException: 账号不可查（403）、任务不存在（404）。
    """
    if employee.employment_status != "active" or employee.account_status != "enabled":
        raise HTTPException(403, "账号当前不可查询")

    # ⚠️ P0-9：写操作意图拦截**不在这里** ——
    #    它在共享内核 ``qa_chat_service.ask_question_async`` 里，
    #    网页端与飞书端调的是同一个函数 ⇒ 两端行为必然一致。
    #    ⚠️ **不要**在这里再加一道 ——
    #    两处拦截会让「拦截点在哪」变得不确定，
    #    且文案可能漂移（飞书一份、网页一份）。

    # ⚠️ P0-5：周期合并 —— 显式 > 上下文继承。
    # ⚠️ 注意这里**只**用上下文里的周期；上下文里的**主体**不在这里用 ——
    #    主体需要候选集收窄（P0-8），在此之前继承主体有越权风险。
    resolved_period = period
    if conversation is not None and conversation.period:
        resolved_period = conversation.period

    # ⚠️ P0-8：目标人 —— 显式传入的优先。
    # ⚠️ **这里刻意不从 ``conversation`` 继承主体** ——
    #    继承的语义在 P0-9 统一处理（P0-5 已明确排除）。
    resolved_subject_id = subject_id

    # ⚠️ P0-8：查同事时，报错文案要换成「TA」而不是「你」——
    #    否则会出现"未找到**你**在 11 月的任务"这种怪异提示。
    subject_label = "该同事" if resolved_subject_id else "你"

    if task_id:
        task = db.get(AssessmentTask, task_id)
        if task is None:
            raise HTTPException(404, "考核任务不存在")
        candidates = [task]
    else:
        candidates = list_candidate_tasks(
            db, employee, resolved_period, subject_id=resolved_subject_id,
        )
        if not candidates:
            raise HTTPException(
                404,
                f"未找到{subject_label}在 {resolved_period} 的考核任务，"
                f"请确认月份，或联系 HR 分配考核任务。",
            )
        if len(candidates) > 1:
            # ⚠️ **不猜** —— 猜错就是答错人的绩效。
            #    返回候选让用户选。
            return FeishuQaOutcome(
                text=(
                    f"{subject_label}在 {resolved_period} 有 {len(candidates)} 个考核任务，"
                    f"回复序号选择要查询的任务：\n"
                    + "\n".join(
                        f"{index}. {entry['label']}"
                        for index, entry in enumerate(
                            _candidate_entries(candidates), start=1,
                        )
                    )
                ),
                candidates=_candidate_entries(candidates),
                subject_id=resolved_subject_id or "",
            )
        task = candidates[0]

    try:
        result = await qa_chat_service.ask_question_async(
            db,
            employee,
            question=question,
            task_id=task.id,
            period=resolved_period,
            subject_id=resolved_subject_id,
            conversation=conversation,
        )
    except HTTPException:
        # ⚠️ 权限类错误（无权查该任务等）—— **不能**降级成规则型回答，
        #    否则会绕过权限检查！直接抛出。
        raise
    except Exception:
        # ⚠️ AI 链路整体异常 → 降级到规则型回答（不抛给用户）。
        logger.exception("飞书 AI 问答异常，回落到规则型回答")
        try:
            text = feishu_qa.answer(db, employee, question, resolved_period)
        except Exception:
            logger.exception("规则型回落也失败")
            text = "查询失败，请稍后重试，或到网页后台「绩效问答」查看明细。"
        return FeishuQaOutcome(
            text=text,
            degraded=True,
            task_id=task.id,
            period=resolved_period,
            subject_id=resolved_subject_id or "",
        )

    if result.degraded:
        # ⚠️ AI 不可用 → 规则型回落。
        #    `result.data` 里已经有预取的结构化数据，
        #    但飞书卡片展示规则型回答更紧凑、更可读。
        try:
            text = feishu_qa.answer(db, employee, question, resolved_period)
        except Exception:
            logger.exception("规则型回落也失败")
            text = result.answer
        return FeishuQaOutcome(
            text=text,
            degraded=True,
            sources=list(result.sources),
            citation_count=len(result.citations),
            dropped_citations=result.dropped_citations,
            task_id=task.id,
            period=result.period or resolved_period,
            subject_id=result.subject_id or (resolved_subject_id or ""),
            subject_name=result.subject_name or "",
        )

    # ⚠️ P0-5：周期继承要在回答里**标注** —— 用户得知道查的是哪个月。
    text = result.answer
    if result.period_inherited and result.period:
        inherited_month = f"{result.period[:4]}-{result.period[4:]}"
        text = f"（沿用上一轮周期：{inherited_month}）\n{text}"

    # ⚠️ P0-8：查同事时，回答里要**标明问的是谁** ——
    #    否则用户看到自己的名字出现在回答里，会以为系统在答自己。
    if result.subject_name and result.subject_id and result.subject_id != employee.id:
        text = f"（查询对象：{result.subject_name}）\n{text}"

    return FeishuQaOutcome(
        text=text,
        degraded=False,
        sources=list(result.sources),
        citation_count=len(result.citations),
        dropped_citations=result.dropped_citations,
        # ★ P0-6：回给调用方，用于写回会话上下文。
        task_id=task.id,
        period=result.period or resolved_period,
        # ★ P0-8：目标人 —— 用于写回会话上下文。
        subject_id=result.subject_id or (resolved_subject_id or ""),
        subject_name=result.subject_name or "",
    )


__all__ = [
    "ACTION_SELECT_TASK",
    "FeishuQaOutcome",
    "answer_ai",
    "list_candidate_tasks",
]
