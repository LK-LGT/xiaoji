"""P1-1 —— 否决（veto）/ 联动扣分（linkage）/ 封顶保底（cap-floor）规则引擎。

设计要点
--------
1. **规则来自冻结快照**（``scheme_payload["rules"]``），不在运行时读配置，
   保证同一份快照可复算、可审计。
2. **只改总分，不改指标原始分**。联动扣分与封顶保底在原始总分之上叠加，
   结果写进 ``trace``，可逐项追溯。
3. **规则配置错误必须阻断**（抛 :class:`ScoringError`）——分数不能是错的。
   但**业务命中**（触发否决、联动扣分）不是错误，是正常结果。

规则语义
--------
``veto``（红线否决）
    当某个指标分低于 ``threshold`` 时，该指标记为"否决项"。
    - ``mode: "grade"``：等级被强制设为 ``result_grade``（例如触犯红线 → 直接 D）
    - ``mode: "zero"``：该指标贡献归零
    - ``mode: "cap"``：总分封顶为 ``max_score``

``linkage``（联动扣分）
    当某指标达到 ``trigger_score``（得分 >= 阈值）时，按 ``deduct`` 扣分。
    支持 ``max_deduct`` 封顶与 ``scope``（``total`` / 指标 code）。

``cap_floor``（封顶保底）
    对最终总分施加 ``min_score`` / ``max_score`` 上下限。

Example:
    ``payload["rules"] = {
        "veto": [
            {"code": "V1", "metric": "K", "threshold": 60, "mode": "grade",
             "result_grade": "D", "name": "红线：合规事故"},
        ],
        "linkage": [
            {"code": "L1", "trigger_metric": "K", "trigger_score": 95,
             "deduct": 5, "max_deduct": 10, "name": "超额完成联动扣减"},
        ],
        "cap_floor": {"min_score": 0, "max_score": 100},
    }``
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .scoring import ScoreResult, ScoringError

#: 允许的否决动作
VETO_MODES = {"grade", "zero", "cap"}


def _rules_of(payload: dict[str, Any]) -> dict[str, Any]:
    """取规则块；缺失时返回空结构（兼容没有规则的历史方案）。"""
    rules = payload.get("rules") or {}
    if not isinstance(rules, dict):
        raise ScoringError("方案规则块格式非法")
    return rules


def _metric_scores(result: ScoreResult) -> dict[str, float]:
    """从 per_metric 提取指标得分。

    ``scoring.py`` 产出的明细键是 ``score``，``scheme_points.py`` 产出的是
    ``contribution``，两者都取。
    """
    scores: dict[str, float] = {}
    for item in result.per_metric:
        code = item.get("metric_code")
        if not code:
            continue
        value = item.get("score")
        if value is None:
            value = item.get("contribution", 0)
        try:
            scores[code] = float(value)
        except (TypeError, ValueError) as exc:
            raise ScoringError(f"指标{code}得分不可解析") from exc
    return scores


def _level_of(result: ScoreResult, total: float) -> str | None:
    """从 trace 取等级。"""
    rating = (result.trace or {}).get("scheme_rating")
    if rating:
        return rating
    grades = (result.trace or {}).get("grades")
    if isinstance(grades, list):
        for g in grades:
            if g.get("min_score", 0) <= total <= g.get("max_score", 100):
                return g.get("level")
    return None


def _grade_for_total(result: ScoreResult, total: float) -> str | None:
    """按总分在冻结等级区间中查等级。

    等级区间可能挂在 ``trace["grade_basis"]`` 或 ``trace["grades"]`` 上。
    """
    trace = result.trace or {}
    grades = trace.get("grades")
    if not grades:
        basis = trace.get("grade_basis")
        if isinstance(basis, dict):
            grades = [basis]
    if not isinstance(grades, list):
        return None
    for g in grades:
        low = float(g.get("min_score", 0))
        high = g.get("max_score")
        if high is None:
            if total >= low:
                return g.get("level")
        elif low <= total <= float(high):
            return g.get("level")
    return None


def apply_rules(payload: dict[str, Any], result: ScoreResult) -> ScoreResult:
    """在原始算分结果之上应用否决 / 联动 / 封顶保底。

    Args:
        payload: 冻结的 ``scheme_payload``
        result: 原始算分结果（**不修改原对象**，返回新结果）

    Returns:
        应用规则后的新 :class:`ScoreResult`，``trace["rules"]`` 记录全部命中过程。

    Raises:
        ScoringError: 规则配置非法时阻断（分数不能是错的）。
    """
    rules = _rules_of(payload)
    total = Decimal(str(result.total))
    scores = _metric_scores(result)
    trace: dict[str, Any] = dict(result.trace or {})
    applied: list[dict[str, Any]] = []
    vetoed_grade: str | None = None

    # ── 1. 红线否决 ────────────────────────────────────────────────
    for rule in rules.get("veto") or []:
        code = rule.get("code") or rule.get("name") or "VETO"
        metric = rule.get("metric")
        mode = rule.get("mode", "zero")
        if mode not in VETO_MODES:
            raise ScoringError(f"否决规则{code}的动作{mode}非法")
        if not metric:
            raise ScoringError(f"否决规则{code}缺少指标")
        if metric not in scores:
            # 该指标不在本次算分范围（可能未参与），不误伤
            continue
        threshold = float(rule.get("threshold", 0))
        value = scores[metric]
        if value >= threshold:
            continue
        if mode == "zero":
            # 该指标贡献归零：total 减去其贡献
            total -= Decimal(str(value))
            applied.append(
                {
                    "type": "VETO",
                    "code": code,
                    "metric": metric,
                    "value": value,
                    "threshold": threshold,
                    "action": "指标贡献归零",
                }
            )
        elif mode == "cap":
            cap = float(rule.get("max_score", 0))
            total = min(total, Decimal(str(cap)))
            applied.append(
                {
                    "type": "VETO",
                    "code": code,
                    "metric": metric,
                    "value": value,
                    "threshold": threshold,
                    "action": f"总分封顶{cap}",
                }
            )
        else:  # mode == "grade"
            vetoed_grade = rule.get("result_grade") or "D"
            applied.append(
                {
                    "type": "VETO",
                    "code": code,
                    "metric": metric,
                    "value": value,
                    "threshold": threshold,
                    "action": f"等级强制为{vetoed_grade}",
                }
            )

    # ── 2. 联动扣分 ────────────────────────────────────────────────
    total_deducted = Decimal(0)
    for rule in rules.get("linkage") or []:
        code = rule.get("code") or rule.get("name") or "LINKAGE"
        trigger_metric = rule.get("trigger_metric")
        if not trigger_metric:
            raise ScoringError(f"联动规则{code}缺少触发指标")
        if trigger_metric not in scores:
            continue
        trigger_score = float(rule.get("trigger_score", 0))
        value = scores[trigger_metric]
        if value < trigger_score:
            continue
        deduct = float(rule.get("deduct", 0))
        max_deduct = rule.get("max_deduct")
        if max_deduct is not None:
            deduct = min(deduct, float(max_deduct))
        if deduct <= 0:
            continue
        scope = rule.get("scope", "total")
        if scope == "total":
            total -= Decimal(str(deduct))
            total_deducted += Decimal(str(deduct))
            applied.append(
                {
                    "type": "LINKAGE",
                    "code": code,
                    "trigger_metric": trigger_metric,
                    "trigger_value": value,
                    "deduct": deduct,
                    "action": f"总分扣{deduct}分",
                }
            )
        else:
            # 作用在某个具体指标上：扣该指标贡献（不低于 0）
            if scope not in scores:
                raise ScoringError(f"联动规则{code}的作用目标{scope}不存在")
            current = scores[scope]
            new_value = max(0.0, current - deduct)
            delta = current - new_value
            total -= Decimal(str(delta))
            total_deducted += Decimal(str(delta))
            applied.append(
                {
                    "type": "LINKAGE",
                    "code": code,
                    "trigger_metric": trigger_metric,
                    "trigger_value": value,
                    "target_metric": scope,
                    "deduct": deduct,
                    "actual_deduct": round(delta, 4),
                    "action": f"指标{scope}扣{deduct}分",
                }
            )

    # ── 3. 封顶保底 ────────────────────────────────────────────────
    cap_floor = rules.get("cap_floor") or {}
    if cap_floor:
        min_score = cap_floor.get("min_score")
        max_score = cap_floor.get("max_score")
        if min_score is not None and total < Decimal(str(min_score)):
            applied.append(
                {
                    "type": "CAP_FLOOR",
                    "action": f"保底提升至{min_score}",
                    "from": float(total),
                }
            )
            total = Decimal(str(min_score))
        if max_score is not None and total > Decimal(str(max_score)):
            applied.append(
                {
                    "type": "CAP_FLOOR",
                    "action": f"封顶降至{max_score}",
                    "from": float(total),
                }
            )
            total = Decimal(str(max_score))

    # ── 4. 等级联动：否决强制等级优先于分数查表 ──────────────────
    final_total = float(total.quantize(Decimal("0.01")))
    rating = None
    if vetoed_grade:
        rating = vetoed_grade
    else:
        rating = _grade_for_total(result, final_total)
        if rating is None:
            rating = _level_of(result, final_total)

    trace["rules"] = {
        "applied": applied,
        "original_total": result.total,
        "final_total": final_total,
        "deducted": float(total_deducted.quantize(Decimal("0.01"))),
        "vetoed_grade": vetoed_grade,
        "result_rating": rating,
    }
    if rating:
        trace["scheme_rating"] = rating

    return ScoreResult(
        total=final_total,
        per_metric=result.per_metric,
        trace=trace,
        warnings=list(result.warnings or []),
    )
