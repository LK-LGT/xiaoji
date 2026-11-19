"""按请求绑定短时令牌的问答装配，不接收客户端指定网关地址。"""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agents import QAAgent
from .base import ModelClient
from .tools import ToolRegistry, ToolSpec, http_handler

ToolName = Literal["get_metric_values", "get_evidence", "get_department_stats"]


class QAContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str = Field(pattern=r"^\d{6}$")
    scope: Literal["self", "department"] = "self"
    task_id: str = Field(min_length=1, max_length=64)


class QARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    context: QAContext
    tool_token: str = Field(min_length=1, max_length=8192, repr=False)
    allowed_tools: list[ToolName] = Field(max_length=3)
    trace_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9-]*$")


class QAResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    citations: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    degraded: bool = False
    tool_calls: list[dict] = Field(default_factory=list)
    agent_status: dict[str, str] = Field(default_factory=dict)


def build_tool_registry(token: str, trace_id: str = "") -> ToolRegistry:
    base_url = os.getenv("AGENT_TOOL_BASE_URL", "http://127.0.0.1:8000")
    registry = ToolRegistry()
    for name, description in (
        ("get_metric_values", "读取本人的规则指标结果，必须使用上下文周期"),
        ("get_evidence", "读取本任务证据；考勤仅参考，不自动扣分"),
        ("get_department_stats", "读取授权主部门聚合；少于5个成绩时隐藏均分"),
    ):
        properties = {"period": {"type": "string", "pattern": r"^\d{6}$"}}
        if name == "get_evidence":
            properties["usage"] = {"type": "string", "enum": ["scoring", "reference_only"]}
        registry.register(ToolSpec(
            name=name, description=description,
            parameters={"type": "object", "properties": properties,
                        "required": ["period"] if name == "get_metric_values" else [], "additionalProperties": False},
            handler=http_handler(base_url, token, name, trace_id),
        ))
    return registry


def answer_question(req: QARequest, client: ModelClient) -> QAResponse:
    agent = QAAgent(client)
    result = agent.run_with_tools(
        build_tool_registry(req.tool_token, req.trace_id), allow=set(req.allowed_tools),
        question=req.question, context=req.context.model_dump(),
    )
    return QAResponse(
        answer=result.data["answer"], citations=result.data.get("citations", []),
        sources=result.sources, degraded=result.degraded or bool(result.warnings),
        tool_calls=result.tool_calls, agent_status={"qa": "degraded" if result.degraded or result.warnings else "ok"},
    )
