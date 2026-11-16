"""R19 —— 防止「僵尸测试目录」复活。

背景
----
``backend/tests/`` 曾长期存在，里面两个文件（``test_dependencies.py`` /
``test_framework.py``）：

- **不在** ``pyproject.toml`` 的 ``testpaths`` 里 → 裸 ``pytest`` 根本不收集；
- 导入根算错（``parents[1]/"backend"`` → ``backend/backend``，从不存在的路径）；
- 用了 ``@pytest.mark.asyncio``，但 ``pytest-asyncio`` **从未声明为依赖**。

结果就是：它们「躺在仓库里假装是测试，实际一次都没跑过」，
显式跑还会炸（21 failed + 20 errors）。本轮已迁入 ``backend/app/tests/``。

本文件是**护栏**：防止同类问题再次发生。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "pyproject.toml"
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "backup", ".venv", "venv", "dist"}


def _testpaths() -> list[str]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    ini = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return list(ini.get("testpaths", []))


def test_legacy_backend_tests_directory_is_gone():
    """⚠️ ``backend/tests/`` 必须**不存在**。

    回归点：它曾经躺在那里 —— 不在 ``testpaths`` 里、导入根算错、
    依赖未声明的 pytest-asyncio，等于 48 个永远不跑的测试。
    """
    legacy = ROOT / "backend" / "tests"
    assert not legacy.exists(), (
        f"backend/tests/ 又出现了：{legacy}\n"
        "这些文件不在 pytest testpaths 里，不会被收集 —— 请迁入 "
        "backend/app/tests/ 后再删除。"
    )


def _is_real_test_file(path: Path) -> bool:
    """判断文件是不是「真正的 pytest 测试」。

    有些叫 ``test_*.py`` 的文件其实是**手动调试脚本**：
    顶层直接 ``print()``、没有 ``def test_*`` 函数、
    或者只在 ``if __name__ == "__main__"`` 里跑。

    那种文件被 pytest 收集反而会出错（导入就炸 / 顶层执行副作用），
    所以它们应该改名为 ``check_*.py`` / ``probe_*.py`` 之类，
    而不是留在 ``test_*.py`` 里假装是测试。
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"^def test_\w+", text, re.MULTILINE):
        return True
    if re.search(r"^\s+def test_\w+", text, re.MULTILINE):
        return True
    # 有 test_ 函数但被包在类里也算（class TestXxx）。
    return bool(re.search(r"^class Test\w+", text, re.MULTILINE))


def test_every_test_file_is_inside_a_ci_covered_root():
    """⚠️ 所有「真测试」都必须在 CI 实际会跑的目录下。

    这是「僵尸测试」的根本防线：不在任何 CI 作业覆盖范围内的测试文件
    永远不会被执行，却会让人误以为该区域已有覆盖。

    当前 CI 覆盖的 pytest 入口（见 ``.github/workflows/ci.yml``）：

    1. ``backend/app/tests``（后端作业）
    2. ``agent-service/tests``（智能体服务作业，``working-directory: agent-service``）

    ⚠️ 注意：**``backend/tests`` 不在其中** —— 它曾经存在，
    不在 testpaths 里、导入根算错、依赖未声明，
    等于 48 个永远不跑的测试。本轮已迁走。
    """
    covered_roots = [
        (ROOT / "backend" / "app" / "tests").resolve(),
        (ROOT / "agent-service" / "tests").resolve(),
    ]

    orphans: list[str] = []
    for path in ROOT.rglob("test_*.py"):
        if set(path.parts) & SKIP_DIRS:
            continue
        if not _is_real_test_file(path):
            # 手动脚本不算「僵尸测试」，另有 test_no_manual_scripts_named_like_tests 盯着。
            continue
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in covered_roots):
            orphans.append(str(path.relative_to(ROOT)))

    assert not orphans, (
        "以下测试文件不在任何 CI 作业覆盖的 pytest 路径内，永远不会被执行：\n"
        + "\n".join(f"  - {p}" for p in sorted(orphans))
        + "\n请迁入 backend/app/tests/ 或 agent-service/tests/。"
    )


def test_no_manual_scripts_are_named_like_tests():
    """⚠️ 手动调试脚本不得叫 ``test_*.py``。

    叫这个名字有两种坏结果：

    1. 被 pytest 收集 → 导入时就执行副作用，或直接 ImportError；
    2. **不被**收集 → 让人误以为这里有测试覆盖。

    正确做法：改名成 ``check_*.py`` / ``probe_*.py`` / ``verify_*.py``，
    或者真的写成 pytest 测试。
    """
    manual_scripts: list[str] = []
    for path in ROOT.rglob("test_*.py"):
        if set(path.parts) & SKIP_DIRS:
            continue
        if _is_real_test_file(path):
            continue
        manual_scripts.append(str(path.relative_to(ROOT)))

    assert not manual_scripts, (
        "以下文件叫 test_*.py 但不是 pytest 测试（手动脚本），请改名或改写成测试：\n"
        + "\n".join(f"  - {p}" for p in sorted(manual_scripts))
    )


def _has_asyncio_decorator(text: str) -> bool:
    """检查文件里**真正**用了 ``@pytest.mark.asyncio`` 装饰器。

    ⚠️ 必须排除注释与字符串 —— 否则本文件自己的说明文字
    （里面就写着 ``@pytest.mark.asyncio``）会把自己判为违规。
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@"):
            continue
        if "pytest.mark.asyncio" in stripped:
            return True
    return False


def test_no_test_file_uses_undeclared_asyncio_marker():
    """⚠️ 不得使用未声明的 ``asyncio`` marker。

    回归点：``backend/tests/test_dependencies.py`` 用了
    ``@pytest.mark.asyncio``，但 ``pytest-asyncio`` 从未写进
    ``backend/requirements.txt`` → 收集阶段直接报
    ``'asyncio' not found in markers configuration option``。

    仓库约定：**不引入异步测试依赖**，需要跑协程就用
    ``asyncio.run(...)`` 包一层。

    ⚠️ 检查的是**装饰器本身**（行首的 ``@pytest.mark.asyncio``），
    不是文件里有没有出现这个字符串 —— 否则本文件自己的说明文字
    就会把自己判为违规。
    """
    offenders: list[str] = []
    for path in (ROOT / "backend").rglob("test_*.py"):
        if set(path.parts) & SKIP_DIRS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _has_asyncio_decorator(text):
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, (
        "以下测试使用了 @pytest.mark.asyncio，但本仓库不声明 pytest-asyncio：\n"
        + "\n".join(f"  - {p}" for p in sorted(offenders))
        + "\n请改用 asyncio.run(...) 包一层。"
    )


def test_no_test_uses_the_old_app_import_root():
    """⚠️ 测试里不得使用旧的 ``from app.xxx import`` 导入根。

    仓库统一用 ``from backend.app.xxx import``（54+ 个文件），
    因为包根是**仓库根**。``from app.xxx`` 依赖把 ``backend/``
    塞进 ``sys.path`` —— 一旦目录层级变化就会静默 ImportError。
    """
    offenders: list[str] = []
    for path in (ROOT / "backend" / "app" / "tests").rglob("test_*.py"):
        if set(path.parts) & SKIP_DIRS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("from app.", "import app.")):
                offenders.append(f"{path.relative_to(ROOT)}: {stripped}")
                break

    assert not offenders, (
        "以下测试仍使用旧导入根 `from app.xxx`：\n" + "\n".join(f"  - {o}" for o in offenders)
    )


def test_tests_must_not_write_to_the_shared_database():
    """⚠️ 测试不得对**共享的 perf.db** 做写操作。

    回归点：``backend/tests/test_dependencies.py`` 的 ``db`` fixture 用的是
    ``get_sessionmaker()`` —— 也就是**共享的 perf.db**。而 mock token 路径
    在库里找不到员工时会 ``db.add(employee); db.commit()``，
    甚至有一处直接 ``session.query(models.Employee).delete()``：

        ⚠️ 测试**往真实数据库写数据 / 删数据**，后果不可逆。

    ⚠️ **允许的例外：CI 专用库模式**。

    ``test_hex_id_routes.py`` 会在库里建一个 ``CI_ADMIN`` 账号 ——
    这是**有意为之**的：CI 跑在全新的 ``_ci_local.db`` 上，
    需要一个管理员才能签发 Token（否则 401/403 会掩盖
    「ID 格式是否被接受」这个真正的断言点）。它甚至专门用
    Alembic 而不是 ``create_all()`` 建表，就是为了不破坏后续迁移检查。

    这种文件必须在文件里**显式声明**自己是 CI 库模式，
    本检查据此放行 —— 避免以后新增的测试在不知情的情况下写库。

    声明方式：文件里出现 ``_ci_local.db`` 或 ``CI 管理员`` 这类标记。
    """
    write_markers = (".add(", ".add_all(", ".delete(", ".commit(", ".flush()")
    # 显式声明「我用的是 CI 专用库」的文件 —— 放行。
    ci_db_markers = ("_ci_local.db", "CI 管理员")
    offenders: list[str] = []
    for path in (ROOT / "backend" / "app" / "tests").rglob("test_*.py"):
        if set(path.parts) & SKIP_DIRS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "get_sessionmaker()" not in text:
            continue
        # 隔离的标志：monkeypatch 了 database_url，或用 create_engine 造独立库。
        if "database_url" in text or "create_engine" in text:
            continue
        if not any(marker in text for marker in write_markers):
            continue
        if any(marker in text for marker in ci_db_markers):
            continue
        offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, (
        "以下测试对共享库做了写操作，可能污染真实数据库：\n"
        + "\n".join(f"  - {p}" for p in sorted(offenders))
        + "\n请改用隔离的 tmp_path 数据库（monkeypatch database_url），"
        "\n或明确声明使用 CI 专用库（_ci_local.db）。"
    )


@pytest.mark.parametrize(
    "filename",
    [
        "test_framework_principles.py",
        "test_dependencies_acl.py",
    ],
)
def test_migrated_legacy_files_are_present_and_active(filename):
    """迁移后的两个文件必须在活跃套件里（且能被收集到）。"""
    target = ROOT / "backend" / "app" / "tests" / filename
    assert target.exists(), f"迁移后的 {filename} 不见了"
    text = target.read_text(encoding="utf-8")
    assert "def test_" in text, f"{filename} 里没有测试函数"
