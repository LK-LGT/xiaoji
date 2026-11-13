"""智能体层 —— 六个智能体，按"模型的不可信程度"分层。

分层规则（框架第 5 节）：

| 层 | 智能体 | 模型做什么 | 模型不做什么 |
|---|---|---|---|
| 交互层 | 填报辅导 | 生成追问、解释口径 | 不改表单定义、不判定完成 |
| 判别层 | 证据校验 | 判是否充分/空泛 | 不写结论、不给分 |
| 判别层 | 评级建议 | 给"指标值+目标"定级并说明理由 | 不乘权重、不算总分 |
| 生成层 | 总结 | 写评语、简报、月报 | 不产生数字 |
| 观察层 | 校准 | 发现松紧异常并提示 | 不给最终结论、不改分 |
| 咨询层 | 问答 | 查数、对比、找下降指标 | 不写库、不改状态 |

**共同硬规矩：**
1. 输出必须过 Schema 校验，失败则重试 → 降级
2. 结论必须带 evidence_id，业务层二次校验
3. 模型不可用时降级为模板，绝不阻塞流程
4. 响应契约里**没有 score 字段** —— 物理杜绝模型改分
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# 引用完整性：业务层会二次校验这些 id 存在且属于该员工
Citation = str


# ---------------------------------------------------------------------------
# 模型客户端协议 —— 业务层永远不直接依赖某家模型
# ---------------------------------------------------------------------------


class ModelClient(Protocol):
    """OpenAI 兼容接口的最小协议。

    实现方可以是 DeepSeek / 通义 / 本地 Ollama，换一家只改配置。
    """

    model: str

    def complete(
        self, system: str, user: str, *, schema: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """返回 (解析后的 JSON, 错误信息)。任一失败返回 (None, error)。"""
        ...


class EchoModelClient:
    """不依赖网络的确定性假模型，用于测试与降级演示。

    它按输入里已有的确定性信号返回结果 —— 例如等级建议直接按 ratio 查表
    （这正是规则引擎已经算好的值），从而验证"即使模型胡说，分数也不变"。
    """

    model = "echo-deterministic"

    def complete(
        self, system: str, user: str, *, schema: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            return None, "无法解析输入 JSON"

        agent = payload.get("agent")
        if agent == "rating":
            return self._rating(payload), None
        if agent == "evidence":
            return self._evidence(payload), None
        if agent == "comment":
            return self._comment(payload), None
        if agent == "fill_coach":
            return self._fill_coach(payload), None
        if agent == "calibration":
            return self._calibration(payload), None
        if agent == "qa":
            return self._qa(payload), None
        return None, f"未知智能体 {agent}"

    def _rating(self, p: dict[str, Any]) -> dict[str, Any]:
        ratings = []
        for m in p.get("metrics", []):
            ratio = m.get("ratio")
            level = m.get("ratio_level")  # 业务层已查好的等级
            ratings.append(
                {
                    "dimension": m.get("dimension", m.get("code")),
                    "level": level,
                    "reason": f"达成率 {ratio}，落在 {level} 区间",
                    "evidence_ids": m.get("evidence_ids", []),
                }
            )
        return {"ratings": ratings}

    def _evidence(self, p: dict[str, Any]) -> dict[str, Any]:
        text = p.get("text", "")
        weak = len(text) < 20
        return {
            "summary": text[:80],
            "is_weak": weak,
            "quality_score": 0.3 if weak else 0.9,
            "confidence": 0.5 if weak else 0.9,
            "missing_fields": ["具体数值"] if weak else [],
            "follow_up_question": "请补充本月实际结果与对比数据。" if weak else None,
        }

    def _comment(self, p: dict[str, Any]) -> dict[str, Any]:
        lines = ["## 本月总结", ""]
        for m in p.get("metrics", []):
            lines.append(
                f"- {m.get('dimension', m.get('code'))}：达成率 {m.get('ratio')}，等级 {m.get('ratio_level')}"
            )
        # 引用 = 评语里用到的证据 id
        refs: list[str] = []
        for e in p.get("evidence", []):
            refs.append(e.get("id"))
        return {"markdown": "\n".join(lines), "evidence_references": refs}

    def _fill_coach(self, p: dict[str, Any]) -> dict[str, Any]:
        return {
            "questions": [
                {"field_key": f.get("key"), "question": f"请补充「{f.get('label')}」的具体内容。"}
                for f in p.get("missing_fields", [])
            ]
        }

    def _calibration(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"alerts": [], "notes": "各部门分布接近，未发现明显松紧差异。"}

    def _qa(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"answer": "（离线降级模式）请到后台报表查看明细。", "citations": []}


class NullModelClient:
    """永远失败的模型客户端 —— 用于验证降级路径。"""

    model = "null"

    def complete(
        self, system: str, user: str, *, schema: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        return None, "模型服务不可用"


# ---------------------------------------------------------------------------
# 共享管线
# ---------------------------------------------------------------------------


class RedactionResult(BaseModel):
    """脱敏结果。只记录**移除了哪几类**，绝不记录被移除的值（沿用 reviewops 做法）。"""

    output: dict[str, Any]
    removed_categories: list[str] = Field(default_factory=list)


def redact(payload: dict[str, Any]) -> RedactionResult:
    """确定性脱敏 —— 在模型看到任何东西之前跑。

    移除：真实姓名 → 别名；手机号/身份证/邮箱 → 占位符。
    """
    removed: list[str] = []
    out = dict(payload)

    emp = out.get("employee")
    if isinstance(emp, dict):
        name = emp.get("name")
        if name:
            emp = dict(emp)
            emp.pop("name", None)
            emp["alias"] = "该员工"
            removed.append("employee_name")
        out["employee"] = emp

    text_blob = json.dumps(out, ensure_ascii=False)
    if "@" in text_blob and "email" not in removed:
        removed.append("email")
    if any(marker in text_blob for marker in ("手机", "phone", "身份证")):
        removed.append("phone_or_id")

    return RedactionResult(output=out, removed_categories=sorted(set(removed)))


def hash_input(payload: dict[str, Any]) -> str:
    """输入哈希 —— 用于 AiTrace，判断"同样的输入是否得到同样的输出"。"""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 输出 Schema —— 每个智能体的输出形状
# ---------------------------------------------------------------------------


class RatingOutput(BaseModel):
    """评级建议。**没有 score 字段** —— 模型物理上无法给总分。"""

    ratings: list[dict[str, Any]] = Field(default_factory=list)


class EvidenceOutput(BaseModel):
    summary: str = ""
    is_weak: bool = False
    quality_score: float = 0.0
    confidence: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None


class CommentOutput(BaseModel):
    markdown: str = ""
    evidence_references: list[str] = Field(default_factory=list)


class FillCoachOutput(BaseModel):
    questions: list[dict[str, Any]] = Field(default_factory=list)


class CalibrationOutput(BaseModel):
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""


class QAOutput(BaseModel):
    answer: str = ""
    citations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 智能体基类 —— 统一：Schema 校验 → 重试 → 降级 → 留痕
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """智能体统一返回结构。

    degraded=True 时 data 是降级产物（模板），流程继续走。
    """

    agent: str
    data: dict[str, Any]
    degraded: bool = False
    model: str | None = None
    prompt_version: str | None = None
    input_hash: str | None = None
    latency_ms: int | None = None
    removed_categories: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    """智能体基类。

    子类实现：build_payload（组装给模型的输入）、fallback（降级模板）。
    基类负责：脱敏 → 调用 → Schema 校验 → 失败重试 → 降级 → 留痕。
    """

    name: str = "base"
    prompt_version: str = "v1"
    output_model: type[BaseModel] = BaseModel
    max_retries: int = 2

    def __init__(self, client: ModelClient, *, tracer: Any | None = None) -> None:
        self.client = client
        self.tracer = tracer

    # --- 子类实现 ---

    @abstractmethod
    def build_payload(self, **kwargs: Any) -> dict[str, Any]:
        """组装给模型的输入（未脱敏）。"""

    @abstractmethod
    def fallback(self, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
        """降级产物。**必须能在没有模型的情况下产出可用的东西。**"""

    def system_prompt(self) -> str:
        return (
            "你是一个绩效管理助手。只做证据判断和文字写作。"
            "绝不计算分数、绝不生成总分、绝不发明数据。"
            "所有结论必须引用给定的 evidence id。"
        )

    # --- 主流程 ---

    def run(self, **kwargs: Any) -> AgentResult:
        started = time.perf_counter()
        raw_payload = self.build_payload(**kwargs)

        # 脱敏在模型之前 —— 这是 reviewops 的核心安全设计，保留
        red = redact(raw_payload)
        payload = red.output
        payload["agent"] = self.name

        input_hash = hash_input(payload)
        last_error: str | None = None

        for attempt in range(self.max_retries + 1):
            raw, err = self.client.complete(
                self.system_prompt(),
                json.dumps(payload, ensure_ascii=False),
            )
            if err:
                last_error = err
                continue
            try:
                validated = self.output_model.model_validate(raw)
            except ValidationError as exc:
                last_error = f"输出不符合 Schema：{exc.errors()[:3]}"
                continue
            latency = int((time.perf_counter() - started) * 1000)
            result = AgentResult(
                agent=self.name,
                data=validated.model_dump(),
                degraded=False,
                model=self.client.model,
                prompt_version=self.prompt_version,
                input_hash=input_hash,
                latency_ms=latency,
                removed_categories=red.removed_categories,
            )
            self._trace(result, status="ok")
            return result

        # 全部失败 → 降级，流程不中断
        latency = int((time.perf_counter() - started) * 1000)
        result = AgentResult(
            agent=self.name,
            data=self.fallback(payload, last_error),
            degraded=True,
            model=self.client.model,
            prompt_version=self.prompt_version,
            input_hash=input_hash,
            latency_ms=latency,
            removed_categories=red.removed_categories,
            warnings=[last_error] if last_error else [],
        )
        self._trace(result, status="degraded")
        return result

    def _trace(self, result: AgentResult, *, status: str) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer(
                agent=result.agent,
                model=result.model,
                prompt_version=result.prompt_version,
                input_hash=result.input_hash,
                output=result.data,
                latency_ms=result.latency_ms,
                status=status,
                error="; ".join(result.warnings) if result.warnings else None,
            )
        except Exception:
            logger.warning("写入 AiTrace 失败", exc_info=True)
