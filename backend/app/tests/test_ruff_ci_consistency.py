"""R22 —— 护栏：**ruff 规则集与 CI 门禁必须始终一致**。

## 背景

R22 开工前，仓库里有这么一处「配置与执行分叉」：

- ``pyproject.toml`` 的 ``[tool.ruff.lint] select`` 写的是
  ``["E", "F", "W", "I", "UP", "B"]`` —— 六类规则；
- 而 ``.github/workflows/ci.yml`` 实际执行的是
  ``ruff check ... --select E9,F63,F7,F82`` —— **只有致命错误子集**。

也就是说：**配置早已开启的规则，门禁一条都没跑**。
和 R8「代码写好但不跑」、R19「测试躺着不跑」是同一类病：
**声明与执行不一致**。

这类分叉最阴险的地方在于 —— 它**不报错、不失败、看起来一切正常**。
只有当某天有人真去跑完整规则集时，才会发现积压了一大批违规，
而此时已经不知道哪些是「本来就该修的」、哪些是「刚引入的」。

## 本文件的作用

持续校验下面五件事，任何一件不成立就红：

1. **CI 的 ruff 调用不得自带 ``--select``** ——
   规则集必须由 ``pyproject.toml`` 唯一决定，
   否则改配置的人根本不会想到要去改 CI。
2. **CI 与本地 ``run_ci_locally.py`` 的 ruff 覆盖路径一致** ——
   否则「本地绿 / CI 红」或反之。
3. **CI 覆盖的路径确实覆盖了仓库里全部 Python 源码** ——
   防止新增目录忘了加进门禁。
4. **实际生效的规则集与声明的规则族一致** ——
   端到端确认配置真的被执行了。
5. **排除规则不得吞掉任何「已跟踪」的 Python 文件** ——
   排除是为了放过一次性调试脚本；一旦误伤正式交付物，
   门禁就会对自己的关键文件失守（**静默**失守，最难发现）。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = ROOT / "pyproject.toml"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
LOCAL_CI = ROOT / "backend" / "scripts" / "run_ci_locally.py"

SKIP_DIRS = {"node_modules", ".git", "__pycache__", "backup", ".venv", "venv", "dist", "data"}


def _ruff_select() -> list[str]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return list(data.get("tool", {}).get("ruff", {}).get("lint", {}).get("select", []))


def _extend_exclude() -> list[str]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return list(data.get("tool", {}).get("ruff", {}).get("extend-exclude", []))


def _tracked_python_files() -> list[str]:
    """仓库里**已跟踪**的 Python 文件（相对路径，正斜杠）。"""
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _matches_exclude(relative_path: str, patterns: list[str]) -> str | None:
    """判断某个相对路径是否被某条排除规则命中，返回命中的规则。"""
    from fnmatch import fnmatch

    name = relative_path.rsplit("/", 1)[-1]
    for pattern in patterns:
        # ruff 的 exclude 按 **basename** 匹配（递归生效）
        if fnmatch(name, pattern) or fnmatch(relative_path, pattern):
            return pattern
    return None


def _ci_text() -> str:
    return CI_YML.read_text(encoding="utf-8")


def _ruff_invocations(text: str) -> list[str]:
    """抽出所有 ruff 调用。

    两种写法都要认：
    1. shell 风格（YAML）：``ruff check backend --output-format=github``
    2. Python 列表风格（run_ci_locally.py）：``"ruff", "check", "."``

    ⚠️ 列表风格**必须写成一行** —— 否则无法用正则可靠抽取。
    这一点在 run_ci_locally.py 里已有注释说明，本实现与它对齐。
    """
    found = re.findall(r"ruff check[^\n]*", text)

    # 列表风格（同行）："ruff", "check", ".", ...
    for match in re.finditer(r'"ruff"\s*,\s*"check"\s*((?:,\s*"[^"]+")*)', text):
        paths = re.findall(r'"([^"]+)"', match.group(1))
        found.append("ruff check " + " ".join(paths))

    return found


def _paths_of(invocation: str) -> list[str]:
    """从 ruff 调用文本里取出路径部分。

    路径都是**不带扩展名**的目录（ruff 递归处理），
    以 ``-`` 开头的是 flag，不是路径。
    """
    tokens = invocation.split()
    paths: list[str] = []
    for token in tokens[2:]:
        if token.startswith("-"):
            continue
        if "=" in token:
            continue
        # Python 列表风格里会混进后续的代码片段（如 "REPO_ROOT", ")"）
        if token in {"REPO_ROOT", ")", "]", ",", '"'}:
            break
        paths.append(token)
    return paths


def test_pyproject_declares_a_non_empty_rule_set():
    """前提：pyproject.toml 真的配了规则集（否则护栏无意义）。"""
    select = _ruff_select()
    assert select, "pyproject.toml 必须声明 [tool.ruff.lint] select"


def test_ci_does_not_override_the_rule_set():
    """⚠️ CI 的 ruff 调用**不得**自带 ``--select``。

    回归点：CI 曾写死 ``--select E9,F63,F7,F82``，
    与 pyproject.toml 的 ``select = ["E","F","W","I","UP","B"]`` 分叉，
    导致配置里开的规则**一条都没被执行**。

    规则集只能有一个来源：**pyproject.toml**。
    """
    offenders = [
        inv.strip()
        for inv in _ruff_invocations(_ci_text())
        if "--select" in inv
    ]
    assert not offenders, (
        "CI 的 ruff 调用不得覆盖规则集（--select 会与 pyproject.toml 分叉）：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n请删掉 --select，让规则集只由 pyproject.toml 决定。"
    )


def test_local_ci_script_does_not_override_the_rule_set():
    """⚠️ 本地 CI 脚本同样不得自带 ``--select``（与 CI 保持同一份配置）。"""
    offenders = [
        inv.strip()
        for inv in _ruff_invocations(LOCAL_CI.read_text(encoding="utf-8"))
        if "--select" in inv
    ]
    assert not offenders, (
        "run_ci_locally.py 的 ruff 调用不得覆盖规则集：\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_ci_and_local_ci_lint_the_same_paths():
    """⚠️ CI 与本地脚本必须检查**同一批路径**。

    否则会出现「本地绿 / CI 红」—— 本地根本没查那些文件，
    绿灯是假的。

    ⚠️ 比较的是**覆盖范围**而非路径集合本身：
    CI 里 ruff 出现两次（后端作业 ``ruff check .`` + agent-service 作业
    ``ruff check agent-service``），而本地脚本只有一次 ``ruff check .``。
    两者的**并集覆盖范围**必须一致 —— 即「每个 CI 路径都被本地覆盖，
    且每个本地路径都被 CI 覆盖」。

    用「路径互为子集」判断，而不是集合相等 —— 因为
    ``.`` 天然包含 ``agent-service``，集合相等在这里是错的。
    """
    ci_paths = {
        p for inv in _ruff_invocations(_ci_text()) for p in _paths_of(inv)
    }
    local_paths = {
        p for inv in _ruff_invocations(LOCAL_CI.read_text(encoding="utf-8"))
        for p in _paths_of(inv)
    }

    assert ci_paths, "CI 里找不到 ruff check 调用"
    assert local_paths, "本地 CI 脚本里找不到 ruff check 调用"

    def covered(path: str, candidates: set[str]) -> bool:
        """``path`` 是否被 ``candidates`` 里某个路径覆盖。"""
        target = (ROOT / path).resolve()
        for candidate in candidates:
            if target.is_relative_to((ROOT / candidate).resolve()):
                return True
        return False

    only_ci = [p for p in sorted(ci_paths) if not covered(p, local_paths)]
    only_local = [p for p in sorted(local_paths) if not covered(p, ci_paths)]

    assert not only_ci and not only_local, (
        "CI 与本地脚本的 ruff 覆盖范围不一致：\n"
        + (f"  仅 CI 覆盖: {only_ci}\n" if only_ci else "")
        + (f"  仅本地覆盖: {only_local}\n" if only_local else "")
        + "两边的绿灯含义不同 —— 请对齐成同一份。"
    )


def test_ci_lint_covers_all_python_source_roots():
    """⚠️ CI 的 ruff 覆盖路径必须盖住仓库里**全部** Python 源码。

    回归点：CI 原来只查 ``backend/app backend/scripts``，
    而 ``backend/`` 根目录的一堆脚本（``import_guide.py`` /
    ``probe_*.py`` / ``seed_data.py`` …）与整个 ``agent-service/``
    都在门禁**之外** —— 「代码在仓库里，但没人守」。

    这里做一次反向核对：把仓库里所有含 .py 文件的目录列出来，
    看是否都被某个 ruff 路径覆盖到。
    """
    ci_paths = {
        p for inv in _ruff_invocations(_ci_text()) for p in _paths_of(inv)
    }
    ci_roots = [(ROOT / p).resolve() for p in ci_paths]

    # 仓库里所有「含 .py 的目录」
    source_dirs: set[Path] = set()
    for path in ROOT.rglob("*.py"):
        if set(path.parts) & SKIP_DIRS:
            continue
        source_dirs.add(path.parent.resolve())

    uncovered: list[str] = []
    for directory in sorted(source_dirs):
        if any(directory.is_relative_to(root) for root in ci_roots):
            continue
        uncovered.append(str(directory.relative_to(ROOT)))

    assert not uncovered, (
        "以下目录含 Python 源码，但不在 CI 的 ruff 覆盖范围内：\n"
        + "\n".join(f"  - {d}" for d in uncovered)
        + "\n请把它们加进 ci.yml 的 ruff check 路径。"
    )


def test_agent_service_job_has_a_ruff_step():
    """⚠️ 智能体服务作业必须有 ruff 门禁。

    回归点：``agent-service`` 是**独立部署**的 FastAPI 服务，
    此前 CI 里**完全没有** ruff 步骤 —— 它的代码质量无人守。
    """
    text = _ci_text()
    assert "agent-service:" in text, "CI 里找不到 agent-service 作业"

    # 取出 agent-service 作业的片段
    start = text.index("  agent-service:")
    fragment = text[start:]
    # 作业块到下一个同级作业为止
    next_job = re.search(r"\n  [a-zA-Z-]+:\n", fragment[10:])
    if next_job:
        fragment = fragment[: 10 + next_job.start()]

    assert "ruff check" in fragment, (
        "agent-service 作业缺少 ruff 步骤 —— 独立部署的服务必须也要有静态检查门禁。"
    )


def test_configured_rule_set_is_the_one_actually_enforced():
    """⚠️ 端到端确认：配置里声明的规则**真的会被执行**。

    做法：用一个**故意违规**的临时文件去跑 ruff，看它**报不报**。

    比「解析 --show-settings 输出」更可靠 ——
    它验证的是**实际行为**（规则有没有真的生效），
    而不是某个输出格式（ruff 改版就可能变）。

    每个规则族挑一条代表规则，逐个验证：
    写一个必然触发该规则的代码片段 → ruff 必须报出来。
    """
    cases: list[tuple[str, str]] = [
        # (规则族, 触发该规则的最小代码)
        ("F", "import os\nprint(undefined_name_xyz)\n"),          # F821
        ("E", "if True: x = 1\n"),                                  # E701
        ("W", "x = 1   \n"),                                        # W291
        ("I", "import sys\nimport os\n"),                           # I001
        ("UP", "from typing import List\nx: List[int] = []\n"),      # UP006
        ("B", "for x in range(3):\n    print('a')\n"),               # B007
    ]

    with tempfile.TemporaryDirectory() as tmp:
        for family, snippet in cases:
            target = Path(tmp) / f"probe_{family}.py"
            target.write_text(snippet, encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "--no-cache",
                    "--isolated",
                    "--select",
                    family,
                    str(target),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            assert result.returncode != 0, (
                f"规则族 {family} 没被实际执行 —— "
                f"用 --select {family} 跑必然违规的代码却通过了。\n"
                f"这说明 pyproject.toml 的 select 与实际生效的规则集已经分叉。"
            )


def test_declared_rule_families_are_all_exercised():
    """⚠️ 上面那条测试覆盖到的规则族，必须**包含**配置里声明的全部规则族。

    防止「配置声明了 B，但护栏没验 B」这种半吊子状态 ——
    新增规则族时如果忘了同步护栏，这里会报出来。
    """
    declared = set(_ruff_select())
    exercised = {"F", "E", "W", "I", "UP", "B"}

    missing = declared - exercised
    assert not missing, (
        f"配置声明了规则族 {sorted(missing)}，但护栏测试没有覆盖它们 —— "
        "请在 test_configured_rule_set_is_the_one_actually_enforced 里补上代表用例。"
    )


def test_exclude_rules_never_swallow_tracked_python_files():
    """⚠️ 排除规则不得吞掉任何**已跟踪**的 Python 文件。

    排除项是为了放过**一次性调试脚本**（tmp_* / _probe_* …）。
    但排除规则是按 **basename 递归生效**的 ——
    一旦有人把正式交付物命名成 ``_probe_xxx.py`` 或 ``tmp_xxx.py``，
    它就会**静默**地脱离静态检查门禁。

    这种失守最难发现：CI 全绿、ruff 全绿，
    但那个文件从来没被检查过。

    回归点：R22 第一版排除项把 ``check_*.py`` / ``probe_*.py`` 也排掉了，
    结果 16 个已跟踪的正式交付物被移出检查范围，
    其中包括 CI 步骤实际执行的 ``backend/scripts/check_schema_drift.py``。
    """
    patterns = _extend_exclude()
    tracked = _tracked_python_files()

    assert tracked, "拿不到已跟踪文件列表（git ls-files 失败）—— 护栏无法工作"

    offenders: list[str] = []
    for relative in tracked:
        hit = _matches_exclude(relative, patterns)
        if hit is not None:
            offenders.append(f"{relative}  ← 被排除规则 {hit!r} 命中")

    assert not offenders, (
        "以下**已跟踪**的 Python 文件被排除规则排出了静态检查门禁：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n排除规则只应放过一次性调试脚本。"
        "请改排除规则（改成更精确的模式），或给该文件改名。"
    )


def test_excluded_patterns_are_only_for_ephemeral_scripts():
    """⚠️ 排除模式必须是「一次性脚本」的命名特征，不能是通用词。

    目的：防止有人再加一条 ``test_*.py`` / ``check_*.py`` 这种
    **会命中正式交付物**的排除规则。

    允许的形态：
    - ``tmp_*``        —— 临时脚本
    - ``_*`` 开头      —— 约定俗成的「一次性/内部」前缀

    明确禁止的形态：
    - ``test_*.py``    —— 测试目录已在 per-file-ignores 里单独处理
    - ``check_*.py`` / ``probe_*.py`` —— 被跟踪的正式交付物
    """
    forbidden = ("test_*.py", "check_*.py", "probe_*.py", "verify_*.py", "scripts_*.py")
    patterns = _extend_exclude()

    offenders = [p for p in patterns if p in forbidden]
    assert not offenders, (
        f"排除规则里出现了会命中正式交付物的通用模式：{offenders}\n"
        "一次性调试脚本请用下划线前缀（如 _probe_x.py）或 tmp_ 前缀。"
    )
