"""验证脚本的**可执行性**护栏。

## 背景

真实案例（2026-11-16）：台账记录「A4 端到端验证脚本已交付」，
但脚本**从未真正跑通过** —— 一执行就崩：

    ModuleNotFoundError: No module named 'backend'

### 根因

Python 把**脚本所在目录**（``backend/scripts/``）设为 ``sys.path[0]``。
所以直接跑 ``python backend/scripts/xxx.py`` 时，
``import backend.app...`` 找不到包 —— 除非脚本自己把仓库根插到 ``sys.path``。

### 为什么这类 bug 特别危险

- 台账会记成「已交付」，实际**一行都没验证过**；
- 单测覆盖不到（单测用 ``conftest.py`` 注入路径，不走脚本入口）；
- CI 不跑这些脚本（它们要连真实库）。

所以必须有**自动化护栏**在提交前拦住。

## 本文件的职责

1. 所有 ``backend/scripts/verify_*.py`` 都能从**仓库根**直接运行；
2. 脚本不依赖「当前工作目录」；
3. 脚本里对固定业务数据的写入是**幂等**的（可重复运行）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "backend" / "scripts"


def _script_files() -> list[Path]:
    return sorted(SCRIPTS_DIR.glob("*.py"))


def _imports_backend_package(tree: ast.Module) -> bool:
    """该脚本是否 import 了 ``backend.*`` / ``app.*``。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(("backend.", "app.")):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(("backend.", "app.")):
                return True
    return False


def _has_sys_path_bootstrap(source: str) -> bool:
    """脚本是否有 ``sys.path`` 引导。

    只看**有没有** ``sys.path`` 字样不够 —— 必须真的**插入**了路径。
    """
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "sys.path.insert" in stripped or "sys.path.append" in stripped:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 一、路径引导
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "path", _script_files(), ids=lambda p: p.name
)
def test_script_can_run_from_repo_root(path: Path):
    """⚠️ 脚本必须能从**仓库根**直接运行。

    回归点：``verify_result_submission_e2e.py`` /
    ``verify_department_approval_e2e.py`` / ``verify_plan_approval_e2e.py``
    都缺 ``sys.path`` 引导 —— 一执行就是
    ``ModuleNotFoundError: No module named 'backend'``。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    if not _imports_backend_package(tree):
        pytest.skip("该脚本不 import 后端包，无需路径引导")

    assert _has_sys_path_bootstrap(source), (
        f"{path.name} 缺少 sys.path 引导 —— "
        "从仓库根直接运行会 ModuleNotFoundError: No module named 'backend'"
    )


@pytest.mark.parametrize(
    "path", _script_files(), ids=lambda p: p.name
)
def test_script_bootstrap_points_to_repo_root(path: Path):
    """⚠️ 引导必须指向**仓库根**（``parents[2]``），不是 backend 目录。

    脚本用的是 ``from backend.app...`` 的**包路径**，
    所以 ``sys.path`` 里要放仓库根 —— 放 ``backend/`` 会变成
    ``ModuleNotFoundError: No module named 'backend'``（因为 ``backend``
    成了顶层，``backend.backend`` 不存在）。
    """
    source = path.read_text(encoding="utf-8")
    if not _imports_backend_package(ast.parse(source)):
        pytest.skip("该脚本不 import 后端包")

    if "from backend." not in source:
        pytest.skip("该脚本用 app.* 风格导入，引导到 backend/ 目录即可")

    assert "parents[2]" in source, (
        f"{path.name} 的 sys.path 引导应指向仓库根（Path(__file__).resolve().parents[2]）"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 二、导入顺序（ruff E402）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "path", _script_files(), ids=lambda p: p.name
)
def test_imports_after_bootstrap_have_noqa(path: Path):
    """⚠️ 路径引导之后的 import 必须带 ``# noqa: E402``。

    ``sys.path.insert`` 之后的模块级 import 会被 ruff 判为 E402
    （module level import not at top of file）。

    ⚠️ **直接复用 ruff 的判定**，不自己重新实现一套 ——
    自己实现会漏掉「标准库 import 豁免」这类细节，
    反而产生误报。
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            str(path),
            "--select",
            "E402",
            "--no-cache",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if result.returncode == 0:
        return
    # 有 E402 违规 → 失败并附上 ruff 的原始输出
    assert result.returncode == 0, (
        f"{path.name} 存在 E402（路径引导后的 import 缺 '# noqa: E402'）：\n"
        f"{result.stdout}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 三、幂等性（固定业务数据）
# ═══════════════════════════════════════════════════════════════════════════


#: 这些脚本会往真实库写**固定主键/固定唯一键**的数据。
#: 不做幂等处理 → 第二次运行撞唯一约束直接崩。
IDEMPOTENT_REQUIRED = {
    "verify_result_submission_e2e.py",
    "verify_department_approval_e2e.py",
    "verify_plan_approval_e2e.py",
}


@pytest.mark.parametrize(
    "script_name", sorted(IDEMPOTENT_REQUIRED), ids=lambda n: n
)
def test_fixed_key_script_is_idempotent(script_name: str):
    """⚠️ 写固定唯一键的脚本必须先清理历史残留。

    回归点：三个 e2e 脚本都用固定的 ``plan_month="2099-08"`` 建月计划，
    而 ``month_plan`` 上有 ``UNIQUE(owner_id, plan_month)`` ——
    第二次运行直接崩。
    """
    source = (SCRIPTS_DIR / script_name).read_text(encoding="utf-8")

    # 必须有「删除旧数据」的逻辑
    has_cleanup = (
        "delete(" in source
        or "cleanup" in source.lower()
        or "_cleanup_previous_run" in source
    )
    assert has_cleanup, (
        f"{script_name} 写入固定唯一键的数据却没有清理逻辑 —— "
        "脚本不可重复运行"
    )

    # 必须捕获唯一约束冲突（兜底）
    assert "IntegrityError" in source or "rollback" in source, (
        f"{script_name} 缺少唯一约束冲突的兜底处理"
    )


def test_e2e_scripts_are_listed_in_docs():
    """⚠️ e2e 脚本应该在台账里有登记。

    台账说「已交付」但脚本根本跑不通 —— 这类问题只能靠
    「台账登记 + 自动化验证」双保险。
    """
    docs = ROOT / "docs" / "11-待完善清单.md"
    if not docs.exists():
        pytest.skip("台账文档不存在")

    content = docs.read_text(encoding="utf-8")
    for name in sorted(IDEMPOTENT_REQUIRED):
        assert name in content, f"台账里没有登记 {name}"


def test_fixed_months_are_unique_across_scripts():
    """⚠️ **不同脚本必须用不同的固定月份**。

    回归点（真实踩坑）：A3 与 A4 脚本都用 ``plan_month="2099-08"``。
    而这些脚本会**随机挑同一个 owner**（第一个有直属下属的员工）——
    于是 A4 先跑插入、A3 再跑就撞 ``UNIQUE(owner_id, plan_month)``。

    症状极具误导性：看起来像「A3 脚本不幂等」，
    实际是**脚本间互相干扰**。排查成本很高。

    所以这里直接断言：所有固定月份互不相同。
    """
    import ast
    import re

    months: dict[str, list[str]] = {}

    for name in sorted(IDEMPOTENT_REQUIRED):
        source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # ⚠️ **只看实际赋值**，不看注释 ——
        #    注释里提到某个月份是「说明」，不是「占用」。
        assigned: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if "MONTH" not in target.id.upper():
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    assigned.add(node.value.value)

        # 兜底：形如 ``plan_month="2099-08"`` 的关键字实参
        for match in re.finditer(r'plan_month\s*=\s*"(\d{4}-\d{2})"', source):
            assigned.add(match.group(1))

        verify_months = {
            m for m in assigned if m.startswith(("2098", "2099", "2100"))
        }
        if verify_months:
            months[name] = sorted(verify_months)

    assert months, "没找到脚本里的固定验证月份"

    seen: dict[str, str] = {}
    for name, month_list in months.items():
        for month in month_list:
            assert month not in seen, (
                f"{name} 与 {seen[month]} 用了同一个固定月份 {month} —— "
                "脚本会随机挑同一个 owner，导致互相撞 UNIQUE(owner_id, plan_month)"
            )
            seen[month] = name


# ═══════════════════════════════════════════════════════════════════════════
# 四、脚本不得依赖 CWD
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "path", _script_files(), ids=lambda p: p.name
)
def test_script_does_not_hardcode_relative_db_path(path: Path):
    """⚠️ 脚本里不能出现**相对**的数据库路径。

    ⚠️ **Alembic 与应用的 DB 路径陷阱**（运维踩坑记录里记过）：
    ``sqlite:///./data/perf.db`` 相对的是**进程 CWD**。
    从仓库根跑 → 打到 ``<root>/data/perf.db``；
    从 ``backend/`` 跑 → 打到 ``backend/data/perf.db``。
    两者是**不同的库**。

    所以脚本必须用**绝对路径**（基于 ``__file__`` 推导）。

    ⚠️ 只检查**字符串字面量**，不看注释 —— 注释里提到
    ``sqlite:///./data/perf.db`` 是在**解释**这个陷阱，不是违规。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # ⚠️ **跳过所有 docstring** —— 模块/类/函数的 docstring 是
    #    「说明文字」，提到相对路径是在**解释**这个陷阱，不是违规。
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstring_nodes:
            continue
        literal = node.value
        if "sqlite:///" not in literal:
            continue
        # 相对路径：``sqlite:///./x.db`` 或 ``sqlite:///data/x.db``
        after_scheme = literal.split("sqlite:///", 1)[1]
        if not after_scheme:
            continue
        # 绝对路径（Windows 盘符 / 绝对 / 以 { 拼接的变量）→ 放行
        if after_scheme.startswith(("/", "\\")) or (
            len(after_scheme) > 1 and after_scheme[1] == ":"
        ):
            continue
        if "{" in after_scheme:
            # f-string 拼接：只要片段本身是相对路径就有风险
            if after_scheme.startswith(("./", "../")):
                raise AssertionError(
                    f"{path.name}:{node.lineno} —— 相对数据库路径会在不同 CWD 下"
                    f"打到不同的库：\n  {literal!r}"
                )
            continue
        raise AssertionError(
            f"{path.name}:{node.lineno} —— 相对数据库路径会在不同 CWD 下"
            f"打到不同的库：\n  {literal!r}"
        )
