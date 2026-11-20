"""飞书云文档客户端（方案自动同步的取数层）。

⚠️ 为什么这个模块要「权限自适应」
--------------------------------
2026-11-20 **实测**（`scripts/probe_feishu_docs.py`）本应用当前可用能力：

    ✅ GET /wiki/v2/spaces/get_node
         → 返回 obj_token / obj_type / title / **obj_edit_time**
         ⇒ 变更检测可用

    ❌ GET /wiki/v2/spaces/{id}/nodes   → 缺 wiki:wiki:readonly
    ❌ GET /docx/v1/documents/{id}/...   → 缺 docx:document:readonly
    ❌ GET /drive/v1/files                → 缺 drive:drive:readonly
    ❌ POST /drive/v1/export_tasks        → 缺 drive:export:readonly
    ❌ GET /sheets/v2/...                 → 缺 sheets:spreadsheet:readonly
    ✅ GET /im/v1/messages/{id}/resources/{type}   （机器人收文件）

⇒ 所以本模块提供**两条取数路径**，并按可用性自动选择：

    路径 A（首选）：``fetch_document_bytes``
        docx → 导出任务下载 / 读 blocks 自行拼装 docx
        sheet → 读 values 生成 xlsx
    路径 B（降级）：``fetch_message_resource``
        从**机器人消息**里取文件（HR 把文件发给机器人）

⚠️ **不要在这里猜权限是否存在** —— 权限开通与否随时会变（IT 加了 scope
就能用）。因此每次调用都**真实试一次**，失败时按错误码降级，
而不是靠配置开关。

错误码判读（来自实测返回的飞书原文）：
    99991672 → 缺应用身份权限（scope 未开通）
    99991663/99991664 → token 失效
    131005   → 对象不存在
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import Any

from ..core.config import settings
from .feishu_client import FeishuClient, FeishuError

logger = logging.getLogger(__name__)

#: 缺应用身份权限的错误码 —— 触发降级路径。
SCOPE_DENIED_CODES = frozenset({99991672, 99991668, 99991679})

#: 已知对象类型
OBJ_TYPE_DOCX = "docx"
OBJ_TYPE_SHEET = "sheet"
OBJ_TYPE_FILE = "file"


class DocsPermissionError(FeishuError):
    """云文档读取所需权限未开通。"""


class DocsContentUnavailable(FeishuError):
    """文档存在但正文取不到（对象被删 / 无协作权限 / 类型不支持）。"""


def _is_scope_denied(exc: FeishuError) -> bool:
    return exc.code in SCOPE_DENIED_CODES


class FeishuDocsClient:
    """飞书云文档读取客户端（封装在 ``FeishuClient`` 之上）。"""

    def __init__(self, client: FeishuClient) -> None:
        self.client = client

    # ─────────────────────────────────────────────────────────────
    # 节点元信息（变更检测的入口）
    # ─────────────────────────────────────────────────────────────

    def get_node(self, node_token: str) -> dict[str, Any]:
        """读 wiki 节点元信息。

        Returns:
            ``{node_token, obj_token, obj_type, title, space_id, edit_time}``

        ⚠️ **这个接口实测可用**（只需基础 wiki 读权限），
        所以**变更检测不依赖正文权限** —— 权限没开通时也能判断
        「文档变了没有」，只是无法拉取新内容。
        """
        if not node_token:
            raise DocsContentUnavailable("缺少飞书文档标识")
        try:
            data = self.client._request(
                "GET",
                "/wiki/v2/spaces/get_node",
                params={"token": node_token},
            )
        except FeishuError as exc:
            if _is_scope_denied(exc):
                raise DocsPermissionError(
                    f"读取飞书文档节点失败（缺 wiki 权限）：{exc.message}", code=exc.code
                ) from exc
            raise
        node = data.get("node") or {}
        if not node:
            raise DocsContentUnavailable("飞书文档节点不存在")
        return {
            "node_token": str(node.get("node_token") or node_token),
            "obj_token": str(node.get("obj_token") or ""),
            "obj_type": str(node.get("obj_type") or ""),
            "title": str(node.get("title") or ""),
            "space_id": str(node.get("space_id") or ""),
            # obj_edit_time 是**秒级**时间戳字符串
            "edit_time": str(node.get("obj_edit_time") or ""),
            "node_type": str(node.get("node_type") or ""),
            "has_child": bool(node.get("has_child")),
        }

    # ─────────────────────────────────────────────────────────────
    # 正文读取：多路径 + 自动降级
    # ─────────────────────────────────────────────────────────────

    def fetch_document_bytes(
        self, obj_token: str, obj_type: str
    ) -> tuple[bytes, str]:
        """取文档正文，返回 ``(内容字节, 文件名)``。

        文件名带正确后缀 —— 下游 ``parse_document`` 靠后缀决定
        走 docx 还是 xlsx 解析分支。

        Raises:
            DocsPermissionError: 所需权限未开通。
            DocsContentUnavailable: 文档取不到。
        """
        if obj_type == OBJ_TYPE_SHEET:
            return self._fetch_sheet_as_xlsx(obj_token)
        if obj_type in (OBJ_TYPE_DOCX, OBJ_TYPE_FILE):
            # 优先用「导出任务」：能把云文档转成标准 docx/xlsx，
            # 直接喂给现有的 parse_document。
            try:
                return self._fetch_via_export(obj_token, obj_type)
            except DocsPermissionError:
                logger.info(
                    "导出任务权限未开通，降级为直接读取 docx blocks: obj_token=%s", obj_token
                )
                return self._fetch_docx_via_blocks(obj_token)
            except DocsContentUnavailable:
                return self._fetch_docx_via_blocks(obj_token)
        raise DocsContentUnavailable(f"暂不支持的飞书文档类型：{obj_type}")

    # ── 路径 A1：导出任务 ──────────────────────────────────────

    def _fetch_via_export(self, obj_token: str, obj_type: str) -> tuple[bytes, str]:
        """用 ``drive/v1/export_tasks`` 把云文档导出成文件再下载。"""
        extension = "xlsx" if obj_type == OBJ_TYPE_SHEET else "docx"
        try:
            data = self.client._request(
                "POST",
                "/drive/v1/export_tasks",
                json={
                    "file_extension": extension,
                    "token": obj_token,
                    "type": obj_type,
                },
            )
        except FeishuError as exc:
            # ⚠️ 只有「缺权限」才降级；参数错误 / 其它业务失败直接上抛，
            #    否则会掩盖真正的问题（降级后报的错是另一个错）。
            if _is_scope_denied(exc):
                raise DocsPermissionError(
                    f"导出飞书文档失败（缺 drive:export 权限）：{exc.message}",
                    code=exc.code,
                ) from exc
            raise DocsContentUnavailable(f"导出飞书文档失败：{exc.message}") from exc
        ticket = str(data.get("ticket") or "")
        if not ticket:
            raise DocsContentUnavailable("飞书未返回导出任务票据")

        result = self._poll_export(ticket)
        file_token = str(result.get("file_token") or "")
        if not file_token:
            raise DocsContentUnavailable("飞书导出任务未产出文件")
        content = self.download_drive_file(file_token)
        return content, f"feishu_scheme.{extension}"

    def _poll_export(self, ticket: str, *, max_attempts: int = 10) -> dict[str, Any]:
        """轮询导出任务直到完成。"""
        import time

        for attempt in range(max_attempts):
            try:
                data = self.client._request("GET", f"/drive/v1/export_tasks/{ticket}")
            except FeishuError as exc:
                if _is_scope_denied(exc):
                    raise DocsPermissionError(
                        f"查询导出任务失败（缺 drive 权限）：{exc.message}", code=exc.code
                    ) from exc
                raise
            result = data.get("result") or {}
            state = str(result.get("job_status") or result.get("state") or "")
            if state in ("0", "completed", "success"):
                return result
            if state in ("2", "failed", "cancelled"):
                raise DocsContentUnavailable(
                    f"飞书导出任务失败：{result.get('job_error_msg') or state}"
                )
            time.sleep(1.0 + attempt * 0.5)
        raise DocsContentUnavailable("飞书导出任务超时")

    # ── 路径 A2：docx blocks 自组装 ────────────────────────────

    def _fetch_docx_via_blocks(self, obj_token: str) -> tuple[bytes, str]:
        """直接读 docx blocks，并组装成最小可解析的 docx。

        ⚠️ **为什么需要这个兜底**
        导出任务与 blocks 读是**两套权限**：
        ``drive:export:readonly`` vs ``docx:document:readonly``。
        IT 可能只批了其中一个。两条都通时优先用导出（保真度更高，
        表格结构完整）；blocks 兜底只保证能拿到正文与表格文本。
        """
        # ⚠️ **不再重复包装错误** —— ``_list_docx_blocks`` 已经把
        # 「缺权限」与「取不到」区分好了。这里再包一层会让错误消息
        # 变成「A：A：真正的原因」，排查时要多剥两层。
        blocks = self._list_docx_blocks(obj_token)

        if not blocks:
            raise DocsContentUnavailable("飞书文档没有可解析的正文块")
        return self._blocks_to_docx(blocks), "feishu_scheme.docx"

    def _list_docx_blocks(self, obj_token: str) -> list[dict[str, Any]]:
        """列出文档全部块（含子块）。"""
        blocks: list[dict[str, Any]] = []
        page_token = ""
        for _ in range(50):  # 上限防止异常分页打死循环
            params: dict[str, Any] = {"page_size": 500, "document_revision_id": -1}
            if page_token:
                params["page_token"] = page_token
            try:
                data = self.client._request(
                    "GET", f"/docx/v1/documents/{obj_token}/blocks", params=params
                )
            except FeishuError as exc:
                if _is_scope_denied(exc):
                    raise DocsPermissionError(
                        f"读取飞书文档正文失败（缺 docx 权限）：{exc.message}",
                        code=exc.code,
                    ) from exc
                raise
            blocks.extend(data.get("items") or [])
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return blocks

    @staticmethod
    def _block_text(block: dict[str, Any]) -> str:
        """取块里的纯文本。"""
        text = block.get("text") or {}
        elements = text.get("elements") or []
        parts: list[str] = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            run = element.get("text_run") or {}
            content = str(run.get("content") or "")
            if content:
                parts.append(content)
        return "".join(parts).strip()

    @staticmethod
    def _block_table(block: dict[str, Any]) -> list[list[str]]:
        """取表格块的二维文本。

        ⚠️ 飞书表格块用**嵌套**结构：
        ``table.cells[].body[].elements[].text_run.content``
        —— 中间隔了两层，不能只取一层。
        """
        table = block.get("table") or {}
        cells = table.get("cells") or []
        rows: list[list[str]] = []
        for cell in cells:
            body = cell.get("body") or {}
            elements = body.get("elements") or []
            parts: list[str] = []
            for element in elements:
                if not isinstance(element, dict):
                    continue
                run = element.get("text_run") or {}
                content = str(run.get("content") or "")
                if content:
                    parts.append(content)
            rows.append(["".join(parts).strip()])
        return rows

    def _blocks_to_docx(self, blocks: list[dict[str, Any]]) -> bytes:
        """把 blocks 组装成最小可解析 docx。

        ⚠️ 只保留**正文段落**与**表格文本** —— 不重建样式、不重建图片。
        目的只有一个：让 ``scheme_document.parse_document`` 能识别
        「考核指标 / 分值 / 评分标准」表头，走既有的解析链路。
        """
        body_parts: list[str] = []
        for block in blocks:
            block_type = int(block.get("block_type") or 0)
            if block_type == 31:  # table
                rows = self._block_table(block)
                if not rows:
                    continue
                body_parts.append(self._docx_table_xml(rows))
            elif block_type == 2:  # text
                text = self._block_text(block)
                if text:
                    body_parts.append(self._docx_paragraph_xml(text))

        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{''.join(body_parts)}</w:body></w:document>"
        )
        return self._build_docx_package(document_xml)

    @staticmethod
    def _docx_paragraph_xml(text: str) -> str:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'

    @staticmethod
    def _docx_table_xml(rows: list[list[str]]) -> str:
        cells_xml: list[str] = []
        for row in rows:
            cell = row[0] if row else ""
            safe = str(cell).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            cells_xml.append(
                f'<w:tc><w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p></w:tc>'
            )
        return f"<w:tbl><w:tr>{''.join(cells_xml)}</w:tr></w:tbl>"

    @staticmethod
    def _build_docx_package(document_xml: str) -> bytes:
        """打包成最小 docx（zip）。

        ⚠️ Word 对 docx 结构校验较严 —— 必须包含
        ``[Content_Types].xml`` / ``_rels/.rels`` / ``word/_rels/document.xml.rels``
        三个部件，否则 Word 打不开。
        """
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        )
        root_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        )
        document_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("word/_rels/document.xml.rels", document_rels)
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    # ── 路径 A3：sheets → xlsx ─────────────────────────────────

    def _fetch_sheet_as_xlsx(self, obj_token: str) -> tuple[bytes, str]:
        """读飞书表格的值域，生成 xlsx。

        ⚠️ 表格的**合并单元格、公式、样式**在 values 接口里丢失。
        绩效方案关心的是「指标 / 分值 / 评分标准」的文本，
        值域足够 —— 真正的保真导出走 ``export_tasks``。
        """
        from openpyxl import Workbook

        try:
            rows = self._read_sheet_values(obj_token)
        except FeishuError as exc:
            if _is_scope_denied(exc):
                raise DocsPermissionError(
                    f"读取飞书表格失败（缺 sheets 权限）：{exc.message}", code=exc.code
                ) from exc
            raise DocsContentUnavailable(f"读取飞书表格失败：{exc.message}") from exc
        if not rows:
            raise DocsContentUnavailable("飞书表格没有数据")

        wb = Workbook()
        ws = wb.active
        ws.title = "指标明细"
        for row in rows:
            ws.append([str(v) if v is not None else "" for v in row])
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue(), "feishu_scheme.xlsx"

    def _read_sheet_values(self, obj_token: str, rng: str = "A1:H200") -> list[list[Any]]:
        """读表格值域。"""
        try:
            data = self.client._request(
                "GET", f"/sheets/v2/spreadsheets/{obj_token}/values/{rng}"
            )
        except FeishuError as exc:
            if _is_scope_denied(exc):
                raise DocsPermissionError(
                    f"读取飞书表格失败（缺 sheets 权限）：{exc.message}", code=exc.code
                ) from exc
            raise
        value_range = data.get("valueRange") or {}
        return list(value_range.get("values") or [])

    # ── 路径 A4：drive 文件下载 ─────────────────────────────────

    def download_drive_file(self, file_token: str) -> bytes:
        """下载云空间文件内容。"""
        try:
            resp = self.client._http_client.get(
                f"/drive/v1/files/{file_token}/download",
                headers={"Authorization": f"Bearer {self.client._get_access_token()}"},
                timeout=60,
            )
        except Exception as exc:  # pragma: no cover - 网络异常
            raise DocsContentUnavailable(f"下载飞书文件失败：{exc}") from exc
        if resp.status_code >= 400:
            raise DocsPermissionError(
                f"下载飞书文件失败（HTTP {resp.status_code}），可能缺 drive 权限"
            )
        return resp.content

    # ─────────────────────────────────────────────────────────────
    # 路径 B：机器人消息里的文件（降级路径）
    # ─────────────────────────────────────────────────────────────

    def fetch_message_resource(
        self, message_id: str, file_key: str, resource_type: str = "file"
    ) -> tuple[bytes, str]:
        """从**机器人消息**里取文件。

        降级路径：HR 在飞书里把方案文件**直接发给机器人**，
        系统据此落库并解析 —— 完全不依赖 wiki/drive/sheets 权限。

        官方端点：``GET /im/v1/messages/{message_id}/resources/{resource_type}?file_key=``
        """
        if not message_id or not file_key:
            raise DocsContentUnavailable("缺少消息 ID 或文件标识")
        try:
            resp = self.client._http_client.get(
                f"/im/v1/messages/{message_id}/resources/{resource_type}",
                params={"file_key": file_key},
                headers={"Authorization": f"Bearer {self.client._get_access_token()}"},
                timeout=60,
            )
        except Exception as exc:  # pragma: no cover - 网络异常
            raise DocsContentUnavailable(f"获取飞书消息文件失败：{exc}") from exc
        if resp.status_code >= 400:
            raise DocsPermissionError(
                f"获取飞书消息文件失败（HTTP {resp.status_code}），可能缺 im 资源权限"
            )
        name = str(resp.headers.get("content-disposition") or "")
        filename = ""
        match = re.search(r'filename="?([^";]+)"?', name)
        if match:
            filename = match.group(1)
        return resp.content, filename or "feishu_upload.bin"


def build_docs_client() -> FeishuDocsClient:
    """按配置构造云文档客户端。"""
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise DocsPermissionError("飞书应用配置未完成")
    return FeishuDocsClient(
        FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
    )


__all__ = [
    "DocsContentUnavailable",
    "DocsPermissionError",
    "FeishuDocsClient",
    "build_docs_client",
]
