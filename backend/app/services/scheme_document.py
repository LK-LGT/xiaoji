"""Lossless business-document import; no inferred arithmetic or invented standards."""

from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile

from openpyxl import load_workbook


def extract_blocks(content: bytes, filename: str) -> list[dict]:
    from .scheme_service import fail

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 50 * 1024 * 1024:
                fail("文档解压后超过50MB限制")
            if filename.lower().endswith(".docx"):
                root = ET.fromstring(archive.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

                def text(node):
                    return "".join(t.text or "" for t in node.findall(".//w:t", ns))

                body = root.find("w:body", ns)
                blocks = []
                for index, element in enumerate(body if body is not None else [], 1):
                    if element.tag.endswith("}p"):
                        blocks.append({"source": f"正文段落{index}", "text": text(element)})
                    elif element.tag.endswith("}tbl"):
                        blocks.append(
                            {
                                "source": f"正文表格{index}",
                                "rows": [
                                    [text(cell) for cell in row.findall("w:tc", ns)]
                                    for row in element.findall("w:tr", ns)
                                ],
                            }
                        )
                return blocks
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            blocks = []
            for ws in wb.worksheets:
                rows = []
                for row in ws.iter_rows():
                    if len(rows) >= 5000:
                        fail("每张工作表最多5000行")
                    values = []
                    for cell in row:
                        value = cell.value
                        if isinstance(value, (float, int)) and "%" in cell.number_format:
                            value = f"{value * 100:g}%"
                        values.append(str(value) if value is not None else "")
                    rows.append(values)
                blocks.append({"source": ws.title, "rows": rows})
            return blocks
        finally:
            wb.close()
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError, ValueError):
        fail("无法读取文档，请使用有效的docx或xlsx文件")


def parse_document(content: bytes, filename: str, defaults: dict) -> dict | None:
    """Recognize scored tables across departments; retain unsupported content verbatim."""
    blocks = extract_blocks(content, filename)
    metrics, grades, adjustments, issues = [], [], [], []
    appendix = False
    heading = ""
    for block in blocks:
        if "text" in block:
            if block["text"].strip():
                heading = block["text"].strip()
            if "附录" in heading or "附件" in heading:
                appendix = True
            continue
        rows = block.get("rows", [])
        for index, header in enumerate(rows):
            metric_col = next(
                (
                    i
                    for i, v in enumerate(header)
                    if v.strip() in ("考核指标", "指标名称", "考核项目", "指标")
                ),
                None,
            )
            points_col = next(
                (
                    i
                    for i, v in enumerate(header)
                    if v.strip() in ("分值", "满分", "标准分", "分数")
                ),
                None,
            )
            description_col = next(
                (
                    i
                    for i, v in enumerate(header)
                    if v.strip() in ("评分标准", "评分说明", "考核标准", "评价标准", "评分细则")
                ),
                None,
            )
            if metric_col is not None and points_col is not None:
                for row_no, row in enumerate(rows[index + 1 :], index + 2):

                    def value(col, cells=row):
                        return cells[col].strip() if col is not None and col < len(cells) else ""

                    name, raw_points = value(metric_col), value(points_col)
                    if not name or name in ("合计", "总计"):
                        continue
                    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*分?", raw_points)
                    if not match:
                        continue
                    points = float(match[1])
                    existing = next((m for m in metrics if m["name"] == name), None)
                    source = f"{block['source']}第{row_no}行"
                    if existing:
                        if existing["max_score"] != points:
                            issues.append(
                                f"{name}：正文为{existing['max_score']:g}分，{source}为{points:g}分。当前以首次完整评分标准为依据，发布前须核对并确认。"
                            )
                        continue
                    if appendix or description_col is None:
                        continue
                    description = value(description_col)
                    if not description:
                        issues.append(f"{source}：{name}缺少评分说明")
                    metrics.append(
                        {
                            "code": f"KPI_{len(metrics) + 1}",
                            "name": name,
                            "type": "narrative",
                            "weight": points,
                            "max_score": points,
                            "scoring_method": "manager_review",
                            "scoring_description": description,
                            "dimension": heading,
                            "source": source,
                            "rules": [],
                        }
                    )
            if "等级" in header and "总分区间" in header:
                for row in rows[index + 1 :]:
                    raw = dict(zip(header, row, strict=False))
                    if raw.get("等级"):
                        grades.append(
                            {
                                "level": raw["等级"],
                                "condition": raw.get("总分区间", ""),
                                "description": "；".join(
                                    f"{k}：{v}"
                                    for k, v in raw.items()
                                    if k not in ("等级", "总分区间")
                                ),
                                "source": block["source"],
                            }
                        )
            if "加减分" in header:
                adjustments.extend(
                    {"source": block["source"], "cells": row}
                    for row in rows[index + 1 :]
                    if any(row)
                )
    if not metrics:
        return None
    if any(
        str(cell).startswith("=")
        for block in blocks
        for row in block.get("rows", [])
        for cell in row
    ):
        from .scheme_service import fail

        fail("配置不允许使用公式")
    if grades:
        issues.append(
            "等级按原文保留，未自动补齐边界或转换自然语言；主管须结合实际总分填写等级及判定依据。"
        )
    if not grades:
        issues.append("未识别到等级及总分区间，请在原文中核对并配置等级后发布。")
    if adjustments:
        issues.append("识别到加减分条款，请核对加减分上下限；系统不从自然语言猜测封顶值。")
    return {
        "code": defaults.get("scheme_code") or "DOC_" + hashlib.sha256(content).hexdigest()[:12],
        "name": defaults.get("scheme_name") or filename.rsplit(".", 1)[0],
        "output_mode": "narrative",
        "scoring_mode": "direct_points",
        "binding": {
            key: defaults.get(key, "")
            for key in (
                "department_id",
                "position",
                "period_type",
                "effective_from",
                "effective_to",
            )
        },
        "metrics": metrics,
        "grades": [],
        "fields": [],
        "document": {
            "blocks": blocks,
            "grade_rules": grades,
            "adjustment_rules": adjustments,
            "issues": list(dict.fromkeys(issues)),
            "review_confirmed": False,
            "review_note": "",
            "adjustment_min": 0,
            "adjustment_max": 0,
            "grading_mode": "manager_confirmed",
            "base_score": sum(m["max_score"] for m in metrics),
        },
    }
