"""【板块4】规则引擎 - 单元测试

测试覆盖：
  1. 指标规范化
  2. 评分计算
  3. 等级映射
  4. 完整规则引擎执行
"""

import pytest
from backend.app.services.rules_engine import (
    MetricNormalizer,
    ScoreCalculator,
    RatingMapper,
    RulesEngine,
)


class TestMetricNormalizer:
    """指标规范化测试"""
    
    def test_normalize_linear(self):
        """✅ 测试：线性规范化"""
        
        # GMV: 95 / 100 * 100 = 95 分
        normalized = MetricNormalizer.normalize(
            value=95,
            normalization_type="linear",
            baseline=100
        )
        
        assert normalized == 95.0
    
    def test_normalize_direct(self):
        """✅ 测试：直接规范化（百分比）"""
        
        # 留任率: 92% → 92 分
        normalized = MetricNormalizer.normalize(
            value=92,
            normalization_type="direct",
            baseline=100
        )
        
        assert normalized == 92.0
    
    def test_normalize_clipping(self):
        """✅ 测试：规范化裁剪到 [0, 100]"""
        
        # 超过 100 的值应该被裁剪
        normalized = MetricNormalizer.normalize(
            value=200,
            normalization_type="linear",
            baseline=100
        )
        
        assert normalized == 100.0
        
        # 负数应该被裁剪
        normalized = MetricNormalizer.normalize(
            value=-10,
            normalization_type="linear",
            baseline=100
        )
        
        assert normalized == 0.0
    
    def test_normalize_zero_baseline(self):
        """✅ 测试：基准为 0 时的规范化"""
        
        # 基准为 0，应该返回 0
        normalized = MetricNormalizer.normalize(
            value=95,
            normalization_type="linear",
            baseline=0
        )
        
        assert normalized == 0.0


class TestScoreCalculator:
    """评分计算测试"""
    
    def test_weighted_score_calculation(self):
        """✅ 测试：加权得分计算"""
        
        normalized = {
            "GMV": 95.0,
            "retention_rate": 92.0,
            "completion_rate": 85.0
        }
        
        weights = {
            "GMV": 0.6,
            "retention_rate": 0.3,
            "completion_rate": 0.1
        }
        
        # 预期: 95 * 0.6 + 92 * 0.3 + 85 * 0.1 = 57 + 27.6 + 8.5 = 93.1
        score = ScoreCalculator.calculate_weighted_score(normalized, weights)
        
        assert abs(score - 93.1) < 0.01
    
    def test_adjustments_attendance(self):
        """✅ 测试：考勤调整"""
        
        # 缺勤 2 天，应该 -2 分
        score, adjustments = ScoreCalculator.apply_adjustments(
            score=93.1,
            absent_days=2
        )
        
        assert adjustments["attendance"] == -2.0
        assert abs(score - 91.1) < 0.01
    
    def test_adjustments_praise(self):
        """✅ 测试：表彰调整"""
        
        # 表彰 1 次，应该 +5 分
        score, adjustments = ScoreCalculator.apply_adjustments(
            score=90.0,
            praise_count=1
        )
        
        assert adjustments["praise"] == 5.0
        assert abs(score - 95.0) < 0.01
    
    def test_adjustments_complaint(self):
        """✅ 测试：投诉调整"""
        
        # 投诉 1 次，应该 -3 分
        score, adjustments = ScoreCalculator.apply_adjustments(
            score=90.0,
            complaint_count=1
        )
        
        assert adjustments["complaint"] == -3.0
        assert abs(score - 87.0) < 0.01
    
    def test_adjustments_combined(self):
        """✅ 测试：组合调整"""
        
        # 缺勤 2 天 (-2), 表彰 1 次 (+5), 投诉 1 次 (-3)
        score, adjustments = ScoreCalculator.apply_adjustments(
            score=90.0,
            absent_days=2,
            praise_count=1,
            complaint_count=1
        )
        
        # 90 - 2 + 5 - 3 = 90
        assert abs(score - 90.0) < 0.01
        assert adjustments["attendance"] == -2.0
        assert adjustments["praise"] == 5.0
        assert adjustments["complaint"] == -3.0


class TestRatingMapper:
    """等级映射测试"""
    
    def test_map_rating_level_1(self):
        """✅ 测试：映射到等级 1"""
        
        rating_ranges = {
            1: (0, 60),
            2: (61, 72),
            3: (73, 84),
            4: (85, 92),
            5: (93, 100)
        }
        
        level, label = RatingMapper.map_to_rating(50, rating_ranges)
        assert level == 1
        assert label == "不达标"
    
    def test_map_rating_level_4(self):
        """✅ 测试：映射到等级 4"""
        
        rating_ranges = {
            1: (0, 60),
            2: (61, 72),
            3: (73, 84),
            4: (85, 92),
            5: (93, 100)
        }
        
        level, label = RatingMapper.map_to_rating(90.1, rating_ranges)
        assert level == 4
        assert label == "超标"
    
    def test_map_rating_level_5(self):
        """✅ 测试：映射到等级 5"""
        
        rating_ranges = {
            1: (0, 60),
            2: (61, 72),
            3: (73, 84),
            4: (85, 92),
            5: (93, 100)
        }
        
        level, label = RatingMapper.map_to_rating(96.1, rating_ranges)
        assert level == 5
        assert label == "大幅超标"
    
    def test_map_rating_boundary(self):
        """✅ 测试：边界值映射"""
        
        rating_ranges = {
            1: (0, 60),
            2: (61, 72),
            3: (73, 84),
            4: (85, 92),
            5: (93, 100)
        }
        
        # 恰好在边界
        level, _ = RatingMapper.map_to_rating(60, rating_ranges)
        assert level == 1
        
        level, _ = RatingMapper.map_to_rating(61, rating_ranges)
        assert level == 2
        
        level, _ = RatingMapper.map_to_rating(72, rating_ranges)
        assert level == 2
        
        level, _ = RatingMapper.map_to_rating(73, rating_ranges)
        assert level == 3


class TestRulesEngineComplete:
    """完整规则引擎测试"""
    
    def test_complete_execution(self):
        """✅ 测试：完整规则引擎执行"""
        
        engine = RulesEngine()
        
        metrics = {
            "GMV": {"value": 95, "unit": "万元"},
            "retention_rate": {"value": 92, "unit": "%"},
            "completion_rate": {"value": 85, "unit": "%"}
        }
        
        normalization_rules = {
            "GMV": {"type": "linear", "baseline": 100, "max": 150},
            "retention_rate": {"type": "direct", "baseline": 100},
            "completion_rate": {"type": "direct", "baseline": 100}
        }
        
        weights = {
            "GMV": 0.6,
            "retention_rate": 0.3,
            "completion_rate": 0.1
        }
        
        rating_ranges = {
            1: (0, 60),
            2: (61, 72),
            3: (73, 84),
            4: (85, 92),
            5: (93, 100)
        }
        
        # 执行规则引擎
        detail = engine.execute(
            record_id="REC_001",
            metrics=metrics,
            normalization_rules=normalization_rules,
            weights=weights,
            rating_ranges=rating_ranges,
            absent_days=2,
            praise_count=0,
            complaint_count=0,
            violation_count=0
        )
        
        # 验证结果
        assert detail.record_id == "REC_001"
        
        # 规范化: GMV=95, retention=92, completion=85
        assert len(detail.normalized_metrics) == 3
        assert detail.normalized_metrics[0].normalized_score == 95.0
        
        # 加权: 95*0.6 + 92*0.3 + 85*0.1 = 93.1
        assert abs(detail.weighted_score - 93.1) < 0.01
        
        # 调整: 93.1 - 2 = 91.1
        assert detail.attendance_adjustment == -2.0
        assert abs(detail.final_score - 91.1) < 0.01
        
        # 等级: 91.1 ∈ [85, 92] → 4
        assert detail.rating_level == 4
        assert detail.rating_label == "超标"
    
    def test_rules_engine_with_multiple_adjustments(self):
        """✅ 测试：多项调整的规则引擎"""
        
        engine = RulesEngine()
        
        metrics = {
            "GMV": {"value": 100, "unit": "万元"},
            "retention_rate": {"value": 95, "unit": "%"}
        }
        
        normalization_rules = {
            "GMV": {"type": "linear", "baseline": 100},
            "retention_rate": {"type": "direct", "baseline": 100}
        }
        
        weights = {
            "GMV": 0.6,
            "retention_rate": 0.4
        }
        
        rating_ranges = {
            1: (0, 60),
            2: (61, 72),
            3: (73, 84),
            4: (85, 92),
            5: (93, 100)
        }
        
        # 执行规则引擎，包含多项调整
        detail = engine.execute(
            record_id="REC_002",
            metrics=metrics,
            normalization_rules=normalization_rules,
            weights=weights,
            rating_ranges=rating_ranges,
            absent_days=0,
            praise_count=2,
            complaint_count=1,
            violation_count=0
        )
        
        # 规范化: GMV=100, retention=95
        # 加权: 100*0.6 + 95*0.4 = 60 + 38 = 98
        assert abs(detail.weighted_score - 98.0) < 0.01
        
        # 调整: 98 + 2*5 - 1*3 = 98 + 10 - 3 = 105 → 100（裁剪）
        assert detail.reward_adjustment == 7.0  # +10 - 3
        assert detail.final_score == 100.0
        
        # 等级: 100 ∈ [93, 100] → 5
        assert detail.rating_level == 5
        assert detail.rating_label == "大幅超标"
