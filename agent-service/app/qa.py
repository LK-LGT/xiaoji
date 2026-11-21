"""按请求绑定短时令牌的问答装配，不接收客户端指定网关地址。"""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agents import QAAgent
from .base import ModelClient
from .tools import ToolRegistry, ToolSpec, http_handler

ToolName = Literal[
    "get_metric_values",
    "get_evidence",
    "get_department_stats",
    "get_scoring_breakdown",
    "get_peer_score",
]


class QAContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str = Field(pattern=r"^\d{6}$")
    scope: Literal["self", "department", "peer", "report", "delegate", "hr"] = "self"
    task_id: str = Field(min_length=1, max_length=64)


class QARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    context: QAContext
    tool_token: str = Field(min_length=1, max_length=8192, repr=False)
    allowed_tools: list[ToolName] = Field(max_length=5)
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
        (
            "get_scoring_breakdown",
            "读取本人的评分构成明细：每项指标的权重、等级分、贡献分与算式，"
            "以及未参与算分的指标及原因；用于解释分数是怎么算出来的",
        ),
        (
            "get_peer_score",
            "读取**同部门平级同事**的绩效摘要：总分、等级、各项指标的达成率与贡献分、算式。"
            "⚠️ 只含分数与指标构成，不含评语、证据原文、自评内容。"
            "⚠️ **只能传 period**，不要传员工姓名或工号 —— 目标人由后端令牌决定，"
            "你无法也无法应该指定查谁。",
        ),
    ):
        properties = {"period": {"type": "string", "pattern": r"^\d{6}$"}}
        if name == "get_evidence":
            properties["usage"] = {"type": "string", "enum": ["scoring", "reference_only"]}
        registry.register(ToolSpec(
            name=name, description=description,
            parameters={"type": "object", "properties": properties,
                        "required": ["period"] if name == "get_metric_values" else [], "additionalProperties": False},
            handler=http_handler(base_url, token, name, trace_id),
            trace_id=trace_id,
        ))
    return registry


def answer_question(req: QARequest, client: ModelClient) -> QAResponse:
    agent = QAAgent(client)
    result = agent.run_with_tools(
        build_tool_registry(req.tool_token, req.trace_id), allow=set(req.allowed_tools),
        question=req.question, context=req.context.model_dump(),
    )
    # 被剔除的无效引用数量 —— 用于向前端/审计表明
    # 「这份回答有一部分引用没能对上真实来源」。
    dropped = int(result.data.get("_dropped_citations") or 0)
    status = "degraded" if result.degraded or result.warnings else "ok"
    if dropped:
        # ⚠️ 不标记 degraded：工具本身正常，回答本身也可读。
        #    这里只是**如实告知**引用有缺口，让前端能中性提示。
        status = "citations_dropped"
    return QAResponse(
        answer=result.data["answer"], citations=result.data.get("citations", []),
        sources=result.sources, degraded=result.degraded or bool(result.warnings),
        tool_calls=result.tool_calls,
        agent_status={"qa": status, **({"dropped_citations": str(dropped)} if dropped else {})},
    )
