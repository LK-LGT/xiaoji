"""六个智能体 —— 按"模型的不可信程度"分层实现。

交互层（最需要对话）→ 判别层 → 生成层 → 观察层 → 咨询层。

每层的降级策略不同：
- 交互层降级 → 不追问，只提示"请补充"（不阻塞提交）
- 判别层降级 → 标记"待人工判断"，进主管队列（不阻塞评分）
- 判别层(评级)降级 → 不给建议，只给原始指标表
- 生成层降级 → 模板化评语 + 指标表
- 观察层降级 → 只出对比表
- 咨询层降级 → 明确提示不可用，指路到后台报表
"""

from __future__ import annotations

from typing import Any

from .base import (
    BaseAgent,
    CalibrationOutput,
    CommentOutput,
    EvidenceOutput,
    FillCoachOutput,
    QAOutput,
    RatingOutput,
)

# ---------------------------------------------------------------------------
# 交互层
# ---------------------------------------------------------------------------


class FillCoachAgent(BaseAgent):
    """填报辅导 —— 生成追问、解释指标口径。

    模型不做什么：不修改表单定义、不判定"算不算完成"。
    """

    name = "fill_coach"
    prompt_version = "v1"
    output_model = FillCoachOutput

    def build_payload(
        self,
        *,
        fields: list[dict[str, Any]],
        answers: dict[str, Any],
        metrics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        missing = []
        for f in fields:
            key = f.get("key")
            required = f.get("required", False)
            val = (answers or {}).get(key)
            empty = val is None or (isinstance(val, str) and not val.strip())
            if required and empty:
                missing.append({"key": key, "label": f.get("label"), "explanation": f.get("explanation")})
            elif not empty and isinstance(val, str) and len(val.strip()) < 20:
                # 写得太空 —— 需要追问
                missing.append({"key": key, "label": f.get("label"), "explanation": f.get("explanation")})
        return {
            "fields": fields,
            "answers": answers or {},
            "missing_fields": missing,
            "metrics": metrics or [],
        }

    def fallback(self, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
        """降级：不追问，只列出必填项。绝不让填报流程卡住。"""
        missing = payload.get("missing_fields", [])
        return {
            "questions": [
                {"field_key": m.get("key"), "question": f"请补充「{m.get('label')}」。"}
                for m in missing
            ]
        }


# ---------------------------------------------------------------------------
# 判别层
# ---------------------------------------------------------------------------


class EvidenceValidationAgent(BaseAgent):
    """证据校验 —— 判断证据是否充分、是否空泛。

    模型不做什么：不写结论、不给分。
    """

    name = "evidence"
    prompt_version = "v1"
    output_model = EvidenceOutput

    def build_payload(
        self,
        *,
        text: str,
        question_text: str = "",
        metric_context: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "text": text,
            "question_text": question_text,
            "metric_context": metric_context or [],
        }

    def fallback(self, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
        """降级：标记"待人工判断"，交给主管。"""
        return {
            "summary": (payload.get("text") or "")[:200],
            "is_weak": True,
            "quality_score": 0.0,
            "confidence": 0.0,
            "missing_fields": ["证据质量待人工判断（AI 校验不可用）"],
            "follow_up_question": None,
        }


class RatingSuggestionAgent(BaseAgent):
    """评级建议 —— 给"指标值 + 目标"定级并说明理由。

    **模型不乘权重、不算总分、不改 Score。**
    输入里的 ratio 和 ratio_level 是业务层已经算好的 —— 模型只做语义映射和理由。
    """

    name = "rating"
    prompt_version = "v1"
    output_model = RatingOutput

    def build_payload(
        self,
        *,
        metrics: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        period: str = "",
    ) -> dict[str, Any]:
        # metrics 里每项已含：code / dimension / value / target / ratio / ratio_level / level_scale
        return {
            "metrics": metrics,
            "evidence": evidence,
            "period": period,
            "instruction": "为每个维度给出等级和理由。等级必须落在 level_scale 给出的取值内。",
        }

    def fallback(self, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
        """降级：不给建议，只给原始指标表（主管自己判断）。"""
        return {
            "ratings": [
                {
                    "dimension": m.get("dimension", m.get("code")),
                    "level": m.get("ratio_level"),
                    "reason": "AI 评级建议不可用，此处为规则引擎算出的达成率，请人工判断",
                    "evidence_ids": [],
                }
                for m in payload.get("metrics", [])
            ]
        }


# ---------------------------------------------------------------------------
# 生成层
# ---------------------------------------------------------------------------


class SummarizeAgent(BaseAgent):
    """总结 —— 写评语、部门简报、公司月报。

    模型不产生数字：所有数字由业务层填入模板。
    """

    name = "comment"
    prompt_version = "v1"
    output_model = CommentOutput

    def build_payload(
        self,
        *,
        employee: dict[str, Any],
        metrics: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        attendance_anomalies: list[dict[str, Any]] | None = None,
        period: str = "",
    ) -> dict[str, Any]:
        return {
            "employee": employee,
            "metrics": metrics,
            "evidence": evidence,
            # 考勤单独一个字段，不混进 evidence —— 模型无法把它当结论支撑
            "attendance_anomalies": attendance_anomalies or [],
            "period": period,
            "instruction": (
                "写一份个人月度总结。每条结论必须引用 evidence id。"
                "如果提到考勤，必须写明「考勤异常不自动扣分，仅作参考」。"
                "不要自己计算或复述分数。"
            ),
        }

    def fallback(self, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
        """降级：模板化评语 + 指标表。"""
        lines = ["## 本月总结", ""]
        for m in payload.get("metrics", []):
            lines.append(
                f"- **{m.get('dimension', m.get('code'))}**：实际 {m.get('value')} {m.get('unit') or ''}，"
                f"目标 {m.get('target')}，达成率 {m.get('ratio')}，等级 {m.get('ratio_level')}"
            )
        lines.append("")
        lines.append("> AI 总结暂不可用，以上为规则引擎计算的指标明细。")
        return {"markdown": "\n".join(lines), "evidence_references": []}


# ---------------------------------------------------------------------------
# 观察层
# ---------------------------------------------------------------------------


class CalibrationAgent(BaseAgent):
    """校准 —— 发现部门间松紧异常并提示。

    模型不做什么：不给最终结论、不改任何人的分。
    """

    name = "calibration"
    prompt_version = "v1"
    output_model = CalibrationOutput

    def build_payload(
        self, *, dept_stats: list[dict[str, Any]], reference: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "dept_stats": dept_stats,
            "reference": reference or {},
            "instruction": "指出明显偏松或偏紧的部门。不给最终结论，只提示。",
        }

    def fallback(self, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
        """降级：只出对比表，不做判断。"""
        stats = payload.get("dept_stats", [])
        lines = ["部门分布对比："]
        for s in stats:
            lines.append(f"- {s.get('department')}：均分 {s.get('avg')}，优秀率 {s.get('excellent_rate')}")
        return {"alerts": [], "notes": "\n".join(lines)}


# ---------------------------------------------------------------------------
# 咨询层
# ---------------------------------------------------------------------------


class QAAgent(BaseAgent):
    """问答 —— HR 用自然语言查数。

    模型不做什么：不写数据库、不改状态。
    """

    name = "qa"
    prompt_version = "v1"
    output_model = QAOutput

    def build_payload(self, *, question: str, context: dict[str, Any]) -> dict[str, Any]:
        return {"question": question, "context": context}

    def fallback(self, payload: dict[str, Any], error: str | None) -> dict[str, Any]:
        """降级：明确告知不可用并指路。"""
        return {
            "answer": "AI 问答暂不可用，请到后台「汇总报表」查看明细。",
            "citations": [],
        }


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def build_agents(client: Any, *, tracer: Any | None = None) -> dict[str, BaseAgent]:
    """一次性装配六个智能体。"""
    return {
        FillCoachAgent.name: FillCoachAgent(client, tracer=tracer),
        EvidenceValidationAgent.name: EvidenceValidationAgent(client, tracer=tracer),
        RatingSuggestionAgent.name: RatingSuggestionAgent(client, tracer=tracer),
        SummarizeAgent.name: SummarizeAgent(client, tracer=tracer),
        CalibrationAgent.name: CalibrationAgent(client, tracer=tracer),
        QAAgent.name: QAAgent(client, tracer=tracer),
    }
