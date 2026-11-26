"""依赖锁文件（lock file）的护栏测试（CI-6）。

⚠️ **为什么需要这个文件**
------------------------------------
本项目的 ``requirements.txt`` 全部用 ``>=`` 下界（方便升级），
配套的 ``requirements.lock.txt`` 才是**真正可复现**的安装依据。

但 lock 文件有个**极易被污染**的地方：``pip-tools`` 会把
**本机 pip 的配置**原样写进文件头 —— 最典型的是 ``--index-url``。

⚠️ **真实踩过的坑**
------------------------------------
本地开发者的 pip 配了清华镜像：

    --index-url https://pypi.tuna.tsinghua.edu.cn/simple

pip-tools 原样写进 lock 文件 ⇒ **CI 也会走这个源**：

- CI 速度被拖慢（境外访问国内镜像）；
- 镜像挂了 ⇒ **整个 CI 挂掉**（而它本该用官方源）；
- 本地环境配置泄漏进仓库。

⇒ 本文件守住三条线：**不带源配置 / 带 hash / 与 requirements 一致**。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

#: 需要 lock 文件的服务 → 各自的 requirements 路径。
LOCK_TARGETS: dict[str, str] = {
    "backend": "backend/requirements.txt",
    "agent-service": "agent-service/requirements.txt",
}


def _lock_path(service: str) -> Path:
    return ROOT / service / "requirements.lock.txt"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("service", sorted(LOCK_TARGETS))
def test_lock_file_exists(service: str) -> None:
    """⚠️ 每个服务都必须有 lock 文件 —— 否则依赖不可复现。"""
    path = _lock_path(service)
    assert path.exists(), (
        f"缺少 {path.relative_to(ROOT)}。\n"
        "生成命令：\n"
        "  python -m piptools compile "
        f"{LOCK_TARGETS[service]} --output-file {path.relative_to(ROOT)} "
        "--generate-hashes --strip-extras --no-header"
    )


@pytest.mark.parametrize("service", sorted(LOCK_TARGETS))
def test_lock_file_has_no_local_index_url(service: str) -> None:
    """⚠️ **核心护栏** —— lock 文件不得包含本机的源配置。

    写了就会让 CI 走本地开发者的镜像源（慢 / 可能不可用），
    并把本地环境配置泄漏进仓库。
    """
    path = _lock_path(service)
    if not path.exists():
        pytest.skip(f"{service} 尚无 lock 文件")

    text = _read(path)
    offenders = [
        line
        for line in text.splitlines()
        if line.startswith("--index-url")
        or line.startswith("--extra-index-url")
        or line.startswith("--trusted-host")
    ]
    assert not offenders, (
        f"{path.relative_to(ROOT)} 里出现了本地源配置：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n请删除这些行后重新生成。pip-tools 会把本机 pip 配置写进来，"
        "但 lock 文件是**全团队共用**的，不能夹带任何人的本地配置。"
    )


@pytest.mark.parametrize("service", sorted(LOCK_TARGETS))
def test_lock_file_has_no_platform_pinning(service: str) -> None:
    """⚠️ ``--only-binary :all:`` **不得**写进 lock 文件。

    写成 ``:all:`` 时，**平台专属的 wheel**（``colorama`` 在 Windows、
    ``tzdata`` 在部分 Linux 镜像）会因为没有对应 wheel 而装不上。

    ⇒ 正确做法：由 ``PIP_ONLY_BINARY`` 环境变量控制，或干脆不限制。
    """
    path = _lock_path(service)
    if not path.exists():
        pytest.skip(f"{service} 尚无 lock 文件")

    text = _read(path)
    offenders = [
        line for line in text.splitlines() if line.startswith("--only-binary")
    ]
    assert not offenders, (
        f"{path.relative_to(ROOT)} 里写了 --only-binary：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n它会让没有预编译 wheel 的平台专属包安装失败。"
    )


@pytest.mark.parametrize("service", sorted(LOCK_TARGETS))
def test_every_locked_package_has_a_hash(service: str) -> None:
    """⚠️ **每个包都必须带 hash** —— 这是 lock 文件的防篡改保证。

    没有 hash，lock 文件就只是「版本号列表」，
    供应链攻击者可以在 PyPI 上投毒同名版本 ⇒ CI 静默装入恶意代码。
    """
    path = _lock_path(service)
    if not path.exists():
        pytest.skip(f"{service} 尚无 lock 文件")

    text = _read(path)
    # 包声明行：形如 ``pkg==1.2.3 \``
    package_lines = [
        line
        for line in text.splitlines()
        if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*==", line)
    ]
    assert package_lines, f"{path.relative_to(ROOT)} 里没解析出任何包"

    missing: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*==", line):
            continue
        # 该包的 hash 行在它之后、下一个包声明之前
        for follow in lines[index + 1 :]:
            if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*==", follow):
                break
            if "--hash=sha256:" in follow:
                break
        else:
            missing.append(line.split("==")[0])
            continue
        if "--hash=sha256:" not in follow:
            missing.append(line.split("==")[0])

    assert not missing, (
        f"{path.relative_to(ROOT)} 里这些包**没有 hash**：\n"
        + "\n".join(f"  - {m}" for m in missing)
        + "\n没有 hash 就没有供应链防篡改保证。"
    )


@pytest.mark.parametrize("service", sorted(LOCK_TARGETS))
def test_locked_versions_satisfy_requirements(service: str) -> None:
    """⚠️ lock 里的版本必须满足 ``requirements.txt`` 的下界。

    否则会出现「lock 文件里是旧版、requirements 里要新版」的漂移 ——
    CI 装的是 lock，开发者以为装的是 requirements。
    """
    path = _lock_path(service)
    req_path = ROOT / LOCK_TARGETS[service]
    if not path.exists():
        pytest.skip(f"{service} 尚无 lock 文件")

    # requirements.txt 里声明的 (包名, 下界)
    declared: dict[str, str] = {}
    for line in _read(req_path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z0-9][a-zA-Z0-9._-]*)\s*(>=|==)\s*([0-9][^\s;]*)", stripped)
        if match:
            declared[match.group(1).lower()] = match.group(3)

    # lock 里锁定的版本
    locked: dict[str, str] = {}
    for line in _read(path).splitlines():
        match = re.match(r"^([a-zA-Z0-9][a-zA-Z0-9._-]*)==([0-9][^\s;]*)", line)
        if match:
            locked[match.group(1).lower()] = match.group(2)

    violations: list[str] = []
    for name, minimum in declared.items():
        actual = locked.get(name)
        if actual is None:
            violations.append(f"{name}: lock 里没有，但 requirements 要求 >={minimum}")
            continue
        if _version_tuple(actual) < _version_tuple(minimum):
            violations.append(
                f"{name}: lock={actual} < requirements 要求的 >={minimum}"
            )

    assert not violations, (
        f"{path.relative_to(ROOT)} 与 {req_path.relative_to(ROOT)} 不一致：\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\n请重新生成 lock 文件。"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 跨服务依赖（2026-11-26）
# ═══════════════════════════════════════════════════════════════════════════
# ⚠️ **背景：CI 红了但本地全绿**
#   后端测试里有几个文件**直接 import agent-service 的模块**：
#     - test_agent_service_auth_r12.py
#     - test_plan_hallucination_detector.py
#     - test_qa_tool_flow.py
#     - test_agent_tool_foundation.py
#   而 ``agent-service/app/tools.py`` 会 ``from jsonschema import ...``
#   ⇒ 后端 CI 只装 ``backend/requirements.lock.txt`` ⇒
#     **ModuleNotFoundError: No module named 'jsonschema'**
#   ⇒ 这几组用例**整批挂掉**。
#   ⚠️ 本地之所以看不出来：开发者机器上**早就装过** jsonschema
#   （agent-service 开发时装过）⇒ 环境差异被掩盖了。
#
# ⚠️ **为什么不用 ``importorskip`` 绕过去**
#   那等于「让安全测试在 CI 里静默跳过」——
#   而 R12 那组恰恰是防「AI 接口裸奔」的**最后一道防线** ⇒ 绝不能跳过。
#
# ⇒ 正确做法：**跨服务测试用到的依赖，必须在 backend 侧显式声明**。

#: 后端测试会 import 的**其它服务**的代码目录。
_CROSS_SERVICE_ROOTS = ("agent-service",)

#: 后端测试文件里**出现过**的服务目录名（用于定位「谁在跨服务 import」）。
def _backend_tests_importing_cross_service() -> dict[str, list[str]]:
    """找出「后端测试里 import 了其它服务代码」的文件。

    返回 ``{依赖包名: [文件名, ...]}``。
    """
    result: dict[str, list[str]] = {}
    tests_dir = ROOT / "backend" / "app" / "tests"
    for path in sorted(tests_dir.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for service in _CROSS_SERVICE_ROOTS:
            if service not in text:
                continue
            # ⚠️ 只认「真的在 import」——
            #   光是字符串里提到目录名（比如注释）不算。
            if not re.search(rf"(importlib|import)\b[^\n]*{re.escape(service)}", text) and not re.search(
                rf"[\"']{re.escape(service)}[\"']", text
            ):
                continue
            result.setdefault(service, []).append(path.name)
    return result


def _declared_packages(requirements_path: Path) -> set[str]:
    """取 requirements.txt 里声明的包名（规范化：小写、``-``→``_``）。"""
    names: set[str] = set()
    for line in _read(requirements_path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z0-9][a-zA-Z0-9._-]*)", stripped)
        if match:
            names.add(match.group(1).lower().replace("-", "_"))
    return names


def _imported_top_level_modules(path: Path) -> set[str]:
    """取一个文件里 ``import X`` / ``from X import ...`` 的顶层模块名。"""
    modules: set[str] = set()
    text = path.read_text(encoding="utf-8", errors="ignore")
    for match in re.finditer(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_.]*)", text, re.M):
        modules.add(match.group(1).split(".")[0])
    for match in re.finditer(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import", text, re.M):
        modules.add(match.group(1).split(".")[0])
    return modules


def test_cross_service_imports_have_declared_dependencies() -> None:
    """⚠️ **核心护栏** —— 跨服务 import 的依赖必须在 backend 侧声明。

    回归点（2026-11-26 实测）：
    后端 CI 报 ``ModuleNotFoundError: No module named 'jsonschema'``
    ⇒ 根因是「后端测试 import agent-service 代码，
    但 agent-service 的依赖没在 backend 侧声明」。

    ⇒ 这条断言把「跨服务 import 到的第三方包」与
      「backend/requirements.txt 声明的包」对齐，
      少一个就红 ⇒ 不会等到 CI 才发现。
    """
    backend_req = ROOT / "backend" / "requirements.txt"
    declared = _declared_packages(backend_req)

    # ⚠️ **stdlib 白名单** —— 标准库不该出现在 requirements 里。
    import sys

    stdlib = set(sys.stdlib_module_names)

    offenders: list[str] = []
    for service in _CROSS_SERVICE_ROOTS:
        service_root = ROOT / service / "app"
        if not service_root.is_dir():
            continue
        # ⚠️ **只扫「服务自己的」代码**——
        #   第三方包不可能出现在自己代码里 ⇒ 命中的就是第三方 import。
        for module_path in sorted(service_root.rglob("*.py")):
            for module in _imported_top_level_modules(module_path):
                normalized = module.lower().replace("-", "_")
                if normalized in stdlib or normalized in declared:
                    continue
                # ⚠️ **跳过相对导入的顶层名**（``from .tools import X``）
                #   —— 正则只匹配绝对导入，``from .x`` 的组是空 ⇒ 不会命中。
                # ⚠️ **跳过服务自己的包名**
                if normalized in {service.replace("-", "_"), "app"}:
                    continue
                offenders.append(
                    f"{module_path.relative_to(ROOT)}: import {module}"
                    f" ⇒ backend/requirements.txt 里没声明"
                )

    assert not offenders, (
        "跨服务代码用到的第三方包**必须**在 backend/requirements.txt 里声明：\n"
        + "\n".join(f"  - {o}" for o in sorted(set(offenders)))
        + "\n原因：后端测试会 import 这些代码，"
        "而 CI 只装 backend/requirements.lock.txt ⇒ CI 会 ModuleNotFoundError。"
        "\n修法：在 backend/requirements.txt 里加声明，"
        "然后重新生成 backend/requirements.lock.txt。"
    )


def test_cross_service_tests_are_not_silently_skipped() -> None:
    """⚠️ 跨服务测试**不许**用 ``importorskip`` 把关键用例悄悄跳过。

    ⚠️ 允许的例外：``test_qa_tool_flow.py`` 用 ``importorskip`` 是因为
    那个文件测的是「QA 工具全链路」（在 agent-service 侧另有一份完整测试）。
    ⇒ 例外名单是**显式**的，不是「谁想跳就跳」。
    """
    # ⚠️ 显式白名单 —— 新增跨服务测试**默认不许** skip，
    #    要 skip 就得先在这里登记（⇒ 变成一次有意识的决定）。
    allowed_to_skip: set[str] = {
        # QA 工具全链路：agent-service 侧有完整覆盖，后端这份是补充。
        "test_qa_tool_flow.py",
    }

    offenders: list[str] = []
    for service, filenames in _backend_tests_importing_cross_service().items():
        for filename in filenames:
            if filename in allowed_to_skip:
                continue
            # ⚠️ **本文件自己排除**——
            #   它提到「agent-service」这些服务名（要在文档里解释清楚），
            #   也提到「importorskip」这个词 ⇒ 否则会**自己告自己**。
            if filename == Path(__file__).name:
                continue
            path = ROOT / "backend" / "app" / "tests" / filename
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "importorskip" in text:
                offenders.append(
                    f"{filename}（跨服务 import {service}）用了 importorskip"
                )

    assert not offenders, (
        "跨服务测试**不许**用 importorskip 静默跳过：\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n原因：跳过了就等于「CI 里这组用例根本没跑」，"
        "而其中包含安全相关的鉴权回归。\n"
        "修法：把缺的依赖声明进 backend/requirements.txt；"
        "确实需要跳过就登记到 allowed_to_skip 里（要经过一次有意识的决定）。"
    )


def _version_tuple(text: str) -> tuple[int, ...]:
    """把版本串转成可比较的元组。

    ⚠️ **只取数字段** —— ``1.2.3rc1`` → ``(1, 2, 3)``。
    ⚠️ 非数字后缀（如 ``.post1``）直接丢掉 —— 对下界比较够用了。
    """
    parts: list[int] = []
    for chunk in re.split(r"[.\-+]", text):
        digits = re.match(r"^(\d+)", chunk)
        if digits:
            parts.append(int(digits.group(1)))
        else:
            break
    return tuple(parts)
