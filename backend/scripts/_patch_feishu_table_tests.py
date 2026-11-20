"""把表格断言从旧结构（cells 数组）改成官方结构（对象数组）。

⚠️ **为什么必须改**
------------------
旧断言基于 ``{"tag":"row","cells":[...]}`` —— 那**不是**飞书 JSON 2.0
的 table 组件结构。官方要求：

    "columns": [{"name": "col_0", "display_name": "城市", "data_type": "text"}]
    "rows":    [{"col_0": "北京", "col_1": 12}]

``rows`` 是**对象数组**（键 = columns[].name）。

⇒ 用旧结构时飞书**静默忽略** rows：接口返回 200，
   卡片却渲染成空白表格 —— 极难排查。

用法::

    python backend/scripts/_patch_feishu_table_tests.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "backend" / "app" / "tests" / "test_feishu_card_render.py"

OLD = '''def test_build_table_component_structure():
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    component = feishu_card_render.build_table_component(tables[0])

    assert component is not None
    assert component["tag"] == "table"
    assert len(component["columns"]) == 3
    assert len(component["rows"]) == 2
    # ⚠️ 单元格必须是 plain_text
    for row in component["rows"]:
        for cell in row["cells"]:
            assert cell["tag"] == "plain_text"
'''

NEW = '''def test_build_table_component_structure():
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
'''

OLD2 = '''def test_build_table_component_does_not_drop_columns():
    """正常表格的列**一个都不能少**。"""
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    component = feishu_card_render.build_table_component(tables[0])
    assert len(component["columns"]) == 3
    for row in component["rows"]:
        assert len(row["cells"]) == 3
'''

NEW2 = '''def test_build_table_component_does_not_drop_columns():
    """正常表格的列**一个都不能少**。"""
    _, tables = feishu_card_render.extract_tables(SAMPLE_TABLE)
    component = feishu_card_render.build_table_component(tables[0])
    assert len(component["columns"]) == 3
    for row in component["rows"]:
        assert len(row) == 3
'''

OLD3 = '''    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    assert len(component["rows"]) == 2
    assert len(component["rows"][1]["cells"]) == 3
    assert component["rows"][1]["cells"][2]["content"] == ""
'''

NEW3 = '''    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    assert len(component["rows"]) == 2
    assert len(component["rows"][1]) == 3
    assert component["rows"][1]["col_2"] == ""
'''

OLD4 = '''    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    cells = component["rows"][0]["cells"]
    assert len(cells) == 2
    assert "3" in cells[1]["content"]
'''

NEW4 = '''    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    row = component["rows"][0]
    assert len(row) == 2
    assert "3" in row["col_1"]
'''

OLD5 = '''    # ⚠️ 关键：第一行数据必须在 rows 里，不能被当表头吃掉
    first_row = [c["content"] for c in component["rows"][0]["cells"]]
    assert first_row[0] == "按计划交付率", first_row
    assert first_row[1] == "15", first_row
    # ⚠️ 而且第一行**不能重复**出现在后面
    all_rows = [[c["content"] for c in r["cells"]] for r in component["rows"]]
    assert all_rows.count(first_row) == 1, all_rows
'''

NEW5 = '''    # ⚠️ 关键：第一行数据必须在 rows 里，不能被当表头吃掉
    first_row = component["rows"][0]
    assert first_row["col_0"] == "按计划交付率", first_row
    assert first_row["col_1"] == "15", first_row
    # ⚠️ 而且第一行**不能重复**出现在后面
    all_rows = [tuple(r.values()) for r in component["rows"]]
    assert all_rows.count(tuple(first_row.values())) == 1, all_rows
'''

OLD6 = '''    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    first_row = [c["content"] for c in component["rows"][0]["cells"]]
    assert first_row == ["销售额", "0.7", "56.0"]
'''

NEW6 = '''    component = feishu_card_render.build_table_component(tables[0])
    assert component is not None
    assert list(component["rows"][0].values()) == ["销售额", "0.7", "56.0"]
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")

    if "rows 是对象数组" in source:
        print("✓ 已打过补丁，跳过")
        return 0

    pairs = [
        (OLD, NEW),
        (OLD2, NEW2),
        (OLD3, NEW3),
        (OLD4, NEW4),
        (OLD5, NEW5),
        (OLD6, NEW6),
    ]
    for index, (old, new) in enumerate(pairs):
        if old not in source:
            print(f"✗ 第 {index + 1} 段找不到 —— 手工核对")
            return 1
        source = source.replace(old, new, 1)

    TARGET.write_text(source, encoding="utf-8")
    print("✓ 补丁完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
