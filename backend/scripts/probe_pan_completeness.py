"""完整性对账 —— 精简版（2026-11-21）。

只输出关键结论：
  ① 每个顶层目录的 mp4 总数与条数
  ② 「成品-*」下的日期目录清单 + 缺失日期
  ③ 未绑定目录的规模
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import get_sessionmaker  # noqa: E402
from app.services.baidu_pan_service import BaiduPanService  # noqa: E402
from app.services.pan_workload_rules import (  # noqa: E402
    DEFAULT_MARKER_RULES,
    judge_file,
)

XPAN_BASE = "https://pan.baidu.com/rest/2.0/xpan"


class Pan:
    def __init__(self, token: str) -> None:
        self.token = token
        self.cache: dict[str, list[dict]] = {}

    def list(self, path: str) -> list[dict]:
        if path in self.cache:
            return self.cache[path]
        out: list[dict] = []
        start = 0
        while True:
            resp = httpx.get(
                f"{XPAN_BASE}/file",
                params={"method": "list", "access_token": self.token, "dir": path,
                        "order": "name", "desc": 0, "start": start,
                        "limit": 1000, "folder": 0},
                timeout=30.0, follow_redirects=False,
            )
            try:
                d = resp.json()
            except ValueError:
                break
            if d.get("errno") != 0:
                break
            items = d.get("list") or []
            out.extend(items)
            if len(items) < 1000:
                break
            start += len(items)
        self.cache[path] = out
        return out


def weight_of(item: dict) -> int:
    p = str(item.get("path"))
    f = type("F", (), {
        "path": p, "name": str(item.get("server_filename")),
        "size": int(item.get("size") or 0), "md5": str(item.get("md5") or ""),
        "ext": p.rsplit(".", 1)[-1].lower() if "." in p else "",
    })()
    return judge_file(f, rules={"default": DEFAULT_MARKER_RULES}).count_weight


def main() -> int:
    db = get_sessionmaker()()
    service = BaiduPanService(db)
    try:
        token = service.ensure_access_token()
        pan = Pan(token)
        root = pan.list("/")

        print("=" * 70)
        print("① 每个顶层目录的规模（递归统计全部 mp4）")
        print("=" * 70)
        stats: dict[str, dict] = {}
        for top_item in root:
            if not int(top_item.get("isdir") or 0):
                continue
            top = str(top_item.get("path"))
            stack = [top]
            n_mp4 = n_other = n_dir = 0
            weight = 0
            direct_files = 0
            while stack:
                cur = stack.pop()
                for x in pan.list(cur):
                    p = str(x.get("path"))
                    if int(x.get("isdir") or 0):
                        n_dir += 1
                        stack.append(p)
                    elif p.lower().endswith(".mp4"):
                        n_mp4 += 1
                        weight += weight_of(x)
                    else:
                        n_other += 1
                if cur == top:
                    direct_files = sum(
                        1 for x in pan.list(cur)
                        if not int(x.get("isdir") or 0)
                    )
            stats[top] = {"mp4": n_mp4, "other": n_other, "dirs": n_dir,
                           "weight": weight, "direct": direct_files}
            print(f"  {top:<16} mp4={n_mp4:>4}  非mp4={n_other:>4}  "
                  f"子目录={n_dir:>4}  直挂文件={direct_files:>3}  条数={weight:>4}")

        print()
        print("=" * 70)
        print("② 「成品-*」的日期目录清单 vs 缺失日期")
        print("=" * 70)
        for top in ("/成品-小瑶", "/成品-小雨"):
            emp = top.split("/")[-1]
            found: set[str] = set()
            month_dirs: list[str] = []
            for m in pan.list(top):
                if not int(m.get("isdir") or 0):
                    continue
                mp = str(m.get("path"))
                month_dirs.append(mp)
                for d in pan.list(mp):
                    if int(d.get("isdir") or 0):
                        found.add(str(d.get("path")).split("/")[-1])
                    else:
                        found.add(f"⚠️直挂:{str(d.get('path')).split('/')[-1]}")
            print(f"\n  【{emp}】月份目录: {month_dirs}")
            print(f"  日期目录({len(found)}): {sorted(found)}")

            # 缺失日期推算
            import calendar
            import re
            for m in month_dirs:
                mm = re.match(r"^(\d{1,2})月$", m.split("/")[-1])
                if not mm:
                    continue
                month = int(mm.group(1))
                last = calendar.monthrange(2026, month)[1]
                want = {f"{month}.{d}" for d in range(1, last + 1)}
                # 10 月只到 10.31（按网盘实际范围），11 月到 11.19
                have = {x for x in found if not x.startswith("⚠️")}
                miss = sorted(want - have, key=lambda s: int(s.split(".")[1]))
                if miss:
                    print(f"    {m.split('/')[-1]} 缺: {miss}")
                extra = sorted(have - want)
                if extra:
                    print(f"    {m.split('/')[-1]} 越界: {extra}")

        print()
        print("=" * 70)
        print("③ 未绑定目录 —— 多少工作量没被统计")
        print("=" * 70)
        total_all = sum(v["weight"] for v in stats.values())
        bound = sum(v["weight"] for k, v in stats.items() if k.startswith("/成品"))
        print(f"  已统计（成品-*）条数 = {bound}")
        print(f"  全网盘条数           = {total_all}")
        print(f"  ⚠️ 未纳入统计的条数   = {total_all - bound}")
        for k, v in stats.items():
            if not k.startswith("/成品"):
                print(f"     {k}: {v['weight']} 条（{v['mp4']} 个 mp4）")
    finally:
        service.close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
