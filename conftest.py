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

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT_SERVICE = ROOT / "agent-service"

if AGENT_SERVICE.is_dir() and str(AGENT_SERVICE) not in sys.path:
    # ⚠️ append 而非 insert(0) —— 理由见模块 docstring。
    sys.path.append(str(AGENT_SERVICE))
