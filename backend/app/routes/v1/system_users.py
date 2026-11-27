"""系统用户管理 API 路由。

对应前端 `src/api/system.ts` 的 `/system/users/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/system/users` 前缀。

分页约定：前端 `getUsers` 用 `PageResult<UserItem>`（即 `records`），
故用户列表返回 { records, total, pageNo, pageSize }。
"""

from __future__ import annotations

import csv
import io
import logging
import re
import secrets
import string
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...dependencies import get_current_user
from ...domain.models import Department, Employee
from ...services import password_hasher, system_views
from ...services.access_control import PermissionAction, build_access_context
from ..shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/system/users", tags=["system-users"], dependencies=[Depends(get_current_user)])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


def _require_employee_admin(db: Session, current_user: Employee) -> None:
    """🔴 要求「员工管理」权限（HR / 系统管理员），否则 403。

    ⚠️ **为什么必须有这道闸（2026-11-26 实测的真实越权）**
    本模块原先只有路由级 ``dependencies=[Depends(get_current_user)]``
    ⇒ **任何登录用户**都能调用：

    - ``POST /v1/system/users/{id}/reset-password``
      → 把**任意人**（含 HR / 管理员）的密码改成攻击者已知口令，
        并**直接拿到明文初始口令** ⇒ 完整账号接管；
    - ``PUT /v1/system/users/{id}`` → 改别人的档案；
    - ``POST /v1/system/users/{id}/status`` → 禁用别人的账号。

    ⇒ 这不是「功能没做完」，是**越权后门**。

    ⚠️ **权限位口径**
    用 ``MANAGE_EMPLOYEES``（**动作权限**，不是前端菜单串）——
    它在 ``access_control`` 里只授予 HR 与系统管理员
    ⇒ 与「谁能管员工」的业务口径一致，且不依赖前端菜单串。

    ⚠️ **为什么不用前端权限串 ``system:employee:view``**
    那是**菜单可见性**标识（``ROLE_UI_PERMISSIONS``），
    后端动作鉴权必须用 ``PermissionAction`` ⇒ 两套标识职责不同，
    混用就会出现「前端能看、后端放行」的越权。
    """
    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.MANAGE_EMPLOYEES):
        raise HTTPException(status_code=403, detail="无权管理员工信息")


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


#: 中国大陆手机号格式（前端 ``EmployeeRegister.vue`` / ``EmployeeManagement.vue`` 同口径）。
_MOBILE_PATTERN = re.compile(r"^1\d{10}$")


def _next_login_account(db: Session) -> str:
    """生成**唯一**的登录账号（8 位数字）。

    ⚠️ **为什么是 8 位数字**：前端 ``EmployeeRegister.vue`` 的说明写着
    「系统自动生成8位数字账号」⇒ 口径必须一致，不能前端一个样、后端另一个样。

    ⚠️ **为什么不用随机数**：随机数会撞唯一约束 ⇒ 只能递增探测。
    代价：账号可被枚举 ⇒ 但账号本身**不是**秘密（密码才是），
    且登录失败有速率限制 ⇒ 可接受。
    """
    from sqlalchemy import select

    existing = {
        str(row)
        for row in db.execute(select(Employee.login_account)).scalars().all()
        if row
    }
    for candidate in range(10000000, 10000100):
        text = str(candidate)
        if text not in existing:
            return text
    # ⚠️ 100 个连续候选都被占用（极端情况）⇒ 用带前缀的兜底
    fallback = f"u{uuid.uuid4().hex[:8]}"
    while fallback in existing:
        fallback = f"u{uuid.uuid4().hex[:8]}"
    return fallback


# ---------------------------------------------------------------------------
# 用户 CRUD
#
# 响应结构：UserItem / RegistrationResult / PageResult<UserItem>
# ---------------------------------------------------------------------------


@router.get("")
async def list_users(
    keyword: str | None = Query(None),
    deptId: str | None = Query(None),
    status: int | None = Query(None),
    roleId: str | None = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取用户列表（真实数据）。

    响应结构：PageResult<UserItem> = { records, total, pageNo, pageSize }

    数据来源：飞书同步落库的 employee 表。

    ⚠️ **2026-11-27：仅 HR / 系统管理员可拉全员通讯录**
    该接口返回手机号、邮箱、直属负责人与角色 ⇒ 不能对任意登录用户开放。
    """
    _require_employee_admin(db, current_user)
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
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """注册员工账号。

    请求体：{ realName, mobile, deptId, directLeaderId?, roleId, positionName? }
    响应结构：RegistrationResult

    ⚠️ **2026-11-26：原实现是空壳** —— 返回 ``userId: 0``、``initialPassword: ""``，
    **数据库里什么都没写**，而前端照样弹「员工账号已创建」⇒ 假成功。
    ⇒ 现在真正建号：生成唯一登录账号、写入 PBKDF2 哈希、置强制改密。

    ⚠️ **角色为什么不落库**
    本系统的权限模型**不是** RBAC 角色表 —— 角色由
    ``access_control.build_access_context`` 根据「部门 / 汇报线 /
    HR 部门配置 / system_admin 表」**动态推导**。
    ⇒ ``roleId`` 只做**合法性校验**（必须是规范角色码），
    不写入任何「用户→角色」表 —— 那张表不存在，建了也是摆设。
    """
    _require_employee_admin(db, current_user)

    real_name = str(payload.get("realName") or "").strip()
    mobile = str(payload.get("mobile") or "").strip()
    dept_id = str(payload.get("deptId") or "").strip()
    position_name = str(payload.get("positionName") or "").strip()
    leader_id = str(payload.get("directLeaderId") or "").strip() or None
    role_id = str(payload.get("roleId") or "").strip()

    if not real_name:
        raise HTTPException(status_code=422, detail="请填写员工姓名")
    if not mobile or not _MOBILE_PATTERN.match(mobile):
        raise HTTPException(status_code=422, detail="请填写正确的11位手机号")
    if not dept_id:
        raise HTTPException(status_code=422, detail="请选择归属组织")

    department = db.get(Department, dept_id)
    if department is None:
        raise HTTPException(status_code=404, detail="归属组织不存在")

    if leader_id:
        if db.get(Employee, leader_id) is None:
            raise HTTPException(status_code=404, detail="直属负责人不存在")

    if role_id:
        from ...services.role_vocabulary import ROLE_LABELS

        if role_id not in ROLE_LABELS:
            raise HTTPException(status_code=422, detail=f"未知角色：{role_id}")

    # ⚠️ **登录账号必须唯一** —— `employee.login_account` 有唯一约束。
    username = _next_login_account(db)
    employee_id = f"LOCAL_{uuid.uuid4().hex[:16]}"
    initial_password = _generate_initial_password()

    employee = Employee(
        id=employee_id,
        name=real_name,
        login_account=username,
        mobile=mobile,
        department_id=dept_id,
        manager_id=leader_id,
        position=position_name or None,
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    try:
        employee.password_hash = password_hasher.hash_password(initial_password)
    except password_hasher.PasswordStrengthError as exc:
        raise HTTPException(status_code=500, detail=f"生成初始口令失败：{exc}") from exc
    employee.password_set_at = datetime.now()
    # ⚠️ 强制首次登录改密 —— 管理员是口头告知初始口令的，
    # 不能假设员工一直用它。
    employee.force_change_password = True

    db.add(employee)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("注册员工失败")
        raise HTTPException(status_code=500, detail="注册员工失败，请稍后重试")

    db.refresh(employee)
    manager = db.get(Employee, leader_id) if leader_id else None
    return _ok(
        {
            "userId": employee.id,
            "username": username,
            "employeeNo": employee.id,
            # ⚠️ 明文**只在这里返回一次**（库里只有哈希）。
            "initialPassword": initial_password,
            "realName": employee.name,
            "positionName": employee.position or "",
            "departmentName": department.name,
            "directLeaderName": manager.name if manager else "",
        }
    )


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    payload: JsonBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """更新用户信息。

    响应结构：UserItem

    ⚠️ **2026-11-26：原实现是空壳** —— 返回一个字段全空的
    ``_empty_user_item(user_id)``，**数据库里什么都没改**，
    而前端弹「员工信息已保存」⇒ 假成功。
    ⇒ 现在真正落库并返回**更新后**的真实条目。
    """
    _require_employee_admin(db, current_user)

    employee = db.get(Employee, user_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    changed = False

    real_name = str(payload.get("realName") or "").strip()
    if real_name and real_name != employee.name:
        employee.name = real_name
        changed = True

    mobile = str(payload.get("mobile") or "").strip()
    if mobile and mobile != (employee.mobile or ""):
        if not _MOBILE_PATTERN.match(mobile):
            raise HTTPException(status_code=422, detail="请填写正确的11位手机号")
        employee.mobile = mobile
        changed = True

    position_name = payload.get("positionName")
    if position_name is not None:
        new_position = str(position_name).strip() or None
        if new_position != employee.position:
            employee.position = new_position
            changed = True

    if payload.has("deptId"):
        dept_id = str(payload.get("deptId") or "").strip() or None
        if dept_id != employee.department_id:
            if dept_id and db.get(Department, dept_id) is None:
                raise HTTPException(status_code=404, detail="归属组织不存在")
            employee.department_id = dept_id
            changed = True

    if payload.has("directLeaderId"):
        leader_id = str(payload.get("directLeaderId") or "").strip() or None
        if leader_id != employee.manager_id:
            if leader_id:
                if db.get(Employee, leader_id) is None:
                    raise HTTPException(status_code=404, detail="直属负责人不存在")
                if leader_id == user_id:
                    raise HTTPException(status_code=422, detail="直属负责人不能是自己")
            employee.manager_id = leader_id
            changed = True

    # ⚠️ **角色不落库**（见 ``register_employee`` 的说明）——
    # ``roleIds`` 传了也只做校验，不写「用户→角色」表（那张表不存在）。
    if payload.has("roleIds") or payload.has("roleId"):
        raw_roles = payload.get("roleIds") or ([payload.get("roleId")] if payload.get("roleId") else [])
        from ...services.role_vocabulary import ROLE_LABELS

        for code in raw_roles:
            if code and str(code) not in ROLE_LABELS:
                raise HTTPException(status_code=422, detail=f"未知角色：{code}")

    if not changed:
        # ⚠️ 没有变化也返回**真实**条目 —— 不返回空壳。
        return _ok(system_views.build_user_item(db, employee))

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("更新员工失败 employee=%s", user_id)
        raise HTTPException(status_code=500, detail="保存失败，请稍后重试")

    db.refresh(employee)
    return _ok(system_views.build_user_item(db, employee))


@router.post("/{user_id}/status")
async def change_user_status(
    user_id: str,
    payload: JsonBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """启用 / 停用用户。

    请求体：{ status: number }（1=启用，0=停用）
    响应结构：UserItem

    ⚠️ **2026-11-26：原实现是空壳** —— 返回空条目、**什么都没改**，
    而前端弹「账号状态已更新」⇒ 假成功。
    ⇒ 现在真正改 ``account_status``，并**吊销已签发的 Token**
    （否则停用后旧 Token 仍能访问，等于没停用）。
    """
    _require_employee_admin(db, current_user)

    employee = db.get(Employee, user_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    raw_status = payload.get("status")
    if raw_status is None:
        raise HTTPException(status_code=422, detail="缺少 status")
    enabled = str(raw_status).strip().lower() in {"1", "true", "enabled"}

    if employee.id == current_user.id and not enabled:
        raise HTTPException(status_code=422, detail="不能停用自己的账号")

    new_status = "enabled" if enabled else "disabled"
    if employee.account_status != new_status:
        employee.account_status = new_status
        # ⚠️ 停用/启用都递增 token_version ⇒ 旧 Token 立即失效。
        employee.token_version = int(employee.token_version or 1) + 1
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("更新账号状态失败 employee=%s", user_id)
            raise HTTPException(status_code=500, detail="更新失败，请稍后重试")

    db.refresh(employee)
    return _ok(system_views.build_user_item(db, employee))


@router.post("/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
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

    ⚠️🔴 **2026-11-26 补上的权限闸（本条曾是 P0 越权后门）**
    本端点原先**只校验了登录态**，⇒ 任何员工都能重置**任何人**
    （含 HR / 管理员）的密码，并拿到明文初始口令 ⇒ **完整账号接管**。
    ⇒ 现在要求 ``MANAGE_EMPLOYEES``（HR / 系统管理员）。
    """
    _require_employee_admin(db, current_user)

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
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """批量导入用户（CSV / Excel）。

    响应结构：{ total, success, failed, errors }

    ⚠️ **2026-11-26：原实现是空壳** —— 恒返回
    ``{total: 0, success: 0, failed: 0, errors: []}``，
    **一个字节都没解析**，而前端照样弹「员工导入结果」弹窗
    ⇒ 用户以为导入成功，库里一个人都没有。

    ⇒ 现在真正解析：逐行建号，**成功/失败分别统计**，错误逐条列出。

    ⚠️ **列名口径**（与模板一致）：
    ``姓名`` / ``手机号`` / ``员工编号(登录账号,选填)`` /
    ``部门ID(选填)`` / ``直属负责人ID(选填)`` / ``岗位(选填)``

    ⚠️ **失败不中断整批** —— 逐行 try/except，最后统一回滚**本行**，
    保证「一行错、其余照进」。
    """
    _require_employee_admin(db, current_user)

    raw = await file.read()
    rows = _parse_user_rows(raw, file.filename or "")
    if rows is None:
        raise HTTPException(status_code=422, detail="无法解析文件，请使用模板格式的 CSV / Excel")

    total = len(rows)
    success = 0
    errors: list[str] = []

    for index, row in enumerate(rows, start=1):
        try:
            _import_one_user(db, row, index)
            success += 1
        except Exception as exc:  # noqa: BLE001 —— 单行失败不阻断整批
            db.rollback()
            errors.append(f"第 {index} 行：{exc}")

    return _ok({"total": total, "success": success, "failed": total - success, "errors": errors})


def _parse_user_rows(raw: bytes, filename: str) -> list[dict[str, str]] | None:
    """把上传文件解析成「行字典」列表。

    ⚠️ 返回 ``None`` 表示**无法识别格式**（区别于「空文件」→ 空列表）。
    """
    if not raw:
        return []

    text: str | None = None
    if filename.lower().endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = workbook.active
            lines = [
                ["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            text = "\n".join(",".join(_csv_escape(cell) for cell in line) for line in lines)
        except Exception:
            return None
    else:
        for encoding in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            return None

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return None

    return [
        {(key or "").strip(): (value or "").strip() for key, value in row.items() if key}
        for row in reader
    ]


def _csv_escape(value: str) -> str:
    """把单元格转成 CSV 字段（供 Excel → CSV 转换使用）。"""
    if any(ch in value for ch in (",", '"', "\n")):
        return '"' + value.replace('"', '""') + '"'
    return value


def _import_one_user(db: Session, row: dict[str, str], index: int) -> None:
    """导入**一行**（建号 + 初始口令哈希 + 强制改密）。

    ⚠️ 抛出的异常信息会**直接展示给管理员** ⇒ 必须是人话，不要暴露栈。
    """
    real_name = row.get("姓名") or row.get("realName") or row.get("姓名 ") or ""
    mobile = row.get("手机号") or row.get("mobile") or ""
    username = row.get("员工编号") or row.get("登录账号") or row.get("username") or ""
    dept_id = row.get("部门ID") or row.get("deptId") or ""
    leader_id = row.get("直属负责人ID") or row.get("directLeaderId") or ""
    position = row.get("岗位") or row.get("positionName") or ""

    if not real_name:
        raise ValueError("缺少「姓名」")
    if not mobile or not _MOBILE_PATTERN.match(mobile):
        raise ValueError("手机号格式不正确")

    if dept_id and db.get(Department, dept_id) is None:
        raise ValueError(f"部门不存在：{dept_id}")
    if leader_id and db.get(Employee, leader_id) is None:
        raise ValueError(f"直属负责人不存在：{leader_id}")

    if username:
        from sqlalchemy import select

        exists = db.execute(
            select(Employee.id).where(Employee.login_account == username)
        ).first()
        if exists:
            raise ValueError(f"登录账号已存在：{username}")
    else:
        username = _next_login_account(db)

    employee_id = f"LOCAL_{uuid.uuid4().hex[:16]}"
    initial_password = _generate_initial_password()

    employee = Employee(
        id=employee_id,
        name=real_name,
        login_account=username,
        mobile=mobile,
        department_id=dept_id or None,
        manager_id=leader_id or None,
        position=position or None,
        employment_status="active",
        account_status="enabled",
        token_version=1,
    )
    employee.password_hash = password_hasher.hash_password(initial_password)
    employee.password_set_at = datetime.now()
    employee.force_change_password = True

    db.add(employee)
    db.commit()


@router.get("/import-template")
async def download_user_import_template(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> Any:
    """下载用户导入模板。

    响应结构：二进制文件流（UTF-8 BOM CSV）

    ⚠️ **2026-11-26：原实现直接 404** —— 前端「下载模板」按钮必然失败。
    ⇒ 现在返回**真模板**（含表头 + 一行示例 + 部门/负责人 ID 提示）。
    """
    _require_employee_admin(db, current_user)

    departments = db.query(Department).order_by(Department.name).all()
    dept_hint = "；".join(f"{d.id}={d.name}" for d in departments[:20]) or "（暂无部门）"

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["姓名", "手机号", "员工编号", "部门ID", "直属负责人ID", "岗位"])
    writer.writerow(["张三", "13800000000", "10000001", "", "", "剪辑"])
    writer.writerow([])
    writer.writerow([f"# 可用部门ID：{dept_hint}"])
    writer.writerow(["# 直属负责人ID 填员工ID（飞书 user_id）；留空表示无直属上级。"])
    writer.writerow(["# 员工编号留空时系统自动生成 8 位数字账号。"])

    body = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([body]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=user-import-template.csv",
            "Cache-Control": "no-store",
        },
    )


@router.get("/export")
async def export_users(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> Any:
    """导出用户列表（CSV）。

    响应结构：二进制文件流（UTF-8 BOM CSV）

    ⚠️ **2026-11-26：原实现直接 404** —— 前端「导出」按钮必然失败。
    ⇒ 现在导出**真实**员工清单。

    ⚠️ **为什么只导出「基础字段」而不是全部**
    含密码哈希等敏感字段的导出会变成新的泄漏面
    ⇒ 只导出管理员真正要用的：姓名 / 账号 / 部门 / 岗位 / 状态 / 角色。
    """
    _require_employee_admin(db, current_user)

    employees = db.query(Employee).order_by(Employee.name).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["员工ID", "姓名", "登录账号", "手机号", "部门", "岗位", "在职状态", "账号状态", "角色"])
    for employee in employees:
        item = system_views.build_user_item(db, employee)
        writer.writerow([
            employee.id,
            employee.name,
            employee.login_account or "",
            employee.mobile or "",
            item["departmentName"],
            employee.position or "",
            employee.employment_status or "",
            employee.account_status or "",
            ",".join(item["roleNames"]),
        ])

    body = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([body]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=user-list.csv",
            "Cache-Control": "no-store",
        },
    )
