"""前端文件编码守卫。

⚠️ **为什么需要这个**
在 Windows 上用 PowerShell 的 ``Add-Content`` / ``Out-File`` 追加中文时，
会按系统 ANSI 编码（简体中文 Windows 是 **GBK**）写入，
而源码文件是 **UTF-8** ⇒ 追加部分的中文全部变成 mojibake。

⚠️ **症状极具迷惑性**
- Vue 模板里表现为「Element is missing end tag」等**看似无关**的编译错误；
- script 里表现为乱码字符串；
- 有时还能编译通过，界面显示「���」。

⇒ 用测试把它**挡住**，而不是等上线才发现。

⚠️ **检查两类问题**
1. **U+FFFD 替换字符** —— mojibake 修复失败的残留；
2. **UTF-8 可解码** —— 文件没被写成 GBK。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = ROOT / "frontend" / "src"

#: 要检查的扩展名
SUFFIXES = (".vue", ".ts", ".js", ".json", ".css", ".scss", ".html")


def iter_source_files():
    for path in FRONTEND_SRC.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUFFIXES:
            continue
        # ⚠️ 跳过依赖与生成物
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        yield path


def main() -> int:
    problems: list[str] = []

    for path in iter_source_files():
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            rel = path.relative_to(ROOT)
            problems.append(f"[编码] {rel} 不是合法 UTF-8（{exc}）")
            continue

        # ⚠️ U+FFFD 说明曾经 mojibake 过
        bad_count = text.count("\ufffd")
        if bad_count:
            rel = path.relative_to(ROOT)
            lines = [
                (i + 1, line)
                for i, line in enumerate(text.split("\n"))
                if "\ufffd" in line
            ]
            problems.append(
                f"[乱码] {rel} 有 {bad_count} 个 U+FFFD 替换字符，"
                f"首个在第 {lines[0][0]} 行"
            )

    if problems:
        print("发现前端文件编码问题：")
        for p in problems:
            print("  " + p)
        print(
            "\n⚠️ 修复方式：用 Write 工具或 Python（显式 encoding='utf-8'）重写该文件。"
            "\n   ⚠️ 不要用 PowerShell 的 Add-Content / Out-File 写中文。"
        )
        return 1

    print("[OK] 前端源码编码正常（无 U+FFFD，全部合法 UTF-8）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
