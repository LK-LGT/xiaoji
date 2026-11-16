"""配置读入校验（`config_service.validate_config`）—— 单元测试。

背景
----
`validate_config` 是决策③的**第一道保险**：配置读入时校验，
失败则「告警且不生效」，不让坏配置进入冻结快照。

自检轮 P1 补了两项校验（第 5、6 项）：

    5. 权重必须为正数
       只校验「合计 = 100」是不够的 —— ``-70 + 170 = 100`` 能通过合计校验，
       但归一化后产生**负权重**，实测总分能跑到 128（越界 >100）。
       `scoring.score_record` 已在运行时兜底阻断，这里是第一道保险。

    6. 有权重的指标必须配置等级区间（rubric）
       缺 rubric 的指标算分时会「定不了级」→ 按 P0 口径**不参与**算分，
       权重会被重新归一到其它指标上 —— 等于配置漏了 rubric = 分数被悄悄改变。
       必须在冻结前拦下。
"""

from __future__ import annotations

import pytest

from backend.app.services.config_service import (
    ConfigValidationError,
    validate_config,
)

METRICS = [
    {
        "code": "gmv",
        "name": "GMV",
        "unit": "元",
        "calculation": "订单金额求和",
        "data_source": "api:ecommerce",
    },
    {
        "code": "conversion_rate",
        "name": "转化率",
        "unit": "%",
        "calculation": "支付人数/访客数",
        "data_source": "api:ecommerce",
    },
]

LEVELS = [
    {"level": "A", "min_ratio": 1.05, "max_ratio": None, "score": 100},
    {"level": "B", "min_ratio": 0.95, "max_ratio": 1.05, "score": 80},
    {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95, "score": 60},
]


def _rubric(*codes: str) -> list[dict]:
    return [{"metric_code": c, "levels_json": LEVELS} for c in codes]


def _weights(*pairs: tuple[str, float]) -> list[dict]:
    return [{"metric_code": c, "weight": w} for c, w in pairs]


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


def test_valid_config_passes():
    """合法配置必须通过。"""
    validate_config(
        METRICS,
        _weights(("gmv", 70), ("conversion_rate", 30)),
        _rubric("gmv", "conversion_rate"),
    )


def test_rubric_with_levels_key_also_accepted():
    """rubric 条目用 ``levels`` 键（而非 ``levels_json``）也要认。"""
    rubric = [{"metric_code": "gmv", "levels": LEVELS}]
    validate_config(METRICS, _weights(("gmv", 100)), rubric)


# ---------------------------------------------------------------------------
# 既有校验项（回归）
# ---------------------------------------------------------------------------


def test_weight_sum_must_be_100():
    with pytest.raises(ConfigValidationError, match="权重合计必须等于 100"):
        validate_config(
            METRICS,
            _weights(("gmv", 70), ("conversion_rate", 20)),
            _rubric("gmv", "conversion_rate"),
        )


def test_duplicate_metric_code_rejected():
    dup = METRICS + [dict(METRICS[0])]
    with pytest.raises(ConfigValidationError, match="指标 ID 重复"):
        validate_config(
            dup,
            _weights(("gmv", 100)),
            _rubric("gmv"),
        )


def test_empty_calibration_rejected():
    bad = [dict(METRICS[0], calculation="  "), dict(METRICS[1])]
    with pytest.raises(ConfigValidationError, match="calculation"):
        validate_config(
            bad,
            _weights(("gmv", 70), ("conversion_rate", 30)),
            _rubric("gmv", "conversion_rate"),
        )


def test_empty_data_source_rejected():
    bad = [dict(METRICS[0], data_source=""), dict(METRICS[1])]
    with pytest.raises(ConfigValidationError, match="data_source"):
        validate_config(
            bad,
            _weights(("gmv", 70), ("conversion_rate", 30)),
            _rubric("gmv", "conversion_rate"),
        )


def test_weight_referencing_undefined_metric_rejected():
    with pytest.raises(ConfigValidationError, match="未定义的指标"):
        validate_config(
            METRICS,
            _weights(("ghost", 100)),
            _rubric("ghost"),
        )


# ---------------------------------------------------------------------------
# 新增第 5 项：权重必须为正数
# ---------------------------------------------------------------------------


def test_negative_weight_rejected():
    """负权重必须拦下 —— ``-70 + 170 = 100`` 能骗过合计校验。

    回归背景：未拦截时归一化出负权重，总分能跑到 128（越界 >100）。
    """
    with pytest.raises(ConfigValidationError, match="不得为负"):
        validate_config(
            METRICS,
            _weights(("gmv", -70), ("conversion_rate", 170)),
            _rubric("gmv", "conversion_rate"),
        )


def test_zero_weight_rejected():
    """零权重必须拦下 —— 该指标实际不参与，归一化会扭曲其余指标权重。"""
    with pytest.raises(ConfigValidationError, match="不得为 0"):
        validate_config(
            METRICS,
            _weights(("gmv", 100), ("conversion_rate", 0)),
            _rubric("gmv", "conversion_rate"),
        )


def test_non_numeric_weight_rejected():
    """权重不是数字必须拦下（不能悄悄当成 0）。"""
    with pytest.raises(ConfigValidationError, match="不是数字"):
        validate_config(
            METRICS,
            [{"metric_code": "gmv", "weight": "abc"}],
            _rubric("gmv"),
        )


def test_negative_weight_reports_all_offenders():
    """多个负权重要**全部**报出来，不只报第一个。"""
    with pytest.raises(ConfigValidationError) as exc:
        validate_config(
            METRICS,
            _weights(("gmv", -70), ("conversion_rate", 170)),
            _rubric("gmv", "conversion_rate"),
        )
    assert "gmv" in str(exc.value)


# ---------------------------------------------------------------------------
# 新增第 6 项：有权重必须配 rubric
# ---------------------------------------------------------------------------


def test_weighted_metric_without_rubric_rejected():
    """有权重却没配等级区间 → 拦下。

    回归背景：按 P0 口径，缺 rubric 的指标算分时「不参与」，
    权重会重新归一到其它指标 —— 等于配置漏了 rubric 就悄悄改变了分数。
    """
    with pytest.raises(ConfigValidationError, match="未配置等级区间"):
        validate_config(
            METRICS,
            _weights(("gmv", 70), ("conversion_rate", 30)),
            _rubric("gmv"),  # conversion_rate 没有 rubric
        )


def test_rubric_with_empty_levels_rejected():
    """rubric 条目存在但 levels 为空 → 同样拦下。"""
    with pytest.raises(ConfigValidationError, match="未配置等级区间"):
        validate_config(
            METRICS,
            _weights(("gmv", 100)),
            [{"metric_code": "gmv", "levels_json": []}],
        )


def test_metric_without_weight_need_not_have_rubric():
    """没有权重的指标不必配 rubric（它不参与算分）。

    ⚠️ 这条很重要 —— 不能因为「多定义了一个指标但没配等级」就拦住整份配置。
    """
    validate_config(
        METRICS,
        _weights(("gmv", 100)),
        _rubric("gmv"),  # conversion_rate 有定义、有 rubric 吗？没有
    )


def test_errors_accumulate_all_checks():
    """多项违规要**一次性全部报出**，不只报第一项。"""
    with pytest.raises(ConfigValidationError) as exc:
        validate_config(
            METRICS,
            _weights(("gmv", -70), ("conversion_rate", 170)),
            _rubric(),  # 两个都没 rubric
        )
    msg = str(exc.value)
    assert "不得为负" in msg
    assert "未配置等级区间" in msg
