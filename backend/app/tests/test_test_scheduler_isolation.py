"""防止「本地 .env 泄漏进测试」导致全量测试慢 16 倍。

背景（2026-11-21 实测）
-----------------------
本地跑全量后端测试要 **36 分 35 秒**；把两个开关关掉后只要 **2 分 14 秒**
（2477 passed / 0 failed）。**同一个代码库，同一份测试，差 16 倍。**

根因链条
~~~~~~~~
1. ``backend/app/core/config.py`` 的 ``Settings`` 写着
   ``env_file=(BACKEND_ROOT / ".env", ".env")`` ——
   **测试进程会读到开发者本地的 ``backend/.env``**。
2. 本地 ``backend/.env`` 为了真机联调开着
   ``BAIDU_PAN_ENABLED=true`` / ``FEISHU_SCHEME_SYNC_ENABLED=true``。
3. 这两个开关让 ``app`` 的 startup 钩子真的起**后台线程**。
4. 每个 ``with TestClient(app)`` 退出时都会走 shutdown 钩子，
   对这两个线程各 ``join(timeout=5.0)`` 秒
   （``_STOP_JOIN_TIMEOUT_SECONDS``）。
5. 线程此刻正卡在**首轮真实取数**（百度 OAuth / 飞书 API），
   不会立刻响应停止事件 → **每个 TestClient 白等 10 秒**。
6. 全量有 31 个文件用 ``TestClient``，反复建 app → 10 秒 × 数十次。

⚠️ **「本地慢、CI 快」的真相**
   CI 上没有 ``backend/.env``（被 .gitignore 排除），走代码默认值
   （两个开关默认 False），所以 CI 一直是快的。
   ⇒ 本地那 36 分钟**不是「代码慢」，是「本地配置泄漏进了测试」**。

为什么不在 ``config.py`` 里改默认值
--------------------------------
- 这些开关的**代码默认值本来就该是 False**（防没配凭证时裸跑），
  改默认值等于放松生产安全边界；
- 真机联调需要的恰恰是「本地 .env 开着」——
  所以要关的是**测试进程**，不是配置本身。

本文件是**护栏**：防止有人后来把这段关闭逻辑删掉，或新增调度器却忘了加开关。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ROOT_CONFTEST = ROOT / "conftest.py"
BACKEND_ENV = ROOT / "backend" / ".env"

#: 测试进程里必须强制关闭的开关 → 为什么必须关
#:
#: ⚠️ **判据是「这个开关会不会让 startup 钩子起后台线程」**。
#: 会起线程的都要关 —— 因为线程会在 shutdown 时被 ``join(timeout=5)``，
#: 而线程正卡在真实网络取数时不会立刻退出。
REQUIRED_OFF = {
    "FEISHU_SYNC_ENABLED": "飞书组织架构定时同步",
    "FEISHU_SCHEME_SYNC_ENABLED": "飞书方案自动同步（今日新增，后台线程 + 首轮真实取数）",
    "BAIDU_PAN_ENABLED": "百度网盘绩效校验（今日新增，后台线程 + 首轮 OAuth 取数）",
    "FEISHU_EVENT_CONSUME_ENABLED": "飞书事件消费调度器",
    "PERIOD_SCHEDULER_ENABLED": "考核周期调度",
    "NOTIFICATION_ENABLED": "IM 通知投递",
}


def _root_conftest_text() -> str:
    return ROOT_CONFTEST.read_text(encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# 1. 根 conftest 必须强制关闭这些开关
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("env_key", sorted(REQUIRED_OFF))
def test_root_conftest_disables_scheduler_switch(env_key: str) -> None:
    """⚠️ 根 ``conftest.py`` 必须对每个后台调度器开关做**强制**关闭。

    漏一个的后果不是「测试失败」，而是「**本地全量慢 16 倍**」——
    这类失败极难被发现，因为测试**全绿**，只是慢得离谱。
    """
    text = _root_conftest_text()

    assert f'"{env_key}"' in text, (
        f"根 conftest.py 没有处理调度器开关 {env_key}（{REQUIRED_OFF[env_key]}）。\n"
        f"漏掉它，测试进程会读本地 backend/.env 真的起后台线程，"
        f"导致每个 TestClient 退出时白等 join 超时。\n"
        f"请在 _TEST_SCHEDULER_OFF 里补上 {env_key}。"
    )


def test_root_conftest_uses_forced_assignment_not_setdefault() -> None:
    """⚠️ 关闭**调度器开关**时必须用**直接赋值**，不得用 ``setdefault``。

    ``setdefault`` 不会覆盖已存在的环境变量 —— 而我们要的是**强制**关闭，
    否则开发者 shell 里残留的 ``BAIDU_PAN_ENABLED=true`` 依然会泄漏进来。

    ⚠️ **范围必须是「调度器开关」，不是「conftest 里的所有 setdefault」**
    --------------------------------------------------------------------
    本断言原先用一条正则扫**全文**的 ``os.environ.setdefault(...)``，
    于是会**误伤**用途完全不同的 setdefault。

    实例（2026-11-24 B1 登录入口改造引入）::

        os.environ.setdefault(
            "TEST_USER_PASSWORDS",
            '{"admin": "admin123", ...}',
        )

    这行是**测试账号口令注入** —— 它**就该**用 ``setdefault``：
    允许外部（CI secret / 开发者 shell）覆盖测试口令，
    而调度器开关恰恰**不允许**被覆盖。

    ⇒ 两者语义相反，用一条全文正则无法区分 ⇒ 必须**限定到开关字典**。
    """
    text = _root_conftest_text()

    # ⚠️ 只提取「关调度器开关的那个字典」的内容。
    #    `_TEST_SCHEDULER_OFF` 是本文件的既定约定（见 conftest.py）。
    match = re.search(
        r"_TEST_SCHEDULER_OFF\s*=\s*\{(?P<body>.*?)\}",
        text,
        re.DOTALL,
    )
    assert match, (
        "根 conftest.py 里找不到 _TEST_SCHEDULER_OFF 字典。\n"
        "本断言依赖它来限定检查范围 —— 请保持该字典名不变。"
    )
    body = match.group("body")

    offenders = re.findall(r"os\.environ\.setdefault\(\s*[\"'][A-Z_]+[\"']", body)
    assert not offenders, (
        "根 conftest.py 的 _TEST_SCHEDULER_OFF 用了 os.environ.setdefault：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\nsetdefault 不会覆盖已存在的值，无法真正强制关闭调度器开关。\n"
        "（⚠️ 字典**之外**的 setdefault 不受此限制 —— "
        "例如测试口令注入就应当允许被覆盖。）"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. 新增调度器时必须同步加开关
# ═════════════════════════════════════════════════════════════════════════════


def _background_scheduler_settings() -> set[str]:
    """扫描 ``config.py``，找出「会起后台线程」的开关名。

    判据：形如 ``xxx_enabled: bool = False`` 且名字里含
    ``sync`` / ``schedule`` / ``consume`` / ``notification`` / ``enabled``。
    这是一个**启发式** —— 宁可多列几个候选，也不要漏掉真正会起线程的。
    """
    config = ROOT / "backend" / "app" / "core" / "config.py"
    text = config.read_text(encoding="utf-8")

    found: set[str] = set()
    for match in re.finditer(
        r"^\s{4}([a-z][a-z0-9_]*enabled)\s*:\s*bool",
        text,
        re.MULTILINE,
    ):
        found.add(match.group(1).upper())
    return found


def test_every_background_scheduler_switch_is_disabled_in_tests() -> None:
    """⚠️ **新增后台调度器时，必须同步在根 conftest 里关掉它。**

    这是本文件最重要的一条：今天新增的 ``BAIDU_PAN_ENABLED`` 与
    ``FEISHU_SCHEME_SYNC_ENABLED`` 就是因为漏了这一步，
    让本地全量测试从 2 分 14 秒涨到 36 分 35 秒。
    """
    candidates = _background_scheduler_settings()
    text = _root_conftest_text()

    # 这些开关**不会**起后台线程（纯请求级开关），豁免。
    exempt = {
        # 严格鉴权模式：只影响请求链路，不起线程
        "SECURE_AUTH",
        # 自动建表：只影响启动期建表，不起线程
        "AUTO_CREATE_TABLES",
        # 机器人开关：请求级判断（bot/events 收到事件时再看），
        # **不**在 startup 里起线程 —— 见 main.py::_startup
        "FEISHU_BOT_ENABLED",
        # 计划助手开关：请求级判断，不起线程
        "PLAN_AI_ENABLED",
        # 企业知识库开关：请求级判断（写操作前置校验），不起线程。
        # ⚠️ 它默认 **True**（本地数据功能，默认关会让页面永远是空的），
        #    但绝不在 startup 里起线程 ⇒ 不需要进 _TEST_SCHEDULER_OFF。
        "KNOWLEDGE_ENABLED",
    }

    missing = sorted(
        name
        for name in candidates
        if name not in exempt and f'"{name}"' not in text
    )

    assert not missing, (
        "以下开关在 config.py 里存在，但根 conftest.py 没有在测试进程里关闭：\n"
        + "\n".join(f"  - {m}" for m in missing)
        + "\n若它会起后台线程，测试进程会真去调外部 API，"
        "导致每个 TestClient 退出时白等 join 超时（本地全量慢 16 倍）。\n"
        "请在根 conftest.py 的 _TEST_SCHEDULER_OFF 里补上，"
        "或确认它确实不起线程后加入豁免名单。"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3. 根 conftest 必须在配置加载**之前**生效
# ═════════════════════════════════════════════════════════════════════════════


def test_root_conftest_is_loaded_before_settings_are_read() -> None:
    """⚠️ 关闭逻辑必须写在**导入期**（模块顶层），不能放在 fixture 里。

    理由：``backend.app.core.config.settings`` 是一个**模块级单例** ——
    第一次被 import 时就把 ``backend/.env`` 读进去了。
    如果关闭逻辑在 fixture 里跑，设置已经读完了，等于没关。

    实现方式用 **AST** 判定，而不是字符串匹配 ——
    字符串匹配会被本文件自己的注释和 docstring 命中（它们正在解释这个坑）。
    """
    import ast

    tree = ast.parse(_root_conftest_text())

    offenders: list[str] = []
    for node in ast.walk(tree):
        # 只看「函数体之外」的赋值 —— 函数体里的赋值属于 fixture/函数作用域，
        # 太晚了。
        if not isinstance(node, (ast.Module, ast.If, ast.For, ast.While, ast.Try)):
            continue
        for stmt in ast.iter_child_nodes(node):
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                continue  # 跳过函数定义本身
            for inner in ast.walk(stmt):
                if not isinstance(inner, ast.Assign):
                    continue
                for target in inner.targets:
                    # 只看 os.environ[...] = ... 这种下标赋值
                    if not (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                        and isinstance(target.value.value, ast.Name)
                        and target.value.value.id == "os"
                    ):
                        continue
                    # 关键：在函数体内 → 太晚
                    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                        offenders.append(
                            f"第 {stmt.lineno} 行：在函数体内赋值 os.environ[...]"
                        )

    assert not offenders, (
        "环境变量赋值必须在 conftest.py 模块顶层（导入期生效）：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n放在函数/fixture 里会太晚 —— settings 是模块级单例，"
        "首次 import 时就已经把 backend/.env 读进去了。"
    )


def test_settings_reads_backend_env_file() -> None:
    """⚠️ 这条断言是**记录当前事实**，不是期望。

    ``Settings`` 会读 ``backend/.env`` —— 正是这个事实导致了上面的性能问题。
    如果哪天改成不读了（更好），本文件的任务就自然消失，可以整体删除。
    这条断言的作用是：**当行为变化时提醒同步更新本文件**。
    """
    config = ROOT / "backend" / "app" / "core" / "config.py"
    text = config.read_text(encoding="utf-8")

    assert "BACKEND_ROOT" in text and ".env" in text, (
        "config.py 的 env_file 配置发生了变化。\n"
        "如果不再读 backend/.env，本文件（调度器开关护栏）就失去意义，"
        "请重新评估是否删除它；"
        "如果仍在读，请确认根 conftest.py 的关闭逻辑依然有效。"
    )


def test_local_env_file_is_not_tracked_by_git() -> None:
    """⚠️ ``backend/.env`` 绝不能入库 —— 里面有真实密钥。

    若它被提交，密钥会永久留在 git 历史里（改文件不等于删历史）。
    """
    if not BACKEND_ENV.exists():
        pytest.skip("本地没有 backend/.env")

    gitignore = ROOT / ".gitignore"
    assert gitignore.exists(), "仓库根缺少 .gitignore"

    text = gitignore.read_text(encoding="utf-8")
    assert re.search(r"^\s*\.env\b", text, re.MULTILINE), (
        ".gitignore 必须忽略 .env —— 否则本地密钥可能被提交入库。"
    )
