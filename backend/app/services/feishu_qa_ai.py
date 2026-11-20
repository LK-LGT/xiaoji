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

    def __post_init__(self) -> None:
        if self.sources is None:
            self.sources = []
        if self.candidates is None:
            self.candidates = []


def list_candidate_tasks(
    db: Session,
    employee: Employee,
    period: str,
) -> list[AssessmentTask]:
    """列出该员工在指定月份的全部考核任务。

    ⚠️ **只看自己的记录** —— 绝不按「消息里指定了谁」去找任务，
    否则能查到别人的绩效。
    """
    return list(
        db.scalars(
            select(AssessmentTask)
            .join(AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id)
            .where(
                AssessmentRecord.employee_id == employee.id,
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
) -> FeishuQaOutcome:
    """AI 问答（带规则型回落）。

    ⚠️ **不抛异常给用户**（除了权限/账号这类无法继续的情况）——
    AI 链路故障对用户来说只是「回答变朴素了」，不该看到红色报错。

    Args:
        task_id: 指定任务。为空时按月份自动定位；
            若定位到**多个**任务则返回「需选择」的结果（``candidates`` 非空）。

    Raises:
        HTTPException: 账号不可查（403）、任务不存在（404）。
    """
    if employee.employment_status != "active" or employee.account_status != "enabled":
        raise HTTPException(403, "账号当前不可查询")

    if task_id:
        task = db.get(AssessmentTask, task_id)
        if task is None:
            raise HTTPException(404, "考核任务不存在")
        candidates = [task]
    else:
        candidates = list_candidate_tasks(db, employee, period)
        if not candidates:
            raise HTTPException(
                404, f"未找到你在 {period} 的考核任务，请确认月份，或联系 HR 分配考核任务。"
            )
        if len(candidates) > 1:
            # ⚠️ **不猜** —— 猜错就是答错人的绩效。
            #    返回候选让用户选。
            return FeishuQaOutcome(
                text=(
                    f"你在 {period} 有 {len(candidates)} 个考核任务，"
                    f"回复序号选择要查询的任务：\n"
                    + "\n".join(
                        f"{index}. {entry['label']}"
                        for index, entry in enumerate(
                            _candidate_entries(candidates), start=1,
                        )
                    )
                ),
                candidates=_candidate_entries(candidates),
            )
        task = candidates[0]

    try:
        result = await qa_chat_service.ask_question_async(
            db,
            employee,
            question=question,
            task_id=task.id,
            period=period,
        )
    except HTTPException:
        # ⚠️ 权限类错误（无权查该任务等）—— **不能**降级成规则型回答，
        #    否则会绕过权限检查！直接抛出。
        raise
    except Exception:
        # ⚠️ AI 链路整体异常 → 降级到规则型回答（不抛给用户）。
        logger.exception("飞书 AI 问答异常，回落到规则型回答")
        try:
            text = feishu_qa.answer(db, employee, question, period)
        except Exception:
            logger.exception("规则型回落也失败")
            text = "查询失败，请稍后重试，或到网页后台「绩效问答」查看明细。"
        return FeishuQaOutcome(text=text, degraded=True)

    if result.degraded:
        # ⚠️ AI 不可用 → 规则型回落。
        #    `result.data` 里已经有预取的结构化数据，
        #    但飞书卡片展示规则型回答更紧凑、更可读。
        try:
            text = feishu_qa.answer(db, employee, question, period)
        except Exception:
            logger.exception("规则型回落也失败")
            text = result.answer
        return FeishuQaOutcome(
            text=text,
            degraded=True,
            sources=list(result.sources),
            citation_count=len(result.citations),
            dropped_citations=result.dropped_citations,
        )

    return FeishuQaOutcome(
        text=result.answer,
        degraded=False,
        sources=list(result.sources),
        citation_count=len(result.citations),
        dropped_citations=result.dropped_citations,
    )


__all__ = [
    "ACTION_SELECT_TASK",
    "FeishuQaOutcome",
    "answer_ai",
    "list_candidate_tasks",
]
