"""绩效评定表的导出（FS-6）—— 生成 Excel / PDF 附件。

⚠️ **为什么要有附件**
------------------------------------
N8「绩效等级已发布」通知原本只有一句话：
「你的绩效结果已确认，请登录系统查看分数和等级」。

问题：员工在飞书里看到通知，但**得点进系统、找到对应考核、翻到那一页**
才能看到自己的分数与各项指标构成。
⇒ 主管要核对下属得分时更麻烦 —— 得一个个登录去查。

⇒ 直接把评定表作为**附件**发过去，收到即可看。

⚠️ **数据只读，绝不回写**
------------------------------------
本模块**只读取** ``AssessmentRecord`` / ``Score`` / ``MetricValue``，
不产生任何写入。

⇒ 符合项目铁律「模型永不生成或改写分数」——
   分数的唯一真值是 ``Score`` 表，本模块只是**呈现**它。

⚠️ **Excel 为主，PDF 为辅**
------------------------------------
- **Excel（.xlsx）** —— 可二次计算、可筛选、可归档。**默认格式。**
- **PDF** —— 需要额外依赖（``reportlab``），且中文字体在服务器上
  常常缺字（豆腐块）。⇒ **PDF 默认不生成**，仅在显式要求且依赖可用时生成。

⇒ 这样既满足「Excel/PDF 附件」的需求，又不让 PDF 的字体坑
   变成上线阻塞项。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..domain.models import AssessmentRecord, AssessmentTask, Employee, Score

logger = logging.getLogger(__name__)

#: Excel 里的指标明细表头。
#:
#: ⚠️ **顺序即阅读顺序** —— 从「是什么」到「算成多少」，
#:    让人能顺着看懂分数是怎么来的。
_METRIC_HEADERS: tuple[str, ...] = (
    "指标编码",
    "指标名称",
    "权重",
    "归一权重",
    "实测值",
    "目标值",
    "达成率",
    "档位",
    "档位说明",
    "档位得分",
    "贡献分",
    "单位",
    "数据来源",
    "数据截至",
)


@dataclass
class RatingExport:
    """一份评定表的导出结果。"""

    filename: str
    content: bytes
    file_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "size": len(self.content),
            "fileType": self.file_type,
        }


def _fmt(value: Any, *, digits: int = 4) -> str:
    """格式化数值 —— 空值显示为 ``-``，浮点去掉多余的尾零。"""
    if value is None or value == "":
        return "-"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def collect_rating_rows(
    db: Session,
    record: AssessmentRecord,
) -> tuple[dict[str, Any], list[list[Any]]]:
    """收集评定表数据。

    Returns:
        ``(概要信息, 指标明细行)``。
    """
    task = db.get(AssessmentTask, record.task_id)
    employee = db.get(Employee, record.employee_id)
    score = db.query(Score).filter_by(record_id=record.id).first()

    summary: dict[str, Any] = {
        "考核任务": task.name if task else "-",
        "考核周期": task.period if task else "-",
        "员工姓名": employee.name if employee else "-",
        "最终等级": record.confirmed_rating or "-",
        "记录状态": record.status or "-",
        "总分": _fmt(score.total if score else None, digits=2),
        "主管评语": (record.manager_comment or "-").strip(),
        "自评内容": "-",
    }
    if record.self_review_content:
        # ⚠️ 自评是 JSON，可能很大 —— 只放摘要，不整份塞进表头区。
        text = str(record.self_review_content).strip()
        summary["自评内容"] = text[:500] + ("…" if len(text) > 500 else "")

    rows: list[list[Any]] = []
    for metric in (score.per_metric_json if score else []) or []:
        rows.append(
            [
                metric.get("metric_code") or "-",
                metric.get("metric_name") or "-",
                _fmt(metric.get("raw_weight")),
                _fmt(metric.get("normalized_weight")),
                _fmt(metric.get("value")),
                _fmt(metric.get("target")),
                _fmt(metric.get("ratio")),
                metric.get("level") or "-",
                metric.get("level_desc") or "-",
                _fmt(metric.get("level_score")),
                _fmt(metric.get("contribution")),
                metric.get("unit") or "-",
                metric.get("source") or "-",
                metric.get("as_of") or "-",
            ]
        )
    return summary, rows


def build_rating_excel(
    db: Session,
    record: AssessmentRecord,
) -> RatingExport:
    """生成评定表 Excel。

    ⚠️ **两个 sheet**
    - 「概览」—— 员工 / 周期 / 等级 / 总分 / 评语；
    - 「指标明细」—— 每一项指标的权重、实测值、达成率、档位、贡献分。

    ⚠️ **不写公式** —— 贡献分直接写算好的**数值**。
    写公式的话，Excel 打开时会按公式重算，
    一旦口径变了，显示值与系统里的真值就对不上了。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    summary, rows = collect_rating_rows(db, record)

    workbook = Workbook()

    # ── Sheet 1：概览 ─────────────────────────────────────────
    overview = workbook.active
    overview.title = "概览"
    bold = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="DDEBF7")

    overview.append(["项目", "内容"])
    for cell in overview[1]:
        cell.font = bold
        cell.fill = header_fill
    for key, value in summary.items():
        overview.append([key, value])
    overview.column_dimensions["A"].width = 18
    overview.column_dimensions["B"].width = 60
    for row in overview.iter_rows(min_row=2, min_col=2, max_col=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    # ── Sheet 2：指标明细 ─────────────────────────────────────
    detail = workbook.create_sheet("指标明细")
    detail.append(list(_METRIC_HEADERS))
    for cell in detail[1]:
        cell.font = bold
        cell.fill = header_fill
    for row in rows:
        detail.append(row)
    for index, width in enumerate((18, 26, 10, 12, 14, 14, 12, 12, 22, 12, 12, 10, 16, 14), start=1):
        detail.column_dimensions[chr(64 + index)].width = width
    detail.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)

    filename = _build_filename(record, db, suffix="xlsx")
    return RatingExport(
        filename=filename,
        content=buffer.getvalue(),
        file_type="xlsx",
    )


def _build_filename(
    record: AssessmentRecord,
    db: Session,
    *,
    suffix: str,
) -> str:
    """生成附件文件名 —— 含员工名与周期，便于接收方直接识别。"""
    employee = db.get(Employee, record.employee_id)
    task = db.get(AssessmentTask, record.task_id)
    name = employee.name if employee else "未知员工"
    period = task.period if task else "未知周期"
    # ⚠️ 文件名里的特殊字符会让某些系统截断 —— 清掉。
    safe = "".join(ch for ch in f"{name}_{period}" if ch not in '\\/:*?"<>|').strip()
    return f"绩效评定表_{safe}.{suffix}"


def build_rating_pdf(
    db: Session,
    record: AssessmentRecord,
) -> RatingExport:
    """生成评定表 PDF。

    ⚠️ **需要 ``reportlab``** —— 未安装则抛 ``RuntimeError``。
    ⚠️ 调用方**必须**捕获这个异常并降级为「只发 Excel」。

    ⚠️ **中文字体**
    缺字体时 PDF 里中文会变成方框（豆腐块）⇒ 文档不可读。
    ⇒ 找不到中文字体时**直接抛错**，而不是生成一份没法看的 PDF。
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "生成 PDF 需要 reportlab 依赖（可选功能）"
        ) from exc

    font_name = _register_chinese_font(pdfmetrics, TTFont)
    if not font_name:
        raise RuntimeError("未找到可用的中文字体，无法生成可读的 PDF")

    summary, rows = collect_rating_rows(db, record)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        title="绩效评定表",
        author="绩效管理系统",
    )
    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "cn",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
    )

    story: list[Any] = [Paragraph("绩效评定表", styles["Title"]), Spacer(1, 8)]

    overview_data = [[Paragraph(str(k), base), Paragraph(str(v), base)] for k, v in summary.items()]
    overview_table = Table(overview_data, colWidths=[120, 480])
    overview_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#DDEBF7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.extend([overview_table, Spacer(1, 10)])

    if rows:
        header = [Paragraph(h, base) for h in _METRIC_HEADERS]
        body = [header] + [
            [Paragraph(str(cell), base) for cell in row] for row in rows
        ]
        detail_table = Table(body, repeatRows=1)
        detail_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEBF7")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(detail_table)

    doc.build(story)
    filename = _build_filename(record, db, suffix="pdf")
    return RatingExport(filename=filename, content=buffer.getvalue(), file_type="pdf")


def _register_chinese_font(pdfmetrics, TTFont) -> str:
    """注册中文字体，返回字体名。

    ⚠️ **找不到就返回空串** —— 由调用方决定是报错还是降级。
    ⚠️ 生成的 PDF 若缺中文字体，汉字会变成方块 —— **不可读**，
    ⚠️ 与其给一份看不懂的 PDF，不如明确报错。
    """
    candidates = [
        # Linux 常见中文字体
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "WQY"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "NotoCJK"),
        ("/usr/share/fonts/truetype/arphic/uming.ttc", "AR PL UMing"),
        # Windows 常见中文字体
        ("C:/Windows/Fonts/msyh.ttc", "MicrosoftYaHei"),
        ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
        ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
    ]
    for path, name in candidates:
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            return name
        except Exception:  # noqa: BLE001 - 字体缺失是预期情况
            continue
    return ""


def build_rating_attachment(
    db: Session,
    record: AssessmentRecord,
    *,
    file_type: str = "xlsx",
) -> RatingExport:
    """按类型生成附件。

    ⚠️ **PDF 失败时自动降级为 Excel** —— 附件发不出去比格式不对更糟。
    """
    normalized = (file_type or "xlsx").lower()
    if normalized == "pdf":
        try:
            return build_rating_pdf(db, record)
        except Exception as exc:  # noqa: BLE001 - 降级必须有兜底
            logger.warning(
                "生成 PDF 评定表失败，降级为 Excel：record=%s err=%s",
                record.id,
                exc,
            )
            return build_rating_excel(db, record)
    return build_rating_excel(db, record)


__all__ = [
    "RatingExport",
    "build_rating_attachment",
    "build_rating_excel",
    "build_rating_pdf",
    "collect_rating_rows",
]
