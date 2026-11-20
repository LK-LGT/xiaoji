"""飞书卡片正文渲染 —— Markdown 清洗 + 表格提取。

⚠️ **为什么需要这个模块**
------------------------
AI 回答是**模型的 Markdown 输出**，直接塞进卡片会出现两种糟心事：

1. **源码外泄** —— 用户看到 ``| 指标 | 权重 |``、``**加粗**``、``- 列表``
   这些语法符号本身。之前为了安全用了 ``plain_text``，
   结果**格式全丢**，一张卡片里 14 行竖线 —— 可读性极差。
2. **真表格塞不进飞书** —— 飞书的 ``markdown`` 元素
   **不支持 Markdown 表格**，再漂亮也渲染成一坨带竖线的文本。

⇒ 所以要**两件事**：
   - 把 Markdown 表格**提取出来**，转成飞书 ``table`` 组件（真表格）；
   - 其余部分**清洗后**用 ``markdown`` 元素渲染。

⚠️ **安全边界：清洗是硬要求，不是锦上添花**
------------------------------------------
``markdown`` 元素会解析 ``<at>``、链接、图片等语法。
模型输出是**不可信输入** —— 幻觉或恶意内容可能被渲染成
「@某人」或外链，让用户误以为是系统发的通知。

⇒ 清洗只做**删除**，不改写、不引入新内容：
   - 去掉 ``<at>...</at>``、``<a>``、``<img>`` 等 HTML 标签；
   - 去掉 ``[文字](javascript:...)`` 这类危险协议的链接；
   - 保留飞书**支持**的语法：``**粗体**``、``*斜体*``、
     ````` ``代码``` ``、``~~删除线~~``、无序/有序列表、引用。

⚠️ **为什么保留 markdown 而不是继续用 plain_text**
------------------------------------------------
纯文本确实最安全，但代价太大：模型精心写的分段、
加粗强调、列表层次全部丢失，用户看到的是一坨纯文字。

⇒ 平衡点：**保留渲染，但先把危险语法删干净**。

⚠️ **为什么只处理已知的、可确定性识别的形态**
------------------------------------------------
「模型是否在说假话」不由这里判断 —— 那是 grounding 与引用校验的职责。
这里只保证**语法层面**不会出乱子。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ===========================================================================
# 1. 危险语法清洗
# ===========================================================================

#: HTML 标签 —— 飞书 markdown 支持 ``<at>``/``<a>``/``<img>`` 等，
#: 它们能让模型输出伪装成「@某人」或外链。
#: ⇒ **全部删掉**（连同内容一起删，避免留下半个标签）。
_HTML_TAG = re.compile(
    r"</?(?:at|a|img|font|color|md|quote|hr|br|b|i|u)\b[^>]*>",
    re.IGNORECASE,
)

#: 带属性的自闭合标签，如 ``<at user_id="ou_xxx"></at>``
_HTML_PAIR = re.compile(
    r"</?(?:at|a|img)\b[^>]*>.*?</(?:at|a|img)>",
    re.IGNORECASE | re.DOTALL,
)

#: 危险协议的链接：``[点击](javascript:...)`` / ``(data:...)``
_DANGEROUS_LINK = re.compile(
    r"\[([^\]]*)\]\(\s*(?:javascript|data|vbscript|file)\s*:[^)]*\)",
    re.IGNORECASE,
)

#: 普通链接的协议白名单检查（只放行 http/https/mailto）
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def _safe_link(match: re.Match[str]) -> str:
    """只放行安全协议的链接，其余降级成纯文本。"""
    text, url = match.group(1), match.group(2)
    lowered = url.lower()
    if lowered.startswith(("http://", "https://", "mailto:")):
        return match.group(0)
    # ⇒ 降级：保留文字，丢掉链接目标
    return text or url


def sanitize_markdown(text: str) -> str:
    """清洗模型输出的 Markdown，使其可在飞书 ``markdown`` 元素安全渲染。

    ⚠️ **只删不改** —— 清洗后仍是模型的原意，不引入新事实。
    """
    if not text:
        return ""

    cleaned = text

    # ① 整对标签先删（连内容一起）
    cleaned = _HTML_PAIR.sub("", cleaned)
    # ② 残留的单个标签
    cleaned = _HTML_TAG.sub("", cleaned)
    # ③ 危险协议的链接 → 纯文本
    cleaned = _DANGEROUS_LINK.sub(r"\1", cleaned)
    # ④ 其余链接按协议过滤
    cleaned = _LINK.sub(_safe_link, cleaned)

    return cleaned.strip()


# ===========================================================================
# 2. Markdown 表格 → 飞书 table 组件
# ===========================================================================


@dataclass
class CardTable:
    """一张从 Markdown 表格提取出来的表。"""

    #: 表头
    headers: list[str]
    #: 数据行
    rows: list[list[str]]
    #: 原始 Markdown（含表头与分隔行）—— 供不渲染 table 的场景回退
    raw: str = ""
    #: ⚠️ **表格的所有行**（含表头那一行），按原顺序。
    #:
    #: 为什么需要它：模型有时会**漏写表头行**，
    #: 这时「第一行」其实是数据 —— 而 ``rows`` 里已经没有它了。
    #: 判断「第一行是不是数据」需要拿回原序列才能做对。
    all_rows: list[list[str]] = None  # type: ignore[assignment]


#: 表格分隔行：``|---|---|`` 或 ``|:---|---:|``
_TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$",
)


def _cell_text(raw: str) -> str:
    """把单元格里的行内 Markdown 语法**剥掉**，只留文字。

    ⚠️ 飞书 ``table`` 组件的单元格是 ``plain_text`` ——
    塞 ``**加粗**`` 进去会原样显示星号。
    """
    value = raw.strip()
    value = value.replace("**", "").replace("`", "")
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    return value.strip()


def _split_row(line: str) -> list[str]:
    """把一行 Markdown 表格切成单元格。"""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [_cell_text(part) for part in stripped.split("|")]


@dataclass
class CardBlock:
    """正文的一个块。

    ⚠️ **为什么要保留块的顺序**
    --------------------------
    模型的回答常是「结论 → 表格 → 补充说明」这种结构。
    如果把所有表格一律挪到末尾，语义就被打乱了 ——
    用户看到「补充说明」在表格前面，会以为表格是补充的。

    ⇒ 渲染层按**块顺序**输出，位置与原文一致。
    """

    #: 该块是表格
    table: CardTable | None = None
    #: 该块是正文（已剥离行内 Markdown）
    text: str = ""


def extract_blocks(text: str) -> list[CardBlock]:
    """把 Markdown 拆成**有序的块**列表（正文块与表格块交替）。

    ⚠️ 这是 :func:`extract_tables` 的**顺序感知版本** ——
    渲染层应该优先用它。
    """
    if not text:
        return []

    lines = text.splitlines()
    blocks: list[CardBlock] = []
    buffer: list[str] = []
    index = 0

    def flush() -> None:
        if buffer:
            body = "\n".join(buffer).strip()
            if body:
                blocks.append(CardBlock(text=body))
            buffer.clear()

    while index < len(lines):
        line = lines[index]
        # 表格 = 当前行含 |，且**下一行是分隔行**
        if (
            "|" in line
            and index + 1 < len(lines)
            and _TABLE_SEPARATOR.match(lines[index + 1])
            and "|" in lines[index + 1]
        ):
            headers = _split_row(line)
            rows: list[list[str]] = []
            cursor = index + 2
            raw_lines = [line, lines[index + 1]]
            while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
                rows.append(_split_row(lines[cursor]))
                raw_lines.append(lines[cursor])
                cursor += 1
            flush()
            blocks.append(
                CardBlock(
                    table=CardTable(
                        headers=headers,
                        rows=rows,
                        raw="\n".join(raw_lines),
                        # ⚠️ 含表头行的完整序列 —— 判断「表头是不是数据行」要用
                        all_rows=[headers, *rows],
                    )
                )
            )
            index = cursor
            continue
        buffer.append(line)
        index += 1

    flush()
    return blocks


def extract_tables(text: str) -> tuple[str, list[CardTable]]:
    """从 Markdown 中提取表格，返回 ``(去掉表格后的正文, 表格列表)``。

    ⚠️ **表格顺序与正文位置不再对应** ——
    需要保持位置关系请用 :func:`extract_blocks`。
    """
    blocks = extract_blocks(text)
    tables = [b.table for b in blocks if b.table is not None]
    body = "\n\n".join(b.text for b in blocks if b.table is None).strip()
    return body, tables


#: 飞书 table 组件单表最大列数
MAX_TABLE_COLUMNS = 8

#: 飞书 table 组件单表最大行数（含表头）
MAX_TABLE_ROWS = 30

#: 单元格里的纯数字（含百分号、负号、小数）
_NUMERIC_CELL = re.compile(r"^[\s\-−]?\d+(?:\.\d+)?\s*(?:%|分|元|次|个)?$")


def _looks_like_data_row(cells: list[str]) -> bool:
    """这一行看起来是**数据行**而不是表头。

    ⚠️ **为什么要判**
    --------------
    模型有时会漏写表头行 —— 于是 Markdown 变成::

        | 按计划交付率 | 15 | 13 | 主管评分 13.0 / 15.0 |
        |---|---|---|---|
        | 当月交付 | 10 | 6 | 6.0 |

    这时「第一行」被当成表头，用户看到表头写着
    「按计划交付率 | 15 | 13」—— 非常奇怪。

    ⚠️ **判据：表头里如果多数是数字，多半是数据行**
    ------------------------------------------------
    真正的表头是「指标 / 权重 / 得分 / 贡献分 / 算式」这类**文字**；
    数据行才是「15 / 13 / 56.0」这类**数字**。

    ⇒ 数字占比过半 → 判定为数据行。
    """
    if not cells:
        return False
    numeric = sum(1 for cell in cells if _NUMERIC_CELL.match(cell.strip()))
    # ⚠️ 用「过半」而不是「全部」——
    #    「13.0 / 15.0」这种算式单元格不是纯数字，
    #    真实数据行往往只有一半是纯数字。
    return numeric * 2 > len(cells)


def build_table_component(
    table: CardTable,
    *,
    max_columns: int = MAX_TABLE_COLUMNS,
    max_rows: int = MAX_TABLE_ROWS,
) -> dict[str, Any] | None:
    """把 Markdown 表格转成飞书 JSON 2.0 的 ``table`` 组件。

    ⚠️ **飞书 table 组件的限制**（超了就是空白卡片）：
       - 列数上限（官方 10，这里保守取 8）
       - 行数上限
       - 每个单元格必须是 ``plain_text``

    ⚠️ **超限时返回 None，**不**静默截断**
    ----------------------------------------
    截断列会**丢数据**（用户看到「贡献分」列消失，却不知道被截了），
    那比渲染失败更糟 —— 静默丢数据是这类系统最招人恨的 bug。

    ⇒ 返回 None，由调用方回退成普通文本（内容完整，只是不漂亮）。
    """
    headers = table.headers
    rows = table.rows

    if not headers:
        return None

    # ⚠️ 先判超限，再动手 —— 不能先截再判。
    if len(headers) > max_columns:
        return None
    if len(rows) + 1 > max_rows:
        return None

    # ⚠️ **表头合理性检查**
    #   模型有时会漏写表头行 —— 于是「第一行数据」被当成表头，
    #   用户看到表头写着「按计划交付率 | 15 | 13」，非常奇怪。
    #
    #   ⇒ 此时**补一个通用表头**，并把第一行放回数据区 ——
    #     比丢掉整张表好，比把数据当表头好。
    if _looks_like_data_row(headers):
        all_rows = table.all_rows or [headers, *rows]
        headers = [f"列 {index + 1}" for index in range(len(headers))]
        # ⚠️ 第一行是**数据** —— 必须放回 rows，否则那一行数据就丢了
        rows = all_rows

    # ⚠️ **每行列数必须与表头一致** ——
    #   飞书 table 要求行宽对齐，否则渲染异常（错位或空白）。
    #   模型写错表格时很常见（某行少一个竖线）。
    #   ⇒ 补齐缺的格子（空字符串），**不丢**多出来的内容。
    normalized: list[list[str]] = []
    for row in rows:
        if len(row) == len(headers):
            normalized.append(row)
            continue
        if len(row) > len(headers):
            # ⚠️ 多出来的格子并进最后一列，不静默丢弃
            merged = row[: len(headers) - 1] + [" | ".join(row[len(headers) - 1:])]
            normalized.append(merged)
        else:
            normalized.append(row + [""] * (len(headers) - len(row)))
    rows = normalized

    columns = [
        {
            # ⚠️ `name` 是列的 key，rows 里用这个名字填值 —— 必填
            "name": f"col_{index}",
            # ⚠️ `display_name` 是**表头显示名** —— 漏了就没有表头
            "display_name": header or f"列 {index + 1}",
            # ⚠️ `data_type` 决定单元格的渲染方式 —— 漏了飞书不认
            "data_type": "text",
            "width": "auto",
            "horizontal_align": "left",
            "vertical_align": "center",
        }
        for index, header in enumerate(headers)
    ]

    return {
        "tag": "table",
        # ⚠️ `page_size` 是**每页行数**，官方范围 [1,10] ——
        #    之前误写成列数，列多时会超范围。
        "page_size": min(max(len(rows), 1), 10),
        "row_height": "low",
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        # ⚠️⚠️ **rows 是「对象数组」，不是 cells 数组**
        #   官方结构：``{"col_0": "北京", "col_1": 12}``
        #   —— 每行是一个**字典**，键是 columns[].name。
        #   （写成 ``{"tag":"row","cells":[...]}`` 飞书会**静默忽略**，
        #     表现是表格渲染成空白 —— 而且接口不报错，极难排查。）
        "rows": [
            {f"col_{index}": cell for index, cell in enumerate(row)}
            for row in rows
        ],
    }


__all__ = [
    "CardBlock",
    "CardTable",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "build_table_component",
    "extract_blocks",
    "extract_tables",
    "sanitize_markdown",
]
