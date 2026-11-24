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
