"""P1-3 —— 执行类指令（催办 / 导出 / 统计）。

与只读工具（``agent_tools.py``）的关键区别
--------------------------------------
执行类工具会**改变外部状态**（发通知、导出文件、生成统计），因此必须：

1. **两阶段执行**：``prepare`` 只生成**执行计划**并返回一次性确认令牌；
   ``confirm`` 才真正落库/投递。令牌一次性、短时效、用后即焚。
2. **显式确认**：没有确认令牌，任何执行类调用都不会产生副作用。
3. **独立授权**：执行类工具用独立的 ``exec:*`` 授权名单，
   与只读工具的授权互不串用 —— 拿到只读令牌不能执行写操作。
4. **全量审计**：``prepare`` 与 ``confirm`` 都写 ``AiToolCall`` 审计。

之所以要两阶段而不是一次调用直接执行：Agent 生成的指令参数可能理解错误
（催办错了人、导出了错的范围）。把参数显式回显给用户确认，是把
「机器误解」变成「人可拦截」的最低成本防线。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from . import access_control

logger = logging.getLogger(__name__)

#: 执行类工具名单（与只读工具分离）
EXEC_TOOLS = ("exec_remind", "exec_export", "exec_stats")

#: 确认令牌有效期（秒）
CONFIRM_TTL_SECONDS = 300

#: 确认令牌签名密钥派生材料（不新增配置项）
_KEY_MATERIAL = b"agent-exec-confirm-v1"


class ExecToolError(Exception):
    """执行类工具失败。``status_code`` 决定 HTTP 响应码。"""

    status_code = 400

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ExecPlan:
    """执行计划（``prepare`` 阶段产物）。

    Attributes:
        tool: 工具名
        summary: 人类可读的「将要做什么」摘要
        impact: 受影响对象清单（员工 / 部门）
        confirm_token: 一次性确认令牌
        expires_at: 令牌过期时间戳
        params: 执行参数
    """

    tool: str
    summary: str
    impact: list[dict[str, Any]]
    confirm_token: str
    expires_at: int
    params: dict[str, Any]


def _sign(encoded: str) -> str:
    """对确认令牌载荷签名（HMAC-SHA256）。"""
    from ..core.config import settings

    if not settings.agent_tool_secret:
        raise ExecToolError("工具网关未配置", 503)
    material = settings.agent_tool_secret.encode() + _KEY_MATERIAL
    return hmac.new(material, encoded.encode("ascii"), hashlib.sha256).hexdigest()


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _unb64(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()


def issue_confirm_token(
    *, tool: str, employee_id: str, params: dict[str, Any]
) -> tuple[str, int]:
    """签发一次性确认令牌。

    Returns:
        ``(token, expires_at)``
    """
    now = int(time.time())
    expires_at = now + CONFIRM_TTL_SECONDS
    payload = {
        "tool": tool,
        "sub": employee_id,
        "params": params,
        "nonce": secrets.token_hex(8),
        "iat": now,
        "exp": expires_at,
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return f"{encoded}.{_sign(encoded)}", expires_at


def verify_confirm_token(
    token: str, *, tool: str, employee_id: str
) -> dict[str, Any]:
    """校验确认令牌并返回载荷。

    Raises:
        ExecToolError: 令牌无效 / 过期 / 工具不匹配 / 归属不符。
    """
    if not isinstance(token, str) or len(token) > 8192 or token.count(".") != 1:
        raise ExecToolError("确认令牌无效")
    encoded, signature = token.split(".")
    if not hmac.compare_digest(signature, _sign(encoded)):
        raise ExecToolError("确认令牌无效")
    try:
        payload = json.loads(_unb64(encoded))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ExecToolError("确认令牌无效") from exc
    if not isinstance(payload, dict) or payload.get("exp", 0) <= int(time.time()):
        raise ExecToolError("确认令牌已过期，请重新发起", 409)
    if payload.get("tool") != tool:
        raise ExecToolError("确认令牌与工具不匹配", 409)
    if payload.get("sub") != employee_id:
        raise ExecToolError("确认令牌不属于当前用户", 403)
    return payload


def exec_scope(tool: str) -> str:
    """执行类工具的授权键 —— 与只读工具名单互斥。"""
    return f"exec:{tool}"


# ---------------------------------------------------------------------------
# 催办
# ---------------------------------------------------------------------------


def prepare_remind(
    db: Session, *, employee_id: str, task_id: str, targets: list[str], message: str = ""
) -> ExecPlan:
    """准备「催办」指令。

    Args:
        targets: 被催办的员工 ID 列表（不能包含自己）。

    Raises:
        ExecToolError: 目标非法（含自己 / 越权 / 不存在 / 不在考核范围）。
    """
    from ..domain.models import AssessmentRecord, AssessmentTask, Employee
    from ..services.access_control import build_access_context

    if not targets:
        raise ExecToolError("催办目标不能为空")
    if employee_id in targets:
        raise ExecToolError("不能催办自己")

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise ExecToolError("当前用户不存在")
    context = build_access_context(db, employee)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise ExecToolError("考核任务不存在")

    impact: list[dict[str, Any]] = []
    for target_id in dict.fromkeys(targets):
        target = db.get(Employee, target_id)
        if target is None:
            raise ExecToolError(f"催办对象不存在：{target_id}")
        # ⚠️ P0-4a：催办是**写操作 + 发通知** ⇒ 用写权限。
        #    用读权限的话，同部门平级一旦获得 peer 读权限，
        #    就能互相催办绩效待办 —— 骚扰且越权。
        if not context.can_write_score_of(target_id, db=db):
            raise ExecToolError(f"无权催办该员工：{target_id}", 403)
        record = db.query(AssessmentRecord).filter_by(
            task_id=task_id, employee_id=target_id
        ).first()
        if record is None:
            raise ExecToolError(f"该员工不在本次考核范围内：{target_id}")
        impact.append(
            {
                "employee_id": target_id,
                "employee_name": target.name,
                "record_id": record.id,
                "status": record.status,
            }
        )

    params = {
        "task_id": task_id,
        "targets": [i["employee_id"] for i in impact],
        "message": message,
    }
    token, expires = issue_confirm_token(
        tool="exec_remind", employee_id=employee_id, params=params
    )
    return ExecPlan(
        tool="exec_remind",
        summary=f"将向 {len(impact)} 位员工发送考核催办提醒",
        impact=impact,
        confirm_token=token,
        expires_at=expires,
        params=params,
    )


def execute_remind(
    db: Session, *, employee_id: str, task_id: str, targets: list[str], message: str = ""
) -> dict[str, Any]:
    """执行催办（只在确认后调用）。

    复用通知服务的每日频控 —— 同一天同一人同一条催办只发一次。
    """
    from ..domain.models import AssessmentTask
    from ..services.notification_service import enqueue_new

    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise ExecToolError("考核任务不存在")
    sent = 0
    skipped = 0
    today = time.strftime("%Y-%m-%d")
    for target_id in targets:
        row = enqueue_new(
            db,
            employee_id=target_id,
            scene="N11",
            period=task.period,
            object_id=f"{task_id}:{target_id}",
            object_type="assessment_record",
            label=task.name,
            event_key=f"{today}:{task_id}:{target_id}:exec_remind",
        )
        if row is None:
            skipped += 1
        else:
            sent += 1
    return {"sent": sent, "skipped": skipped, "task_id": task_id, "message": message}


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def prepare_export(
    db: Session, *, employee_id: str, task_id: str, scope: str = "self"
) -> ExecPlan:
    """准备「导出」指令。

    Args:
        scope: ``self``（本人） / ``department``（本部门，需管理权限）。
    """
    from ..domain.models import AssessmentTask, Employee
    from ..services.access_control import build_access_context

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise ExecToolError("当前用户不存在")
    context = build_access_context(db, employee)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise ExecToolError("考核任务不存在")

    impact: list[dict[str, Any]] = []
    if scope == "department":
        department = employee.department_id
        if not department or department not in context.managed_department_ids:
            raise ExecToolError("无权导出部门数据", 403)
        impact.append({"department_id": department})
    elif scope == "self":
        impact.append({"employee_id": employee_id, "employee_name": employee.name})
    else:
        raise ExecToolError("导出范围非法")

    params = {"task_id": task_id, "scope": scope}
    token, expires = issue_confirm_token(
        tool="exec_export", employee_id=employee_id, params=params
    )
    return ExecPlan(
        tool="exec_export",
        summary=(
            "将导出本人在该考核任务下的绩效数据"
            if scope == "self"
            else f"将导出部门 {employee.department_id} 在该考核任务下的绩效数据"
        ),
        impact=impact,
        confirm_token=token,
        expires_at=expires,
        params=params,
    )


def execute_export(
    db: Session, *, employee_id: str, task_id: str, scope: str = "self"
) -> dict[str, Any]:
    """执行导出（只在确认后调用）。

    返回脱敏后的结构化数据；文件渲染由上层负责。
    """
    from ..domain.models import AssessmentRecord, AssessmentTask, Employee, Score
    from ..services import privacy
    from ..services.access_control import build_access_context

    employee = db.get(Employee, employee_id)
    context = build_access_context(db, employee)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise ExecToolError("考核任务不存在")

    query = (
        db.query(AssessmentRecord, Employee, Score)
        .join(Employee, Employee.id == AssessmentRecord.employee_id)
        .join(Score, Score.record_id == AssessmentRecord.id)
        .filter(AssessmentRecord.task_id == task_id)
    )
    if scope == "self":
        query = query.filter(AssessmentRecord.employee_id == employee_id)
    else:
        department = employee.department_id
        if not department or department not in context.managed_department_ids:
            raise ExecToolError("无权导出部门数据", 403)
        query = query.filter(Employee.department_id == department)

    rows = []
    for record, emp, score in query.all():
        rows.append(
            {
                "employee_id": emp.id,
                "employee_name": emp.name,
                "record_id": record.id,
                "total": score.total,
                "rating": (score.trace_json or {}).get("scheme_rating")
                or record.confirmed_rating,
                "status": record.status,
            }
        )
    # 导出内容同样走脱敏，避免敏感字段外泄。
    return privacy.redact_obj({"task_id": task_id, "scope": scope, "rows": rows})


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def prepare_stats(
    db: Session, *, employee_id: str, task_id: str, scope: str = "department"
) -> ExecPlan:
    """准备「统计」指令（部门分布统计）。"""
    from ..domain.models import AssessmentTask, Employee
    from ..services.access_control import build_access_context

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise ExecToolError("当前用户不存在")
    context = build_access_context(db, employee)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise ExecToolError("考核任务不存在")
    if scope != "department":
        raise ExecToolError("统计范围非法")

    department = employee.department_id
    if not department or not (
        department in context.managed_department_ids
        or context.has_permission(access_control.PermissionAction.VIEW_ALL_SCORES)
    ):
        raise ExecToolError("无权统计部门数据", 403)

    params = {"task_id": task_id, "scope": scope}
    token, expires = issue_confirm_token(
        tool="exec_stats", employee_id=employee_id, params=params
    )
    return ExecPlan(
        tool="exec_stats",
        summary=f"将统计部门 {department} 在该考核任务下的绩效分布",
        impact=[{"department_id": department}],
        confirm_token=token,
        expires_at=expires,
        params=params,
    )


def execute_stats(
    db: Session, *, employee_id: str, task_id: str, scope: str = "department"
) -> dict[str, Any]:
    """执行统计（只在确认后调用）。"""
    from ..domain.models import AssessmentTask, Employee
    from ..services import privacy
    from ..services.access_control import build_access_context

    employee = db.get(Employee, employee_id)
    context = build_access_context(db, employee)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise ExecToolError("考核任务不存在")
    department = employee.department_id
    if not department or not (
        department in context.managed_department_ids
        or context.has_permission(access_control.PermissionAction.VIEW_ALL_SCORES)
    ):
        raise ExecToolError("无权统计部门数据", 403)

    from ..services.agent_tool_service import load_department_stats

    data = load_department_stats(db, department_id=department, task_id=task_id)
    return privacy.redact_obj(data)
