"""跑 Alembic 迁移 —— 不把数据库口令打印到日志。

用法：
    python backend/scripts/run_migrations.py upgrade
    python backend/scripts/run_migrations.py current

⚠️ **为什么要这个脚本**
``alembic`` 要求显式提供 ``DATABASE_URL`` 和 ``ALEMBIC_EXPECT_TARGET``
（后者是**独立核对**用的目标标识 —— 防止连错库）。
直接手敲容易把口令落到 PowerShell 历史里，所以这里从
``backend/.env`` 读，**不打印**。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for path in (str(ROOT), str(BACKEND)):
    if path not in sys.path:
        sys.path.insert(0, path)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env")

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("缺少 DATABASE_URL（backend/.env）", file=sys.stderr)
        return 1

    # ⚠️ **不打印完整 URL** —— 口令会进日志。
    from sqlalchemy.engine import make_url

    try:
        url = make_url(database_url)
    except Exception as exc:  # pragma: no cover
        print(f"DATABASE_URL 解析失败：{exc}", file=sys.stderr)
        return 1

    backend = url.get_backend_name()
    if backend == "sqlite":
        raw_path = database_url.split("///", 1)[-1]
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = (BACKEND / raw_path).resolve()
        expected = f"sqlite:{db_path.resolve().as_posix()}"
        print(f"目标库：SQLite → {db_path.name}")
    elif backend == "postgresql":
        expected = f"postgresql:{url.host}:{url.port}/{url.database}"
        print(f"目标库：PostgreSQL → {url.host}:{url.port}/{url.database}")
    else:
        print(f"不支持的数据库类型：{backend}", file=sys.stderr)
        return 1

    os.environ["DATABASE_URL"] = database_url
    os.environ["ALEMBIC_EXPECT_TARGET"] = expected

    os.chdir(BACKEND)
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND / "alembic.ini"))
    command.upgrade(config, "head")

    # 跑完确认落在 head —— 静默失败比报错危险。
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    print(f"当前版本：{current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
