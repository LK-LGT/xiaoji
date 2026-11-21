"""把 ``panWorkload.ts`` 里所有 axios 调用改成**双泛型**。

⚠️ **为什么必须改**
框架的响应拦截器（``src/api/http.ts``）已经解包了一层：

    http.interceptors.response.use((response) => {
      const body = response.data
      if (body && typeof body.code !== 'undefined') {
        if (body.code !== 0) return Promise.reject(...)
        return body.data          // ← 已经解包
      }
      return body                  // ← 也已经解包
    })

⇒ ``http.get(...)`` 拿到的**已经是业务数据**，
再写 ``.then(r => r.data)`` 就是 ``undefined``。

⚠️ **表现极具迷惑性**
- 不报错
- 界面显示「暂无数据」
⇒ 极易误判成「后端没数据」。

⇒ 框架的正确写法是**双泛型**：``http.get<T, T>(...)``。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "api"
    / "panWorkload.ts"
)

#: 单泛型 → 双泛型
GENERIC_RE = re.compile(r"http\.(get|post)<([^<>]*(?:<[^<>]*>)?[^<>]*)>\(")


def fix_generics(text: str) -> tuple[str, int]:
    """单泛型改成双泛型。"""
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        method, inner = m.group(1), m.group(2)
        if "," in inner:
            return m.group(0)
        count += 1
        return f"http.{method}<{inner}, {inner}>("

    return GENERIC_RE.sub(repl, text), count


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text, n1 = fix_generics(text)

    # 去掉多余的 .then(r => r.data)
    before = text.count("r.data")
    text = text.replace(".then((r) => r.data)", "")
    text = re.sub(r"\n\s*\n(\s*\},)", r"\n\1", text)
    after = text.count("r.data")

    TARGET.write_text(text, encoding="utf-8")
    print(f"双泛型改写 {n1} 处；去掉 r.data {before - after} 处；剩余 {after}")

    # 复查
    singles = GENERIC_RE.findall(text)
    bad = [s for s in singles if "," not in s[1]]
    if bad or "r.data" in text:
        print(f"[FAIL] 仍有 {len(bad)} 处单泛型 / r.data 残留")
        return 1
    print("[OK] 全部为双泛型且无 r.data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
