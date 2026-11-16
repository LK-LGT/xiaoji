"""A8 端点鉴权边界现场核查。

用途：人工确认受保护端点在**未认证**时的返回码，
以及 webhook 在**未配置 token** 时确实不放行。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 脚本可能从仓库根目录或 backend/ 目录执行，两种都要能找到 backend 包
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

PROTECTED = [
    "/v1/feishu/approval/bindings",
    "/v1/feishu/approval/status",
    "/v1/feishu/approval/reflow-records",
]


def main() -> int:
    failures: list[str] = []
    with TestClient(app) as c:
        print("=== 受保护端点（未认证，期望 401）===")
        for url in PROTECTED:
            r = c.get(url)
            ok = r.status_code == 401
            print(f"  {url:45s} -> {r.status_code}  {'OK' if ok else 'FAIL'}")
            if not ok:
                failures.append(f"{url} 返回 {r.status_code}，期望 401")

        print()
        print("=== webhook 未配置 token（期望 503，不放行）===")
        r = c.post("/v1/feishu/approval/events", json={"token": "x"})
        ok = r.status_code == 503
        print(f"  {'POST /v1/feishu/approval/events':45s} -> {r.status_code}  {'OK' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"webhook 返回 {r.status_code}，期望 503")

        print()
        print("=== 基础连通性 ===")
        r = c.get("/openapi.json")
        print(f"  {'GET /openapi.json':45s} -> {r.status_code}")
        if r.status_code != 200:
            failures.append("openapi 不可达")
        r = c.get("/v1/health")
        print(f"  {'GET /v1/health':45s} -> {r.status_code}")
        if r.status_code != 200:
            failures.append("health 不可达")

    print()
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
