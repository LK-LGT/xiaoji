#!/usr/bin/env python
"""每日填报提醒推送 —— **配置体检**（不发送任何消息）。

⚠️ **为什么要有这个脚本**
推送是**主动打扰用户**的操作：配错了会天天刷屏，
配漏了又以为「系统没生效」。而推送是否生效取决于**四层**条件，
只看 ``.env`` 是看不出来全貌的：

| 层 | 位置 | 决定什么 |
|---|---|---|
| ① 开关 | ``.env`` 的 ``FEISHU_DAILY_PUSH_ENABLED`` | 调度器起不起来 |
| ② 凭据 | ``.env`` 的 ``FEISHU_APP_ID/SECRET`` | 能不能发出去 |
| ③ 板块 | ``department_module`` 表 | **哪个部门**收得到 |
| ④ 口径 | ``pan_workload_policy`` 表 | **哪个岗位**收得到、填什么指标 |

⇒ 这个脚本把四层**一次看全**，并**干跑**出「今天会推给谁」。

用法::

    python backend/scripts/check_daily_push_config.py

⚠️ **绝不发送消息** —— 只读 + 干跑。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.core.config import settings  # noqa: E402
from backend.app.core.db import get_sessionmaker  # noqa: E402


def _ok(flag: bool) -> str:
    return "✅" if flag else "❌"


def main() -> int:
    print("=" * 68)
    print("每日填报提醒推送 · 配置体检（不发送任何消息）")
    print("=" * 68)

    problems: list[str] = []
    warnings: list[str] = []

    # ── ① 开关 ────────────────────────────────────────────────────────
    print("\n【① 开关】（只管「自动定时推」）")
    enabled = settings.feishu_daily_push_enabled
    if enabled:
        print(f"  ✅ FEISHU_DAILY_PUSH_ENABLED = {enabled} ⇒ 定时自动推送已开启")
    else:
        print(f"  ⏸️ FEISHU_DAILY_PUSH_ENABLED = {enabled} ⇒ **定时自动推送未开启**")
        # ⚠️ **这不是「配错了」** —— 开关关着时**手动触发照样能用**
        #    （``push_daily_metrics`` 只校验飞书凭据，不看这个开关）。
        #    ⇒ 只提示，不算「问题」。
        warnings.append(
            "定时自动推送未开启（开关是关的）—— 但**手动触发不受影响**，"
            "随时可以 run_daily_push.py --confirm"
        )
    print(f"  ℹ️  推送时刻 = {settings.feishu_daily_push_time}（服务器本地时间）")

    # ── ② 凭据 ────────────────────────────────────────────────────────
    print("\n【② 飞书凭据】")
    has_id = bool(settings.feishu_app_id)
    has_secret = bool(settings.feishu_app_secret)
    print(f"  {_ok(has_id)} FEISHU_APP_ID 已配置")
    print(f"  {_ok(has_secret)} FEISHU_APP_SECRET 已配置")
    print(f"  {_ok(bool(settings.feishu_tenant_id))} FEISHU_TENANT_ID 已配置")
    if not (has_id and has_secret):
        problems.append("飞书凭据不完整 ⇒ 推送会静默跳过（not_configured）")

    # ── ③④ 板块 + 口径（真实库） ──────────────────────────────────────
    print("\n【③ 板块 / ④ 口径】")
    db = get_sessionmaker()()
    try:
        from sqlalchemy import select

        from backend.app.domain.pan_workload_models import (
            DepartmentModule,
            PanFeishuMetricSource,
            PanWorkloadPolicy,
        )
        from backend.app.services import pan_workload_policy as policy
        from backend.app.services.department_modules import PAN_WORKLOAD_MODULE
        from backend.app.services.feishu_daily_metric_push import editors_for_push
        from backend.app.services.feishu_daily_push import leaders_of

        # 板块开通情况
        mods = db.execute(
            select(DepartmentModule).where(
                DepartmentModule.module_code == PAN_WORKLOAD_MODULE,
                DepartmentModule.active.is_(True),
            )
        ).scalars().all()
        if not mods:
            print(f"  ❌ 没有任何部门开通「{PAN_WORKLOAD_MODULE}」板块 ⇒ 没人会收到")
            problems.append("没有部门开通 pan_workload 板块")
        for m in mods:
            print(f"  ✅ 板块开通：{m.department_id}（{PAN_WORKLOAD_MODULE}）")

        # 岗位口径
        print("\n  岗位口径（pan_workload_policy）：")
        pols = db.execute(
            select(PanWorkloadPolicy).where(PanWorkloadPolicy.active.is_(True))
        ).scalars().all()
        if not pols:
            print("    ❌ 没有任何岗位口径 ⇒ 没人会收到")
            problems.append("pan_workload_policy 表为空")
        for p in pols:
            who = "要填报(收推送)" if p.role == "reporter" else "只核对(收汇总)"
            print(f"    · 部门={p.department_id} 岗位={p.position} → {who}"
                  f" 指标={p.metric_codes or '(无)'}")

        # ── 干跑：今天会推给谁 ────────────────────────────────────────
        print("\n【干跑】今天会推给谁：")
        today = date.today().isoformat()
        editors = editors_for_push(db, today)
        if not editors:
            print("  ℹ️  今天没有需要提醒的剪辑（可能都已填完，或口径不匹配）")
        leaders: dict[str, object] = {}
        for emp in editors:
            metrics = policy.reporter_metric_codes(db, emp)
            print(f"  📤 剪辑：{emp.name}（{emp.id}）岗位={emp.position} 指标={metrics or '(无)'}")
            if not metrics:
                problems.append(f"{emp.name} 没有配置指标口径 ⇒ 会被跳过")
            for leader in leaders_of(db, emp):
                leaders[leader.id] = leader
        for emp in leaders.values():
            print(f"  📋 主管汇总：{emp.name}（{emp.id}）岗位={emp.position}")

        # 表格绑定（可选）
        srcs = db.execute(
            select(PanFeishuMetricSource).where(PanFeishuMetricSource.active.is_(True))
        ).scalars().all()
        print(f"\n  ℹ️  飞书表格绑定 = {len(srcs)} 条"
              f"（没有也能推，卡片会省略表格链接）")
    finally:
        db.close()

    # ── 结论 ──────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    if problems:
        print("结论：❌ 有问题需要处理")
        for p in problems:
            print(f"  · {p}")
    else:
        print("结论：✅ 配置齐全，推送可以正常发出")
        if settings.feishu_daily_push_enabled:
            print("  ℹ️  定时自动推送已开启 ⇒ 到点会自动推")
        else:
            print("  ℹ️  定时自动推送**未开启**，但**手动触发随时可用**")
    for w in warnings:
        print(f"  ⚠️  {w}")
    if not problems:
        print("\n  ℹ️  想先手动推一轮看效果（注意：会真的发消息）：")
        print("      python backend/scripts/run_daily_push.py --day 2026-11-24 --confirm")
    print("=" * 68)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
