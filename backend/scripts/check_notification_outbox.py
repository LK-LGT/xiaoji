#!/usr/bin/env python
"""查看通知发件箱积压（只读，不发送）。

用法::

    python backend/scripts/check_notification_outbox.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, select  # noqa: E402

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.domain.models import Employee  # noqa: E402
from backend.app.domain.notification_models import Notification  # noqa: E402


def main() -> int:
    session = get_sessionmaker()()
    try:
        total = session.execute(select(func.count()).select_from(Notification)).scalar_one()
        print(f"通知总数: {total}")
        rows = session.execute(
            select(Notification.status, func.count())
            .group_by(Notification.status)
            .order_by(func.count().desc())
        ).all()
        print("按状态:")
        for status, count in rows:
            print(f"  {status}: {count}")

        pending = (
            session.execute(
                select(Notification)
                .where(Notification.status == "pending")
                .order_by(Notification.created_at)
                .limit(10)
            )
            .scalars()
            .all()
        )
        print(f"\n最早 pending（最多 10 条，共 {sum(1 for _ in pending)} 条展示）:")
        for row in pending:
            employee = session.get(Employee, row.employee_id)
            print(
                f"  {row.created_at} {row.scene} {row.message_type} "
                f"→ {employee.name if employee else row.employee_id} | {row.title}"
            )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
