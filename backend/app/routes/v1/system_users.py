"""系统用户管理 API 路由。

对应前端 `src/api/system.ts` 的 `/system/users/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/system/users` 前缀。

分页约定：前端 `getUsers` 用 `PageResult<UserItem>`（即 `records`），
故用户列表返回 { records, total, pageNo, pageSize }。
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...domain.models import Employee
from ...services import password_hasher, system_views
from ..shared import JsonBody
from ...dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/system/users", tags=["system-users"], dependencies=[Depends(get_current_user)])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


def _generate_initial_password(length: int = 12) -> str:
    """生成随机初始口令。

    ⚠️ 字符集刻意**不含**容易混淆的 ``0/O``、``1/l/I``，
    避免管理员口头/截图传递时抄错。

    用 :func:`secrets.choice`（而非 ``random.choice``）——
    初始口令要参与密码认证，必须是密码学安全的随机源。
    """
    alphabet = string.ascii_letters + string.digits
    # 去掉易混淆字符
    ambiguous = set("0O1lI")
    pool = [c for c in alphabet if c not in ambiguous]
    return "".join(secrets.choice(pool) for _ in range(length))


# ---------------------------------------------------------------------------
# 用户 CRUD
#
# 响应结构：UserItem / RegistrationResult / PageResult<UserItem>
# ---------------------------------------------------------------------------


@router.get("")
async def list_users(
    keyword: Optional[str] = Query(None),
    deptId: Optional[str] = Query(None),
    status: Optional[int] = Query(None),
    roleId: Optional[str] = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """获取用户列表（真实数据）。

    响应结构：PageResult<UserItem> = { records, total, pageNo, pageSize }

    数据来源：飞书同步落库的 employee 表。
    """
    return _ok(
        system_views.list_user_items(
            db,
            keyword=keyword,
            dept_id=deptId,
            status=status,
            page_no=pageNo,
            page_size=pageSize,
        )
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
    user_id: str,
    payload: JsonBody,
    db: Session = Depends(get_db),
) -> dict:
    """更新用户信息。

    响应结构：UserItem
    """
    return _ok(_empty_user_item(user_id))


@router.post("/{user_id}/status")
async def change_user_status(
    user_id: str,
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
    user_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """重置用户密码（R4 落实）。

    之前是**空壳**：直接返回 ``{"initialPassword": ""}``，什么都没做 ——
    前端显示"重置成功"，但用户的老密码**依然能用**。

    现在的行为：
      1. 生成一个**随机初始口令**（不是固定 "123456"）；
      2. 写入 PBKDF2 哈希，**明文只在这一刻返回一次**，不落库；
      3. 置 ``force_change_password = True`` → 该用户下次登录必须改密；
      4. 递增 ``token_version`` → 该用户所有已签发的 Token 立即失效
         （否则旧 Token 还能继续用，"重置密码"等于没重置）。

    响应结构：{ initialPassword: string }
    """
    employee = db.get(Employee, user_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 随机初始口令：大小写字母 + 数字，12 位。
    # ⚠️ 不再返回固定的 "123456" —— 全员共用一个口令等于没重置。
    initial_password = _generate_initial_password()

    try:
        employee.password_hash = password_hasher.hash_password(initial_password)
    except password_hasher.PasswordStrengthError as exc:
        raise HTTPException(status_code=500, detail=f"生成初始口令失败：{exc}") from exc
    employee.password_set_at = datetime.now()
    # 强制该用户下次登录后改密（管理员不知道用户会怎么用它）。
    employee.force_change_password = True
    # 吊销所有旧 Token。
    employee.token_version = int(employee.token_version or 1) + 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("重置密码失败 employee=%s", user_id)
        raise HTTPException(status_code=500, detail="重置密码失败，请稍后重试")

    logger.info("管理员重置了员工 %s 的密码，token_version → %s", user_id, employee.token_version)

    # ⚠️ 明文只在这里返回一次，之后无法再取回（库里只有哈希）。
    return _ok({"initialPassword": initial_password})


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


def _empty_user_item(user_id: str) -> dict:
    """构造 UserItem，字段与 frontend/src/api/system.ts 保持一致。

    ⚠️ `id` 是员工 ID（飞书 `user_id`，长十六进制字符串），故为 string。
    """
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
