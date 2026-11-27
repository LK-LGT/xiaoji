"""运维工具：为指定员工重置登录口令（R4 密码体系下的账号找回入口）。

为什么需要它
------------
R4 落地后，所有账号都改成了 PBKDF2 哈希存储，明文不可逆。
但**管理员自己也可能忘记口令** —— 这时「管理员重置他人密码」
的接口（``POST /v1/system/users/{id}/reset-password``）本身就需要管理员登录，
就成了死锁。

本脚本是**服务端本地运维入口**（不走 HTTP，因此不需要已登录态），
用于：
- 账号找回（管理员自己忘记口令）
- 初始化 / 重置演示账号口令

它复用 ``password_hasher.hash_password``，与登录、改密接口**同一套**算法与强度规则，
不会出现「脚本能设、登录接口认不出」的分裂。

用法
----
    python backend/scripts/reset_password.py <employee_id> <new_password>
    python backend/scripts/reset_password.py 2aaf32ea            # 随机口令
    python backend/scripts/reset_password.py --list                # 列出账号

安全说明
--------
- 口令只以 PBKDF2 哈希落库，明文**只在终端回显一次**；
- 重置后 ``force_change_password=True`` → 该用户下次登录必须改密；
- ``token_version`` 自增 → 该用户已签发的所有 Token 立即失效。
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ⚠️ 必须在 import backend.app.core.* **之前**切换 CWD：
# ``settings.database_url`` 是相对路径（``sqlite:///./data/perf.db``），
# 相对的是进程 CWD。服务端以 ``backend/`` 为 CWD 启动，
# 脚本必须对齐，否则就会改到**另一个库**上（详见 _resolve_db_path 注释）。
os.chdir(BACKEND_DIR)

# ⚠️ 用 ``backend.app.*`` 全路径 —— 顶层 ``app`` 会被 agent-service 的
# ``app`` 包抢占（仓库根 conftest 会把 agent-service 放进 sys.path）。
from backend.app.core.config import settings  # noqa: E402
from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.domain.models import Employee  # noqa: E402
from backend.app.services import password_hasher  # noqa: E402


def _resolve_db_path() -> Path:
    """把 ``settings.database_url`` 解析成**绝对路径**。

    ⚠️ 关键点：``database_url`` 默认是 ``sqlite:///./data/perf.db`` —— **相对路径**。
    相对谁？相对**进程当前工作目录（CWD）**。
    服务端启动时 CWD = ``backend/``，于是它用的是 ``backend/data/perf.db``；
    而运维脚本如果在仓库根目录跑，CWD = 仓库根，
    同样的 URL 会解析到 ``<repo>/data/perf.db`` —— **另一个库**！

    后果：脚本"重置了口令"，服务端却纹丝不动 → 登录永远 401，
    而脚本读起来一切正常（它改的是另一个文件）。

    现在统一：**始终以 BACKEND_DIR 为基准**解析，
    与服务端启动目录保持一致，杜绝"改了一个库、服务用另一个库"。
    """
    raw = settings.database_url
    if not raw.startswith("sqlite"):
        raise SystemExit(f"暂不支持非 SQLite 数据库：{raw}")
    rel = raw.split("///", 1)[-1]
    if rel in ("", ":memory:"):
        raise SystemExit("内存数据库无法做口令重置")
    return (BACKEND_DIR / rel).resolve()


DB_PATH = _resolve_db_path()


def _session():
    """开一个数据库会话（与服务层用同一个 engine / sessionmaker）。"""
    return get_sessionmaker()()


# 去掉易混淆字符（0/O、1/l/I）
_PASSWORD_ALPHABET = (
    "".join(c for c in string.ascii_letters if c not in "OoIiLl")
    + string.digits.replace("0", "").replace("1", "")
    + "!@#$%^&*-_=+"
)


def generate_password(length: int = 16) -> str:
    """生成随机初始口令（满足 8–32 位长度约束）。"""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def list_accounts() -> int:
    """列出全部在职账号（不含口令明文）。"""
    db = _session()
    try:
        rows = (
            db.query(Employee)
            .order_by(Employee.name)
            .all()
        )
        print(f"{'员工ID':<18} {'姓名':<10} {'登录账号':<16} {'强制改密':<8} 口令状态")
        print("-" * 78)
        for emp in rows:
            hashed = "已设(PBKDF2)" if emp.password_hash else "未设(明文兼容)"
            print(
                f"{emp.id:<18} {emp.name:<10} "
                f"{(emp.login_account or '-'):<16} "
                f"{('是' if emp.force_change_password else '否'):<8} {hashed}"
            )
        return 0
    finally:
        db.close()


def reset_password(employee_id: str, new_password: str | None) -> int:
    """重置指定员工的口令。"""
    db = _session()
    try:
        emp = db.get(Employee, employee_id)
        if emp is None:
            print(f"✗ 员工不存在：{employee_id}", file=sys.stderr)
            return 1

        plain = new_password or generate_password()
        try:
            emp.password_hash = password_hasher.hash_password(plain)
        except password_hasher.PasswordStrengthError as exc:
            print(f"✗ 口令不满足强度要求：{exc}", file=sys.stderr)
            return 1

        emp.password_set_at = datetime.now()
        # 重置后强制改密：管理员只是"开锁"，不知道用户会怎么用它。
        emp.force_change_password = True
        # 吊销该用户所有已签发 Token。
        emp.token_version = int(emp.token_version or 1) + 1

        db.commit()
        db.refresh(emp)

        print(f"✓ 已重置口令：{emp.name}（{emp.id}）")
        print(f"  数据库：{DB_PATH}")
        print(f"  登录账号：{emp.login_account or employee_id}")
        print(f"  初始口令：{plain}")
        print("  ⚠ 明文只在此刻显示一次，请立即交给本人；对方登录后必须改密。")
        return 0
    except Exception as exc:  # pragma: no cover - 运维脚本兜底
        db.rollback()
        print(f"✗ 重置失败：{exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="运维：重置员工登录口令")
    parser.add_argument("employee_id", nargs="?", help="员工 ID")
    parser.add_argument("new_password", nargs="?", help="新口令（省略则随机生成）")
    parser.add_argument("--list", action="store_true", help="列出全部账号")
    args = parser.parse_args()

    if args.list:
        return list_accounts()
    if not args.employee_id:
        parser.print_help()
        return 1
    return reset_password(args.employee_id, args.new_password)


if __name__ == "__main__":
    raise SystemExit(main())
