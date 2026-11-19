"""考核配置（指标 / 权重集 / 等级区间）—— 决策③「多维表格 HR 直改」的**产品落点**。

背景（BUG-R6-26）
------------------
算分的唯一真值链路是：

    Metric（指标定义）
      → WeightSet（权重集，部门 × 岗位）
        → Rubric（等级区间）
          → ConfigSnapshot（冻结快照，绑定到 AssessmentTask）
            → services/scoring.py 算分 → score 表

但直到 R6 闭环验证走通之前，**这条链路的"配置入口"完全不存在**：

- ``weight_set`` / ``rubric`` 表**永远是空的**；
- 没有读取也没有写入它们的 API；
- ``services/config_service.py`` 里的 ``LocalProvider`` / ``freeze_snapshot``
  / ``validate_config`` 写得很好，**但没有任何调用方**；
- 前端「参考分规则」页（``ScoreRule.vue``）管的是另一套东西
  （``ScoreRule`` JSON 块），与算分引擎读的 ``WeightSet``/``Rubric``
  **不是同一个东西** —— 名字像，语义不同，最容易误导人。

结果就是：即使前面的月计划 → 审批 → 成果上报 → 证据物化 → 指标写入
全链路都跑通了（``metric_value`` 里确实有 ``plan_execution_rate``），
**也永远算不出分** —— 因为没有权重、没有等级区间，
``scoring.build_specs`` 直接抛 ``ScoringError: 权重引用了未定义的指标``。

本模块补上这个缺口
------------------
提供 HR / 管理员可用的配置读写接口，以及"按当前配置发起考核任务
（自动冻结快照）"的接口，让闭环验证可以走**真实产品链路**而不是
直接改数据库。

权限
----
- 读配置：HR / 管理员（有 ``MANAGE_SYSTEM_CONFIG`` 权限）；
- 写配置：同上；
- 发起任务：HR / 管理员。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.db import get_db
from ...dependencies import get_current_user
from ...domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Employee,
    Metric,
    Rubric,
    WeightSet,
)
from ...services import config_service
from ...services.access_control import PermissionAction, build_access_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/assessment/config", tags=["assessment-config"])


def _ok(data: Any) -> dict:
    """统一响应信封（前端 ``http.ts`` 会自动拆包出 ``data``）。"""
    return {"code": 0, "data": data}


def _require_config_admin(db: Session, current_user: Employee) -> None:
    """只有 HR / 管理员能改算分配置。

    ⚠️ 算分配置是**分数的唯一来源**，权限必须收紧：
    任何能改权重的人都能改全公司每个人的分数。

    BUG-R6-31：过去这里要求 ``MANAGE_SYSTEM_CONFIG``，而该权限**只授予
    ``SystemAdmin``**。但决策③写得很清楚 ——「多维表格 HR 直改」——
    配置的**业务所有者是 HR**。结果是：

    - ``weight_set`` / ``rubric`` 表**永远是空的**（没有任何人调得动这个接口）；
    - 唯一的 admin 账号口令一变，整个算分链路再也无法从产品路径启动。

    现在 ``access_control`` 已把 ``MANAGE_SYSTEM_CONFIG`` 同时授予 HR，
    本函数无需再改 —— 它只认**权限**，不认角色名。
    """
    context = build_access_context(db, current_user)
    if not context.has_permission(PermissionAction.MANAGE_SYSTEM_CONFIG):
        raise HTTPException(status_code=403, detail="无权管理考核配置")


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class MetricUpsertRequest(BaseModel):
    """指标定义。"""

    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    unit: str | None = None
    calculation: str = Field(..., min_length=1)
    data_source: str = Field(..., min_length=1)
    caliber: str | None = None
    owner: str | None = None
    is_attendance: bool = False


class RubricLevel(BaseModel):
    """等级区间。``[min_ratio, max_ratio)``，``max_ratio=None`` 表示无上限。"""

    level: str = Field(..., min_length=1, max_length=8)
    min_ratio: float
    max_ratio: float | None = None
    score: float | None = None
    desc: str | None = None


class RubricUpsertRequest(BaseModel):
    """某个指标在某套权重下的等级区间。"""

    metric_code: str = Field(..., min_length=1, max_length=64)
    levels: list[RubricLevel] = Field(default_factory=list)


class WeightSetUpsertRequest(BaseModel):
    """权重集（部门 × 岗位）。

    ⚠️ ``weights`` 里的 ``metric_code`` 必须指向已定义的指标，
    合计必须 = 100 —— 由 ``config_service.validate_config`` 统一校验。
    """

    department_id: str = Field(..., min_length=1, max_length=64)
    position: str | None = None
    weights: list[dict[str, Any]] = Field(default_factory=list)
    rubrics: list[RubricUpsertRequest] = Field(default_factory=list)
    version: int = 1


class TaskCreateRequest(BaseModel):
    """发起考核任务（自动冻结配置快照 + 批量生成考核记录）。"""

    title: str = Field(..., min_length=1, max_length=128)
    period: str = Field(..., description="考核周期，YYYY-MM 或 YYYYMM")
    department_id: str | None = None
    position: str | None = None
    target_employee_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _serialize_metric(m: Metric) -> dict[str, Any]:
    return {
        "id": m.id,
        "code": m.code,
        "name": m.name,
        "unit": m.unit,
        "calculation": m.calculation,
        "dataSource": m.data_source,
        "caliber": m.caliber,
        "owner": m.owner,
        "isAttendance": bool(m.is_attendance),
        "active": bool(m.active),
    }


def _serialize_weight_set(db: Session, ws: WeightSet) -> dict[str, Any]:
    rubrics = (
        db.query(Rubric).filter(Rubric.weight_set_id == ws.id).all()
    )
    return {
        "id": ws.id,
        "departmentId": ws.department_id,
        "position": ws.position,
        "weights": ws.weights_json,
        "version": ws.version,
        "active": bool(ws.active),
        "rubrics": [
            {
                "metricCode": r.metric_code,
                "levels": r.levels_json,
            }
            for r in rubrics
        ],
    }


# ---------------------------------------------------------------------------
# 读接口
# ---------------------------------------------------------------------------


@router.get("/metrics")
def list_metrics(
    include_inactive: bool = Query(default=False),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """指标定义列表（算分的"有哪些维度"）。"""
    _require_config_admin(db, current_user)
    q = db.query(Metric)
    if not include_inactive:
        q = q.filter(Metric.active.is_(True))
    rows = q.order_by(Metric.code).all()
    return _ok([_serialize_metric(m) for m in rows])


@router.get("/weight-sets")
def list_weight_sets(
    department_id: str | None = Query(default=None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """权重集列表（含各自的等级区间）。"""
    _require_config_admin(db, current_user)
    q = db.query(WeightSet)
    if department_id:
        q = q.filter(WeightSet.department_id == department_id)
    rows = q.order_by(WeightSet.created_at.desc()).all()
    return _ok([_serialize_weight_set(db, ws) for ws in rows])


# ---------------------------------------------------------------------------
# 写接口
# ---------------------------------------------------------------------------


@router.post("/metrics")
def upsert_metric(
    payload: MetricUpsertRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """新增 / 更新指标定义（按 code 幂等）。"""
    _require_config_admin(db, current_user)

    existing = (
        db.query(Metric).filter(Metric.code == payload.code).one_or_none()
    )
    if existing is None:
        existing = Metric(id=f"METRIC_{payload.code}")
        db.add(existing)
    existing.code = payload.code
    existing.name = payload.name
    existing.unit = payload.unit or ""
    existing.calculation = payload.calculation
    existing.data_source = payload.data_source
    existing.caliber = payload.caliber or ""
    existing.owner = payload.owner or ""
    existing.is_attendance = payload.is_attendance
    existing.active = True
    db.flush()
    return _ok(_serialize_metric(existing))


@router.post("/weight-sets")
def upsert_weight_set(
    payload: WeightSetUpsertRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """新增 / 更新权重集（含等级区间）。

    ⚠️ 落库前跑一次 ``config_service.validate_config`` —— 这是决策③的
    **第一道保险**：权重合计 = 100、指标已定义、等级区间齐全。
    不合格的配置**不生效**（直接 422），而不是留到算分时才炸。
    """
    _require_config_admin(db, current_user)

    # 组装待校验的三段配置
    codes = [str(w.get("metric_code") or "") for w in payload.weights]
    metrics = []
    for code in codes:
        m = db.query(Metric).filter(Metric.code == code).one_or_none()
        if m is None:
            raise HTTPException(
                status_code=422, detail=f"权重引用了未定义的指标 {code}"
            )
        metrics.append(
            {
                "code": m.code,
                "name": m.name,
                "calculation": m.calculation,
                "data_source": m.data_source,
            }
        )
    rubric = [
        {"metric_code": r.metric_code, "levels_json": [level.model_dump() for level in r.levels]}
        for r in payload.rubrics
    ]

    try:
        config_service.validate_config(metrics, payload.weights, rubric)
    except config_service.ConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=f"配置校验失败：{exc}") from exc

    # 同一 部门 × 岗位 只保留一套生效配置（版本号递增）
    existing = (
        db.query(WeightSet)
        .filter(
            WeightSet.department_id == payload.department_id,
            WeightSet.position == payload.position,
        )
        .order_by(WeightSet.version.desc())
        .first()
    )
    version = payload.version
    if existing is not None:
        version = (existing.version or 1) + 1
        existing.weights_json = payload.weights
        existing.version = version
        existing.active = True
        ws = existing
    else:
        ws = WeightSet(
            department_id=payload.department_id,
            position=payload.position,
            weights_json=payload.weights,
            version=version,
            active=True,
        )
        db.add(ws)
    db.flush()

    # 等级区间按 metric_code 覆盖写
    for r in payload.rubrics:
        row = (
            db.query(Rubric)
            .filter(
                Rubric.weight_set_id == ws.id,
                Rubric.metric_code == r.metric_code,
            )
            .one_or_none()
        )
        levels = [level.model_dump() for level in r.levels]
        if row is None:
            db.add(
                Rubric(weight_set_id=ws.id, metric_code=r.metric_code, levels_json=levels)
            )
        else:
            row.levels_json = levels
    db.flush()

    return _ok(_serialize_weight_set(db, ws))


# ---------------------------------------------------------------------------
# 发起考核任务（冻结快照）
# ---------------------------------------------------------------------------


def _normalize_period(period: str) -> str:
    """周期归一成 ``YYYYMM``（与 ``AssessmentTask.period`` 一致）。

    ⚠️ ``metric_value.period`` 用的是 ``YYYY-MM``（见
    ``services/plan_execution_metric.py``），而 ``AssessmentTask.period``
    用 ``YYYYMM``（见 ``main.py`` 的任务创建）。**两者不一致会导致
    算分时查不到 ``MetricValue``，指标"缺数据不参与"，分数算不出来。**
    这里统一存 ``YYYYMM``，并在算分侧做兼容（见
    ``services/review_service.py`` 的 ``_build_metric_context``）。
    """
    text = str(period or "").strip()
    if len(text) == 7 and text[4] == "-":
        text = text[:4] + text[5:]
    if len(text) != 6 or not text.isdigit():
        raise HTTPException(
            status_code=422, detail=f"period 格式非法，应为 YYYY-MM 或 YYYYMM：{period}"
        )
    return text


@router.post("/tasks")
def create_assessment_task(
    payload: TaskCreateRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """发起考核任务：冻结配置快照 + 批量生成考核记录。

    ⚠️ 这是闭环验证里"算分"的前置条件。任务必须带上
    ``snapshot_id``，否则 ``review_service.compute_score`` 会抛
    ``InvalidState: 任务没有配置快照，无法计算``。
    """
    _require_config_admin(db, current_user)

    from ...domain.scheme_models import PerformanceScheme
    if db.query(PerformanceScheme.id).first() is not None:
        raise HTTPException(status_code=409, detail="已启用HR方案版本，请从考核方案管理按员工匹配方案发起考核")

    period = _normalize_period(payload.period)
    target_ids = [str(e) for e in payload.target_employee_ids if e]
    if not target_ids:
        raise HTTPException(status_code=422, detail="target_employee_ids 不能为空")

    task_id = f"TASK_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(target_ids)}"

    task = AssessmentTask(
        id=task_id,
        name=payload.title,
        period=period,
        department_id=payload.department_id,
        position=payload.position,
        status="created",
        rating_policy="reference_only",
        owner_id=current_user.id,
        created_at=datetime.now(),
    )
    db.add(task)
    db.flush()

    # 冻结配置快照（决策③第二道保险）
    provider = config_service.LocalProvider(db)
    try:
        snapshot = config_service.freeze_snapshot(db, task, provider)
    except config_service.ConfigValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"配置冻结失败：{exc}") from exc

    # 批量生成考核记录
    records = []
    for emp_id in target_ids:
        rec_id = f"REC_{task_id}_{emp_id}"
        if db.get(AssessmentRecord, rec_id) is not None:
            continue
        rec = AssessmentRecord(
            id=rec_id,
            task_id=task_id,
            employee_id=emp_id,
            status="not_started",
        )
        db.add(rec)
        records.append(rec_id)
    db.commit()

    logger.info(
        "已创建考核任务 %s（快照 %s），生成 %d 条考核记录",
        task_id,
        snapshot.id,
        len(records),
    )
    return _ok(
        {
            "taskId": task_id,
            "snapshotId": snapshot.id,
            "period": period,
            "recordIds": records,
            "configVersion": snapshot.config_version,
        }
    )
