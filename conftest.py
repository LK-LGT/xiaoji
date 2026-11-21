"""仓库根级 pytest 配置 —— 让 L3 测试在根目录运行时也能导入。

⚠️ **为什么需要这个文件**（一个真实的、长期的静默缺口）：

``agent-service/tests/`` 下的测试用 ``from tests._test_secret import ...``
导入共享的测试令牌。``tests`` 这个包名只有在
**``agent-service`` 目录被放进 ``sys.path``** 时才能解析。

而 CI 的 agent-service 作业恰好 ``working-directory: agent-service``，
所以那条路径是通的 —— **L3 在 CI 里一直有回归保护**。

问题出在**从仓库根目录跑 pytest** 时：
``pyproject.toml`` 的 ``testpaths`` 原本只含后端，
L3 被**静默跳过**（不报错、也不执行）——
开发者以为"跑了全量测试"，实际整个智能体服务没被测。

现在做两件事：
1. 本文件把 ``agent-service`` 追加到 ``sys.path``，
   让 ``tests._test_secret`` 在根目录下也能解析。
2. ``pyproject.toml`` 的 ``testpaths`` 补上 ``agent-service/tests``。

⚠️ **为什么是 append 而不是 insert(0)**：

``sys.path.insert(0, agent-service)`` 会让 ``tests`` 包名**优先**解析到
``agent-service/tests``，而 pytest 自己的 rootdir 插入逻辑会解析到
``backend/app/tests`` —— 两套解析并存时，
``test_scoring_breakdown_agent.py`` 会被**漏收集**（实测 156 → 149）。

改成 append 后，``agent-service`` 排在 ``sys.path`` **末尾**：
pytest 的 rootdir 插入仍然优先生效（保证收集完整），
而 ``tests._test_secret`` 在 rootdir 找不到时，
仍能回退到 ``agent-service/tests`` 解析出来（保证 import 成功）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT_SERVICE = ROOT / "agent-service"

if AGENT_SERVICE.is_dir() and str(AGENT_SERVICE) not in sys.path:
    # ⚠️ append 而非 insert(0) —— 理由见模块 docstring。
    sys.path.append(str(AGENT_SERVICE))


# ─────────────────────────────────────────────────────────────────────────────
# 后台调度器开关 —— 测试进程里必须**强制关闭**
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️⚠️ **为什么需要这一段（2026-11-21 实测，耗时差 16 倍）**
#
# 现象：本地跑全量后端测试要 **36 分 35 秒**；
#       同一个代码库，关掉两个开关后只要 **2 分 14 秒**（2477 passed / 0 failed）。
#
# 根因链条：
#   1. ``backend/app/core/config.py`` 的 ``Settings`` 用
#      ``env_file=(BACKEND_ROOT / ".env", ".env")`` ——
#      **测试进程会读到开发者本地的 ``backend/.env``**。
#   2. 本地 ``backend/.env`` 里为了真机联调开着
#      ``BAIDU_PAN_ENABLED=true`` / ``FEISHU_SCHEME_SYNC_ENABLED=true``。
#   3. 这两个开关会让 ``app`` 的 startup 钩子真的起**后台线程**。
#   4. 每个 ``with TestClient(app)`` 退出时都会触发 shutdown 钩子，
#      里面对这两个线程各 ``join(timeout=5.0)`` 秒
#      （``_STOP_JOIN_TIMEOUT_SECONDS``）。
#   5. 线程此刻正卡在**首轮真实取数**（百度 OAuth / 飞书 API），
#      根本不会立刻响应停止事件 → **每个 TestClient 白等 10 秒**。
#   6. 全量有 31 个文件用 ``TestClient``，反复建 app → 10 秒 × 数十次。
#
# ⚠️ **这就是「本地慢、CI 快」的真相** ——
#    CI 上没有 ``backend/.env``（被 .gitignore 排除），
#    走的是代码默认值（两个开关默认 False），所以 CI 一直是快的。
#    换句话说：**本地这 36 分钟不是「代码慢」，是「本地配置泄漏进了测试」**。
#
# 为什么在这里关，而不是改 ``config.py`` 的默认值：
#   - 这些开关的**代码默认值本来就该是 False**（防止没配凭证时裸跑），
#     改默认值等于放松生产安全边界；
#   - 真机联调需要的就是「本地 .env 开着」——
#     所以要关的是**测试进程**，不是配置本身。
#
# 为什么不用 monkeypatch：这里在**导入期**生效，早于任何 fixture；
# 且不依赖具体测试文件 —— 无论从根目录还是 backend/ 目录跑 pytest 都生效。
_TEST_SCHEDULER_OFF = {
    # 飞书组织架构定时同步
    "FEISHU_SYNC_ENABLED": "false",
    # 飞书方案自动同步（后台线程 + 首轮真实取数）
    "FEISHU_SCHEME_SYNC_ENABLED": "false",
    # 百度网盘绩效校验（后台线程 + 首轮 OAuth 取数）
    "BAIDU_PAN_ENABLED": "false",
    # 飞书事件消费调度器
    "FEISHU_EVENT_CONSUME_ENABLED": "false",
    # 考核周期调度
    "PERIOD_SCHEDULER_ENABLED": "false",
    # IM 通知投递
    "NOTIFICATION_ENABLED": "false",
}
for _key, _value in _TEST_SCHEDULER_OFF.items():
    # ⚠️ 用直接赋值，不用 setdefault ——
    #    setdefault 不会覆盖已存在的值，而我们要的是**强制**关闭。
    os.environ[_key] = _value
