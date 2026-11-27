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

import hmac
import logging
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .agents import build_agents
from .base import AgentResult, EchoModelClient, ModelClient, NullModelClient
from .openai_client import OpenAICompatibleClient, build_client_from_env
from .plan import PlanRequest, PlanResponse, run_plan
from .qa import QARequest, QAResponse, answer_question
from .sanitize import sanitize_comment


def _load_dotenv() -> None:
    """加载 ``agent-service/.env``（若存在）。

    ⚠️ **不引入 python-dotenv 依赖** —— agent-service 的
    ``requirements.txt`` 只有 fastapi / uvicorn / pydantic，
    而这个服务可能是**独立容器**部署的（不共用后端的 venv）。
    多一个依赖就多一个「部署时忘了装」的坑。

    这里实现一个极简加载器：只认 ``KEY=VALUE``，
    忽略 ``#`` 注释与空行，**不覆盖**已存在的环境变量
    （环境变量优先级更高 —— 容器部署时通常由环境注入）。

    解析刻意保持简单：这是本地开发便利，不是完整的 dotenv 规范。
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:  # pragma: no cover - 读不了就当没有 .env
        pass


_load_dotenv()

logger = logging.getLogger(__name__)

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

    三种模式（`MODEL_MODE`）：
    - ``null``  —— 永远失败，用于演练降级路径
    - ``echo``  —— 确定性假模型（不依赖网络），保证框架可独立跑通
    - ``openai`` —— 真模型（OpenAI 兼容协议：DeepSeek / 通义 / GLM / Ollama）

    换供应商**只改配置**，业务代码零改动。
    真模型配置不全时**自动退回 echo**，不阻塞服务启动。
    """
    mode = (os.getenv("MODEL_MODE") or "echo").strip().lower()
    if mode == "null":
        return NullModelClient()
    if mode == "openai":
        client = build_client_from_env()
        if client is not None:
            return client
        logger.warning("真模型配置不完整 —— 本次退回 echo 模式")
    return EchoModelClient()


_client = _build_client()
_agents = build_agents(_client)
_client_lock = threading.Lock()
_runtime_info: dict[str, Any] = {"mode": (os.getenv("MODEL_MODE") or "echo").lower(), "model": _client.model}


def _verify_token(authorization: str | None) -> None:
    """校验业务层传来的服务令牌。

    ⚠️ R12 安全加固 —— 原实现是「secret 为空就放行」
    --------------------------------------------------
    原代码::

        if not _SERVICE_TOKEN:
            return  # 未配置令牌时跳过（本地开发）

    后果：**未配置令牌 = 完全免鉴权**。4 个端点（含 AI 生成接口）
    对任何能访问该端口的人裸奔。本机开发时因为端口是
    ``127.0.0.1`` 侥幸没出事，但一旦独立部署 / 换 IP /
    放到反向代理后面 —— 立刻变成公网可调用的 AI 接口。

    这类「为了本地开发方便」的开关是典型的**安全债**：
    开发期看起来无害，上线时忘记关就是事故。

    现在的做法（**按最坏情况假设**：这个服务可能被外部访问）：

    1. **secret 为空 → 进程启动即失败**（fail fast）。
       与其「跑着一个裸奔的服务」，不如**起不来** ——
       启动失败会在部署时被立刻发现，而不是等到出事。
    2. 令牌比较用 ``hmac.compare_digest`` —— 防时序侧信道。
    3. 令牌错误 → 401，且**不泄露**是哪一部分错了。

    逃生舱：本地开发如果确实需要免鉴权，
    显式设 ``AGENT_ALLOW_INSECURE=true`` 并**仅限本机**使用。
    这个开关会打日志警告，绝不能带到生产。
    """
    if not _SERVICE_TOKEN:
        # 理论上走不到这里 —— 模块加载时已经 fail fast。
        # 保留这道防线是为了防「运行期有人把环境变量清掉」。
        raise HTTPException(status_code=503, detail="服务令牌未配置")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="服务令牌无效")

    presented = authorization[7:]
    # ⚠️ 恒定时间比较 —— 普通 `==` 会在第一个不同字节处短路返回，
    #    攻击者可以通过响应时间差逐字节猜出令牌。
    if not hmac.compare_digest(presented, _SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="服务令牌无效")


def require_service_token(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI 依赖 —— 校验服务令牌。

    ⚠️ **为什么用依赖而不是在端点函数体里调**：
    FastAPI 的执行顺序是「**先解析并校验请求体**，再调端点函数」。
    若把校验写在函数体里，一个**格式错误**的请求体会先撞上
    422（参数校验失败），而不是 401（未认证）——

    这有两个害处：
    1. **未认证的调用方能探到端点的参数结构**（422 的 detail 会
       告诉你缺哪个字段、类型是什么）—— 这本身就是信息泄露；
    2. 鉴权失败被伪装成参数错误，日志和监控都会误判。

    依赖在**路由匹配之后、请求体解析之前**执行，
    保证「没令牌 → 一律 401」，不泄露任何端点细节。
    """
    _verify_token(authorization)


def _require_startup_secret() -> None:
    """启动时校验令牌已配置 —— **fail fast**。

    ⚠️ 宁可起不来，也不要裸奔。

    逃生舱：``AGENT_ALLOW_INSECURE=true`` 可跳过（仅限本地开发）。
    """
    if _SERVICE_TOKEN:
        return

    if os.getenv("AGENT_ALLOW_INSECURE", "").lower() in ("1", "true", "yes", "on"):
        logger.warning(
            "⚠️ AGENT_ALLOW_INSECURE=true —— 服务令牌未配置，"
            "本实例**完全免鉴权**。仅限本地开发，严禁带到生产。"
        )
        return

    raise RuntimeError(
        "AGENT_SHARED_SECRET 未配置 —— 拒绝启动。\n"
        "本服务是业务层的内部 AI 通道，未鉴权等于把 AI 接口暴露给所有人。\n"
        "请在环境变量中设置 AGENT_SHARED_SECRET（业务层 backend/.env 同名变量需一致）。\n"
        "如需在本地开发临时免鉴权，请显式设置 AGENT_ALLOW_INSECURE=true。"
    )


_require_startup_secret()


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


class RuntimeConfigRequest(BaseModel):
    """运行时模型配置（由业务层下发；不落 agent-service 磁盘）。"""

    mode: str = "openai"  # openai | echo | null
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: float = 60.0
    max_retries: int = 2


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
        "model": getattr(_client, "model", _MODEL_NAME),
        "agents": list(_agents.keys()),
    }


@app.post("/v1/agent/draft", response_model=DraftResponse)
def draft(req: DraftRequest, _auth: None = Depends(require_service_token)) -> DraftResponse:
    """生成评级建议 + 评语草稿 + 证据缺口。

    三个智能体协同：评级建议 → 评语 → 缺口整理。
    任一失败自动降级，不阻塞流程。
    """
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

    # --- 3.5 红线清洗（P3-2 压测驱动） ---
    #    真模型压测发现两个**真实缺陷**：
    #      ① 模型把证据 impact 里的「个人绩效分：95」抄进**评级理由/评语**（分数泄漏）
    #      ② 模型给考勤编了个不存在的引用 ID（`attendance-2026-11-14`）
    #    ⚠️ 这两个都**不能靠提示词解决** —— 分数确实在原文里，ID 是格式需要。
    #       必须在输出侧做**确定性清洗**。
    legal_refs = sorted(evidence_by_id.keys())

    # ⚠️ **原文全集** —— 模型能看到的全部事实性文本。
    #    用于判定引文是「如实引用」还是「模型编造」。
    #    ⚠️ `impact`（审批意见/完成情况）**也是原文** —— 漏了它，
    #    模型如实引用「成果内容与证据符合要求，同意通过」会被误判成编造。
    source_texts: list[str] = []
    for e in req.evidence:
        if e.summary:
            source_texts.append(e.summary)
        if e.impact:
            source_texts.append(e.impact)
    for a in req.attendance_anomalies:
        if a.summary:
            source_texts.append(a.summary)

    # ① 评级理由也要清洗 ——
    #    ⚠️ 实测分数泄漏**主要发生在评级理由**里（3/3 例），
    #    只洗评语会漏掉大头。
    cleaned_ratings: list[dict[str, Any]] = []
    all_removed: list[str] = []
    for item in r_result.data.get("ratings", []):
        if not isinstance(item, dict):
            cleaned_ratings.append(item)
            continue
        cleaned = dict(item)
        reason = str(cleaned.get("reason") or "")
        if reason:
            sanitized_reason = sanitize_comment(
                reason, legal_refs=legal_refs, source_texts=source_texts
            )
            if sanitized_reason.changed:
                cleaned["reason"] = sanitized_reason.text
                all_removed.extend(sanitized_reason.removed)
        cleaned_ratings.append(cleaned)

    # ② 评语清洗
    sanitized = sanitize_comment(
        c_result.data.get("markdown", ""),
        legal_refs=legal_refs,
        source_texts=source_texts,
    )
    all_removed.extend(sanitized.removed)

    if all_removed:
        warnings.append(
            {
                "type": "output_sanitized",
                "message": (
                    f"模型输出中有 {len(all_removed)} 处不符合引用/分数规范的内容已自动移除："
                    + "、".join(all_removed[:5])
                ),
                "severity": "medium",
                "rule_id": "OUTPUT_SANITIZE",
            }
        )
        # 清洗掉的非法引用不能留在 citations 里
        cited -= set(all_removed)

    # --- 4. 来源锚定（核心升级） ---
    #    把模型的评级建议逐条对照**封闭来源目录**做原文真实性校验。
    #    校验不通过的结论降级为 UNKNOWN，而不是静默接受。
    #    ⚠️ 传**清洗后**的 ratings —— 锚定校验的是最终要给人看的文本。
    grounding = _run_grounding(req, cleaned_ratings)

    return DraftResponse(
        ratings=cleaned_ratings,
        comment_draft=sanitized.text,
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
def rating(req: RatingRequest, _auth: None = Depends(require_service_token)) -> RatingResponse:
    """只要评级建议。"""
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
    req: EvidenceCheckRequest, _auth: None = Depends(require_service_token)
) -> EvidenceCheckResponse:
    """证据校验。"""
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


@app.post("/v1/agent/qa", response_model=QAResponse)
def qa(req: QARequest, _auth: None = Depends(require_service_token)) -> QAResponse:
    return answer_question(req, _client)


@app.post("/v1/agent/plan", response_model=PlanResponse)
def plan(req: PlanRequest, _auth: None = Depends(require_service_token)) -> PlanResponse:
    """计划助手 —— 生成 / 调整 / 检查员工计划。

    ⚠️ 输出**只是草稿建议**，不落库、不否决提交。
       最终由员工确认后写入业务层。
    """
    return run_plan(req, _client)


@app.get("/v1/agent/runtime-config")
def get_runtime_config(_auth: None = Depends(require_service_token)) -> dict[str, Any]:
    """返回当前运行时模型（**绝不返回 API Key**）。"""
    return dict(_runtime_info)


@app.post("/v1/agent/runtime-config")
def set_runtime_config(
    req: RuntimeConfigRequest,
    _auth: None = Depends(require_service_token),
) -> dict[str, Any]:
    """业务层下发运行时模型配置，立即生效（无需重启）。

    ⚠️ 只接受 openai / echo / null；openai 缺字段直接 422。
    ⚠️ 响应/日志**绝不包含 API Key**。
    """
    global _client, _agents, _runtime_info
    mode = (req.mode or "echo").strip().lower()
    if mode == "null":
        new_client: ModelClient = NullModelClient()
        info = {"mode": "null", "model": new_client.model, "baseUrl": ""}
    elif mode == "echo":
        new_client = EchoModelClient()
        info = {"mode": "echo", "model": new_client.model, "baseUrl": ""}
    elif mode == "openai":
        if not (req.api_key and req.base_url and req.model):
            raise HTTPException(status_code=422, detail="openai 模式需要 api_key / base_url / model")
        new_client = OpenAICompatibleClient(
            api_key=req.api_key,
            base_url=req.base_url,
            model=req.model,
            timeout=req.timeout_seconds,
            max_retries=req.max_retries,
        )
        info = {"mode": "openai", "model": req.model, "baseUrl": req.base_url}
    else:
        raise HTTPException(status_code=422, detail=f"不支持的 mode：{mode}")

    old_client: ModelClient = _client
    with _client_lock:
        _client = new_client
        _agents = build_agents(new_client)
        _runtime_info = info
    close = getattr(old_client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover - 关闭旧客户端失败不影响新配置
            logger.warning("关闭旧模型客户端失败", exc_info=True)
    return {"status": "applied", **info}


@app.post("/v1/agent/reload")
def reload_agents(_auth: None = Depends(require_service_token)) -> dict[str, Any]:
    """重载模型客户端与智能体（换模型配置后调用，无需重启）。

    ⚠️ R12 —— 原实现**完全没有鉴权**。
    这个端点会重建模型客户端，等价于「切换后端模型」的控制面 ——
    免鉴权意味着任何人都能让你换掉正在服务的模型。
    """
    global _client, _agents, _runtime_info
    with _client_lock:
        _client = _build_client()
        _agents = build_agents(_client)
        _runtime_info = {
            "mode": (os.getenv("MODEL_MODE") or "echo").lower(),
            "model": _client.model,
        }
    return {"status": "reloaded", "model": _client.model}
