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

import pytest

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
    # 网盘剪辑工作量统计（后台线程 + 首轮真实取数）
    "PAN_WORKLOAD_ENABLED": "false",
    # 飞书事件消费调度器
    "FEISHU_EVENT_CONSUME_ENABLED": "false",
    # 考核周期调度
    "PERIOD_SCHEDULER_ENABLED": "false",
    # IM 通知投递
    "NOTIFICATION_ENABLED": "false",
    # ★ 每日填报提醒推送（后台线程 + 首轮真实取数）
    "FEISHU_DAILY_PUSH_ENABLED": "false",
    # ★ 飞书审批回流定时补偿（2026-11-24，FS-7）
    #    ⚠️ 后台线程 + 首轮就真去拉飞书审批实例 ⇒ 必须关。
    "FEISHU_APPROVAL_COMPENSATION_ENABLED": "false",
}
for _key, _value in _TEST_SCHEDULER_OFF.items():
    # ⚠️ 用直接赋值，不用 setdefault ——
    #    setdefault 不会覆盖已存在的值，而我们要的是**强制**关闭。
    os.environ[_key] = _value


# ─────────────────────────────────────────────────────────────────────────────
# ★ 测试数据库隔离（2026-11-25，CI 22 连红修复的最后一环）
#
# ⚠️⚠️ **为什么必须做（一个真实的 CI 失败）**
#
#   现象：CI 的「后端单元与集成测试」这一步挂了，报
#         ``sqlite3.OperationalError: no such table: employee``。
#   根因链条：
#     1. CI 里**没有** ``backend/.env`` ⇒ ``Settings.database_url``
#        走代码默认值（或 CI 注入的 ``DATABASE_URL``）。
#     2. CI 把 ``AUTO_CREATE_TABLES=false`` ⇒ 运行时**不建表**。
#     3. CI 的步骤顺序是「**先跑测试、后跑 Alembic 迁移**」
#        ⇒ 测试跑的时候，真实库文件**一张表都没有**。
#     4. 大部分测试用 ``dependency_overrides[get_db]`` 换成内存库 ⇒ 不受影响。
#        但**少数**测试（如 ``test_login_entry_gate.py`` 的
#        「开启后测试账号仍能登录」）**直接打真实端点、没覆盖 get_db**
#        ⇒ 撞上真实空库 ⇒ ``no such table`` ⇒ 整步挂掉。
#
# ⚠️ **为什么「本地不复现」**
#   本地有 ``backend/.env``（``AUTO_CREATE_TABLES=true`` 或库已建好）
#   ⇒ 真实库里有表 ⇒ 碰巧能过。
#   ⇒ 又是「本地全绿 / CI 全红」，且**错在测试而不是在业务代码**。
#
# ⚠️ **为什么不能靠「把 Alembic 迁移挪到测试前面」来修**
#   那样确实能建表，但会引入另一个问题：
#   测试会往**真实库**里写数据（登录会落 token_version、审计日志……）
#   ⇒ 测试之间互相污染 ⇒ 「跑一次绿、跑两次红」的顺序依赖。
#   ⇒ 单元测试的**正确做法**是每个用例自带隔离库。
#
# ✅ **本文件的做法**：把 ``DATABASE_URL`` 强制指向
#    **测试专用**的 SQLite 文件（不是内存库 —— 内存库每个连接独立，
#    而这里需要「多个连接看到同一份数据」，因为 app 自己的
#    ``get_sessionmaker()`` 与测试的连接不是同一个）。
#    ⇒ 这样「没覆盖 get_db」的测试也不会碰到真实库。
#
# ⚠️ **为什么用「直接赋值」而不是 setdefault**
#   同上 —— 必须**强制**覆盖，否则 CI 注入的 DATABASE_URL 会赢。
_TEST_DB_PATH = ROOT / "data" / "_pytest_test.db"
# ⚠️ **必须用 ``as_posix()``**（2026-11-25）
#   SQLite URL 里的路径用正斜杠。
#   Windows 上 ``str(Path)`` 得到 ``D:\a\b\data\_pytest_test.db`` ——
#   反斜杠在 SQLite URL 里是**转义字符**（``\d`` 不是合法路径），
#   在 Linux 上则是正斜杠 ⇒ 同一份代码两边行为不一致。
#   ⇒ 统一用 ``as_posix()``，两边都得到正斜杠。
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
# ⚠️ **测试库要能自己建表** —— 否则又回到「no such table」。
#    （这只影响**测试进程**，与生产的 Alembic 路线不冲突。）
os.environ["AUTO_CREATE_TABLES"] = "true"

# ⚠️⚠️ **删旧库文件必须放在「导入期」**（2026-11-25 实测踩坑）
#   文件被删不掉的原因是「**还有进程持有它的句柄**」
#   —— SQLAlchemy 的 engine 是**全局单例**，一旦有连接开着，
#   Windows 上就 ``PermissionError: [WinError 32]``。
#
#   ⇒ 在**模块导入期**（还没人建 engine）删文件 ⇒ 此时没有任何句柄
#   ⇒ 删得掉。之后 engine 再建也是从零开始。
#
#   ⚠️ **为什么不能放到 fixture 里**
#   fixture 跑的时候，pytest 可能已经 import 了别的模块
#   ⇒ engine 可能已经被建好、连接可能已经打开 ⇒ 删不掉
#   ⇒ 实测：整批用例在 setup 阶段就 ERROR
#   （``ERROR at setup of test_xxx``）。
#
#   ⚠️ **为什么这里用「尽量删，删不掉就算了」**
#   删不掉（极端情况：上一次进程还没退干净）时，
#   后面的 fixture 还会 drop schema ⇒ 起点依然干净 ⇒ 不阻断测试。
for _suffix in ("", "-wal", "-shm"):
    _candidate = Path(f"{_TEST_DB_PATH}{_suffix}")
    try:
        if _candidate.exists():
            _candidate.unlink()
    except OSError:
        # ⚠️ 删不掉不算致命 —— 后面的 fixture 会 drop schema 兜底。
        pass
_TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# ★ 登录入口开关（2026-11-24，B1）
#
# ⚠️ **为什么测试进程要打开口令登录**
# 自动化测试需要一条**绕过飞书 OAuth**的登录通路
# （CI 里没有飞书凭证，也不可能每次真去调飞书换 token）。
# ⇒ 测试进程里显式打开 `PASSWORD_LOGIN_ENABLED`。
#
# ⚠️ **为什么口令要在这里注入**
# 源码里**不再有明文口令**（2026-11-24 已移除 `admin123` 等字面量）
# ⇒ 口令只能来自环境变量 ``TEST_USER_PASSWORDS``。
# ⇒ 这个文件是**唯一的注入点**，且**只在测试进程里**生效。
#
# ⚠️ **为什么不写进 ``backend/.env``**
# ``.env`` 是**开发者本地真机联调**用的，且会被 ``.gitignore`` 排除
# ⇒ 放进去等于「本地能登录、生产登录不了」的隐形差异，
#   而且真机联调时万一提交就泄露了。
# ⇒ 放 conftest ⇒ 与代码同版本管理，**生产不会带出去**。
#
# ⚠️ **这是测试专用口令，不是任何真实账号的口令**
# （测试账号映射的是真实员工，但口令只在本测试进程里有效）。
_TEST_LOGIN_ON = {
    "PASSWORD_LOGIN_ENABLED": "true",
    "TEST_USERS_ENABLED": "true",
}
for _key, _value in _TEST_LOGIN_ON.items():
    os.environ[_key] = _value

# ⚠️ 测试账号口令 —— 注入到环境变量，供 ``routes/auth.py`` 读取。
# ⚠️ **必须与 ``routes/auth.py`` 的 ``_TEST_USER_META`` 的 key 一致** ——
#    对不上的账号会被跳过（源码里会打 warning）。
#
# ⚠️ **2026-11-24（B2）：``manager`` / ``leader`` 改名为真实身份**
#    ⇒ 原因见 ``auth.py::_TEST_USER_META`` 的注释（那两个名字撞 RBAC 角色名，
#      而且两个不同的人权限完全一样 ⇒ 测试根本区分不出来）。
os.environ.setdefault(
    "TEST_USER_PASSWORDS",
    '{"admin": "admin123", "yangfei": "yangfei123", '
    '"biyumi": "biyumi123", "employee": "employee123"}',
)


# ─────────────────────────────────────────────────────────────────────────────
# ★ 工具网关密钥（2026-11-25，CI 22 连红修复的第三环）
#
# ⚠️ **为什么必须做（又是一个「本地绿 / CI 红」）**
#   现象：CI 的后端测试挂了 23 个用例，全是工具令牌相关
#         （``test_qa_peer_score_tool.py`` 等），报
#         ``AgentToolUnavailable: 工具网关未配置``。
#   根因：``agent_tool_token._sign()`` 在 ``settings.agent_tool_secret``
#         为空时**直接抛 503** ⇒ 工具令牌签发/校验全都不可用。
#         而这些测试**自己不去设置**这个值 ⇒ 依赖外部配置。
#         本地有 ``backend/.env``（配了 ``AGENT_TOOL_SECRET``）⇒ 能过；
#         CI 没有 ``.env`` ⇒ 空 ⇒ 必挂。
#
# ⚠️ **为什么这里可以写死**
#   ``AGENT_TOOL_SECRET`` 只用于**测试进程内**的令牌签名自洽性检查
#   （签一个、验一个），不对应任何真实凭据、不出网、不进生产。
#   ⇒ 生产由部署环境的 Secret 管理系统注入。
#
# ⚠️ **为什么必须与 AUTH_TOKEN_SECRET 不同**
#   ``Settings._validate_agent_tool_secret`` 明确要求
#   「工具密钥不得与登录密钥、共享密钥相同」⇒ 两者必须是不同的字符串。
_TEST_CREDENTIALS = {
    # 工具网关签名密钥（≥32 位，不得与下面两个相同）
    "AGENT_TOOL_SECRET": "ci-test-only-agent-tool-secret-not-real-0123456789",
}
for _key, _value in _TEST_CREDENTIALS.items():
    # ⚠️ 强制覆盖 —— 本地 ``backend/.env`` 里可能有真实值，
    #    测试进程**必须**用测试值（不碰真实凭据）。
    os.environ[_key] = _value

# ⚠️⚠️ **不要在这里设 ``AGENT_SHARED_SECRET``**（2026-11-25 实测踩坑）
#   那个密钥**属于 agent-service**，由
#   ``agent-service/tests/_test_secret.py`` 统一提供（用 ``setdefault``）。
#   若根 conftest 强制覆盖它 ⇒ agent-service 的测试里
#   ``AUTH_HEADERS``（用 ``TEST_SECRET`` 算的）与实际配置不一致
#   ⇒ 401 ⇒ 12 个 agent-service 用例全挂。
#   ⇒ 这正是「用 setdefault 的那个才是权威」——
#     谁拥有这个配置，谁说了算。


# ─────────────────────────────────────────────────────────────────────────────
# ★ 测试库生命周期（隔离的另一半）
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ **为什么清空旧数据**
#   上一次跑测试留下的数据会被这一轮读到 ⇒
#   「跑一次绿、跑两次红」的**顺序依赖**，
#   而这种 flaky 在 CI 上表现为「有时红有时绿」，极难查。
#   ⇒ 每轮测试都从**干净起点**开始。
#
# ⚠️⚠️ **为什么「删文件」改成「drop schema」（2026-11-25 实测踩坑）**
#   删文件在 Windows 上直接炸：
#     ``PermissionError: [WinError 32] 另一个程序正在使用此文件``
#   —— 因为上一轮测试的 SQLAlchemy engine 还**持有该文件的句柄**
#   （连接池没关）⇒ 删不掉 ⇒ **整批用例在 setup 阶段就 ERROR**。
#
#   ⇒ 改成「用同一个 engine 把所有表 drop 掉」：
#     - 不碰文件本身 ⇒ 没有文件锁问题（Windows / Linux 都安全）；
#     - 效果一样：下一轮 ``init_db()`` 会重新 ``create_all`` 重建。
#
#   ⚠️ **为什么用 ``checkfirst=True``**
#   表可能还没建（第一次跑）⇒ 不加这个参数会报「表不存在」。
@pytest.fixture(autouse=True, scope="session")
def _reset_test_database():
    """每轮测试开始前清空测试库的表。"""
    _TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ⚠️ **先确保目录存在，再尝试清理旧表** ——
    #    库文件还不存在时（第一次跑）直接跳过清理。
    if not _TEST_DB_PATH.exists():
        yield
        return

    try:
        from backend.app.core.db import get_engine
        from backend.app.domain.models import Base

        engine = get_engine()
        Base.metadata.drop_all(engine, checkfirst=True)
    except Exception:
        # ⚠️ **清理失败不能阻断测试** ——
        #    清理只是「让起点更干净」的辅助手段，
        #    真正保证隔离的是「每个用例自带内存库」。
        #    ⇒ 这里静默降级，让测试继续跑。
        pass

    yield
