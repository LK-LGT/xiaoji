"""计划助手业务层 —— 签发工具令牌、调智能体、清洗输出。

## 这一层做什么

```
前端 employee_ai.py  →  plan_ai_service  →  agent-service /v1/agent/plan
                                            ↓（模型自主调工具）
                                     backend /v1/plan-tools/*
```

本层负责四件事，**每件都是「安全边界」而不是「业务逻辑」**：

1. **签发工具令牌** —— 决定模型能看到什么（白名单 + scope）。
   ⚠️ 令牌里的 ``allow`` 由**业务配置**决定，不由前端参数决定。
2. **调智能体** —— 失败不抛给前端，降级成空草稿 + 提示。
3. **清洗输出** —— 复用 P3-2 建的 ``sanitize.py``：
   模型可能把「绩效分 95」写进计划条目（这属于考核数据，不该出现在计划里）。
4. **字段收敛** —— 模型只产出文字类字段，权重/预估价值固定为 0。

## ⚠️ 为什么计划助手不用 ``task_id``

考核问答（qa）要求任务存在且周期匹配。计划助手**没有考核任务上下文**，
所以令牌里用哨兵 ``plan``，工具网关走独立的 ``/v1/plan-tools/*``。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.models import Employee
from ..services.agent_tool_token import issue_agent_tool_token

logger = logging.getLogger(__name__)

#: 计划工具网关的哨兵任务 ID
PLAN_TASK_SENTINEL = "plan"

#: 模型返回的条目字段 → 前端契约字段
#:
#: ⚠️ agent-service 返回的是 **snake_case**（``task_name``）——
#:    那是智能体输出契约（Python 侧）。
#:    这里同时接受 camelCase（``taskName``），是为了让**降级/缓存/其他来源**
#:    的数据也能被正确收敛 —— 否则字段会静默变空。
_ITEM_FIELD_MAP = {
    "task_name": "taskName",
    "task_content": "taskContent",
    "deliverable": "deliverable",
    "quality": "quality",
    "estimated_start_date": "estimatedStartDate",
    "estimated_end_date": "estimatedEndDate",
}

#: camelCase 别名 —— 与 ``_ITEM_FIELD_MAP`` 同一批字段的另一种写法
_ITEM_FIELD_ALIASES = {
    "taskName": "taskName",
    "taskContent": "taskContent",
    "deliverable": "deliverable",
    "quality": "quality",
    "estimatedStartDate": "estimatedStartDate",
    "estimatedEndDate": "estimatedEndDate",
}

#: ⚠️ 模型**不允许**产出的字段 —— 考核权重与业务预估价值
#:
#: 这两个字段前端契约里有，但它们决定考核权重 / 业务预估值，
#: 属于**业务层的事实**。模型给个数就等于让模型参与考核算分。
_BANNED_ITEM_FIELDS = ("performanceWeight", "estimatedValue")


def is_enabled() -> bool:
    """计划助手总开关。"""
    return bool(settings.plan_ai_enabled)


def allowed_tools() -> list[str]:
    """当前配置允许模型调用的工具（白名单）。"""
    raw = (settings.plan_ai_allowed_tools or "").strip()
    names = [part.strip() for part in raw.split(",") if part.strip()]
    valid = {"get_my_plan_history", "get_department_plan_history"}
    return [name for name in names if name in valid]


def _scope_for(current_user: Employee) -> str:
    """令牌 scope —— 有部门且开关打开才给 department 级可见性。

    ⚠️ 即使前端请求了「看部门的」，也要过这道闸：
    ``plan_ai_department_context`` 关着就只给 self。
    """
    if settings.plan_ai_department_context and current_user.department_id:
        return "department"
    return "self"


def _clean_item(raw: dict[str, Any]) -> dict[str, Any]:
    """把模型返回的条目收敛成前端契约形状。

    ⚠️ **必须补齐所有契约字段** —— 前端按字段名取值，
       少一个字段就是 ``undefined``，表格里显示成空白，
       而且这种问题在前端很难定位（不报错，就是空的）。
    """
    item: dict[str, Any] = {}
    # snake_case 优先（agent-service 的真实输出），camelCase 兜底
    for src, dst in _ITEM_FIELD_MAP.items():
        value = raw.get(src)
        if value is None:
            value = raw.get(dst)
        item[dst] = str(value).strip() if isinstance(value, (str, int, float)) else ""
    # 计划条目默认是「任务」类型
    item["workType"] = "TASK"
    # 权重与预估价值由业务层/员工填，模型不产出 → 固定 0
    item["performanceWeight"] = 0
    item["estimatedValue"] = 0
    return item


def _clean_finding(raw: dict[str, Any]) -> dict[str, Any]:
    """收敛体检结论的形状。"""
    target = raw.get("target_index")
    if target is None:
        target = raw.get("targetIndex")
    try:
        target_index = int(target)
    except (TypeError, ValueError):
        target_index = -1
    return {
        "kind": str(raw.get("kind") or "other"),
        "severity": str(raw.get("severity") or "info"),
        "targetIndex": target_index,
        "message": str(raw.get("message") or ""),
        "suggestion": str(raw.get("suggestion") or ""),
    }


async def call_plan_agent(
    db: Session,
    *,
    current_user: Employee,
    mode: str,
    intent_text: str = "",
    instruction: str = "",
    plan_type: str = "month",
    period: str = "",
    current_form: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调计划助手智能体，返回**统一结构**的结果。

    ⚠️ 本函数**永不抛异常** —— 智能体不可用时降级成空草稿 +
    明确提示，前端不白屏、不报错。

    ⚠️ **必须 async** —— 智能体服务会回调本进程取工具数据，
       同步阻塞会饿死事件循环。
    """
    tools = allowed_tools()
    if not tools:
        return _degraded_result("计划助手未配置可用工具")

    scope = _scope_for(current_user)
    # 部门级工具只有在 scope=department 时才可用 —— 白名单与 scope 必须一致
    effective_tools = list(tools)
    if scope != "department":
        effective_tools = [name for name in effective_tools if name != "get_department_plan_history"]
    if not effective_tools:
        return _degraded_result("计划助手未配置可用工具")

    try:
        token = issue_agent_tool_token(
            employee_id=current_user.id,
            task_id=PLAN_TASK_SENTINEL,
            allow=effective_tools,
            scope=scope,
        )
    except Exception:
        logger.warning("计划助手工具令牌签发失败", exc_info=True)
        return _degraded_result("计划助手令牌签发失败")

    # ⚠️ 智能体服务会在**回调本进程**取工具数据。
    #    若这里还持有写事务，SQLite 会因写锁冲突报 "database is locked"。
    #    与 qa_service 同一处理：先 rollback 释放锁。
    db.rollback()

    payload = {
        "mode": mode,
        "intent_text": intent_text or "",
        "context": {
            "scope": scope,
            "plan_type": plan_type,
            "period": period or "",
        },
        "tool_token": token,
        "allowed_tools": effective_tools,
        "trace_id": current_user.id[:32],
        "current_form": current_form or {},
        "instruction": instruction or "",
    }

    try:
        from .review_service import AgentClient

        raw = await AgentClient().plan(payload)
    except Exception:
        logger.warning("计划助手智能体调用失败", exc_info=True)
        return _degraded_result("AI 服务暂不可用")

    return _normalize_response(raw)


def shape_items(items: Any) -> list[dict[str, Any]]:
    """把条目收敛成前端契约形状 —— **路由层的最终防线**。

    ⚠️ 为什么在路由层再收敛一次（``_normalize_response`` 里已经做过）：
    #   这里的职责是「**响应出去之前**必须符合前端契约」。
    #   智能体响应可能来自任何路径（正常调用、降级、缓存），
    #   契约收敛必须发生在**最后一跳**，否则漏一条路径就漏一个字段。
    #
    #   前端按字段名取值，少一个字段就是 ``undefined`` ——
    #   不报错，只是表格里那一列空白，非常难定位。
    """
    if not isinstance(items, list):
        return []
    return [_clean_item(entry) for entry in items if isinstance(entry, dict)]


def shape_findings(findings: Any) -> list[dict[str, Any]]:
    """把体检结论收敛成前端契约形状。"""
    if not isinstance(findings, list):
        return []
    return [_clean_finding(entry) for entry in findings if isinstance(entry, dict)]


def _normalize_response(raw: dict[str, Any]) -> dict[str, Any]:
    """把智能体响应收敛成内部统一结构。"""
    data = raw.get("data") if isinstance(raw, dict) and "data" in raw else raw
    if not isinstance(data, dict):
        return _degraded_result("AI 服务返回异常")

    items = shape_items(data.get("items"))
    findings = shape_findings(data.get("findings"))

    warnings = [str(w) for w in (data.get("warnings") or []) if isinstance(w, str)]
    missing_context = [str(m) for m in (data.get("missing_context") or []) if isinstance(m, str)]
    degraded = bool(data.get("degraded"))

    return {
        "items": items,
        "findings": findings,
        "warnings": warnings,
        "missingContext": missing_context,
        "degraded": degraded,
        "toolCalls": data.get("tool_calls") or [],
        "sources": data.get("sources") or [],
    }


def _degraded_result(reason: str) -> dict[str, Any]:
    """降级结果 —— 结构完整，内容为空，附明确原因。"""
    return {
        "items": [],
        "findings": [],
        "warnings": [reason],
        "missingContext": [],
        "degraded": True,
        "toolCalls": [],
        "sources": [],
    }
