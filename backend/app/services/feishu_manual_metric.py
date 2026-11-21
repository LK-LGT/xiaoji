"""飞书端 —— 网盘剪辑工作量的数据填报（BP-2 / P3）。

两条通路
--------
**A. 发消息触发**（员工主动）
    员工给机器人发「填报表数据」/「完播率」等 ⇒ 机器人回一张**填报卡片**
    ⇒ 员工在卡片里填 ⇒ 提交。

**B. 直接发图**（最自然）
    员工把**数据截图**直接发进聊天 ⇒ 机器人把它当作**证据附件**存下来，
    并提示「已收到截图，请再发文字说明数值」。

    ⚠️ **为什么不自动 OCR 数值**
    截图里的数字识别错了就是**错误绩效数据**。
    ⇒ **数值必须由员工自己填**，截图只作**证据**。
    ⇒ 这与框架「AI 只提供建议、判定权在人」的口径一致。

⚠️ **为什么飞书端也要能上传**
剪辑同事天天用飞书，让他们为一个绩效数字专门开浏览器 —— 不现实。
⇒ 飞书是**主通路**，前端是补充。

⚠️ **归属校验（约束 C3）**
必须确认「点按钮的人」有权报这个人的数据 —— 否则 B 就能替 A 填。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from .pan_manual_metric_service import (
    METRIC_COMPLETION_RATE,
    METRIC_RETENTION_RATE,
    SOURCE_FEISHU,
    PanManualMetricError,
    PanManualMetricService,
)

logger = logging.getLogger(__name__)

#: 飞书消息里的资源类型
RESOURCE_IMAGE = "image"
RESOURCE_FILE = "file"

#: ⚠️ **数值解析的容错**
#: 员工可能写「35%」「0.35」「35」「35.5%」
#: ⇒ 统一归一到 0~1。
_RATE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*%?\s*$")

#: 指标别名 → 指标码
METRIC_ALIASES: dict[str, str] = {
    "完播率": METRIC_COMPLETION_RATE,
    "完播": METRIC_COMPLETION_RATE,
    "播放完成率": METRIC_COMPLETION_RATE,
    "留存率": METRIC_RETENTION_RATE,
    "留存": METRIC_RETENTION_RATE,
}


class FeishuManualMetricError(Exception):
    """飞书端填报业务异常。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def parse_rate(text: str) -> float | None:
    """从文本里解析出一个比率（0~1）。

    ⚠️ **只接受明确的数值**
    解析不出就返回 ``None`` —— **绝不猜**。
    猜错就是错误绩效数据。

    支持：
        ``35%``  → 0.35
        ``0.35`` → 0.35
        ``35``   → 0.35   （裸数字按百分数理解）
        ``35.5%``→ 0.355
    """
    match = _RATE_RE.match(text or "")
    if not match:
        return None
    raw = float(match.group(1))
    if "%" in (text or ""):
        return round(raw / 100.0, 6)
    # ⚠️ 裸数字：>1 视为百分数，<=1 视为小数
    if raw > 1.0:
        return round(raw / 100.0, 6)
    return round(raw, 6)


def detect_metric_code(text: str) -> str:
    """从文本里认出是哪个指标。

    ⚠️ 认不出就抛错 —— 宁可让员工重发，也不要把完播率当留存率存进去。
    """
    normalized = (text or "").strip()
    for alias, code in METRIC_ALIASES.items():
        if alias in normalized:
            return code
    raise FeishuManualMetricError(
        "没认出是哪个指标。支持：完播率、留存率。"
        "请写成「完播率 35%」这样的格式。"
    )


def extract_date(text: str, *, default: str = "") -> str:
    """从文本里认出日期（YYYY-MM-DD）。

    ⚠️ 认不出就用默认（今天）—— 日期有默认值是合理的
    （「今天剪的那条完播率 35%」很自然），
    但**指标和数值认不出不能有默认值**。
    """
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text or "")
    if match:
        y, m, d = (int(x) for x in match.groups())
        try:
            return date(y, m, d).isoformat()
        except ValueError:
            return default
    match = re.search(r"(\d{1,2})[-/月](\d{1,2})", text or "")
    if match:
        m, d = (int(x) for x in match.groups())
        today = date.today()
        try:
            candidate = date(today.year, m, d)
        except ValueError:
            return default
        # ⚠️ 跨年：月份比今天大 ⇒ 那是去年的
        if candidate > today:
            try:
                candidate = date(today.year - 1, m, d)
            except ValueError:
                return default
        return candidate.isoformat()
    return default


def extract_value(text: str) -> float | None:
    """从文本里认出数值（比率）。

    ⚠️ **三种写法都要认**（与 ``parse_rate`` 口径一致）：
    ``35%``  → 0.35
    ``0.35`` → 0.35
    ``35``   → 0.35   （裸数字按百分数理解）

    ⚠️ **裸数字 > 1 一律按百分数**
    比率类指标的取值范围是 0~1 ⇒ 出现 ``35`` 只能是「35%」。
    """
    # ⚠️ 先找带 % 的（最明确）
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", text or "")
    if match:
        return round(float(match.group(1)) / 100.0, 6)
    # 再找裸小数（0~1）
    match = re.search(r"(?:^|\s)(0\.[0-9]+)(?:\s|$)", text or "")
    if match:
        return round(float(match.group(1)), 6)
    # ⚠️ 最后找裸整数（>1 必然是百分数）
    match = re.search(r"(?:^|\s)([0-9]{1,3})(?:\s|$)", text or "")
    if match:
        return round(float(match.group(1)) / 100.0, 6)
    return None


# ===========================================================================
# 落库
# ===========================================================================


def submit_from_text(
    db: Session,
    *,
    employee_id: str,
    text: str,
    open_id: str,
    chat_id: str = "",
    message_id: str = "",
) -> dict[str, Any]:
    """从一条飞书文本消息提交指标。

    ⚠️ **三个要素缺一不可**：指标、数值、日期
    - 指标认不出 → 报错（不能瞎猜）
    - 数值认不出 → 报错（**绝不猜**）
    - 日期认不出 → 用今天（这是合理的默认值）

    Returns:
        ``{"metric_id": ..., "metric_code": ..., "value": ..., "date": ...}``
    """
    metric_code = detect_metric_code(text)
    value = extract_value(text)
    if value is None:
        raise FeishuManualMetricError(
            "没认出数值。请写成「完播率 35%」这样的格式。"
        )
    day = extract_date(text, default=date.today().isoformat())

    svc = PanManualMetricService(db)
    # ⚠️ **先查有没有待关联的截图** ——
    # 员工的自然顺序常常是「先发截图，再补数值」。
    # ⇒ 若此刻已有待关联截图，就挂到**即将提交**的那条指标上。
    pending = svc.list_pending_evidence(employee_id)

    row = svc.submit(
        employee_id=employee_id,
        metric_date=day,
        metric_code=metric_code,
        metric_value=value,
        scope="day",
        source=SOURCE_FEISHU,
        created_by=employee_id,
        remark="飞书端填报",
        feishu_open_id=open_id,
        feishu_chat_id=chat_id,
        feishu_message_id=message_id,
    )
    # ⚠️ **自动挂上先到的截图** —— 员工不必再发一次
    attached = 0
    if pending:
        attached = svc.attach_pending_evidence(employee_id, row.id)
    db.flush()
    return {
        "metric_id": row.id,
        "metric_code": row.metric_code,
        "value": row.metric_value,
        "date": row.metric_date.isoformat(),
        "attached_evidence": attached,
    }


def store_evidence(
    db: Session,
    *,
    employee_id: str,
    message_id: str,
    image_key: str = "",
    file_key: str = "",
    content: bytes | None = None,
    filename: str = "",
    open_id: str = "",
    chat_id: str = "",
) -> dict[str, Any]:
    """把飞书消息里的截图存成**证据附件**。

    ⚠️ **截图本身不构成一条指标**
    截图里的数字**不自动识别**（识别错就是错误绩效数据）。
    ⇒ 这里只把截图**存下来**并挂到最近的一条指标上。

    ⚠️ **没有指标时存成「待关联」**
    员工的自然顺序常常是「先发截图，再补数值」。
    ⇒ 若此刻一条指标都没有，截图**先存着**（``metric_id`` 为空），
    等他补了数值，``submit_from_text`` 会自动把待关联的截图挂上去。

    ⚠️ **绝不能因为「还没填数值」就丢弃截图**
    截图是员工主动提供的原始凭证 —— 丢了就再也找不回来。

    ⚠️ **为什么仍然要存**
    员工截完图发过来，说明「就是这条」——
    主管复核时能看到「员工当时给的原始凭证」。
    """
    if not content:
        raise FeishuManualMetricError("没有拿到文件内容")
    if not message_id:
        raise FeishuManualMetricError("缺少消息 ID，无法定位文件")
    if not (image_key or file_key):
        raise FeishuManualMetricError("缺少文件标识")

    svc = PanManualMetricService(db)
    # ⚠️ 挂到**最近**的一条指标上（员工多半是补证据）
    latest = svc.list_metrics(employee_id=employee_id)
    latest.sort(key=lambda m: (m.metric_date, m.created_at), reverse=True)

    metric_id = latest[0].id if latest else ""
    # ⚠️ 没有指标 ⇒ 存成待关联（``metric_id=""``），**不报错**
    pending = not metric_id

    media_type = "image/png" if image_key else "application/octet-stream"
    artifact = svc.add_artifact(
        metric_id,
        name=filename or (f"feishu_{image_key or file_key}.png"),
        media_type=media_type,
        content=content,
        source=SOURCE_FEISHU,
        feishu_image_key=image_key or None,
        feishu_message_id=message_id,
        uploaded_by=employee_id,
    )
    db.flush()
    return {
        "artifact_id": artifact.id,
        "metric_id": metric_id,
        "pending": pending,
        "digest": artifact.digest,
    }
