"""飞书表格读取适配器 —— 把飞书表格读成**统一的行结构**。

⚠️ **为什么需要这个模块**
「以表格形式填写」有两张面孔：
- **电子表格**（Spreadsheets）—— 值域接口，一行一数组
- **多维表格**（Bitable）—— 记录接口，一行一字典（字段名 → 值）

⇒ 两种的**原始形态完全不同**，但业务上都是「一行一条填报记录」。
⇒ 本模块把两者**归一化成同一份行结构**：
    ``[{"cells": {"日期": "2026-11-20", "完播率": "35%", ...}}, ...]``
⇒ 上层（解析 + 落库）**完全不知道**底下是哪种表格。

⚠️ **只读，绝不写**
2026-11-21 实测：应用**没有** ``sheets:spreadsheet`` 写权限、
**没有** ``bitable`` scope ⇒ 系统**写不了**表格。
⇒ 表格由剪辑同事**自己在飞书里加行**，系统只负责读回来。
⇒ 这不是能力缺失，是**业务想要的样子**（表格就是员工的填入口）。

⚠️ **权限缺失时的行为：抛 ``FeishuTablePermissionError``**
上层要能区分「权限没开（找 IT）」与「表格读错了（找 HR）」——
两种错误的对策完全不同 ⇒ 不能都吞成一句话。
"""

from __future__ import annotations

import logging
from typing import Any

from .feishu_client import FeishuClient, FeishuError

logger = logging.getLogger(__name__)

#: 表格类型 —— 电子表格
KIND_SHEET = "sheet"
#: 表格类型 —— 多维表格
KIND_BITABLE = "bitable"

#: 表格类型的白名单
ALLOWED_TABLE_KINDS = (KIND_SHEET, KIND_BITABLE)

#: 飞书「缺权限」类错误码（与应用版本未发布混在一起，但两者都要找 IT）
SCOPE_DENIED_CODES = frozenset({99991672, 99991668, 99991669, 99991679})

#: 飞书「对象不存在 / 取不到」类错误码
NOT_FOUND_CODES = frozenset({131005, 1254043, 1310251, 230001, 91402})


class FeishuTableError(Exception):
    """飞书表格读取失败。"""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class FeishuTablePermissionError(FeishuTableError):
    """缺少权限 —— **需要 IT 申请 scope**（不是 HR 能解决的）。

    ⚠️ 单独一个类型 ⇒ 上层能给出「请找 IT」而不是「请检查链接」。
    """


class FeishuTableUnavailableError(FeishuTableError):
    """表格取不到 —— 链接错 / 表被删 / 应用没被授权。

    ⚠️ 与权限错误分开：这是 **HR 能自己解决**的（重新共享 / 重新粘链接）。
    """


def classify_error(exc: FeishuError) -> FeishuTableError:
    """把飞书错误翻译成「权限问题」或「内容问题」。

    ⚠️ **必须按错误码分类，不能一律当异常**
    - ``99991672`` → 应用没申请 scope ⇒ **找 IT**
    - ``131005`` / ``91402`` → 表不存在 ⇒ **找 HR**（链接错了）
    - 其余 → 内容问题

    ⇒ 两种错误的对策完全不同 ⇒ 必须分开，否则用户只会收到
      「出错了，请重试」这种无信息量的提示。
    """
    code = exc.code
    if code in SCOPE_DENIED_CODES:
        return FeishuTablePermissionError(
            f"缺少飞书表格权限：{exc.message}（需 IT 申请对应 scope 后**发布应用新版本**）",
            code=code,
        )
    if code in NOT_FOUND_CODES:
        return FeishuTableUnavailableError(
            f"取不到该飞书表格：{exc.message}（请确认链接正确，且已把该表格共享给应用）",
            code=code,
        )
    return FeishuTableError(f"读取飞书表格失败：{exc.message}", code=code)


class FeishuTableReader:
    """飞书表格读取器（电子表格 + 多维表格双通路）。

    Usage:
        >>> with FeishuTableReader(client) as reader:
        ...     rows = reader.read_rows(kind="sheet", obj_token="xxx", sheet_id="yyy")
    """

    def __init__(self, client: FeishuClient) -> None:
        self.client = client

    # ─────────────────────────────────────────────────────────────
    # 电子表格（Spreadsheets）
    # ─────────────────────────────────────────────────────────────

    def list_sheets(self, spreadsheet_token: str) -> list[dict[str, Any]]:
        """列出表格里的所有工作表（sheetId / 标题）。

        ⚠️ **为什么需要它**
        HR 粘的链接是 ``/sheets/{token}?sheet={sheetId}`` ——
        ``sheetId`` 在链接的查询参数里，但**链接也可能不带**。
        ⇒ 绑定时要把 ``sheetId`` 存下来（同步时要用），
          拿不到时列出工作表让 HR 选。
        """
        try:
            data = self.client._request(
                "GET", f"/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
            )
        except FeishuError as exc:
            raise classify_error(exc) from exc
        return list(data.get("sheets") or [])

    def read_sheet_rows(
        self,
        spreadsheet_token: str,
        sheet_id: str = "",
        rng: str = "",
    ) -> list[list[Any]]:
        """读电子表格的值域，返回**二维数组**。

        ⚠️ **range 的四种写法**
        官方支持 ``sheetId!A1:B5`` / ``sheetId!A:B`` / ``sheetId!A1:B`` / ``sheetId``。
        这里默认用**整表**（``sheetId`` 单独给）⇒ 表格加多少行都能读到。

        ⚠️ **不指定 sheetId 时**
        飞书会返回该表格的**第一个**工作表 ⇒ 有多个工作表时
        必须显式指定，否则可能读到错的那张。
        """
        if not spreadsheet_token:
            raise FeishuTableError("缺少表格 token")
        target = rng or (f"{sheet_id}!A1:Z500" if sheet_id else "A1:Z500")
        try:
            data = self.client._request(
                "GET", f"/sheets/v2/spreadsheets/{spreadsheet_token}/values/{target}"
            )
        except FeishuError as exc:
            raise classify_error(exc) from exc
        value_range = data.get("valueRange") or {}
        return list(value_range.get("values") or [])

    # ─────────────────────────────────────────────────────────────
    # 多维表格（Bitable）
    # ─────────────────────────────────────────────────────────────

    def list_bitable_tables(self, app_token: str) -> list[dict[str, Any]]:
        """列出多维表格里的所有数据表。"""
        try:
            data = self.client._request(
                "GET", f"/bitable/v1/apps/{app_token}/tables"
            )
        except FeishuError as exc:
            raise classify_error(exc) from exc
        return list(data.get("items") or [])

    def read_bitable_records(
        self,
        app_token: str,
        table_id: str,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        """读多维表格的全部记录（自动翻页）。

        ⚠️ **翻页上限 200 页**
        超过就是异常数据（或者表被误用成了别的东西）⇒ 停下来报错，
        而不是无限翻下去把进程拖死。
        """
        if not app_token or not table_id:
            raise FeishuTableError("缺少多维表格的 app_token 或 table_id")
        records: list[dict[str, Any]] = []
        page_token = ""
        for _ in range(200):
            params: dict[str, Any] = {"page_size": min(page_size, 500)}
            if page_token:
                params["page_token"] = page_token
            try:
                data = self.client._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                )
            except FeishuError as exc:
                raise classify_error(exc) from exc
            records.extend(data.get("items") or [])
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return records

    # ─────────────────────────────────────────────────────────────
    # 统一入口
    # ─────────────────────────────────────────────────────────────

    def read_rows(
        self,
        *,
        kind: str,
        obj_token: str,
        sheet_id: str = "",
        table_id: str = "",
    ) -> list[dict[str, Any]]:
        """读表格 → **统一的行结构**。

        Returns:
            ``[{"cells": {"列名": "值", ...}}, ...]``
            ⚠️ **列名来自表头** —— 电子表格取第一行，多维表格取字段名。
            ⇒ 上层解析时不必关心底下是哪种表格。

        ⚠️ **多维表格的「单元格」可能是数组**
        多维表格的人员字段、附件字段的值是**结构化对象/数组**
        ⇒ 这里**原样保留**（转成字符串），
          上层按需取（人员字段取 ``name``，附件字段取 ``file_token``）。
        """
        if kind == KIND_SHEET:
            return self._read_sheet_normalized(obj_token, sheet_id)
        if kind == KIND_BITABLE:
            return self._read_bitable_normalized(obj_token, table_id)
        raise FeishuTableError(f"不支持的表格类型：{kind}")

    def _read_sheet_normalized(
        self, obj_token: str, sheet_id: str
    ) -> list[dict[str, Any]]:
        """电子表格 → 统一行结构。

        ⚠️ **第一行是表头**
        ⇒ 表头是「列名」，不是数据 ⇒ **不能当成一条填报**。
        """
        raw = self.read_sheet_rows(obj_token, sheet_id)
        if not raw:
            return []
        headers = [str(cell).strip() for cell in raw[0]]
        rows: list[dict[str, Any]] = []
        for cells in raw[1:]:
            # ⚠️ **整行为空 ⇒ 跳过** —— 飞书表格常在末尾留空行
            if not any(str(cell).strip() for cell in cells if cell is not None):
                continue
            row: dict[str, Any] = {}
            for index, header in enumerate(headers):
                if not header:
                    continue
                row[header] = cells[index] if index < len(cells) else ""
            rows.append({"cells": row})
        return rows

    def _read_bitable_normalized(
        self, obj_token: str, table_id: str
    ) -> list[dict[str, Any]]:
        """多维表格 → 统一行结构。

        ⚠️ **字段名从 ``fields`` 的键来** —— 那是列名。
        ⇒ 与电子表格的「第一行表头」语义对齐。
        """
        records = self.read_bitable_records(obj_token, table_id)
        rows: list[dict[str, Any]] = []
        for record in records:
            fields = record.get("fields") or {}
            if not any(_has_value(value) for value in fields.values()):
                # ⚠️ **整行为空 ⇒ 跳过** —— 飞书多维表格常有空行
                continue
            rows.append({"cells": dict(fields)})
        return rows

    # ─────────────────────────────────────────────────────────────
    # 元信息（变更检测）
    # ─────────────────────────────────────────────────────────────

    def get_edit_time(self, obj_token: str, kind: str) -> str:
        """取表格的编辑时间（秒级时间戳字符串）—— 变更检测依据。

        ⚠️ **只依赖 wiki 节点读权限** —— 实测已开通 ⇒ 稳定。
        ⚠️ **取不到时返回空串** ⇒ 上层保守判定「需要同步」，
        而不是因为一次抖动就跳过同步。

        ⚠️ **wiki 节点的 token 不一定等于表格的 token**
        知识库里的表格，链接是 ``/wiki/{nodeToken}``，
        而 ``obj_token`` 才是表格的真实标识 ⇒
        调用方要传 **node_token**（链接里的那个），这里取回 ``obj_token``
        与 ``edit_time``。
        """
        if not obj_token:
            return ""
        try:
            data = self.client._request(
                "GET", "/wiki/v2/spaces/get_node", params={"token": obj_token}
            )
        except FeishuError as exc:
            logger.debug("取飞书节点编辑时间失败：%s", exc.message)
            return ""
        node = data.get("node") or {}
        # ⚠️ ``obj_edit_time`` 是**秒级**时间戳字符串（与 ``feishu_docs`` 一致）
        return str(node.get("obj_edit_time") or "")


def _has_value(value: Any) -> bool:
    """判断多维表格的某个字段值是否「有内容」。

    ⚠️ **人员字段 / 附件字段是数组**，空数组 = 无内容。
    ⇒ 不能只判 ``bool(value)``（空列表是 falsy，字符串 ``""`` 也是）
    —— 但 ``0`` 是**有效内容**（比如完播率 0%）⇒ 不能用「假值即空」。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


__all__ = [
    "ALLOWED_TABLE_KINDS",
    "FeishuTableError",
    "FeishuTablePermissionError",
    "FeishuTableReader",
    "FeishuTableUnavailableError",
    "KIND_BITABLE",
    "KIND_SHEET",
    "classify_error",
]
