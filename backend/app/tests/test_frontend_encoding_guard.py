"""前端源码编码守卫（防 mojibake）。

⚠️ **这个坑的真实经过**
在 Windows 上用 PowerShell 的 ``Add-Content`` 给 ``.vue`` 追加中文时，
PowerShell 按系统 ANSI 编码（GBK）写入，而文件本身是 UTF-8
⇒ 追加部分的中文全变 mojibake。

⚠️ **症状极具迷惑性**，三种表现：
1. Vue 模板报「Element is missing end tag」—— 看着像模板写错，实际是编码坏了；
2. script 里字符串变乱码，但**能编译通过** ⇒ 界面显示「���」；
3. 某些字符能通过 latin-1 roundtrip「修复」，但**信息已不可逆丢失**
   （变成 U+FFFD），反而更糟。

⇒ 用测试**提前挡住**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = ROOT / "frontend" / "src"

#: 要检查的扩展名
SUFFIXES = {".vue", ".ts", ".js", ".json", ".css", ".scss", ".html"}


def _source_files() -> list[Path]:
    if not FRONTEND_SRC.exists():
        return []
    files: list[Path] = []
    for path in FRONTEND_SRC.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        files.append(path)
    return sorted(files)


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_前端文件是合法UTF8(path: Path):
    """⚠️ 文件必须能按 UTF-8 解码。

    ⚠️ PowerShell 的 ``Add-Content`` 在中文 Windows 上按 **GBK** 写入
    ⇒ 中文部分会变成非法 UTF-8 字节。
    """
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(
            f"{path.relative_to(ROOT)} 不是合法 UTF-8（{exc}）—— "
            "很可能被 PowerShell 的 Add-Content / Out-File 以 ANSI 编码写入过。"
            "请用 Write 工具或 Python（encoding='utf-8'）重写该文件。"
        )


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_前端文件没有替换字符(path: Path):
    """⚠️ U+FFFD（``�``）是 mojibake 修复失败后的残留。

    ⚠️ **它不会报错，只会静默显示乱码** ——
    模板里的「���」用户看得见，script 里的「���」往往看不见。
    """
    text = path.read_text(encoding="utf-8")
    if "\ufffd" not in text:
        return

    lines = [
        (i + 1, line)
        for i, line in enumerate(text.split("\n"))
        if "\ufffd" in line
    ]
    preview = "\n".join(f"    第 {n} 行: {line[:100]}" for n, line in lines[:5])
    pytest.fail(
        f"{path.relative_to(ROOT)} 含 {text.count(chr(0xFFFD))} 个 U+FFFD 替换字符"
        f"（mojibake 残留）：\n{preview}"
    )
