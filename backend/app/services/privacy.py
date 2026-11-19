"""工具数据离开 L2 前脱敏；语义与 L3 grounding 保持一致。"""

from __future__ import annotations

import re
from typing import Any

MOBILE_RE = re.compile(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[0-9Xx])(?![0-9Xx])")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
EMPLOYEE_NO_RE = re.compile(r"(?i)(员工编号|employeeNo|employee_no)\s*[:：=]?\s*[A-Za-z0-9_-]+")
SENSITIVE_KEYS = ("mobile", "phone", "email", "employeeno", "employee_no")


def redact_text(text: str | None) -> str:
    if not text:
        return ""
    value = MOBILE_RE.sub(r"\1****\2", text)
    value = ID_CARD_RE.sub(r"\1********\2", value)
    value = EMAIL_RE.sub(r"\1***\2", value)
    return EMPLOYEE_NO_RE.sub("员工编号:***", value)


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: "***" if any(s in key.lower() for s in SENSITIVE_KEYS) else redact_obj(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(item) for item in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
