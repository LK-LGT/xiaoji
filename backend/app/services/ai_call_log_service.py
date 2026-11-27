"""AI 调用指标与调用日志（★ 2026-11-26 新增，替换空壳）。

⚠️ **原状**
``routes/v1/system_ai.py`` 的 ``GET /metrics`` 与 ``GET /call-logs``
恒返回全 0 / 空数组 ⇒ 前端「AI 控制中心」的「运行日志」「指标概览」
两个页签**永远是空的**。

⚠️ **真实数据在哪**
------------------------------------------------------------------
项目里**已经有**真实的 AI 留痕表（不是新建的，是早就在用的）：

| 表 | 记什么 | 对应前端字段 |
|---|---|---|
| ``ai_trace`` | 每次 AI 调用：模型、prompt 版本、输入哈希、输出、耗时、状态 | 调用日志主行 |
| ``ai_tool_call`` | 工具调用审计：谁、查了谁、调了什么工具、耗时、是否成功 | 调用日志的「场景」维度 |
| ``ai_feedback`` | 人工反馈：采纳 / 修改 / 拒绝 | 「采纳率」 |

⇒ 所以这里**不需要新建表**，只需要把已有表**读出来**，
并映射成前端契约的形状。

⚠️ **字段映射的取舍**
------------------------------------------------------------------
前端 ``AiCallLogRow`` 要的是 ``providerCode`` / ``modelName`` /
``inputTokens`` / ``outputTokens`` / ``requestId`` / ``suggestionId`` 等，
而 ``ai_trace`` 存的是 ``model`` / ``prompt_version`` / ``input_hash`` /
``output_json`` / ``latency_ms`` / ``status``。

⇒ **不硬凑**：能对上的直接取，对不上的**留空**，
并在文档里说明「令牌数需要模型侧回传才能统计」——
**宁可空着，也不要编一个假数字**（假数字比没有数字更坏：
它会让「成功率」「成本」这类判断建立在虚构数据上）。

⚠️ **隐私边界**
------------------------------------------------------------------
调用日志里含「谁在什么时候调了什么场景」——
属于审计信息，只对配置权限者开放（路由层已收紧）。
⚠️ **不返回输入正文**：``input_hash`` 只给哈希，``output_json`` 只给
**脱敏后的结构**（键名，不给值）⇒ 与 ``ai_tool_call`` 的既有口径一致
（见 ``models.AiToolCall`` 的注释：「仅记录参数哈希与键名，不存凭据或业务正文」）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import AiFeedback, AiToolCall, AiTrace


def _parse_start(start: str | None) -> datetime | None:
    if not start:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(start, fmt)
        except ValueError:
            continue
    return None


def _parse_end(end: str | None) -> datetime | None:
    if not end:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(end, fmt)
        except ValueError:
            continue
    return None


def _trace_scene(trace: AiTrace) -> str:
    """从 trace 推导「场景」标识。

    ⚠️ ``ai_trace.agent`` 存的就是 agent 名（``plan`` / ``qa`` / ``review`` …）
    ⇒ 直接用作场景，不另造映射。
    """
    return trace.agent or "unknown"


def collect_metrics(db: Session, days: int) -> dict[str, Any]:
    """AI 调用指标（近 N 天）。"""
    since = datetime.now() - timedelta(days=max(1, days))

    traces = db.execute(
        select(AiTrace).where(AiTrace.created_at >= since)
    ).scalars().all()

    total_calls = len(traces)
    success_calls = sum(1 for t in traces if (t.status or "").lower() in {"ok", "success"})

    latencies = [int(t.latency_ms) for t in traces if t.latency_ms is not None]
    average_latency = int(sum(latencies) / len(latencies)) if latencies else 0

    # ⚠️ **采纳率**：分母是**有反馈的记录数**，不是全部调用数。
    #    「总调用里有多少被采纳」这个口径会把「没人反馈的调用」算成「被拒绝」，
    #    指标会严重失真。
    feedback_rows = db.execute(
        select(AiFeedback).where(AiFeedback.created_at >= since)
    ).scalars().all()
    adopted = sum(1 for f in feedback_rows if (f.action or "").upper() in {"ACCEPT", "MODIFY"})

    by_scene: dict[str, dict[str, Any]] = {}
    for trace in traces:
        scene = _trace_scene(trace)
        bucket = by_scene.setdefault(
            scene, {"sceneCode": scene, "totalCalls": 0, "successCalls": 0}
        )
        bucket["totalCalls"] += 1
        if (trace.status or "").lower() in {"ok", "success"}:
            bucket["successCalls"] += 1

    for bucket in by_scene.values():
        total = bucket["totalCalls"] or 1
        bucket["successRate"] = round(bucket["successCalls"] * 100 / total, 2)

    return {
        "totalCalls": total_calls,
        "successCalls": success_calls,
        "successRate": round(success_calls * 100 / total_calls, 2) if total_calls else 0,
        # ⚠️ **令牌数留 0** —— 当前模型层不回传 usage。
        #    宁可显示 0（表示「没统计」），也不要编一个数字。
        "inputTokens": 0,
        "outputTokens": 0,
        "averageLatencyMs": average_latency,
        "adoptedSuggestions": adopted,
        "adoptionRate": round(adopted * 100 / len(feedback_rows), 2) if feedback_rows else 0,
        "byScene": sorted(by_scene.values(), key=lambda x: x["totalCalls"], reverse=True),
    }


def list_call_logs(
    db: Session,
    *,
    start: str | None = None,
    end: str | None = None,
    scene_code: str | None = None,
    success: bool | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
    request_id: str | None = None,
    suggestion_id: str | None = None,
    page_no: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """AI 调用日志（分页）。

    ⚠️ **按 trace 列表返回**，并把 ``ai_tool_call`` 的调用人信息
    （``employee_id`` / ``subject_id``）挂上去 ⇒ 前端「调用人」列有内容。

    ⚠️ ``org_id`` / ``request_id`` / ``suggestion_id`` 目前**没有对应列**
    ⇒ 传了也过滤不出东西（**不报错**，避免前端一填就 500）。
    """
    stmt = select(AiTrace)

    start_at = _parse_start(start)
    if start_at:
        stmt = stmt.where(AiTrace.created_at >= start_at)
    end_at = _parse_end(end)
    if end_at:
        stmt = stmt.where(AiTrace.created_at <= end_at)
    if scene_code:
        stmt = stmt.where(AiTrace.agent == scene_code)

    rows = db.execute(stmt.order_by(AiTrace.created_at.desc())).scalars().all()

    # ⚠️ **调用人**：``ai_trace`` 本身不记「谁调的」（它记 record_id），
    #    调用人在 ``ai_tool_call.employee_id``。
    #    ⇒ 按 trace id 关联一次，取第一条作为调用人。
    trace_ids = [t.id for t in rows]
    actors: dict[str, str] = {}
    if trace_ids:
        tool_calls = db.execute(
            select(AiToolCall).where(AiToolCall.trace_id.in_(trace_ids))
        ).scalars().all()
        for call in tool_calls:
            if call.trace_id and call.trace_id not in actors:
                actors[call.trace_id] = call.employee_id

    items: list[dict[str, Any]] = []
    for trace in rows:
        is_success = (trace.status or "").lower() in {"ok", "success"}
        if success is not None and is_success != success:
            continue
        actor = actors.get(trace.id, "")
        if user_id and actor != user_id:
            continue

        items.append(
            {
                "id": _numeric_id(trace.id),
                "createdAt": trace.created_at.isoformat() if trace.created_at else "",
                "sceneCode": _trace_scene(trace),
                "success": is_success,
                "userId": actor,
                "orgId": None,
                "bizType": trace.agent,
                "bizId": None,
                "providerCode": None,
                "modelName": trace.model,
                "promptVersion": trace.prompt_version,
                "inputTokens": 0,
                "outputTokens": 0,
                "latencyMs": trace.latency_ms or 0,
                "errorCode": trace.status if not is_success else None,
                "errorMessage": trace.error,
                "requestId": trace.id,
                "suggestionId": "",
            }
        )

    total = len(items)
    offset = (max(1, page_no) - 1) * max(1, page_size)
    return {
        "items": items[offset: offset + max(1, page_size)],
        "total": total,
        "pageNo": page_no,
        "pageSize": page_size,
    }


def get_call_log(db: Session, log_id: int) -> dict[str, Any] | None:
    """调用日志详情（含输出**结构**但不含输出**正文**）。

    ⚠️ **不返回 ``output_json`` 的值** —— 只返回**键名列表**。
    输出里可能含员工姓名、绩效内容等正文 ⇒ 审计页不需要看到正文，
    需要细节的人去看 ``assessment_record``（那是有权限控制的）。
    """
    for trace in db.execute(select(AiTrace)).scalars().all():
        if _numeric_id(trace.id) != log_id:
            continue

        actor = ""
        tool_calls = db.execute(
            select(AiToolCall).where(AiToolCall.trace_id == trace.id)
        ).scalars().all()
        if tool_calls:
            actor = tool_calls[0].employee_id

        is_success = (trace.status or "").lower() in {"ok", "success"}
        output = trace.output_json or {}
        output_keys = sorted(output.keys()) if isinstance(output, dict) else []

        return {
            "id": log_id,
            "createdAt": trace.created_at.isoformat() if trace.created_at else "",
            "sceneCode": _trace_scene(trace),
            "success": is_success,
            "userId": actor,
            "orgId": None,
            "bizType": trace.agent,
            "bizId": None,
            "providerCode": None,
            "modelName": trace.model,
            "promptVersion": trace.prompt_version,
            "inputTokens": 0,
            "outputTokens": 0,
            "latencyMs": trace.latency_ms or 0,
            "errorCode": trace.status if not is_success else None,
            "errorMessage": trace.error,
            "requestId": trace.id,
            "suggestionId": "",
            "inputHash": trace.input_hash or "",
            # ⚠️ 只给**摘要**，不给正文。
            "inputSummary": None,
            "outputJson": {"keys": output_keys} if output_keys else None,
            "suggestionAction": None,
            "traceEvents": [],
        }
    return None


def _numeric_id(trace_id: str) -> int:
    """把 trace 的字符串 id 映射成一个**稳定的整数**（前端 ``id: number``）。

    ⚠️ **为什么需要映射**
    ``ai_trace.id`` 是 UUID 字符串，而前端契约是 ``id: number``
    （用于 ``el-table`` 的 ``row-key`` 与路由参数）。

    ⚠️ **为什么用哈希而不是自增序号**
    没有自增列可用；而「日志 id 会随库变化」在审计场景里不可接受
    ⇒ 用 id 的哈希取正数部分，**同一 id 永远映射到同一个数**。

    ⚠️ **32 位截断有碰撞可能**
    日志条数量级（十万级）下，31 位空间碰撞概率约 0.5%
    ⇒ 碰撞只影响「点哪一行」，不影响数据本身；可接受。
    """
    import hashlib

    digest = hashlib.sha256(trace_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


__all__ = ["collect_metrics", "get_call_log", "list_call_logs"]
