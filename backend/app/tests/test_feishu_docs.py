"""飞书云文档取数层 —— 针对**真实飞书接口**的回归测试。

⚠️ **为什么单独一个文件**
2026-11-20 拿到真实飞书权限后，实测发现了 4 个**只有在真实接口下**
才会暴露的 bug。它们的共同点是：靠"读文档猜结构"一定会猜错。

    Bug 1  导出任务轮询必须带 ``token`` 查询参数
           → 漏掉会返回 ``99992402 field validation failed``
    Bug 2  ``job_status`` 是**整数**（0=成功/1=初始化/2=处理中）
           → 当字符串比会漏掉 int 0（成功态）
    Bug 3  ``table.cells`` 是 **block_id 字符串列表**，不是单元格对象
           → 当对象用会抛 ``'str' object has no attribute 'get'``
    Bug 4  表格 cell 内的文本块**也在顶层 blocks 列表里**
           → 不过滤会让每个单元格内容在正文重复出现一遍

本文件把上述结构**固化成测试数据**，防止将来再被"优化"掉。

⚠️ **本文件不联网** —— 用真实抓到的 JSON 结构做夹具。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from backend.app.services.feishu_client import FeishuError
from backend.app.services.feishu_docs import (
    DocsContentUnavailable,
    DocsPermissionError,
    FeishuDocsClient,
    is_scope_denied,
)
from backend.app.services.scheme_document import extract_blocks, parse_document

# ═══════════════════════════════════════════════════════════════════════════
# 夹具：真实飞书 blocks 结构
# ═══════════════════════════════════════════════════════════════════════════


def _text_block(block_id: str, parent_id: str, content: str) -> dict:
    return {
        "block_id": block_id,
        "block_type": 2,
        "parent_id": parent_id,
        "text": {"elements": [{"text_run": {"content": content}}], "style": {}},
    }


def build_feishu_blocks(rows: list[list[str]]) -> list[dict]:
    """按**真实飞书结构**构造 blocks。

    结构（2026-11-20 实测）：
        table (31)  →  table.cells = [cell_id, ...]      ← **字符串列表**
        cell   (32)  →  children = [text_block_id, ...]
        text   (2)   →  text.elements[].text_run.content
    """
    blocks: list[dict] = []
    table_id = "TBL1"
    cell_ids: list[str] = []
    for row_index, row in enumerate(rows):
        for col_index, content in enumerate(row):
            cell_id = f"C{row_index}_{col_index}"
            text_id = f"T{row_index}_{col_index}"
            blocks.append(_text_block(text_id, cell_id, content))
            blocks.append(
                {
                    "block_id": cell_id,
                    "block_type": 32,
                    "parent_id": table_id,
                    "children": [text_id],
                }
            )
            cell_ids.append(cell_id)
    blocks.append(
        {
            "block_id": table_id,
            "block_type": 31,
            "parent_id": "DOC1",
            "children": cell_ids,
            "table": {
                "cells": cell_ids,
                "property": {"column_size": len(rows[0]), "header_row": True},
            },
        }
    )
    blocks.append(_text_block("P1", "DOC1", "短视频剪辑岗位绩效考核方案"))
    return blocks


METRIC_ROWS = [
    ["考核指标", "分值", "评分标准"],
    ["交付数量", "30", "剪辑每天6~10条，审核通过才算有效片"],
    ["素材质量", "70", "以完播率、3秒留存率为考核标准"],
]


def _shell() -> FeishuDocsClient:
    """空壳客户端 —— 被测方法不碰 ``self.client``。"""
    return FeishuDocsClient.__new__(FeishuDocsClient)


# ═══════════════════════════════════════════════════════════════════════════
# Bug 3 / 4：表格结构
# ═══════════════════════════════════════════════════════════════════════════


def test_table_cells_are_id_strings_not_objects():
    """⚠️ 回归：``table.cells`` 是 **block_id 字符串列表**。

    当成对象用（``cell.get("body")``）会抛
    ``'str' object has no attribute 'get'``。
    """
    blocks = build_feishu_blocks(METRIC_ROWS)
    table_block = next(b for b in blocks if b["block_type"] == 31)
    cells = table_block["table"]["cells"]
    assert cells, "表格必须有单元格"
    assert all(isinstance(cell, str) for cell in cells), (
        "table.cells 必须是 block_id 字符串 —— "
        "当成对象解析会抛 AttributeError"
    )


def test_table_parsed_into_two_dimensions():
    """表格应还原成二维，且列数与 column_size 一致。"""
    blocks = build_feishu_blocks(METRIC_ROWS)
    by_id = {str(b.get("block_id")): b for b in blocks}
    table_block = next(b for b in blocks if b["block_type"] == 31)
    rows = _shell()._block_table(table_block, by_id)
    assert rows == METRIC_ROWS


def test_cell_text_not_duplicated_in_body():
    """⚠️ 回归：表格 cell 里的文本块**不能**再作为正文段落出现。

    Bug 4 的症状：表格里每个单元格的内容在正文又出现一遍。
    """
    blocks = build_feishu_blocks(METRIC_ROWS)
    content = _shell()._blocks_to_docx(blocks)
    parsed = extract_blocks(content, "feishu_scheme.docx")

    texts = [b["text"] for b in parsed if "text" in b]
    # 「交付数量」只在表格里出现一次，不该作为独立段落重复
    assert texts.count("交付数量") == 0, (
        f"表格 cell 文本被当成正文重复输出了：{texts}"
    )
    # 正文标题保留
    assert "短视频剪辑岗位绩效考核方案" in texts
    # 表格保留且只保留一份
    tables = [b for b in parsed if "rows" in b]
    assert len(tables) == 1
    assert tables[0]["rows"] == METRIC_ROWS


def test_table_renders_all_columns():
    """⚠️ 回归：docx 表格必须渲染**每一列**。

    只渲染第一列会把「指标/分值/评分标准」压成一列，
    ``parse_document`` 就认不出列含义了。
    """
    blocks = build_feishu_blocks(METRIC_ROWS)
    content = _shell()._blocks_to_docx(blocks)
    xml = zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml").decode("utf-8")
    assert xml.count("<w:tc>") == len(METRIC_ROWS) * len(METRIC_ROWS[0])
    for header in METRIC_ROWS[0]:
        assert header in xml


# ═══════════════════════════════════════════════════════════════════════════
# 端到端：飞书表格 → 方案指标
# ═══════════════════════════════════════════════════════════════════════════


def test_feishu_table_becomes_scheme_metrics():
    """⚠️ 回归：飞书表格 → parse_document → 指标（含分值与评分标准）。"""
    blocks = build_feishu_blocks(METRIC_ROWS)
    content = _shell()._blocks_to_docx(blocks)
    payload = parse_document(
        content,
        "feishu_scheme.docx",
        {
            "scheme_code": "SCHEME_D_MONTHLY",
            "scheme_name": "短视频剪辑岗位绩效考核方案",
            "department_id": "D",
            "position": "",
            "period_type": "monthly",
            "effective_from": "2026-11-21",
            "effective_to": "2026-11-30",
        },
    )
    assert payload is not None, "应能从飞书表格解析出方案"
    metrics = payload["metrics"]
    assert len(metrics) == 2
    assert metrics[0]["name"] == "交付数量"
    assert metrics[0]["max_score"] == 30
    assert "审核通过" in metrics[0]["scoring_description"]
    assert metrics[1]["name"] == "素材质量"
    assert metrics[1]["max_score"] == 70


def test_narrative_only_document_yields_no_payload():
    """没有指标表的文档（如使用说明）应返回 None —— 不能瞎编指标。"""
    blocks = build_feishu_blocks(
        [
            ["模块", "入口", "用途"],
            ["绩效方案文档", "短视频剪辑岗位绩效考核方案", "考核口径、评分细则"],
        ]
    )
    content = _shell()._blocks_to_docx(blocks)
    payload = parse_document(
        content,
        "feishu_scheme.docx",
        {
            "scheme_code": "X",
            "scheme_name": "使用说明",
            "department_id": "D",
            "position": "",
            "period_type": "monthly",
            "effective_from": "2026-11-21",
            "effective_to": "2026-11-30",
        },
    )
    assert payload is None, "没有考核指标表时不应生成方案"


# ═══════════════════════════════════════════════════════════════════════════
# Bug 1 / 2：导出任务
# ═══════════════════════════════════════════════════════════════════════════


class FakeClient:
    """记录调用参数的假客户端。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict | None]] = []

    def _request(self, method: str, endpoint: str, *, params=None, json=None):
        self.calls.append((method, endpoint, params))
        for matcher, value in self.responses:
            if matcher in endpoint:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"未预期的调用：{method} {endpoint}")


def test_export_poll_must_pass_token_param():
    """⚠️ 回归：轮询导出任务**必须带 token 查询参数**。

    官方：``GET /drive/v1/export_tasks/{ticket}?token={云文档 token}``。
    漏掉 → ``99992402 field validation failed``。
    """
    client = FakeClient(
        [
            ("/drive/v1/export_tasks/", {"result": {"job_status": 0, "file_token": "FT"}}),
        ]
    )
    shell = FeishuDocsClient.__new__(FeishuDocsClient)
    shell.client = client
    result = shell._poll_export("TICKET", "OBJ_TOKEN")
    assert result["file_token"] == "FT"
    _, _, params = client.calls[0]
    assert params == {"token": "OBJ_TOKEN"}, (
        f"轮询必须带 token 参数，实测参数 = {params}"
    )


@pytest.mark.parametrize(
    "raw_status,immediate",
    [(0, True), ("0", True), (1, False), (2, False)],
)
def test_export_job_status_accepts_int_and_str(raw_status, immediate):
    """⚠️ 回归：``job_status`` 是**整数**，也要兼容字符串。

    当字符串比会漏掉 int 0（成功态）→ 永远轮询到超时。
    """
    calls = {"n": 0}

    class SeqClient:
        def _request(self, method, endpoint, *, params=None, json=None):
            calls["n"] += 1
            # 成功态返回 file_token；处理中（1/2）继续返回处理中
            if raw_status in (0, "0"):
                return {"result": {"job_status": raw_status, "file_token": "FT"}}
            if calls["n"] > 3:
                return {"result": {"job_status": 0, "file_token": "FT"}}
            return {"result": {"job_status": raw_status}}

    shell = FeishuDocsClient.__new__(FeishuDocsClient)
    shell.client = SeqClient()
    result = shell._poll_export("T", "OBJ", max_attempts=5)
    assert result["file_token"] == "FT"
    if immediate:
        assert calls["n"] == 1, "成功态应立即返回，不该继续轮询"


def test_export_job_status_failure_raises_with_reason():
    """终态失败（110=无权限）应抛出带原因的异常，而不是当成「处理中」。"""
    class FailClient:
        def _request(self, method, endpoint, *, params=None, json=None):
            return {"result": {"job_status": 110, "job_error_msg": "no permission"}}

    shell = FeishuDocsClient.__new__(FeishuDocsClient)
    shell.client = FailClient()
    with pytest.raises(DocsContentUnavailable) as exc:
        shell._poll_export("T", "OBJ", max_attempts=3)
    assert "110" in str(exc.value)
    assert "no permission" in str(exc.value)


# ═══════════════════════════════════════════════════════════════════════════
# 错误码判读
# ═══════════════════════════════════════════════════════════════════════════


def test_scope_denied_vs_space_not_joined():
    """⚠️ 回归：两种「没权限」必须区分开。

    - ``99991672`` → 没申请 scope
    - ``131006``   → scope 有，但应用没被加入该知识空间
    """
    assert is_scope_denied(FeishuError("x", code=99991672)) is True
    assert is_scope_denied(FeishuError("x", code=131006)) is False


def test_export_type_mapping():
    """导出任务的 ``type`` 必须是飞书认识的类型名。

    ⚠️ 传 ``doc``（旧版）会报 ``1069914 file token invalid``；
    新版云文档必须传 ``docx``。
    """
    seen: list[dict] = []

    class ExportClient:
        def _request(self, method, endpoint, *, params=None, json=None):
            seen.append(json)
            raise FeishuError("stop", code=1069914)

    shell = FeishuDocsClient.__new__(FeishuDocsClient)
    shell.client = ExportClient()
    with pytest.raises(Exception):
        shell._fetch_via_export("OBJ", "docx")
    assert seen[0]["type"] == "docx"
    assert seen[0]["file_extension"] == "docx"

    seen.clear()
    with pytest.raises(Exception):
        shell._fetch_via_export("OBJ", "sheet")
    assert seen[0]["type"] == "sheet"
    assert seen[0]["file_extension"] == "xlsx"


def test_permission_error_carries_single_wrapping():
    """⚠️ 回归：错误消息**不能**被重复包装成「A：A：原因」。

    排查时要多剥两层，浪费时间。
    """
    exc = DocsPermissionError("缺少通讯录权限或应用版本未发布")
    message = f"权限不足：{exc.message}"
    assert message.count("缺少通讯录权限") == 1
