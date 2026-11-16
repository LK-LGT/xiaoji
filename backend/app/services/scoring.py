"""规则引擎 —— 分数的唯一产地。

**这是全框架最硬的一块代码。** 模型永远不碰数字：
- 权重来自冻结快照
- 目标值来自 MetricValue
- 达成率 = value / target，由程序算
- 等级由 Rubric 区间映射，由程序查表
- 得分 = 权重 × 等级分，由程序乘

模型只拿到已经算好的 "指标值 + 目标 + 区间表"，做区间到等级的语义判断和理由说明。
本模块**不 import 任何模型客户端**，纯确定性，可复算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 等级 → 分数系数。默认 A/B/C 三档，可在 Rubric 里覆盖。
DEFAULT_LEVEL_SCORE: dict[str, float] = {"A": 100.0, "B": 80.0, "C": 60.0, "D": 40.0}


@dataclass(frozen=True)
class MetricSpec:
    """参与算分的单个指标规格（来自冻结快照）。"""

    code: str
    name: str
    weight: float
    unit: str | None = None
    levels: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class MetricInput:
    """指标实测值（来自 MetricValue 算分集）。"""

    code: str
    value: float
    target: float | None
    unit: str | None = None
    source: str = ""
    as_of: str | None = None


@dataclass
class ScoreResult:
    """算分结果。per_metric 保留每项明细，供复算与追溯。"""

    total: float
    per_metric: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "per_metric": self.per_metric,
            "trace": self.trace,
            "warnings": self.warnings,
        }


class ScoringError(Exception):
    """算分失败（配置错误）。这类错误必须阻断，不能降级 —— 分数不能是错的。"""


def _ratio(value: float, target: float | None) -> float | None:
    """达成率。target 缺失或为 0 时无法计算，返回 None。"""
    if target is None or target == 0:
        return None
    return value / target


def _match_level(ratio: float, levels: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    """按区间查表定级。

    区间约定：[min_ratio, max_ratio)，max_ratio 为 None 表示无上限。
    这与给 AI 的 level_scale 是同一套结构，保证模型和程序用同一把尺子。
    """
    for lvl in levels:
        lo = lvl.get("min_ratio")
        hi = lvl.get("max_ratio")
        label = lvl.get("level", "")
        if lo is not None and ratio < float(lo):
            continue
        if hi is not None and ratio >= float(hi):
            continue
        return lvl
    return None


def _level_score(level: dict[str, Any]) -> float:
    """等级 → 分数。优先用 level 自带的 score，其次用默认映射。"""
    if "score" in level and level["score"] is not None:
        return float(level["score"])
    return DEFAULT_LEVEL_SCORE.get(str(level.get("level", "")).upper(), 0.0)


def score_record(
    specs: list[MetricSpec],
    inputs: list[MetricInput],
    *,
    level_scores: dict[str, float] | None = None,
) -> ScoreResult:
    """算分主入口。

    规则：
    1. 权重合计必须 = 100，否则抛 ScoringError（决策③读入校验的运行时兜底）
    2. **算不出分数的指标一律不参与**，权重按实际参与项重新归一
       —— 避免"缺数据/缺配置 = 扣分"（决策③的核心原则）
    3. 总分 = Σ(归一化权重 × 等级分)

    什么算"算不出分数"（指标不参与，权重重新归一）：
        a. 该指标没有 `MetricValue` 数据
        b. 该指标没有目标值（`target` 为 NULL 或 0）→ 算不出达成率
        c. 该指标的 rubric 条目缺失（`levels` 为空）→ 定不了级
        d. 达成率未命中任何等级区间 → rubric 有缝隙（配置问题）

    ⚠️ 这四类在旧实现里分成了两拨：
       a 是"不参与"（不扣分），而 b/c/d 是"参与但记 0 分"（**扣分**）。
       结果是：同一个指标**彻底没数据反而一分不扣**，
       只是目标值缺失 / rubric 配错就被静默扣掉整块权重。
       语义上说不通 —— 配置瑕疵不该伪装成业务低绩效。

       现在统一为"不参与"，并把原因写进 `warnings`（**必须落库可见**，
       否则运维看到分数却不知道有指标没参与成功）。
    """
    level_scores = level_scores or DEFAULT_LEVEL_SCORE

    if not specs:
        raise ScoringError("指标规格为空，无法算分")

    total_weight = sum(s.weight for s in specs)
    if abs(total_weight - 100.0) > 1e-6:
        raise ScoringError(f"权重合计必须等于 100，当前为 {total_weight}")

    # 权重必须非负。
    #
    # ⚠️ 合计 = 100 这个检查单独用是不够的：`-70 + 170 = 100` 能通过，
    #    但归一化后会产生**负权重**，把「好的指标」扣成负贡献、把「差的指标」
    #    放大成 >100 的贡献，最终总分跑到 0~100 之外（实测能算出 128）。
    #    这类配置必须阻断，不能出分。
    negative = [s.code for s in specs if s.weight < 0]
    if negative:
        raise ScoringError(f"权重不得为负：{sorted(negative)}")
    zero = [s.code for s in specs if s.weight == 0]
    if zero:
        raise ScoringError(f"权重不得为 0：{sorted(zero)}")

    by_code = {i.code: i for i in inputs}

    # 第一轮：算出每个指标的达成率与等级，识别缺失项
    # ⚠️ 能进 `resolved` 的指标一定**已定级**（level 非 None）——
    #    算不出分数的指标在第一轮就 `continue` 掉了，不参与算分。
    resolved: list[tuple[MetricSpec, float, dict[str, Any], MetricInput | None]] = []
    warnings: list[str] = []

    for spec in specs:
        mv = by_code.get(spec.code)
        if mv is None:
            warnings.append(f"指标 {spec.code} 缺少数据，未参与算分")
            continue
        if not spec.levels:
            warnings.append(f"指标 {spec.code} 缺少等级区间（rubric 未配置），未参与算分")
            continue
        ratio = _ratio(mv.value, mv.target)
        if ratio is None:
            warnings.append(f"指标 {spec.code} 缺少目标值，无法计算达成率，未参与算分")
            continue
        level = _match_level(ratio, spec.levels)
        if level is None:
            warnings.append(
                f"指标 {spec.code} 达成率 {ratio:.4f} 未命中任何等级区间"
                f"（等级区间有缝隙），未参与算分"
            )
            continue
        resolved.append((spec, ratio, level, mv))

    if not resolved:
        raise ScoringError("所有指标均缺少可用数据，无法算分")

    # 第二轮：按实际参与项归一权重（缺数据不连带扣其他指标的权）
    participating_weight = sum(s.weight for s, _, _, _ in resolved)
    if participating_weight <= 0:
        raise ScoringError("参与计算的权重合计为 0")

    per_metric: list[dict[str, Any]] = []
    total = 0.0

    for spec, ratio, level, mv in resolved:
        norm_weight = spec.weight / participating_weight
        # ⚠️ 这里不再有 `level is None` 分支 —— 能进 `resolved` 的指标
        #    一定已经定到级（见第一轮的三处 `continue`）。
        lvl = str(level.get("level", ""))
        level_score = (
            float(level["score"]) if level.get("score") is not None else level_scores.get(lvl, 0.0)
        )
        level_label = lvl
        level_desc = level.get("desc")

        contribution = norm_weight * level_score
        total += contribution

        per_metric.append(
            {
                "metric_code": spec.code,
                "metric_name": spec.name,
                "raw_weight": spec.weight,
                "normalized_weight": round(norm_weight, 6),
                "value": mv.value if mv else None,
                "target": mv.target if mv else None,
                "ratio": round(ratio, 6) if ratio is not None else None,
                "level": level_label,
                "level_desc": level_desc,
                "level_score": level_score,
                "contribution": round(contribution, 4),
                "unit": spec.unit or (mv.unit if mv else None),
                "source": mv.source if mv else None,
                "as_of": mv.as_of if mv else None,
            }
        )

    return ScoreResult(
        total=round(total, 2),
        per_metric=per_metric,
        trace={
            "rule": "level_match_v1",
            "spec_count": len(specs),
            "participating_count": len(resolved),
            "total_weight": total_weight,
            "participating_weight": participating_weight,
            "level_scores": level_scores,
            # ⚠️ warnings 必须**落库**。
            #    旧实现算出了 warnings 却没人消费 —— 运维看到分数
            #    完全不知道有指标因为缺数据/缺目标值/rubric 配错而没参与。
            "warnings": warnings,
        },
        warnings=warnings,
    )


def build_specs(
    metrics_json: list[dict[str, Any]],
    weights_json: list[dict[str, Any]],
    rubric_json: list[dict[str, Any]],
) -> list[MetricSpec]:
    """从冻结快照的三段配置组装指标规格。"""
    metric_by_code = {m["code"]: m for m in metrics_json}
    rubric_by_code = {r["metric_code"]: r for r in rubric_json}

    specs: list[MetricSpec] = []
    for w in weights_json:
        code = w["metric_code"]
        metric = metric_by_code.get(code)
        if metric is None:
            raise ScoringError(f"权重引用了未定义的指标 {code}")
        rubric = rubric_by_code.get(code) or {}
        specs.append(
            MetricSpec(
                code=code,
                name=metric.get("name", code),
                weight=float(w["weight"]),
                unit=metric.get("unit"),
                levels=tuple(rubric.get("levels_json") or rubric.get("levels") or ()),
            )
        )
    return specs
