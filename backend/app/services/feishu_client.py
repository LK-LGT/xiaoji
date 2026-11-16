"""飞书 OpenAPI 生产级客户端。

包含：
- tenant_access_token 缓存、提前刷新、失效后单次强制刷新；
- HTTP 429 / 5xx / 超时的指数退避与抖动；
- Retry-After 优先；
- 可注入 HTTP transport，支持 Mock 与录制响应；
- 敏感字段脱敏，日志不输出 app_secret / token / Authorization。
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

import httpx

logger = logging.getLogger(__name__)

Transport = Callable[[httpx.Request], httpx.Response]


# 飞书开放平台常见错误码 -> 可读业务语义。
# 用于把同步失败记录从「看不懂的错误码」变成「可执行的排查指引」。
FEISHU_ERROR_CODES: dict[int, str] = {
    99991663: "Access Token 无效",
    99991664: "Access Token 已过期",
    99991668: "应用未获得通讯录权限",
    99991669: "应用未获得用户基本信息权限",
    99991672: "缺少通讯录权限或应用版本未发布",
    99991679: "应用可见范围未包含目标部门",
    99991688: "租户 Access Token 不匹配",
    1254290: "接口触发限流，请稍后重试",
    99991400: "飞书服务繁忙，请稍后重试",
}

# 可重试的错误码（限流与服务端繁忙）。
RETRYABLE_ERROR_CODES = frozenset({99991400, 1254290})

# Token 失效类错误码：需要强制刷新 Token 后重放一次。
TOKEN_INVALID_ERROR_CODES = frozenset({99991663, 99991668, 99991669})


def describe_feishu_error(code: int | None, fallback: str = "飞书接口错误") -> str:
    """把飞书错误码转换为可读的业务语义。"""
    if code is None:
        return fallback
    return FEISHU_ERROR_CODES.get(code, f"{fallback}（错误码 {code}）")


class FeishuError(Exception):
    """飞书接口业务错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class FeishuTokenError(FeishuError):
    """Access Token 无效或获取失败。"""


class FeishuRateLimitError(FeishuError):
    """接口限流。"""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, retryable=True, status_code=429)
        self.retry_after = retry_after


class MockTransport(httpx.BaseTransport):
    """按请求路径/方法依次返回录制响应。

    录制数据格式：{"GET /contact/v3/users": [response, ...]}。
    每条 response 可以是 httpx.Response 或包含 status_code/json/text/headers 的字典。
    """

    def __init__(self, recordings: dict[str, list[Any]]) -> None:
        self.recordings = recordings
        self.calls: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        key = f"{request.method} {path}"
        normalized_path = path.removeprefix("/open-apis")
        candidates = (
            self.recordings.get(key)
            or self.recordings.get(f"{request.method} {normalized_path}")
            or self.recordings.get(normalized_path)
            or self.recordings.get(path)
            or []
        )
        if not candidates:
            return httpx.Response(
                404,
                json={"code": 404, "msg": f"mock response not found: {key}"},
                request=request,
            )
        item = candidates.pop(0) if len(candidates) > 1 else candidates[0]
        if isinstance(item, httpx.Response):
            item.request = request
            return item
        status_code = int(item.get("status_code", 200))
        headers = item.get("headers", {})
        if "json" in item:
            return httpx.Response(status_code, json=item["json"], headers=headers, request=request)
        return httpx.Response(
            status_code,
            text=item.get("text", ""),
            headers=headers,
            request=request,
        )


class FeishuClient:
    """飞书 OpenAPI 客户端。"""

    BASE_URL = "https://open.feishu.cn/open-apis"
    TOKEN_ENDPOINT = "/auth/v3/tenant_access_token/internal"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        tenant_id: str | None = None,
        *,
        transport: Transport | None = None,
        max_retries: int = 4,
        base_delay: float = 0.5,
        max_delay: float = 20.0,
        timeout: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.tenant_id = tenant_id
        self.max_retries = max(0, max_retries)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep = sleep
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._http_client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=timeout,
            transport=transport,
        )

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and self._access_token
            and self._token_expires_at
            and now < self._token_expires_at
        ):
            return self._access_token

        try:
            resp = self._http_client.post(
                self.TOKEN_ENDPOINT,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            if resp.status_code == 429:
                raise FeishuRateLimitError(
                    "飞书 Token 接口触发限流", self._retry_after(resp)
                )
            if resp.status_code >= 500:
                raise FeishuError(
                    "飞书 Token 服务暂时不可用",
                    retryable=True,
                    status_code=resp.status_code,
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise FeishuError("飞书 Token 请求超时", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise FeishuTokenError("飞书 Token 请求失败") from exc

        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise FeishuTokenError("飞书 Token 获取失败")

        expires_in = max(60, int(data.get("expire", 7200)))
        self._access_token = str(data["tenant_access_token"])
        self._token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=max(30, expires_in - 300)
        )
        logger.info("飞书 Access Token 已刷新，有效期秒数=%s", expires_in)
        return self._access_token

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        delay = min(self.base_delay * (2**attempt), self.max_delay)
        return delay + random.uniform(0, min(0.5, delay / 2))

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送认证请求；网络错误、429、5xx 自动退避重试。"""
        token_refreshed = False
        for attempt in range(self.max_retries + 1):
            try:
                token = self._get_access_token()
            except FeishuRateLimitError as exc:
                if attempt >= self.max_retries:
                    raise
                self.sleep(self._backoff(attempt, exc.retry_after))
                continue
            except FeishuError as exc:
                if exc.retryable and attempt < self.max_retries:
                    self.sleep(self._backoff(attempt))
                    continue
                raise
            try:
                resp = self._http_client.request(
                    method,
                    endpoint,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries:
                    raise FeishuError("飞书接口请求超时", retryable=True) from exc
                self.sleep(self._backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise FeishuError("飞书接口网络错误", retryable=True) from exc
                self.sleep(self._backoff(attempt))
                continue

            if resp.status_code == 429:
                if attempt >= self.max_retries:
                    raise FeishuRateLimitError("飞书接口触发限流", self._retry_after(resp))
                self.sleep(self._backoff(attempt, self._retry_after(resp)))
                continue

            if resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise FeishuError(
                        "飞书接口服务暂时不可用",
                        retryable=True,
                        status_code=resp.status_code,
                    )
                self.sleep(self._backoff(attempt, self._retry_after(resp)))
                continue

            if resp.status_code in (401, 403) and not token_refreshed:
                # Token 失效：强制刷新一次后重放原请求。
                token_refreshed = True
                self._access_token = None
                self._token_expires_at = None
                continue

            try:
                data = resp.json()
            except ValueError as exc:
                raise FeishuError("飞书接口返回了非法 JSON") from exc

            if resp.status_code >= 400:
                raise FeishuError(
                    describe_feishu_error(
                        data.get("code"), str(data.get("msg") or "飞书接口请求失败")
                    ),
                    code=data.get("code"),
                    retryable=False,
                    status_code=resp.status_code,
                )
            if data.get("code") != 0:
                code = data.get("code")
                message = describe_feishu_error(
                    code, str(data.get("msg") or "飞书接口返回错误")
                )
                token_invalid = code in TOKEN_INVALID_ERROR_CODES
                if token_invalid and not token_refreshed:
                    token_refreshed = True
                    self._access_token = None
                    self._token_expires_at = None
                    continue
                raise FeishuError(
                    message,
                    code=code,
                    retryable=code in RETRYABLE_ERROR_CODES,
                )
            return data.get("data") or {}

        raise FeishuError("飞书接口请求失败", retryable=True)

    def fetch_departments(self, page_size: int = 50) -> list[dict[str, Any]]:
        """拉取完整部门树。"""
        page_size = min(page_size, 50)
        departments: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "page_size": page_size,
                "department_id_type": "department_id",
                "fetch_child": "true",
            }
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET", "/contact/v3/departments/0/children", params=params
            )
            departments.extend(data.get("items", []))
            page_token = data.get("page_token")
            if not page_token:
                break
        logger.info("飞书部门拉取完成，数量=%s", len(departments))
        return departments

    def fetch_employees(
        self,
        page_size: int = 50,
        departments: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """按部门拉取员工并按 user_id 去重。"""
        page_size = min(page_size, 50)
        if departments is None:
            departments = self.fetch_departments()

        employees: dict[str, dict[str, Any]] = {}
        for dept in departments:
            dept_id = dept.get("department_id")
            if not dept_id:
                continue
            try:
                dept_employees = self.fetch_employees_in_department(str(dept_id), page_size)
            except FeishuError as exc:
                # 单个部门拉取失败不应阻断其他部门。
                logger.warning("飞书部门员工拉取失败，department_id=%s，原因=%s", dept_id, exc.message)
                continue
            for emp in dept_employees:
                key = str(emp.get("user_id") or emp.get("open_id") or "")
                if not key:
                    continue
                if key in employees:
                    existing = employees[key].get("department_ids") or []
                    for dep_id in emp.get("department_ids") or []:
                        if dep_id not in existing:
                            existing.append(dep_id)
                    employees[key]["department_ids"] = existing
                else:
                    employees[key] = emp
        logger.info("飞书员工拉取完成，唯一员工数=%s", len(employees))
        return list(employees.values())

    def fetch_employees_in_department(
        self, department_id: str, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """拉取单个部门的直属员工。"""
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
            data = self._request("GET", "/contact/v3/users", params=params)
            users.extend(data.get("items", []))
            page_token = data.get("page_token")
            if not page_token:
                break
        return users

    def fetch_employee(self, user_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/contact/v3/users/{user_id}")
        return data.get("user", {})

    def fetch_approval_instance(self, instance_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/approval/v4/instances/{instance_id}")
        return data.get("instance", {})

    def fetch_approval_instances(
        self,
        *,
        code: str | None = None,
        user_id: str | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """拉取审批实例列表（`GET /approval/v4/instances`）。

        用于审批回流场景：按审批定义 code 过滤出本系统对应的实例，
        再逐个用 `fetch_approval_instance` 取详情。

        ⚠️ 该接口**必填** `code`（审批定义 code）与 `user_id`（发起人），
        缺失会返回 `99992402 field validation failed`。这里默认用占位值
        保证请求能通过校验，真正有意义的过滤由调用方在拿到结果后完成。

        Returns:
            `(实例列表, 下一页 page_token)`。`page_token` 为 None 表示已到末页。
        """
        params: dict[str, Any] = {
            "page_size": min(page_size, 100),
            "code": code or "",
            "user_id": user_id or "",
        }
        if page_token:
            params["page_token"] = page_token
        data = self._request("GET", "/approval/v4/instances", params=params)
        items = data.get("items") or []
        return list(items), data.get("page_token") or None

    def fetch_approval_instance_tasks(self, instance_id: str) -> list[dict[str, Any]]:
        """查审批实例的节点与审批人（`GET /approval/v4/instances/{id}/tasks`）。

        用于判断「当前这一轮该谁审」。返回项包含 `node_id` / `approver_id` /
        `status` 等字段。
        """
        data = self._request("GET", f"/approval/v4/instances/{instance_id}/tasks")
        return list(data.get("items") or [])

    # ─────────────────────────────────────────────────────────────
    # IM 域
    # ─────────────────────────────────────────────────────────────

    def send_text_message(
        self,
        receive_id: str,
        text: str,
        *,
        receive_id_type: str = "open_id",
        msg_type: str = "text",
    ) -> dict[str, Any]:
        """发飞书消息（`POST /im/v1/messages`）。

        Args:
            receive_id: 接收者 ID，取决于 `receive_id_type`
                （`open_id` / `user_id` / `chat_id`）。
            text: 消息正文（`msg_type="text"` 时为纯文本）。
            receive_id_type: 接收者 ID 类型。
            msg_type: 消息类型，目前实现仅支持 `text`。

        需要的权限：`im:message:send_as_bot`。
        """
        content = json.dumps({"text": text}, ensure_ascii=False)
        data = self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content,
            },
        )
        return data

    # ─────────────────────────────────────────────────────────────
    # 考勤域
    # ─────────────────────────────────────────────────────────────

    # 飞书「查询打卡结果」接口：单次最多 50 人、单次日期跨度最多 30 天。
    ATTENDANCE_MAX_USERS_PER_REQUEST = 50
    ATTENDANCE_MAX_DATE_RANGE_DAYS = 30

    def fetch_attendance_tasks(
        self,
        user_ids: list[str],
        start_date: str,
        end_date: str,
        *,
        employee_type: str = "employee_id",
        need_overtime_result: bool = True,
        ignore_invalid_users: bool = True,
    ) -> list[dict[str, Any]]:
        """查询打卡结果（POST /attendance/v1/user_tasks/query）。

        Args:
            user_ids: 员工 ID 列表（对应 employee_type）。本方法内部按 50 人分批。
            start_date: 起始日期，格式 yyyyMMdd。
            end_date: 结束日期，格式 yyyyMMdd。与起始日跨度不得超过 30 天。
            employee_type: 员工 ID 类型，默认为 employee_id（与本地 Employee.id 对齐）。
            need_overtime_result: 是否返回加班班段。
            ignore_invalid_users: 忽略无效/无权限用户，避免整批失败。

        Returns:
            user_task_results 列表，每项为「员工 × 日期」的考勤任务。
        """
        if not user_ids:
            return []
        if len(start_date) != 8 or len(end_date) != 8:
            raise ValueError("考勤查询日期必须为 yyyyMMdd 格式")

        # 飞书限制单次日期跨度 30 天，这里主动校验，避免拿到一个含义不明的错误码。
        span_days = (
            datetime.strptime(end_date, "%Y%m%d")
            - datetime.strptime(start_date, "%Y%m%d")
        ).days
        if span_days < 0:
            raise ValueError("考勤查询的结束日期不能早于起始日期")
        if span_days > self.ATTENDANCE_MAX_DATE_RANGE_DAYS - 1:
            raise ValueError(
                f"考勤查询日期跨度不得超过 {self.ATTENDANCE_MAX_DATE_RANGE_DAYS} 天，"
                f"当前为 {span_days + 1} 天"
            )

        chunk_size = self.ATTENDANCE_MAX_USERS_PER_REQUEST
        results: list[dict[str, Any]] = []
        for i in range(0, len(user_ids), chunk_size):
            chunk = user_ids[i : i + chunk_size]
            data = self._request(
                "POST",
                "/attendance/v1/user_tasks/query",
                params={
                    "employee_type": employee_type,
                    "ignore_invalid_users": (
                        "true" if ignore_invalid_users else "false"
                    ),
                },
                json={
                    "user_ids": chunk,
                    "check_date_from": int(start_date),
                    "check_date_to": int(end_date),
                    "need_overtime_result": need_overtime_result,
                },
            )
            results.extend(data.get("user_task_results") or [])
        logger.info(
            "飞书打卡结果拉取完成，用户数=%s，返回任务数=%s", len(user_ids), len(results)
        )
        return results

    def __enter__(self) -> "FeishuClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self._http_client.close()
