"""清理 BUG-R6-47：历史遗留的脏 / 重复同步批次。

## 背景

真实库里累积了两类**历史脏批次**：

### 1. 「超时失败 + 紧接着的成功」对（旧代码遗留）

2026-11-16 之前，批次在多实例部署下有两条路径：

- 进程被 kill → 批次永远停在 ``running``；
- 下次调度前 ``recover_stale_batches()`` 把它标成 ``failed``，
  错误信息写「同步超时未完成（实例异常退出或重启）」；
- 紧接着的**重试**成功 → 又落一条 ``ok`` 批次。

于是每次异常留下**一对**批次：一条 ``failed``（超时）+ 一条 ``ok``（重试成功）。
真实库里有 **6 对**这样的记录 —— 它们**不是重复数据**，
而是「一次异常 + 一次成功重试」的历史痕迹。

### 2. 真的卡住的 ``running`` 批次

实例在同步中被 kill 且**再没被恢复**（比如进程重启后没跑到自愈逻辑），
批次永远停在 ``running``。真实库里有一条从 2026-11-17 02:59 起
就卡在 ``running`` 的批次 —— 它会一直让看板显示「同步中」。

## 清理策略（保守）

⚠️ **不删数据，只做状态归位**：

| 脏批次类型 | 处理 |
|---|---|
| 超时失败，且**同批次键下已有更晚的成功批次** | 标成 ``superseded``（已被取代） |
| 卡在 ``running`` 且**已超过超时阈值** | 标成 ``failed``（超时未完成） |
| 其它（真正失败、没有后继成功） | **不动** —— 那是真实的失败历史，要留痕 |

⚠️ 为什么**不物理删除**：
批次是审计链的一环（``organization_sync_audit.batch_id`` /
``organization_sync_item_error.batch_id`` / ``organization_sync_cursor.last_batch_id``
都指向它）。删掉会留下悬空外键，审计记录也断链。

新增状态 ``superseded``（已被取代）而不是复用 ``failed`` ——
这样看板能区分「真失败」和「被成功重试取代的历史异常」。

⚠️ **默认是演练模式**，必须显式加 ``--apply`` 才写库。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 卡住多久算「超时」（与服务层 ``recover_stale_batches`` 默认值一致）。
DEFAULT_STALE_MINUTES = 30

#: 判定「超时失败」的错误信息特征（与服务层写死的那句一致）。
STALE_TIMEOUT_MARKER = "同步超时未完成"

#: 「已被取代」状态：这条异常批次后面有一条成功的同键批次。
STATUS_SUPERSEDED = "superseded"

#: 超时未完成状态。
STATUS_FAILED = "failed"


def _parse_dt(value: str | None) -> datetime | None:
    """解析 SQLite 里存的时间串（``YYYY-MM-DD HH:MM:SS.ffffff``）。"""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清理 BUG-R6-47 历史遗留的脏 / 重复同步批次"
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / "backend" / "data" / "perf.db"),
        help="SQLite 数据库路径",
    )
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=DEFAULT_STALE_MINUTES,
        help=f"running 批次超过多少分钟算卡住（默认 {DEFAULT_STALE_MINUTES}）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库（不加此参数只做演练）",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ 数据库不存在：{db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        print(f"数据库：{db_path}")
        print("")

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "organization_sync_batch" not in tables:
            print("❌ 缺少 organization_sync_batch 表 —— 请先跑迁移")
            return 1

        now = datetime.now()
        threshold = now - timedelta(minutes=args.stale_minutes)

        rows = conn.execute(
            """
            SELECT id, batch_no, status, mode, trigger_type, triggered_by,
                   started_at, finished_at, error_message
            FROM organization_sync_batch
            ORDER BY started_at
            """
        ).fetchall()

        # ── 找出「超时失败」且「其后有成功批次」的批次 ──
        #    同键 = 同一 (mode, trigger_type, triggered_by, 日期)。
        #    这些 failed 是「一次异常」，后面的 ok 是「重试成功」。
        #
        #    ⚠️ 同一键下可能有多条成功批次（重试了好几次才成功）。
        #    对每条 failed，取**它之后最早的那条**成功批次 ——
        #    那才是「紧接着的重试」，语义最贴。
        success_by_key: dict[tuple, list[tuple[datetime, str]]] = {}
        for row in rows:
            (
                batch_id,
                _no,
                status,
                mode,
                trigger_type,
                triggered_by,
                started_at,
                _finished_at,
                _err,
            ) = row
            if status != "ok":
                continue
            started = _parse_dt(started_at)
            if started is None:
                continue
            key = (mode, trigger_type, triggered_by, started.strftime("%Y-%m-%d"))
            success_by_key.setdefault(key, []).append((started, batch_id))
        for entries in success_by_key.values():
            entries.sort()

        updates: list[tuple[str, str, str]] = []  # (new_status, new_msg, batch_id)

        print(f"=== 批次现状（共 {len(rows)} 条）===")
        superseded_count = 0
        stale_running_count = 0

        for row in rows:
            (
                batch_id,
                batch_no,
                status,
                mode,
                trigger_type,
                triggered_by,
                started_at,
                finished_at,
                error_message,
            ) = row

            if status in (STATUS_SUPERSEDED,):
                continue  # 已清理过

            started = _parse_dt(started_at)

            # ── 情况 1：卡在 running 且已超时 ──
            if status == "running":
                if started is not None and started < threshold:
                    new_msg = (
                        "同步超时未完成（实例异常退出或重启），"
                        "由 BUG-R6-47 清理脚本标记"
                    )
                    updates.append((STATUS_FAILED, new_msg, batch_id))
                    stale_running_count += 1
                    print(
                        f"  [超时 running] {batch_no} "
                        f"{_fmt(started)} → {STATUS_FAILED}"
                    )
                else:
                    print(
                        f"  [保留 running] {batch_no} "
                        f"{_fmt(started)}（未超过 {args.stale_minutes} 分钟）"
                    )
                continue

            # ── 情况 2：超时失败，且其后有成功批次 ──
            if status == STATUS_FAILED and error_message and (
                STALE_TIMEOUT_MARKER in error_message
            ):
                key = (
                    mode,
                    trigger_type,
                    triggered_by,
                    started.strftime("%Y-%m-%d") if started else "",
                )
                # 取「该 failed 之后最早的那条成功批次」
                successor = None
                if started is not None:
                    for success_at, _success_id in success_by_key.get(key, []):
                        if success_at > started:
                            successor = success_at
                            break
                if successor is not None:
                    new_msg = (
                        "同步超时未完成，已被后续成功批次取代"
                        f"（{_fmt(successor)}）"
                    )
                    updates.append((STATUS_SUPERSEDED, new_msg, batch_id))
                    superseded_count += 1
                    print(
                        f"  [被取代]     {batch_no} "
                        f"{_fmt(started)} → {STATUS_SUPERSEDED}"
                        f"（后继成功 {_fmt(successor)}）"
                    )
                    continue

            # ── 其余一律保留 ──
            if status in (STATUS_FAILED, "partial_failed"):
                print(f"  [保留失败]   {batch_no} {_fmt(started)} {status}")

        print("")
        if not updates:
            print("OK 无需清理（没有历史脏批次）")
            return 0

        print(
            f"共需清理 {len(updates)} 条"
            f"（被取代 {superseded_count} 条、超时 running {stale_running_count} 条）"
        )

        if not args.apply:
            print("（演练模式，未写库。加 --apply 实际执行）")
            return 0

        conn.executemany(
            "UPDATE organization_sync_batch "
            "SET status = ?, error_message = ? WHERE id = ?",
            updates,
        )
        conn.commit()

        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM organization_sync_batch GROUP BY status"
        ).fetchall()
        print("OK 已写库。清理后各状态批次数：")
        for status, count in sorted(by_status):
            print(f"  {status:16s} {count}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
