"""Direct points are summed, never percentage-weighted a second time."""

from decimal import Decimal

from .scoring import ScoreResult, ScoringError


def score_points(payload, reviews):
    items = []
    total = Decimal(0)
    try:
        for metric in payload["metrics"]:
            raw = reviews[metric["code"]]
            value = Decimal(str(raw["score"]))
            maximum = Decimal(str(metric["max_score"]))
            if not value.is_finite() or not 0 <= value <= maximum:
                raise ScoringError(f"指标{metric['name']}评分超出0至{maximum}分")
            total += value
            items.append(
                {
                    "metric_code": metric["code"],
                    "metric_name": metric["name"],
                    "contribution": float(value),
                    "max_score": float(maximum),
                    "source": "scheme_manager_review",
                }
            )
        document = payload["document"]
        adjustment = reviews.get("_adjustment", {}).get("score", 0)
        value = Decimal(str(adjustment))
        if not value.is_finite() or not Decimal(
            str(document.get("adjustment_min", 0))
        ) <= value <= Decimal(str(document.get("adjustment_max", 0))):
            raise ScoringError("加减分超出该方案的封顶范围")
        total += value
        items.append(
            {
                "metric_code": "_adjustment",
                "contribution": float(value),
                "source": "scheme_manager_review",
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ScoringError(f"缺少有效的逐项评分：{exc}") from exc
    return ScoreResult(
        total=float(total.quantize(Decimal("0.01"))),
        per_metric=items,
        trace={
            "rule": "scheme_direct_points_v1",
            "adjustment": float(value),
            "grading_mode": "manager_confirmed",
        },
    )
