"""认证 API 路由 - 登录、获取当前用户、登出。"""

from datetime import datetime, timedelta
from typing import Any, Optional
import base64
import hashlib
import hmac
import json
import time

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.config import settings
from ..domain.models import Employee, Department, FeishuIdentity
from ..services.access_control import build_access_context

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
            # 主管评分页
            "performance:manager-review:view",
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


# 前端菜单/按钮权限标识。
# 后端权限体系（access_control）使用动作级标识，
# 这里把角色映射成前端已使用的权限串，保证两套标识同时可用。
ROLE_UI_PERMISSIONS: dict[str, list[str]] = {
    "employee": [
        "dashboard:view",
        "planning:month:view",
        "planning:day:view",
        "planning:result:view",
        "performance:dashboard:view",
        "performance:self-review:view",
    ],
    "manager": [
        "department:dashboard:view",
        "department:month-approval:view",
        "department:result-confirm:view",
        "department:todo:view",
        "department:template:view",
        "department:standard:view",
        "department:score-rule:view",
        "leader:workbench:view",
        # 主管评分页：待评下属列表 + 评分提交
        "performance:manager-review:view",
    ],
    "department_leader": [
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
        # 部门负责人同样承担下属评分职责
        "performance:manager-review:view",
    ],
    "hr": [
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
        "dispute:dashboard:view",
        "dispute:case:view",
        # HR 可查看全部考核记录
        "performance:manager-review:view",
    ],
    "admin": [
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
}


def _resolve_permissions(db: Session, employee: Employee) -> tuple[list[str], list[str]]:
    """返回 (前端权限串, 后端动作权限串)。

    角色来自 access_control 的统一解析（HR 走部门配置、
    部门负责人走飞书 leader_id、管理员走 system_admin 显式授权）。
    """
    context = build_access_context(db, employee)

    ui_permissions: set[str] = set()
    for role in context.roles:
        ui_permissions.update(ROLE_UI_PERMISSIONS.get(role, []))

    # 后端动作权限原样透出，便于前端/客户端按需判断。
    backend_permissions = set(context.permissions)
    return sorted(ui_permissions), sorted(backend_permissions)


def _generate_token(user_id: str, username: str, token_version: int = 1) -> tuple[str, str]:
    """生成不可伪造的 HMAC Token。

    Payload 包含 token_version；离职或禁用账号时递增版本即可立即使旧 Token 失效。
    """
    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "ver": token_version,
        "iat": now,
        "exp": now + 7 * 24 * 3600,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        settings.auth_token_secret.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    token = f"{encoded}.{signature}"
    refresh_token = hmac.new(
        settings.auth_token_secret.encode(),
        f"refresh:{user_id}:{token_version}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return token, refresh_token


def _decode_token(token: str) -> dict[str, Any]:
    """校验签名与有效期。

    密钥轮换过渡期同时接受新旧两个密钥（旧密钥来自 AUTH_TOKEN_SECRET_PREV），
    轮换完成观察一个 Token 有效期后应清空旧密钥。
    """
    try:
        encoded, signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("Token 格式错误") from exc

    candidates = [settings.auth_token_secret]
    if settings.auth_token_secret_prev:
        candidates.append(settings.auth_token_secret_prev)

    matched = False
    for secret in candidates:
        expected = hmac.new(
            secret.encode(),
            encoded.encode(),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            matched = True
            break
    if not matched:
        raise ValueError("Token 签名无效")

    padded = encoded + "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Token 已过期")
    return payload


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
    
    # 检查数据库中的员工：支持登录账号、手机号或飞书身份登录。
    employee = (
        db.query(Employee)
        .filter(
            (Employee.login_account == username.lower())
            | (Employee.email == username)
            | (Employee.name == username)
        )
        .first()
    )
    if not employee:
        identity = (
            db.query(FeishuIdentity)
            .filter(
                FeishuIdentity.identity_value == username,
                FeishuIdentity.active.is_(True),
            )
            .first()
        )
        employee = db.get(Employee, identity.employee_id) if identity else None
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    if employee.employment_status == "exited":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="员工已离职，账号已禁用",
        )
    if employee.account_status != "enabled":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已禁用，请联系管理员",
        )
    
    # 简化实现：任何员工使用 "123456" 作为密码
    if password != "123456":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    
    # 角色与权限统一由 access_control 解析：
    #   - HR：成员属于 hr_department_config 中任一启用的部门
    #   - 部门负责人：飞书部门 leader_id（自动覆盖子部门）
    #   - 系统管理员：system_admin 表显式授权（不参与业务评分）
    #   - 多个角色可叠加
    context = build_access_context(db, employee)
    roles = sorted(context.roles)
    permissions, backend_permissions = _resolve_permissions(db, employee)

    access_token, refresh_token = _generate_token(
        employee.id, username, employee.token_version
    )
    
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
            "backendPermissions": backend_permissions,
        },
    }


@router.get("/me")
async def get_me(request: Request, db: Session = Depends(get_db)) -> dict:
    """获取当前用户信息，并校验离职/禁用状态与 Token 版本。"""
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少认证凭据")
    token = authorization[7:]
    try:
        payload = _decode_token(token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error):
        raise HTTPException(status_code=401, detail="无效的认证凭据")

    username = payload.get("username")
    user_id = payload.get("sub")
    if not username or not user_id:
        raise HTTPException(status_code=401, detail="Token 中缺少用户信息")

    employee = db.get(Employee, str(user_id))
    if employee:
        if employee.employment_status == "exited" or employee.account_status != "enabled":
            raise HTTPException(status_code=403, detail="员工已离职或账号已禁用")
        if int(payload.get("ver", 1)) != employee.token_version:
            raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")

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
    if not employee:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 与登录接口保持一致：角色/权限均来自 access_context 实时解析。
    context = build_access_context(db, employee)
    permissions, backend_permissions = _resolve_permissions(db, employee)
    return {
        "code": 0,
        "data": {
            "userId": employee.id,
            "username": employee.login_account or username,
            "realName": employee.name,
            "forceChangePassword": False,
            "roles": sorted(context.roles),
            "permissions": permissions,
            "backendPermissions": backend_permissions,
        },
    }


@router.post("/logout")
async def logout(request: Request) -> dict:
    """登出接口。"""
    # 可以在这里添加日志记录或会话清理逻辑
    return {
        "code": 0,
        "data": {
            "message": "登出成功"
        }
    }


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""
    oldPassword: str = Field(..., min_length=1)
    newPassword: str = Field(..., min_length=1)


@router.post("/change-password")
async def change_password(request: ChangePasswordRequest) -> dict:
    """修改密码接口。"""
    # 简化实现
    return {"code": 0, "data": {"message": "密码修改成功"}}
