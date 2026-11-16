"""生成并分发 agent-service 共享密钥（R12）。

⚠️ 这个脚本**会改写 backend/.env** —— 只在明确要轮换密钥时运行。

用法::

    python backend/scripts/rotate_agent_secret.py          # 生成并写入
    python backend/scripts/rotate_agent_secret.py --show   # 只打印，不写
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ENV = REPO_ROOT / "backend" / ".env"

# 32 字节 → 43 字符 base64url，无 padding。
# 足够长（256 位熵），且不含易混字符，复制粘贴不会出错。
SECRET_BYTES = 32


def generate_secret() -> str:
    """生成一个高熵、URL 安全、便于手工粘贴的令牌。"""
    return secrets.token_urlsafe(SECRET_BYTES)


def upsert_env(path: Path, key: str, value: str) -> bool:
    """把 ``key=value`` 写进 .env 文件。

    - key 不存在 → 追加到文件末尾（带注释说明）
    - key 已存在 → **原地替换**，保留其它内容与顺序
    - 文件不存在 → 创建

    Returns:
        True 表示确实改了文件。
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{key}={value}\n", encoding="utf-8")
        return True

    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        new_text = pattern.sub(f"{key}={value}", text, count=1)
    else:
        new_text = text.rstrip("\n") + f"\n{key}={value}\n"

    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="轮换 agent-service 共享密钥")
    parser.add_argument(
        "--show",
        action="store_true",
        help="只打印新令牌，不写文件",
    )
    parser.add_argument(
        "--value",
        help="指定令牌值（不指定则自动生成）",
    )
    args = parser.parse_args()

    secret = args.value or generate_secret()

    if args.show:
        print(secret)
        return 0

    changed = upsert_env(BACKEND_ENV, "AGENT_SHARED_SECRET", secret)
    print(f"backend/.env: {'已更新' if changed else '无变化'} AGENT_SHARED_SECRET")

    print()
    print("令牌值（agent-service 侧需配置同一个值）：")
    print(f"  {secret}")
    print()
    print("agent-service 启动方式（二选一）：")
    print("  1) 环境变量：")
    print(f'     $env:AGENT_SHARED_SECRET="{secret}"')
    print("  2) .env 文件：agent-service/.env 里写")
    print(f"     AGENT_SHARED_SECRET={secret}")
    print()
    print("⚠️ 轮换后两边必须**同时**重启，否则业务层会收到 401。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
