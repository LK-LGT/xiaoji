"""P1-5 —— 数据源白名单与字段脱敏配置。

语义
----
1. **默认不信任**：**没有配置**的数据源不可读、不可进算分集。
   漏配的后果是「数据没进来」（可见、可修），而不是「数据泄露了」（不可见、不可修）。
2. **角色可读**：每个源声明哪些角色能读（``hr`` / ``manager`` /
   ``department_leader`` / ``employee``）。HR 永远可读（结算与申诉需要）。
3. **可进算分集**：``allowed=True`` 的源可以产生 ``usage=scoring`` 的证据；
   ``allowed=False`` 的源只能作参考（``reference_only``）。
4. **字段脱敏**：源可声明需要脱敏的字段，读出时统一替换为 ``***``。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import DataSourcePolicy

logger = logging.getLogger(__name__)

#: HR 永远可读任何源（结算、申诉、审计需要完整数据）
ALWAYS_ALLOWED_ROLES = frozenset({"hr"})

#: 内置的默认策略（表里没配置时的兜底）。
#: 与 ``0030_data_source_policy`` 迁移里初始化的三行保持一致 ——
#: 迁移负责落库，这份字典负责「表还没建 / 查不到」时仍有确定的语义。
DEFAULT_POLICIES: dict[str, dict[str, Any]] = {
    "attendance": {
        "display_name": "考勤系统",
        "allowed": True,
        "allowed_roles": "hr,manager,department_leader,employee",
        "redacted_fields": "",
    },
    "feishu_approval": {
        "display_name": "飞书审批",
        "allowed": True,
        "allowed_roles": "hr,manager,department_leader,employee",
        "redacted_fields": "",
    },
    "manual_upload": {
        "display_name": "人工上传",
        "allowed": True,
        "allowed_roles": "hr,manager,employee",
        "redacted_fields": "mobile,email",
    },
}


class DataSourceError(Exception):
    """数据源配置失败。"""

    status_code = 400

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _split(value: str) -> list[str]:
    return [x.strip().lower() for x in (value or "").split(",") if x.strip()]


def load_policies(db: Session) -> dict[str, dict[str, Any]]:
    """加载全部数据源策略（表为准，缺失项用内置默认补齐）。"""
    policies: dict[str, dict[str, Any]] = {
        code: {**cfg, "source_code": code, "note": "", "configured": False}
        for code, cfg in DEFAULT_POLICIES.items()
    }
    rows = db.scalars(select(DataSourcePolicy)).all()
    for row in rows:
        policies[row.source_code] = {
            "source_code": row.source_code,
            "display_name": row.display_name,
            "allowed": bool(row.allowed),
            "allowed_roles": row.allowed_roles,
            "redacted_fields": row.redacted_fields,
            "note": row.note,
            "configured": True,
        }
    return policies


def get_policy(db: Session, source_code: str) -> dict[str, Any] | None:
    """取单个源的策略；未配置返回 ``None``（= 不可读）。"""
    row = db.scalar(
        select(DataSourcePolicy).where(DataSourcePolicy.source_code == source_code)
    )
    if row is not None:
        return {
            "source_code": row.source_code,
            "display_name": row.display_name,
            "allowed": bool(row.allowed),
            "allowed_roles": row.allowed_roles,
            "redacted_fields": row.redacted_fields,
            "note": row.note,
            "configured": True,
        }
    default = DEFAULT_POLICIES.get(source_code)
    if default is None:
        return None
    return {**default, "source_code": source_code, "note": "", "configured": False}


def is_readable(db: Session, source_code: str, roles: frozenset[str] | set[str]) -> bool:
    """当前角色集合能否读取该数据源。"""
    policy = get_policy(db, source_code)
    if policy is None:
        # 默认不信任：未配置的源不可读
        return False
    if ALWAYS_ALLOWED_ROLES & set(roles):
        return True
    allowed_roles = set(_split(policy["allowed_roles"]))
    return bool(allowed_roles & set(roles))


def can_enter_scoring(db: Session, source_code: str) -> bool:
    """该源是否允许进入算分集（``usage=scoring``）。"""
    policy = get_policy(db, source_code)
    return bool(policy and policy["allowed"])


def redacted_fields(db: Session, source_code: str) -> set[str]:
    """该源需要脱敏的字段集合（字段名小写）。"""
    policy = get_policy(db, source_code)
    if policy is None:
        return set()
    return set(_split(policy["redacted_fields"]))


def apply_redaction(
    db: Session, source_code: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """按源策略脱敏字段（在既有 ``privacy.redact_obj`` 之上叠加）。"""
    fields = redacted_fields(db, source_code)
    if not fields or not isinstance(payload, dict):
        return payload
    result = dict(payload)
    for key in list(result):
        if key.lower() in fields:
            result[key] = "***"
    return result


def upsert_policy(
    db: Session,
    *,
    source_code: str,
    display_name: str = "",
    allowed: bool = False,
    allowed_roles: str = "hr",
    redacted_fields: str = "",
    note: str = "",
    updated_by: str | None = None,
) -> DataSourcePolicy:
    """新增或更新数据源策略（HR 配置入口）。"""
    if not source_code.strip():
        raise DataSourceError("数据源标识不能为空")
    row = db.scalar(
        select(DataSourcePolicy).where(DataSourcePolicy.source_code == source_code)
    )
    if row is None:
        row = DataSourcePolicy(source_code=source_code)
        db.add(row)
    row.display_name = display_name or row.display_name or source_code
    row.allowed = bool(allowed)
    row.allowed_roles = allowed_roles or "hr"
    row.redacted_fields = redacted_fields or ""
    row.note = note or ""
    row.updated_by = updated_by
    db.flush()
    return row


def serialize(policy: dict[str, Any]) -> dict[str, Any]:
    """序列化为 API 形状。"""
    return {
        "sourceCode": policy["source_code"],
        "displayName": policy.get("display_name", ""),
        "allowed": bool(policy.get("allowed")),
        "allowedRoles": _split(policy.get("allowed_roles", "")),
        "redactedFields": _split(policy.get("redacted_fields", "")),
        "note": policy.get("note", ""),
        "configured": bool(policy.get("configured")),
    }
