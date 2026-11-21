"""问答入口：短时凭据、只读预取、异步编排和确定性降级。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import AssessmentRecord, AssessmentTask, Department, Employee
from ..services import qa_chat_service, qa_permission
from ..services.access_control import PermissionAction, build_access_context

# ⚠️ 从 `qa_chat_service` 转出 —— 这里是历史位置，外部有引用。
#    定义本身搬到服务层了（飞书侧也要用同一套响应校验）。
AgentQAResponse = qa_chat_service.AgentQAResponse

router = APIRouter(prefix="/v1/qa", tags=["qa"])


class QAAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    task_id: str = Field(min_length=1, max_length=64)
    period: str | None = Field(default=None, max_length=10)
    #: ★ P0-4b：查询目标人（缺省 = 提问者本人）。
    #:
    #: ⚠️ 这**不是**"想查谁就查谁" ——
    #:    它会走 ``qa_authorization.authorize_qa_subject`` 的**硬闸门**：
    #:    同部门平级同事放行，跨部门/跨级/上级一律拒绝。
    #:    拒绝时文案统一为「不在可见范围内」，不泄露目标人信息。
    subject_id: str | None = Field(default=None, min_length=1, max_length=64)


@router.get("/tasks")
def list_qa_tasks(
    current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """列出当前用户**有权问答**的考核任务。

    ⚠️ 为什么必须有这个接口：
    ``POST /v1/qa/ask`` 要求前端传 ``task_id``，但前端不知道
    「我有哪些任务能问」。没有这个接口，前端只能：
      ① 让用户手填 task_id（没人记得住），或
      ② 拉全部任务再逐个试探（既慢又越权）。

    这里**按权限收敛**后才返回 —— 每个任务都经过
    ``qa_permission.resolve_scope`` 判定，能问才列出来。
    拿不到权限的任务**根本不出现**在列表里。

    返回的 ``allowed_tools`` 是这个任务下**实际**可用的工具，
    前端据此提示「这个问题可能查不到」而不是等后端 403。
    """
    if current_user.employment_status != "active" or current_user.account_status != "enabled":
        raise HTTPException(403, "账号当前不可查询")

    # 候选任务 = 我有考核记录的 ∪ 我管辖部门成员有考核记录的 ∪ 显式挂在我管辖部门下的
    #
    # ⚠️ 为什么第三种也要查 `AssessmentTask.department_id`：
    # 任务表允许 `department_id` 为 NULL（全员任务）。而真实库里
    # 23 个任务的 `department_id` **全是 NULL** —— 只按这一列查，
    # 管理者会拿到 0 个任务，问答入口对 HR/主管直接不可用。
    #
    # 所以候选集必须同时包含「部门成员参与的���务」。
    # 最终是否可问仍由下面的 `resolve_scope` 逐个判定 ——
    # 这里只是**扩大召回**，不放松权限。
    context = build_access_context(db, current_user)
    own_task_ids = set(
        db.scalars(
            select(AssessmentRecord.task_id).where(
                AssessmentRecord.employee_id == current_user.id
            )
        ).all()
    )
    managed = context.managed_department_ids
    dept_task_ids: set[str] = set()
    if managed:
        # ① 显式挂在我管辖部门下的任务
        dept_task_ids |= set(
            db.scalars(
                select(AssessmentTask.id).where(
                    AssessmentTask.department_id.in_(managed)
                )
            ).all()
        )
        # ② 我管辖部门的**成员**参与的任务（含 department_id 为 NULL 的全员任务）
        dept_task_ids |= set(
            db.scalars(
                select(AssessmentRecord.task_id)
                .join(Employee, Employee.id == AssessmentRecord.employee_id)
                .where(Employee.department_id.in_(managed))
                .distinct()
            ).all()
        )
    if context.has_permission(PermissionAction.VIEW_ALL_SCORES):
        # ③ HR（VIEW_ALL_SCORES）可看全员 —— 候选集取全库有考核记录的任务。
        #
        # ⚠️ 这里只是**扩大召回**，不放松权限：最终能不能问，
        #    仍由下面的 `resolve_scope` 用同一套规则逐个判定。
        #    候选集大了只会多跑几次判定，不会多给数据。
        dept_task_ids |= set(
            db.scalars(
                select(AssessmentRecord.task_id).distinct()
            ).all()
        )

    candidate_ids = own_task_ids | dept_task_ids
    if not candidate_ids:
        return {"code": 0, "data": {"items": []}}

    tasks = db.scalars(
        select(AssessmentTask).where(AssessmentTask.id.in_(candidate_ids))
    ).all()

    items: list[dict[str, Any]] = []
    for task in tasks:
        try:
            scope = qa_permission.resolve_scope(db, current_user, task.id, None)
        except HTTPException:
            # 无权问答的任务**不出现**在列表里（而不是标个「无权限」）。
            continue
        items.append({
            "task_id": task.id,
            "name": task.name,
            "period": scope.period,
            "status": task.status,
            "scope": scope.scope,
            "allowed_tools": list(scope.allowed_tools),
            "has_snapshot": bool(task.snapshot_id),
            "created_at": task.created_at.isoformat() if task.created_at else None,
        })

    # ⚠️ 同名任务必须能区分 —— 否则下拉框里会出现一排一模一样的选项，
    #    用户无从选择（「这五个『2026 年 11 月月度考核』哪个是我要的？」）。
    #
    #    真实库里 23 个任务同名就是活生生的例子（测试造数 + 真实业务同名都会发生）。
    #    所以这里按「同名 → 加后缀」生成 `label`，而不是让前端自己拼：
    #    前端拿不到 `status` 的中文语义，也判断不了哪个才是"本期"。
    #
    # ⚠️ **排序必须在标注之前** —— label 里的「第 N 项」要跟列表里的
    #    出现顺序一致（第 1 项 = 列表里第一个 = 最新的那个）。
    #    否则会出现「第 22 项」排在最前面这种看不懂的标签。
    #
    # 最近创建的排前面，方便用户直接看到「本期」。
    items.sort(key=lambda x: (x["created_at"] or ""), reverse=True)
    _decorate_labels(items, db)
    return {"code": 0, "data": {"items": items}}


# 任务状态 → 中文（前端直接用，避免各处再维护一份映射）。
_TASK_STATUS_LABELS: dict[str, str] = {
    "draft": "草稿",
    "created": "已创建",
    "in_progress": "进行中",
    "pending_confirm": "待确认",
    "confirmed": "已确认",
    "archived": "已归档",
    "closed": "已结束",
}


def _decorate_labels(items: list[dict[str, Any]], db: Session) -> None:
    """给每个任务生成**人类可区分**的 `label`（就地写入）。

    ⚠️ 只在**同名**时才加后缀 —— 名字本来就唯一的任务保持干净，
    免得正常场景下标签变得又长又丑。

    后缀按「能唯一确定任务」的维度依次尝试：
      ① 所属部门名（部门任务最有业务含义）
      ② 状态（进行中 / 已确认 …）
      ③ 创建时间（精确到秒）
      ④ 短 ID（最后兜底）

    ⚠️ **必须保证同名组的 label 互不相同** —— 这是本函数的硬性契约。
    后缀是「尽量 informative」，但**区分性是第一优先级**：
    真实库里有 23 个同名任务，且它们的 ID 都是 `TASK_<时间戳>_<周期>`
    这种同前缀格式（取前 6 位全都是 `TASK_2`，毫无区分度），
    同一分钟内批量创建的任务连「分钟级时间戳」都一样。
    所以末尾用**组内序号**兜底 —— 它永远不会重复。
    """
    if not items:
        return

    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_name.setdefault(item["name"], []).append(item)

    # 同名组只有一个 → 名字本身就够区分，保持原样。
    unique_names = {name for name, group in by_name.items() if len(group) == 1}
    for name, group in by_name.items():
        if name in unique_names:
            for item in group:
                item["label"] = name
            continue

        # 预取部门名（只查需要区分的那几组涉及的部门）。
        dept_names = _department_names(db, [item["task_id"] for item in group])

        # 组内序号：保证 label 互不相同（见 docstring 的契约说明）。
        for index, item in enumerate(group, start=1):
            item["label"] = _build_label(item, dept_names, index)


def _department_names(db: Session, task_ids: list[str]) -> dict[str, str]:
    """task_id → 部门名（查不到就返回空表，不报错）。"""
    if not task_ids:
        return {}
    try:
        rows = db.execute(
            select(AssessmentTask.id, Department.name)
            .join(Department, Department.id == AssessmentTask.department_id)
            .where(AssessmentTask.id.in_(task_ids))
        ).all()
        return {row[0]: row[1] for row in rows}
    except Exception:  # noqa: BLE001 —— 部门表不可用不该让整个列表挂掉
        return {}


def _build_label(item: dict[str, Any], dept_names: dict[str, str], index: int) -> str:
    """拼出「名称 · 区分后缀」。

    ⚠️ `index` 是同名组内的序号（1 起）—— 它是**唯一性保证**，
    不能因为「前面已经有部门/状态/时间了」就省掉：
    同名任务可能**部门相同、状态相同、甚至同一秒创建**。
    """
    name = item["name"]
    parts: list[str] = []

    dept_name = dept_names.get(item["task_id"])
    if dept_name:
        parts.append(dept_name)

    status_text = _TASK_STATUS_LABELS.get(item["status"])
    if status_text:
        parts.append(status_text)

    # 同名任务往往连状态都一样 —— 这时用创建时间兜底（精确到秒）。
    created = item.get("created_at")
    if created:
        parts.append(created[:19].replace("T", " "))

    # 最后一节：组内序号。
    # ⚠️ 不能用「短 ID」代替 —— 真实库的 ID 都是 `TASK_<时间戳>_<周期>`，
    #    前 6 位恒为 `TASK_2`，完全无区分度。序号则永远唯一。
    parts.append(f"第 {index} 项")

    return f"{name} · {' · '.join(parts)}"


@router.post("/ask")
async def ask(
    req: QAAskRequest, current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict[str, Any]:
    """问答（网页端）。

    \u26a0\ufe0f **核心逻辑在 `qa_chat_service.ask_question_async`** \u2014\u2014
    \u98de\u4e66\u673a\u5668\u4eba\u8c03\u7684\u662f**\u540c\u4e00\u4e2a\u51fd\u6570**\uff0c\u6240\u4ee5\u4e24\u4e2a\u5165\u53e3\u7684
    \u6743\u9650\u6536\u655b\u3001\u5f15\u7528\u6821\u9a8c\u3001\u964d\u7ea7\u884c\u4e3a**\u5b8c\u5168\u4e00\u81f4**\uff0c\u4e0d\u4f1a\u6f02\u79fb\u3002
    """
    result = await qa_chat_service.ask_question_async(
        db,
        current_user,
        question=req.question,
        task_id=req.task_id,
        period=req.period,
        # ⚠️ 目标人 —— 授权判定在服务层做（后端硬闸门）。
        subject_id=req.subject_id,
        # ⚠️ P0-5：网页端**不持久化**会话上下文 ——
        #    网页端每次都是独立请求，没有"会话"这个概念。
        #    持久化由飞书端（``BotSession.qa_state``）负责。
        #    ⇒ 这里传 None ⇒ 不继承任何槽位（既有行为不变）。
        conversation=None,
    )
    return {
        "answer": result.answer,
        "citations": result.citations,
        "sources": result.sources,
        "data": result.data,
        "degraded": result.degraded,
        "follow_up_questions": result.follow_up_questions,
        "tool_calls": result.tool_calls,
        "trace_id": result.trace_id,
        "dropped_citations": result.dropped_citations,
        # ★ P0-5：把解析结果回给前端 —— 前端要显示"沿用上一轮：2026-10"。
        "resolved_subject_id": result.subject_id,
        "resolved_subject_name": result.subject_name,
        "period_inherited": result.period_inherited,
        "subject_inherited": result.subject_inherited,
        "data_quality": {
            "completeness": result.completeness,
            "as_of": datetime.now(UTC).isoformat(),
            "degraded": result.degraded,
            "degrade_reason": "\u5de5\u5177\u6216\u667a\u80fd\u4f53\u4e0d\u53ef\u7528" if result.degraded else None,
            "note": "\u5b8c\u6574\u5ea6\u8868\u793a\u53ef\u7528\u6570\u636e\u6e90\u6bd4\u4f8b\uff0c\u4e0d\u8868\u793a\u6307\u6807\u586b\u62a5\u5b8c\u6574\u7387",
        },
    }
