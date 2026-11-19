"""飞书只读绩效解释：复用问答权限与业务工具，不依赖模型可用性。"""

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AssessmentRecord, AssessmentTask, Employee
from . import agent_tool_service, privacy, qa_permission
from .period import period_match_keys


def answer(db: Session, employee: Employee, question: str, period: str) -> str:
    if employee.employment_status != "active" or employee.account_status != "enabled":
        raise HTTPException(403, "账号当前不可查询")
    # 从可信员工身份匹配任务，不接受消息中指定其他员工身份。
    tasks = db.scalars(select(AssessmentTask).join(
        AssessmentRecord, AssessmentRecord.task_id == AssessmentTask.id,
    ).where(
        AssessmentRecord.employee_id == employee.id,
        AssessmentTask.period.in_(period_match_keys(period)),
    ).order_by(AssessmentTask.created_at.desc()).limit(2)).all()
    if not tasks:
        return f"未找到你在 {period} 的考核任务，请确认月份，或联系 HR 分配考核任务。"
    if len(tasks) > 1:
        return f"你在 {period} 有多个考核任务，请到网页后台选择具体任务后查询，避免混用计算依据。"
    task = tasks[0]
    scope = qa_permission.resolve_scope(db, employee, task.id, period)
    team = any(word in question for word in ("本组", "团队", "部门"))
    if team and "get_department_stats" not in scope.allowed_tools:
        raise HTTPException(403, "无权查询部门绩效数据")
    if team:
        data = agent_tool_service.load_department_stats(db, department_id=employee.department_id, task_id=task.id)
        note = "当前提供部门汇总，尚不支持异常人员名单；不能据此判断个人评分异常。"
        source = "部门绩效汇总（仅当前主部门及当前任务）"
    else:
        if "get_metric_values" not in scope.allowed_tools:
            raise HTTPException(403, "无权查询该任务指标")
        data = {
            "指标与适用等级规则": agent_tool_service.load_metric_values(db, employee_id=employee.id, task=task),
            "评分证据": agent_tool_service.load_evidence(db, employee_id=employee.id, task=task, usage=None),
        }
        note = "以下为系统规则计算及证据查询结果。指标等级不等于已确认的最终成绩；未提供的扣分原因不作推断。"
        source = "任务冻结配置、周期指标、当前任务可见证据；考勤参考材料不直接作为扣分依据"
    # 纯文本展示，避免把证据或用户内容当成卡片 Markdown 指令。
    detail = json.dumps(privacy.redact_obj(data), ensure_ascii=False, indent=2)
    if len(detail) > 6500:
        detail = detail[:6500] + "\n（内容已截断，请到网页后台查看完整明细）"
    return f"周期：{scope.period}\n任务：{task.name}\n规则快照：{task.snapshot_id or '暂无'}\n\n{note}\n\n数据来源：{source}\n\n{detail}"
