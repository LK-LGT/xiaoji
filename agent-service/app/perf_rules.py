"""绩效场景的规则语义 —— 锚定服务的"业务注入"部分。

`GroundingService` 本身是通用的（来源目录 + 引文校验）。
"哪些来源对某维度是必需的/相关的"这部分是业务语义，Java 版把它硬编码在
`AiGroundingService` 里（针对计划/成果场景）。绩效场景的语义不同，
所以这里单独定义，**与 GroundingService 解耦**。

这样做的价值：换业务场景只换这一个文件，锚定引擎不动。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 绩效维度的来源相关性
#
# 路径约定（由 build_draft_payload 生成的 business_data 决定）：
#   businessData.metrics[i].value            指标实测值
#   businessData.metrics[i].target           指标目标值
#   businessData.metrics[i].ratio            达成率（业务层算的）
#   businessData.metrics[i].ratio_level      等级（业务层算的）
#   businessData.metrics[i].source           数据来源
#   businessData.metrics[i].as_of            数据截至时间
#   businessData.metrics[i].evidence_ids     关联证据 id
#   businessData.evidence[i].summary         证据摘要
#   businessData.evidence[i].impact          证据影响说明
#   businessData.attendance[i].summary       考勤异常摘要（仅参考）
#   businessData.rubric[i].levels_json       等级矩阵定义
# ---------------------------------------------------------------------------

METRIC_FIELDS = ("value", "target", "ratio", "ratio_level", "source", "as_of")


def _is_metric(path: str, field: str | None = None) -> bool:
    """判断路径是否指向某个指标字段。"""
    if not path.startswith("businessData.metrics["):
        return False
    if field is None:
        return True
    tail = path.split(".")[-1]
    return tail == field


def _is_evidence(path: str) -> bool:
    return path.startswith("businessData.evidence[")


def _is_attendance(path: str) -> bool:
    return path.startswith("businessData.attendance[")


def _is_rubric(path: str) -> bool:
    return path.startswith("businessData.rubric[")


# ---------------------------------------------------------------------------
# 维度定义 —— 绩效考核的检查维度
# ---------------------------------------------------------------------------

# 每个维度声明：需要哪些来源字段算"覆盖完整"
DIMENSION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    # 业绩达成：必须有实测值 + 目标值
    "PERF_RESULT": ("value", "target"),
    # 过程质量：需要达成率 + 等级（业务层算的）
    "PERF_LEVEL": ("ratio", "ratio_level"),
    # 证据充分性：必须有证据摘要
    "PERF_EVIDENCE": (),
    # 数据可信度：需要来源 + 数据截至时间
    "PERF_DATA_FRESH": ("source", "as_of"),
}


def required_for_rule(rule_id: str, path: str) -> bool:
    """该维度必须覆盖哪些来源。"""
    fields = DIMENSION_REQUIRED_FIELDS.get(rule_id)
    if fields is None:
        return False
    if not fields:
        # 证据维度不要求特定字段，但要求有证据来源
        return _is_evidence(path)
    return any(_is_metric(path, f) for f in fields)


def relevant_for_rule(rule_id: str, path: str) -> bool:
    """该来源与该维度是否相关。"""
    if rule_id == "PERF_RESULT":
        return _is_metric(path) and not _is_attendance(path)
    if rule_id == "PERF_LEVEL":
        return _is_metric(path, "ratio") or _is_metric(path, "ratio_level") or _is_rubric(path)
    if rule_id == "PERF_EVIDENCE":
        return _is_evidence(path) or _is_metric(path, "evidence_ids")
    if rule_id == "PERF_DATA_FRESH":
        return _is_metric(path, "source") or _is_metric(path, "as_of")
    return False


def risk_invalidates_pass(risk_rule_id: str, pass_rule_id: str) -> bool:
    """某个风险规则是否使某个"通过"规则失效。

    逻辑：证据不足（PERF_EVIDENCE）会让业绩维度的"通过"失去支撑。
    """
    if risk_rule_id == pass_rule_id:
        return True
    if risk_rule_id == "PERF_EVIDENCE":
        return pass_rule_id in {"PERF_RESULT", "PERF_LEVEL"}
    if risk_rule_id == "PERF_DATA_FRESH":
        return pass_rule_id in {"PERF_RESULT", "PERF_LEVEL"}
    return False


def severity_for_rule(rule_id: str) -> str:
    """规则 → 严重级别。"""
    return {
        "PERF_EVIDENCE": "HIGH",
        "PERF_DATA_FRESH": "HIGH",
        "PERF_RESULT": "MEDIUM",
        "PERF_LEVEL": "MEDIUM",
    }.get(rule_id, "MEDIUM")


def build_performance_rules() -> list[Any]:
    """构造绩效场景的标准检查维度。

    返回 GroundingService.Rule 列表。
    """
    from .grounding import Rule

    return [
        Rule(
            id="PERF_RESULT",
            title="业绩达成情况",
            text="核对该员工本期各项经营指标的实测值与目标值，判断是否达成目标。",
        ),
        Rule(
            id="PERF_LEVEL",
            title="评级依据一致性",
            text="核对业务层算出的达成率与等级，判断评级结论是否有据可依。",
        ),
        Rule(
            id="PERF_EVIDENCE",
            title="证据充分性",
            text="核对每项指标是否关联了可引用的成果证据，判断证据是否足以支撑结论。",
        ),
        Rule(
            id="PERF_DATA_FRESH",
            title="数据时效与来源可信度",
            text="核对指标的数据来源与数据截至时间，判断数据是否可用。",
        ),
    ]


def build_grounding_service() -> Any:
    """装配绩效场景的锚定服务。"""
    from .grounding import GroundingService

    return GroundingService(
        required_for_rule=required_for_rule,
        relevant_for_rule=relevant_for_rule,
        risk_invalidates_pass=risk_invalidates_pass,
        severity_for_rule=severity_for_rule,
    )
