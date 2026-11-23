"""飞书表格 → 人工指标的**同步服务**。

职责
----
1. **绑定**：HR 把某个剪辑同事的飞书表格链接粘进来 ⇒ 绑到该员工。
2. **解析**：把表格的**一行**解析成「日期 + 指标 + 数值」。
3. **落库**：幂等地写进 ``pan_manual_metric``（来源 ``feishu_table``）。
4. **变更检测**：内容指纹没变 ⇒ **不重复入库**。

⚠️ **表格长什么样（这是给 HR 的模板）**
--------------------------------------------------
第一行是**表头**，一行一条填报：

| 日期 | 完播率 | 视频留存率 | 备注 |
|------|--------|------------|------|
| 2026-11-20 | 35% | 62% | 抖音 |
| 2026-11-21 | 0.28 | 0.55 | 视频号 |

⚠️ **列名是「认」出来的，不是「写死」的**
列名支持别名（见 ``COLUMN_ALIASES``）：
- 日期 ← ``日期`` / ``业务日期`` / ``date`` / ``时间``
- 完播率 ← ``完播率`` / ``完播`` / ``completion_rate`` / ``播放完成率``
- 留存率 ← ``留存率`` / ``视频留存率`` / ``retention_rate``

⚠️ **为什么不用「人名」列**
绑定是**一人一张表** ⇒ 表里每一行都是这个人的 ⇒ 不需要人名列，
也就不会因为「同名 / 改名 / 打字错」把数据算到别人头上。

⚠️ **数值一律归一到 0~1**
- ``35%``  → ``0.35``
- ``0.35`` → ``0.35``
- ``35``   → ``0.35``（裸数字 > 1 一律按百分数）

⚠️ **解析不出来就跳过，绝不猜**
一行的日期认不出、或数值认不出 ⇒ **跳过这行并记下来**，
不落库、不报错中断整批。⇒ 主管看到的是「这行有问题」，
而不是「整个表都同步失败」。

⚠️ **幂等：内容指纹**
整张表算一个 sha256 ⇒ 指纹没变 ⇒ **整批跳过**。
变了 ⇒ 逐行 upsert（同人同天同指标同 target_key ⇒ 覆盖）。
⇒ 重复点「立即同步」**不会**产生重复数据。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.pan_workload_models import (
    PanFeishuMetricSource,
)
from .feishu_table import (
    ALLOWED_TABLE_KINDS,
    KIND_BITABLE,
    KIND_SHEET,
    FeishuTableError,
    FeishuTablePermissionError,
    FeishuTableReader,
    FeishuTableUnavailableError,
)
from .pan_manual_metric_service import (
    METRIC_COMPLETION_RATE,
    METRIC_RETENTION_RATE,
    PanManualMetricError,
    PanManualMetricService,
)

logger = logging.getLogger(__name__)

#: 表格来源标记（写进 ``pan_manual_metric.source``）
SOURCE_FEISHU_TABLE = "feishu_table"

#: 日期列的别名 —— **小写后**比对
DATE_ALIASES = frozenset(
    {"日期", "业务日期", "date", "时间", "publish_date", "发布日期", "统计日期"}
)

#: 完播率列的别名
COMPLETION_ALIASES = frozenset(
    {
        "完播率",
        "完播",
        "播放完成率",
        "完成率",
        "completion_rate",
        "video_completion_rate",
        "完播率(%)",
    }
)

#: 留存率列的别名
RETENTION_ALIASES = frozenset(
    {
        "留存率",
        "视频留存率",
        "留存",
        "retention_rate",
        "video_retention_rate",
        "留存率(%)",
    }
)

#: 备注列的别名
REMARK_ALIASES = frozenset({"备注", "说明", "remark", "note", "作品名", "视频名", "标题"})

#: 日期解析 —— ``2026-11-20`` / ``2026/11/20`` / ``2026年11月20日``
_DATE_RE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")

#: 比率解析 —— ``35%`` / ``0.35`` / ``35``
_RATE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*%?\s*$")

#: 飞书表格链接里取 token 的形态
_LINK_PATTERNS = (
    re.compile(r"/sheets/([A-Za-z0-9]+)", re.IGNORECASE),
    re.compile(r"/bitable/([A-Za-z0-9]+)", re.IGNORECASE),
    re.compile(r"/base/([A-Za-z0-9]+)", re.IGNORECASE),
    re.compile(r"/wiki/([A-Za-z0-9]+)", re.IGNORECASE),
)


class FeishuMetricSyncError(Exception):
    """飞书表格同步业务异常。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ===========================================================================
# 解析（纯函数 —— 好测）
# ===========================================================================


def _norm_header(value: Any) -> str:
    """归一化列名 —— 去空白、转小写。"""
    return str(value or "").strip().lower()


def _match_column(
    headers: dict[str, str], aliases: frozenset[str]
) -> str:
    """在表头里找出匹配别名的**原始列名**。

    ⚠️ **先精确匹配，再去括号后缀**
    「完播率(%)」与「完播率」是两个列名 —— 前者更具体，
    应当优先于后者匹配 ⇒ 所以先试精确，再试「去掉括号」。

    Returns:
        原始列名；找不到返回空串。
    """
    for original, normalized in headers.items():
        if normalized in aliases:
            return original
    for original, normalized in headers.items():
        stripped = re.sub(r"[（(].*?[）)]", "", normalized).strip()
        if stripped in aliases:
            return original
    return ""


def parse_date(value: Any) -> str:
    """从单元格值里解析出 ``YYYY-MM-DD``。

    ⚠️ **认不出返回空串** —— 上层会跳过这行。
    ⚠️ **飞书日期单元格可能是秒级时间戳**（未格式化时）
    ⇒ 这里也认这种形态。
    """
    if value is None:
        return ""
    # ⚠️ **飞书日期列常是数字** —— 有的是秒级时间戳，有的是 Excel 序列号
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        # ⚠️ Excel 序列号（1900 基准）：2026-11-20 ≈ 46319
        if 20000 < number < 80000:
            from datetime import timedelta

            return (date(1899, 12, 30) + timedelta(days=int(number))).isoformat()
        # ⚠️ 秒级时间戳
        if 1_000_000_000 < number < 4_000_000_000:
            return datetime.fromtimestamp(number).date().isoformat()
        return ""
    text = str(value).strip()
    if not text:
        return ""
    match = _DATE_RE.search(text)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_rate(value: Any) -> float | None:
    """从单元格值里解析出比率（0~1）。

    ⚠️ **口径与 ``feishu_manual_metric.parse_rate`` 完全一致**
    ``35%`` → 0.35 / ``0.35`` → 0.35 / ``35`` → 0.35
    ⚠️ **认不出返回 ``None``** —— 绝不猜。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        # ⚠️ **百分比形态的数字**（飞书里「35%」常被读成 0.35 或 35）
        if number > 1.0:
            return round(number / 100.0, 6)
        return round(number, 6)
    text = str(value).strip()
    if not text:
        return None
    match = _RATE_RE.match(text)
    if not match:
        return None
    raw = float(match.group(1))
    if "%" in text:
        return round(raw / 100.0, 6)
    if raw > 1.0:
        return round(raw / 100.0, 6)
    return round(raw, 6)


def _cell_text(value: Any) -> str:
    """把单元格值转成纯文本。

    ⚠️ **多维表格的人员字段是数组**（``[{"id":..., "name":...}]``）
    ⇒ 取第一个的 ``name``。
    ⚠️ **多维表格的附件字段也是数组** ⇒ 取第一个的 ``name``（文件名）。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [_cell_text(item) for item in value]
        return " ".join(part for part in parts if part)
    if isinstance(value, dict):
        # ⚠️ **优先取人名 / 文件名** —— 多维表格的人员与附件字段
        for key in ("name", "text", "value"):
            if value.get(key):
                return _cell_text(value[key])
        return ""
    return str(value).strip()


def parse_row(
    cells: dict[str, Any],
) -> dict[str, Any]:
    """把一行单元格解析成结构化填报。

    ⚠️ **这是纯函数** —— 不碰数据库 ⇒ 好测。

    Returns:
        ``{"ok": True, "date": ..., "metrics": [{"code":..., "value":...}], "remark": ...}``
        或 ``{"ok": False, "reason": "..."}``
    """
    headers = {_norm_header(key): key for key in cells}
    if not headers:
        return {"ok": False, "reason": "整行为空"}

    date_col = _match_column(headers, DATE_ALIASES)
    if not date_col:
        return {"ok": False, "reason": "找不到「日期」列"}

    day = parse_date(cells.get(date_col))
    if not day:
        return {"ok": False, "reason": f"日期认不出：{_cell_text(cells.get(date_col))}"}

    metrics: list[dict[str, Any]] = []
    for code, aliases in (
        (METRIC_COMPLETION_RATE, COMPLETION_ALIASES),
        (METRIC_RETENTION_RATE, RETENTION_ALIASES),
    ):
        col = _match_column(headers, aliases)
        if not col:
            continue
        raw = cells.get(col)
        if not _cell_text(raw):
            # ⚠️ **空单元格 = 这项没填** ⇒ 不是错误，只是没数据
            continue
        value = parse_rate(raw)
        if value is None:
            return {
                "ok": False,
                "reason": f"数值认不出（{_cell_text(raw)}）",
                "date": day,
            }
        if not (0.0 <= value <= 1.0):
            return {
                "ok": False,
                "reason": f"数值超出 0~1：{_cell_text(raw)}",
                "date": day,
            }
        metrics.append({"code": code, "value": value})

    if not metrics:
        return {"ok": False, "reason": "没有任何可识别的指标", "date": day}

    remark = ""
    remark_col = _match_column(headers, REMARK_ALIASES)
    if remark_col:
        remark = _cell_text(cells.get(remark_col))[:255]

    return {"ok": True, "date": day, "metrics": metrics, "remark": remark}


def content_hash(rows: list[dict[str, Any]]) -> str:
    """整张表的内容指纹 —— **幂等去重**依据。

    ⚠️ **必须稳定** ⇒ 不能依赖 dict 的插入顺序
    ⇒ 用 ``sort_keys=True`` 序列化。
    ⚠️ **只算业务内容** ⇒ 不含行顺序（员工调整行序不应触发重新入库）。
    """
    normalized = sorted(
        (
            {
                "cells": {
                    str(key): _cell_text(value)
                    for key, value in sorted((row.get("cells") or {}).items())
                }
            }
            for row in rows
        ),
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_source_token(url: str, kind: str) -> str:
    """从飞书链接里取表格 token。

    ⚠️ **多维表格的链接形态是 ``/base/{app_token}?table={table_id}``**
    ⇒ token 在路径里，``table_id`` 在**查询参数**里。
    ⇒ 电子表格是 ``/sheets/{token}?sheet={sheetId}`` ⇒ 同理。
    """
    text = (url or "").strip()
    if not text:
        return ""
    if kind == KIND_BITABLE:
        match = re.search(r"/(?:base|bitable)/([A-Za-z0-9]+)", text, re.IGNORECASE)
    elif kind == KIND_SHEET:
        match = re.search(r"/sheets/([A-Za-z0-9]+)", text, re.IGNORECASE)
    else:
        match = None
    if match:
        return match.group(1)
    # ⚠️ **wiki 链接** —— token 在路径里，但真实表格标识要靠 wiki 节点查
    for pattern in _LINK_PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group(1)
    return ""


def extract_query_param(url: str, name: str) -> str:
    """从链接的**查询参数**里取值（如 ``?sheet=xxx`` / ``?table=xxx``）。"""
    from urllib.parse import parse_qs, urlparse

    text = (url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    values = parse_qs(parsed.query).get(name) or []
    return values[0] if values else ""


# ===========================================================================
# 服务
# ===========================================================================


class FeishuMetricSourceService:
    """飞书表格来源绑定与同步。"""

    def __init__(self, db: Session, reader: FeishuTableReader) -> None:
        self.db = db
        self.reader = reader

    # ─────────────────────────────────────────────────────────────
    # 绑定
    # ─────────────────────────────────────────────────────────────

    def list_sources(self, *, only_active: bool = False) -> list[PanFeishuMetricSource]:
        stmt = select(PanFeishuMetricSource).order_by(
            PanFeishuMetricSource.created_at
        )
        if only_active:
            stmt = stmt.where(PanFeishuMetricSource.active.is_(True))
        return list(self.db.scalars(stmt).all())

    def get(self, source_id: str) -> PanFeishuMetricSource | None:
        return self.db.get(PanFeishuMetricSource, source_id)

    def get_by_employee(self, employee_id: str) -> PanFeishuMetricSource | None:
        return self.db.scalar(
            select(PanFeishuMetricSource).where(
                PanFeishuMetricSource.employee_id == employee_id
            )
        )

    def bind(
        self,
        *,
        employee_id: str,
        source_url: str,
        table_kind: str = KIND_SHEET,
        actor_id: str | None = None,
    ) -> PanFeishuMetricSource:
        """绑定一个员工的飞书表格。

        ⚠️ **绑定前先「探一次」**
        直接存进去 ⇒ HR 填错了链接也要等到同步时才发现。
        ⇒ 绑定时就读一次表头 ⇒ 立刻能验证「链接对不对、表是不是有表头」。

        ⚠️ **一人一张表** ⇒ 重复绑定是**更新**，不是新增。
        """
        if table_kind not in ALLOWED_TABLE_KINDS:
            raise FeishuMetricSyncError(f"不支持的表格类型：{table_kind}")

        from ..domain.models import Employee

        employee = self.db.get(Employee, employee_id)
        if employee is None:
            raise FeishuMetricSyncError("员工不存在", 404)

        url = (source_url or "").strip()
        if not url:
            raise FeishuMetricSyncError("请填写飞书表格链接")

        token = extract_source_token(url, table_kind)
        if not token:
            raise FeishuMetricSyncError(
                "无法识别飞书表格链接，请粘贴形如 /sheets/xxx 或 /base/xxx 的完整链接"
            )

        sheet_id = extract_query_param(url, "sheet") if table_kind == KIND_SHEET else ""
        table_id = extract_query_param(url, "table") if table_kind == KIND_BITABLE else ""

        # ⚠️ **绑定即验证** —— 立刻读一次，确认链接真的能用
        try:
            rows = self.reader.read_rows(
                kind=table_kind,
                obj_token=token,
                sheet_id=sheet_id,
                table_id=table_id,
            )
        except FeishuTablePermissionError as exc:
            raise FeishuMetricSyncError(exc.message, 503) from exc
        except FeishuTableUnavailableError as exc:
            raise FeishuMetricSyncError(exc.message, 422) from exc
        except FeishuTableError as exc:
            raise FeishuMetricSyncError(exc.message, 502) from exc

        if not rows:
            raise FeishuMetricSyncError(
                "表格里没有数据行。请确认第一行是表头（至少含「日期」「完播率」列），"
                "且已填了至少一行数据。"
            )

        headers = list((rows[0].get("cells") or {}).keys())
        if not _match_column({_norm_header(h): h for h in headers}, DATE_ALIASES):
            raise FeishuMetricSyncError(
                f"表格缺少「日期」列。当前表头：{headers}。"
                "请把第一行改成表头，第一列写「日期」。"
            )

        existing = self.get_by_employee(employee_id)
        if existing is None:
            existing = PanFeishuMetricSource(
                employee_id=employee_id,
                table_kind=table_kind,
                obj_token=token,
            )
            self.db.add(existing)
        existing.table_kind = table_kind
        existing.obj_token = token
        existing.sheet_id = sheet_id
        existing.table_id = table_id
        existing.source_url = url
        existing.active = True
        # ⚠️ **换表 ⇒ 指纹作废** —— 否则新表内容恰好同指纹时会跳过同步
        existing.last_content_hash = ""
        existing.last_error = ""
        self.db.flush()

        from ..domain.models import AuditLog

        self.db.add(
            AuditLog(
                actor_id=actor_id,
                action="pan_feishu_metric_source.bind",
                resource_type="pan_feishu_metric_source",
                resource_id=existing.id,
                metadata_json={
                    "employee_id": employee_id,
                    "table_kind": table_kind,
                },
            )
        )
        return existing

    def unbind(self, source_id: str, *, actor_id: str | None = None) -> None:
        """解除绑定（停用，保留痕迹）。"""
        source = self.get(source_id)
        if source is None:
            raise FeishuMetricSyncError("绑定不存在", 404)
        source.active = False

        from ..domain.models import AuditLog

        self.db.add(
            AuditLog(
                actor_id=actor_id,
                action="pan_feishu_metric_source.unbind",
                resource_type="pan_feishu_metric_source",
                resource_id=source_id,
            )
        )
        self.db.flush()

    # ─────────────────────────────────────────────────────────────
    # 同步
    # ─────────────────────────────────────────────────────────────

    def sync_one(self, source: PanFeishuMetricSource) -> dict[str, Any]:
        """同步一个来源。

        ⚠️ **幂等靠内容指纹**
        指纹没变 ⇒ 直接返回「无变化」，**不重跑解析**。
        ⇒ 重复点「立即同步」不会产生重复数据，也不会刷屏告警。

        ⚠️ **解析失败的行不阻断整批**
        逐行解析 ⇒ 坏行进 ``skipped`` 列表 ⇒ 主管能看到
        「哪一行没同步上、为什么」。
        """
        try:
            rows = self.reader.read_rows(
                kind=source.table_kind,
                obj_token=source.obj_token,
                sheet_id=source.sheet_id,
                table_id=source.table_id,
            )
        except FeishuTablePermissionError as exc:
            source.last_error = exc.message
            source.last_synced_at = datetime.now()
            self.db.flush()
            raise FeishuMetricSyncError(exc.message, 503) from exc
        except FeishuTableUnavailableError as exc:
            source.last_error = exc.message
            source.last_synced_at = datetime.now()
            self.db.flush()
            raise FeishuMetricSyncError(exc.message, 422) from exc
        except FeishuTableError as exc:
            source.last_error = exc.message
            source.last_synced_at = datetime.now()
            self.db.flush()
            raise FeishuMetricSyncError(exc.message, 502) from exc

        digest = content_hash(rows)
        if digest and digest == source.last_content_hash:
            source.last_synced_at = datetime.now()
            source.last_error = ""
            self.db.flush()
            return {
                "status": "unchanged",
                "sourceId": source.id,
                "employeeId": source.employee_id,
                "rows": len(rows),
                "written": 0,
                "skipped": [],
            }

        svc = PanManualMetricService(self.db)
        written = 0
        skipped: list[dict[str, Any]] = []

        for index, row in enumerate(rows, start=2):  # ⚠️ 从第 2 行开始（第 1 行是表头）
            parsed = parse_row(row.get("cells") or {})
            if not parsed.get("ok"):
                skipped.append({"row": index, "reason": parsed.get("reason", "解析失败")})
                continue
            for metric in parsed["metrics"]:
                try:
                    svc.submit(
                        employee_id=source.employee_id,
                        metric_date=parsed["date"],
                        metric_code=metric["code"],
                        metric_value=metric["value"],
                        scope="day",
                        source=SOURCE_FEISHU_TABLE,
                        created_by=source.employee_id,
                        remark=parsed.get("remark", "") or "飞书表格同步",
                    )
                except PanManualMetricError as exc:
                    skipped.append({"row": index, "reason": exc.message})
                    continue
                written += 1

        source.last_content_hash = digest
        source.last_row_count = len(rows)
        source.last_synced_at = datetime.now()
        source.last_error = ""
        self.db.flush()

        return {
            "status": "synced",
            "sourceId": source.id,
            "employeeId": source.employee_id,
            "rows": len(rows),
            "written": written,
            "skipped": skipped,
        }

    def sync_all(self, *, only_source_id: str = "") -> dict[str, Any]:
        """同步全部（启用的）来源。

        ⚠️ **一个来源失败不阻断其它来源**
        否则一张表被删了，整个板块就同步不动了。
        """
        if only_source_id:
            source = self.get(only_source_id)
            sources = [source] if source is not None else []
        else:
            sources = self.list_sources(only_active=True)

        results: list[dict[str, Any]] = []
        failed = 0
        for source in sources:
            try:
                results.append(self.sync_one(source))
            except FeishuMetricSyncError as exc:
                failed += 1
                results.append(
                    {
                        "status": "failed",
                        "sourceId": source.id,
                        "employeeId": source.employee_id,
                        "error": exc.message,
                    }
                )
                logger.warning("飞书表格同步失败（source=%s）：%s", source.id, exc.message)

        return {
            "sources": len(sources),
            "succeeded": len(sources) - failed,
            "failed": failed,
            "detail": results,
        }


__all__ = [
    "COMPLETION_ALIASES",
    "DATE_ALIASES",
    "FeishuMetricSourceService",
    "FeishuMetricSyncError",
    "REMARK_ALIASES",
    "RETENTION_ALIASES",
    "SOURCE_FEISHU_TABLE",
    "content_hash",
    "extract_query_param",
    "extract_source_token",
    "parse_date",
    "parse_rate",
    "parse_row",
]
