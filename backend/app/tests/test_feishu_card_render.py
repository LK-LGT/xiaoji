"""飞书卡片渲染的单元测试 —— Markdown 清洗 + 表格提取。

⚠️ **为什么这组测试重要**
------------------------
AI 回答是**模型的 Markdown 输出**，直接进卡片会有两个问题：

1. **源码外泄** —— 用户看到 ``| 指标 | 权重 |``、``**加粗**``
   这些语法符号本身（早期版本用 ``plain_text`` 直出，正是这个效果）。
2. **表格塞不进飞书** —— 飞书 ``markdown`` 元素不支持 Markdown 表格。

⇒ 所以表格必须提取成飞书 ``table`` 组件，正文必须**先清洗再渲染**。

⚠️ **安全是硬要求**
------------------
``markdown`` 元素会解析 ``<at>``、链接等语法。
模型输出是**不可信输入** —— 幻觉或恶意内容可能被渲染成
「@某人」或外链，让用户误以为是系统发的通知。
⇒ 清洗只做**删除**，不改写、不引入新内容。
"""

from __future__ import annotations

import pytest

from backend.app.services import feishu_card, feishu_card_render

# ===========================================================================
# ① Markdown 清洗 —— 危险语法必须删掉
# ===========================================================================


@pytest.mark.parametrize(
    "dangerous",
    [
        '<at user_id="ou_xxx">李四</at> 请查分',
        '<at id="ou_xxx"></at>',
        "<img src=x onerror=alert(1)>",
        "[点我领红包](javascript:alert(1))",
        "[看这个](data:text/html;base64,PHNjcmlwdD4=)",
        "[本地文件](file:///etc/passwd)",
        "<a href='http://evil.com'>钓鱼</a>",
    ],
)
def test_dangerous_syntax_removed(dangerous):
    """危险语法必须被清掉。"""
    cleaned = feishu_card_render.sanitize_markdown(dangerous)
    assert "<at" not in cleaned.lower()
    assert "<img" not in cleaned.lower()
    assert "<a " not in cleaned.lower()
    assert "javascript:" not in cleaned.lower()
    assert "data:" not in cleaned.lower()
    assert "file:" not in cleaned.lower()
    assert "onerror" not in cleaned.lower()


@pytest.mark.parametrize(
    "safe",
    [
        "普通回答，没有任何标签",
        "**加粗** 与 *斜体*",
        "`scheme_direct_points_v1`",
        "[正常链接](https://example.com/a)",
        "- 列表项\n- 另一个",
        "~~删除线~~",
    ],
)
def test_safe_content_preserved(safe):
    """正常内容必须原样保留。"""
    cleaned = feishu_card_render.sanitize_markdown(safe)
    assert cleaned == safe.strip()


def test_sanitize_keeps_link_text_when_protocol_dropped():
    """危险协议的链接 → 保留文字，丢掉目标。"""
    cleaned = feishu_card_render.sanitize_markdown("[点这里](javascript:alert(1))")
    assert "点这里" in cleaned
    assert "javascript" not in cleaned


def test_sanitize_empty():
    assert feishu_card_render.sanitize_markdown("") == ""
    assert feishu_card_render.sanitize_markdown("   ") == ""


# ===========================================================================
# ② 表格提取
# ===========================================================================

SAMPLE_TABLE = """这是结论。

| 指标 | 权重 | 贡献分 |
|---|---|---|
| 销售额 | 0.7 | 56.0 |
| 转化率 | 0.3 | 24.0 |

这是补充说明。"""


def test_extract_tables_finds_table():
    body, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    assert len(tables) == 1
    assert tables[0].headers == ["指标", "权重", "贡献分"]
    assert tables[0].rows == [["销售额", "0.7", "56.0"], ["转化率", "0.3", "24.0"]]
    assert "|---" not in body
    assert "这是结论。" in body
    assert "这是补充说明。" in body


def test_extract_tables_strips_cell_markdown():
    """单元格里的 ``**加粗**`` 必须剥掉。

    ⚠️ 飞书 table 单元格是 ``plain_text`` ——
    塞 ``**加粗**`` 会原样显示星号。
    """
    _, tables = feishu_card_render.extract_tables(
        "| 指标 | 说明 |\n|---|---|\n| A | **重点** |"
    )
    assert tables[0].rows[0] == ["A", "重点"]


def test_extract_blocks_preserves_order():
    """块的顺序必须与原文一致（结论 → 表格 → 补充）。"""
    blocks = feishu_card_render.extract_blocks(SAMPLE_TABLE)
    assert len(blocks) == 3
    assert blocks[0].table is None
    assert "这是结论。" in blocks[0].text
    assert blocks[1].table is not None
    assert blocks[2].table is None
    assert "这是补充说明。" in blocks[2].text


def test_extract_blocks_no_table():
    blocks = feishu_card_render.extract_blocks("就是一段普通文字。")
    assert len(blocks) == 1
    assert blocks[0].table is None


def test_pipe_without_separator_is_not_a_table():
    """只有竖线、没有分隔行 → 不是表格。

    ⚠️ 否则正文里的 ``a | b`` 会被误当表格，拆成两列。
    """
    body, tables = feishu_card_render.extract_tables("指标 A | 指标 B")
    assert tables == []
    assert "指标 A | 指标 B" in body


# ===========================================================================
# ③ table 组件
# ===========================================================================


def test_build_table_component_structure():
    """结构必须符合飞书 JSON 2.0 的 table 组件。

    ⚠️ **rows 是对象数组**（键 = columns[].name）——
    写成 ``{"tag":"row","cells":[...]}`` 飞书会**静默忽略**，
    表现是表格渲染成空白，而接口不报错。
    """
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    component = feishu_card_render.build_table_component(tables[0])

    assert component is not None
    assert component["tag"] == "table"
    assert len(component["columns"]) == 3
    assert len(component["rows"]) == 2

    # ⚠️ columns 必须有 display_name（表头名）与 data_type
    for index, column in enumerate(component["columns"]):
        assert column["name"] == f"col_{index}"
        assert column["display_name"], column
        assert column["data_type"] == "text"

    # ⚠️ rows 必须是「键=列名」的对象字典
    assert component["rows"][0] == {
        "col_0": "销售额", "col_1": "0.7", "col_2": "56.0",
    }
    for row in component["rows"]:
        assert isinstance(row, dict)
        assert set(row) == {"col_0", "col_1", "col_2"}


def test_table_page_size_in_official_range():
    """``page_size`` 是每页行数，官方范围 [1,10]。"""
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    component = feishu_card_render.build_table_component(tables[0])
    assert 1 <= component["page_size"] <= 10


def test_build_table_component_respects_limits():
    """超列数/行数 → 返回 None（宁可回退文本，也不发白卡片）。

    ⚠️ **不能静默截断** —— 截掉「贡献分」列会让用户以为没这项指标，
    那比渲染失败更糟。
    """
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    assert feishu_card_render.build_table_component(tables[0], max_columns=2) is None
    assert feishu_card_render.build_table_component(tables[0], max_rows=1) is None


def test_build_table_component_does_not_drop_columns():
    """正常表格的列**一个都不能少**。"""
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    component = feishu_card_render.build_table_component(tables[0])
    assert len(component["columns"]) == 3
    for row in component["rows"]:
        assert len(row) == 3


def test_ragged_rows_are_padded_not_dropped():
    """某行少一格 → 补空格子，不丢内容。"""
    _, tables = feishu_card_render.extract_tables(
        "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 |"
    )
    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    assert len(component["rows"]) == 2
    assert len(component["rows"][1]) == 3
    assert component["rows"][1]["col_2"] == ""


def test_ragged_rows_extra_cells_are_merged_not_dropped():
    """某行多一格 → 并进最后一列，不静默丢弃。"""
    _, tables = feishu_card_render.extract_tables(
        "| A | B |\n|---|---|\n| 1 | 2 | 3 |"
    )
    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    row = component["rows"][0]
    assert len(row) == 2
    assert "3" in row["col_1"]


# ===========================================================================
# ④ 卡片组装
# ===========================================================================


def test_missing_header_row_gets_generic_header():
    """模型漏写表头行 → 补通用表头，数据行不丢。

    ⚠️ **真实发生过**：模型输出过
    ``| 按计划交付率 | 15 | 13 | ... |`` 开头（没有表头行），
    结果第一行数据被当表头，用户看到表头写着「按计划交付率」。
    """
    _, tables = feishu_card_render.extract_tables(
        "| 按计划交付率 | 15 | 13 |\n"
        "|---|---|---|\n"
        "| 当月交付 | 10 | 6 |"
    )
    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None

    # ⚠️ 关键：第一行数据必须在 rows 里，不能被当表头吃掉
    first_row = component["rows"][0]
    assert first_row["col_0"] == "按计划交付率", first_row
    assert first_row["col_1"] == "15", first_row
    # ⚠️ 而且第一行**不能重复**出现在后面
    all_rows = [tuple(r.values()) for r in component["rows"]]
    assert all_rows.count(tuple(first_row.values())) == 1, all_rows


def test_real_header_row_is_kept():
    """正常表头不能被误判成数据行。"""
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    assert list(component["rows"][0].values()) == ["销售额", "0.7", "56.0"]


def test_qa_card_renders_markdown_and_table():
    """问答卡：正文是 markdown，表格是 table 组件。"""
    card = feishu_card.build_qa_ai_card(
        answer=SAMPLE_TABLE,
        sources=["tool:call_00_abc"],
        citation_count=1,
        period="2026-11",
    )
    elements = card["body"]["elements"]
    tags = [e.get("tag") for e in elements]

    assert "table" in tags
    # ⚠️ 飞书 JSON 2.0 里 markdown 是**元素本身**，
    #    不能包在 div 的 text 里（那样飞书直接拒收整张卡）。
    assert "markdown" in tags, tags


def test_qa_card_preserves_block_order():
    """表格必须在它原本的位置（结论之后、补充之前）。"""
    card = feishu_card.build_qa_ai_card(answer=SAMPLE_TABLE)
    elements = card["body"]["elements"]

    positions: dict[str, int] = {}
    for index, element in enumerate(elements):
        if element.get("tag") == "table":
            positions.setdefault("表格", index)
            continue
        # ⚠️ markdown 元素的内容在 content 字段
        content = element.get("content") or (element.get("text") or {}).get("content", "")
        if "这是结论。" in content:
            positions.setdefault("结论", index)
        if "这是补充说明。" in content:
            positions.setdefault("补充", index)

    assert positions["结论"] < positions["表格"] < positions["补充"]


def test_qa_card_no_dangerous_tags_anywhere():
    """整张卡片里不能有 HTML 标签残留。"""
    import json

    card = feishu_card.build_qa_ai_card(
        answer='<at user_id="ou_x">李四</at> 我的分数是 <img src=x onerror=alert(1)> 80 分',
    )
    raw = json.dumps(card, ensure_ascii=False)
    assert "<at" not in raw
    assert "<img" not in raw
    assert "onerror" not in raw


def test_qa_card_empty_answer():
    """空回答 → 给占位，不发空白卡片。"""
    card = feishu_card.build_qa_ai_card(answer="")
    raw = str(card)
    assert "无内容" in raw or len(card["body"]["elements"]) > 0
