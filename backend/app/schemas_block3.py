"""【板块3】自评和评分接口的 Schema 定义

包含：
  1. SelfReviewRequest/Response - 自评表单
  2. ManagerReviewRequest/Response - 评分表单
  3. MetricValueRequest - 指标值
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ─────────────────────────────────────────────────────────────
# 指标值 Schema
# ─────────────────────────────────────────────────────────────


class MetricValueRequest(BaseModel):
    """指标值请求（自评和评分都可能包含）"""
    
    metric_code: str = Field(..., min_length=1, max_length=50)
    # 示例："GMV", "retention_rate"
    
    value: float = Field(...)
    # 示例：95.5
    
    unit: str = Field(..., min_length=1, max_length=20)
    # 示例："万元", "%"
    
    comment: Optional[str] = Field(None, max_length=500)
    # 可选的备注


class MetricValueResponse(BaseModel):
    """指标值响应"""
    
    metric_code: str
    value: float
    unit: str
    comment: Optional[str] = None
    source: str  # "self" 或 "manager"


# ─────────────────────────────────────────────────────────────
# 【板块3.1】自评表单 Schema
# ─────────────────────────────────────────────────────────────


class SelfReviewRequest(BaseModel):
    """【板块3.1】员工自评请求
    
    示例：
    {
      "self_score": 4,
      "self_comment": "今年完成了销售目标的 95%...",
      "highlights": "新产品推进",
      "improvement_areas": "国际市场开拓",
      "metric_values": [
        {"metric_code": "kpi_1", "value": 85, "unit": "%"}
      ]
    }
    """
    
    # 1. 自评分数（1-5 分）
    self_score: int = Field(
        ...,
        ge=1,
        le=5,
        description="自评分数，范围 1-5"
    )
    
    # 2. 自评评论（自由文本）
    self_comment: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="自评评论，长度 10-2000 字"
    )
    
    # 3. 自评指标值（可选）
    metric_values: Optional[List[MetricValueRequest]] = Field(
        None,
        description="可选的指标值数组"
    )
    
    # 4. 自评亮点（可选）
    highlights: Optional[str] = Field(
        None,
        max_length=1000,
        description="今年的主要亮点"
    )
    
    # 5. 改进方向（可选）
    improvement_areas: Optional[str] = Field(
        None,
        max_length=1000,
        description="可以改进的方向"
    )
    
    class Config:
        schema_extra = {
            "example": {
                "self_score": 4,
                "self_comment": "完成了销售目标的 95%，特别是推进了新产品市场开拓...",
                "highlights": "新产品推进成功",
                "improvement_areas": "国际市场策略需要改进"
            }
        }


class SelfReviewResponse(BaseModel):
    """【板块3.1】员工自评响应"""
    
    record_id: str = Field(..., description="评估记录 ID")
    employee_id: str = Field(..., description="员工 ID")
    status: str = Field(..., description="当前状态，应为 'reviewing'")
    
    # 自评数据
    self_score: int = Field(..., description="自评分数")
    self_comment: str = Field(..., description="自评评论")
    highlights: Optional[str] = Field(None, description="亮点")
    improvement_areas: Optional[str] = Field(None, description="改进方向")
    
    # 时间戳
    self_submitted_at: datetime = Field(..., description="自评提交时间")
    
    # 截止时间信息
    self_review_deadline: datetime = Field(..., description="自评截止时间")
    manager_review_deadline: datetime = Field(..., description="评分截止时间")
    
    # 状态信息
    message: str = Field("自评已成功提交，等待主管评分", description="状态信息")


# ─────────────────────────────────────────────────────────────
# 【板块3.2】评分表单 Schema
# ─────────────────────────────────────────────────────────────


class ManagerReviewRequest(BaseModel):
    """【板块3.2】主管评分请求
    
    示例：
    {
      "metric_values": [
        {"metric_code": "GMV", "value": 95, "unit": "万元", "comment": "超目标"},
        {"metric_code": "retention_rate", "value": 92, "unit": "%"}
      ],
      "manager_score": 4,
      "manager_comment": "完成良好，继续保持...",
      "rating_reason": "指标完成 90% 以上",
      "recommended_action": "继续加强国际市场开拓"
    }
    """
    
    # 1. 指标值数组（必需）
    metric_values: List[MetricValueRequest] = Field(
        ...,
        min_items=1,
        description="至少一个指标值"
    )
    
    # 2. 主管评分（1-5 分）
    manager_score: int = Field(
        ...,
        ge=1,
        le=5,
        description="主管评分，范围 1-5"
    )
    
    # 3. 主管评论（自由文本）
    manager_comment: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="主管评论，长度 10-2000 字"
    )
    
    # 4. 评分理由（可选）
    rating_reason: Optional[str] = Field(
        None,
        max_length=1000,
        description="评分的理由或依据"
    )
    
    # 5. 建议行动（可选）
    recommended_action: Optional[str] = Field(
        None,
        max_length=1000,
        description="对员工的建议和下一步行动"
    )
    
    class Config:
        schema_extra = {
            "example": {
                "metric_values": [
                    {"metric_code": "GMV", "value": 95, "unit": "万元", "comment": "超目标 5%"},
                    {"metric_code": "retention_rate", "value": 92, "unit": "%"}
                ],
                "manager_score": 4,
                "manager_comment": "表现很好，指标完成很好...",
                "rating_reason": "GMV 超目标，团队稳定性好"
            }
        }


class ManagerReviewResponse(BaseModel):
    """【板块3.2】主管评分响应"""
    
    record_id: str = Field(..., description="评估记录 ID")
    employee_id: str = Field(..., description="员工 ID")
    manager_id: str = Field(..., description="主管 ID")
    status: str = Field(..., description="当前状态，应为 'scored'")
    
    # 主管评分数据
    manager_score: int = Field(..., description="主管评分")
    manager_comment: str = Field(..., description="主管评论")
    rating_reason: Optional[str] = Field(None, description="评分理由")
    recommended_action: Optional[str] = Field(None, description="建议行动")
    
    # 指标值摘要
    metric_values_count: int = Field(..., description="指标值数量")
    metric_values: List[MetricValueResponse] = Field(..., description="所有指标值")
    
    # 初步得分
    preliminary_score: Optional[float] = Field(
        None,
        description="规则引擎计算前的初步得分"
    )
    
    # 时间戳
    manager_submitted_at: datetime = Field(..., description="评分提交时间")
    
    # 状态信息
    message: str = Field("评分已成功提交，等待规则引擎计算", description="状态信息")
    next_step: str = Field(
        "等待 HR 最终确认或规则引擎计算最终得分",
        description="下一步"
    )


# ─────────────────────────────────────────────────────────────
# 错误响应 Schema
# ─────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """错误响应统一格式"""
    
    code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误消息")
    status_code: int = Field(..., description="HTTP 状态码")
    timestamp: datetime = Field(default_factory=datetime.now)
    
    class Config:
        schema_extra = {
            "examples": {
                "permission_denied": {
                    "code": "PERMISSION_DENIED",
                    "message": "您无权修改其他员工的评估记录",
                    "status_code": 403
                },
                "deadline_exceeded": {
                    "code": "DEADLINE_EXCEEDED",
                    "message": "自评截止时间已过",
                    "status_code": 422
                },
                "employee_not_reviewed": {
                    "code": "EMPLOYEE_NOT_REVIEWED",
                    "message": "员工尚未完成自评，无法进行主管评分",
                    "status_code": 409
                }
            }
        }
