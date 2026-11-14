"""认证 API 路由 - 登录、获取当前用户、登出。"""

from datetime import datetime, timedelta
from typing import Any, Optional
import base64
import json

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import Employee, Department

router = APIRouter(prefix="/v1/auth", tags=["认证"])


# ─────────────────────────────────────────────────────
# 请求/响应模型
# ─────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """登录请求。"""
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=100)


# 测试账号 - 每个账号只有其对应角色的权限
TEST_USERS = {
    "admin": {
        "password": "admin123",
        "id": "8d5c97gb",
        "name": "管理员",
        "roles": ["admin"],
        "permissions": [
            # System permissions only - 系统管理员
            "system:dashboard:view",
            "system:employee:register",
            "system:employee:view",
            "system:org:view",
            "system:role:view",
            "system:permission:view",
            "system:workday:view",
            "system:week-standard:view",
            "system:audit:view",
            "system:knowledge:view",
        ],
    },
    "manager": {
        "password": "manager123",
        "id": "g426c684",
        "name": "部门主管",
        "roles": ["manager"],
        "permissions": [
            # Department permissions only - 部门负责人
            "department:dashboard:view",
            "department:month-approval:view",
            "department:result-confirm:view",
            "department:todo:view",
            "department:template:view",
            "department:standard:view",
            "department:score-rule:view",
            "department:export-task:view",
        ],
    },
    "leader": {
        "password": "leader123",
        "id": "leader001",
        "name": "直属领导",
        "roles": ["leader"],
        "permissions": [
            # Leader permissions only - 直属领导
            "leader:workbench:view",
            "leader:month-approval:view",
            "leader:daily-review:view",
            "leader:result-suggest:view",
        ],
    },
    "employee": {
        "password": "employee123",
        "id": "2aaf32ea",
        "name": "普通员工",
        "roles": ["employee"],
        "permissions": [
            # Employee permissions only - 员工
            "dashboard:view",
            "planning:month:view",
            "planning:day:view",
            "planning:result:view",
            "performance:dashboard:view",
            "performance:self-review:view",
        ],
    },
}


def _generate_token(user_id: str, username: str) -> tuple[str, str]:
    """生成 JWT Token（简化版）。"""
    # 这是简化实现，生产环境应该使用真正的 JWT
    import base64
    import json
    
    payload = {
        "sub": user_id,
        "username": username,
        "exp": (datetime.utcnow() + timedelta(days=7)).timestamp(),
    }
    token = base64.b64encode(json.dumps(payload).encode()).decode()
    refresh_token = base64.b64encode(json.dumps({"sub": user_id}).encode()).decode()
    return token, refresh_token


@router.post("/login")
async def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
) -> dict:
    """登录接口。
    
    支持测试账号和数据库员工账号。
    """
    username = request.username
    password = request.password
    
    # 检查测试账号
    if username in TEST_USERS:
        user_info = TEST_USERS[username]
        if user_info["password"] != password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )
        
        access_token, refresh_token = _generate_token(user_info["id"], username)
        
        return {
            "code": 0,
            "data": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresIn": 7 * 24 * 3600,  # 7 天
                "userId": user_info["id"],
                "username": username,
                "realName": user_info["name"],
                "forceChangePassword": False,
                "roles": user_info["roles"],
                "permissions": user_info["permissions"],
            },
        }
    
    # 检查数据库中的员工
    employee = db.query(Employee).filter(Employee.name == username).first()
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    
    # 简化实现：任何员工使用 "123456" 作为密码
    if password != "123456":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    
    # 确定角色（基于是否是主管或 HR）
    roles = ["employee"]
    permissions = [
        "dashboard:view",
        "planning:month:view",
        "planning:day:view",
        "planning:result:view",
        "performance:dashboard:view",
        "performance:self-review:view",
    ]
    
    # 检查是否是部门主管
    is_manager = (
        db.query(Department)
        .filter(Department.leader_id == employee.id)
        .first() is not None
    )
    if is_manager:
        roles = ["manager"]
        permissions = [
            "department:dashboard:view",
            "department:month-approval:view",
            "department:result-confirm:view",
            "department:todo:view",
            "department:template:view",
            "department:standard:view",
            "department:score-rule:view",
            "leader:workbench:view",
            "dashboard:view",
            "planning:month:view",
            "planning:day:view",
            "planning:result:view",
            "performance:dashboard:view",
            "performance:manager-review:view",
        ]
    
    # 检查是否是 HR（特定部门或特殊标记）
    if employee.department_id == "hr":  # 假设 HR 部门 ID 为 "hr"
        roles = ["admin"]
        permissions = [
            "system:dashboard:view",
            "system:employee:register",
            "system:employee:view",
            "system:org:view",
            "system:role:view",
            "system:permission:view",
            "system:workday:view",
            "system:week-standard:view",
            "system:audit:view",
            "system:knowledge:view",
            "department:dashboard:view",
            "department:month-approval:view",
            "department:result-confirm:view",
            "department:todo:view",
            "department:template:view",
            "department:standard:view",
            "department:score-rule:view",
            "department:export-task:view",
            "department:department-ledger:view",
            "leader:workbench:view",
            "leader:month-approval:view",
            "leader:daily-review:view",
            "leader:result-suggest:view",
            "leader:plan-adjust:view",
            "leader:team-ledger:view",
            "dashboard:view",
            "planning:month:view",
            "planning:day:view",
            "planning:result:view",
            "dispute:dashboard:view",
            "dispute:case:view",
            "performance:dashboard:view",
            "performance:self-review:view",
            "performance:manager-review:view",
        ]
    
    access_token, refresh_token = _generate_token(employee.id, username)
    
    return {
        "code": 0,
        "data": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresIn": 7 * 24 * 3600,
            "userId": employee.id,
            "username": username,
            "realName": employee.name,
            "forceChangePassword": False,
            "roles": roles,
            "permissions": permissions,
        },
    }


@router.get("/me")
async def get_me(request: Request) -> dict:
    """获取当前用户信息。"""
    # 从前端的 Authorization 请求头中获取用户信息
    # Token 格式为 base64 编码的 JSON，包含 username 和其他信息
    # 这是简化实现，生产环境应该使用真正的 JWT
    
    authorization = request.headers.get("Authorization")
    
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证凭据",
        )
    
    try:
        token = authorization[7:]  # 移除 "Bearer " 前缀
        # 尝试从 token 中解析用户信息
        # Token 来自 _generate_token，格式为 base64(json({sub, username, exp}))
        payload_json = base64.b64decode(token).decode()
        payload = json.loads(payload_json)
        username = payload.get("username")
        
        if not username:
            raise ValueError("Token 中缺少 username")
            
        # 从 TEST_USERS 中查找用户信息
        if username in TEST_USERS:
            user_info = TEST_USERS[username]
            return {
                "code": 0,
                "data": {
                    "userId": user_info["id"],
                    "username": username,
                    "realName": user_info["name"],
                    "forceChangePassword": False,
                    "roles": user_info["roles"],
                    "permissions": user_info["permissions"],
                },
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户不存在",
            )
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"无效的认证凭据: {str(e)}",
        )


@router.post("/logout")
async def logout(request: BaseModel = None) -> dict:
    """登出接口。"""
    return {"code": 0, "data": {"message": "登出成功"}}


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""
    oldPassword: str = Field(..., min_length=1)
    newPassword: str = Field(..., min_length=1)


@router.post("/change-password")
async def change_password(request: ChangePasswordRequest) -> dict:
    """修改密码接口。"""
    # 简化实现
    return {"code": 0, "data": {"message": "密码修改成功"}}
