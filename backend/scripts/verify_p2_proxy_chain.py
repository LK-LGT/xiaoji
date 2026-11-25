"""P2 前端→后端**代理链路**验证。

为什么单独一���：
前端 ``src/api/http.ts`` 的 baseURL 是 ``/api``，由 Vite 代理重写成 ``/v1``。
如果这条链路断了（代理没起、rewrite 规则不对、后端端口不对），
前端会拿到 404 / 网络错误，而**后端 pytest 全绿也发现不了**。

这个脚本打的是**前端自己的源**（5180），走完整代理链路，
所以它验的是「前端真正会走的那个 URL」。

用法：
    python _p2_proxy_check.py [FRONTEND_ORIGIN]
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
#    ⇒ 从环境变量读；没配就用一个**必然失败**的占位值，
#      让脚本在登录那步明确报错，而不是偷偷用弱口令。
LOGIN_USER = os.getenv("VERIFY_ADMIN_USER", "admin")
LOGIN_PASSWORD = os.getenv("VERIFY_ADMIN_PASSWORD", "")

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def call(method: str, path: str, *, token: str | None = None, body: Any = None) -> tuple[int, Any]:
    """打**前端源**上的 /api/xxx（模拟浏览器实际请求）。"""
    url = f"{ORIGIN}/api{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
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


def main() -> int:
    print("=" * 78)
    print(f"P2 代理链路验证 —— {ORIGIN}/api → /v1")
    print("=" * 78)

    print("\n[1] 前端页面可达")
    try:
        with urllib.request.urlopen(f"{ORIGIN}/", timeout=15) as resp:
            html = resp.read().decode("utf-8")
        check("前端首页 200", "<div id=\"app\">" in html, html[:200])
    except Exception as exc:
        check("前端首页可达", False, str(exc))
        return 1

    print("\n[2] 登录（走代理）")
    status, body = call("POST", "/auth/login", body={
        "username": LOGIN_USER, "password": LOGIN_PASSWORD,
    })
    check("登录 200", status == 200, f"status={status} body={str(body)[:200]}")
    data = body.get("data") if isinstance(body, dict) else None
    token = data.get("accessToken") if isinstance(data, dict) else None
    check("拿到 accessToken", bool(token), str(body)[:200])
    if not token:
        return 1

    print("\n[3] P2 端点经代理可达（路径映射正确）")
    endpoints = [
        ("GET", "/data-sources"),
        ("GET", "/semantic-qc/records/R6E2E_REC_gda7d3ef"),
        ("GET", "/trend-report/trend/gda7d3ef"),
        ("GET", "/trend-report/position?position=%E9%94%80%E5%94%AE"),
        ("GET", "/trend-report/risk-roster"),
        ("GET", "/trend-report/payroll-export"),
        ("GET", "/feedback"),
        ("GET", "/feedback/prompts"),
        ("GET", "/feedback/prompts/compare"),
        ("GET", "/feedback/cases"),
    ]
    for method, path in endpoints:
        status, body = call(method, path, token=token)
        envelope = isinstance(body, dict) and body.get("code") == 0 and "data" in body
        check(
            f"{method} {path.split('?')[0]} 经代理 200 且信封正确",
            status == 200 and envelope,
            f"status={status} body={str(body)[:160]}",
        )

    print("\n[4] 未登录应被拦截（代理不绕过鉴权）")
    status, body = call("GET", "/data-sources")
    check("无 token 请求被拒", status == 401, f"status={status} body={str(body)[:160]}")

    print("\n" + "=" * 78)
    print(f"通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("\n失败明细：")
        for name, detail in FAILED:
            print(f"  ✗ {name}\n      {detail}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
