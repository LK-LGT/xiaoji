"""未绑定目录规模统计（2026-11-21）—— 只查 /口播成品 与 /鹿13。

用 BP-1 已有的递归客户端（已验证可用），避免逐目录手写请求。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

# ⚠️ **引导到仓库根 + 用 ``backend.app.*``** —— 见
# ``seed_department_workload.py`` 里同一问题的说明：
# 顶层 ``app`` 会被 agent-service 的 ``app`` 包抢占。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.db import get_sessionmaker  # noqa: E402
from backend.app.services.baidu_pan_service import BaiduPanService  # noqa: E402
from backend.app.services.pan_workload_rules import (  # noqa: E402
    DEFAULT_MARKER_RULES,
    judge_file,
)


def main() -> int:
    db = get_sessionmaker()()
    service = BaiduPanService(db)
    try:
        token = service.ensure_access_token()
        client = service.client

        for top in ("/口播成品", "/鹿13"):
            print("=" * 68)
            print(f"【{top}】")
            print("=" * 68)
            try:
                files = client.list_files_recursive(token, top, limit=3000)
            except Exception as exc:  # noqa: BLE001
                print(f"  读取失败: {exc}")
                continue
            print(f"  递归到文件数 = {len(files)}")

            by_dir: dict[str, list] = defaultdict(list)
            for f in files:
                by_dir[str(Path(f.path).parent)].append(f)

            mp4 = [f for f in files if f.ext == "mp4"]
            other = [f for f in files if f.ext != "mp4"]
            weight = 0
            for f in mp4:
                weight += judge_file(
                    f, rules={"default": DEFAULT_MARKER_RULES}
                ).count_weight
            print(f"  mp4 = {len(mp4)}   非mp4 = {len(other)}   规则算出条数 = {weight}")
            print(f"  子目录数 = {len(by_dir)}")

            # 列出子目录（前 40 个）
            print("  子目录:")
            for d in sorted(by_dir)[:40]:
                n_mp4 = sum(1 for f in by_dir[d] if f.ext == "mp4")
                print(f"    {d}   mp4={n_mp4}")
            if len(by_dir) > 40:
                print(f"    ... 还有 {len(by_dir) - 40} 个")

            # 含标记的文件
            marked = [
                (f, judge_file(f, rules={"default": DEFAULT_MARKER_RULES}))
                for f in mp4
            ]
            marked = [(f, v) for f, v in marked if v.markers.names]
            print(f"  含标记文件 = {len(marked)}")
            for f, v in marked[:20]:
                print(f"    {f.path}  → {v.markers.names} 权重{v.count_weight}")
            print()
    finally:
        service.close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
