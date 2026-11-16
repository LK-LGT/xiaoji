"""核查：当前 webhook 实现能否解析飞书**真实**的事件格式。

飞书 v2.0 信封（官方逐字）：
    {
      "schema": "2.0",
      "header": {
        "event_id": "...",
        "token": "...",
        "event_type": "approval.approval.updated_v4",
        "app_id": "...",
        "tenant_key": "..."
      },
      "event": {
        "object": {
          "approval_id": "...",       # ← 实例 ID
          "approval_code": "...",     # ← 审批定义 code
          ...
        }
      }
    }

当前实现读的是 body 顶层的 token / instance_code / instance_status ——
这些字段在真实 v2.0 信封里**根本不存在**。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.core.config import settings  # noqa: E402
from backend.app.main import app  # noqa: E402

# 飞书真实 v2.0 审批事件（结构取自官方文档逐字模板）
REAL_FEISHU_V2_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "b53e80a132ec090ba249a8bb74da0123",
        "token": "REAL_FEISHU_TOKEN",
        "event_type": "approval.approval.updated_v4",
        "app_id": "cli_xxx",
        "tenant_key": "d6ae59e175fxxx",
        "event_create_time": "1502199207.7171419",
    },
    "event": {
        "object": {
            "approval_id": "74333067777841234",
            "approval_code": "5B43240B-848C-49BD-9D27-11EF0EE11234",
            "version_id": "7459343285667611234",
            "widget_group_type": 0,
            "form_definition_id": "7459341670005371234",
            "timestamp": "1736763698",
            "extra": "",
        }
    },
}


def main() -> int:
    old_token = settings.feishu_approval_webhook_token
    settings.feishu_approval_webhook_token = "REAL_FEISHU_TOKEN"
    try:
        with TestClient(app) as c:
            r = c.post("/v1/feishu/approval/events", json=REAL_FEISHU_V2_EVENT)
            print("=== 用飞书真实 v2.0 事件格式投递 ===")
            print(f"  HTTP 状态码: {r.status_code}")
            print(f"  响应体: {r.text[:200]}")
            print()
            if r.status_code == 401:
                print("❌ 结论：当前实现【无法处理】飞书真实事件格式")
                print("   原因：token 在 header.token，但实现读的是 body['token']")
                print("   → 真实飞书事件会被判定为「Token 校验失败」直接 401 拒绝")
                return 1
            print("✅ 当前实现能处理")
            return 0
    finally:
        settings.feishu_approval_webhook_token = old_token


if __name__ == "__main__":
    sys.exit(main())
