"""🔴 **生产部署姿态核对脚本**的护栏（2026-11-24，B4）。

⚠️ **为什么要测一个「读 .env 的脚本」**
``backend/scripts/check_production_posture.py`` 是 B4 的交付物：
部署时跑一次，确认「口令登录关闭、无测试账号」。
⇒ 脚本本身**必须可信** —— 否则它会给出「✅ 通过」的假安心。
⇒ 本文件锁住：
  ① 判定逻辑正确（真开 / 真关 / 未设置 三种情形）
  ② **敏感度** —— 脚本必须**能抓到**误配（否则等于没检查）
  ③ 它是**只读**的（不修改任何配置）
  ④ CI 里**真的在跑**（不然部署前那一道保险不存在）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "check_production_posture.py"


def _run(env: dict[str, str], env_file: Path | None = None) -> subprocess.CompletedProcess:
    """跑一次核对脚本（**不继承**当前环境变量，避免被测试环境影响）。"""
    import os

    child_env = {
        k: v for k, v in os.environ.items()
        # ⚠️ 清掉测试进程自己注入的这几个 —— 否则脚本永远只看到「开」
        if k not in (
            "PASSWORD_LOGIN_ENABLED",
            "TEST_USERS_ENABLED",
            "TEST_USER_PASSWORDS",
        )
    }
    child_env.update(env)
    cmd = [sys.executable, str(SCRIPT)]
    if env_file is not None:
        cmd += ["--env", str(env_file)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=child_env
    )


class TestProductionPostureCheck:
    """🔴 核对脚本本身的可信度。"""

    def test_脚本存在(self):
        assert SCRIPT.is_file(), f"找不到 {SCRIPT}"

    def test_干净环境下通过(self, tmp_path):
        """⚠️ 什么都没配 ⇒ 走代码默认值（全关）⇒ 必须通过。"""
        empty = tmp_path / ".env"
        empty.write_text("", encoding="utf-8")

        proc = _run({}, env_file=empty)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "生产姿态正确" in proc.stdout

    def test_抓到口令登录被打开(self, tmp_path):
        """🔴 **核心敏感度** —— 必须抓到。"""
        empty = tmp_path / ".env"
        empty.write_text("", encoding="utf-8")

        proc = _run({"PASSWORD_LOGIN_ENABLED": "true"}, env_file=empty)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "PASSWORD_LOGIN_ENABLED" in proc.stdout
        assert "存在不合规配置" in proc.stdout

    def test_抓到测试账号口令存在(self, tmp_path):
        """🔴 **核心敏感度** —— 生产出现口令就是泄露。"""
        empty = tmp_path / ".env"
        empty.write_text("", encoding="utf-8")

        proc = _run({"TEST_USER_PASSWORDS": '{"admin": "x"}'}, env_file=empty)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "TEST_USER_PASSWORDS" in proc.stdout

    def test_抓到env文件里配了开关(self, tmp_path):
        """🔴 **核心敏感度** —— 误配最常见的地方是 ``.env`` 文件本身。

        ⚠️ 环境变量优先级高于文件 ⇒ 上一条测的是环境变量；
        ⇒ 这一条测**文件**里的误配（部署时手改 .env 的场景）。
        """
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PASSWORD_LOGIN_ENABLED=true\nTEST_USER_PASSWORDS={\"admin\":\"x\"}\n",
            encoding="utf-8",
        )

        proc = _run({}, env_file=env_file)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "PASSWORD_LOGIN_ENABLED" in proc.stdout

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", ""])
    def test_各种关闭写法都算关(self, tmp_path, value: str):
        """⚠️ **别把 false 误判成开** —— 那会造成假警报。"""
        empty = tmp_path / ".env"
        empty.write_text("", encoding="utf-8")

        proc = _run({"PASSWORD_LOGIN_ENABLED": value}, env_file=empty)
        assert proc.returncode == 0, (
            f"{value!r} 应被当成『关』，却报了不合规：\n{proc.stdout}"
        )

    def test_不修改env文件(self, tmp_path):
        """⚠️ **只读** —— 核对脚本绝不能改配置。

        ⇒ 否则「跑一次核对」会顺手把生产配置改了。
        """
        env_file = tmp_path / ".env"
        original = "PASSWORD_LOGIN_ENABLED=true\n"
        env_file.write_text(original, encoding="utf-8")

        _run({}, env_file=env_file)

        assert env_file.read_text(encoding="utf-8") == original, (
            "核对脚本修改了 .env ⇒ 它必须是只读的"
        )

    def test_缺失的env文件不报错(self, tmp_path):
        """⚠️ 没有 ``.env`` 是正常情况（用代码默认值）⇒ 不该崩。"""
        proc = _run({}, env_file=tmp_path / "nonexistent.env")
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestPostureCheckWiredIntoCI:
    """🔴 核对脚本必须**真的被 CI 调用**，否则部署前没有那道保险。"""

    def test_ci里有这个核对步骤(self):
        """⚠️ **静态检查** CI 配置里有没有这一项。"""
        ci = ROOT / ".github" / "workflows" / "ci.yml"
        assert ci.is_file(), "找不到 CI 配置"
        text = ci.read_text(encoding="utf-8")
        assert "check_production_posture.py" in text, (
            "CI 里没有跑 check_production_posture.py ⇒ "
            "部署前的配置核对没有自动化保险"
        )

    def test_本地CI脚本里也有(self):
        """⚠️ 本地复现脚本也要有 —— 否则「本地全绿、CI 红」。"""
        local = ROOT / "backend" / "scripts" / "run_ci_locally.py"
        text = local.read_text(encoding="utf-8")
        assert "check_production_posture.py" in text, (
            "run_ci_locally.py 里没有这一项 ⇒ 本地不会复现 CI 的这道检查"
        )
