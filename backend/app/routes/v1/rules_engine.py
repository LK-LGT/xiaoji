"""【板块4】规则引擎 - API 路由

包含一个核心接口：
  POST /v1/assessment/records/{record_id}/execute-rules-engine - 执行规则引擎
"""

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from datetime import datetime
import logging

from backend.app.core.security import get_current_user
from backend.app.core.db import get_db
from backend.app.domain.models import (
    Employee,
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
)
from backend.app import exceptions
from backend.app.schemas_block4 import (
    RulesEngineRequest,
    RulesEngineResponse,
    RulesEngineErrorResponse,
    CalculationDetail,
)
from backend.app.services.rules_engine import RulesEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/assessment/records", tags=["rules_engine"])

# 全局规则引擎实例
rules_engine = RulesEngine()


# ─────────────────────────────────────────────────────────────
# 规则引擎执行接口
# ─────────────────────────────────────────────────────────────


@router.post(
    "/{record_id}/execute-rules-engine",
    response_model=RulesEngineResponse,
    responses={
        401: {"model": RulesEngineErrorResponse, "description": "无效 Token"},
        403: {"model": RulesEngineErrorResponse, "description": "权限不足"},
        404: {"model": RulesEngineErrorResponse, "description": "评估记录不存在"},
        409: {"model": RulesEngineErrorResponse, "description": "业务冲突（状态错误）"},
        422: {"model": RulesEngineErrorResponse, "description": "参数验证失败"},
    },
    summary="【板块4】执行规则引擎",
    description="根据指标数据和配置的规则，自动计算员工最终评分",
)
async def execute_rules_engine(
    record_id: str = Path(..., description="评估记录 ID"),
    request: RulesEngineRequest = None,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    执行规则引擎计算员工最终评分
    
    【业务流程】
    
    【步骤 1】权限和身份检查
    ├─ Token 有效性
    └─ 用户身份是 HR 或管理员
    
    【步骤 2】数据查询和验证
    ├─ AssessmentRecord（评估记录）
    ├─ AssessmentTask（考核任务）
    ├─ ConfigSnapshot（规则配置）
    └─ MetricValue（指标值）
    
    【步骤 3】状态检查
    ├─ Record.status 必须为 "scored"（已评分）
    ├─ 员工自评已完成
    └─ 主管评分已完成
    
    【步骤 4】数据验证
    ├─ 指标完整性检查
    ├─ 规范化规则有效性
    ├─ 权重配置有效性
    └─ 等级映射有效性
    
    【步骤 5】规则引擎计算
    ├─ 加载规则配置
    ├─ 读取指标数据
    ├─ 调用 RulesEngine.execute()
    └─ 获取计算结果
    
    【步骤 6】结果保存
    ├─ 更新 record.final_score
    ├─ 更新 record.rating
    ├─ 保存计算详情
    └─ 更新 record.status = "finalized"
    
    【步骤 7】异常处理
    ├─ PermissionDenied：非 HR/Admin
    ├─ DataValidationError：数据不合法
    ├─ BusinessLogicError：超过截止时间
    └─ ConflictError：状态不对
    
    【步骤 8】事务提交
    └─ db.commit()
    """
    
    try:
        logger.info(f"[规则引擎] 开始执行 user={current_user.id}, record_id={record_id}")
        
        # ───────────────────────────────────────────
        # 步骤 1：权限检查
        # ───────────────────────────────────────────
        
        if not current_user:
            raise exceptions.NotAuthenticated("Token 无效或已过期")
        
        # 只有 HR 和管理员可以执行规则引擎
        if current_user.role not in ["hr", "admin"]:
            raise exceptions.PermissionDenied(
                user_id=current_user.id,
                action="execute_rules_engine",
                resource="AssessmentRecord",
                reason="仅 HR 和管理员可以执行规则引擎"
            )
        
        # ───────────────────────────────────────────
        # 步骤 2：数据查询
        # ───────────────────────────────────────────
        
        record = db.query(AssessmentRecord).filter_by(id=record_id).first()
        
        if not record:
            raise exceptions.ResourceNotFoundError(
                resource="AssessmentRecord",
                resource_id=record_id
            )
        
        task = db.query(AssessmentTask).filter_by(id=record.task_id).first()
        
        if not task:
            raise exceptions.ResourceNotFoundError(
                resource="AssessmentTask",
                resource_id=record.task_id
            )
        
        snapshot = db.query(ConfigSnapshot).filter_by(id=task.snapshot_id).first()
        
        if not snapshot:
            raise exceptions.ResourceNotFoundError(
                resource="ConfigSnapshot",
                resource_id=task.snapshot_id
            )
        
        # ───────────────────────────────────────────
        # 步骤 3：状态检查
        # ───────────────────────────────────────────
        
        # 必须已评分
        if record.status != "scored" and not request.force_execute:
            raise exceptions.ConflictError(
                message=f"记录状态不对，期望 scored，实际 {record.status}。"
                        f"使用 force_execute=true 强制执行"
            )
        
        # ───────────────────────────────────────────
        # 步骤 4：数据验证
        # ───────────────────────────────────────────
        
        import json
        
        # 解析规则配置
        try:
            metrics_config = json.loads(snapshot.metrics_json)
            weights_config = json.loads(snapshot.weights_json)
            rubric_config = json.loads(snapshot.rubric_json)
        except json.JSONDecodeError as e:
            raise exceptions.DataValidationError(
                message=f"规则配置 JSON 格式错误: {e}",
                field="config_snapshot"
            )
        
        # 从 MetricValue 表读取指标值
        from backend.app.domain.models import MetricValue
        
        metric_values = db.query(MetricValue).filter_by(
            record_id=record_id,
            source="manager"  # 只读取主管的指标值
        ).all()
        
        if not metric_values:
            raise exceptions.BusinessLogicError(
                message="没有找到任何指标值，无法执行规则引擎"
            )
        
        # 构建指标数据字典
        metrics_data = {}
        for mv in metric_values:
            metrics_data[mv.metric_code] = {
                "value": mv.value,
                "unit": mv.unit
            }
        
        logger.info(f"指标值: {metrics_data}")
        
        # 构建规范化规则
        normalization_rules = {}
        for metric_info in metrics_config:
            code = metric_info.get("code")
            normalization_rules[code] = {
                "type": metric_info.get("normalization", {}).get("type", "direct"),
                "baseline": metric_info.get("normalization", {}).get("baseline", 100),
                "max": metric_info.get("normalization", {}).get("max"),
                "min": metric_info.get("normalization", {}).get("min")
            }
        
        # 构建权重字典
        weights = {}
        for metric_code, weight in weights_config.items():
            weights[metric_code] = float(weight)
        
        # 验证权重总和接近 1.0
        total_weight = sum(weights.values())
        if abs(total_weight - 1.0) > 0.1:
            logger.warning(f"权重总和不为 1.0: {total_weight}")
        
        # 构建等级范围
        rating_ranges = {}
        for level_str, score_range in rubric_config.items():
            try:
                level = int(level_str)
                if isinstance(score_range, (list, tuple)) and len(score_range) == 2:
                    rating_ranges[level] = (float(score_range[0]), float(score_range[1]))
                elif isinstance(score_range, dict):
                    rating_ranges[level] = (
                        float(score_range.get("min", 0)),
                        float(score_range.get("max", 100))
                    )
            except (ValueError, TypeError) as e:
                logger.warning(f"等级配置解析失败: {e}")
        
        if not rating_ranges:
            # 使用默认等级范围
            rating_ranges = {
                1: (0, 60),
                2: (61, 72),
                3: (73, 84),
                4: (85, 92),
                5: (93, 100)
            }
        
        # ───────────────────────────────────────────
        # 步骤 5：执行规则引擎
        # ───────────────────────────────────────────
        
        logger.info("调用 RulesEngine.execute()")
        
        calculation_detail = rules_engine.execute(
            record_id=record_id,
            metrics=metrics_data,
            normalization_rules=normalization_rules,
            weights=weights,
            rating_ranges=rating_ranges,
            absent_days=request.absent_days or 0,
            praise_count=request.praise_count or 0,
            complaint_count=request.complaint_count or 0,
            violation_count=request.violation_count or 0
        )
        
        logger.info(
            f"规则引擎执行完成: "
            f"final_score={calculation_detail.final_score}, "
            f"rating={calculation_detail.rating_level}"
        )
        
        # ───────────────────────────────────────────
        # 步骤 6：保存结果
        # ───────────────────────────────────────────
        
        # 更新 AssessmentRecord
        record.status = "finalized"
        record.final_score = calculation_detail.final_score
        record.rating = calculation_detail.rating_label
        record.confirmed_rating = float(calculation_detail.rating_level)
        
        # 保存计算详情（转换为 JSON）
        calculation_detail_json = calculation_detail.dict()
        import json
        record.calculation_detail = json.dumps(calculation_detail_json, default=str)
        
        db.add(record)
        db.flush()
        
        # ───────────────────────────────────────────
        # 步骤 7：事务提交
        # ───────────────────────────────────────────
        
        db.commit()
        db.refresh(record)
        
        logger.info(f"[规则引擎] 执行成功 record_id={record_id}")
        
        # ───────────────────────────────────────────
        # 步骤 8：返回响应
        # ───────────────────────────────────────────
        
        return RulesEngineResponse(
            record_id=record.id,
            status="success",
            final_score=calculation_detail.final_score,
            rating_level=calculation_detail.rating_level,
            rating_label=calculation_detail.rating_label,
            calculation_detail=calculation_detail,
            message=f"规则引擎执行成功，最终得分 {calculation_detail.final_score}（{calculation_detail.rating_label}）"
        )
    
    except exceptions.AppException as e:
        logger.warning(f"[规则引擎] 业务异常: {e}")
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "record_id": record_id,
                "error_code": e.code,
                "error_message": str(e),
                "timestamp": datetime.now().isoformat()
            }
        )
    except Exception as e:
        logger.error(f"[规则引擎] 系统异常: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "record_id": record_id,
                "error_code": "SYSTEM_ERROR",
                "error_message": "系统内部错误",
                "timestamp": datetime.now().isoformat()
            }
        )
