"""真实网盘采集端到端验证（2026-11-21）。

⚠️ 只读网盘，但会**写入本地 perf.db**（pan_* 表）。
用法：python scripts/verify_pan_workload_real.py [--write]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.core.db import get_sessionmaker  # noqa: E402
from app.domain.models import Employee  # noqa: E402
from app.domain.pan_workload_models import (  # noqa: E402
    PanAccountBinding,
    PanWorkloadDaily,
    PanWorkloadFile,
)
from app.services.pan_workload_service import PanWorkloadService  # noqa: E402

#: 期望值 —— 来自探针脚本 ``probe_pan_rules_regression.py`` 的真实回归结果
EXPECTED = {
    "小瑶": {"files": 189, "count": 207, "days": 27},
    "小雨": {"files": 183, "count": 196, "days": 27},
}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yao", default="", help="小瑶对应的系统员工 ID（留空则用名字匹配）"
    )
    parser.add_argument(
        "--yu", default="", help="小雨对应的系统员工 ID（留空则用名字匹配）"
    )
    parser.add_argument(
        "--create-placeholder",
        action="store_true",
        help="找不到员工时创建占位员工（标注待映射）",
    )
    args = parser.parse_args()

    db = get_sessionmaker()()
    svc = PanWorkloadService(db)
    try:
        # ── 准备绑定（小瑶 / 小雨）──
        db.execute(delete(PanWorkloadDaily))
        db.execute(delete(PanWorkloadFile))
        db.execute(delete(PanAccountBinding))
        db.commit()

        employees = {e.name: e.id for e in db.scalars(select(Employee)).all()}

        def resolve(name: str, explicit: str) -> str | None:
            """按名字或显式 ID 找员工；找不到时按需建占位员工。"""
            if explicit:
                return explicit
            hit = next((v for k, v in employees.items() if name in k), None)
            if hit:
                return hit
            if not args.create_placeholder:
                return None
            placeholder = Employee(
                id=f"pan_placeholder_{name}", name=f"{name}（网盘目录，待映射）"
            )
            db.add(placeholder)
            db.commit()
            employees[placeholder.name] = placeholder.id
            return placeholder.id

        yao = resolve("小瑶", args.yao)
        yu = resolve("小雨", args.yu)
        if not yao or not yu:
            print("⚠️ 库里找不到「小瑶」「小雨」员工")
            print("   ⚠️ 这是**真实的映射缺口** —— 网盘目录名不等于系统员工名。")
            print("   请用 --yao / --yu 指定系统员工 ID，")
            print("   或加 --create-placeholder 先建占位员工（后续在界面上改绑）。")
            return 1
        print(f"绑定：小瑶={yao}  小雨={yu}")

        db.add_all([
            PanAccountBinding(
                employee_id=yao, folder_path="/成品-小瑶", folder_name="小瑶"
            ),
            PanAccountBinding(
                employee_id=yu, folder_path="/成品-小雨", folder_name="小雨"
            ),
        ])
        db.commit()

        # ── 跑采集 ──
        print("\n=== 采集 ===")
        result = svc.collect(trigger_type="manual")
        db.commit()
        for k, v in result.items():
            if k == "bindings":
                print(f"  {k}:")
                for b in v:
                    print(f"    {b}")
            else:
                print(f"  {k}: {v}")

        # ── 核对 ──
        print("\n=== 核对（期望值来自真实探针回归）===")
        ok = True
        for name, emp_id in (("小瑶", yao), ("小雨", yu)):
            files = db.scalars(
                select(PanWorkloadFile).where(
                    PanWorkloadFile.employee_id == emp_id
                )
            ).all()
            dailies = db.scalars(
                select(PanWorkloadDaily).where(
                    PanWorkloadDaily.employee_id == emp_id
                )
            ).all()
            total = sum(d.total_count for d in dailies)
            exp = EXPECTED[name]
            counted_files = [f for f in files if f.counted]
            actual = {
                "files": len(counted_files),
                "count": total,
                "days": len(dailies),
            }
            match = actual == exp
            ok = ok and match
            print(f"\n  【{name}】")
            print(f"    期望 {exp}")
            print(f"    实际 {actual}  {'✅' if match else '❌'}")

            # 排除项统计
            excluded = [f for f in files if not f.counted]
            by_reason: dict[str, int] = {}
            for f in excluded:
                key = f.exclude_reason.split(":")[0]
                by_reason[key] = by_reason.get(key, 0) + 1
            print(f"    落库文件总数 {len(files)}（计入 {len(counted_files)} / 排除 {len(excluded)}）")
            print(f"    排除明细 {by_reason}")

        # 异常与标记
        print("\n=== 异常检测 ===")
        anomalies = svc.detect_anomalies()
        print(f"  命中 {len(anomalies)} 条")
        for a in anomalies[:8]:
            print(f"    {a['employee_id']} {a['business_date']} {a['message']}")

        # 每日明细抽样
        print("\n=== 小瑶每日条数（前 12 天）===")
        rows = db.scalars(
            select(PanWorkloadDaily)
            .where(PanWorkloadDaily.employee_id == yao)
            .order_by(PanWorkloadDaily.business_date)
        ).all()
        line = [f"{r.business_date.strftime('%m-%d')}={r.total_count}" for r in rows[:12]]
        print("  " + "  ".join(line))
        line = [f"{r.business_date.strftime('%m-%d')}={r.total_count}" for r in rows[12:]]
        if line:
            print("  " + "  ".join(line))

        print("\n=== 结论 ===")
        print("  ✅ 与真实探针完全一致" if ok else "  ❌ 与探针不一致，需排查")
        return 0 if ok else 1
    finally:
        svc.close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
