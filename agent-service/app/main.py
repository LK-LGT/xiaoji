"""智能体服务 —— FastAPI 入口。

对外只暴露一个核心契约：POST /v1/agent/draft
它把「业务层已算好的指标 + 证据」交给智能体，拿到：
  - 评级建议（ratings）—— 不含分数
  - 评语草稿（comment_draft）—— 带引用
  - 证据缺口（gaps）
  - 公平性报告（fairness）

**响应里没有 score 字段。** 分数只由业务层的规则引擎产出。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .agents import build_agents
from .base import AgentResult, EchoModelClient, ModelClient, NullModelClient

app = FastAPI(
    title="绩效智能体服务",
    version="0.1.0",
    description="六个智能体，按模型不可信程度分层；模型不算分、不审批、不自动生效。",
)

# 业务层传进来的服务令牌
_SERVICE_TOKEN = os.getenv("AGENT_SHARED_SECRET", "")
_MODEL_NAME = os.getenv("MODEL_NAME", "echo-deterministic")


def _build_client() -> ModelClient:
    """模型客户端。

    现阶段用确定性假模型（不依赖网络），保证框架可独立跑通。
    接 DeepSeek / 通义时替换此处即可 —— 业务代码零改动。
    """
    if os.getenv("MODEL_MODE") == "null":
        return NullModelClient()
    return EchoModelClient()


_client = _build_client()
_agents = build_agents(_client)


def _verify_token(authorization: str | None) -> None:
    if not _SERVICE_TOKEN:
        return  # 未配置令牌时跳过（本地开发）
    expected = f"Bearer {_SERVICE_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="服务令牌无效")


# ---------------------------------------------------------------------------
# 契约
# ---------------------------------------------------------------------------


class MetricForRating(BaseModel):
    """业务层已算好的指标。

    ratio 与 ratio_level 由业务层规则引擎查表得出 ——
    模型只做语义映射，不自己算。
    """

    code: str
    dimension: str = ""
    value: float
    target: float | None = None
    unit: str | None = None
    ratio: float | None = None
    ratio_level: str | None = None
    level_scale: list[dict[str, Any]] = Field(default_factory=list)
    source: str = ""
    as_of: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceForDraft(BaseModel):
    id: str
    kind: str
    summary: str
    impact: str | None = None
    usage: str = "scoring"
    visibility: str = "share_with_manager"
    source: str = ""
    as_of: str | None = None


class AttendanceAnomaly(BaseModel):
    """考勤异常 —— 独立字段，不混进 evidence（决策②）。"""

    type: str
    count: int = 1
    summary: str
    approved: bool | None = None
    as_of: str | None = None


class DraftRequest(BaseModel):
    task_id: str
    employee: dict[str, Any]
    period: str
    metrics: list[MetricForRating] = Field(default_factory=list)
    evidence: list[EvidenceForDraft] = Field(default_factory=list)
    attendance_anomalies: list[AttendanceAnomaly] = Field(default_factory=list)
    # 评分标准（等级矩阵）
    rubric: list[dict[str, Any]] = Field(default_factory=list)


class DraftResponse(BaseModel):
    """**注意：没有 score 字段。** 物理上杜绝模型改分。"""

    ratings: list[dict[str, Any]] = Field(default_factory=list)
    comment_draft: str = ""
    citations: list[str] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    fairness: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    degraded: bool = False
    agent_status: dict[str, str] = Field(default_factory=dict)


class RatingRequest(BaseModel):
    metrics: list[MetricForRating]
    evidence: list[EvidenceForDraft] = Field(default_factory=list)
    period: str = ""


class RatingResponse(BaseModel):
    ratings: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False


class EvidenceCheckRequest(BaseModel):
    text: str
    question_text: str = ""


class EvidenceCheckResponse(BaseModel):
    summary: str = ""
    is_weak: bool = True
    quality_score: float = 0.0
    confidence: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
    degraded: bool = False


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": _MODEL_NAME,
        "agents": list(_agents.keys()),
    }


@app.post("/v1/agent/draft", response_model=DraftResponse)
def draft(req: DraftRequest, authorization: str | None = Header(default=None)) -> DraftResponse:
    """生成评级建议 + 评语草稿 + 证据缺口。

    三个智能体协同：评级建议 → 评语 → 缺口整理。
    任一失败自动降级，不阻塞流程。
    """
    _verify_token(authorization)

    status: dict[str, str] = {}
    warnings: list[str] = []

    # --- 1. 评级建议 ---
    rating_agent = _agents["rating"]
    r_result: AgentResult = rating_agent.run(
        metrics=[m.model_dump() for m in req.metrics],
        evidence=[e.model_dump() for e in req.evidence],
        period=req.period,
    )
    status["rating"] = "degraded" if r_result.degraded else "ok"
    warnings.extend(r_result.warnings)

    # --- 2. 评语草稿 ---
    comment_agent = _agents["comment"]
    c_result: AgentResult = comment_agent.run(
        employee=req.employee,
        metrics=[m.model_dump() for m in req.metrics],
        evidence=[e.model_dump() for e in req.evidence],
        attendance_anomalies=[a.model_dump() for a in req.attendance_anomalies],
        period=req.period,
    )
    status["comment"] = "degraded" if c_result.degraded else "ok"
    warnings.extend(c_result.warnings)

    # --- 3. 证据缺口 ---
    #    判定标准：某维度**没有关联到任何可引用证据** → 需要补
    #    （而不是"评语里没引用" —— 评语是模型写的，不该用它判断证据够不够）
    
    # 构建证据目录 —— evidence_id → evidence 对象
    evidence_by_id = {e.id: e for e in req.evidence}
    
    gaps: list[dict[str, Any]] = []
    for m in req.metrics:
        # 情况 1：没有关联任何证据
        if not m.evidence_ids:
            gaps.append(
                {
                    "dimension": m.dimension or m.code,
                    "request": f"「{m.dimension or m.code}」缺少可引用的成果证据，请补充具体事例与数据。",
                }
            )
            continue
        
        # 情况 2：关联的证据 ID 在 evidence 里不存在
        missing_ids = [eid for eid in m.evidence_ids if eid not in evidence_by_id]
        if missing_ids:
            gaps.append(
                {
                    "dimension": m.dimension or m.code,
                    "request": f"「{m.dimension or m.code}」引用的证据 ID {missing_ids} 不存在或已删除，请重新关联。",
                }
            )
    
    # 所有实际存在且可用的证据
    linked_evidence_ids = {eid for m in req.metrics for eid in m.evidence_ids if eid in evidence_by_id}

    # 引用集合 = 评语实际引用的 ∪ 各维度已关联的
    cited = set(c_result.data.get("evidence_references", [])) | linked_evidence_ids

    # --- 4. 来源锚定（核心升级） ---
    #    把模型的评级建议逐条对照**封闭来源目录**做原文真实性校验。
    #    校验不通过的结论降级为 UNKNOWN，而不是静默接受。
    grounding = _run_grounding(req, r_result.data.get("ratings", []))

    return DraftResponse(
        ratings=r_result.data.get("ratings", []),
        comment_draft=c_result.data.get("markdown", ""),
        citations=sorted(cited),
        gaps=gaps,
        fairness=grounding,
        degraded=r_result.degraded or c_result.degraded,
        agent_status=status,
    )


def _run_grounding(req: DraftRequest, ratings: list[dict[str, Any]]) -> dict[str, Any]:
    """来源锚定 —— 把评级建议放进封闭来源目录里做原文校验。

    这是从 `enterprise-plan-results` 的 `AiGroundingService` 吸收并用 Python
    重新实现的能力（该项目无 LICENSE，故只读设计语义、不复制代码）。

    与旧版公平性检查的区别：
      旧版：统计有多少结论**没引用**证据（统计级）
      现在：验证引用的引文**是不是真的**在原文里（原文级）
    """
    from .perf_rules import build_grounding_service, build_performance_rules

    svc = build_grounding_service()
    rules = build_performance_rules()

    # 构造业务数据 —— 路径规则与 perf_rules 中的定义严格对应
    business_data: dict[str, Any] = {
        "metrics": [
            {
                "value": m.value,
                "target": m.target,
                "ratio": m.ratio,
                "ratio_level": m.ratio_level,
                "source": m.source,
                "as_of": m.as_of,
                "evidence_ids": ",".join(m.evidence_ids),
            }
            for m in req.metrics
        ],
        "evidence": [
            {"summary": e.summary, "impact": e.impact or ""} for e in req.evidence
        ],
        # 考勤单独放 —— 不与证据混在一起（决策②）
        "attendance": [{"summary": a.summary} for a in req.attendance_anomalies],
        "rubric": [{"levels_json": str(m.level_scale)} for m in req.metrics],
    }

    request = svc.prepare("perf_review", rules, business_data)

    # 把维度结论里的来源路径解析成 source id（模型唯一能引用的句柄）
    path_to_id = {s.path: s.id for s in request.source_catalog}
    raw_dimensions = _build_raw_dimensions(req, ratings)
    for dim in raw_dimensions:
        dim["references"] = _resolve_dimension_refs(dim, path_to_id)

    result = svc.validate(request, {"analysisDimensions": raw_dimensions})

    # 把锚定结果映射回可读的结构
    warnings: list[dict[str, Any]] = []
    for dim in result.dimensions:
        if dim.status == "UNKNOWN":
            warnings.append(
                {
                    "type": "unverified_claim",
                    "message": f"「{dim.title}」的结论未通过原文校验：{dim.basis}",
                    "severity": "high"
                    if dim.rule_id in {"PERF_EVIDENCE", "PERF_DATA_FRESH"}
                    else "medium",
                    "rule_id": dim.rule_id,
                }
            )

    return {
        "grounded": result.grounded,
        "warnings": warnings,
        "downgraded_count": result.downgraded_count,
        "unsupported_claims": sum(1 for d in result.dimensions if d.status == "UNKNOWN"),
        "dimensions": [
            {
                "rule_id": d.rule_id,
                "title": d.title,
                "status": d.status,
                "quote": d.quote,
                "basis": d.basis,
                "references": d.references,
            }
            for d in result.dimensions
        ],
        "cited_evidence": sorted({ref for d in result.dimensions for ref in d.references}),
    }


def _resolve_dimension_refs(
    dim: dict[str, Any], path_to_id: dict[str, str]
) -> list[str]:
    """把维度声明的字段解析成实际来源 id。

    规则 → 需要哪些字段（与 perf_rules.DIMENSION_REQUIRED_FIELDS 呼应）：
      PERF_RESULT     → metrics[0].value + metrics[0].target
      PERF_LEVEL      → metrics[0].ratio + metrics[0].ratio_level
      PERF_EVIDENCE   → evidence[*].summary
      PERF_DATA_FRESH → metrics[0].source + metrics[0].as_of
    """
    rule_id = dim.get("rule_id", "")
    fields = dim.get("_fields", ())
    refs: list[str] = []

    if rule_id in {"PERF_RESULT", "PERF_LEVEL", "PERF_DATA_FRESH"}:
        for field in fields:
            sid = path_to_id.get(f"businessData.metrics[0].{field}")
            if sid:
                refs.append(sid)
    elif rule_id == "PERF_EVIDENCE":
        for path, sid in path_to_id.items():
            if path.startswith("businessData.evidence["):
                refs.append(sid)
    return refs


def _build_raw_dimensions(
    req: DraftRequest, ratings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """构造待校验的维度结论。

    每条结论的 quote 都取自业务层给的数据 —— 模型若改写引文，
    锚定校验会立刻发现"引文不在原文里"并降级。
    """
    if not req.metrics:
        return []

    m = req.metrics[0]
    raw: list[dict[str, Any]] = [
        {
            "rule_id": "PERF_RESULT",
            "status": "PASS" if m.ratio_level else "UNKNOWN",
            "conclusion": f"{m.dimension} 达成率 {m.ratio}，等级 {m.ratio_level}",
            "quote": str(m.value),
            "basis": f"业务层算出的达成率 {m.ratio}",
            "references": [],
            "_fields": ("value", "target"),
        },
        {
            "rule_id": "PERF_LEVEL",
            "status": "PASS" if m.ratio_level else "UNKNOWN",
            "conclusion": f"{m.dimension} 评级为 {m.ratio_level}",
            "quote": str(m.ratio_level),
            "basis": "等级由业务层规则引擎按区间查表得出",
            "references": [],
            "_fields": ("ratio", "ratio_level"),
        },
        {
            "rule_id": "PERF_EVIDENCE",
            "status": "PASS" if req.evidence else "RISK",
            "conclusion": f"共 {len(req.evidence)} 条可引用证据",
            "quote": req.evidence[0].summary if req.evidence else "",
            "basis": "证据卡片来自员工自评与业务数据",
            "references": [],
            "_fields": (),
        },
        {
            "rule_id": "PERF_DATA_FRESH",
            "status": "PASS" if m.source else "UNKNOWN",
            "conclusion": f"数据来源 {m.source}",
            "quote": str(m.source),
            "basis": f"数据截至 {m.as_of or '未标注'}",
            "references": [],
            "_fields": ("source", "as_of"),
        },
    ]
    return raw


@app.post("/v1/agent/rating", response_model=RatingResponse)
def rating(req: RatingRequest, authorization: str | None = Header(default=None)) -> RatingResponse:
    """只要评级建议。"""
    _verify_token(authorization)
    result = _agents["rating"].run(
        metrics=[m.model_dump() for m in req.metrics],
        evidence=[e.model_dump() for e in req.evidence],
        period=req.period,
    )
    return RatingResponse(
        ratings=result.data.get("ratings", []), degraded=result.degraded
    )


@app.post("/v1/agent/evidence-check", response_model=EvidenceCheckResponse)
def evidence_check(
    req: EvidenceCheckRequest, authorization: str | None = Header(default=None)
) -> EvidenceCheckResponse:
    """证据校验。"""
    _verify_token(authorization)
    result = _agents["evidence"].run(text=req.text, question_text=req.question_text)
    data = result.data
    return EvidenceCheckResponse(
        summary=data.get("summary", ""),
        is_weak=data.get("is_weak", True),
        quality_score=data.get("quality_score", 0.0),
        confidence=data.get("confidence", 0.0),
        missing_fields=data.get("missing_fields", []),
        follow_up_question=data.get("follow_up_question"),
        degraded=result.degraded,
    )


@app.post("/v1/agent/reload")
def reload_agents() -> dict[str, Any]:
    """重载模型客户端与智能体（换模型配置后调用，无需重启）。"""
    global _client, _agents
    _client = _build_client()
    _agents = build_agents(_client)
    return {"status": "reloaded", "model": _client.model}
