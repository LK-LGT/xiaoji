"""规则引擎真实数据回归（2026-11-21）—— 修正版。

用真实网盘的 526 个文件跑一遍规则引擎，输出两人各自的每日条数，
验证：**只数 mp4**、**智能体/OOTD 算 2 条**、**目录归属**、**目录归日**。

⚠️ 只读诊断脚本，不改任何数据。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import get_sessionmaker  # noqa: E402
from app.services.baidu_pan_service import BaiduPanService  # noqa: E402
from app.services.pan_workload_rules import (  # noqa: E402
    DEFAULT_MARKER_RULES,
    judge_file,
    resolve_owner,
)

BINDINGS = [
    {"folder_path": "/成品-小瑶", "employee_id": "小瑶", "active": True},
    {"folder_path": "/成品-小雨", "employee_id": "小雨", "active": True},
]


def main() -> int:
    db = get_sessionmaker()()
    service = BaiduPanService(db)
    try:
        token = service.ensure_access_token()
        client = service.client

        all_files = []
        for top in ("/成品-小瑶", "/成品-小雨"):
            all_files.extend(client.list_files_recursive(token, top, limit=1000))
        print(f"扫到文件数 = {len(all_files)}\n")

        per_person: dict[str, list] = defaultdict(list)
        exclude_counter: dict[str, int] = defaultdict(int)
        anomaly_files: dict[str, list] = defaultdict(list)
        unbound: list[str] = []

        for f in all_files:
            emp, kind, _ = resolve_owner(f.path, BINDINGS)
            if kind == "unbound":
                unbound.append(f.path)
            v = judge_file(f, rules={"default": DEFAULT_MARKER_RULES})
            if not v.counted:
                exclude_counter[v.exclude_reason.split(":")[0]] += 1
                continue
            for a in v.anomaly_flags:
                anomaly_files[a].append((f, v))
            per_person[emp or f"({kind})"].append((f, v))

        # ── 汇总 ──
        print("=" * 72)
        print("汇总")
        print("=" * 72)
        for emp, items in sorted(per_person.items()):
            total = sum(v.count_weight for _, v in items)
            marker_files = [(f, v) for f, v in items if v.markers.names]
            plain = len(items) - len(marker_files)
            print(
                f"\n【{emp}】计入文件 {len(items)} 个 → 总条数 {total}"
                f"（普通 {plain}×1 + 标记 {len(marker_files)}×2）"
            )

        # 每日明细
        for emp, items in sorted(per_person.items()):
            daily = defaultdict(int)
            for f, v in items:
                daily[v.business_date or "(未知)"] += v.count_weight
            print(f"\n【{emp}】每日条数（共 {len(daily)} 天）")
            line = []
            for day in sorted(daily):
                line.append(f"{day[5:]}={daily[day]}")
            for i in range(0, len(line), 8):
                print("   " + "  ".join(line[i:i + 8]))

        # ── 排除与异常 ──
        print("\n" + "=" * 72)
        print("排除统计")
        print("=" * 72)
        for reason, cnt in sorted(exclude_counter.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:<28} {cnt} 个")

        print("\n异常标记明细")
        for a, files in sorted(anomaly_files.items()):
            print(f"\n  ▸ {a}  ({len(files)} 个)")
            for f, v in files:
                print(f"      {f.path}")
                print(f"        归日={v.business_date}  权重={v.count_weight}")

        if unbound:
            print(f"\n未归属文件 {len(unbound)} 个:")
            for p in unbound[:10]:
                print(f"  {p}")

        # ── 重点复核：OOTD 命中的文件名是否真的是 OOTD ──
        print("\n" + "=" * 72)
        print("★ 复核：所有命中 OOTD 的文件名")
        print("=" * 72)
        for emp, items in sorted(per_person.items()):
            for f, v in items:
                if "OOTD" in v.markers.names:
                    print(f"  [{emp}] {f.name}")
    finally:
        service.close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
