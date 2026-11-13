"""自定义异常。

这里定义所有业务异常，对应各项设计决策和错误场景。
"""

from __future__ import annotations

from typing import Any, Optional


class BaseAppError(Exception):
    """所有业务异常的基类。
    
    FastAPI 中间件会捕获这个异常，转成 HTTPException。
    """
    
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[dict] = None
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


# ┌─────────────────────────────────────────────────────┐
# │ 决策相关异常                                        │
# └─────────────────────────────────────────────────────┘


class AttendanceEvidenceRejected(BaseAppError):
    """决策②：考勤异常证据不能进入算分集。
    
    当：
    - 尝试添加考勤异常作为指标值
    - 尝试用考勤异常支持某个评分
    
    场景：
    - 员工请假 → 仅记录，不计分
    - 加班 → 仅参考，不计分
    - 迟到 → 仅参考，不计分
    """
    
    def __init__(self, attendance_type: Optional[str] = None):
        message = f"考勤异常({attendance_type})" if attendance_type else "考勤异常"
        message += "仅作参考，不能进入评分计算集合"
        super().__init__(
            code="ATTENDANCE_REJECTED",
            message=message,
            status_code=400,
            details={"attendance_type": attendance_type}
        )


class ModelNotTrusted(BaseAppError):
    """决策⑤：模型无法自行决策权限、等级等敏感事项。
    
    当：
    - AI 尝试覆盖用户权限
    - AI 尝试直接修改分数
    - AI 尝试作为唯一依据做权限检查
    
    场景：
    - AI 建议"查看员工 A 的数据" → 被拒绝，因为权限由 L2 决定
    - AI 生成的等级作为 Score.rating → 被拒绝，因为分数由规则引擎产出
    """
    
    def __init__(self, detail: str, ai_output: Optional[str] = None):
        super().__init__(
            code="MODEL_NOT_TRUSTED",
            message=f"AI 建议不可直接使用：{detail}",
            status_code=400,
            details={"ai_output": ai_output, "reason": detail}
        )


class GapDetected(BaseAppError):
    """关键指标缺失。
    
    当：
    - 必需指标没有数据
    - 指标指向的证据不存在
    
    场景：
    - 销售额指标指向证据 E001，但 E001 不存在 → 降级处理
    - 关键指标（weight > 0.1）缺失 → 提示 HR
    """
    
    def __init__(self, metric_code: str, reason: str):
        super().__init__(
            code="GAP_DETECTED",
            message=f"指标 {metric_code} 缺失数据：{reason}",
            status_code=422,
            details={"metric_code": metric_code, "reason": reason}
        )


# ┌─────────────────────────────────────────────────────┐
# │ 权限相关异常                                        │
# └─────────────────────────────────────────────────────┘


class PermissionDenied(BaseAppError):
    """用户无权执行该操作。
    
    参考 P0-3 权限模型：
    - 权限来自 token（可信）
    - 不信任 URL 参数或 Body 中的 employee_id
    - 权限检查在 L2 业务层，不让 L3 AI 参与
    """
    
    def __init__(
        self,
        user_id: str,
        action: str,
        resource: Optional[str] = None
    ):
        resource_str = f" {resource}" if resource else ""
        message = f"用户 {user_id} 无权执行 {action}{resource_str}"
        super().__init__(
            code="PERMISSION_DENIED",
            message=message,
            status_code=403,
            details={
                "user_id": user_id,
                "action": action,
                "resource": resource
            }
        )


class NotAuthenticated(BaseAppError):
    """用户未认证。"""
    
    def __init__(self, reason: str = "Token 无效或过期"):
        super().__init__(
            code="NOT_AUTHENTICATED",
            message=reason,
            status_code=401
        )


# ┌─────────────────────────────────────────────────────┐
# │ 数据相关异常                                        │
# └─────────────────────────────────────────────────────┘


class DataValidationError(BaseAppError):
    """数据验证失败。"""
    
    def __init__(self, detail: str, field: Optional[str] = None):
        message = f"数据验证失败"
        if field:
            message += f"（字段 {field}）"
        message += f"：{detail}"
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=422,
            details={"field": field, "detail": detail}
        )


class RecordNotFound(BaseAppError):
    """记录不存在。"""
    
    def __init__(self, entity_type: str, entity_id: str):
        super().__init__(
            code="NOT_FOUND",
            message=f"{entity_type} {entity_id} 不存在",
            status_code=404,
            details={"entity_type": entity_type, "entity_id": entity_id}
        )


class DuplicateRecord(BaseAppError):
    """记录重复。"""
    
    def __init__(self, entity_type: str, key: str, value: Any):
        super().__init__(
            code="DUPLICATE",
            message=f"{entity_type} 的 {key}={value} 已存在",
            status_code=409,
            details={"entity_type": entity_type, "key": key, "value": value}
        )


# ┌─────────────────────────────────────────────────────┐
# │ 配置相关异常                                        │
# └─────────────────────────────────────────────────────┘


class ConfigurationError(BaseAppError):
    """配置错误。
    
    参考决策③：配置检验的三道保险。
    """
    
    def __init__(self, detail: str, field: Optional[str] = None):
        message = f"配置错误"
        if field:
            message += f"（{field}）"
        message += f"：{detail}"
        super().__init__(
            code="CONFIG_ERROR",
            message=message,
            status_code=500,
            details={"field": field, "detail": detail}
        )


class RubricNotFound(BaseAppError):
    """rubric（分级规则）不存在或损坏。"""
    
    def __init__(self, metric_code: str):
        super().__init__(
            code="RUBRIC_NOT_FOUND",
            message=f"指标 {metric_code} 的 rubric 不存在或格式错误",
            status_code=500,
            details={"metric_code": metric_code}
        )


# ┌─────────────────────────────────────────────────────┐
# │ 业务流程异常                                        │
# └─────────────────────────────────────────────────────┘


class InvalidStateTransition(BaseAppError):
    """无效的状态转移。
    
    如：
    - 已确认的 Record 尝试重新自评
    - pending 状态的 Task 尝试关闭
    """
    
    def __init__(
        self,
        entity_type: str,
        current_state: str,
        attempted_action: str
    ):
        message = (
            f"{entity_type} 处于 {current_state} 状态，"
            f"不能执行 {attempted_action}"
        )
        super().__init__(
            code="INVALID_STATE_TRANSITION",
            message=message,
            status_code=409,
            details={
                "entity_type": entity_type,
                "current_state": current_state,
                "attempted_action": attempted_action
            }
        )


class DeadlineExceeded(BaseAppError):
    """截止期限已过。"""
    
    def __init__(self, deadline_type: str, deadline: str):
        super().__init__(
            code="DEADLINE_EXCEEDED",
            message=f"{deadline_type} 截止期限为 {deadline}，已过期",
            status_code=409,
            details={"deadline_type": deadline_type, "deadline": deadline}
        )


class DowngradedMode(BaseAppError):
    """系统降级模式。
    
    当：
    - AI 服务不可用
    - 某个关键依赖挂了
    
    处理：
    - 返回结构化数据
    - 不尝试 AI 分析
    - 标注 data_quality.degraded = True
    """
    
    def __init__(self, reason: str, impact: Optional[str] = None):
        message = f"系统降级：{reason}"
        if impact:
            message += f"。影响：{impact}"
        super().__init__(
            code="DEGRADED_MODE",
            message=message,
            status_code=503,
            details={"reason": reason, "impact": impact}
        )


# ┌─────────────────────────────────────────────────────┐
# │ 隐私相关异常（P0-4）                               │
# └─────────────────────────────────────────────────────┘


class KAnonymityViolation(BaseAppError):
    """k-匿名 破裂。
    
    参考 P2-1：
    - 部门 < 5 人，不能显示平均分
    - 某些查询会暴露个人信息
    """
    
    def __init__(self, detail: str):
        super().__init__(
            code="K_ANONYMITY_VIOLATION",
            message=f"隐私保护冲突：{detail}。数据已脱敏或隐藏。",
            status_code=200,  # 返回 200 但标注降级
            details={"detail": detail}
        )


# ┌─────────────────────────────────────────────────────┐
# │ 工具函数                                            │
# └─────────────────────────────────────────────────────┘


def exception_to_dict(exc: BaseAppError) -> dict:
    """将异常转成 API 响应字典。"""
    return {
        "code": exc.code,
        "message": exc.message,
        "details": exc.details,
        "status_code": exc.status_code
    }
