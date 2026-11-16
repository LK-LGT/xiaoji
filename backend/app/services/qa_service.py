"""Q&A 查询服务（参考 P0-1/2/4）。

这是数据查询层。主要职责：
- 根据查询类型返回不同的数据结构
- 计算数据完整度（data_quality）
- 执行 k-匿名 检查（P2-1）
- 隐私脱敏（P0-4）

关键原则：不产生新数据，只查询和展示已有数据。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .. import exceptions, schemas
from ..domain import models
from ..services import scoring

logger = logging.getLogger(__name__)


# ┌─────────────────────────────────────────────────────┐
# │ 月中进度查询（P0-1）                               │
# └─────────────────────────────────────────────────────┘


def query_self_progress(
    employee_id: str,
    task_id: str,
    db: Session
) -> schemas.ProgressResponse:
    """
    查询自己的月中进度。

    场景：
    - 员工登录系统，想看"我现在的进度怎么样"
    - 数据来源：当前已提交的 MetricValue（不需要主管评分）

    响应格式（参考 P0-1）：
    {
        "as_of": "2026-10-31",
        "completed_weight": 0.8,      # 已出数权重
        "missing_weight": 0.2,        # 缺失权重
        "per_metric": [
            {
                "metric_code": "GMV",
                "display_name": "销售额",
                "raw_value": 1250000,
                "score": 85,           # rubric 转换后的分数
                "weight": 0.4,
                "status": "completed"
            },
            ...
        ]
    }
    """

    # 1. 查找 AssessmentRecord
    record = db.query(models.AssessmentRecord).filter_by(
        task_id=task_id,
        employee_id=employee_id
    ).first()

    if not record:
        raise exceptions.RecordNotFound(
            "AssessmentRecord",
            f"task_id={task_id}, employee_id={employee_id}"
        )

    # 2. 查找 TaskSnapshot（冻结的配置）
    task_snapshot = db.query(models.TaskSnapshot).filter_by(
        task_id=task_id
    ).first()

    if not task_snapshot:
        raise exceptions.RecordNotFound("TaskSnapshot", task_id)

    # 3. 计算各指标的进度
    import json
    metric_defs = json.loads(task_snapshot.metric_json)
    weight_set = json.loads(task_snapshot.weight_set_json)

    per_metric_list = []
    completed_weight = 0.0
    missing_weight = 0.0

    for metric_def in metric_defs:
        metric_code = metric_def["code"]
        metric_weight = weight_set.get(metric_code, 0)

        # 查找该指标的最新值
        metric_value = db.query(models.MetricValue).filter_by(
            record_id=record.id,
            metric_code=metric_code
        ).order_by(models.MetricValue.created_at.desc()).first()

        if metric_value:
            # 已有数据
            status = "completed"
            completed_weight += metric_weight

            # 用 rubric 转换成分数
            score = scoring.convert_value_to_score(
                metric_code,
                metric_value.value,
                task_snapshot,
                db
            )
        else:
            # 缺失数据
            status = "pending"
            missing_weight += metric_weight
            score = None

        per_metric_list.append(
            schemas.ProgressMetricInfo(
                metric_code=metric_code,
                display_name=metric_def.get("display_name", metric_code),
                raw_value=metric_value.value if metric_value else None,
                score=score,
                weight=metric_weight,
                status=status
            )
        )

    # 4. 构建响应
    return schemas.ProgressResponse(
        as_of=datetime.now().isoformat(),
        completed_weight=completed_weight,
        missing_weight=missing_weight,
        per_metric=per_metric_list
    )


# ┌─────────────────────────────────────────────────────┐
# │ 月末成绩查询（P0-1）                               │
# └─────────────────────────────────────────────────────┘


def query_self_result(
    employee_id: str,
    task_id: str,
    db: Session
) -> schemas.ResultResponse:
    """
    查询自己的月末成绩。

    场景：
    - 员工查看最终评分和等级
    - 需要主管已评分（status != pending）

    响应格式（参考 P0-1）：
    {
        "status": "confirmed",
        "confirmed_rating": "A",
        "confirmed_by": "M001",
        "confirmed_at": "2026-11-10T18:00:00",
        "score": {...ScoreResponse...},
        "per_metric": [...],
        "appeal_deadline": "2026-11-15"
    }
    """

    # 1. 查找 Record + Score
    record = db.query(models.AssessmentRecord).filter_by(
        task_id=task_id,
        employee_id=employee_id
    ).first()

    if not record:
        raise exceptions.RecordNotFound("AssessmentRecord", f"{task_id}/{employee_id}")

    score = db.query(models.Score).filter_by(
        record_id=record.id
    ).first()

    if not score or record.status == "pending":
        # 还没有评分或主管还没评
        return schemas.ResultResponse(
            status="pending",
            confirmed_rating=None,
            confirmed_by=None,
            score={},
            per_metric=[]
        )

    # 2. 转换 Score 为 ScoreResponse
    import json
    score_response = schemas.ScoreResponse(
        total=score.total,
        per_metric_json=json.loads(score.per_metric_json),
        data_quality=schemas.DataQualityInfo(
            completeness=0.95,  # TODO: 从 trace 计算
            as_of=score.confirmed_at.isoformat() if score.confirmed_at else datetime.now().isoformat(),
            degraded=False,
            citations=["Score.total", "MetricValue.*"]
        ),
        trace=json.loads(score.trace)
    )

    # 3. 构建 per_metric 列表
    per_metric_list = []
    for metric_code, metric_score in json.loads(score.per_metric_json).items():
        per_metric_list.append(
            schemas.ResultMetricInfo(
                metric_code=metric_code,
                display_name=metric_code,  # TODO: 从 TaskSnapshot 查
                score=metric_score,
                weight=0.2,  # TODO: 从 weight_set_json 查
                contribution=metric_score * 0.2
            )
        )

    # 4. 构建响应
    return schemas.ResultResponse(
        status=record.status,
        confirmed_rating=record.confirmed_rating,
        confirmed_by=score.confirmed_by,
        confirmed_at=score.confirmed_at.isoformat() if score.confirmed_at else None,
        score=score_response.model_dump(),
        per_metric=per_metric_list,
        appeal_deadline=(
            (score.confirmed_at.date()).isoformat()
            if score.confirmed_at
            else None
        )
    )


# ┌─────────────────────────────────────────────────────┐
# │ 直属下级成绩查询                                    │
# └─────────────────────────────────────────────────────┘


def query_colleague_result(
    colleague_employee_id: str,
    task_id: str,
    db: Session
) -> schemas.ResultResponse:
    """
    查询某员工的成绩（通常是下级）。

    场景：
    - 主管查看下级的评分
    - HR 查看任何员工的评分

    权限检查在 API 层做，这里假设已检查通过。

    实现：与 query_self_result 一样，只是 employee_id 不同。
    """

    return query_self_result(colleague_employee_id, task_id, db)


# ┌─────────────────────────────────────────────────────┐
# │ 部门汇总查询（P2-1 k-匿名）                        │
# └─────────────────────────────────────────────────────┘


def query_department_summary(
    department_id: str,
    task_id: str,
    db: Session
) -> schemas.DepartmentSummaryResponse:
    """
    查询部门在某考核任务中的汇总。

    场景：
    - HR 看"销售部这月的评分分布怎么样"
    - 领导看"各部门对比"

    关键（参考 P2-1 k-匿名）：
    - 如果部门 < 5 人，average_score 返回 null
    - 显示等级分布（聚合，不排名）

    响应格式：
    {
        "total_employees": 8,
        "scored_employees": 8,
        "average_score": 76.5,     # 如果 >= 5 人
        "rating_distribution": [
            {"rating": "A", "count": 3, "percentage": 0.375},
            ...
        ],
        "note": ""
    }
    """

    # 1. 查找部门的所有成员
    employees = db.query(models.Employee).filter_by(
        department_id=department_id,
        employment_status="active"
    ).all()

    total_count = len(employees)
    employee_ids = [emp.id for emp in employees]

    # 2. 查找该部门在 task 中的所有 Record + Score
    records_scores = db.query(
        models.AssessmentRecord,
        models.Score
    ).filter(
        models.AssessmentRecord.task_id == task_id,
        models.AssessmentRecord.employee_id.in_(employee_ids),
        models.Score.record_id == models.AssessmentRecord.id
    ).all()

    scored_count = len(records_scores)

    # 3. 计算等级分布
    rating_distribution = {}  # {"A": 3, "B": 4, ...}
    total_score = 0.0

    for record, score in records_scores:
        rating = record.confirmed_rating or "未定"
        rating_distribution[rating] = rating_distribution.get(rating, 0) + 1
        total_score += score.total

    # 4. k-匿名 检查（参考 P2-1）
    average_score = None
    note = ""

    if total_count < 5:
        # 部门太小，不显示平均分
        note = "部门员工数 < 5，隐私保护中不显示平均分"
    else:
        if scored_count > 0:
            average_score = total_score / scored_count

    # 5. 转换为响应格式
    rating_dist_list = []
    for rating, count in sorted(rating_distribution.items()):
        percentage = count / scored_count if scored_count > 0 else 0
        rating_dist_list.append(
            schemas.DepartmentRatingDistribution(
                rating=rating,
                count=count,
                percentage=percentage
            )
        )

    return schemas.DepartmentSummaryResponse(
        total_employees=total_count,
        scored_employees=scored_count,
        average_score=average_score,
        rating_distribution=rating_dist_list,
        note=note
    )


# ┌─────────────────────────────────────────────────────┐
# │ 通用的数据质量计算函数                              │
# └─────────────────────────────────────────────────────┘


def calculate_data_quality(
    record_id: str,
    task_snapshot: models.TaskSnapshot,
    db: Session
) -> schemas.DataQualityInfo:
    """
    计算数据的完整度和质量信息。

    参考 P0-5：data_quality 替代 confidence。
    由 L2 业务层计算，而不是 AI 自报。

    计算逻辑：
    - completeness = 已出数权重 / 总权重
    - degraded：是否因某些原因无法完整展示
    - citations：数据来源追踪
    """

    import json

    # 查找该 record 的所有 MetricValue
    metric_values = db.query(models.MetricValue).filter_by(
        record_id=record_id
    ).all()

    value_codes = {mv.metric_code for mv in metric_values}

    # 从 TaskSnapshot 读取权重
    weight_set = json.loads(task_snapshot.weight_set_json)

    # 计算完整度
    completed_weight = sum(weight_set.get(code, 0) for code in value_codes)
    total_weight = sum(weight_set.values())
    completeness = completed_weight / total_weight if total_weight > 0 else 0

    # 构建 citations
    citations = []
    for code in value_codes:
        citations.append(f"MetricValue.{code}")

    return schemas.DataQualityInfo(
        completeness=completeness,
        as_of=datetime.now().isoformat(),
        degraded=completeness < 0.8,
        degrade_reason="数据缺失超 20%" if completeness < 0.8 else None,
        citations=citations,
        note=""
    )
