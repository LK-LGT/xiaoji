"""【板块4】规则引擎 - Schema 定义

包含：
  1. RulesEngineRequest/Response - API 请求/响应
  2. MetricNormalizationRule - 指标规范化规则
  3. AdjustmentRule - 调整规则
  4. RatingMapping - 等级映射
  5. CalculationDetail - 计算详情
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class NormalizationType(str, Enum):
    """规范化类型"""
    LINEAR = "linear"  # 线性规范化
    DIRECT = "direct"  # 直接使用百分比
    PIECEWISE = "piecewise"  # 分段规则


class AdjustmentType(str, Enum):
    """调整项类型"""
    ATTENDANCE = "attendance"  # 考勤调整
    REWARD = "reward"  # 奖惩调整
    CUSTOM = "custom"  # 自定义


# ─────────────────────────────────────────────────────────────
# 规范化规则 Schema
# ─────────────────────────────────────────────────────────────


class MetricNormalizationRule(BaseModel):
    """指标规范化规则"""
    
    metric_code: str = Field(..., description="指标代码")
    normalization_type: NormalizationType = Field(..., description="规范化类型")
    baseline: float = Field(..., description="基准值")
    max_value: Optional[float] = Field(None, description="最大值")
    min_value: Optional[float] = Field(None, description="最小值")
    
    class Config:
        schema_extra = {
            "example": {
                "metric_code": "GMV",
                "normalization_type": "linear",
                "baseline": 100,
                "max_value": 150,
                "min_value": 0
            }
        }


# ─────────────────────────────────────────────────────────────
# 调整规则 Schema
# ─────────────────────────────────────────────────────────────


class AttendanceAdjustment(BaseModel):
    """考勤调整规则"""
    
    absent_days_1_3: int = Field(-2, description="缺勤 1-3 天的调整分数")
    absent_days_3_5: int = Field(-5, description="缺勤 3-5 天的调整分数")
    absent_days_5_plus: int = Field(-10, description="缺勤 5+ 天的调整分数")


class RewardAdjustment(BaseModel):
    """奖惩调整规则"""
    
    praise: int = Field(5, description="公司表彰分数")
    complaint: int = Field(-3, description="客户投诉分数")
    violation: int = Field(-10, description="违规处罚分数")


class AdjustmentRule(BaseModel):
    """调整规则集合"""
    
    attendance: Optional[AttendanceAdjustment] = None
    reward: Optional[RewardAdjustment] = None


# ─────────────────────────────────────────────────────────────
# 等级映射 Schema
# ─────────────────────────────────────────────────────────────


class RatingLevel(BaseModel):
    """单个等级定义"""
    
    level: int = Field(..., ge=1, le=5, description="等级（1-5）")
    min_score: float = Field(..., description="最小分数（含）")
    max_score: float = Field(..., description="最大分数（含）")
    label: str = Field(..., description="等级标签")
    description: str = Field(..., description="等级描述")


class RatingMapping(BaseModel):
    """等级映射规则"""
    
    ratings: List[RatingLevel] = Field(..., description="等级列表")
    
    class Config:
        schema_extra = {
            "example": {
                "ratings": [
                    {"level": 1, "min_score": 0, "max_score": 60, "label": "不达标", "description": "绩效不达标"},
                    {"level": 2, "min_score": 61, "max_score": 72, "label": "基本达标", "description": "绩效基本达标"},
                    {"level": 3, "min_score": 73, "max_score": 84, "label": "达标", "description": "绩效达标"},
                    {"level": 4, "min_score": 85, "max_score": 92, "label": "超标", "description": "绩效超标"},
                    {"level": 5, "min_score": 93, "max_score": 100, "label": "大幅超标", "description": "绩效大幅超标"}
                ]
            }
        }


# ─────────────────────────────────────────────────────────────
# 计算详情 Schema
# ─────────────────────────────────────────────────────────────


class NormalizedMetricDetail(BaseModel):
    """规范化后的指标详情"""
    
    metric_code: str
    original_value: float
    original_unit: str
    normalized_score: float
    normalization_rule: str


class CalculationStep(BaseModel):
    """计算步骤"""
    
    step_number: int = Field(..., description="步骤号")
    step_name: str = Field(..., description="步骤名称")
    input_value: float = Field(..., description="输入值")
    operation: str = Field(..., description="操作说明")
    output_value: float = Field(..., description="输出值")


class CalculationDetail(BaseModel):
    """完整计算详情"""
    
    record_id: str = Field(..., description="评估记录 ID")
    
    # 规范化步骤
    normalized_metrics: List[NormalizedMetricDetail] = Field(..., description="规范化后的指标")
    
    # 加权计算
    metric_weights: Dict[str, float] = Field(..., description="每个指标的权重")
    weighted_score: float = Field(..., description="加权得分")
    
    # 调整项
    attendance_adjustment: float = Field(0, description="考勤调整")
    reward_adjustment: float = Field(0, description="奖惩调整")
    total_adjustment: float = Field(0, description="总调整")
    
    # 最终结果
    final_score: float = Field(..., description="最终得分（0-100）")
    rating_level: int = Field(..., ge=1, le=5, description="等级（1-5）")
    rating_label: str = Field(..., description="等级标签")
    
    # 时间戳
    calculated_at: datetime = Field(default_factory=datetime.now)
    calculation_duration_ms: Optional[float] = Field(None, description="计算耗时（毫秒）")


# ─────────────────────────────────────────────────────────────
# 规则引擎请求/响应 Schema
# ─────────────────────────────────────────────────────────────


class RulesEngineRequest(BaseModel):
    """规则引擎请求"""
    
    record_id: str = Field(..., description="评估记录 ID")
    
    # 可选的调整项数据
    absent_days: Optional[int] = Field(0, description="缺勤天数")
    praise_count: Optional[int] = Field(0, description="表彰次数")
    complaint_count: Optional[int] = Field(0, description="投诉次数")
    violation_count: Optional[int] = Field(0, description="违规次数")
    
    # 可选的强制参数
    force_execute: Optional[bool] = Field(False, description="强制执行（即使状态不对）")
    
    class Config:
        schema_extra = {
            "example": {
                "record_id": "REC_001",
                "absent_days": 2,
                "praise_count": 1,
                "complaint_count": 0,
                "violation_count": 0
            }
        }


class RulesEngineResponse(BaseModel):
    """规则引擎响应"""
    
    record_id: str = Field(..., description="评估记录 ID")
    status: str = Field(..., description="执行状态")
    
    # 计算结果
    final_score: float = Field(..., description="最终得分（0-100）")
    rating_level: int = Field(..., ge=1, le=5, description="等级（1-5）")
    rating_label: str = Field(..., description="等级标签")
    
    # 详细信息
    calculation_detail: CalculationDetail = Field(..., description="完整计算详情")
    
    # 时间戳
    executed_at: datetime = Field(default_factory=datetime.now)
    message: str = Field("规则引擎执行成功", description="状态消息")
    
    class Config:
        schema_extra = {
            "example": {
                "record_id": "REC_001",
                "status": "success",
                "final_score": 90.1,
                "rating_level": 4,
                "rating_label": "超标",
                "calculation_detail": {
                    "record_id": "REC_001",
                    "normalized_metrics": [...],
                    "weighted_score": 93.1,
                    "final_score": 90.1,
                    "rating_level": 4,
                    "rating_label": "超标"
                },
                "message": "规则引擎执行成功"
            }
        }


# ─────────────────────────────────────────────────────────────
# 错误响应 Schema
# ─────────────────────────────────────────────────────────────


class RulesEngineErrorResponse(BaseModel):
    """规则引擎错误响应"""
    
    record_id: Optional[str] = Field(None, description="评估记录 ID")
    error_code: str = Field(..., description="错误代码")
    error_message: str = Field(..., description="错误消息")
    details: Optional[Dict[str, Any]] = Field(None, description="详细信息")
    timestamp: datetime = Field(default_factory=datetime.now)
    
    class Config:
        schema_extra = {
            "examples": {
                "missing_metrics": {
                    "record_id": "REC_001",
                    "error_code": "MISSING_METRICS",
                    "error_message": "缺少必需的指标",
                    "details": {"missing": ["GMV", "retention_rate"]}
                },
                "invalid_state": {
                    "record_id": "REC_001",
                    "error_code": "INVALID_STATE",
                    "error_message": "评估记录状态不对，期望 scored，实际 reviewing"
                }
            }
        }
