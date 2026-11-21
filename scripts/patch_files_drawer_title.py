"""给文件明细抽屉标题加上日期。

⚠️ **为什么要加**
抽屉默认展示「最近一天」的文件，但标题里没写是哪一天
⇒ 用户看到「暂无数据」会以为是系统坏了，
实际只是那天本来就没有文件。

⚠️ 用 Python 改（显式 utf-8）——
   **不要用 PowerShell 的 Add-Content 写中文**（会按 ANSI/GBK 编码）。
"""

from __future__ import annotations

import sys
from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "views"
    / "performance"
    / "PanWorkloadPanel.vue"
)

OLD_TITLE = "  filesTitle.value = `文件明细 — ${employeeId}`"
NEW_TITLE = "  filesTitle.value = '文件明细'"

OLD_LOAD = """    const res = await panWorkloadApi.getFiles({
      employee_id: employeeId,
      business_date: latest.businessDate,
    })
    files.value = res.items"""
NEW_LOAD = """    // ⚠️ **标题要带上日期** —— 否则用户不知道看的是哪一天，
    //    看到「暂无数据」会以为是系统出问题。
    filesTitle.value = `文件明细 — ${employeeId} — ${latest.businessDate}`
    const res = await panWorkloadApi.getFiles({
      employee_id: employeeId,
      business_date: latest.businessDate,
    })
    files.value = res.items"""


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    if OLD_TITLE not in text:
        print("[SKIP] 标题已含日期处理（或锚点变化）")
    else:
        text = text.replace(OLD_TITLE, NEW_TITLE, 1)
    if OLD_LOAD not in text:
        print("[FAIL] 找不到加载锚点")
        return 1
    text = text.replace(OLD_LOAD, NEW_LOAD, 1)
    TARGET.write_text(text, encoding="utf-8")
    print("[OK] 文件明细标题已加日期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
