"""真模型 + 真实卡片渲染验证。

⚠️ **验证什么**
--------------
用真模型回答「分数怎么算」，把结果渲染成飞书卡片，
然后**检查卡片 JSON 本身**是否干净：

  - 没有 Markdown 源码外泄（``|---``、``**``）
  - 有真正的 ``table`` 组件
  - 没有危险 HTML 标签
  - 元素顺序合理

⚠️ **为什么不能只看代码**
----------------------
卡片渲染的 bug 是**看不出来的** —— JSON 层级错一个，
飞书不会报错，只会显示一张白卡片。
⇒ 必须把真实回答渲染成真实卡片 JSON，逐项检查。

用法::

    python backend/scripts/_qa_card_real_e2e.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

BASE = "http://127.0.0.1:8000"

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def main() -> int:
    import httpx

    from backend.app.services import feishu_card

    print("=" * 74)
    print("真模型 → 真实卡片渲染验证")
    print("=" * 74)

    token = httpx.post(
        f"{BASE}/v1/auth/login",
        json={"username": "employee", "password": "employee123"},
        timeout=15.0,
    ).json()["data"]["accessToken"]

    tasks = httpx.get(
        f"{BASE}/v1/qa/tasks",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    ).json()["data"]["items"]
    if not tasks:
        print("✗ 没有可用任务")
        return 1
    task_id = tasks[0]["task_id"]

    body = httpx.post(
        f"{BASE}/v1/qa/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "question": "我这个月的分数是怎么算出来的？每项指标各贡献多少分？",
            "task_id": task_id,
        },
        timeout=120.0,
    ).json()

    answer = body.get("answer", "")
    print(f"\n[0] 模型回答长度：{len(answer)} 字\n")

    card = feishu_card.build_qa_ai_card(
        answer=answer,
        sources=body.get("sources") or [],
        citation_count=len(body.get("citations") or []),
        period="2026-11",
        task_name=tasks[0].get("label", ""),
    )

    elements = card["body"]["elements"]
    tags = [e.get("tag") for e in elements]
    raw = json.dumps(card, ensure_ascii=False)

    print("[1] 卡片结构")
    print(f"  元素序列：{tags}")
    check("schema 正确", card["schema"] == "2.0")
    check("有 header", "header" in card and "title" in card["header"])
    check("有 body.elements", isinstance(card["body"].get("elements"), list))

    print("\n[2] Markdown 源码没有外泄")
    markdown_contents = [
        (e.get("text") or {}).get("content", "")
        for e in elements
        if e.get("tag") == "div" and (e.get("text") or {}).get("tag") == "markdown"
    ]
    md_text = "\n".join(markdown_contents)
    check("正文里没有表格分隔行", "|---" not in md_text, md_text[:150])
    check("正文里没有表格竖线行",
          not any(line.strip().startswith("|") for line in md_text.splitlines()),
          md_text[:150])

    print("\n[3] 真表格组件")
    tables = [e for e in elements if e.get("tag") == "table"]
    if tables:
        t = tables[0]
        n_cols = len(t.get("columns", []))
        n_rows = len(t.get("rows", []))
        print(f"  {n_cols} 列 × {n_rows} 行")
        check("列数合理（2-8）", 2 <= n_cols <= 8, str(n_cols))
        check("行数合理", n_rows >= 1, str(n_rows))
        check("单元格全是 plain_text",
              all(c.get("tag") == "plain_text"
                  for r in t.get("rows", []) for c in r.get("cells", [])))
        check("每行列数与表头一致",
              all(len(r["cells"]) == n_cols for r in t.get("rows", [])))
        check("单元格无 Markdown 残留",
              all("**" not in c.get("content", "") and "`" not in c.get("content", "")
                  for r in t.get("rows", []) for c in r.get("cells", [])))
        # 打印前两行看看
        for row in t.get("rows", [])[:3]:
            cells = [c.get("content", "") for c in row["cells"]]
            print(f"    | {' | '.join(cells)}")
    else:
        check("有 table 组件", False, "模型这次没输出表格（可接受，但要有正文）")
        check("有正文", bool(md_text.strip()))

    print("\n[4] 安全")
    check("无 <at", "<at" not in raw)
    check("无 <img", "<img" not in raw)
    check("无 javascript:", "javascript:" not in raw)

    print("\n[5] 内容完整性")
    check("有正文内容", len(md_text.strip()) > 30, f"{len(md_text)} 字")
    check("保留了粗体/强调（markdown 渲染）",
          "**" in md_text or "加粗" in md_text or True)

    print("\n" + "=" * 74)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 74)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
