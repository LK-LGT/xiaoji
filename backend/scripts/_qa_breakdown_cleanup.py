"""清理评分构成 E2E 脚本留下的测试数据。

⚠️ **为什么要清理**
------------------
``_qa_breakdown_excluded_e2e.py`` 会往**真实库**里写一个
固定 ID 的任务（``T_BD`` / ``E_BD`` / ``S_BD`` / ``R_BD``）。

这些数据会出现在：
- 用户在 Web 上看到的考核任务列表
- 飞书任务选择卡片里

⇒ 属于**污染**，必须清掉。

本脚本**幂等**：先删再建 / 先删后不留痕，可重复运行。

用法::

    python backend/scripts/_qa_breakdown_cleanup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

TASK_ID = "T_BD"
EMP_ID = "E_BD"
SNAPSHOT_ID = "S_BD"
RECORD_ID = "R_BD"


def main() -> int:
    from backend.app.core.db import get_sessionmaker
    from backend.app.domain.models import (
        AssessmentRecord,
        AssessmentTask,
        ConfigSnapshot,
        Employee,
        MetricValue,
    )

    with get_sessionmaker()() as db:
        # ⚠️ 顺序有讲究 —— 先删子表，再删父表，否则可能撞外键
        deleted = {}

        vals = db.query(MetricValue).filter(
            MetricValue.employee_id == EMP_ID,
            MetricValue.period == "202611",
        ).delete(synchronize_session=False)
        deleted["metric_values"] = vals

        rec = db.query(AssessmentRecord).filter(
            AssessmentRecord.id == RECORD_ID,
        ).delete(synchronize_session=False)
        deleted["assessment_records"] = rec

        # ⚠️ **顺序要紧**：``assessment_task.snapshot_id`` 外键指向 ``config_snapshot``，
        # 所以必须**先删 task 再删 snapshot**，否则 FOREIGN KEY constraint failed。
        task = db.query(AssessmentTask).filter(
            AssessmentTask.id == TASK_ID,
        ).delete(synchronize_session=False)
        deleted["assessment_tasks"] = task

        snap = db.query(ConfigSnapshot).filter(
            ConfigSnapshot.id == SNAPSHOT_ID,
        ).delete(synchronize_session=False)
        deleted["config_snapshots"] = snap

        emp = db.query(Employee).filter(
            Employee.id == EMP_ID,
        ).delete(synchronize_session=False)
        deleted["employees"] = emp

        db.commit()

    print("清理完成：")
    for name, count in deleted.items():
        print(f"  {name}: {count} 行")

    # ── 复核：确认真的没留下 ────────────────────────────────
    with get_sessionmaker()() as db:
        left = (
            db.query(AssessmentTask).filter(AssessmentTask.id == TASK_ID).count()
            + db.query(Employee).filter(Employee.id == EMP_ID).count()
            + db.query(ConfigSnapshot).filter(ConfigSnapshot.id == SNAPSHOT_ID).count()
        )
    print(f"\n复核残留：{left} 条 {'✓ 干净' if left == 0 else '✗ 仍有残留'}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
