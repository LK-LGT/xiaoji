"""请求/响应的 Pydantic 模型（API 契约层）。

这是前后端、AI 层的契约定义。改动这里需要通知相关方。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, List

from pydantic import BaseModel, Field


# ┌─────────────────────────────────────────────────────┐
# │ 指标相关                                            │
# └─────────────────────────────────────────────────────┘


class MetricValueRequest(BaseModel):
    """添加指标值的请求。
    
    使用场景：
    - HR 系统上传销售额、留任率等数值指标
    - 主管手动输入定性指标
    - 考勤系统上报异常
    """
    
    metric_code: str = Field(
        ...,
        description="指标代码，如 'GMV'、'refund_rate'、'attendance_anomaly'"
    )
    value: float = Field(
        ...,
        description="数值。对于等级指标，1=基础/D, 2=良好/C, 3=优秀/B, 4=杰出/A"
    )
    source: str = Field(
        default="hr_system",
        description="数据来源：hr_system / manager_input / attendance_system / self_review"
    )
    evidence_id: Optional[str] = Field(
        None,
        description="关联的证据 ID（如果有的话）"
    )
    collected_at: Optional[datetime] = Field(
        None,
        description="数据采集时间"
    )

    class Config:
        example = {
            "metric_code": "GMV",
            "value": 1250000.0,
            "source": "hr_system",
            "evidence_id": "E001",
            "collected_at": "2026-10-31T23:59:59"
        }


# ┌─────────────────────────────────────────────────────┐
# │ 自评相关                                            │
# └─────────────────────────────────────────────────────┘


class SelfReviewRequest(BaseModel):
    """员工自评的请求。
    
    使用场景：
    - 员工填写自评表（动态表单）
    - 补充证据、强调重点成绩
    """
    
    self_review_text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="自评文本（支持 Markdown）"
    )
    highlight_evidence_ids: List[str] = Field(
        default_factory=list,
        description="重点展示的证据 ID 列表（最多 10 个）"
    )
    attachment_urls: List[str] = Field(
        default_factory=list,
        description="附件 URL（如 PPT、证书扫描件）"
    )

    class Config:
        example = {
            "self_review_text": "本月完成销售目标 120%，新增客户 5 家...",
            "highlight_evidence_ids": ["E001", "E002"],
            "attachment_urls": ["https://example.com/ppt.pdf"]
        }


# ┌─────────────────────────────────────────────────────┐
# │ 主管评分相关                                        │
# └─────────────────────────────────────────────────────┘


class ManagerReviewRequest(BaseModel):
    """主管评分的请求。
    
    使用场景：
    - 主管在系统里确认等级、输入评语
    - 触发自动计分
    
    关键：分数由规则引擎计算，主管的 confirmed_rating 作为输入进入算式
    """
    
    manager_comment: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="主管评语"
    )
    confirmed_rating: str = Field(
        ...,
        pattern="^[A-D]$",
        description="等级：A / B / C / D"
    )
    rating_reason: Optional[str] = Field(
        None,
        description="等级理由（供申诉参考）"
    )
    confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="主管对此评分的自信度（仅用于监控，不进计分逻辑）"
    )

    class Config:
        example = {
            "manager_comment": "今年表现不错，销售超目标，带队也有进步...",
            "confirmed_rating": "A",
            "rating_reason": "销售业绩 +20%，新人带队",
            "confidence": 0.9
        }


# ┌─────────────────────────────────────────────────────┐
# │ 分数相关（P0-5 data_quality 格式）                 │
# └─────────────────────────────────────────────────────┘


class DataQualityInfo(BaseModel):
    """数据完整度信息。
    
    替代 confidence（AI 自报的置信度）。
    由 L2 业务层计算，用 citations 追踪数据来源。
    """
    
    completeness: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0-1 的完整度，由已出数权重/总权重计算"
    )
    as_of: str = Field(
        ...,
        description="数据截止日期（ISO 8601）"
    )
    degraded: bool = Field(
        default=False,
        description="是否降级（如 AI 不可用、小部门 k-匿名 等）"
    )
    degrade_reason: Optional[str] = Field(
        None,
        description="降级原因"
    )
    citations: List[str] = Field(
        default_factory=list,
        description="数据来源引用，如 ['Score.total', 'MetricValue.gmv', 'Evidence.E001']"
    )
    note: Optional[str] = Field(
        None,
        description="备注（如'样本过小，隐私保护中'）"
    )

    class Config:
        example = {
            "completeness": 0.95,
            "as_of": "2026-10-31",
            "degraded": False,
            "citations": ["MetricValue.gmv", "MetricValue.retention", "ManagerReview.rating"],
            "note": ""
        }


class ScoreResponse(BaseModel):
    """分数响应。
    
    关键改动（P0-5）：
    - 用 data_quality 替代 confidence
    - 用 per_metric_json 追踪每个指标的转换后得分
    - trace 记录完整的计算链路
    
    注意：这个响应不直接暴露给前端/AI。
    前端看到的是 QAQueryResponse（聚合的结构化数据）。
    """
    
    total: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="0-100 的最终分数"
    )
    per_metric_json: dict = Field(
        ...,
        description="各指标的转换后得分，如 {'GMV': 85, 'refund_rate': 72}"
    )
    data_quality: DataQualityInfo = Field(
        ...,
        description="数据完整度及来源"
    )
    trace: dict = Field(
        ...,
        description="计算链路（用于审计和调试），包含 weights、components、computed_at 等"
    )

    class Config:
        example = {
            "total": 78.5,
            "per_metric_json": {
                "GMV": 85,
                "retention": 72,
                "manager_rating": 82
            },
            "data_quality": {
                "completeness": 0.95,
                "as_of": "2026-10-31",
                "degraded": False,
                "citations": ["MetricValue.gmv", "Score.trace"]
            },
            "trace": {
                "components": [
                    {"metric": "GMV", "value": 85, "weight": 0.4, "contribution": 34.0}
                ],
                "total": 78.5,
                "computed_at": "2026-11-10T18:00:00"
            }
        }


# ┌─────────────────────────────────────────────────────┐
# │ Q&A 查询相关（P0-2 POST，P0-1 新增 progress）     │
# └─────────────────────────────────────────────────────┘


class QAQueryRequest(BaseModel):
    """Q&A 查询请求。
    
    改动（P0-2）：从 GET 改成 POST，支持复杂查询参数。
    
    查询类型（参考 P0-1）：
    - self_progress：我的月中进度
    - self_result：我的月末成绩
    - colleague_result：某员工的月末成绩（权限检查）
    - peer_performance_distribution：部门等级分布（聚合，不排名）
    """
    
    query_type: str = Field(
        ...,
        pattern="^(self_progress|self_result|colleague_result|peer_performance_distribution)$",
        description="查询类型"
    )
    target_employee_id: str = Field(
        ...,
        description="要查询的员工 ID（self_* 时必须是自己）"
    )
    task_id: str = Field(
        ...,
        description="考核任务 ID"
    )
    include_ai_analysis: bool = Field(
        default=False,
        description="是否需要 AI 分析（会调用 L3 智能体层）"
    )

    class Config:
        example = {
            "query_type": "self_progress",
            "target_employee_id": "E001",
            "task_id": "T202611",
            "include_ai_analysis": True
        }


class ProgressMetricInfo(BaseModel):
    """月中进度中的单个指标信息。"""
    
    metric_code: str
    display_name: str
    raw_value: Optional[float] = None
    score: Optional[float] = None
    weight: float
    status: str  # "completed" / "pending" / "missing"


class ProgressResponse(BaseModel):
    """月中进度响应（参考 P0-1）。
    
    结构：
    - as_of：数据截止日期
    - completed_weight：已出数权重
    - missing_weight：缺失权重
    - per_metric：各指标详情
    """
    
    as_of: str = Field(..., description="数据截止日期")
    completed_weight: float = Field(..., description="已出数权重（0-1）")
    missing_weight: float = Field(..., description="缺失权重（0-1）")
    per_metric: List[ProgressMetricInfo] = Field(..., description="各指标详情")


class ResultMetricInfo(BaseModel):
    """月末成绩中的单个指标信息。"""
    
    metric_code: str
    display_name: str
    score: float
    weight: float
    contribution: float  # score * weight


class ResultResponse(BaseModel):
    """月末成绩响应（参考 P0-1）。
    
    结构：
    - status：confirmed / pending
    - confirmed_rating：等级（A/B/C/D）
    - confirmed_by：确认人 ID
    - score：最终分数
    - per_metric：各指标详情
    """
    
    status: str = Field(..., description="confirmed / pending")
    confirmed_rating: str = Field(..., description="等级 A/B/C/D")
    confirmed_by: Optional[str] = Field(None, description="确认人 ID")
    confirmed_at: Optional[str] = Field(None, description="确认时间")
    score: dict = Field(..., description="ScoreResponse（见上面定义）")
    per_metric: List[ResultMetricInfo] = Field(..., description="各指标详情")
    appeal_deadline: Optional[str] = Field(None, description="申诉期限")


class DepartmentRatingDistribution(BaseModel):
    """部门等级分布（不排名，仅聚合，参考 P2-2）。"""
    
    rating: str = Field(..., description="A/B/C/D")
    count: int = Field(..., description="人数")
    percentage: float = Field(..., description="百分比（0-1）")


class DepartmentSummaryResponse(BaseModel):
    """部门汇总响应。
    
    关键（参考 P2-1 k-匿名）：
    - 如果部门 < 5 人，average_score 返回 null
    - 显示等级分布（聚合，不排名）
    """
    
    total_employees: int = Field(..., description="部门员工总数")
    scored_employees: int = Field(..., description="已评分员工数")
    average_score: Optional[float] = Field(None, description="平均分（< 5 人时 null）")
    rating_distribution: List[DepartmentRatingDistribution] = Field(...)
    note: Optional[str] = Field(None, description="备注（如'样本过小，隐私保护中'）")


class QAQueryResponse(BaseModel):
    """Q&A 查询的通用响应。
    
    根据 query_type 不同，data 字段的结构不同：
    - self_progress → ProgressResponse
    - self_result → ResultResponse
    - colleague_result → ResultResponse
    - peer_performance_distribution → DepartmentSummaryResponse
    
    注意：不含 AI 分析的文本。如需要，调用单独的 AI 接口。
    """
    
    query_type: str = Field(...)
    data: dict = Field(..., description="结构化数据（根据 query_type 变化）")
    data_quality: DataQualityInfo = Field(..., description="数据完整度及来源")

    class Config:
        example = {
            "query_type": "self_progress",
            "data": {
                "as_of": "2026-10-31",
                "completed_weight": 0.8,
                "missing_weight": 0.2,
                "per_metric": [
                    {"metric_code": "GMV", "display_name": "销售额", "raw_value": 1250000, "score": 85, "weight": 0.4, "status": "completed"}
                ]
            },
            "data_quality": {
                "completeness": 0.8,
                "as_of": "2026-10-31",
                "degraded": False,
                "citations": ["MetricValue.GMV"]
            }
        }


# ┌─────────────────────────────────────────────────────┐
# │ 任务相关                                            │
# └─────────────────────────────────────────────────────┘


class AssessmentTaskRequest(BaseModel):
    """发起考核任务的请求。"""
    
    title: str = Field(..., description="任务标题，如'2026 年 11 月月度考核'")
    description: Optional[str] = Field(None)
    target_employee_ids: List[str] = Field(..., description="被评员工 ID 列表")
    assessor_groups: Optional[dict] = Field(None, description="评估组配置（评价标准）")
    start_date: str = Field(..., description="考核开始日期（ISO 8601）")
    end_date: str = Field(..., description="考核结束日期（ISO 8601）")
    self_review_deadline: str = Field(..., description="自评截止时间")
    manager_review_deadline: str = Field(..., description="主管评分截止时间")


class AssessmentTaskResponse(BaseModel):
    """发起考核后的响应。"""
    
    task_id: str = Field(..., description="任务 ID")
    status: str = Field(..., description="状态，如 created / in_progress / completed")
    created_at: str
    target_count: int = Field(..., description="被评员工数")
    message: str = Field(default="", description="提示信息")


# ┌─────────────────────────────────────────────────────┐
# │ 通用响应                                            │
# └─────────────────────────────────────────────────────┘


class ErrorResponse(BaseModel):
    """错误响应（参考异常处理）。"""
    
    code: str = Field(..., description="错误代码，如 ATTENDANCE_REJECTED")
    message: str = Field(..., description="错误信息")
    details: Optional[dict] = Field(None, description="详细信息")


class HealthCheckResponse(BaseModel):
    """健康检查响应。"""
    
    status: str = Field(..., description="ok / degraded / error")
    version: str = Field(..., description="API 版本")
    database: str = Field(..., description="数据库类型")
    timestamp: str = Field(..., description="检查时间")


class SystemInfoResponse(BaseModel):
    """系统信息响应。"""
    
    agents: List[str] = Field(..., description="可用的 AI 智能体")
    rules_version: str = Field(..., description="规则引擎版本")
    database: str = Field(..., description="数据库类型")
    config_version: str = Field(..., description="当前配置版本")
