"""问答范围与已落地工具能力一致：本人及有权限的当前主部门。"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domain.models import AssessmentRecord, AssessmentTask, Employee
from .access_control import PermissionAction, build_access_context
from .period import normalize_period


@dataclass(frozen=True)
class QaScope:
    scope: str
    allowed_tools: tuple[str, ...]
    period: str


def resolve_scope(db: Session, employee: Employee, task_id: str, period: str | None) -> QaScope:
    if employee.employment_status != "active" or employee.account_status != "enabled":
        raise HTTPException(403, "账号当前不可查询")
    context = build_access_context(db, employee)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise HTTPException(404, "考核任务不存在")
    canonical = normalize_period(task.period)
    if canonical is None or (period is not None and normalize_period(period) != canonical):
        raise HTTPException(400, "请求周期与任务不一致")
    tools = []
    if context.can_view_score_of(employee.id) and db.query(AssessmentRecord.id).filter_by(
        task_id=task.id, employee_id=employee.id,
    ).first():
        tools.extend(["get_metric_values", "get_evidence", "get_scoring_breakdown"])
    department_allowed = bool(employee.department_id and (
        employee.department_id in context.managed_department_ids
        or context.has_permission(PermissionAction.VIEW_ALL_SCORES)
    ) and (task.department_id is None or task.department_id == employee.department_id))
    if department_allowed:
        tools.append("get_department_stats")
    if not tools:
        raise HTTPException(403, "无权查询该任务数据")
    return QaScope("department" if department_allowed else "self", tuple(tools), canonical)
