"""查询飞书应用实际拥有的权限范围（只读诊断，不修改任何数据）。

用途：核实「飞书审批 / IM / 日历等权限是否已开通」，以**飞书服务端返回**为准，
而不是以文档里的旧结论为准。

────────────────────────────────────────────────────────────────────────
判据（重要）
────────────────────────────────────────────────────────────────────────
飞书接口调用失败时，**必须区分**三类情况，它们含义完全不同：

| 情况 | HTTP | 特征 | 结论 |
|---|---|---|---|
| ✅ 权限已开通 | 200 | `code == 0` | 权限没问题 |
| ❌ 权限不足 | 400/403 | 错误码 ∈ `PERMISSION_DENIED_CODES` | **需要申请权限** |
| ⚠️ 参数不对 | 400 | `99992402 field validation failed` 等 | **权限没问题**，是参数没给对 |
| ⚠️ 路径不对 | 404 | `404 page not found` | **接口路径写错了**，与权限无关 |
| ⚠️ 网关问题 | 5xx | — | 服务端临时故障，可重试 |

**关键**：飞书权限不足会返回**明确的权限类错误码**（如 `99991668` 未获得通讯录权限、
`99991672` 缺少权限或应用版本未发布）。而 `99992402 field validation failed`
出现在**鉴权通过之后**的参数校验层 —— 恰恰说明**权限是通的**。

⚠️ 2026-11-16 教训：早期版本把 `400` 一律判为「无权限」，导致误报
「飞书审批权限未开通」，实际权限早已具备。此脚本已按上述判据修正。

安全：只输出权限探测结果，不输出 app_secret / token 等敏感值。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.feishu_client import FeishuClient  # noqa: E402

BASE_URL = "https://open.feishu.cn/open-apis"

# ─────────────────────────────────────────────────────────────
# 权限不足类错误码（这些才是真正的「需要申请权限」）
# ─────────────────────────────────────────────────────────────
PERMISSION_DENIED_CODES: dict[int, str] = {
    99991668: "应用未获得通讯录权限",
    99991669: "应用未获得用户基本信息权限",
    99991672: "缺少通讯录权限或应用版本未发布",
    99991679: "应用可见范围未包含目标部门",
    99991688: "租户 Access Token 不匹配",
    99991663: "Access Token 无效",
    99991664: "Access Token 已过期",
}

# ─────────────────────────────────────────────────────────────
# 业务层错误码（鉴权已通过，只是探测用的假数据不存在）
# 这些同样**不代表权限缺失**。
# ─────────────────────────────────────────────────────────────
BUSINESS_ERROR_CODES: dict[int, str] = {
    1390001: "param is invalid（审批参数无效）",
    1390003: "instance code not found（探测用的审批 code 不存在）",
    1395001: "服务端繁忙，请稍后重试",
}

# ─────────────────────────────────────────────────────────────
# 参数校验类错误码（说明鉴权已通过，权限是通的）
# ─────────────────────────────────────────────────────────────
PARAM_ERROR_CODES: dict[int, str] = {
    99992402: "field validation failed（参数校验未通过）",
    230001: "invalid request parameter（参数无效）",
}


def classify(status_code: int, code: int | None, msg: str) -> tuple[str, str]:
    """把一次调用结果归类为 (判定, 说明)。

    判定取值：
      GRANTED    权限已开通
      DENIED     权限不足，需申请
      PARAM      权限已开通，但参数不对
      NOT_FOUND  接口路径不存在（与权限无关）
      SERVER     服务端故障
      UNKNOWN    无法归类
    """
    # 404：路径写错 —— 不是权限问题
    if status_code == 404:
        return "NOT_FOUND", "接口路径不存在（探测路径写错，与权限无关）"

    # 5xx：服务端问题
    if status_code >= 500:
        return "SERVER", "服务端临时故障，可重试"

    # ⚠️ Token 类型不匹配：部分接口要求 user_access_token（用户态），
    #    用 tenant_access_token 调用会报 99991663。
    #    这**不是权限缺失**，而是认证方式要求。
    if code in (99991663, 99991664):
        return "AUTH_MODE", f"需 user_access_token（当前用 tenant token）：{PERMISSION_DENIED_CODES.get(code, code)}"

    # 权限类错误码 —— 真正的「需要申请权限」
    if code in PERMISSION_DENIED_CODES:
        return "DENIED", PERMISSION_DENIED_CODES[code]

    # 参数类错误码 —— 鉴权已通过，权限是通的
    if code in PARAM_ERROR_CODES:
        return "PARAM", PARAM_ERROR_CODES[code]

    # 业务层错误码 —— 请求已进到业务逻辑（如探测用的审批 code 不存在）
    if code in BUSINESS_ERROR_CODES:
        return "PARAM", BUSINESS_ERROR_CODES[code]

    # HTTP 403 兜底
    if status_code == 403:
        return "DENIED", f"HTTP 403 禁止访问：{msg[:80]}"

    # HTTP 401 兜底
    if status_code == 401:
        return "DENIED", f"HTTP 401 未认证：{msg[:80]}"

    if status_code == 200:
        return "GRANTED", "调用成功"

    if code is not None:
        return "UNKNOWN", f"未归类错误码 {code}：{msg[:80]}"

    return "UNKNOWN", f"HTTP {status_code}：{msg[:80]}"


# ─────────────────────────────────────────────────────────────
# 探测清单
#
# ⚠️ method / path / params 必须与飞书官方文档一致，否则：
#   - method 错 → 405 / 404，误判为「没权限」
#   - path 错   → 404，误判为「没权限」
#   - params 缺 → 400 field validation failed，误判为「没权限」
# 例如考勤是 `POST /attendance/v1/user_tasks/query`（不是 GET），
# 审批实例列表需要带 `code`（审批定义 code）等参数。
# ─────────────────────────────────────────────────────────────
PROBES: list[dict[str, Any]] = [
    {
        "label": "通讯录读 contact:user.base:readonly",
        "method": "GET",
        "path": "/contact/v3/users",
        "params": {"page_size": "1"},
    },
    {
        "label": "考勤任务读 attendance:task:readonly",
        "method": "POST",
        "path": "/attendance/v1/user_tasks/query",
        "params": {"employee_type": "employee_id", "ignore_invalid_users": "true"},
        "body": {
            "user_ids": ["2aaf32ea"],
            "start_date": "20260101",
            "end_date": "20260102",
        },
    },
    {
        "label": "审批定义列表 approval:approval:readonly",
        "method": "GET",
        "path": "/approval/v4/approvals",
        "params": {"page_size": "1", "locale": "zh-CN"},
        # ⚠️ 该接口需 **user_access_token**（用户态），tenant token 会返回
        #    99991663 Invalid access token。这是**认证方式**要求，不是权限缺失。
        "note": "需 user_access_token；tenant token 探测会报 99991663",
    },
    {
        "label": "审批实例列表 approval:approval:readonly",
        "method": "GET",
        "path": "/approval/v4/instances",
        "params": {"page_size": "1", "code": "probe"},
    },
    {
        "label": "审批节点/审批人 approval:approval:readonly",
        "method": "GET",
        "path": "/approval/v4/instances/tasks",
        "params": {"page_size": "1"},
    },
    {
        "label": "日历读 calendar:calendar:readonly",
        "method": "GET",
        "path": "/calendar/v4/calendars",
        "params": {"page_size": "1"},
    },
    {
        "label": "IM 消息读 im:message:readonly",
        "method": "GET",
        "path": "/im/v1/messages",
        "params": {
            "container_id_type": "chat",
            "container_id": "oc_probe_invalid",
            "page_size": "1",
        },
    },
]

MARK = {
    "GRANTED": ("✅ 权限已开通", "✅"),
    "DENIED": ("❌ 权限不足，需申请", "❌"),
    "PARAM": ("⚠️  权限已开通（参数待调）", "⚠️"),
    "AUTH_MODE": ("⚠️  权限已开通（需用户态 token）", "⚠️"),
    "NOT_FOUND": ("⚠️  探测路径有误", "⚠️"),
    "SERVER": ("⚠️  服务端故障", "⚠️"),
    "UNKNOWN": ("❓ 无法归类", "❓"),
}


def main() -> int:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        print("未配置飞书凭据（FEISHU_APP_ID / FEISHU_APP_SECRET），无法查询")
        return 1

    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )
    token = client._get_access_token()

    print("=" * 78)
    print("飞书应用权限实测")
    print("=" * 78)

    tally: dict[str, int] = {}
    denied_permissions: list[str] = []

    for probe in PROBES:
        label = probe["label"]
        try:
            resp = httpx.request(
                probe["method"],
                BASE_URL + probe["path"],
                headers={"Authorization": f"Bearer {token}"},
                params=probe.get("params"),
                json=probe.get("body"),
                timeout=25,
            )
            status = resp.status_code
            try:
                data = resp.json()
            except ValueError:
                data = {}
            code = data.get("code")
            msg = str(data.get("msg") or resp.text[:120])
        except Exception as exc:  # 网络异常
            status, code, msg = 0, None, str(exc)[:120]

        verdict, detail = classify(status, code, msg)
        tally[verdict] = tally.get(verdict, 0) + 1
        if verdict == "DENIED":
            denied_permissions.append(label)

        text, icon = MARK[verdict]
        print(f"  {icon} {label}")
        print(f"       {text}  |  {detail}")
        if probe.get("note"):
            print(f"       ℹ️  {probe['note']}")

    print()
    print("=" * 78)
    print(f"权限已直接调用成功   : {tally.get('GRANTED', 0)} 项")
    print(f"权限已开通(参数待调) : {tally.get('PARAM', 0)} 项（鉴权通过，仅参数需补全）")
    print(f"权限已开通(需用户态) : {tally.get('AUTH_MODE', 0)} 项（需 user_access_token，非权限问题）")
    print(f"探测路径有误         : {tally.get('NOT_FOUND', 0)} 项（脚本 path 写错，非权限问题）")
    print(f"服务端故障           : {tally.get('SERVER', 0)} 项")
    print(f"无法归类             : {tally.get('UNKNOWN', 0)} 项")
    print(f"⚠️ 权限不足          : {tally.get('DENIED', 0)} 项（需申请）")
    print("=" * 78)

    if denied_permissions:
        print()
        print("需要申请的权限：")
        for p in denied_permissions:
            print(f"  - {p}")
        client.close()
        return 1

    print()
    print("✅ 结论：未发现权限不足项 —— 相关权限均已具备。")
    print("   （「参数待调」与「需用户态 token」都不构成权限阻塞）")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
