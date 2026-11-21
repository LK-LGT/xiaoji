"""计划助手装配 —— 按请求绑定短时令牌，不接收客户端指定网关地址。

⚠️ **与 ``qa.py`` 的关键差异**：

qa 的工具网关是 ``/v1/agent-tools/*``（考核任务域），
plan 的工具网关是 ``/v1/plan-tools/*``（计划域，与考核任务解耦）。

两者共用同一个 ``AGENT_TOOL_BASE_URL``（后端服务地址），
但**路径前缀不同**、令牌里的 ``allow`` 白名单不同。
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import ModelClient
from .plan_agent import PlanAgent
from .sanitize import sanitize_comment
from .tools import ToolRegistry, ToolSpec, http_handler

PlanToolName = Literal["get_my_plan_history", "get_department_plan_history"]
PlanMode = Literal["generate", "adjust", "check"]


class PlanContext(BaseModel):
    """计划助手上下文。

    ⚠️ **没有** ``task_id`` —— 计划助手与考核任务解耦。

    ⚠️⚠️ **``period`` 必须接受业务层真实传的格式**（2026-11-21 修复）

    原来的 pattern 是 ``^$|^\\d{6}$``（只认 ``202611``），
    但业务层传的是**前端原样的值**：

    ==============  ===================  ==========================
    场景            前端字段              实际值
    ==============  ===================  ==========================
    月计划          ``planMonth``         ``2026-11``（``YYYY-MM``）
    周计划          ``weekStart``         ``2026-11-17``（``YYYY-MM-DD``）
    日计划          ``planDate``          ``2026-11-17``
    ==============  ===================  ==========================

    ⇒ **月/周/日三条链路全部 422** → 后端降级成空草稿 →
    用户看到「AI 建议暂不可用」，而**前端一点提示都没有**。

    ⚠️ 这个 bug 之所以能活下来，是因为**测试和压测脚本都用了
    ``202611``**（唯一能通过旧 pattern 的格式）——
    测试造数和业务调用**用的不是同一个格式**，断言照过。
    """

    model_config = ConfigDict(extra="forbid")

    scope: Literal["self", "department"] = "self"
    plan_type: Literal["month", "week", "day"] = "month"
    period: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}$|^\d{4}-\d{2}-\d{2}$")

    @field_validator("period", mode="before")
    @classmethod
    def _normalize_period(cls, value: Any) -> Any:
        """把周期归一化成 ``YYYY-MM-DD``（日/周）或 ``YYYY-MM``（月）。

        ⚠️ 只做**格式归一**，不做业务判断 —— 越界日期交给业务层。

        ⚠️⚠️ **为什么归一化成「带横线」而不是「紧凑 8 位」**（2026-11-21 实测）

        紧凑格式 ``20261117`` 对**模型**是歧义的 ——
        实测模型把它读成了复数 ``periods=20261117``，
        并据此**推断出错误的计划起止日期**（把 11-17 推成 11-17~11-20）。

        ⇒ 周期是要**给人读也要给模型读**的字段，
        必须用模型不会误读的 ``YYYY-MM-DD`` / ``YYYY-MM``。
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return ""
        if re.fullmatch(r"\d{8}", text):
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        if re.fullmatch(r"\d{6}", text):
            return f"{text[:4]}-{text[4:]}"
        # 归一化失败就原样返回，让 pattern 去拒 —— 不要在这里静默吞掉
        return text



class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: PlanMode = "generate"
    intent_text: str = Field(default="", max_length=2000)
    context: PlanContext = Field(default_factory=PlanContext)
    tool_token: str = Field(min_length=1, max_length=8192, repr=False)
    allowed_tools: list[PlanToolName] = Field(max_length=2)
    trace_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9-]*$")
    # 已有草稿（adjust / check 模式用）
    current_form: dict[str, Any] = Field(default_factory=dict)
    instruction: str = Field(default="", max_length=2000)


class PlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    degraded: bool = False
    tool_calls: list[dict] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    agent_status: dict[str, str] = Field(default_factory=dict)


def build_plan_tool_registry(token: str, trace_id: str = "") -> ToolRegistry:
    """注册计划域的只读工具。

    ⚠️ 参数 schema 里**禁止身份字段**（``IDENTITY_PARAMS``）——
       ``ToolRegistry.register`` 会强制检查。
       查谁的计划由**令牌**决定，不由参数决定。
    """
    base_url = os.getenv("AGENT_TOOL_BASE_URL", "http://127.0.0.1:8000")
    registry = ToolRegistry()
    specs = (
        (
            "get_my_plan_history",
            "读取我自己的历史计划（月/周/日）。用于参考以往做过什么、避免重复。",
            {"months": {"type": "integer", "minimum": 1, "maximum": 24},
             "plan_type": {"type": "string", "enum": ["month", "week", "day"]}},
            ["months", "plan_type"],
        ),
        (
            "get_department_plan_history",
            "读取同部门历史计划（已脱敏，无具体人名）。用于了解部门常见工作方向。",
            {"months": {"type": "integer", "minimum": 1, "maximum": 24},
             "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            ["months", "limit"],
        ),
    )
    for name, description, properties, required in specs:
        registry.register(ToolSpec(
            name=name, description=description,
            parameters={"type": "object", "properties": properties,
                        "required": required, "additionalProperties": False},
            handler=http_handler(base_url, token, name, trace_id, prefix="/v1/plan-tools"),
            trace_id=trace_id,
        ))
    return registry


def _sanitize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对模型产出的计划条目做红线清洗。

    ⚠️ 为什么计划场景也要清洗分数泄漏：
    模型在「总结」场景会把证据里的「个人绩效分 95」抄进评语（P3-2 实测）。
    同一个模型在「计划」场景会犯同样的错 —— 而且更糟：
    计划会变成**考核指标**，分数泄漏到计划里等于把考核结论提前写进计划。

    ⚠️ 只清文本字段，**不动**结构字段（kind / severity / target_index）。
    """
    text_fields = ("task_name", "task_content", "deliverable", "quality")
    cleaned: list[dict[str, Any]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        for field in text_fields:
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                item[field] = sanitize_comment(value).text
        cleaned.append(item)
    return cleaned


def _sanitize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对体检结论的文本做清洗。"""
    text_fields = ("message", "suggestion")
    cleaned: list[dict[str, Any]] = []
    for entry in findings:
        if not isinstance(entry, dict):
            continue
        finding = dict(entry)
        for field in text_fields:
            value = finding.get(field)
            if isinstance(value, str) and value.strip():
                finding[field] = sanitize_comment(value).text
        cleaned.append(finding)
    return cleaned


def run_plan(req: PlanRequest, client: ModelClient) -> PlanResponse:
    """执行一次计划助手任务。"""
    agent = PlanAgent(client)
    context: dict[str, Any] = {
        **req.context.model_dump(),
        "current_form": req.current_form,
        "instruction": req.instruction,
    }
    result = agent.run_with_tools(
        build_plan_tool_registry(req.tool_token, req.trace_id),
        allow=set(req.allowed_tools),
        mode=req.mode,
        intent_text=req.intent_text,
        context=context,
    )
    data = result.data or {}
    # ⚠️ **工具失败 ≠ 输出降级**
    #
    # 计划助手和问答的语义不同：
    # - 问答里工具是**唯一**的数据来源 —— 工具挂了，答案就没有依据，
    #   所以问答里工具失败 = degraded（既有行为，不动）。
    # - 计划助手里工具提供的是**参考材料**（历史计划）——
    #   工具挂了模型仍能给出草稿，只是少了参考。
    #
    # 所以这里只在**模型输出本身**降级时才标 degraded；
    # 工具失败以 warnings 如实上报（前端能看到「参考数据没取到」）。
    degraded = result.degraded
    warnings = list(data.get("warnings", []))
    for warning in result.warnings:
        if warning not in warnings:
            warnings.append(warning)
    return PlanResponse(
        items=_sanitize_items(data.get("items", [])),
        findings=_sanitize_findings(data.get("findings", [])),
        warnings=warnings,
        missing_context=data.get("missing_context", []),
        degraded=degraded,
        tool_calls=result.tool_calls,
        sources=result.sources,
        agent_status={"plan": "degraded" if degraded else "ok"},
    )
