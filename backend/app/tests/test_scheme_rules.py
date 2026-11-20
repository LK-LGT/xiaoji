"""P1-1 acceptance: 否决 / 联动扣分 / 封顶保底 规则引擎。"""
import pytest

from backend.app.services.scheme_rules import apply_rules
from backend.app.services.scoring import ScoreResult, ScoringError


def _result(total=90.0, per_metric=None, trace=None):
    return ScoreResult(
        total=total,
        per_metric=per_metric if per_metric is not None else [
            {"metric_code": "K", "score": 60.0},
            {"metric_code": "Q", "score": 30.0},
        ],
        trace=trace if trace is not None else {"grades": [
            {"level": "A", "min_score": 90, "max_score": 100},
            {"level": "B", "min_score": 70, "max_score": 89.99},
            {"level": "C", "min_score": 0, "max_score": 69.99},
        ]},
    )


def test_no_rules_is_identity():
    """没有规则时是恒等变换 —— 不影响既有方案。"""
    payload = {"metrics": []}
    original = _result()
    out = apply_rules(payload, original)
    assert out.total == original.total
    assert out.trace["rules"]["applied"] == []


def test_veto_zero_mode_removes_metric_contribution():
    """红线否决：指标分低于阈值 → 贡献归零。"""
    payload = {"rules": {"veto": [
        {"code": "V1", "metric": "K", "threshold": 70, "mode": "zero"}
    ]}}
    out = apply_rules(payload, _result(total=90.0))
    # 90 - 60 = 30
    assert out.total == 30.0
    applied = out.trace["rules"]["applied"]
    assert applied[0]["type"] == "VETO"
    assert applied[0]["action"] == "指标贡献归零"


def test_veto_grade_mode_forces_grade():
    """红线否决：等级强制指定。"""
    payload = {"rules": {"veto": [
        {"code": "V2", "metric": "K", "threshold": 70, "mode": "grade", "result_grade": "D"}
    ]}}
    out = apply_rules(payload, _result(total=90.0))
    assert out.trace["scheme_rating"] == "D"
    assert out.trace["rules"]["vetoed_grade"] == "D"
    # 等级强制不改总分
    assert out.total == 90.0


def test_veto_cap_mode_caps_total():
    """红线否决：总分封顶。"""
    payload = {"rules": {"veto": [
        {"code": "V3", "metric": "K", "threshold": 70, "mode": "cap", "max_score": 70}
    ]}}
    out = apply_rules(payload, _result(total=90.0))
    assert out.total == 70.0


def test_veto_not_triggered_when_above_threshold():
    """未触发红线时不产生任何影响。"""
    payload = {"rules": {"veto": [
        {"code": "V1", "metric": "K", "threshold": 60, "mode": "zero"}
    ]}}
    out = apply_rules(payload, _result(total=95.0, per_metric=[
        {"metric_code": "K", "score": 65.0},
        {"metric_code": "Q", "score": 30.0},
    ]))
    assert out.total == 95.0
    assert out.trace["rules"]["applied"] == []


def test_linkage_deducts_from_total():
    """联动扣分：触发指标达标 → 总分扣减。"""
    payload = {"rules": {"linkage": [
        {"code": "L1", "trigger_metric": "K", "trigger_score": 60, "deduct": 5}
    ]}}
    out = apply_rules(payload, _result(total=90.0))
    assert out.total == 85.0
    assert out.trace["rules"]["deducted"] == 5.0


def test_linkage_respects_max_deduct():
    """联动扣分有封顶。"""
    payload = {"rules": {"linkage": [
        {"code": "L1", "trigger_metric": "K", "trigger_score": 60, "deduct": 50, "max_deduct": 10}
    ]}}
    out = apply_rules(payload, _result(total=90.0))
    assert out.total == 80.0


def test_linkage_scope_on_specific_metric():
    """联动扣分可作用在指定指标上（不低于 0）。"""
    payload = {"rules": {"linkage": [
        {"code": "L1", "trigger_metric": "K", "trigger_score": 60,
         "deduct": 10, "scope": "Q"}
    ]}}
    out = apply_rules(payload, _result(total=90.0))
    # Q 原 30 分，扣 10 → 实际扣 10，总分 80
    assert out.total == 80.0
    assert out.trace["rules"]["applied"][0]["target_metric"] == "Q"


def test_cap_floor_bounds_final_total():
    """封顶保底。"""
    payload = {"rules": {"cap_floor": {"min_score": 60, "max_score": 80}}}
    out = apply_rules(payload, _result(total=90.0))
    assert out.total == 80.0
    low = apply_rules(payload, _result(total=40.0))
    assert low.total == 60.0


def test_invalid_rule_blocks_scoring():
    """规则配置非法必须阻断 —— 分数不能是错的。"""
    bad_mode = {"rules": {"veto": [{"code": "V", "metric": "K", "mode": "explode"}]}}
    with pytest.raises(ScoringError):
        apply_rules(bad_mode, _result())

    missing_metric = {"rules": {"linkage": [{"code": "L", "deduct": 5}]}}
    with pytest.raises(ScoringError):
        apply_rules(missing_metric, _result())


def test_rules_are_deterministic_and_repeatable():
    """同一输入多次执行结果完全一致（可复算）。"""
    payload = {"rules": {
        "veto": [{"code": "V", "metric": "K", "threshold": 60, "mode": "zero"}],
        "linkage": [{"code": "L", "trigger_metric": "Q", "trigger_score": 30, "deduct": 2}],
        "cap_floor": {"min_score": 0, "max_score": 100},
    }}
    first = apply_rules(payload, _result(total=90.0))
    second = apply_rules(payload, _result(total=90.0))
    assert first.total == second.total
    assert first.trace["rules"] == second.trace["rules"]
