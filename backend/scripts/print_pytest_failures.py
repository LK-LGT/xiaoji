#!/usr/bin/env python3
"""把 pytest 的 junit 报告转成**人可读**的失败摘要。

⚠️ **为什么需要这个脚本（2026-11-25）**
GitHub Actions 的 job log 需要 **admin 权限**才能下载
⇒ 排查 CI 失败时，非管理员根本看不到「为什么挂」。

⇒ 这个脚本在 CI 里跑（失败后），把每个失败用例的
- 用例名
- 断言消息
- traceback 尾部

直接 ``print`` 到 stdout ⇒ 出现在**网页上的 job log** 里，
不需要任何权限就能看。

用法::

    python backend/scripts/print_pytest_failures.py data/_pytest_report.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: traceback 最多打印多少字符 —— 多了会把网页 log 撑爆。
_MAX_TRACEBACK_CHARS = 3000
#: 断言消息最多打印多少字符。
_MAX_MESSAGE_CHARS = 2000


def _failures(root: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    """从 junit XML 里挑出所有 failure / error 节点。"""
    found: list[tuple[ET.Element, ET.Element]] = []
    for case in root.iter("testcase"):
        for child in case:
            if child.tag in ("failure", "error"):
                found.append((case, child))
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python print_pytest_failures.py <junit.xml>")
        return 2

    path = Path(argv[1])
    if not path.exists():
        print(f"（没有找到 junit 报告：{path}）")
        return 0

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        print(f"（junit 报告解析失败：{exc}）")
        return 0

    failures = _failures(root)
    if not failures:
        print("（junit 报告里没有失败用例 —— 失败可能发生在收集阶段）")
        # ⚠️ **收集阶段失败**（import 错误、语法错误）不会有 testcase 节点
        #    ⇒ 这种情况要提示去看原始输出。
        for suite in root.iter("testsuite"):
            for key in ("errors", "failures"):
                count = suite.get(key)
                if count and count != "0":
                    print(f"  test suite {key} = {count}")
        return 0

    print(f"共 {len(failures)} 个失败用例：\n")
    for case, child in failures:
        print("=" * 72)
        print(f"FAILED: {case.get('classname')}::{case.get('name')}")
        message = (child.get("message") or "").strip()
        if message:
            print("--- message ---")
            print(message[:_MAX_MESSAGE_CHARS])
        text = (child.text or "").strip()
        if text:
            print("--- traceback ---")
            print(text[-_MAX_TRACEBACK_CHARS:])
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
