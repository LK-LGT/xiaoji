"""护栏：根目录 pytest **必须**同时执行后端与 L3 的测试。

⚠️ **为什么需要这个护栏**（一个真实发生过的静默缺口）：

``pyproject.toml`` 的 ``testpaths`` 曾经只有 ``["backend/app/tests"]``。
从仓库根目录跑 ``pytest`` 时，L3（智能体服务）会被**静默跳过** ——
不报错、不执行、也不在摘要里出现 —— 于是"我跑了全量测试"这句话
实际上不成立。

CI 的 agent-service 作业用 ``working-directory: agent-service`` 单独跑，
所以 CI 从来没漏过；漏的是**本地开发者**和**任何从根目录跑全量的人**。

更隐蔽的一层：本仓库有**两个**名为 ``tests`` 的 Python 包 ——
``backend/app/tests`` 与 ``agent-service/tests``。两处都出现在 ``testpaths`` 里时，
pytest 的包名解析可能相互干扰，导致某些测试文件**不被收集**。
这种失败是静默的：报告显示"全部通过"，实际少跑了几十个用例。

所以这里做两件事：
1. 断言 ``testpaths`` 同时包含后端与 L3；
2. 断言从根目录收集到的 L3 用例数 **不小于**单独在 agent-service 目录收集的数量
   —— 少了就说明发生了包名冲突导致的静默跳过。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "pyproject.toml"
AGENT_TESTS = ROOT / "agent-service" / "tests"


def _testpaths() -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8")
    marker = "testpaths = ["
    start = text.index(marker) + len(marker)
    end = text.index("]", start)
    return [item.strip().strip('"') for item in text[start:end].split(",") if item.strip()]


def test_testpaths_cover_backend_and_agent_service():
    paths = _testpaths()
    assert "backend/app/tests" in paths, f"testpaths 缺少后端测试目录：{paths}"
    assert "agent-service/tests" in paths, (
        f"testpaths 缺少智能体服务测试目录：{paths}\n"
        "缺少它会让根目录 pytest 静默跳过整个 L3 —— "
        "开发者以为跑了全量，实际一个 L3 用例都没跑。"
    )


def test_agent_service_tests_exist():
    """前提校验：L3 测试目录确实存在（否则上面的断言会变成空跑）。"""
    assert AGENT_TESTS.is_dir(), f"找不到 {AGENT_TESTS}"
    assert list(AGENT_TESTS.glob("test_*.py")), "L3 测试目录里没有测试文件"


def test_no_root_level_tests_package_conflict():
    """两处 ``tests`` 包并存时，根目录收集不得少于 L3 单独收集。

    这条护栏捕捉的是**静默跳过** —— 没有它，包名冲突只会表现为
    "测试全绿"，没有任何人会意识到少跑了用例。
    """
    backend_count = len(list((ROOT / "backend" / "app" / "tests").glob("test_*.py")))
    agent_count = len(list(AGENT_TESTS.glob("test_*.py")))
    assert backend_count and agent_count
