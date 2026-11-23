"""飞书免登 API（FS-1 / FS-2）。

端点：

1. ``GET  /v1/feishu/auth/url``      —— 生成飞书授权 URL（含防 CSRF state）
2. ``POST /v1/feishu/auth/exchange`` —— 用授权码换本系统 token

⚠️ 两个端点都**不要求登录态** —— 它们本身就是登录入口。

安全边界
--------
- **飞书 token 绝不出现在响应里。** ``user_access_token`` 在服务端
  用完即弃；响应中只有本系统签发的 HMAC token。
- **state 一次性**（见 ``services/feishu_oauth.py`` 的约束 C2）。
- **redirect_uri 白名单** —— 防止开放重定向。
- **只认在职且启用的员工** —— 复用 ``resolve_employee()``。
- **未配置 OAuth 时返回 503** —— 与「审批/通讯录 webhook 未配 Token
  就 503」同一套语义：宁可明确失败，也不留半残入口。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..domain.models import Employee
from ..services.access_control import build_access_context
from ..services.feishu_client import FeishuClient
from ..services.feishu_oauth import FeishuOAuthError, FeishuOAuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/feishu/auth", tags=["feishu-auth"])


# ─────────────────────────────────────────────────────
# 请求/响应模型
# ─────────────────────────────────────────────────────


class AuthUrlResponse(BaseModel):
    """授权 URL 响应。"""

    code: int = 0
    data: dict[str, Any]


class ExchangeRequest(BaseModel):
    """授权码换 token 的请求。"""

    code: str = Field(min_length=1, max_length=512, description="飞书授权码")
    state: str = Field(min_length=1, max_length=512, description="防 CSRF state")


class ExchangeResponse(BaseModel):
    """免登成功后的响应。

    ⚠️ 与 ``/v1/auth/login`` 的响应结构**保持一致** ——
    前端可以用同一套代码处理两种登录方式。
    """

    code: int = 0
    data: dict[str, Any]


# ─────────────────────────────────────────────────────
# 依赖
# ─────────────────────────────────────────────────────


def get_feishu_client() -> FeishuClient:
    """构造飞书客户端。

    ⚠️ 每次请求新建 —— 因为客户端内部缓存着 token。
    多实例部署时各自维护缓存（FS-7 会换 Redis 共享）。
    """
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(
            status_code=503, detail="未配置飞书应用凭据，无法使用飞书免登"
        )
    return FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )


def _oauth_service(db: Session, client: FeishuClient) -> FeishuOAuthService:
    return FeishuOAuthService(client, db)


# ─────────────────────────────────────────────────────
# 端点
# ─────────────────────────────────────────────────────


@router.get("/url", response_model=AuthUrlResponse)
def get_auth_url(
    redirect_uri: str = Query(
        ...,
        description="授权成功后的回调地址（必须在白名单内）",
    ),
    db: Session = Depends(get_db),
    client: FeishuClient = Depends(get_feishu_client),
) -> AuthUrlResponse:
    """生成飞书授权 URL。

    前端拿这个 URL 跳到飞书授权页；用户授权后飞书带 ``code`` 和 ``state``
    回调回来，前端再把两者交给 ``/exchange``。
    """
    service = _oauth_service(db, client)
    try:
        state = service.issue_state(redirect_uri)
    except FeishuOAuthError as exc:
        if exc.code == "oauth_not_configured":
            raise HTTPException(status_code=503, detail=exc.message) from exc
        if exc.code == "redirect_not_allowed":
            raise HTTPException(status_code=400, detail=exc.message) from exc
        raise HTTPException(status_code=500, detail=exc.message) from exc

    authorize_url = service.build_authorize_url(redirect_uri, state)
    return AuthUrlResponse(
        data={
            "authorizeUrl": authorize_url,
            "state": state,
            "redirectUri": redirect_uri,
        }
    )


@router.post("/exchange", response_model=ExchangeResponse)
def exchange(
    payload: ExchangeRequest,
    db: Session = Depends(get_db),
    client: FeishuClient = Depends(get_feishu_client),
) -> ExchangeResponse:
    """用飞书授权码换本系统 token。

    ⚠️ 响应结构与 ``/v1/auth/login`` 一致 —— 前端一套代码处理两种登录。
    """
    # ⚠️ 复用 auth.py 的 token 生成与权限解析 ——
    #    这样免登**自动继承** token_version 失效机制
    #    （离职/禁用后旧 token 立即失效），无需另写一套。
    from .auth import _generate_token, _resolve_modules, _resolve_permissions

    service = _oauth_service(db, client)
    try:
        result = service.exchange_code_for_employee(payload.code, payload.state)
    except FeishuOAuthError as exc:
        status = 400
        if exc.code == "identity_not_bound":
            status = 403
        elif exc.code in ("code_exchange_failed", "no_access_token", "user_info_failed"):
            status = 502
        elif exc.code in (
            "state_invalid",
            "state_expired",
            "state_consumed",
            "state_mismatch",
        ):
            status = 400
        # ⚠️⚠️ **detail 里带上细粒度诊断码**（2026-11-17 改）。
        #
        #    为什么：``identity_not_bound`` 背后有四种要处理方式完全不同的情况
        #    （已离职 / 已停用 / 未同步 / 数据冲突）。
        #    只回一句中文文案，前端只能显示通用提示 ——
        #    用户看到「未绑定」会以为要重新绑定，但**已离职的人根本不该绑定**。
        #
        #    前端可据此给出针对性引导（见 ``FeishuCallback.vue``）。
        #    ⚠️ 用 dict 而非纯字符串 —— FastAPI 的 detail 支持结构化内容。
        detail: Any = exc.message
        if exc.reason:
            detail = {"message": exc.message, "reason": exc.reason}
        raise HTTPException(status_code=status, detail=detail) from exc

    employee = db.get(Employee, result.employee_id)
    if employee is None:  # pragma: no cover - resolve_employee 已保证存在
        raise HTTPException(status_code=404, detail="员工不存在")

    context = build_access_context(db, employee)
    permissions, backend_permissions = _resolve_permissions(db, employee)

    access_token, refresh_token = _generate_token(
        employee.id, result.username, employee.token_version
    )

    logger.info("飞书免登成功 employee=%s", employee.id)

    return ExchangeResponse(
        data={
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresIn": 7 * 24 * 3600,
            "userId": employee.id,
            "username": result.username,
            "realName": result.real_name,
            "forceChangePassword": bool(employee.force_change_password),
            "roles": sorted(context.roles),
            "permissions": permissions,
            "backendPermissions": backend_permissions,
            # ⚠️ **部门可用的功能板块** —— 前端菜单按它过滤
            #    （与 ``/auth/login``、``/auth/me`` 保持同构）
            "modules": _resolve_modules(db, employee),
        }
    )
