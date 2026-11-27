#!/usr/bin/env python
"""手动跑一轮每日填报提醒推送。

⚠️ **这个脚本会真的给员工发飞书消息** —— 不是干跑。

为什么要单独一个脚本
--------------------
推送是**主动打扰用户**的操作，默认是关闭的
（``FEISHU_DAILY_PUSH_ENABLED=false``）⇒ 平时不会自己响。

但「配好了到底长什么样」只有真发一次才知道 ⇒ 需要一个
**显式、可控**的触发口，而不是去改 ``.env`` 再重启。

⚠️ **两道防误触**
1. 必须显式传 ``--day``（或 ``--today``）—— 不会「不小心」就发；
2. 必须显式传 ``--confirm`` —— 没有它就只打印**将要发什么**，不发。

用法::

    # ① 先看会发什么（不发消息）
    python backend/scripts/run_daily_push.py --day 2026-11-24

    # ② 确认后再真发
    python backend/scripts/run_daily_push.py --day 2026-11-24 --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
# ⚠️ **引导到仓库根 + 用 ``backend.app.*``** —— 见
# ``seed_department_workload.py`` 里同一问题的说明：
# 顶层 ``app`` 会被 agent-service 的 ``app`` 包抢占。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> int:
    parser = argparse.ArgumentParser(description="手动执行每日填报提醒推送")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--day", help="推送哪一天（YYYY-MM-DD）")
    group.add_argument("--today", action="store_true", help="推送今天")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="⚠️ 真正发送；不加这个只打印计划",
    )
    args = parser.parse_args()

    day = date.today().isoformat() if args.today else args.day
    if not day:
        parser.error("请指定 --day 或 --today")

    from backend.app.core.config import settings
    from backend.app.core.db import get_sessionmaker
    from backend.app.services import pan_workload_policy as policy
    from backend.app.services.feishu_daily_metric_push import editors_for_push
    from backend.app.services.feishu_daily_push import leaders_of

    print("=" * 68)
    print(f"每日填报提醒推送 · {day}")
    print("=" * 68)

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        print("❌ 飞书凭据未配置 ⇒ 无法发送")
        return 1

    db = get_sessionmaker()()
    try:
        editors = editors_for_push(db, day)
        leaders: dict[str, object] = {}
        for emp in editors:
            metrics = policy.reporter_metric_codes(db, emp)
            for leader in leaders_of(db, emp):
                leaders[leader.id] = leader

        print("\n将要发送：")
        for emp in editors:
            metrics = policy.reporter_metric_codes(db, emp)
            print(f"  📤 {emp.name}（{emp.id}）指标={metrics or '(无 ⇒ 会跳过)'}")
        for emp in leaders.values():
            print(f"  📋 主管汇总 → {emp.name}（{emp.id}）")

        if not editors:
            print("\nℹ️  没有需要提醒的人（可能都已填完）")
            return 0

        if not args.confirm:
            print("\n⚠️  这是**演练**（未加 --confirm）⇒ 没有发送任何消息。")
            print("   确认无误后加 --confirm 真正发送。")
            return 0

        print("\n▶ 正在发送…")
        from backend.app.services.feishu_daily_push import push_daily_metrics

        result = push_daily_metrics(db, day)
        db.commit()
        print("\n结果：")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        db.rollback()
        print(f"❌ 推送失败：{exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
