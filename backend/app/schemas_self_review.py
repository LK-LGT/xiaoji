"""【板块3】员工自评提交 —— 请求/响应 Schema。

设计约定：
    - 请求体只描述「业务语义」（自评分数、评论、指标值、附件），
      不含任何渠道字段（无 open_id / 无表单控件 key）。渠道翻译在板块6做。
    - 响应保持扁平结构，方便渠道适配层包装成飞书卡片或 H5 回显。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SelfReviewMetricInput(BaseModel):
    """自评中的指标值（可选）。"""

    model_config = ConfigDict(extra="forbid")

    metric_code: str = Field(..., min_length=1, max_length=64, description="指标编码")
    value: float = Field(..., description="指标实测值")
    unit: Optional[str] = Field(None, max_length=32, description="单位")
    comment: Optional[str] = Field(None, max_length=500, description="指标备注")


class SelfReviewAttachment(BaseModel):
    """附件元信息（文件实体由文件服务托管，这里只存引用）。"""

    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(..., min_length=1, max_length=128, description="文件 ID")
    file_name: Optional[str] = Field(None, max_length=255, description="文件名")
    file_size: Optional[int] = Field(None, ge=0, description="文件大小（字节）")
    file_type: Optional[str] = Field(None, max_length=64, description="文件类型")
    url: Optional[str] = Field(None, max_length=1024, description="可访问链接")


class SelfReviewAnswerItem(BaseModel):
    """表单式自评的单项答案（板块6 飞书表单翻译的目标形态）。"""

    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(..., min_length=1, max_length=64)
    text: Optional[str] = Field(None, max_length=4000)


class SelfReviewSubmitRequest(BaseModel):
    """员工提交自评请求。

    示例::

        {
          "self_score": 4,
          "self_comment": "本月完成销售目标 105%，新签 3 家客户",
          "highlights": "新客户拓展",
          "improvement_areas": "回款周期偏长",
          "metric_values": [{"metric_code": "GMV", "value": 1050, "unit": "万元"}],
          "attachments": [{"file_id": "f_1", "file_name": "报表.pdf"}]
        }
    """

    model_config = ConfigDict(extra="forbid")

    self_score: int = Field(..., ge=1, le=5, description="自评分数 1-5")
    self_comment: str = Field(
        ..., min_length=1, max_length=4000, description="自评正文（业务层要求 >=10 字）"
    )
    highlights: Optional[str] = Field(None, max_length=2000, description="亮点")
    improvement_areas: Optional[str] = Field(None, max_length=2000, description="改进方向")
    metric_values: Optional[list[SelfReviewMetricInput]] = Field(
        None, description="指标值列表（可选）"
    )
    attachments: Optional[list[SelfReviewAttachment]] = Field(
        None, description="附件列表（可选）"
    )
    answers: Optional[list[SelfReviewAnswerItem]] = Field(
        None, description="结构化表单答案（供渠道层使用，业务层可忽略）"
    )
    dedup_key: Optional[str] = Field(
        None,
        max_length=255,
        description="幂等键（飞书事件可能重复投递；自研前端可不传）",
    )


class SelfReviewSubmitResponse(BaseModel):
    """自评提交成功响应。"""

    code: int = Field(0, description="业务状态码，0 表示成功")
    message: str = Field("自评已提交成功", description="提示信息")
    data: dict[str, Any] = Field(..., description="自评提交结果")


class SelfReviewErrorResponse(BaseModel):
    """统一的错误响应。"""

    code: str = Field(..., description="错误码，如 PERMISSION_DENIED")
    message: str = Field(..., description="错误说明")
    details: dict[str, Any] = Field(default_factory=dict, description="错误细节")
