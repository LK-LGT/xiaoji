"""系统用户管理 API 路由。

对应前端 `src/api/system.ts` 的 `/system/users/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/system/users` 前缀。

分页约定：前端 `getUsers` 用 `PageResult<UserItem>`（即 `records`），
故用户列表返回 { records, total, pageNo, pageSize }。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...core.db import get_db
from ..shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/system/users", tags=["system-users"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 用户 CRUD
#
# 响应结构：UserItem / RegistrationResult / PageResult<UserItem>
# ---------------------------------------------------------------------------


@router.get("")
async def list_users(
    keyword: Optional[str] = Query(None),
    deptId: Optional[int] = Query(None),
    status: Optional[int] = Query(None),
    roleId: Optional[int] = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """获取用户列表。

    响应结构：PageResult<UserItem> = { records, total, pageNo, pageSize }
    """
    return _ok(
        {
            "records": [],
            "total": 0,
            "pageNo": pageNo,
            "pageSize": pageSize,
        }
    )


@router.post("/register")
async def register_employee(
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """注册员工账号。

    请求体：{ realName, mobile, deptId, directLeaderId?, roleId, positionName? }
    响应结构：RegistrationResult
    """
    return _ok(
        {
            "userId": 0,
            "username": "",
            "employeeNo": "",
            "initialPassword": "",
            "realName": payload.get("realName", ""),
            "positionName": payload.get("positionName", ""),
            "departmentName": "",
            "directLeaderName": "",
        }
    )


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """更新用户信息。

    响应结构：UserItem
    """
    return _ok(_empty_user_item(user_id))


@router.post("/{user_id}/status")
async def change_user_status(
    user_id: int,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """启用 / 停用用户。

    请求体：{ status: number }
    响应结构：UserItem
    """
    return _ok(_empty_user_item(user_id))


@router.post("/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """重置用户密码。

    响应结构：{ initialPassword: string }
    """
    return _ok({"initialPassword": ""})


# ---------------------------------------------------------------------------
# 导入导出
#
# 响应结构：{ total, success, failed, errors } / 二进制文件流
# ---------------------------------------------------------------------------


@router.post("/import")
async def import_users(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """批量导入用户。

    响应结构：{ total, success, failed, errors }
    """
    return _ok(
        {
            "total": 0,
            "success": 0,
            "failed": 0,
            "errors": [],
        }
    )


@router.get("/import-template")
async def download_user_import_template(
    db: Session = Depends(get_db),
) -> Any:
    """下载用户导入模板。

    响应结构：二进制文件流
    """
    # 尚无真实模板时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="导入模板暂不可用")


@router.get("/export")
async def export_users(
    db: Session = Depends(get_db),
) -> Any:
    """导出用户列表。

    响应结构：二进制文件流
    """
    # 尚无真实数据时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="暂无可导出的用户数据")


def _empty_user_item(user_id: int) -> dict:
    """构造 UserItem，字段与 frontend/src/api/system.ts 保持一致。"""
    return {
        "id": user_id,
        "username": "",
        "employeeNo": "",
        "realName": "",
        "positionName": None,
        "mobile": "",
        "deptId": None,
        "departmentName": "",
        "groupId": None,
        "directLeaderId": None,
        "directLeaderName": "",
        "roleIds": [],
        "roleNames": [],
        "status": 1,
        "forceChangePassword": False,
        "lastLoginAt": None,
        "createdAt": "",
    }
