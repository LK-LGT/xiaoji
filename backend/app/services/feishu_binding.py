"""计划 ↔ 飞书审批实例的绑定服务（A8 补强）。

解决的问题
----------
A8 首批实现的审批回流依赖 webhook 事件**自带** ``plan_type`` / ``plan_id``。
这只覆盖「本系统提交 → 飞书收到通知」这一半 ——
**员工从飞书侧发起审批**时，事件里没有本系统的计划 ID，链就断了。

本模块提供两条独立的关联通道：

┌─────────────────────────────────────────────────────────────────┐
│ 通道 A：instance_code 精确匹配                                  │
│   本系统提交计划 → 创建飞书审批实例 → 立刻写绑定                 │
│   回流时按 instance_code 反查计划                                │
│   适用：本系统发起的审批                                        │
└─────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│ 通道 B：correlation_key 关联键匹配                              │
│   本系统给计划生成短码 → 随通知/表单发给员工                     │
│   员工在飞书表单填入该码 → 回流时按码反查计划                    │
│   适用：飞书侧发起的审批                                        │
└─────────────────────────────────────────────────────────────────┘

反查优先级：``instance_code`` > ``correlation_key`` > 均无（记日志忽略）。

设计要点
--------
1. **绑定是幂等的**：重复绑定同一计划不报错，复用已有行。
2. **1:1 由唯一约束保证**：一个计划只能绑一个实例，一个实例不能绑多个计划，
   同一个关联码全局唯一 —— 反查不会歧义。
3. **关联码可读、可手输**：用不含易混字符（0/O/1/I）的短码，
   员工在飞书表单里手输不易出错。
4. **绑定表只存关联关系**：不复制计划内容，也不改业务状态 ——
   状态仍由 ``plan_approval`` 流水与计划状态机负责。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.plan_models import PlanFeishuBinding

logger = logging.getLogger(__name__)


# ===========================================================================
# 常量
# ===========================================================================

#: 绑定来源
BIND_SOURCE_LOCAL_SUBMIT = "local_submit"   # 本系统提交时创建实例并绑定
BIND_SOURCE_FEISHU_MATCH = "feishu_match"   # 按关联键反查后回填
BIND_SOURCE_MANUAL = "manual"               # 人工绑定

#: 关联码字符集 —— 刻意剔除易混字符（0/O、1/I/l），
#: 员工在飞书表单里手输时不易出错。
CORRELATION_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

#: 关联码长度（8 位 ≈ 30^8 ≈ 6.5 亿组合，够用且好念）
CORRELATION_KEY_LENGTH = 8

#: 关联码前缀 —— 员工看到时知道这是「关联码」不是别的编号。
CORRELATION_KEY_PREFIX = "PP"


# ===========================================================================
# 关联码
# ===========================================================================


def generate_correlation_key(db: Session | None = None) -> str:
    """生成一个全局唯一的关联码。

    形如 ``PP-A7K2M9QX``（去掉分隔符，便于手输与复制）。

    唯一性由数据库唯一约束兜底；这里做一次存在性检查，
    避免在极端碰撞时生成重复码。
    """
    for _ in range(8):
        raw = "".join(secrets.choice(CORRELATION_ALPHABET) for _ in range(CORRELATION_KEY_LENGTH))
        key = f"{CORRELATION_KEY_PREFIX}{raw}"
        if db is None:
            return key
        if get_binding_by_correlation_key(db, key) is None:
            return key
    # 极端情况下仍碰撞 —— 交给唯一约束报错，不静默覆盖
    raise FeishuBindingError("关联码生成失败：连续碰撞", code="FEISHU_CORR_COLLISION")


def normalize_correlation_key(raw: str) -> str:
    """归一化用户/飞书传入的关联码。

    - 转大写
    - 去掉空格、连字符、下划线
    - 去掉 `PP` 前缀再补回（兼容用户只抄了主体码的情况）
    """
    if not raw:
        return ""
    cleaned = "".join(ch for ch in raw.upper() if ch.isalnum())
    if cleaned.startswith(CORRELATION_KEY_PREFIX):
        return cleaned
    return f"{CORRELATION_KEY_PREFIX}{cleaned}"


# ===========================================================================
# 异常
# ===========================================================================


class FeishuBindingError(Exception):
    """绑定相关错误。"""

    def __init__(self, message: str, *, code: str = "FEISHU_BINDING_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ===========================================================================
# 查询
# ===========================================================================


def get_binding_by_instance(
    db: Session, instance_code: str
) -> PlanFeishuBinding | None:
    """按飞书实例 code 查绑定。"""
    if not instance_code:
        return None
    return db.execute(
        select(PlanFeishuBinding).where(
            PlanFeishuBinding.instance_code == instance_code
        )
    ).scalar_one_or_none()


def get_binding_by_correlation_key(
    db: Session, correlation_key: str
) -> PlanFeishuBinding | None:
    """按关联码查绑定。"""
    if not correlation_key:
        return None
    return db.execute(
        select(PlanFeishuBinding).where(
            PlanFeishuBinding.correlation_key == correlation_key
        )
    ).scalar_one_or_none()


def get_binding_by_plan(
    db: Session, plan_type: str, plan_id: str
) -> PlanFeishuBinding | None:
    """按计划查绑定。"""
    return db.execute(
        select(PlanFeishuBinding).where(
            PlanFeishuBinding.plan_type == plan_type,
            PlanFeishuBinding.plan_id == plan_id,
        )
    ).scalar_one_or_none()


def list_bindings(
    db: Session,
    *,
    plan_type: str | None = None,
    plan_id: str | None = None,
    limit: int = 100,
) -> list[PlanFeishuBinding]:
    """列出绑定（管理端排查用）。"""
    stmt = select(PlanFeishuBinding)
    if plan_type:
        stmt = stmt.where(PlanFeishuBinding.plan_type == plan_type)
    if plan_id:
        stmt = stmt.where(PlanFeishuBinding.plan_id == plan_id)
    stmt = stmt.order_by(PlanFeishuBinding.created_at.desc()).limit(min(limit, 500))
    return list(db.execute(stmt).scalars().all())


# ===========================================================================
# 写入
# ===========================================================================


def bind_plan_to_instance(
    db: Session,
    *,
    plan_type: str,
    plan_id: str,
    instance_code: str = "",
    approval_code: str = "",
    correlation_key: str = "",
    bind_source: str = BIND_SOURCE_LOCAL_SUBMIT,
    created_by: str = "",
) -> PlanFeishuBinding:
    """绑定计划与飞书审批实例（幂等）。

    重复绑定同一计划 → 复用并更新已有行，不报错。
    关联码为空时自动生成一个 —— 保证「飞书侧发起」这条通道始终有反查依据。
    """
    existing = get_binding_by_plan(db, plan_type, plan_id)
    if existing is not None:
        # 补齐缺失字段（不覆盖已有值）
        changed = False
        if instance_code and not existing.instance_code:
            existing.instance_code = instance_code
            changed = True
        if approval_code and not existing.approval_code:
            existing.approval_code = approval_code
            changed = True
        if correlation_key and not existing.correlation_key:
            existing.correlation_key = correlation_key
            changed = True
        if changed:
            db.flush()
        return existing

    if not correlation_key:
        correlation_key = generate_correlation_key(db)

    row = PlanFeishuBinding(
        plan_type=plan_type,
        plan_id=plan_id,
        instance_code=instance_code or None,
        approval_code=approval_code or None,
        correlation_key=correlation_key,
        bind_source=bind_source,
        created_by=created_by or None,
    )
    db.add(row)
    db.flush()
    logger.info(
        "计划 ↔ 飞书实例绑定已建立：plan=%s instance=%s corr=%s",
        plan_id,
        instance_code or "-",
        correlation_key,
    )
    return row


def update_binding_status(
    db: Session, binding: PlanFeishuBinding, status: str
) -> PlanFeishuBinding:
    """同步绑定行上的飞书实例状态。"""
    if status:
        binding.instance_status = status
        db.flush()
    return binding


def resolve_plan_by_instance(
    db: Session,
    *,
    instance_code: str = "",
    correlation_key: str = "",
    approval_code: str = "",
) -> tuple[str, str, PlanFeishuBinding | None]:
    """把飞书侧信息反查成本系统计划。

    优先级：``instance_code`` > ``correlation_key``。

    Returns:
        ``(plan_type, plan_id, binding)``。
        找不到时返回 ``("", "", None)``。
    """
    if instance_code:
        binding = get_binding_by_instance(db, instance_code)
        if binding is not None:
            return binding.plan_type, binding.plan_id, binding
        logger.info("按 instance_code 未找到绑定：%s", instance_code)

    if correlation_key:
        key = normalize_correlation_key(correlation_key)
        binding = get_binding_by_correlation_key(db, key)
        if binding is not None:
            logger.info(
                "按关联码匹配到计划：corr=%s plan=%s", key, binding.plan_id
            )
            return binding.plan_type, binding.plan_id, binding
        logger.info("按关联码未找到绑定：%s", key)

    logger.info(
        "无法反查计划：instance=%s corr=%s approval=%s",
        instance_code or "-",
        correlation_key or "-",
        approval_code or "-",
    )
    return "", "", None


# ===========================================================================
# 序列化
# ===========================================================================


def serialize_binding(row: PlanFeishuBinding) -> dict[str, Any]:
    """序列化绑定行。"""
    return {
        "id": row.id,
        "planType": row.plan_type,
        "planId": row.plan_id,
        "instanceCode": row.instance_code or "",
        "approvalCode": row.approval_code or "",
        "correlationKey": row.correlation_key or "",
        "bindSource": row.bind_source,
        "instanceStatus": row.instance_status or "",
        "createdBy": row.created_by or "",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
        "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
    }


def build_correlation_message(
    *,
    owner_name: str,
    plan_type: str,
    plan_title: str,
    correlation_key: str,
    approver_name: str = "",
) -> str:
    """构造带**关联码**的通知文案。

    关联码用于「飞书侧发起审批」的关联 —— 员工在飞书表单里填入此码，
    回流时就能反查到本系统的计划。

    Args:
        approver_name: 审批人姓名；非空时说明「可由主管在飞书发起」。
    """
    type_label = {"month": "月计划", "week": "周计划", "day": "日计划"}.get(
        plan_type, plan_type
    )
    lines = [f"【{type_label}】{owner_name} 的{plan_title}"]
    if approver_name:
        lines.append(f"审批人：{approver_name}")
    lines.append(f"关联码：{correlation_key}")
    lines.append("（在飞书发起审批时填写此码，审批结果将自动同步至本系统）")
    return "\n".join(lines)
