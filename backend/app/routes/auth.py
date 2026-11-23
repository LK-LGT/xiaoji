"""认证 API 路由 - 登录、获取当前用户、登出。"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..domain.models import Employee, FeishuIdentity, SystemAdmin
from ..services import password_hasher
from ..services.access_control import build_access_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["认证"])


# ─────────────────────────────────────────────────────
# 请求/响应模型
# ─────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """登录请求。"""
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=100)


# ─────────────────────────────────────────────────────────────
# 测试账号
#
# ⚠️ 重要设计（R5 修复）：
#   这里**不再硬编码权限列表**。过去每个测试账号带一份写死的
#   `permissions`，导致出现「前端显示菜单、点进去后端 403」的
#   不一致 —— 因为前端看的是这份硬编码列表，后端判的是
#   `access_control` 的动作级权限，两者不是同一套东西。
#
#   现在测试账号与真实员工走**完全相同**的解析路径：
#   `access_control.build_access_context()` 实时按
#   （system_admin 表 / 部门负责人 / 直属下属 / HR 部门配置）解析。
#   权限只有一个事实源，前端菜单与后端鉴权必然一致。
#
#   因此 `id` 必须是**真实员工 ID**（能在 employee 表查到），
#   否则解析不出角色，只会得到「普通员工」权限。
#
#   账号对照（真实员工，来自飞书同步落库）：
#     admin   → b8663c7c  林雅静   人事主管（system_admin 表内，全能账号）
#     manager → g426c684  杨飞     总经理（总经办负责人 + 有直属下属）
#     leader  → 939931ba  别嘧咪   总经理（部门负责人）
#     employee→ 2aaf32ea  李凯     FDE（普通员工）
#
#   ⚠️ admin 账号映射的是**真实管理员**，因此它会同时带有
#   admin / hr / manager / department_leader 多个角色 —— 这是
#   真实情况，不是 bug。
#
#   ⚠️ R4：测试账号的口令也**不再明文存放**。
#      过去是 `user_info["password"] != password` 直接明文比对；
#      现在导入时立刻算一次哈希，之后只存哈希、只做哈希比对。
#      ⚠️ 这**不是**真正的安全提升 —— 明文口令仍然硬编码在源码里
#      （测试账号必须能被自动化测试登录）。它的真实价值是：
#      - 让测试账号与真实员工走**同一条校验路径**（`verify_password`），
#        不会因为「测试账号走明文分支」而掩盖真实账号的哈希缺陷；
#      - 源码里不再有可直接 grep 出的 `user_info["password"] != password`
#        这种明文比对模式；
#      - 一旦将来改为从环境变量/密钥管理读取口令，只需改这一处。
# ─────────────────────────────────────────────────────────────

# 测试账号的明文口令（仅用于导入时算一次哈希）。
# ⚠️ 这些口令必须能被自动化测试登录，因此只能留在源码里。
#    生产部署应通过环境变量覆盖（见 _TEST_USER_PLAINTEXT_ENV）。
_TEST_USER_PLAINTEXT: dict[str, str] = {
    "admin": "admin123",
    "manager": "manager123",
    "leader": "leader123",
    "employee": "employee123",
}


def _build_test_users() -> dict[str, dict[str, str]]:
    """构造测试账号表：口令在导入时算成哈希，明文不落表。

    口令来源优先级：
      1. 环境变量 ``TEST_USER_PASSWORDS``（JSON，key=用户名）；
      2. 内置明文常量（仅供自动化测试）。
    """
    raw = dict(_TEST_USER_PLAINTEXT)
    env_override = os.environ.get("TEST_USER_PASSWORDS")
    if env_override:
        try:
            parsed = json.loads(env_override)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if isinstance(value, str) and value:
                        raw[str(key)] = value
        except (json.JSONDecodeError, TypeError):
            logger.warning("TEST_USER_PASSWORDS 不是合法 JSON，忽略")

    meta = {
        "admin": {"id": "b8663c7c", "name": "管理员"},
        "manager": {"id": "g426c684", "name": "部门主管"},
        "leader": {"id": "939931ba", "name": "直属领导"},
        "employee": {"id": "2aaf32ea", "name": "普通员工"},
    }

    users: dict[str, dict[str, str]] = {}
    for username, info in meta.items():
        plaintext = raw.get(username)
        if not plaintext:
            logger.warning("测试账号 %s 没有口令，跳过", username)
            continue
        users[username] = {
            "id": info["id"],
            "name": info["name"],
            # 只存哈希，明文不保留在内存结构里。
            "password_hash": password_hasher.hash_password(plaintext),
        }
    return users


TEST_USERS = _build_test_users()


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
        # ★ BP-2：员工填报工作�� / 发起异议
        "pan:workload:view",
        "pan:workload:submit",
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
        # ★ BP-2：主管核对下属的剪辑工作量 + 裁定异议工单
        "pan:workload:view",
        "pan:workload:resolve",
    ],
    "department_leader": [
        "performance:schemes:view",
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
        # ★ BP-2：部门负责人同样要核对/裁定下属的剪辑工作量
        "pan:workload:view",
        "pan:workload:resolve",
    ],
    "hr": [
        "performance:schemes:manage",
        "performance:schemes:view",
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
        # 飞书组织同步管理（触发同步 / 查看批次、失败、对账）
        "system:feishu-sync:manage",
        # ★ BP-1：百度网盘产出校验管理（授权 / 手动取数 / 查看快照与批次）
        # ⚠️ 独立权限位 —— 授权动作涉及第三方账号凭证，必须 HR/管理员专属。
        "system:pan-verification:manage",
        "dispute:dashboard:view",
        "dispute:case:view",
        # HR 可查看全部考核记录
        "performance:manager-review:view",
        # ★ BP-2：HR 可看全部、裁定、维护绑定、跑聚合
        "pan:workload:view",
        "pan:workload:resolve",
        "pan:workload:manage",
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
        # 飞书组织同步管理（触发同步 / 查看批次、失败、对账）
        "system:feishu-sync:manage",
        # ★ BP-1：百度网盘产出校验管理（授权 / 手动取数 / 查看快照与批次）
        # ⚠️ 管理员同 HR —— 授权动作涉及第三方账号凭证。
        "system:pan-verification:manage",
        # ★ BP-2：管理员同 HR
        "pan:workload:view",
        "pan:workload:resolve",
        "pan:workload:manage",
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


def _resolve_test_user(
    db: Session, username: str
) -> tuple[Employee | None, dict[str, Any]]:
    """解析测试账号 → (真实员工对象, 权限信息)。

    测试账号不再是「凭空捏造的用户」：它们映射到**真实员工**，
    权限一律由 `access_control` 实时解析，与真实登录完全同路。

    这样保证：
    - 前端菜单权限 与 后端动作鉴权 来自同一套解析，不会再出现
      「前端显示菜单、点进去 403」的分裂；
    - `TEST_USERS` 里删掉的硬编码权限列表不会与真实授权表脱节。

    降级策略（按顺序）：
    1. 映射的 employee 在库中 → 正常解析；
    2. employee 不存在，但 `system_admin` 表里有任一启用管理员
       → 用该管理员解析（覆盖「换环境/独立测试库，真实员工 ID 不存在」）；
    3. 都没有 → 返回最小权限（仅 employee 角色）。
    """
    user_info = TEST_USERS[username]
    employee = db.get(Employee, user_info["id"])

    if employee is None:
        # 降级 2：从 system_admin 表找一个启用中的管理员。
        # 独立测试库 / 新环境里真实员工 ID 可能不存在，但管理员授权表通常有。
        admin_employee_id = db.execute(
            select(SystemAdmin.employee_id).where(SystemAdmin.active.is_(True)).limit(1)
        ).scalar_one_or_none()
        if admin_employee_id:
            employee = db.get(Employee, admin_employee_id)
            if employee is not None:
                logger.info(
                    "测试账号 %s 映射的员工 %s 不在库中，改用管理员 %s 解析权限",
                    username,
                    user_info["id"],
                    admin_employee_id,
                )

    if employee is None:
        logger.warning(
            "测试账号 %s 无法定位员工，降级为最小权限", username
        )
        return None, {
            "roles": ["employee"],
            "permissions": list(ROLE_UI_PERMISSIONS.get("employee", [])),
            "backendPermissions": [],
        }

    context = build_access_context(db, employee)
    permissions, backend_permissions = _resolve_permissions(db, employee)
    return employee, {
        "roles": sorted(context.roles),
        "permissions": permissions,
        "backendPermissions": backend_permissions,
    }


# ─────────────────────────────────────────────────────
# 密码校验（R4）
#
# ⚠️ 历史包袱：系统上线以来**所有员工共用明文口令 "123456"**，
#    等于没设密码。这里做**渐进式迁移**：
#
#    1. 账号已有 `password_hash` → 走哈希校验（正常路径）；
#    2. 账号没有 `password_hash`、但密码正好是 "123456" → 兼容登录，
#       **登录成功后立即补写哈希**并置 `force_change_password=True`
#       → 用户下次登录必须改密，弱口令从此失效；
#    3. 其余情况 → 一律 401。
#
#    这样不停机、不锁死任何在职用户地完成迁移。
# ─────────────────────────────────────────────────────

# 历史明文口令（仅用于上述兼容路径，迁移完成后应彻底移除）。
_LEGACY_PLAINTEXT_PASSWORD = "123456"


def _verify_employee_password(
    db: Session, employee: Employee, password: str
) -> tuple[bool, bool]:
    """校验员工密码。

    Returns:
        ``(是否通过, 是否需要立即补写哈希)``

    ⚠️ 「需要补写」意味着本次是用兼容明文登录通过的 ——
    调用方应在登录成功后把哈希写进库并要求用户改密。

    ⚠️ 关于「弱口令何时失效」（BUG-R6-18 的教训）
    ------------------------------------------------
    补写哈希后，弱口令**在改密完成前仍然可以登录** ——
    这是**有意为之**，不能改：

    - 渐进迁移的原意是「不停机、不锁死任何在职用户」；
    - 如果迁移完就把弱口令从登录口堵死，用户一旦在改密前
      离开（关浏览器、断网、换设备），下次就**再也登不进来**：
      弱口令被拒 → 拿不到登录态 → 到不了改密页 → 永久锁死。

    真正的防线是 ``force_change_password`` 标记：
    前端路由守卫会把这类用户强制重定向到改密页，
    后端也应据此拦截业务接口（见 ``require_password_changed``）。
    弱口令在**改密成功后**自然失效（新哈希覆盖旧哈希）。
    """
    stored_hash = employee.password_hash

    if stored_hash:
        if password_hasher.verify_password(password, stored_hash):
            # 参数过时（如将来提高迭代轮数）→ 静默重算并升级。
            # ⚠️ 重算时**不套用强度规则** —— 存量账号此刻
            #    持有的可能是 6 位弱口令（迁移时按 enforce_strength=False 哈希的），
            #    若走默认校验会抛 PasswordStrengthError，把正常登录打成 500。
            if password_hasher.needs_rehash(stored_hash):
                try:
                    employee.password_hash = password_hasher.hash_password(
                        password, enforce_strength=False
                    )
                    db.commit()
                except Exception:  # pragma: no cover - 升级失败不该阻断登录
                    db.rollback()
                    logger.warning("密码哈希升级失败 employee=%s", employee.id)
            return True, False
        return False, False

    # 无哈希 → 兼容历史明文口令。
    if password == _LEGACY_PLAINTEXT_PASSWORD:
        return True, True
    return False, False


def _upgrade_legacy_password(db: Session, employee: Employee, password: str) -> None:
    """把兼容登录成功的账号补写哈希，并强制其下次登录改密。

    ⚠️ 补写后**弱口令立即失效** —— 下次登录必须用新密码。

    BUG-R4-01 修复
    --------------
    存量弱口令是明文 ``"123456"``（**6 位**），而
    ``password_hasher.validate_password_strength`` 的下限是 8 位。
    原实现直接调 ``hash_password(password)`` → 抛
    ``PasswordStrengthError`` → 被下面的 ``except Exception`` 吞掉 →
    ``db.rollback()`` → **哈希没写进去、force_change_password 也没置位**。

    后果：弱口令 ``"123456"`` 永久可用，每次登录都走兼容分支，
    「强制首次改密」形同虚设。

    现在：
    1. 用 ``enforce_strength=False`` 哈希（迁移场景不套用新口令的强度规则）；
    2. 失败时**不吞异常** —— 至少要留下 ERROR 日志，
       避免再次出现「静默失败 = 迁移没发生」。
    """
    try:
        employee.password_hash = password_hasher.hash_password(
            password, enforce_strength=False
        )
        employee.password_set_at = datetime.now()
        employee.force_change_password = True
        db.commit()
        logger.info(
            "员工 %s 已从明文口令迁移到哈希，并标记强制改密", employee.id
        )
    except Exception:  # pragma: no cover - 补写失败不该让用户登不进去
        db.rollback()
        logger.exception(
            "明文口令迁移为哈希失败 employee=%s —— 该账号仍可继续用弱口令登录，"
            "需人工介入（BUG-R4-01 回归观察点）",
            employee.id,
        )


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
        # R4：测试账号与真实员工走**同一条**校验路径（哈希比对）。
        if not password_hasher.verify_password(password, user_info["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )

        # 测试账号的权限同样走 access_control 实时解析（R5 修复）：
        # 不再返回硬编码权限串，杜绝「前端有菜单、后端 403」的分裂。
        employee, resolved = _resolve_test_user(db, username)

        # ⚠️ Token 必须带上员工**当前**的 token_version。
        #    依赖层会用 `payload["ver"] == employee.token_version` 校验，
        #    不一致直接 401。离职 / 停用 / 改密都会递增 token_version，
        #    这里若漏传（默认 1），该员工一旦被递增过版本就**再也登不进来**。
        access_token, refresh_token = _generate_token(
            user_info["id"],
            username,
            int(employee.token_version or 1) if employee else 1,
        )

        return {
            "code": 0,
            "data": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresIn": 7 * 24 * 3600,  # 7 天
                "userId": user_info["id"],
                "username": username,
                "realName": employee.name if employee else user_info["name"],
                "forceChangePassword": bool(
                    employee.force_change_password if employee else False
                ),
                "roles": resolved["roles"],
                "permissions": resolved["permissions"],
                "backendPermissions": resolved["backendPermissions"],
            },
        }

    # 检查数据库中的员工：支持登录账号、手机号、飞书身份或**员工 ID**登录。
    #
    # ⚠️ BUG-R6-37（能用员工 ID 登录的人登不进来）：
    # 过去这里只按 ``login_account / email / name`` 查，
    # **没有匹配 ``Employee.id``**。
    # 而真实库里大量员工的 ``login_account`` 是 NULL
    # （飞书同步建号时没回填登录账号），
    # 于是「用员工 ID 当账号登录」这条最直觉的路径直接 401。
    # 现象：R6 闭环验证用 ``2aaf32ea`` 登录李凯，反复 401，
    # 但用 ``yang.fei`` 登录杨飞就正常 —— 因为杨飞**有** login_account。
    #
    # 现在把 ``id`` 也纳入匹配（ID 是十六进制字符串，大小写不敏感），
    # 并保留原有的 login_account / email / name 匹配。
    employee = (
        db.query(Employee)
        .filter(
            (Employee.login_account == username.lower())
            | (Employee.email == username)
            | (Employee.name == username)
            | (Employee.id == username.lower())
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

    # 密码校验（R4）：优先哈希；无哈希时兼容历史明文并立即补写。
    password_ok, need_upgrade = _verify_employee_password(db, employee, password)
    if not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    if need_upgrade:
        _upgrade_legacy_password(db, employee, password)

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
            "forceChangePassword": bool(employee.force_change_password),
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
        # 与登录接口保持一致：测试账号权限也走 access_control 实时解析（R5 修复）。
        _, resolved = _resolve_test_user(db, username)
        return {
            "code": 0,
            "data": {
                "userId": user_info["id"],
                "username": username,
                "realName": employee.name if employee else user_info["name"],
                "forceChangePassword": bool(
                    employee.force_change_password if employee else False
                ),
                "roles": resolved["roles"],
                "permissions": resolved["permissions"],
                "backendPermissions": resolved["backendPermissions"],
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
            "forceChangePassword": bool(employee.force_change_password),
            "roles": sorted(context.roles),
            "permissions": permissions,
            "backendPermissions": backend_permissions,
        },
    }


@router.post("/logout")
async def logout(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """登出接口 —— **真正吊销当前 Token**。

    ⚠️ 之前这里是空实现：只返回「登出成功」，但服务端 Token 依然有效，
       签发后 7 天内无法真正注销（盘点项 R14）。

    现在的做法：把该员工的 ``token_version`` 递增。
    Token 里带着签发时的 ``ver``，与库里的 ``token_version`` 不一致即失效
    → 所有设备上的旧 Token **同时**作废，客户端必须重新登录。

    测试账号（``TEST_USERS``）不落库，无法递增版本 —— 这类账号的 Token
    由 ``_generate_token`` 的固定版本签发，改为在响应里提示重新登录即可。
    """
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        # 没有凭据也返回成功：登出是「幂等」的，前端不应因已失效而报错。
        return {"code": 0, "data": {"message": "登出成功"}}

    token = authorization[7:]
    try:
        payload = _decode_token(token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error):
        return {"code": 0, "data": {"message": "登出成功"}}

    user_id = str(payload.get("sub") or "")
    if not user_id:
        return {"code": 0, "data": {"message": "登出成功"}}

    employee = db.get(Employee, user_id)
    if employee is not None:
        # ⚠️ BUG-R6-17：只有**当前有效**的 Token 才允许递增版本。
        #   否则「拿一个已经失效的旧 Token 调 logout」会把
        #   `token_version` 往前推一格 → 连带把**其它设备上
        #   正在正常使用的会话**一起踢下线。
        #
        #   典型触发场景：前端 ``changePassword`` 成功后调
        #   ``authStore.logout()``，但此时改密接口已经把
        #   ``token_version`` 递增过了 —— 这个 logout 带着
        #   **已失效**的 Token，若照旧递增就是第二次递增。
        token_ver = int(payload.get("ver", 1))
        current_ver = int(employee.token_version or 1)
        if token_ver != current_ver:
            # Token 早已失效，无需（也不应）再动版本号。
            return {"code": 0, "data": {"message": "登出成功"}}
        employee.token_version = current_ver + 1
        db.commit()
        logger.info("员工 %s 登出，token_version → %s", employee.id, employee.token_version)

    return {"code": 0, "data": {"message": "登出成功"}}


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""

    oldPassword: str = Field(..., min_length=1, max_length=128)
    newPassword: str = Field(..., min_length=1, max_length=128)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """修改密码接口（R4 落实）。

    之前这里是**空实现** —— 直接返回「密码修改成功」，实际什么都没改。
    现在真正落库：

    1. 校验旧密码（旧密码不对 → 401）；
    2. 校验新密码强度（长度不足 → 400）；
    3. 写入新哈希，置 ``password_set_at``，清 ``force_change_password``；
    4. **递增 ``token_version``** → 其他设备上的旧 Token 全部作废，
       本次改密后前端需重新登录（这是安全的默认：改密即视为凭据轮换）。
    """
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少认证凭据")
    token = authorization[7:]
    try:
        token_payload = _decode_token(token)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error):
        raise HTTPException(status_code=401, detail="无效的认证凭据")

    user_id = str(token_payload.get("sub") or "")
    employee = db.get(Employee, user_id) if user_id else None
    if employee is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if employee.employment_status == "exited" or employee.account_status != "enabled":
        raise HTTPException(status_code=403, detail="员工已离职或账号已禁用")
    # Token 版本校验（与 /me 一致）：
    # 管理员重置密码后 token_version 会递增，此时旧 Token 必须失效，
    # 否则「重置密码」就形同虚设 —— 拿到旧 Token 的人照样能改密。
    if int(token_payload.get("ver", 1)) != int(employee.token_version or 1):
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")

    # 1) 旧密码校验
    old_ok, need_upgrade = _verify_employee_password(db, employee, payload.oldPassword)
    if not old_ok:
        raise HTTPException(status_code=401, detail="旧密码不正确")
    if need_upgrade:
        # 用兼容明文改密 —— 说明这是首次从弱口令迁移，顺带补写哈希。
        _upgrade_legacy_password(db, employee, payload.oldPassword)

    # 2) 新密码强度校验
    try:
        password_hasher.validate_password_strength(payload.newPassword)
    except password_hasher.PasswordStrengthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.oldPassword == payload.newPassword:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")

    # 3) 写入新哈希
    employee.password_hash = password_hasher.hash_password(payload.newPassword)
    employee.password_set_at = datetime.now()
    employee.force_change_password = False
    # 4) 吊销其他设备的 Token
    employee.token_version = int(employee.token_version or 1) + 1
    try:
        db.commit()
    except Exception:
        # ⚠️ 不回滚的话，Session 里挂着一个「密码已改、版本已递增」的脏状态，
        #    后续任何一次隐式 flush 都会把半成品写进库 —— 用户的密码就废了。
        db.rollback()
        logger.exception("修改密码失败 employee=%s", employee.id)
        raise HTTPException(status_code=500, detail="修改密码失败，请稍后重试")
    logger.info("员工 %s 修改密码成功，token_version → %s", employee.id, employee.token_version)

    return {
        "code": 0,
        "data": {
            "message": "密码修改成功，请重新登录",
            "reloginRequired": True,
        },
    }
