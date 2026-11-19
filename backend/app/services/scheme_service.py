"""Scheme validation, import, publication and deterministic organization resolution."""

from __future__ import annotations

import calendar
import copy
import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..domain.models import AuditLog, ConfigSnapshot, Department, Employee, EmployeeDepartment, _now
from ..domain.scheme_models import (
    PerformanceScheme,
    SchemeBinding,
    SchemeVersion,
)
from .access_control import build_access_context

# One row per metric; repeated scheme metadata must be identical within a scheme.
COLUMNS = {
    "scheme_code": "方案编码",
    "scheme_name": "方案名称",
    "department_id": "部门ID",
    "position": "岗位",
    "period_type": "周期类型",
    "effective_from": "生效日期",
    "effective_to": "结束日期",
    "output_mode": "展示方式",
    "metric_code": "指标编码",
    "metric_name": "指标名称",
    "metric_type": "指标类型",
    "weight": "权重",
    "unit": "单位",
    "scoring_method": "评分方式",
    "direction": "指标方向",
    "scoring_description": "评分说明",
    "target": "默认目标",
    "evidence_required": "证据必填",
}
RULE_COLUMNS = {
    "scheme_code": "方案编码",
    "metric_code": "指标编码",
    "level": "等级编码",
    "min_ratio": "下限",
    "max_ratio": "上限",
    "score": "分数",
    "description": "说明",
}
GRADE_COLUMNS = {
    "scheme_code": "方案编码",
    "level": "等级编码",
    "min_score": "最低分",
    "max_score": "最高分",
    "description": "等级说明",
}
FIELD_COLUMNS = {
    "scheme_code": "方案编码",
    "metric_code": "指标编码",
    "key": "字段编码",
    "label": "字段名称",
    "stage": "填报阶段",
    "type": "字段类型",
    "required": "必填",
}
SHEETS = {
    "指标明细": COLUMNS,
    "评分区间": RULE_COLUMNS,
    "等级规则": GRADE_COLUMNS,
    "表单字段": FIELD_COLUMNS,
}


def fail(message: str, status: int = 422):
    raise HTTPException(status_code=status, detail=message)


def hr(db: Session, user: Employee):
    ctx = build_access_context(db, user)
    if "hr" not in ctx.roles:
        fail("仅HR可以维护考核方案", 403)
    return ctx


def allowed_departments(db: Session, user: Employee):
    ctx = build_access_context(db, user)
    if "hr" in ctx.roles:
        return None
    if "department_leader" not in ctx.roles:
        fail("仅HR和部门负责人可以查看方案库", 403)
    return ctx.managed_department_ids


def audit(db, user, action, resource, **metadata):
    db.add(
        AuditLog(
            actor_id=user.id,
            action=f"scheme.{action}",
            resource_type="scheme",
            resource_id=resource,
            metadata_json=metadata,
        )
    )


def number(value, name):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return float(result)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name}必须是有限数值")


def intervals(rows, lo, hi, minimum, maximum):
    if not rows:
        raise ValueError("必须配置完整区间")
    ordered = sorted(rows, key=lambda r: float("-inf") if r.get(lo) is None else number(r[lo], lo))
    edge = minimum
    seen = set()
    for i, row in enumerate(ordered):
        lower = row.get(lo)
        upper = row.get(hi)
        lower = None if lower in (None, "") else number(lower, lo)
        upper = None if upper in (None, "") else number(upper, hi)
        if lower != edge or (upper is None and i != len(ordered) - 1):
            raise ValueError("区间存在重叠、空洞或边界错误；使用左闭右开区间")
        if upper is not None and (lower is None or upper <= lower):
            raise ValueError("区间上限必须大于下限")
        if not str(row.get("level", "")).strip() or row["level"] in seen:
            raise ValueError("区间等级编码不能为空或重复")
        seen.add(row["level"])
        row[lo], row[hi] = lower, upper
        edge = upper
    if edge != maximum:
        raise ValueError("区间必须覆盖完整范围")


def validate_payload(db: Session, payload: dict, *, draft=False) -> list[str]:
    errors = []
    document = payload.get("document")
    if draft and document:
        if not payload.get("metrics") or not payload.get("code") or not payload.get("name"):
            return ["文档草稿必须包含方案编码、名称和指标"]
        return []
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(payload.get("code", ""))):
            raise ValueError("方案编码限64位字母、数字、下划线和连字符")
        if not str(payload.get("name", "")).strip() or len(payload["name"]) > 200:
            raise ValueError("方案名称必填且不超过200字")
        binding = payload["binding"]
        dept = db.get(Department, binding["department_id"])
        if not dept or not dept.active:
            raise ValueError("部门ID不存在或部门已停用")
        if len(binding.get("position", "")) > 128:
            raise ValueError("岗位名称过长")
        if binding["period_type"] not in ("monthly", "quarterly", "annual"):
            raise ValueError("周期类型应为monthly/quarterly/annual")
        start, end = (
            date.fromisoformat(binding["effective_from"]),
            date.fromisoformat(binding["effective_to"]),
        )
        if start > end:
            raise ValueError("生效日期晚于结束日期")
        if payload.get("output_mode") not in ("quantitative", "narrative"):
            raise ValueError("展示方式应为quantitative/narrative")
        metrics = payload["metrics"]
        if not metrics or len(metrics) > 500:
            raise ValueError("每个方案需要1至500个指标")
        codes = set()
        total = Decimal(0)
        for m in metrics:
            code = str(m.get("code", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code) or code in codes:
                raise ValueError(f"指标编码为空、非法或重复：{code}")
            codes.add(code)
            if not str(m.get("name", "")).strip():
                raise ValueError(f"指标{code}缺少名称")
            weight = number(m.get("weight"), "权重")
            if weight <= 0 or (weight > 100 and payload.get("scoring_mode") != "direct_points"):
                raise ValueError(f"指标{code}权重必须大于0且不超过100")
            total += Decimal(str(m["weight"]))
            if m.get("type") not in (
                "number",
                "percentage",
                "currency",
                "count",
                "narrative",
                "rating",
            ):
                raise ValueError(f"指标{code}类型不合法")
            if not str(m.get("scoring_description", "")).strip():
                raise ValueError(f"指标{code}必须保留评分说明")
            method = m.get("scoring_method")
            if method not in ("ratio", "manager_review"):
                raise ValueError(f"指标{code}评分方式仅支持ratio/manager_review")
            if method == "ratio":
                if m["type"] in ("narrative", "rating") or not m.get("unit"):
                    raise ValueError(f"指标{code}比例评分需要数值类型和单位")
                if m.get("direction") not in ("higher_is_better", "lower_is_better"):
                    raise ValueError(f"指标{code}必须明确越高越好或越低越好")
                intervals(m.get("rules", []), "min_ratio", "max_ratio", 0.0, None)
                for r in m["rules"]:
                    if not 0 <= number(r.get("score"), "分数") <= 100:
                        raise ValueError("第一版指标分数范围为0至100")
            if m.get("target") not in (None, "") and number(m["target"], "目标值") <= 0:
                raise ValueError("默认目标必须大于0；未确定时请留空")
        if payload.get("scoring_mode") == "direct_points":
            for metric in metrics:
                if (
                    metric["scoring_method"] != "manager_review"
                    or number(metric.get("max_score"), "指标满分") <= 0
                ):
                    raise ValueError("直接分值方案需要逐项主管评分及有效满分")
            if (
                not document
                or not document.get("review_confirmed")
                or not str(document.get("review_note", "")).strip()
            ):
                raise ValueError("请核对原文差异和评分规则，填写核对说明并确认后发布")
            if not document.get("grade_rules"):
                raise ValueError("请配置等级原文规则")
            if document.get("grading_mode") != "manager_confirmed":
                raise ValueError("文档等级目前须由主管依据原文确认，不自动猜测区间")
            if (
                number(document.get("adjustment_min", 0), "加减分下限") > 0
                or number(document.get("adjustment_max", 0), "加减分上限") < 0
            ):
                raise ValueError("加减分下限须不大于0，上限须不小于0")
        else:
            if total != Decimal(100):
                raise ValueError(f"权重合计必须为100，当前{total}")
            intervals(payload.get("grades", []), "min_score", "max_score", 0.0, 100.0)
        keys = set()
        for f in payload.get("fields", []):
            if f.get("metric_code") not in codes or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,63}", str(f.get("key", ""))
            ):
                raise ValueError("表单字段引用未知指标或字段编码非法")
            if f["key"] in keys or not f.get("label"):
                raise ValueError("表单字段编码重复或名称为空")
            keys.add(f["key"])
            if f.get("stage") not in ("plan", "result", "self_review", "manager_review") or f.get(
                "type"
            ) not in ("text", "textarea", "number", "attachment"):
                raise ValueError("表单字段阶段或类型不合法")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return errors


def default_fields(metrics):
    return [
        {
            "metric_code": m["code"],
            "key": f"metric_{i}_result",
            "label": m["name"],
            "stage": "result",
            "type": "textarea" if m["type"] == "narrative" else "number",
            "required": True,
        }
        for i, m in enumerate(metrics)
    ]


def workbook_template():
    wb = Workbook()
    wb.remove(wb.active)
    for name, columns in SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(list(columns.values()))
        ws.freeze_panes = "A2"
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 22
    ws = wb.create_sheet("填写说明")
    for line in [
        "指标明细一行一个指标；同方案编码的基本信息须一致。部门ID请从系统部门选项复制。",
        "周期类型：monthly/quarterly/annual；展示方式：quantitative/narrative；日期：YYYY-MM-DD。",
        "指标类型：number/percentage/currency/count/narrative/rating；权重填0至100数值，合计100。",
        "评分方式：ratio（程序计算）/manager_review（主管评价）。评分说明必须保留正式原文。",
        "比例方向：higher_is_better/lower_is_better。评分区间填小数，1表示100%；从0连续覆盖至无上限（留空）。",
        "等级规则最低分从0开始，连续覆盖到100；左闭右开，最后一区间包含100。",
        "表单字段可留空由系统生成；阶段plan/result/self_review/manager_review，类型text/textarea/number/attachment。",
        "导入生成草稿，发布后才能使用。请勿将示例数据作为正式考核标准。",
    ]:
        ws.append([line])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


COLUMN_ALIASES = {
    "scheme_name": ("考核方案", "方案"),
    "department_id": ("部门", "部门名称"),
    "metric_name": ("考核指标", "指标", "考核项目", "考核内容"),
    "weight": ("权重(%)", "权重（%）", "占比", "权重%"),
    "scoring_description": ("评分标准", "考核标准", "评价标准", "评分细则"),
    "scoring_method": ("计分方式",),
}


def document_workbook(content):
    """Read Word tables in document order; never execute embedded document content."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 50 * 1024 * 1024:
                fail("Word解压后超过50MB限制")
            root = ET.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        fail("无法读取Word文件，请使用有效的docx文件；旧版doc请另存为docx")
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    wb = Workbook()
    wb.remove(wb.active)
    section = ""
    for element in root.find("w:body", ns) or []:
        if element.tag.endswith("}p"):
            text = "".join(element.itertext()).strip()
            section = next((s for s in SHEETS if s in text), section)
        elif element.tag.endswith("}tbl"):
            ws = wb.create_sheet(section or f"文档表格{len(wb.sheetnames) + 1}")
            for row in element.findall("w:tr", ns):
                ws.append(
                    [
                        "\n".join("".join(p.itertext()) for p in cell.findall("w:p", ns)).strip()
                        for cell in row.findall("w:tc", ns)
                    ]
                )
    if not wb.sheetnames:
        fail("Word中没有可识别的表格，请将考核指标、权重、评分说明整理为表格后上传")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def read_workbook(content, mapping):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 50 * 1024 * 1024:
                fail("Excel解压后超过50MB限制")
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, OSError):
        fail("无法读取Excel文件，请使用有效的xlsx文件")
    sheets, errors = {}, []
    try:
        # Locate tables below document titles, including sheets with user-defined names.
        detected = {}
        for ws in wb.worksheets:
            for row_no, cells in enumerate(ws.iter_rows(), 1):
                if row_no > 50:
                    break
                headers = [str(c.value or "").strip() for c in cells]
                for name, columns in SHEETS.items():
                    labels = {
                        key: next(
                            (
                                label
                                for label in (
                                    (mapping[name][key],)
                                    if mapping.get(name, {}).get(key)
                                    else (standard, *COLUMN_ALIASES.get(key, ()))
                                )
                                if label in headers
                            ),
                            None,
                        )
                        for key, standard in columns.items()
                    }
                    required = {
                        "指标明细": ("metric_name", "weight"),
                        "评分区间": ("min_ratio", "score"),
                        "等级规则": ("min_score", "max_score"),
                        "表单字段": ("key", "label"),
                    }[name]
                    if all(labels[k] for k in required) and name not in detected:
                        detected[name] = (ws.title, row_no, labels)
        for name, columns in SHEETS.items():
            if name not in detected and name not in wb.sheetnames:
                if name != "表单字段":
                    errors.append(
                        {
                            "sheet": name,
                            "row": 1,
                            "field": "",
                            "message": f"未找到{name}表格，请补充相应表头和数据",
                        }
                    )
                sheets[name] = []
                continue
            title, header_row, labels = detected.get(name, (name, 1, {}))
            rows = wb[title].iter_rows(min_row=header_row)
            headers = [str(c.value or "").strip() for c in next(rows, [])]
            nonempty_headers = [h for h in headers if h]
            if len(set(nonempty_headers)) != len(nonempty_headers):
                errors.append({"sheet": name, "row": 1, "field": "", "message": "表头重复"})
            indexes = {
                k: headers.index(labels.get(k) or mapping.get(name, {}).get(k, label))
                for k, label in columns.items()
                if (labels.get(k) or mapping.get(name, {}).get(k, label)) in headers
            }
            sheets[name] = []
            for row_no, cells in enumerate(rows, header_row + 1):
                if row_no > 5001:
                    fail("每张工作表最多5000行")
                if not any(c.value is not None for c in cells):
                    continue
                record = {"_row": row_no}
                for key, idx in indexes.items():
                    c = cells[idx] if idx < len(cells) else None
                    if c and c.data_type == "f":
                        errors.append(
                            {
                                "sheet": name,
                                "row": row_no,
                                "field": key,
                                "message": "配置不允许使用公式",
                            }
                        )
                    value = c.value if c else None
                    if (
                        key == "weight"
                        and c
                        and "%" in c.number_format
                        and isinstance(value, (int, float))
                    ):
                        value = float(Decimal(str(value)) * 100)
                    record[key] = (
                        value.isoformat()[:10] if isinstance(value, (date, datetime)) else value
                    )
                sheets[name].append(record)
    finally:
        wb.close()
    return sheets, errors


def preview_import(db, batch):
    from .scheme_document import parse_document

    document_payload = parse_document(
        batch.source_file, batch.file_name, batch.mapping.get("_defaults", {})
    )
    if document_payload:
        document_payload["fields"] = default_fields(document_payload["metrics"])
        batch.preview = {
            "schemes": [document_payload],
            "errors": [],
            "warnings": [
                "已提取业务文档，请导入草稿后核对适用范围、正文与附录差异、加减分和等级原文；核对完成前不可发布。"
            ],
            "scheme_count": 1,
        }
        batch.status = "validated"
        return batch.preview
    content = batch.source_file
    if batch.file_name.lower().endswith(".docx"):
        content = document_workbook(content)
    sheets, errors = read_workbook(content, batch.mapping)
    defaults = batch.mapping.get("_defaults", {})
    generated_code = "IMPORT_" + hashlib.sha256(batch.source_file).hexdigest()[:12]
    departments = db.scalars(select(Department).where(Department.active.is_(True))).all()
    for sheet, rows in sheets.items():
        for i, row in enumerate(rows, 1):
            if not row.get("scheme_code"):
                row["scheme_code"] = defaults.get("scheme_code") or generated_code
            if sheet != "指标明细":
                continue
            for key in (
                "scheme_name",
                "department_id",
                "position",
                "period_type",
                "effective_from",
                "effective_to",
                "output_mode",
            ):
                if not row.get(key) and defaults.get(key):
                    row[key] = defaults[key]
            row.setdefault("scheme_name", batch.file_name.rsplit(".", 1)[0])
            row.setdefault("metric_code", f"KPI_{i}")
            row.setdefault("metric_type", "narrative")
            row.setdefault("scoring_method", "manager_review")
            row.setdefault("output_mode", "narrative")
            dept_value = str(row.get("department_id") or "").strip()
            if not any(d.id == dept_value for d in departments):
                matches = [d for d in departments if d.name == dept_value]
                if len(matches) == 1:
                    row["department_id"] = matches[0].id
            translations = {
                "period_type": {
                    "月度": "monthly",
                    "月": "monthly",
                    "季度": "quarterly",
                    "年度": "annual",
                    "年": "annual",
                },
                "output_mode": {"量化表格": "quantitative", "工作成果文字": "narrative"},
                "scoring_method": {
                    "主管评价": "manager_review",
                    "主管按说明评价": "manager_review",
                    "比例评分": "ratio",
                },
                "metric_type": {
                    "数值": "number",
                    "百分比": "percentage",
                    "金额": "currency",
                    "数量": "count",
                    "文字": "narrative",
                    "等级": "rating",
                },
                "direction": {"越高越好": "higher_is_better", "越低越好": "lower_is_better"},
            }
            for key, values in translations.items():
                row[key] = values.get(str(row.get(key) or "").strip(), row.get(key))
            weight = row.get("weight")
            if isinstance(weight, str) and weight.strip().endswith(("%", "％")):
                row["weight"] = weight.strip()[:-1]
    schemes = {}
    warnings = []
    if any(row.get("scheme_code") == generated_code for row in sheets.get("指标明细", [])):
        warnings.append(
            "文件未填写方案编码，已按单个方案生成编码；多方案文件请填写各自的方案编码。缺少指标类型和评分方式时按文字指标、主管评价解析，请在发布前核对。"
        )
    for row in sheets.get("指标明细", []):
        try:
            code = str(row.get("scheme_code") or "").strip()
            if not code:
                raise ValueError("方案编码不能为空")
            meta = {
                "code": code,
                "name": row.get("scheme_name"),
                "output_mode": row.get("output_mode"),
                "binding": {
                    "department_id": str(row.get("department_id") or "").strip(),
                    "position": str(row.get("position") or "").strip(),
                    "period_type": row.get("period_type"),
                    "effective_from": str(row.get("effective_from") or ""),
                    "effective_to": str(row.get("effective_to") or ""),
                },
            }
            if code in schemes and any(schemes[code][k] != v for k, v in meta.items()):
                raise ValueError("同一方案编码的基本信息不一致；不同适用范围请使用不同方案编码")
            p = schemes.setdefault(code, {**meta, "metrics": [], "grades": [], "fields": []})
            m = {
                "code": str(row.get("metric_code") or ""),
                "name": row.get("metric_name"),
                "type": row.get("metric_type"),
                "weight": number(row.get("weight"), "权重"),
                "unit": row.get("unit"),
                "scoring_method": row.get("scoring_method"),
                "direction": row.get("direction"),
                "scoring_description": row.get("scoring_description"),
                "target": None
                if row.get("target") in (None, "")
                else number(row["target"], "默认目标"),
                "evidence_required": str(row.get("evidence_required", "")).lower()
                in ("是", "true", "1"),
                "rules": [],
            }
            p["metrics"].append(m)
        except ValueError as exc:
            errors.append(
                {"sheet": "指标明细", "row": row["_row"], "field": "", "message": str(exc)}
            )
    for sheet in ("评分区间", "等级规则", "表单字段"):
        for row in sheets.get(sheet, []):
            try:
                p = schemes.get(str(row.get("scheme_code") or ""))
                if not p:
                    raise ValueError("引用未知方案编码")
                raw = {k: v for k, v in row.items() if not k.startswith("_") and k != "scheme_code"}
                if sheet == "评分区间":
                    m = next((m for m in p["metrics"] if m["code"] == raw.get("metric_code")), None)
                    if not m:
                        raise ValueError("引用未知指标编码")
                    raw["score"] = number(raw.get("score"), "分数")
                    m["rules"].append(raw)
                elif sheet == "等级规则":
                    p["grades"].append(raw)
                else:
                    raw["required"] = str(raw.get("required", "")).lower() in ("是", "true", "1")
                    p["fields"].append(raw)
            except ValueError as exc:
                errors.append(
                    {"sheet": sheet, "row": row["_row"], "field": "", "message": str(exc)}
                )
    for code, p in schemes.items():
        if not p["fields"]:
            p["fields"] = default_fields(p["metrics"])
        for error in validate_payload(db, p):
            first = next(r["_row"] for r in sheets["指标明细"] if r.get("scheme_code") == code)
            errors.append({"sheet": "指标明细", "row": first, "field": code, "message": error})
    if not schemes:
        errors.append(
            {
                "sheet": "指标明细",
                "row": 2,
                "field": "",
                "message": "文件中没有方案数据：下载的模板仅含表头，请从第2行填写指标、权重和评分说明后再上传；Word请使用表格填写",
            }
        )
    batch.preview = {
        "schemes": list(schemes.values()),
        "errors": errors,
        "warnings": warnings,
        "scheme_count": len(schemes),
    }
    batch.status = "invalid" if errors else "validated"
    return batch.preview


def create_version(db, user, payload, source=None, copied_from=None):
    errors = validate_payload(db, payload, draft=True)
    if errors:
        fail("；".join(errors))
    scheme = db.scalar(select(PerformanceScheme).where(PerformanceScheme.code == payload["code"]))
    if scheme is None:
        scheme = PerformanceScheme(code=payload["code"], name=payload["name"], created_by=user.id)
        db.add(scheme)
        db.flush()
    db.execute(
        update(PerformanceScheme)
        .where(PerformanceScheme.id == scheme.id)
        .values(revision=PerformanceScheme.revision + 1)
    )
    versions = db.scalars(
        select(SchemeVersion.version_no).where(SchemeVersion.scheme_id == scheme.id)
    ).all()
    version = SchemeVersion(
        scheme_id=scheme.id,
        version_no=max(versions, default=0) + 1,
        payload=copy.deepcopy(payload),
        created_by=user.id,
        source_import_id=source,
        copied_from_id=copied_from,
    )
    db.add(version)
    db.flush()
    audit(db, user, "create", version.id)
    return version


def binding_from_payload(db, version):
    b = version.payload["binding"]
    db.add(
        SchemeBinding(
            version_id=version.id,
            department_id=b["department_id"],
            position=b.get("position", ""),
            period_type=b["period_type"],
            effective_from=date.fromisoformat(b["effective_from"]),
            effective_to=date.fromisoformat(b["effective_to"]),
        )
    )


def serialize(db, version):
    scheme = db.get(PerformanceScheme, version.scheme_id)
    return {
        "id": version.id,
        "scheme_id": scheme.id,
        "version_no": version.version_no,
        "status": version.status,
        "revision": version.revision,
        "payload": version.payload,
        "published_at": version.published_at,
        "created_at": version.created_at,
    }


def publish(db, user, version):
    errors = validate_payload(db, version.payload)
    if errors:
        fail("；".join(errors))
    b = version.payload["binding"]
    # Department row acts as a scope mutex across different scheme codes and works on SQLite/PG.
    db.execute(
        update(Department).where(Department.id == b["department_id"]).values(name=Department.name)
    )
    start, end = date.fromisoformat(b["effective_from"]), date.fromisoformat(b["effective_to"])
    conflicts = db.scalars(
        select(SchemeBinding)
        .join(SchemeVersion, SchemeVersion.id == SchemeBinding.version_id)
        .where(
            SchemeBinding.department_id == b["department_id"],
            SchemeBinding.position == b.get("position", ""),
            SchemeBinding.period_type == b["period_type"],
            SchemeVersion.status == "published",
            SchemeBinding.effective_from <= end,
            SchemeBinding.effective_to >= start,
        )
    ).all()
    if conflicts:
        fail("同部门、岗位、周期存在生效时间重叠的已发布方案；请调整新版本生效范围", 409)
    result = db.execute(
        update(SchemeVersion)
        .where(
            SchemeVersion.id == version.id,
            SchemeVersion.status == "draft",
            SchemeVersion.revision == version.revision,
        )
        .values(status="published", published_at=_now(), revision=SchemeVersion.revision + 1)
    )
    if result.rowcount != 1:
        fail("版本已变化或已发布，请刷新", 409)
    binding_from_payload(db, version)
    audit(db, user, "publish", version.id)
    db.flush()
    db.refresh(version)
    return version


def period_range(period, period_type):
    if period_type == "monthly" and re.fullmatch(r"\d{4}-?\d{2}", period):
        p = period.replace("-", "")
        year, month = int(p[:4]), int(p[4:])
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    if period_type == "quarterly" and re.fullmatch(r"\d{4}Q[1-4]", period):
        year, month = int(period[:4]), (int(period[-1]) - 1) * 3 + 1
        return date(year, month, 1), date(year, month + 2, calendar.monthrange(year, month + 2)[1])
    if period_type == "annual" and re.fullmatch(r"\d{4}", period):
        return date(int(period), 1, 1), date(int(period), 12, 31)
    fail("周期格式错误：月YYYYMM、季度YYYYQn、年YYYY")


def resolve(db, employee, period, period_type="monthly"):
    try:
        start, end = period_range(period, period_type)
    except ValueError:
        fail("考核周期日期无效")
    primary = db.scalars(
        select(EmployeeDepartment.department_id).where(
            EmployeeDepartment.employee_id == employee.id, EmployeeDepartment.is_primary.is_(True)
        )
    ).all()
    if len(set(primary)) > 1:
        fail("员工主部门配置冲突，请联系HR", 409)
    department = primary[0] if primary else employee.department_id
    dept = db.get(Department, department) if department else None
    if not dept or not dept.active:
        fail("员工缺少有效部门，请先同步组织或联系HR", 409)
    candidates = db.execute(
        select(SchemeVersion, SchemeBinding)
        .join(SchemeBinding, SchemeBinding.version_id == SchemeVersion.id)
        .where(
            SchemeVersion.status == "published",
            SchemeBinding.department_id == department,
            SchemeBinding.period_type == period_type,
            SchemeBinding.effective_from <= start,
            SchemeBinding.effective_to >= end,
            SchemeBinding.position.in_(["", employee.position or ""]),
        )
    ).all()
    exact = [v for v, b in candidates if b.position and b.position == employee.position]
    matches = exact or [v for v, b in candidates if not b.position]
    if len(matches) != 1:
        fail("考核方案未配置" if not matches else "考核方案匹配冲突，请联系HR", 409)
    return matches[0]


def freeze_version(db, task, version, employees):
    if version.status != "published":
        fail("只能使用已发布方案", 409)
    p = copy.deepcopy(version.payload)
    metrics = [
        {
            "code": m["code"],
            "name": m["name"],
            "unit": m.get("unit"),
            "calculation": m["scoring_description"],
            "data_source": "scheme",
            **m,
        }
        for m in p["metrics"]
    ]
    snapshot = ConfigSnapshot(
        task_id=task.id,
        metrics_json=metrics,
        weights_json=[{"metric_code": m["code"], "weight": m["weight"]} for m in p["metrics"]],
        rubric_json=[
            {"metric_code": m["code"], "levels_json": m.get("rules", [])} for m in p["metrics"]
        ],
        form_template_json={
            "name": p["name"],
            "fields": p["fields"],
            "scheme_version_id": version.id,
            "output_mode": p["output_mode"],
            "grades": p["grades"],
            "scheme_payload": p,
            "employee_scope": [
                {"employee_id": e.id, "department_id": e.department_id, "position": e.position}
                for e in employees
            ],
        },
        config_version=f"scheme:{version.id}",
    )
    db.add(snapshot)
    db.flush()
    task.snapshot_id = snapshot.id
    task.department_id = p["binding"]["department_id"]
    task.position = p["binding"].get("position") or None
    return snapshot
