"""FastAPI 依赖注入层 —— 鉴权与权限控制。

这是权限、身份验证的关键。遵循 P0-3 原则：
- 身份来自 token（可信）
- 不信任 URL 参数或 Body 里的 employee_id
- 权限检查由 L2 业务层做，不让 L3 AI 参与

【板块1】实现内容：
1. get_current_user：Mock token解析，token硬编码映射员工对象（hr / manager / ordinary emp）
2. get_direct_reports：根据user_id查询数据库，返回直属下属id列表
3. check_permission：权限矩阵，区分普通员工/主管/HR三种角色权限
4. 封装工具函数：check_can_submit_review、check_can_view_score
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .core.db import get_db
from .domain import models
from .exceptions import (
    NotAuthenticated,
    PermissionDenied,
    RecordNotFound
)

logger = logging.getLogger(__name__)


# ┌─────────────────────────────────────────────────────┐
# │ Mock Token 数据库（板块0完成后可删除）              │
# └─────────────────────────────────────────────────────┘

# Mock 员工库：token -> (employee_id, role, name, manager_id, department_id)
# 规则：
#   - hr_token_XXX     → HR 角色
#   - manager_token_X  → 主管角色（有直属下级）
#   - emp_token_X      → 普通员工角色（无下级）
MOCK_TOKENS = {
    # HR 示例
    "hr_token_001": {
        "employee_id": "HR001",
        "name": "李红（HR总监）",
        "role": "hr",
        "position": "HR总监",
        "manager_id": None,
        "department_id": "DEPT_HR",
    },
    
    # 主管示例 1：M001 是 E001/E002 的直属主管
    "manager_token_001": {
        "employee_id": "M001",
        "name": "王伟（销售经理）",
        "role": "manager",
        "position": "销售经理",
        "manager_id": "M002",  # M001 的上级是 M002（部门经理）
        "department_id": "DEPT_SALES",
    },
    
    # 主管示例 2：M002 是 M001 的直属主管（部门经理）
    "manager_token_002": {
        "employee_id": "M002",
        "name": "张文（销售总监）",
        "role": "manager",
        "position": "销售总监",
        "manager_id": None,
        "department_id": "DEPT_SALES",
    },
    
    # 普通员工示例 1：E001 由 M001 管理
    "emp_token_001": {
        "employee_id": "E001",
        "name": "刘刚（销售代表）",
        "role": "ordinary_employee",
        "position": "销售代表",
        "manager_id": "M001",
        "department_id": "DEPT_SALES",
    },
    
    # 普通员工示例 2：E002 由 M001 管理
    "emp_token_002": {
        "employee_id": "E002",
        "name": "赵燕（销售代表）",
        "role": "ordinary_employee",
        "position": "销售代表",
        "manager_id": "M001",
        "department_id": "DEPT_SALES",
    },
    
    # 普通员工示例 3：E003 由 M002 管理
    "emp_token_003": {
        "employee_id": "E003",
        "name": "陈晶（财务主管）",
        "role": "ordinary_employee",
        "position": "财务主管",
        "manager_id": "M002",
        "department_id": "DEPT_FINANCE",
    },
}


# ┌─────────────────────────────────────────────────────┐
# │ 权限角色枚举                                        │
# └─────────────────────────────────────────────────────┘

class UserRole(StrEnum):
    """用户角色（从 token 中解析）。"""
    HR = "hr"
    MANAGER = "manager"
    ORDINARY_EMPLOYEE = "ordinary_employee"


# ┌─────────────────────────────────────────────────────┐
# │ 权限矩阵（check_permission 的核心）                │
# └─────────────────────────────────────────────────────┘

# 权限矩阵：(role, action) -> True/False
# action 的取值：
#   - "view_own_score": 查看自己的成绩
#   - "view_direct_report_score": 查看直属下级的成绩
#   - "view_all_scores": 查看所有员工的成绩（包括跨部门）
#   - "submit_own_review": 提交自己的自评
#   - "submit_direct_report_review": 提交直属下级的评分
#   - "confirm_rating": 确认最终等级
#   - "add_metric_to_record": 添加指标到考核记录
#   - "view_audit_log": 查看操作日志

PERMISSION_MATRIX = {
    UserRole.ORDINARY_EMPLOYEE: {
        "view_own_score": True,
        "view_direct_report_score": False,
        "view_all_scores": False,
        "submit_own_review": True,
        "submit_direct_report_review": False,
        "confirm_rating": False,
        "add_metric_to_record": False,
        "view_audit_log": False,
    },
    UserRole.MANAGER: {
        "view_own_score": True,
        "view_direct_report_score": True,
        "view_all_scores": False,
        "submit_own_review": True,
        "submit_direct_report_review": True,
        "confirm_rating": False,  # 只有部门经理或 HR 才能
        "add_metric_to_record": True,  # 可以补充直属下级的指标
        "view_audit_log": False,
    },
    UserRole.HR: {
        "view_own_score": True,
        "view_direct_report_score": True,
        "view_all_scores": True,
        "submit_own_review": True,
        "submit_direct_report_review": True,
        "confirm_rating": True,
        "add_metric_to_record": True,
        "view_audit_log": True,
    },
}


# ┌─────────────────────────────────────────────────────┐
# │ 身份认证（token → employee_id）                    │
# └─────────────────────────────────────────────────────┘

async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> models.Employee:
    """从 token 提取 employee_id 并验证。
    
    关键原则（P0-3）：
    - token 由 L2 鉴权层签发，是可信的
    - employee_id 硬写在 token 里，不让前端/AI 决定
    - 即使请求中有其他 employee_id，也全部忽略
    
    参数：
    - authorization: HTTP 请求头 "Authorization: Bearer <token>"
    
    返回：
    - Employee 对象（包含 id, name, department_id, manager_id 等）
    
    抛出：
    - NotAuthenticated：token 无效或过期
    
    【板块1】实现：Mock 版本，从硬编码 token 解析用户信息。
    """
    
    if not authorization:
        raise NotAuthenticated("缺少 Authorization header")
    
    # 提取 token（格式：Bearer <token>）
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise NotAuthenticated("Authorization header 格式错误，应为 'Bearer <token>'")
    
    token = parts[1]
    
    # 从 Mock 数据库中查询
    token_data = MOCK_TOKENS.get(token)
    if not token_data:
        logger.warning(f"Invalid token received: {token}")
        raise NotAuthenticated(f"Token 无效或已过期")
    
    employee_id = token_data["employee_id"]
    
    # 从数据库查询员工对象
    # 如果数据库中没有这个员工，则创建一个临时对象（模拟场景）
    employee = db.query(models.Employee).filter_by(id=employee_id).first()
    
    if not employee:
        # Mock 场景下可以创建，实际生产环境应该拒绝
        logger.info(f"创建 Mock 员工: {employee_id} ({token_data['name']})")
        employee = models.Employee(
            id=employee_id,
            name=token_data["name"],
            position=token_data.get("position"),
            manager_id=token_data.get("manager_id"),
            department_id=token_data.get("department_id"),
            employment_status="active"
        )
        db.add(employee)
        db.commit()
        db.refresh(employee)
    
    logger.info(f"Auth success: {employee.name} ({employee.id})")
    return employee


# ┌─────────────────────────────────────────────────────┐
# │ 权限检查（employee → 可操作的目标）                │
# └─────────────────────────────────────────────────────┘

def _get_user_role(user: models.Employee) -> UserRole:
    """根据员工的 position 和 manager_id 推断角色。
    
    规则：
    - position 包含 "HR" → HR 角色
    - 有直属下级 → manager 角色
    - 否则 → ordinary_employee 角色
    
    【板块1】实现：Mock 版本。
    """
    # 先查 Mock 数据中的角色
    for token_data in MOCK_TOKENS.values():
        if token_data["employee_id"] == user.id:
            return UserRole(token_data["role"])
    
    # 如果不在 Mock 中，根据 position 推断
    if user.position and "HR" in user.position:
        return UserRole.HR
    
    # 检查是否有直属下级
    # （在完整实现中，这里会查数据库）
    return UserRole.ORDINARY_EMPLOYEE


def get_direct_reports(
    user: models.Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> list[str]:
    """获取 user 的直属下级 ID 列表。
    
    使用场景：
    - 主管查看/评分下级员工
    - 权限检查（colleague_result 查询）
    
    返回：
    - 直属下级的 employee_id 列表
    
    【板块1】实现：从数据库查询。
    """
    
    direct_reports = db.query(models.Employee).filter_by(
        manager_id=user.id,
        employment_status="active"
    ).all()
    
    return [emp.id for emp in direct_reports]


def check_permission(
    user: models.Employee,
    action: str
) -> bool:
    """权限矩阵检查。
    
    参数：
    - user: 当前用户
    - action: 要执行的操作（见 PERMISSION_MATRIX 的 key）
    
    返回：
    - True 如果有权
    - False 如果无权
    
    【板块1】实现：根据角色查权限矩阵。
    """
    role = _get_user_role(user)
    permissions = PERMISSION_MATRIX.get(role, {})
    return permissions.get(action, False)


def check_can_view_score(
    user_id: str,
    target_employee_id: str,
    db: Session
) -> bool:
    """检查 user_id 是否能查看 target_employee_id 的成绩。
    
    权限规则：
    - 自己能查自己（view_own_score）
    - 主管能查直属下级（view_direct_report_score）
    - HR 能查所有人（view_all_scores）
    
    参数：
    - user_id: 当前用户 ID
    - target_employee_id: 目标员工 ID
    - db: SQLAlchemy Session（必须传入，不能用 Depends）
    
    返回：True（有权）
    
    抛出：PermissionDenied（无权）
    
    【要求 13-17】
    - 所有函数都接收 db:Session，数据库操作全部使用传入会话
    - 权限不通过时，抛出 exceptions.PermissionDenied，不返回普通 HTTPException
    - 没有写死业务逻辑，不混入业务接口代码，只做依赖注入
    """
    
    # 第 1 步：查询当前用户完整信息
    user = db.query(models.Employee).filter_by(id=user_id).first()
    if not user:
        raise PermissionDenied(
            user_id=user_id,
            action="view_score",
            resource=f"employee:{target_employee_id}（当前用户不存在）"
        )
    
    # 第 2 步：查询目标员工
    target_emp = db.query(models.Employee).filter_by(id=target_employee_id).first()
    if not target_emp:
        raise PermissionDenied(
            user_id=user_id,
            action="view_score",
            resource=f"employee:{target_employee_id}（目标员工不存在）"
        )
    
    # 第 3 步：查看自己
    if user_id == target_employee_id:
        if check_permission(user, "view_own_score"):
            return True
        else:
            raise PermissionDenied(
                user_id=user_id,
                action="view_score",
                resource=f"employee:{target_employee_id}"
            )
    
    # 第 4 步：查看所有人（HR）
    if check_permission(user, "view_all_scores"):
        return True
    
    # 第 5 步：查看直属下级
    if check_permission(user, "view_direct_report_score"):
        # 验证 target_employee 是否是 user 的直属下级
        if target_emp.manager_id == user_id:
            return True
    
    # 都不符合，无权
    raise PermissionDenied(
        user_id=user_id,
        action="view_score",
        resource=f"employee:{target_employee_id}"
    )


def check_can_submit_review(
    user_id: str,
    target_record_id: str,
    db: Session
) -> models.AssessmentRecord:
    """检查 user_id 是否能提交某个 record 的评分。
    
    权限规则：
    - 自己的 record，自己能提交自评（submit_own_review）
    - 直属下级的 record，主管能提交评分（submit_direct_report_review）
    
    参数：
    - user_id: 当前用户 ID
    - target_record_id: 目标记录 ID
    - db: SQLAlchemy Session（必须传入，不能用 Depends）
    
    返回：AssessmentRecord 对象（后续使用）
    
    抛出：
    - PermissionDenied（无权）
    - RecordNotFound（记录不存在）
    
    【要求 13-17】
    - 所有函数都接收 db:Session，数据库操作全部使用传入会话
    - 权限不通过时，抛出 exceptions.PermissionDenied，不返回普通 HTTPException
    - 没有写死业务逻辑，不混入业务接口代码，只做依赖注入
    """
    
    # 第 1 步：查询当前用户完整信息
    user = db.query(models.Employee).filter_by(id=user_id).first()
    if not user:
        raise PermissionDenied(
            user_id=user_id,
            action="submit_review",
            resource=f"record:{target_record_id}（当前用户不存在）"
        )
    
    # 第 2 步：查询记录
    record = db.query(models.AssessmentRecord).filter_by(id=target_record_id).first()
    if not record:
        raise RecordNotFound("AssessmentRecord", target_record_id)
    
    # 第 3 步：查询被评估的员工
    target_emp = db.query(models.Employee).filter_by(id=record.employee_id).first()
    if not target_emp:
        raise PermissionDenied(
            user_id=user_id,
            action="submit_review",
            resource=f"record:{target_record_id}（目标员工不存在）"
        )
    
    # 第 4 步：自己的自评
    if record.employee_id == user_id:
        if check_permission(user, "submit_own_review"):
            return record
        else:
            raise PermissionDenied(
                user_id=user_id,
                action="submit_review",
                resource=f"record:{target_record_id}"
            )
    
    # 第 5 步：主管的评分
    if check_permission(user, "submit_direct_report_review"):
        # 验证目标员工是否是当前用户的直属下级
        if target_emp.manager_id == user_id:
            return record
    
    # 都不符合
    raise PermissionDenied(
        user_id=user_id,
        action="submit_review",
        resource=f"record:{target_record_id}"
    )


# ┌─────────────────────────────────────────────────────┐
# │ 常用的组合依赖                                      │
# └─────────────────────────────────────────────────────┘

async def get_current_hr(
    user: models.Employee = Depends(get_current_user)
) -> models.Employee:
    """只有 HR 才能调用的接口的依赖。
    
    【板块1】实现：检查 position 或角色。
    """
    
    if not check_permission(user, "view_all_scores"):
        raise PermissionDenied(
            user_id=user.id,
            action="access_hr_interface",
            resource="HR操作"
        )
    
    return user


async def get_current_manager(
    user: models.Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> models.Employee:
    """检查 user 是否是某个部门的主管（有直属下级）。
    
    【板块1】实现：检查是否有直属下级。
    """
    
    direct_reports = db.query(models.Employee).filter_by(
        manager_id=user.id,
        employment_status="active"
    ).all()
    
    if not direct_reports:
        raise PermissionDenied(
            user_id=user.id,
            action="access_manager_interface",
            resource="主管操作（无直属下级）"
        )
    
    return user
