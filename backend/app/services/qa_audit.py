"""P0-10：问答审计留痕 —— 「谁查了谁」与「谁越权了」。

⚠️ **为什么单独一个模块**
----------------------
审计写入散落在各处会失控 ——
每处自己写一遍 ``AuditLog``，字段口径就会漂移
（有的写 ``result="denied"``、有的写 ``result="fail"``），
看板按 ``result`` 聚合时就会漏掉一半。

⇒ 本模块是**唯一**的问答审计写入入口。

⚠️ **记什么、不记什么**
----------------------
记：
- **谁**（``actor_id`` = 提问者）
- **查了谁 / 想查谁**（``resource_id`` = 目标人）
- **判定结果**（``result`` = allowed / denied）
- **授权理由**（``metadata_json.reason``）
- **周期**（``metadata_json.period``）

不记：
- ⚠️ **问题原文** —— PII。员工可能在飞书里问任何东西。
  需要复现时用 ``trace_id`` 关联，而不是存原文。
- ⚠️ **目标人姓名** —— 可反查员工表，且姓名会变。
- ⚠️ **拒绝的详细原因**（跨部门 vs 跨级）——
  ⚠️ **审计表本身也是敏感资产**：
  如果审计表能被越权的人读到，那"谁查过谁"就变成了泄露面。
  ⇒ ``metadata_json`` 里存**粗粒度**的 reason（``out_of_scope``），
  不存能反推层级/部门的细粒度原因。

⚠️ **写入必须不影响主流程**
--------------------------
审计写失败**不能**让用户的查询失败 ——
用户不关心审计写没写成功，但会关心"我查不到数据"。

⇒ 用**保存点**（``begin_nested``）隔离：
审计 INSERT 失败只回滚保存点，不污染外层事务。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..domain.models import AuditLog

logger = logging.getLogger(__name__)

#: 审计动作名 —— 看板按它筛选。
#:
#: ⚠️ 为什么固定成这几个值
#:    看板的 SQL 依赖这些字面量 ——
#:    改名会让历史数据"消失"（看板查不到旧记录）。
ACTION_QUERY_PEER = "qa.query_peer"

#: 判定结果 —— 看板按它分「正常」与「越权」。
RESULT_ALLOWED = "allowed"
RESULT_DENIED = "denied"


def write_qa_subject_audit(
    db: Session,
    *,
    actor_id: str,
    subject_id: str,
    result: str,
    reason: str = "",
    period: str = "",
    action: str = ACTION_QUERY_PEER,
    metadata: dict[str, Any] | None = None,
) -> None:
    """写一条问答主体审计。

    ⚠️ **失败不能影响主流程**
    ------------------------
    审计写失败只记 warning —— 用户要的是数据，不是审计。
    """
    payload: dict[str, Any] = {
        "reason": reason or "unspecified",
        "period": period or "",
    }
    if metadata:
        # ⚠️ 只并入**白名单内**的键 —— 防止调用方误塞 PII 进来。
        for key in ("scope", "task_id", "audit_level", "source"):
            if key in metadata:
                payload[key] = metadata[key]

    try:
        with db.begin_nested():
            db.add(AuditLog(
                actor_id=actor_id,
                action=action,
                resource_type="employee",
                resource_id=subject_id,
                result=result,
                metadata_json=payload,
            ))
            db.flush()
    except Exception:
        # ⚠️ **审计失败不抛** —— 主流程照常。
        logger.warning("问答审计写入失败：actor=%s subject=%s", actor_id, subject_id)


def write_qa_denial_audit(
    db: Session,
    *,
    actor_id: str,
    subject_id: str,
    reason: str,
    period: str = "",
    action: str = ACTION_QUERY_PEER,
) -> None:
    """写一条**越权拒绝**审计。

    ⚠️ **reason 必须是粗粒度的**
    ------------------------
    调用方**不要**把「跨部门」「上一级」这种细粒度原因传进来 ——
    审计表里留这种信息，一旦审计表可被越权者读到，就成了新的泄露面。

    ⇒ 本函数只接受粗粒度 reason（``out_of_scope`` / ``inactive_subject``）。
    """
    write_qa_subject_audit(
        db,
        actor_id=actor_id,
        subject_id=subject_id,
        result=RESULT_DENIED,
        reason=reason,
        period=period,
        action=action,
        metadata={"audit_level": "high"},
    )


__all__ = [
    "ACTION_QUERY_PEER",
    "RESULT_ALLOWED",
    "RESULT_DENIED",
    "write_qa_denial_audit",
    "write_qa_subject_audit",
]
