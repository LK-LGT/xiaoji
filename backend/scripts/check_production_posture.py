"""🔍 生产部署配置**核对**（2026-11-24，B4）。

⚠️ **这个脚本解决什么问题**
B1 交付了三个开关（``PASSWORD_LOGIN_ENABLED`` / ``TEST_USERS_ENABLED`` /
``TEST_USER_PASSWORDS``），**代码默认值就是安全的**（全关）。
但部署环境若已有 ``.env``，可能**手动**打开过其中之一
⇒ 一次误配就能让「生产只走飞书 SSO」的规则失效。

⇒ 与其靠人记得核对，不如**让脚本核对**：
- CI 上跑 ⇒ 配错就红；
- 部署时跑一次 ⇒ 输出即证据。

用法：
    python backend/scripts/check_production_posture.py [--env <路径>]

⚠️ **只读** —— 本脚本**不修改**任何配置，只报告。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 必须为「关」的开关 → 为什么
REQUIRED_OFF = {
    "PASSWORD_LOGIN_ENABLED": (
        "口令登录总闸门 —— 生产只走飞书 SSO；打开 ⇒ 任意口令可尝试登录"
    ),
    "TEST_USERS_ENABLED": (
        "测试账号开关 —— 生产不应有测试账号"
    ),
}

#: 生产**不得**出现的变量
MUST_BE_ABSENT = {
    "TEST_USER_PASSWORDS": (
        "测试账号口令 —— 生产出现它 ⇒ 拿到即可登录真实员工"
    ),
}


def _read_env_file(path: Path) -> dict[str, str]:
    """读 ``.env``（只解析 ``KEY=VALUE``，不求值）。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _is_on(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    parser = argparse.ArgumentParser(description="核对生产部署的登录入口配置")
    parser.add_argument(
        "--env",
        default=str(ROOT / "backend" / ".env"),
        help="要核对的 .env 路径（默认 backend/.env）",
    )
    args = parser.parse_args()
    env_path = Path(args.env)

    file_values = _read_env_file(env_path)

    # ⚠️ **实际生效值** = 环境变量 > .env 文件 > 代码默认值
    def effective(key: str) -> str:
        if key in os.environ:
            return os.environ[key]
        return file_values.get(key, "")

    print("=" * 72)
    print("生产姿态核对 —— 登录入口")
    print("=" * 72)
    print(f"  .env 路径    : {env_path}")
    print(f"  .env 是否存在: {'是' if env_path.is_file() else '否（用代码默认值）'}")
    print()

    ok = True

    print("【必须为『关』的开关】")
    for key, why in REQUIRED_OFF.items():
        raw = effective(key)
        source = (
            "环境变量"
            if key in os.environ
            else (".env" if key in file_values else "代码默认值")
        )
        on = _is_on(raw)
        if on:
            print(f"  ❌ {key} = {raw!r}（来源：{source}）")
            print(f"       ⇒ {why}")
            ok = False
        else:
            shown = raw if raw else "(未设置 → 默认关)"
            print(f"  ✅ {key} = {shown}（来源：{source}）")
    print()

    print("【生产不得出现的变量】")
    for key, why in MUST_BE_ABSENT.items():
        raw = effective(key)
        if raw:
            print(f"  ❌ {key} 已配置（来源：{'环境变量' if key in os.environ else '.env'}）")
            print(f"       ⇒ {why}")
            ok = False
        else:
            print(f"  ✅ {key} 未配置")
    print()

    print("【结论】")
    if ok:
        print("  ✅ 生产姿态正确 —— 口令登录关闭、无测试账号 ⇒ 只走飞书 SSO")
        print()
        print("  提示：口令登录被**显式关闭**时，`POST /v1/auth/login` 会返回")
        print("        503 `PASSWORD_LOGIN_DISABLED` ⇒ 这是**预期行为**，不是故障。")
        return 0
    print("  ❌ 存在不合规配置 ⇒ 见上面标 ❌ 的项")
    print()
    print("  修法：把对应变量设为 false / 删除该行，然后重启服务。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
