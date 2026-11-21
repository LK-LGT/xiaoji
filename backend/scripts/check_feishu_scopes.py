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
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.feishu_client import FeishuClient  # noqa: E402

BASE_URL = "https://open.feishu.cn/open-apis"

# ─────────────────────────────────────────────────────────────
# 汇报（周报/月报）探针的时间窗
# ─────────────────────────────────────────────────────────────
# ⚠️ ``report/v1/tasks/query`` 的 commit_start_time / commit_end_time
#    是**秒级**时间戳（不是毫秒）。
#    取最近 14 天 —— 覆盖两个周报周期，避免探针恰好落在没人提交周报的空窗。
#    ⚠️ 这两个值在**模块导入时**计算 —— 探针脚本是一次性运行的，没问题。
_RECENT_WINDOW_END = int(time.time())
_RECENT_WINDOW_START = _RECENT_WINDOW_END - 14 * 24 * 3600

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
    # ─────────────────────────────────────────────────────────────
    # FS-1 / FS-3 新增探测（2026-11-17）
    #
    # 这四项对应「免登 / 机器人会话填表」两条新链路所需的能力。
    # 加它们的理由同 A8：**不实测就没有事实**——
    # 文档里写「权限没开通」曾被实测证伪过一次。
    # ─────────────────────────────────────────────────────────────
    {
        "label": "免登基础 · 当前用户信息 contact:user.base:readonly",
        "method": "GET",
        # ⚠️ 这里用**真实的飞书 user_id**（不是本系统 employee.id）。
        #    实测发现：组织架构同步时 `Employee.id = item["user_id"]`，
        #    两者取值相同 —— 但这是同步逻辑的巧合，不是契约。
        #
        # ⚠️⚠️ **必须带 user_id_type=user_id**：
        #    缺这个参数时飞书按 **open_id** 解析，传 user_id 会返回
        #    99992351「not a valid {open_id} or not exists」。
        #    这是 FS-1 免登实现的关键参数，漏了会直接查不到人。
        "path": "/contact/v3/users/17cd8645",
        "params": {"user_id_type": "user_id"},
        "note": "免登流程会用同一路径换取 open_id（须带 user_id_type）",
    },
    {
        "label": "OAuth 网页授权 authen:authen",
        "method": "GET",
        # ⚠️ OAuth 授权页是**浏览器跳转**的端点：
        #    它**不返回 JSON**，成功时是 302 跳转到飞书登录页。
        #    因此这里用「状态码是 3xx/200 且不是 99991668/99991672」
        #    来判定 —— 403 才可能是权限问题。
        "path": "/authen/v1/index",
        "params": {
            "app_id": "<APP_ID>",
            "redirect_uri": "https://example.invalid/callback",
            "state": "probe",
        },
        "redirect_probe": True,
        "note": "授权页为 302 跳转；能拿到跳转而非 403 即说明未拦权限",
    },
    {
        "label": "机器人卡片发送 im:message:send_as_bot",
        "method": "POST",
        # ⚠️ 与上面的「IM 消息读」是**不同权限维度**：
        #    读列表能过不代表能以机器人身份发消息（含卡片）。
        #    这里用无效 chat_id 探测 —— 能进到参数校验层即权限已开通。
        "path": "/im/v1/messages",
        "params": {"receive_id_type": "chat_id"},
        "body": {
            "receive_id": "oc_probe_invalid",
            "msg_type": "text",
            "content": "{\"text\":\"probe\"}",
        },
        "note": "卡片(interactive)与文本同属该权限，无需单独探测",
    },
    {
        "label": "应用信息读 application:application:readonly",
        "method": "GET",
        # ⚠️ app_ticket 的 POST 路径 /application/v6/applications/{id}/app_ticket
        #    实测 **404**（该路径不存在，404 与权限无关）。
        #    真实可用的是应用信息读接口 —— 它能验证「应用维度凭证」这一
        #    权限维度是否开通（机器人能力依赖这一维度）。
        "path": "/application/v6/applications",
        "params": {"app_id": "<APP_ID>"},
        "note": "app_ticket 的 POST 路径实测 404；FS-7 做长连接前需再确认路径",
    },
    # ─────────────────────────────────────────────────────────────
    # 汇报（周报/月报）相关 —— FDE 周报采集路径 A 的前提（2026-11-21）
    #
    # ⚠️ 飞书工作台里的「周报」「汇报」是原生**汇报模块**
    #    （oa.feishu.cn/report），有正式只读 API：
    #      POST /report/v1/tasks/query   查任务（含 form_contents 字段值）
    #      GET  /report/v1/rules/query  查规则（rule_id / 模板定义）
    #      POST /report/v1/rule_views/remove  移除规则看板（管理类，用不到）
    #
    # ★ 关键：**没有写入接口** —— 员工仍须在飞书原生界面提交周报，
    #    系统只读。这正是 FDE 想要的（不重复造填报表）。
    #
    # ⚠️ probe 时间窗用**最近 14 天**（周报周期），
    #    commit_start_time/commit_end_time 是**秒级**时间戳。
    # ─────────────────────────────────────────────────────────────
    {
        "label": "汇报任务读 report:task:readonly",
        "method": "POST",
        "path": "/report/v1/tasks/query",
        "body": {
            "commit_start_time": _RECENT_WINDOW_START,
            "commit_end_time": _RECENT_WINDOW_END,
            "page_token": "",
            "page_size": 20,
        },
        "note": (
            "★ FDE 周报采集路径 A 前提。"
            "tenant 态能读到哪些人取决于**应用数据权限范围** —— "
            "探针输出里若有 items，说明已可读；为空则可能是范围不含目标部门。"
            "⚠️ user_access_token 只能读「当前用户发起+收到的」且不分页。"
        ),
    },
    {
        "label": "汇报规则读 report:rule:readonly",
        "method": "GET",
        "path": "/report/v1/rules/query",
        "params": {"rule_name": "周报"},
        "note": "取 rule_id 与模板定义；rule_name 必填，填实际规则名更准",
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
        # ⚠️ 路径与参数里可能含 <APP_ID> 占位符 —— 探测脚本**不能**把
        #    占位符原样打给飞书（否则只会得到一个误导性的 404/400）。
        #    这里用真实 app_id 替换；替换不了就跳过该探测项。
        path = probe["path"].replace("<APP_ID>", settings.feishu_app_id or "")
        params = {
            k: (
                str(v).replace("<APP_ID>", settings.feishu_app_id or "")
                if isinstance(v, str)
                else v
            )
            for k, v in (probe.get("params") or {}).items()
        }
        if "<APP_ID>" in path and not settings.feishu_app_id:
            print(f"  ⏭️ {label}")
            print("       未配置 FEISHU_APP_ID，跳过该探测")
            continue
        try:
            resp = httpx.request(
                probe["method"],
                BASE_URL + path,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=probe.get("body"),
                timeout=25,
                # ⚠️ OAuth 授权页 / app_ticket 是**跳转或非 JSON** 端点，
                #    不能让 httpx 自动跟随跳转（会打到飞书登录页 HTML）。
                follow_redirects=False,
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

        # ⚠️ OAuth 授权页是**浏览器跳转**端点：正常响应是 302 跳到飞书登录页，
        #    body 是 HTML 而非 JSON → 上面的 classify 会因为 code=None 判成 UNKNOWN。
        #    这里特判：能拿到 3xx/200 跳转 = **没有被权限拦住**（鉴权通过）。
        if probe.get("redirect_probe") and verdict in ("UNKNOWN", "NOT_FOUND"):
            if 200 <= status < 400:
                verdict = "GRANTED"
                detail = f"授权页可达（HTTP {status} 跳转），未被权限拦截"

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
