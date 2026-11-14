"""飞书 API 客户端。

文档：https://open.feishu.cn/document/home
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FeishuClient:
    """飞书 OpenAPI 客户端。"""

    BASE_URL = "https://open.feishu.cn/open-apis"
    TOKEN_ENDPOINT = "/auth/v3/tenant_access_token/internal"

    def __init__(self, app_id: str, app_secret: str, tenant_id: str | None = None):
        """初始化飞书客户端。

        Args:
            app_id: 应用 ID（cli_xxxxx）
            app_secret: 应用 Secret
            tenant_id: 企业 ID（可选，某些 API 需要）
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.tenant_id = tenant_id
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._http_client = httpx.Client(base_url=self.BASE_URL)

    def _get_access_token(self) -> str:
        """获取 access_token（带缓存和自动刷新）。

        Returns:
            access_token 字符串
        """
        now = datetime.now(timezone.utc)
        if self._access_token and self._token_expires_at and now < self._token_expires_at:
            return self._access_token

        logger.debug("Requesting new Feishu access token")
        resp = self._http_client.post(
            self.TOKEN_ENDPOINT,
            json={
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise Exception(f"Feishu error: {data}")

        self._access_token = data["tenant_access_token"]
        expires_in = data.get("expire", 7200)  # 默认 2 小时
        self._token_expires_at = datetime.now(timezone.utc).replace(
            microsecond=0
        ) + __import__("datetime").timedelta(seconds=expires_in - 300)  # 提前 5 分钟刷新
        logger.debug(f"Access token refreshed, expires in {expires_in}s")
        return self._access_token

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送认证请求。

        Args:
            method: HTTP 方法（GET、POST）
            endpoint: API 端点（相对路径）
            params: 查询参数
            json: 请求体

        Returns:
            响应 JSON 对象
        """
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
        }
        resp = self._http_client.request(
            method,
            endpoint,
            params=params,
            json=json,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        # 飞书 API 格式：{"code": 0, "msg": "success", "data": {...}}
        if data.get("code") != 0:
            msg = data.get("msg", "Unknown error")
            logger.error(f"Feishu API error: {msg}")
            raise Exception(f"Feishu API error: {msg}")

        return data.get("data", {})

    # -------------------------------------------------------
    # 部门 API（通讯录）
    # -------------------------------------------------------

    def fetch_departments(self, page_size: int = 50) -> list[dict[str, Any]]:
        """拉取所有部门（含子部门）。

        文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/contact-v3/department/list

        注意：`/contact/v3/departments` 在不带 department_id 时只返回根部门（id=0）。
        要拿到整棵组织树，必须用 `/contact/v3/departments/0/children` 并带 fetch_child=true。

        Returns:
            部门列表，每个部门包含：
                - department_id: 部门 ID
                - name: 部门名称
                - parent_department_id: 父部门 ID
                - leader_user_id: 部门主管 user_id
                - status: 1=正常，2=停用
        """
        # 飞书 API 限制：page_size 最大为 50
        page_size = min(page_size, 50)
        departments: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params = {
                "page_size": page_size,
                "department_id_type": "department_id",
                "fetch_child": "true",
            }
            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET", "/contact/v3/departments/0/children", params=params
            )

            items = data.get("items", [])
            departments.extend(items)

            # 判断是否还有下一页
            page_token = data.get("page_token")
            if not page_token:
                break

        logger.info(f"Fetched {len(departments)} departments from Feishu")
        return departments

    # -------------------------------------------------------
    # 员工 API（通讯录）
    # -------------------------------------------------------

    def fetch_employees(
        self,
        page_size: int = 50,
        departments: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """拉取所有员工。

        文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/contact-v3/user/list

        ⚠️ 重要：`/contact/v3/users` **不带 department_id 时会返回空列表**
        （当前企业的应用可见范围限制所致）。必须按部门逐个查询再合并去重。

        Args:
            page_size: 每页数量（最大 50）
            departments: 已知部门列表；不传则自动先拉取部门

        Returns:
            员工列表（按 user_id 去重），每个员工包含：
                - user_id: 用户 ID
                - name: 姓名
                - open_id: 开放 ID
                - department_ids: 所在部门 ID 列表
                - leader_user_id: 直属主管 user_id
                - email: 邮箱
                - status: {"is_activated": true/false, "is_exited": true/false}
        """
        # 飞书 API 限制：page_size 最大为 50
        page_size = min(page_size, 50)

        if departments is None:
            departments = self.fetch_departments()

        employees: dict[str, dict[str, Any]] = {}
        empty_departments: list[str] = []

        for dept in departments:
            dept_id = dept.get("department_id")
            if not dept_id:
                continue

            dept_employees = self._fetch_users_in_department(dept_id, page_size)
            if not dept_employees:
                empty_departments.append(f"{dept.get('name') or dept_id}")

            for emp in dept_employees:
                key = emp.get("user_id") or emp.get("open_id")
                if not key:
                    continue
                # 同一员工可能属于多个部门，合并 department_ids
                if key in employees:
                    existing = employees[key].get("department_ids") or []
                    for d in emp.get("department_ids") or []:
                        if d not in existing:
                            existing.append(d)
                    employees[key]["department_ids"] = existing
                else:
                    employees[key] = emp

        result = list(employees.values())
        logger.info(
            f"Fetched {len(result)} unique employees from Feishu "
            f"({len(departments)} departments, {len(empty_departments)} empty)"
        )
        return result

    def _fetch_users_in_department(
        self, department_id: str, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """拉取单个部门下的直属员工（分页）。"""
        page_size = min(page_size, 50)
        users: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "page_size": page_size,
                "department_id": department_id,
                "department_id_type": "department_id",
                "user_id_type": "user_id",
            }
            if page_token:
                params["page_token"] = page_token

            try:
                data = self._request("GET", "/contact/v3/users", params=params)
            except Exception as exc:  # 单个部门失败不影响整体
                logger.warning(
                    f"Failed to fetch users in department {department_id}: {exc}"
                )
                break

            items = data.get("items", [])
            users.extend(items)

            page_token = data.get("page_token")
            if not page_token:
                break

        return users

    def fetch_employee(self, user_id: str) -> dict[str, Any]:
        """拉取单个员工详情。

        文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/contact-v3/user/get

        Args:
            user_id: 用户 ID

        Returns:
            员工详情
        """
        data = self._request("GET", f"/contact/v3/users/{user_id}")
        logger.debug(f"Fetched employee {user_id}")
        return data.get("user", {})

    # -------------------------------------------------------
    # 审批 API（可选，用于接收表单提交事件）
    # -------------------------------------------------------

    def fetch_approval_instance(self, instance_id: str) -> dict[str, Any]:
        """拉取审批实例详情（用于表单提交回调）。

        文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/approval-v4/approval-instance/get

        Args:
            instance_id: 审批实例 ID

        Returns:
            审批实例详情
        """
        data = self._request("GET", f"/approval/v4/instances/{instance_id}")
        logger.debug(f"Fetched approval instance {instance_id}")
        return data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._http_client.close()

    def close(self):
        self._http_client.close()
