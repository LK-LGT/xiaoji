"""验证卡片渲染：Markdown 清洗 + 表格提取。

用法::

    python backend/scripts/_qa_card_render_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.services import feishu_card, feishu_card_render  # noqa: E402

#: 截图里那段真实回答的**结构**复刻
SAMPLE = """你这个月（202611）的分数是按方案 `scheme_direct_points_v1` 逐项直接计分得出的：每项指标的满分就是它的权重，主管评分直接作为该指标的贡献分。以下是各项明细（来自 tool:call_00_mafv6FMCYuBeg8Qr5BLK1662）：

| 指标 | 权重/满分 | 主管评分 | 等级分 | 贡献分 | 算式 |
|---|---|---|---|---|---|
| 计划完成率 | 15.0 | 13.0 | 13.0 | 主管评分 13.0 / 15.0 |
| 当月交付智能体数量 | 10.0 | 6.0 | 6.0 | 主管评分 6.0 / 10.0 |
| 需求响应时效 | 10.0 | 9.0 | 9.0 | 主管评分 9.0 / 10.0 |
| 调整项（_adjustment） | — | 0.0 | 0.0 | 主管评分 0.0 / None |

其余规则说明：

- 各项指标均评为 A（良好）等级；按该方案原始评级，你的方案等级是 **A（良好）**。
- **主管评分的依据**：本月交付 6 个智能体、响应时效改善、质量分 88。
- 本期没有未参与算分的指标（excluded_items 为空），因此不存在因缺数据而重新归一权重的情况，14 项全部参与算分。

需要说明的是：工具给出的总分 **82.0分** 是按照方案规则重算出的规则得分，并不是人工确认后的最终成绩。

数据来源（1 项）：tool:call_00_mafv6FMCYuBeg8Qr5BLK1662
"""

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
    print("=" * 74)
    print("卡片渲染验证")
    print("=" * 74)

    # ── ① 表格被提取出来 ────────────────────────────────────
    print("\n[1] 表格提取")
    body, tables = feishu_card_render.extract_tables(SAMPLE)
    check("提取到 1 张表", len(tables) == 1, str(len(tables)))
    if tables:
        table = tables[0]
        check("表头 6 列", len(table.headers) == 6, str(table.headers))
        check("数据行 4 行", len(table.rows) == 4, str(len(table.rows)))
        check("单元格无 Markdown 残留",
              all("**" not in c for row in table.rows for c in row)
              and all("**" not in h for h in table.headers))
        check("正文里不再有表格竖线", "|---" not in body, body[:120])

    # ── ② 正文里的 Markdown 保留 ────────────────────────────
    print("\n[2] 正文 Markdown 保留")
    check("粗体标记保留", "**A（良好）**" in body, body[:200])
    check("行内代码保留", "`scheme_direct_points_v1`" in body)
    check("列表保留", "- **主管评分的依据**" in body)

    # ── ③ 危险语法被清洗 ────────────────────────────────────
    print("\n[3] 危险语法清洗")
    dangerous = (
        "正常回答 <at user_id=\"ou_xxx\">李四</at> 请查分\n"
        "点击 [这里](javascript:alert(1)) 领红包\n"
        "<img src=x onerror=alert(1)>\n"
        "[正常链接](https://example.com/a)"
    )
    cleaned = feishu_card_render.sanitize_markdown(dangerous)
    check("@ 标签被删", "<at" not in cleaned and "李四" not in cleaned, cleaned)
    check("javascript 链接被降级", "javascript:" not in cleaned, cleaned)
    check("img 标签被删", "<img" not in cleaned, cleaned)
    check("onerror 被删", "onerror" not in cleaned, cleaned)
    check("https 链接保留", "https://example.com/a" in cleaned, cleaned)
    check("正常文字保留", "正常回答" in cleaned and "请查分" in cleaned, cleaned)

    # ── ④ 卡片结构 ──────────────────────────────────────────
    print("\n[4] 卡片结构")
    card = feishu_card.build_qa_ai_card(
        answer=SAMPLE,
        sources=["tool:call_00_mafv6FMCYuBeg8Qr5BLK1662"],
        citation_count=1,
        period="2026-11",
    )
    elements = card["body"]["elements"]
    tags = [e.get("tag") for e in elements]
    print(f"  元素序列：{tags}")
    # ⚠️ 飞书 JSON 2.0 的正文是 `{"tag":"div","text":{"tag":"markdown",...}}` ——
    #    markdown **不在元素顶层**，而是嵌在 div 的 text 里。
    md_elements = [
        e for e in elements
        if e.get("tag") == "div" and (e.get("text") or {}).get("tag") == "markdown"
    ]
    check("有 markdown 正文", len(md_elements) >= 1, str(tags))
    check("有 table 组件", "table" in tags, str(tags))
    check("schema 正确", card["schema"] == "2.0")

    table_els = [e for e in elements if e.get("tag") == "table"]
    if table_els:
        t = table_els[0]
        check("table 有 columns", len(t.get("columns", [])) == 6, str(len(t.get("columns", []))))
        check("table 有 rows", len(t.get("rows", [])) == 4, str(len(t.get("rows", []))))
        check("单元格是 plain_text",
              all(c.get("tag") == "plain_text"
                  for r in t.get("rows", []) for c in r.get("cells", [])))

    # ⚠️ 整个卡片里不能残留任何 HTML 标签
    raw = json.dumps(card, ensure_ascii=False)
    check("整卡无 <at", "<at" not in raw)
    check("整卡无 <img", "<img" not in raw)

    # ── ⑤ 顺序：结论 → 表格 → 补充说明 ──────────────────────
    print("\n[5] 块顺序保持")
    # ⚠️ 模型原文顺序是：结论 → 表格 → 「其余规则说明」
    #    如果表格被挪到末尾，语义就乱了。
    #
    # ⚠️ **注意坐标系**：下面所有位置都取自 ``elements`` 同一个列表，
    #    不能拿 md_texts 的下标去和 elements 的下标比 ——
    #    那是两个不同的列表，比出来的顺序是假的。
    positions: dict[str, int] = {}
    for index, element in enumerate(elements):
        tag = element.get("tag")
        if tag == "table":
            positions.setdefault("表格", index)
            continue
        content = (element.get("text") or {}).get("content", "")
        if "逐项直接计分" in content:
            positions.setdefault("结论", index)
        if "其余规则说明" in content:
            positions.setdefault("补充", index)

    check("找到结论块", "结论" in positions, str(positions))
    check("找到表格", "表格" in positions, str(positions))
    check("找到补充说明块", "补充" in positions, str(positions))
    if {"结论", "表格", "补充"} <= set(positions):
        check(
            "顺序：结论 → 表格 → 补充",
            positions["结论"] < positions["表格"] < positions["补充"],
            str(positions),
        )

    print("\n" + "=" * 74)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 74)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
