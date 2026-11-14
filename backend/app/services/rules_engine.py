"""【板块4】规则引擎 - 核心实现

包含：
  1. RulesEngine - 规则引擎主类
  2. MetricNormalizer - 指标规范化器
  3. ScoreCalculator - 评分计算器
  4. RatingMapper - 等级映射器
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging
import time

from ..schemas_block4 import (
    NormalizationType,
    CalculationDetail,
    NormalizedMetricDetail,
    CalculationStep,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 指标规范化器
# ─────────────────────────────────────────────────────────────


class MetricNormalizer:
    """指标规范化 - 把不同单位的数据转换成 0-100 分"""
    
    @staticmethod
    def normalize(
        value: float,
        normalization_type: str,
        baseline: float,
        max_value: Optional[float] = None,
        min_value: Optional[float] = None
    ) -> float:
        """
        规范化单个指标值
        
        Args:
            value: 原始值
            normalization_type: 规范化类型 (linear/direct/piecewise)
            baseline: 基准值（目标值）
            max_value: 最大值
            min_value: 最小值
        
        Returns:
            规范化后的得分 (0-100)
        """
        
        if normalization_type == "linear":
            # 线性规范化：value / baseline * 100
            # GMV: 95 / 100 * 100 = 95 分
            if baseline == 0:
                normalized = 0
            else:
                normalized = (value / baseline) * 100
        
        elif normalization_type == "direct":
            # 直接使用百分比
            # 留任率: 92% → 92 分
            normalized = value
        
        elif normalization_type == "piecewise":
            # 分段规则
            # 低于基准: 按比例降分
            # 高于基准: 按比例加分
            if value < baseline:
                # 低于基准
                if min_value is not None and value < min_value:
                    normalized = 0
                else:
                    ratio = (value / baseline) if baseline > 0 else 0
                    normalized = ratio * 100
            else:
                # 高于或等于基准
                if max_value is not None and value > max_value:
                    normalized = 100
                else:
                    # 从 100 到 150 映射为 100-150
                    overage = value - baseline
                    max_overage = (max_value - baseline) if max_value else baseline
                    normalized = 100 + (overage / max_overage * 50) if max_overage > 0 else 100
        
        else:
            # 默认使用直接方式
            normalized = value
        
        # 裁剪到 [0, 100]
        return max(0.0, min(100.0, normalized))


# ─────────────────────────────────────────────────────────────
# 评分计算器
# ─────────────────────────────────────────────────────────────


class ScoreCalculator:
    """评分计算 - 加权求和 + 调整"""
    
    @staticmethod
    def calculate_weighted_score(
        normalized_metrics: Dict[str, float],
        weights: Dict[str, float]
    ) -> float:
        """
        多指标加权求和
        
        Args:
            normalized_metrics: 规范化后的指标 {code: score}
            weights: 权重 {code: weight}
        
        Returns:
            加权得分 (0-100)
        """
        
        if not normalized_metrics or not weights:
            logger.warning("规范化指标或权重为空")
            return 0.0
        
        weighted_score = 0.0
        total_weight = 0.0
        
        for metric_code, normalized_score in normalized_metrics.items():
            weight = weights.get(metric_code, 0.0)
            
            if weight > 0:
                weighted_score += normalized_score * weight
                total_weight += weight
                
                logger.debug(
                    f"指标 {metric_code}: score={normalized_score}, weight={weight}, "
                    f"contribution={normalized_score * weight}"
                )
        
        # 如果权重不为 1.0，进行标准化
        if total_weight > 0 and abs(total_weight - 1.0) > 0.01:
            logger.warning(f"权重总和不为 1.0，实际为 {total_weight}，进行标准化")
            weighted_score = weighted_score / total_weight * 100
        
        logger.info(f"加权得分: {weighted_score}")
        return min(100.0, max(0.0, weighted_score))
    
    @staticmethod
    def calculate_attendance_adjustment(absent_days: int) -> float:
        """
        计算考勤调整分数 - 优化版
        
        规则：基于周期的阶段递进
          第 1 周（1-7 天）：-2 分/天
          第 2 周（8-14 天）：-3 分/天
          第 3+ 周（15+ 天）：-4 分/天
        
        Args:
            absent_days: 缺勤天数
        
        Returns:
            调整分数（负数表示扣分）
        
        Examples:
            0 天 → 0 分
            5 天 → -10 分
            7 天 → -14 分
            10 天 → 7×(-2) + 3×(-3) = -23 分
            15 天 → 7×(-2) + 7×(-3) + 1×(-4) = -39 分
            20 天 → 7×(-2) + 7×(-3) + 6×(-4) = -55 分
        """
        
        if absent_days <= 0:
            return 0.0
        
        elif absent_days <= 7:
            # 第 1 周：每天 -2 分
            adjustment = absent_days * (-2)
        
        elif absent_days <= 14:
            # 第 2 周：前 7 天 -14 分 + 第 2 周 -3 分/天
            week1_adjustment = 7 * (-2)  # -14 分
            week2_days = absent_days - 7
            week2_adjustment = week2_days * (-3)
            adjustment = week1_adjustment + week2_adjustment
        
        else:
            # 第 3+ 周：前 14 天已定 + 第 3+ 周 -4 分/天
            week1_adjustment = 7 * (-2)  # -14 分
            week2_adjustment = 7 * (-3)  # -21 分
            week3_days = absent_days - 14
            week3_adjustment = week3_days * (-4)
            adjustment = week1_adjustment + week2_adjustment + week3_adjustment
        
        return float(adjustment)
    
    @staticmethod
    def apply_adjustments(
        score: float,
        absent_days: int = 0,
        praise_count: int = 0,
        complaint_count: int = 0,
        violation_count: int = 0,
        attendance_rule: Optional[Dict] = None,
        reward_rule: Optional[Dict] = None
    ) -> Tuple[float, Dict[str, float]]:
        """
        应用调整项
        
        Args:
            score: 基础得分
            absent_days: 缺勤天数
            praise_count: 表彰次数
            complaint_count: 投诉次数
            violation_count: 违规次数
            attendance_rule: 考勤调整规则（已弃用，保留兼容性）
            reward_rule: 奖惩调整规则
        
        Returns:
            (调整后的得分, 调整详情字典)
        """
        
        adjustments = {
            "attendance": 0.0,
            "praise": 0.0,
            "complaint": 0.0,
            "violation": 0.0,
            "total": 0.0
        }
        
        adjusted_score = score
        
        # 默认奖惩规则
        if reward_rule is None:
            reward_rule = {
                "praise": 5,
                "complaint": -3,
                "violation": -10
            }
        
        # 考勤调整 - 使用新的优化规则
        if absent_days > 0:
            adjustment = ScoreCalculator.calculate_attendance_adjustment(absent_days)
            adjustments["attendance"] = float(adjustment)
            adjusted_score += adjustment
            
            # 详细日志
            if absent_days <= 7:
                logger.info(f"考勤调整: {absent_days} 天（第 1 周）→ {adjustment} 分")
            elif absent_days <= 14:
                logger.info(f"考勤调整: {absent_days} 天（第 2 周）→ {adjustment} 分")
            else:
                logger.info(f"考勤调整: {absent_days} 天（第 3+ 周）→ {adjustment} 分")
        
        # 表彰调整
        if praise_count > 0:
            adjustment = praise_count * reward_rule.get("praise", 5)
            adjustments["praise"] = float(adjustment)
            adjusted_score += adjustment
            logger.info(f"表彰调整: {praise_count} 次 → +{adjustment} 分")
        
        # 投诉调整
        if complaint_count > 0:
            adjustment = complaint_count * reward_rule.get("complaint", -3)
            adjustments["complaint"] = float(adjustment)
            adjusted_score += adjustment
            logger.info(f"投诉调整: {complaint_count} 次 → {adjustment} 分")
        
        # 违规调整
        if violation_count > 0:
            adjustment = violation_count * reward_rule.get("violation", -10)
            adjustments["violation"] = float(adjustment)
            adjusted_score += adjustment
            logger.info(f"违规调整: {violation_count} 次 → {adjustment} 分")
        
        # 总调整
        total_adjustment = sum(v for k, v in adjustments.items() if k != "total")
        adjustments["total"] = total_adjustment
        
        # 裁剪到 [0, 100]
        final_score = max(0.0, min(100.0, adjusted_score))
        
        logger.info(f"调整后得分: {adjusted_score} → {final_score}")
        
        return final_score, adjustments


# ─────────────────────────────────────────────────────────────
# 等级映射器
# ─────────────────────────────────────────────────────────────


class RatingMapper:
    """等级映射 - 百分制转换为等级"""
    
    @staticmethod
    def map_to_rating(
        score: float,
        rating_ranges: Dict[int, Tuple[float, float]]
    ) -> Tuple[int, str]:
        """
        根据分数映射到等级
        
        Args:
            score: 得分 (0-100)
            rating_ranges: 等级范围 {level: (min, max)}
                          例如 {1: (0, 60), 2: (61, 72), ...}
        
        Returns:
            (等级, 等级标签)
        """
        
        # 按等级从低到高遍历
        for level in sorted(rating_ranges.keys()):
            min_score, max_score = rating_ranges[level]
            if min_score <= score <= max_score:
                label_map = {
                    1: "不达标",
                    2: "基本达标",
                    3: "达标",
                    4: "超标",
                    5: "大幅超标"
                }
                label = label_map.get(level, f"等级{level}")
                logger.info(f"得分 {score} → 等级 {level} ({label})")
                return level, label
        
        # 默认处理
        logger.warning(f"得分 {score} 不在任何等级范围内，使用默认等级")
        return 3, "达标"


# ─────────────────────────────────────────────────────────────
# 规则引擎主类
# ─────────────────────────────────────────────────────────────


class RulesEngine:
    """规则引擎 - 整体编排和调度"""
    
    def __init__(self):
        """初始化规则引擎"""
        self.normalizer = MetricNormalizer()
        self.calculator = ScoreCalculator()
        self.mapper = RatingMapper()
        self.logger = logging.getLogger(__name__)
    
    def execute(
        self,
        record_id: str,
        metrics: Dict[str, Dict[str, float]],  # {code: {value, unit}}
        normalization_rules: Dict[str, Dict],  # {code: {type, baseline, ...}}
        weights: Dict[str, float],  # {code: weight}
        rating_ranges: Dict[int, Tuple[float, float]],  # {level: (min, max)}
        absent_days: int = 0,
        praise_count: int = 0,
        complaint_count: int = 0,
        violation_count: int = 0,
        attendance_rule: Optional[Dict] = None,
        reward_rule: Optional[Dict] = None
    ) -> CalculationDetail:
        """
        执行规则引擎完整流程
        
        Args:
            record_id: 评估记录 ID
            metrics: 指标数据
            normalization_rules: 规范化规则
            weights: 权重
            rating_ranges: 等级范围
            absent_days: 缺勤天数
            praise_count: 表彰次数
            complaint_count: 投诉次数
            violation_count: 违规次数
            attendance_rule: 考勤调整规则
            reward_rule: 奖惩调整规则
        
        Returns:
            计算详情
        """
        
        start_time = time.time()
        
        try:
            self.logger.info(f"[规则引擎] 开始计算 record_id={record_id}")
            
            # ───────────────────────────────────────
            # 步骤 1：验证数据
            # ───────────────────────────────────────
            
            if not metrics:
                raise ValueError("指标数据为空")
            
            if not weights:
                raise ValueError("权重配置为空")
            
            self.logger.info(f"指标个数: {len(metrics)}, 权重个数: {len(weights)}")
            
            # ───────────────────────────────────────
            # 步骤 2：规范化指标
            # ───────────────────────────────────────
            
            normalized_metrics: Dict[str, float] = {}
            normalized_details: List[NormalizedMetricDetail] = []
            
            for metric_code, metric_data in metrics.items():
                value = metric_data.get("value", 0)
                unit = metric_data.get("unit", "")
                
                # 获取规范化规则
                rule = normalization_rules.get(metric_code, {})
                norm_type = rule.get("type", "direct")
                baseline = rule.get("baseline", 100)
                max_val = rule.get("max", None)
                min_val = rule.get("min", None)
                
                # 执行规范化
                normalized_score = self.normalizer.normalize(
                    value=value,
                    normalization_type=norm_type,
                    baseline=baseline,
                    max_value=max_val,
                    min_value=min_val
                )
                
                normalized_metrics[metric_code] = normalized_score
                
                # 记录详情
                normalized_details.append(
                    NormalizedMetricDetail(
                        metric_code=metric_code,
                        original_value=value,
                        original_unit=unit,
                        normalized_score=normalized_score,
                        normalization_rule=norm_type
                    )
                )
            
            self.logger.info(f"规范化完成: {normalized_metrics}")
            
            # ───────────────────────────────────────
            # 步骤 3：加权计算
            # ───────────────────────────────────────
            
            weighted_score = self.calculator.calculate_weighted_score(
                normalized_metrics=normalized_metrics,
                weights=weights
            )
            
            # ───────────────────────────────────────
            # 步骤 4：应用调整
            # ───────────────────────────────────────
            
            adjusted_score, adjustments = self.calculator.apply_adjustments(
                score=weighted_score,
                absent_days=absent_days,
                praise_count=praise_count,
                complaint_count=complaint_count,
                violation_count=violation_count,
                attendance_rule=attendance_rule,
                reward_rule=reward_rule
            )
            
            # ───────────────────────────────────────
            # 步骤 5：等级映射
            # ───────────────────────────────────────
            
            rating_level, rating_label = self.mapper.map_to_rating(
                score=adjusted_score,
                rating_ranges=rating_ranges
            )
            
            # ───────────────────────────────────────
            # 步骤 6：生成计算详情
            # ───────────────────────────────────────
            
            elapsed_time = (time.time() - start_time) * 1000  # 毫秒
            
            detail = CalculationDetail(
                record_id=record_id,
                normalized_metrics=normalized_details,
                metric_weights=weights,
                weighted_score=weighted_score,
                attendance_adjustment=adjustments.get("attendance", 0),
                reward_adjustment=sum([
                    adjustments.get("praise", 0),
                    adjustments.get("complaint", 0),
                    adjustments.get("violation", 0)
                ]),
                total_adjustment=adjustments.get("total", 0),
                final_score=adjusted_score,
                rating_level=rating_level,
                rating_label=rating_label,
                calculation_duration_ms=elapsed_time
            )
            
            self.logger.info(
                f"[规则引擎] 计算完成 "
                f"score={adjusted_score}, rating={rating_level}, "
                f"耗时={elapsed_time:.2f}ms"
            )
            
            return detail
        
        except Exception as e:
            self.logger.error(f"[规则引擎] 执行失败: {e}", exc_info=True)
            raise
