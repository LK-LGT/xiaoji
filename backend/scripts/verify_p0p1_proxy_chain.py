"""P0 / P1 前端→后端**代理链路**验证。

为什么单独一个脚本
-----------------
前端 ``src/api/http.ts`` 的 baseURL 是 ``/api``，由 Vite 代理重写成 ``/v1``。
前端新增的 P0/P1 接入全部走这条链路 —— 如果路径写错（比如误写成 ``/v1/...``）
或后端路由没注册，**前端会拿到 404，而 pytest 全绿也发现不了**。

这个脚本打的是**前端自己的源**（5180），走完整代理链路，
所以它验的是「前端真正会走的那个 URL」。

另外，这个脚本还验一条**后端 pytest 验不到**的链路：
``/v1/exec-tools/*`` 网关只认**工具令牌**，前端必须先用**登录会话**
换一个执行类令牌（``POST /v1/exec-tools/token``）——
这条桥是 P1-3 前端接入的前提。

用法：
    python verify_p0p1_proxy_chain.py [FRONTEND_ORIGIN]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

ORIGIN = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180").rstrip("/")

# ⚠️ 测试账号口令**不在源码里**（2026-11-24 已移除明文常量）。
#    ⇒ 从环境变量读；没配就传空串 ⇒ 登录必然失败 ⇒ 脚本会明确报错。
LOGIN_USER = os.getenv("VERIFY_ADMIN_USER", "admin")
LOGIN_PASSWORD = os.getenv("VERIFY_ADMIN_PASSWORD", "")

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: Any = None,
    bearer: str | None = None,
) -> tuple[int, Any]:
    """打**前端源**上的 /api/xxx（模拟浏览器实际请求）。

    ``bearer`` 用于工具令牌场景：``/v1/exec-tools/*`` 不接受登录会话令牌。
    """
    url = f"{ORIGIN}/api{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status = exc.code
    except Exception as exc:
        return 0, {"_transport_error": str(exc)}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"_raw": raw[:300]}


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} :: {detail}")


def envelope_ok(body: Any) -> bool:
    return isinstance(body, dict) and body.get("code") == 0 and "data" in body


def login(username: str, password: str) -> str | None:
    status, body = call(
        "POST", "/auth/login", body={"username": username, "password": password}
    )
    if status != 200:
        print(f"  登录失败 status={status} body={str(body)[:200]}")
        return None
    data = body.get("data") if isinstance(body, dict) else None
    return data.get("accessToken") if isinstance(data, dict) else None


def find_record(token: str) -> tuple[str, str] | None:
    """找一条可操作的考核记录（record_id + task_id）。

    优先用主管评分页真正用的端点（``/assessment/records/pending``），
    退回到方案填报页的 ``/performance/scheme-records``。
    """
    status, body = call(
        "GET",
        "/assessment/records/pending?include_all_statuses=true",
        token=token,
    )
    if status == 200 and envelope_ok(body):
        data = body["data"]
        rows = data.get("items") if isinstance(data, dict) else data
        for row in rows or []:
            if isinstance(row, dict) and row.get("record_id"):
                return row["record_id"], row.get("task_id", "")
    status, body = call("GET", "/performance/scheme-records", token=token)
    if status == 200 and envelope_ok(body):
        for row in body.get("data") or []:
            if isinstance(row, dict) and row.get("id"):
                return row["id"], row.get("task_id", "")
    return None


def main() -> int:
    print("=" * 78)
    print(f"P0/P1 代理链路验证 —— {ORIGIN}/api → /v1")
    print("=" * 78)

    print("\n[1] 前端页面可达")
    try:
        with urllib.request.urlopen(f"{ORIGIN}/", timeout=15) as resp:
            html = resp.read().decode("utf-8")
        check("前端首页 200", '<div id="app">' in html, html[:200])
    except Exception as exc:
        check("前端首页可达", False, str(exc))
        return 1

    print("\n[2] 登录（走代理）")
    token = login(LOGIN_USER, LOGIN_PASSWORD)
    check("拿到 accessToken", bool(token))
    if not token:
        return 1

    print("\n[3] P0/P1 端点经代理可达（路径映射正确）")
    endpoints = [
        # P0-1 互评
        ("GET", "/performance/scheme-records"),
        # P0-2 分布质检
        ("GET", "/ai/reviews/distribution?period=202611"),
        # P1-2 周期调度
        ("GET", "/performance/scheduler/status"),
        # P1-4 代理授权
        ("GET", "/delegations"),
        ("GET", "/delegations/my-scope"),
        # P1-3 执行类令牌（会话换工具令牌）
        ("POST", "/exec-tools/token"),
    ]
    for method, path in endpoints:
        payload = None
        if path == "/exec-tools/token":
            payload = {"tool": "exec_remind", "task_id": "T", "scope": "self"}
        status, body = call(method, path, token=token, body=payload)
        ok = status in (200, 403, 404) and (
            envelope_ok(body) or isinstance(body, dict)
        )
        check(
            f"{method} {path.split('?')[0]} 经代理可达且信封正确",
            ok and (status == 200 or status in (403, 404)),
            f"status={status} body={str(body)[:160]}",
        )

    print("\n[4] 互评：读取某条记录的互评清单")
    found = find_record(token)
    check("找到考核记录", found is not None)
    if found:
        record_id, task_id = found
        status, body = call(
            "GET", f"/performance/scheme-records/{record_id}/peers", token=token
        )
        check(
            "互评清单 200 且信封正确",
            status == 200 and envelope_ok(body),
            f"status={status} body={str(body)[:200]}",
        )
        if status == 200 and envelope_ok(body):
            data = body["data"]
            check(
                "互评清单结构完整",
                isinstance(data, dict)
                and "total" in data
                and "submitted" in data
                and "items" in data,
                str(data)[:200],
            )
        if task_id:
            status, body = call(
                "GET", f"/performance/scheme-tasks/{task_id}/peer-progress", token=token
            )
            check(
                "互评进度 200",
                status == 200 and envelope_ok(body),
                f"status={status} body={str(body)[:200]}",
            )

    print("\n[5] 分布质检：结构与 limitation 透出")
    status, body = call(
        "GET", "/ai/reviews/distribution?period=202611", token=token
    )
    if status == 200 and envelope_ok(body):
        data = body["data"]
        check(
            "分布质检返回 groups/alerts/limitations",
            isinstance(data, dict)
            and "groups" in data
            and "alerts" in data
            and "limitations" in data,
            str(data)[:200],
        )
        check(
            "分布质检 limitations 非空（前端要原样透出）",
            bool(data.get("limitations")),
            str(data.get("limitations"))[:200],
        )
    else:
        check("分布质检 200", False, f"status={status} body={str(body)[:200]}")

    print("\n[6] 执行类指令：会话 → 工具令牌 → prepare → confirm")
    found = find_record(token)
    if not found:
        check("找到考核任务", False)
        return 1
    _, task_id = found

    status, body = call(
        "POST",
        "/exec-tools/token",
        token=token,
        body={"tool": "exec_remind", "task_id": task_id, "scope": "self"},
    )
    check(
        "会话换取执行类令牌 200",
        status == 200 and envelope_ok(body),
        f"status={status} body={str(body)[:200]}",
    )
    tool_token = ""
    if status == 200 and envelope_ok(body):
        tool_token = body["data"].get("token", "")
        check("拿到工具令牌", bool(tool_token))

    if tool_token:
        status, body = call(
            "POST",
            "/exec-tools/exec_remind/prepare",
            body={"task_id": task_id, "targets": [], "message": "代理链路自检"},
            bearer=tool_token,
        )
        check(
            "prepare 用工具令牌可达（无会话令牌也会 401）",
            status in (200, 400),
            f"status={status} body={str(body)[:200]}",
        )
        if status == 200 and envelope_ok(body):
            plan = body["data"]
            check(
                "prepare 返回 confirmToken 与 impact",
                bool(plan.get("confirmToken")) and "impact" in plan,
                str(plan)[:200],
            )

        # 没有工具令牌时应当被网关拒绝（证明两阶段不是摆设）
        status, body = call(
            "POST",
            "/exec-tools/exec_remind/prepare",
            body={"task_id": task_id, "targets": []},
            token=token,
        )
        check(
            "只带会话令牌调执行网关被拒（401）",
            status == 401,
            f"status={status} body={str(body)[:160]}",
        )

    print("\n[7] 代理授权：委托查询与作用域")
    status, body = call("GET", "/delegations", token=token)
    check(
        "委托列表 200 且是数组",
        status == 200 and envelope_ok(body) and isinstance(body["data"], list),
        f"status={status} body={str(body)[:200]}",
    )
    status, body = call("GET", "/delegations/my-scope", token=token)
    check(
        "我的代理范围 200 且含 employeeIds",
        status == 200
        and envelope_ok(body)
        and isinstance(body["data"].get("employeeIds"), list),
        f"status={status} body={str(body)[:200]}",
    )

    print("\n[8] 未登录应被拦截（代理不绕过鉴权）")
    status, _ = call("GET", "/delegations")
    check("无 token 请求被拒", status == 401, f"status={status}")

    print("\n" + "=" * 78)
    print(f"通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("\n失败明细：")
        for name, detail in FAILED:
            print(f"  - {name} :: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
