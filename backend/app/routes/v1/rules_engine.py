"""【板块4】规则引擎 - API 路由

单一接口：
  POST /v1/assessment/records/{record_id}/execute-rules-engine - 执行规则引擎

**口径归属（重要）**
-----------------------------------
本端点**不再自带算分逻辑**。算分的唯一真值是
``services/scoring.py`` + ``services/review_service.py``：

- 权重 / 等级区间来自**冻结快照**（决策③）
- 目标值来自 ``MetricValue``
- 达成率 = value / target，等级由 Rubric 区间查表
- 分数由程序计算，模型永不产生数字（原则③）

历史上的 ``services/rules_engine.py``（1~5 级、硬编码等级区间、考勤进算分）
已废弃，本端点不再调用它。详见 ``services/rules_engine.py`` 顶部说明。

**考勤不参与算分（决策②）**
-----------------------------------
``absent_days`` / ``praise_count`` / ``complaint_count`` / ``violation_count``
仍被接受（契约兼容），但**只回显、不参与计算**。
``MetricValue`` 表里本来就没有考勤数据，考勤只在
``reference_only`` 的证据视图里出现，因此物理上无法影响分数。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from ... import exceptions
from ...core.db import get_db
from ...dependencies import get_current_user
from ...domain.enums import RecordStatus
from ...domain.models import AssessmentRecord, AssessmentTask, Employee
from ...schemas_block4 import (
    CalculationDetail,
    NormalizedMetricDetail,
    RulesEngineErrorResponse,
    RulesEngineRequest,
    RulesEngineResponse,
)
from ...services import review_service
from ...services.access_control import build_access_context, can_access_employee
from ...services.scoring import ScoringError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/assessment/records", tags=["rules_engine"])

#: 可算分的状态。Score 是幂等重算，状态本身不阻断计算。
_FORCEABLE_STATES = frozenset(
    {
        RecordStatus.SELF_REVIEW_OPEN.value,
        RecordStatus.SELF_REVIEW_SUBMITTED.value,
        RecordStatus.MANAGER_SCORING.value,
        RecordStatus.DEPT_REVIEW.value,
        RecordStatus.CALIBRATED.value,
        RecordStatus.HR_REVIEW.value,
        RecordStatus.ARCHIVED.value,
        RecordStatus.RETURNED.value,
    }
)

#: 等级标签（A/B/C/D 体系）→ 1~5 整数档位（响应契约用）
_LEVEL_TO_INT = {"A": 5, "B": 4, "C": 3, "D": 2}

#: 总分 → 整档等级（A/B/C/D）。用于把加权总分折算成**整体**评级。
#:
#: ⚠️ 历史实现在这里有缺陷：整档评级取「第一个有等级的指标」，导致
#:    混合等级场景（gmv=A、conv=C，总分 88）回报「A」——与总分不符，
#:    且与 `message` 文案自相矛盾。整档评级必须由**总分**决定。
#:
#: 阈值与 `scoring.DEFAULT_LEVEL_SCORE` 的默认分档保持一致：
#:    A=100 / B=80 / C=60 / D=40
_TOTAL_BANDS: tuple[tuple[float, str], ...] = (
    (90.0, "A"),
    (75.0, "B"),
    (50.0, "C"),
    (0.0, "D"),
)


# ─────────────────────────────────────────────────────────────
# 适配层：scoring.ScoreResult → schemas_block4.CalculationDetail
# ─────────────────────────────────────────────────────────────


def _level_int(level: str | None) -> int:
    """A/B/C/D 等级 → 1~5 档位（响应契约兼容）。

    未知等级保守落在 3（"达标"），不虚报高分。
    """
    if not level:
        return 3
    return _LEVEL_TO_INT.get(str(level).upper(), 3)


def _level_label(level: str | None) -> str:
    """等级标签。A/B/C/D 直接用；未知退化为「达标」。"""
    if not level:
        return "达标"
    text = str(level).upper()
    if text in _LEVEL_TO_INT:
        return text
    return "达标"


def _overall_level(total: float) -> str:
    """由**加权总分**折算整体等级。

    为什么不用「第一个指标的等级」：
        指标等级是「该指标相对自身目标」的达成情况，
        而整档评级必须描述「这个人整体拿了多少分」。
        两者在混合等级时会分歧（gmv=A + conv=C → 总分 88，
        按首指标会误报「A」，按总分才是「B」）。
    """
    for threshold, level in _TOTAL_BANDS:
        if total >= threshold:
            return level
    return "D"


def _build_calculation_detail(
    record_id: str,
    result,
    duration_ms: float,
) -> CalculationDetail:
    """把权威算分结果装进板块4 的计算详情契约。"""
    normalized_metrics: list[NormalizedMetricDetail] = []

    for pm in result.per_metric:
        normalized_metrics.append(
            NormalizedMetricDetail(
                metric_code=pm["metric_code"],
                original_value=float(pm["value"] or 0.0),
                original_unit=pm.get("unit") or "",
                normalized_score=float(pm.get("level_score") or 0.0),
                normalization_rule=(
                    f"达成率 {pm.get('ratio')} 落在等级 {pm.get('level')} 区间，"
                    f"等级分 {pm.get('level_score')}"
                ),
            )
        )

    weighted_score = round(
        sum(float(pm.get("contribution") or 0.0) for pm in result.per_metric), 2
    )

    # 整档评级由**加权总分**决定（不是首个指标的等级 —— 见 `_overall_level` 注释）
    primary_level = _overall_level(float(result.total))

    return CalculationDetail(
        record_id=record_id,
        normalized_metrics=normalized_metrics,
        metric_weights={
            pm["metric_code"]: float(pm.get("normalized_weight") or 0.0)
            for pm in result.per_metric
        },
        weighted_score=weighted_score,
        # 决策②：考勤/奖惩一律不参与算分，恒为 0
        attendance_adjustment=0.0,
        reward_adjustment=0.0,
        total_adjustment=0.0,
        final_score=float(result.total),
        rating_level=_level_int(primary_level),
        rating_label=_level_label(primary_level),
        # ⚠️ 算分告警必须回显 —— 未参与算分的指标与原因。
        #    这些指标**不是**扣了分（权重已重新归一），但分数不完整，
        #    运维看不到就无从追溯。
        warnings=list(result.warnings or []),
        calculation_duration_ms=round(duration_ms, 2),
    )


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
    description=(
        "按冻结快照口径计算员工评分并落库到 Score 表。"
        "算分唯一真值来源为 services/scoring.py；考勤不参与算分（决策②）。"
    ),
)
async def execute_rules_engine(
    record_id: str = Path(..., description="评估记录 ID"),
    request: RulesEngineRequest = None,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """执行规则引擎计算员工最终评分。

    流程：
      1. 鉴权 + 数据范围校验（HR/Admin 全公司；主管按管理范围）
      2. 取考核记录 → 任务 → 冻结快照
      3. 状态检查（``force_execute`` 可越过）
      4. 委托 ``review_service.compute_score`` 算分
      5. ``review_service.persist_score`` 落库到 ``Score``
    """
    started = datetime.now()

    try:
        if not current_user:
            raise exceptions.NotAuthenticated("Token 无效或已过期")

        # ── 数据查询 ──
        record = db.query(AssessmentRecord).filter_by(id=record_id).first()
        if not record:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id)

        task = db.query(AssessmentTask).filter_by(id=record.task_id).first()
        if not task:
            raise exceptions.RecordNotFound("AssessmentTask", record.task_id)

        # ── 权限与数据范围 ──
        context = build_access_context(db, current_user)
        target_employee = db.get(Employee, record.employee_id)
        if target_employee is None:
            raise exceptions.RecordNotFound("Employee", record.employee_id)
        # ⚠️ require_active_target=True：已离职的员工不再参与**日常评分**。
        #    本人与主管/部门负责人被挡下（离职了还给他打绩效、还让他自己
        #    给自己算分都不合理），但 **HR 仍然放行** ——
        #    离职结算是真实业务场景，HR 要能算这份分。
        if not can_access_employee(
            context, target_employee, db, require_active_target=True
        ):
            raise exceptions.PermissionDenied(
                user_id=current_user.id,
                action="execute_rules_engine",
                resource="AssessmentRecord",
            )

        # ── 状态检查 ──
        force = bool(request.force_execute) if request else False
        if record.status not in _FORCEABLE_STATES and not force:
            raise exceptions.InvalidStateTransition(
                entity_type="AssessmentRecord",
                current_state=record.status,
                attempted_action="执行规则引擎（使用 force_execute=true 可强制执行）",
            )

        # ── 算分（唯一真值：scoring + 冻结快照 + MetricValue）──
        try:
            result = review_service.compute_score(db, record_id=record_id)
        except ScoringError as exc:
            # 配置类错误必须阻断，不能降级出假分（决策③）
            raise exceptions.ConfigurationError(detail=str(exc)) from exc
        except review_service.NotFound as exc:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id) from exc
        except review_service.InvalidState as exc:
            raise exceptions.InvalidStateTransition(
                entity_type="AssessmentRecord",
                current_state=record.status,
                attempted_action=str(exc),
            ) from exc

        # ── 落库（Score 表是唯一真值表）──
        review_service.persist_score(db, record_id=record_id, result=result)

        # 注意：**不推进 record.status**。
        # 状态机由业务流程（自评 → 主管评分 → 部门复核 → …）推进，
        # 算分只是「算出一个数并落库」，与权威流程 `review_service.confirm_draft`
        # 的做法一致。历史实现在这里写 `final_score` / `rating` /
        # `calculation_detail` —— 那三个列在 `AssessmentRecord` 上根本不存在。

        duration_ms = (datetime.now() - started).total_seconds() * 1000
        detail = _build_calculation_detail(record_id, result, duration_ms)

        db.flush()

        logger.info(
            "[规则引擎] 执行成功 record_id=%s total=%s warnings=%s",
            record_id,
            result.total,
            detail.warnings or "无",
        )

        return RulesEngineResponse(
            record_id=record.id,
            status="success",
            final_score=detail.final_score,
            rating_level=detail.rating_level,
            rating_label=detail.rating_label,
            calculation_detail=detail,
            message=(
                f"规则引擎执行成功，最终得分 {detail.final_score}"
                f"（{detail.rating_label}）"
                + (
                    f"；注意：{len(detail.warnings)} 个指标未参与算分"
                    f"（{detail.warnings[0]} …）"
                    if detail.warnings
                    else ""
                )
            ),
        )

    except exceptions.BaseAppError as e:
        logger.warning("[规则引擎] 业务异常: %s", e)
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "record_id": record_id,
                "error_code": e.code,
                "error_message": str(e),
                "timestamp": datetime.now().isoformat(),
            },
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[规则引擎] 系统异常: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "record_id": record_id,
                "error_code": "SYSTEM_ERROR",
                "error_message": "系统内部错误",
                "timestamp": datetime.now().isoformat(),
            }
        ) from e
