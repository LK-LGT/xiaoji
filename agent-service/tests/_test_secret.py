"""测试环境的服务令牌（R12）。

⚠️ **为什么需要这个文件**：
``agent-service/tests/`` 下多个测试文件都要导入 ``app.main``。
若每个文件各自 ``os.environ.setdefault("AGENT_SHARED_SECRET", ...)``，
**先被导入的文件会定下 secret**，后导入的文件拿到的是**不同**的值
→ 令牌校验失败 → 401 → 测试失败，且报错信息完全指不到根因。

这里把 secret 收敛到**唯一一处**，谁先导入都一样。
"""

from __future__ import annotations

import os

TEST_SECRET = "agent-service-test-secret"

# ⚠️ setdefault 而非直接赋值 —— 外部显式设置的 secret 优先
#    （CI 里可能通过环境变量注入）。
os.environ.setdefault("AGENT_SHARED_SECRET", TEST_SECRET)

AUTH_HEADERS = {"Authorization": f"Bearer {TEST_SECRET}"}
