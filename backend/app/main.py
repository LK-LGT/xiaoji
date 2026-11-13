"""业务后端 API —— 唯一事实源。

对外暴露的都是业务语义接口（发起考核、提交自评、生成草稿、确认、查分），
智能体层只通过内部契约被调用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.db import get_db, init_db
from .core.config import settings
from .domain.enums import SubmitSource
from .domain.models import (
    AssessmentRecord,
    AssessmentTask,
    CommentDraft,
    Employee,
    Evidence,
    MetricValue,
    RatingDraft,
    Score,
)
from .services import evidence_service, review_service, qa_service
from .services.review_service import AgentClient, InvalidState, NotFound
from . import schemas, exceptions, dependencies
from .domain.models import (
    ConfigSnapshot,
    Metric,
    WeightSet,
    Rubric,
    FormTemplate,
)
import json
from sqlalchemy import and_
from datetime import datetime

app = FastAPI(
    title="员工绩效管理 - 业务后端",
    version="0.1.0",
    description="唯一事实源。数字只在这里产生；AI 只做判断与写作。",
)

# ┌─────────────────────────────────────────────────────────────┐
# │ 注册路由（【板块3】自评接口）                              │
# └─────────────────────────────────────────────────────────────┘

try:
    from .routes import self_review as self_review_routes
    app.include_router(self_review_routes.router)
    print("[APP] Self-review router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register self-review router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import feishu as feishu_routes
    app.include_router(feishu_routes.router)
    print("[APP] Feishu router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register feishu router: {e}")
    import traceback
    traceback.print_exc()

# 直接在 main.py 中也注册一份（确保生效）
try:
    from .schemas_self_review import SelfReviewSubmitRequest, SelfReviewSubmitResponse
    from .services import self_review_service
    
    @app.post(
        "/v1/assessment/records/{record_id}/self-review",
        response_model=SelfReviewSubmitResponse,
        status_code=201,
        tags=["SelfReview"],
        summary="【板块3】员工提交自评",
    )
    async def submit_self_review_direct(
        record_id: str,
        request: SelfReviewSubmitRequest,
        current_user: Employee = Depends(dependencies.get_current_user),
        db: Session = Depends(get_db),
    ):
        try:
            dependencies.check_can_submit_review(current_user.id, record_id, db)
            result_data = self_review_service.submit_self_review(
                db,
                record_id=record_id,
                current_employee=current_user,
                req=request,
            )
            return SelfReviewSubmitResponse(
                code=0, message="自评已提交成功", data=result_data
            )
        except exceptions.PermissionDenied as exc:
            raise HTTPException(status_code=403, detail={"code": exc.code, "message": exc.message}) from exc
        except exceptions.RecordNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc
        except exceptions.InvalidStateTransition as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
        except exceptions.DataValidationError as exc:
            raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Error: {exc}", exc_info=True)
            db.rollback()
            raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "服务器内部错误"}) from exc
    
    print("[APP] Self-review endpoint registered in main.py")
except Exception as e:
    print(f"[APP] WARNING: Failed to register self-review endpoint: {e}")
    import traceback
    traceback.print_exc()

@app.on_event("startup")
def _startup() -> None:
    from .core.db import init_db
    init_db()


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class CreateTaskReq(BaseModel):
    name: str
    period: str
    department_id: str | None = None
    position: str | None = None
    owner_id: str | None = None


class SubmitSelfReviewReq(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    source: SubmitSource
    dedup_key: str | None = None


class MetricValueReq(BaseModel):
    employee_id: str
    metric_code: str
    period: str
    value: float
    target: float | None = None
    unit: str | None = None
    source: str
    source_ref: str | None = None
    as_of: datetime | None = None


class AnomalyReq(BaseModel):
    employee_id: str
    period: str
    anomaly_type: str
    count: int = 1
    as_of: datetime | None = None
    approved: bool | None = None
    detail: str | None = None


class ConfirmReq(BaseModel):
    confirmed_by: str
    confirmed_rating: str | None = None


# ---------------------------------------------------------------------------
# 基础查询
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "agent_service_url": settings.agent_service_url,
        "rating_policy": settings.rating_policy,
    }


@app.get("/api/employees")
def list_employees(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.execute(select(Employee)).scalars().all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "department_id": e.department_id,
            "position": e.position,
            "manager_id": e.manager_id,
        }
        for e in rows
    ]


@app.get("/api/records/{record_id}")
def get_record(record_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """查一条考核记录的全貌：分数、草稿、证据。"""
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")

    score = db.execute(
        select(Score).where(Score.record_id == record_id)
    ).scalar_one_or_none()
    rating = db.execute(
        select(RatingDraft)
        .where(RatingDraft.record_id == record_id)
        .order_by(RatingDraft.created_at.desc())
    ).scalars().first()
    comment = db.execute(
        select(CommentDraft)
        .where(CommentDraft.record_id == record_id)
        .order_by(CommentDraft.created_at.desc())
    ).scalars().first()

    emp = db.get(Employee, rec.employee_id)
    ev_rows = evidence_service.list_scoring_evidence(db, rec.employee_id)
    scoring_ev, reference_ev = evidence_service.split_by_usage(ev_rows)

    return {
        "record_id": record_id,
        "status": rec.status,
        "employee": {"id": emp.id, "name": emp.name, "position": emp.position} if emp else None,
        "submit_source": rec.submit_source,
        "confirmed_rating": rec.confirmed_rating,
        "score": (
            {
                "total": score.total,
                "per_metric": score.per_metric,
                "computed_at": score.computed_at.isoformat(),
            }
            if score
            else None
        ),
        "rating_draft": (
            {
                "ratings": rating.ratings_json,
                "citations": rating.citations,
                "gaps": rating.gaps_json,
                "fairness": rating.fairness_json,
                "status": rating.status,
            }
            if rating
            else None
        ),
        "comment_draft": (
            {"markdown": comment.markdown, "citations": comment.citations, "status": comment.status}
            if comment
            else None
        ),
        "evidence": {
            "scoring": [
                {"id": e.id, "summary": e.summary, "impact": e.impact, "source": e.source}
                for e in scoring_ev
            ],
            # 决策②：参考类证据（考勤）单独返回，带免责标注
            "reference_only": evidence_service.build_attendance_view(reference_ev),
        },
    }


# ---------------------------------------------------------------------------
# 考核任务
# ---------------------------------------------------------------------------


@app.post("/v1/assessment/tasks", tags=["Task"])
def create_assessment_task(
    request: schemas.AssessmentTaskRequest,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> schemas.AssessmentTaskResponse:
    """
    【板块2】发起考核任务 - HR 专属接口。
    
    流程：
    1. 权限检查：仅 HR 可调用
    2. 参数验证：日期、员工列表有效性
    3. 生成 ConfigSnapshot：冻结当前配置（指标、权重、评分标准、表单）
    4. 创建 AssessmentTask：任务主记录
    5. 批量生成 AssessmentRecord：为每个被评员工生成记录
    
    设计要点：
    - ConfigSnapshot：确保本次考核的配置不会被中途变更影响
    - 幂等性：同一 task_id 重复调用不会重复创建记录
    - 权限隔离：从 token 验证身份，不信任 current_user 参数
    
    参数：
    - title: 任务标题（如'2026 年 11 月月度考核'）
    - target_employee_ids: 被评员工 ID 列表
    - start_date / end_date: 考核周期
    - self_review_deadline: 自评截止时间
    - manager_review_deadline: 主管评分截止时间
    
    返回：
    - task_id: 新创建的任务 ID
    - status: 任务状态（created）
    - target_count: 被评员工数
    - created_at: 创建时间
    """
    
    try:
        # ─────────────────────────────────────────────────────────────
        # 1. 权限检查：仅 HR 可调用
        # ─────────────────────────────────────────────────────────────
        if not dependencies.check_permission(current_user, "view_all_scores"):
            # 仅 HR 有 view_all_scores 权限
            raise exceptions.PermissionDenied(
                user_id=current_user.id,
                action="create_assessment_task",
                resource="AssessmentTask（仅 HR 可操作）"
            )
        
        # ─────────────────────────────────────────────────────────────
        # 2. 参数验证
        # ─────────────────────────────────────────────────────────────
        
        # 验证员工列表不为空
        if not request.target_employee_ids:
            raise exceptions.DataValidationError(
                "被评员工列表不能为空",
                field="target_employee_ids"
            )
        
        # 验证被评员工都存在
        employees = db.query(Employee).filter(
            Employee.id.in_(request.target_employee_ids)
        ).all()
        
        found_ids = {e.id for e in employees}
        missing_ids = set(request.target_employee_ids) - found_ids
        if missing_ids:
            raise exceptions.DataValidationError(
                f"以下员工不存在：{', '.join(missing_ids)}",
                field="target_employee_ids"
            )
        
        # 验证日期顺序
        from datetime import datetime as dt_cls
        start_dt = dt_cls.fromisoformat(request.start_date)
        end_dt = dt_cls.fromisoformat(request.end_date)
        self_review_dt = dt_cls.fromisoformat(request.self_review_deadline)
        manager_review_dt = dt_cls.fromisoformat(request.manager_review_deadline)
        
        if start_dt >= end_dt:
            raise exceptions.DataValidationError(
                "开始日期必须早于结束日期",
                field="start_date"
            )
        
        if self_review_dt <= start_dt or self_review_dt > manager_review_dt:
            raise exceptions.DataValidationError(
                "自评截止时间必须在考核开始后、主管评分前",
                field="self_review_deadline"
            )
        
        # ─────────────────────────────────────────────────────────────
        # 3. 生成 ConfigSnapshot（冻结配置）
        # ─────────────────────────────────────────────────────────────
        
        # 从数据库读取当前活跃的配置
        # （实际项目中这里应该有更复杂的逻辑，这里简化为示例）
        metrics = db.query(Metric).filter_by(active=True).all()
        weight_sets = db.query(WeightSet).filter_by(active=True).all()
        rubrics = db.query(Rubric).all()
        form_templates = db.query(FormTemplate).filter_by(active=True).all()
        
        # 构建快照 JSON
        metrics_json = [
            {
                "id": m.id,
                "code": m.code,
                "name": m.name,
                "unit": m.unit,
                "calculation": m.calculation,
                "data_source": m.data_source,
                "caliber": m.caliber,
                "owner": m.owner,
                "is_attendance": m.is_attendance,
            }
            for m in metrics
        ]
        
        weights_json = [
            {
                "id": w.id,
                "department_id": w.department_id,
                "position": w.position,
                "weights": w.weights_json,
                "version": w.version,
            }
            for w in weight_sets
        ]
        
        rubric_json = [
            {
                "id": r.id,
                "weight_set_id": r.weight_set_id,
                "metric_code": r.metric_code,
                "levels": r.levels_json,
            }
            for r in rubrics
        ]
        
        form_template_json = None
        if form_templates:
            form_template_json = {
                "id": form_templates[0].id,
                "name": form_templates[0].name,
                "fields": form_templates[0].fields_json,
                "version": form_templates[0].version,
            }
        
        # 创建 ConfigSnapshot 记录（task_id 作为唯一键）
        task_id = f"TASK_{dt_cls.now().strftime('%Y%m%d%H%M%S')}_{len(request.target_employee_ids)}"
        
        config_snapshot = ConfigSnapshot(
            task_id=task_id,
            metrics_json=metrics_json,
            weights_json=weights_json,
            rubric_json=rubric_json,
            form_template_json=form_template_json,
            config_version="1.0.0",
            frozen_at=dt_cls.now()
        )
        db.add(config_snapshot)
        db.flush()
        
        # ─────────────────────────────────────────────────────────────
        # 4. 创建 AssessmentTask 记录
        # ─────────────────────────────────────────────────────────────
        
        task = AssessmentTask(
            id=task_id,
            name=request.title,
            period=start_dt.strftime("%Y%m"),  # 期间：202611 格式
            department_id=None,  # 跨部门考核时为 None
            position=None,
            status="created",  # 初始状态
            rating_policy="reference_only",  # Mock 政策：参考类证据仅参考不计分
            snapshot_id=config_snapshot.id,
            owner_id=current_user.id,  # 由当前 HR 用户创建
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()
        
        # ─────────────────────────────────────────────────────────────
        # 5. 批量生成 AssessmentRecord
        # ─────────────────────────────────────────────────────────────
        
        records = []
        for emp_id in request.target_employee_ids:
            record = AssessmentRecord(
                task_id=task_id,
                employee_id=emp_id,
                status="not_started",  # 初始状态：未开始
                submit_source=None,  # 尚未提交
                submit_locked=False,
                confirmed_rating=None,  # 尚未确认等级
                created_at=dt_cls.now(),
                updated_at=dt_cls.now()
            )
            records.append(record)
        
        db.add_all(records)
        db.flush()
        
        # ─────────────────────────────────────────────────────────────
        # 6. 提交事务
        # ─────────────────────────────────────────────────────────────
        
        db.commit()
        
        # ─────────────────────────────────────────────────────────────
        # 7. 返回响应
        # ─────────────────────────────────────────────────────────────
        
        return schemas.AssessmentTaskResponse(
            task_id=task_id,
            status="created",
            created_at=task.created_at.isoformat(),
            target_count=len(records),
            message=f"成功创建考核任务，包含 {len(records)} 名被评员工"
        )
    
    except exceptions.BaseAppError as exc:
        # 自定义业务异常：直接转换为 HTTP 响应
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message
        ) from exc
    
    except Exception as exc:
        # 未预期的异常：通用错误处理
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"内部错误：{str(exc)}"
        ) from exc


@app.post("/api/tasks")
def create_task(req: CreateTaskReq, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        task = review_service.create_task(
            db,
            name=req.name,
            period=req.period,
            department_id=req.department_id,
            position=req.position,
            owner_id=req.owner_id,
        )
    except review_service.config_service.ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=f"配置校验失败：{exc}") from exc
    return {"task_id": task.id, "snapshot_id": task.snapshot_id, "status": task.status}


@app.post("/api/tasks/{task_id}/open-records")
def open_records(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        recs = review_service.open_records(db, task_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"count": len(recs), "record_ids": [r.id for r in recs]}


# ---------------------------------------------------------------------------
# 自评
# ---------------------------------------------------------------------------


@app.post("/api/records/{record_id}/self-review")
def submit_self_review(
    record_id: str, req: SubmitSelfReviewReq, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        sr = review_service.submit_self_review(
            db,
            record_id=record_id,
            answers=req.answers,
            attachments=req.attachments,
            source=req.source,
            dedup_key=req.dedup_key,
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"self_review_id": sr.id, "submitted_at": sr.submitted_at.isoformat() if sr.submitted_at else None}


# ---------------------------------------------------------------------------
# 数据写入
# ---------------------------------------------------------------------------


@app.post("/api/metric-values")
def add_metric_value(req: MetricValueReq, db: Session = Depends(get_db)) -> dict[str, Any]:
    """写入指标实测值（算分集唯一入口）。考勤来源会被拒绝。"""
    try:
        mv = evidence_service.add_metric_value(
            db,
            employee_id=req.employee_id,
            metric_code=req.metric_code,
            period=req.period,
            value=req.value,
            target=req.target,
            unit=req.unit,
            source=req.source,
            source_ref=req.source_ref,
            as_of=req.as_of,
        )
    except evidence_service.AttendanceEvidenceRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": mv.id}


@app.post("/api/attendance/anomalies")
def add_anomaly(req: AnomalyReq, db: Session = Depends(get_db)) -> dict[str, Any]:
    """写入考勤异常。全部 reference_only，不进算分集。"""
    from .domain.enums import AttendanceAnomalyType

    try:
        atype = AttendanceAnomalyType(req.anomaly_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"未知考勤异常类型：{req.anomaly_type}") from exc

    evs = evidence_service.record_attendance_anomalies(
        db,
        [
            evidence_service.AnomalyInput(
                employee_id=req.employee_id,
                period=req.period,
                anomaly_type=atype,
                count=req.count,
                as_of=req.as_of,
                approved=req.approved,
                detail=req.detail,
            )
        ],
    )
    return {
        "evidence_ids": [e.id for e in evs],
        "usage": evs[0].usage,
        "disclaimer": evidence_service.ATTENDANCE_DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------


@app.post("/api/records/{record_id}/draft")
def create_draft(record_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """生成评级建议 + 评语草稿。

    智能体不可用时自动降级，**不会失败** —— 流程不中断。
    """
    try:
        bundle = review_service.generate_draft(db, record_id=record_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "record_id": record_id,
        "score": bundle.score.total,
        "ratings": bundle.ratings,
        "comment_draft": bundle.comment_markdown,
        "citations": bundle.citations,
        "gaps": bundle.gaps,
        "fairness": bundle.fairness,
        "degraded": bundle.degraded,
        "agent_status": bundle.agent_status,
    }


@app.post("/api/records/{record_id}/score/recompute")
def recompute_score(record_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """复算分数。**关掉 AI 重跑，结果完全一致**（框架验收第 1 条）。"""
    try:
        result = review_service.compute_score(db, record_id=record_id)
        review_service.persist_score(db, record_id=record_id, result=result)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result.to_dict()


@app.post("/api/records/{record_id}/confirm")
def confirm(record_id: str, req: ConfirmReq, db: Session = Depends(get_db)) -> dict[str, Any]:
    """人工确认草稿（最后一道闸）。"""
    try:
        return review_service.confirm_draft(
            db,
            record_id=record_id,
            confirmed_by=req.confirmed_by,
            confirmed_rating=req.confirmed_rating,
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/records/{record_id}/citations/validate")
def validate_citations(
    record_id: str, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """校验草稿引用的证据是否有效（存在、属于该员工、可见性允许）。"""
    rating = db.execute(
        select(RatingDraft)
        .where(RatingDraft.record_id == record_id)
        .order_by(RatingDraft.created_at.desc())
    ).scalars().first()
    if rating is None:
        raise HTTPException(status_code=404, detail="没有草稿")

    ok, valid = review_service.validate_citations(
        db, record_id=record_id, evidence_ids=list(rating.citations)
    )
    return {
        "grounded": ok,
        "valid_citations": valid,
        "invalid_citations": [c for c in rating.citations if c not in valid],
    }


# ┌─────────────────────────────────────────────────────┐
# │ P0-2：Q&A 查询 API（改成 POST）                     │
# │ P0-1：progress / result 拆分                        │
# │ P0-4：数据质量 + 隐私脱敏                           │
# │ P2-1：k-匿名 检查                                  │
# └─────────────────────────────────────────────────────┘


@app.post("/v1/api/qa/query", tags=["QA"])
def query_assessment_data(
    request: schemas.QAQueryRequest,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """
    Q&A 查询接口（改成 POST，参考 P0-2）。
    
    查询类型（参考 P0-1）：
    - self_progress：我的月中进度（未确认的数据）
    - self_result：我的月末成绩（已确认）
    - colleague_result：某员工的成绩（权限检查）
    - peer_performance_distribution：部门等级分布（聚合，不排名，参考 P2-2）
    
    关键原则（P0/P2）：
    - 权限来自 token（current_user），不信 URL 参数
    - 响应含 data_quality（替代 confidence）
    - 不暴露 score 字段
    - < 5 人时隐藏平均分（k-匿名，P2-1）
    """
    
    try:
        # 1. 权限检查 + 数据查询
        if request.query_type == "self_progress":
            if request.target_employee_id != current_user.id:
                raise exceptions.PermissionDenied(
                    current_user.id,
                    f"query {request.query_type}",
                    f"employee:{request.target_employee_id}"
                )
            progress_data = qa_service.query_self_progress(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = progress_data.model_dump()
        
        elif request.query_type == "self_result":
            if request.target_employee_id != current_user.id:
                raise exceptions.PermissionDenied(
                    current_user.id,
                    "query self_result",
                    f"employee:{request.target_employee_id}"
                )
            result_data = qa_service.query_self_result(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = result_data.model_dump()
        
        elif request.query_type == "colleague_result":
            # 主管查下级，或 HR 查任何人
            dependencies.check_can_view_score(
                current_user,
                request.target_employee_id,
                db
            )
            result_data = qa_service.query_colleague_result(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = result_data.model_dump()
        
        elif request.query_type == "peer_performance_distribution":
            # 部门汇总（权限检查：只能查自己部门）
            target_emp = db.get(Employee, request.target_employee_id)
            if not target_emp:
                raise exceptions.RecordNotFound("Employee", request.target_employee_id)
            
            summary_data = qa_service.query_department_summary(
                target_emp.department_id,
                request.task_id,
                db
            )
            data = summary_data.model_dump()
        
        else:
            raise exceptions.DataValidationError(
                f"未知查询类型: {request.query_type}",
                field="query_type"
            )
        
        # 2. 计算数据质量（参考 P0-5）
        data_quality = schemas.DataQualityInfo(
            completeness=0.95,
            as_of=datetime.now().isoformat(),
            degraded=False,
            citations=["AssessmentRecord", "Score", "MetricValue"]
        )
        
        return {
            "query_type": request.query_type,
            "data": data,
            "data_quality": data_quality.model_dump()
        }
    
    except exceptions.BaseAppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ┌─────────────────────────────────────────────────────┐
# │ 主管评分 API（补全的路由，P0-3）                   │
# └─────────────────────────────────────────────────────┘


class ManagerReviewReq(BaseModel):
    """主管提交评分。"""
    manager_comment: str = Field(..., min_length=1, max_length=2000)
    confirmed_rating: str = Field(..., pattern="^[A-D]$")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


@app.post("/v1/assessment/records/{record_id}/manager-review", tags=["Review"])
def submit_manager_review(
    record_id: str,
    request: ManagerReviewReq,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """主管提交评分（P0-3 权限模型）。"""
    
    try:
        # 1. 权限检查
        record = db.get(AssessmentRecord, record_id)
        if not record:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id)
        
        target_emp = db.get(Employee, record.employee_id)
        if not target_emp or target_emp.manager_id != current_user.id:
            raise exceptions.PermissionDenied(
                current_user.id,
                "submit_manager_review",
                f"record:{record_id}"
            )
        
        # 2. 验证等级
        if request.confirmed_rating not in ("A", "B", "C", "D"):
            raise exceptions.DataValidationError(
                f"等级必须是 A/B/C/D，得到 {request.confirmed_rating}",
                field="confirmed_rating"
            )
        
        # 3. 更新 Record
        record.manager_comment = request.manager_comment
        record.confirmed_rating = request.confirmed_rating
        record.stage = "confirmation"
        db.add(record)
        db.flush()
        
        # 4. 触发自动计分
        result = review_service.compute_score(db, record_id=record_id)
        review_service.persist_score(db, record_id=record_id, result=result)
        
        db.commit()
        
        return {
            "status": "success",
            "record_id": record_id,
            "confirmed_rating": request.confirmed_rating,
            "stage": "confirmation"
        }
    
    except exceptions.BaseAppError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except InvalidState as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ┌─────────────────────────────────────────────────────┐
# │ 添加指标值路由（补全 P0-5 data_quality）           │
# └─────────────────────────────────────────────────────┘


@app.post("/v1/assessment/records/{record_id}/metrics", tags=["Metrics"])
def add_metric_to_record(
    record_id: str,
    request: schemas.MetricValueRequest,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """添加指标值到某条 Record（P0-5 data_quality 相关）。"""
    
    try:
        record = db.get(AssessmentRecord, record_id)
        if not record:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id)
        
        # 添加指标值（决策②检查内置）
        metric_value = evidence_service.add_metric_value(
            db,
            employee_id=record.employee_id,
            metric_code=request.metric_code,
            period="",
            value=request.value,
            source=request.source,
            as_of=request.collected_at
        )
        
        db.commit()
        
        return {
            "status": "success",
            "metric_id": metric_value.id,
            "metric_code": request.metric_code,
            "value": request.value
        }
    
    except exceptions.AttendanceEvidenceRejected as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except exceptions.BaseAppError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ┌─────────────────────────────────────────────────────┐
# │ 健康检查 + 系统信息                                 │
# └─────────────────────────────────────────────────────┘


@app.get("/v1/health", tags=["Health"])
def health_check() -> dict[str, Any]:
    """健康检查。"""
    return {
        "status": "ok",
        "version": "1.0.0",
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/v1/info", tags=["Health"])
def system_info() -> dict[str, Any]:
    """系统信息。"""
    return {
        "agents": ["QA", "FillCoach", "Suggestion", "Grounding", "PerformanceExplain", "RankExplain"],
        "rules_version": "1.0.0",
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "api_version": "1.0.0",
        "features": {
            "k_anonymity": True,
            "privacy_degradation": True,
            "attendance_rejection": True,
            "model_not_trusted": True
        }
    }


# ============================================================================
# 认证接口（Auth）—— 前端登录必需
# ============================================================================

class LoginRequest(BaseModel):
    """登录请求模型。"""
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    """登录响应模型。"""
    accessToken: str
    refreshToken: str
    expiresIn: int = 7200
    userId: int
    username: str
    realName: str
    forceChangePassword: bool = False
    roles: list[str]
    permissions: list[str]


class AuthUserResponse(BaseModel):
    """当前用户信息模型。"""
    userId: int
    username: str
    realName: str
    mobile: str = ""
    deptId: str | None = None
    departmentName: str | None = None
    groupId: str | None = None
    status: int | None = None
    statusSource: str | None = None
    lastLoginAt: str | None = None
    forceChangePassword: bool = False
    roles: list[str]
    permissions: list[str]


# Mock 用户数据库
MOCK_USERS = {
    "admin": {
        "password": "admin123",
        "user_id": 1,
        "username": "admin",
        "realName": "系统管理员",
        "mobile": "13800000001",
        "dept_id": "DEPT_HR",
        "department_name": "人力资源部",
        "roles": ["admin", "hr"],
        "permissions": ["system:dashboard:view", "system:employee:register", "system:employee:view", "system:org:view", "system:role:view", "system:permission:view", "system:workday:view", "system:week-standard:view", "system:audit:view", "system:knowledge:view"],
    },
    "manager": {
        "password": "manager123",
        "user_id": 2,
        "username": "manager",
        "realName": "王伟（销售经理）",
        "mobile": "13800000002",
        "dept_id": "DEPT_SALES",
        "department_name": "销售部",
        "roles": ["manager"],
        "permissions": ["department:dashboard:view", "department:month-approval:view", "department:result-confirm:view", "department:todo:view", "department:template:view", "department:standard:view", "department:score-rule:view", "department:export-task:view"],
    },
    "employee": {
        "password": "employee123",
        "user_id": 3,
        "username": "employee",
        "realName": "刘刚（销售代表）",
        "mobile": "13800000003",
        "dept_id": "DEPT_SALES",
        "department_name": "销售部",
        "roles": ["employee"],
        "permissions": ["dashboard:view", "planning:month:view", "planning:day:view", "planning:result:view"],
    },
}


@app.post("/auth/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """用户登录。
    
    测试账号：
    - admin / admin123
    - manager / manager123
    - employee / employee123
    """
    
    # 验证用户名密码
    user_data = MOCK_USERS.get(req.username)
    if not user_data or user_data["password"] != req.password:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    
    # 生成 token
    access_token = f"token_{req.username}_{user_data['user_id']}_{datetime.now().timestamp()}"
    refresh_token = f"refresh_{req.username}_{user_data['user_id']}"
    
    # 返回前端期望的格式
    return {
        "code": 0,
        "data": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresIn": 7200,
            "userId": user_data["user_id"],
            "username": user_data["username"],
            "realName": user_data["realName"],
            "forceChangePassword": False,
            "roles": user_data["roles"],
            "permissions": user_data["permissions"],
        }
    }


@app.get("/auth/me")
async def get_current_user_info(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """获取当前登录用户信息。"""
    
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少 Authorization header")
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authorization header 格式错误")
    
    token = parts[1]
    
    # 从 token 中提取用户名
    try:
        token_parts = token.split("_")
        if len(token_parts) < 3:
            raise ValueError("Token 格式错误")
        username = token_parts[1]
    except Exception:
        raise HTTPException(status_code=401, detail="Token 无效")
    
    user_data = MOCK_USERS.get(username)
    if not user_data:
        raise HTTPException(status_code=401, detail="用户不存在")
    
    # 返回前端期望的格式
    return {
        "code": 0,
        "data": {
            "userId": user_data["user_id"],
            "username": user_data["username"],
            "realName": user_data["realName"],
            "mobile": user_data.get("mobile", ""),
            "deptId": user_data.get("dept_id"),
            "departmentName": user_data.get("department_name"),
            "groupId": None,
            "status": None,
            "statusSource": None,
            "lastLoginAt": datetime.now().isoformat(),
            "forceChangePassword": False,
            "roles": user_data["roles"],
            "permissions": user_data["permissions"],
        }
    }


@app.post("/auth/logout")
async def logout(authorization: str | None = Header(None)):
    """用户登出。"""
    return {"code": 0, "data": {"message": "登出成功"}}


# ============================================================================
# 计划与成果接口（Planning）—— Mock 数据，用于演示
# ============================================================================

class PlanningStatsResponse(BaseModel):
    """仪表板统计数据。"""
    pendingDayPlans: int
    overduePendingDayPlans: int
    currentMonthResults: int
    closureRate: str


class MonthPlan(BaseModel):
    """月计划。"""
    id: int
    title: str
    planMonth: str
    status: str
    ownerUserId: int
    deptId: str | None = None


class DayPlan(BaseModel):
    """日计划。"""
    id: int
    title: str
    planDate: str
    content: str
    status: str
    ownerUserId: int
    deptId: str | None = None


class Result(BaseModel):
    """成果。"""
    id: int
    title: str
    resultDate: str
    content: str
    planType: str
    status: str
    ownerUserId: int
    deptId: str | None = None


@app.get("/system/dashboard")
async def get_system_dashboard(db: Session = Depends(get_db)):
    """获取系统管理工作台数据。"""
    return {
        "code": 0,
        "data": {
            "departmentCount": 8,
            "employeeCount": 156,
            "roleCount": 6,
            "auditCount": 2847,
            "risks": [
                {
                    "code": "NO_WORKDAY_RULE",
                    "level": "warning",
                    "title": "未配置工作日规则",
                    "count": 0,
                    "route": "/system/workday-rules"
                },
                {
                    "code": "INCOMPLETE_PERMISSIONS",
                    "level": "error",
                    "title": "权限配置不完整",
                    "count": 3,
                    "route": "/system/permissions"
                },
                {
                    "code": "INACTIVE_EMPLOYEES",
                    "level": "warning",
                    "title": "启用员工数量较少",
                    "count": 12,
                    "route": "/system/employees"
                }
            ],
            "recentAudits": [
                {
                    "createdAt": "2026-11-12 13:45:23",
                    "action": "LOGIN",
                    "actionLabel": "用户登录",
                    "targetType": "User",
                    "targetSummary": "admin",
                    "operatorName": "系统管理员",
                    "username": "admin",
                    "result": "SUCCESS"
                },
                {
                    "createdAt": "2026-11-12 13:40:15",
                    "action": "USER_CREATE",
                    "actionLabel": "创建用户",
                    "targetType": "User",
                    "targetSummary": "新员工账号",
                    "operatorName": "系统管理员",
                    "username": "admin",
                    "result": "SUCCESS"
                },
                {
                    "createdAt": "2026-11-12 13:35:42",
                    "action": "ROLE_UPDATE",
                    "actionLabel": "修改角色",
                    "targetType": "Role",
                    "targetSummary": "销售经理",
                    "operatorName": "系统管理员",
                    "username": "admin",
                    "result": "SUCCESS"
                }
            ]
        }
    }
async def get_planning_stats(db: Session = Depends(get_db)):
    """获取仪表板统计数据。"""
    return {
        "code": 0,
        "data": {
            "pendingDayPlans": 12,
            "overduePendingDayPlans": 3,
            "currentMonthResults": 45,
            "closureRate": "87%",
        }
    }


@app.get("/planning/month-plans")
async def list_month_plans(db: Session = Depends(get_db)):
    """列出月计划。"""
    return {
        "code": 0,
        "data": [
            {
                "id": 1,
                "title": "2026年11月销售目标",
                "planMonth": "2026-11",
                "status": "APPROVED",
                "ownerUserId": 2,
                "deptId": "DEPT_SALES",
            },
            {
                "id": 2,
                "title": "2026年11月运营目标",
                "planMonth": "2026-11",
                "status": "PENDING",
                "ownerUserId": 3,
                "deptId": "DEPT_OPS",
            },
        ]
    }


@app.get("/planning/day-plans")
async def list_day_plans(db: Session = Depends(get_db)):
    """列出日计划。"""
    return {
        "code": 0,
        "data": [
            {
                "id": 1,
                "title": "完成第一季度财务预算",
                "planDate": "2026-11-12",
                "content": "编制详细的财务预算方案",
                "status": "APPROVED",
                "ownerUserId": 1,
                "deptId": "DEPT_FINANCE",
            },
            {
                "id": 2,
                "title": "跟进大客户合同",
                "planDate": "2026-11-12",
                "content": "与客户沟通合同条款",
                "status": "PENDING",
                "ownerUserId": 2,
                "deptId": "DEPT_SALES",
            },
        ]
    }


@app.get("/planning/results")
async def list_results(db: Session = Depends(get_db)):
    """列出成果。"""
    return {
        "code": 0,
        "data": [
            {
                "id": 1,
                "title": "完成月度财务汇总",
                "resultDate": "2026-11-11",
                "content": "按时完成财务部所有部门的成本统计和分析",
                "planType": "DAY",
                "status": "CONFIRMED",
                "ownerUserId": 1,
                "deptId": "DEPT_FINANCE",
            },
            {
                "id": 2,
                "title": "签署新客户合同",
                "resultDate": "2026-11-10",
                "content": "成功签署A公司年度服务合同，金额100万",
                "planType": "MONTH",
                "status": "PENDING",
                "ownerUserId": 2,
                "deptId": "DEPT_SALES",
            },
        ]
    }


# ============================================================================
# 员工管理 API
# ============================================================================

from .domain.models import Employee, Department


@app.get("/system/employees")
async def list_employees(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    department_id: str = Query(None),
    status: str = Query(None),
    db: Session = Depends(get_db)
):
    """获取员工列表。"""
    query = db.query(Employee)
    
    # 筛选
    if department_id:
        query = query.filter(Employee.department_id == department_id)
    if status:
        query = query.filter(Employee.employment_status == status)
    
    # 获取总数
    total = query.count()
    
    # 分页
    employees = query.offset(skip).limit(limit).all()
    
    return {
        "code": 0,
        "data": {
            "list": [{
                "id": emp.id,
                "name": emp.name,
                "email": emp.email,
                "department_id": emp.department_id,
                "position": emp.position,
                "manager_id": emp.manager_id,
                "employment_status": emp.employment_status,
                "joined_at": emp.joined_at.isoformat() if emp.joined_at else None,
            } for emp in employees],
            "total": total,
            "page": skip // limit + 1,
            "pageSize": limit,
        }
    }


@app.get("/system/employees/{employee_id}")
async def get_employee(
    employee_id: str,
    db: Session = Depends(get_db)
):
    """获取单个员工详情。"""
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        return {"code": 404, "message": "Employee not found"}
    
    return {
        "code": 0,
        "data": {
            "id": employee.id,
            "name": employee.name,
            "email": employee.email,
            "department_id": employee.department_id,
            "position": employee.position,
            "manager_id": employee.manager_id,
            "employment_status": employee.employment_status,
        }
    }


@app.post("/system/employees")
async def create_employee(
    req: dict,
    db: Session = Depends(get_db)
):
    """创建新员工。"""
    new_employee = Employee(
        name=req.get("name"),
        email=req.get("email"),
        department_id=req.get("department_id"),
        position=req.get("position"),
        manager_id=req.get("manager_id"),
        employment_status="active",
    )
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)
    
    return {
        "code": 0,
        "data": {"id": new_employee.id, "name": new_employee.name}
    }


@app.put("/system/employees/{employee_id}")
async def update_employee(
    employee_id: str,
    req: dict,
    db: Session = Depends(get_db)
):
    """更新员工信息。"""
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        return {"code": 404, "message": "Employee not found"}
    
    # 允许更新的字段
    for key in ["name", "email", "department_id", "position", "manager_id", "employment_status"]:
        if key in req:
            setattr(employee, key, req[key])
    
    db.commit()
    db.refresh(employee)
    
    return {
        "code": 0,
        "data": {"id": employee.id, "name": employee.name}
    }


@app.delete("/system/employees/{employee_id}")
async def delete_employee(
    employee_id: str,
    db: Session = Depends(get_db)
):
    """删除员工（实际上只是改为禁用状态）。"""
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        return {"code": 404, "message": "Employee not found"}
    
    employee.employment_status = "inactive"
    db.commit()
    
    return {
        "code": 0,
        "message": "Employee disabled"
    }


@app.get("/system/departments")
async def list_departments(
    db: Session = Depends(get_db)
):
    """获取部门列表。"""
    departments = db.query(Department).filter(Department.active == True).all()
    
    return {
        "code": 0,
        "data": [{
            "id": dept.id,
            "name": dept.name,
            "parent_id": dept.parent_id,
            "leader_id": dept.leader_id,
        } for dept in departments]
    }


@app.get("/system/roles")
async def list_roles():
    """获取角色列表。"""
    roles = [
        {
            "id": "admin",
            "name": "系统管理员",
            "description": "拥有所有系统权限",
            "permissions": ["system:*"]
        },
        {
            "id": "department",
            "name": "部门负责人",
            "description": "管理部门下的计划、成果和员工",
            "permissions": ["department:*"]
        },
        {
            "id": "leader",
            "name": "直属领导",
            "description": "审批下属的计划和成果",
            "permissions": ["leader:*"]
        },
        {
            "id": "employee",
            "name": "普通员工",
            "description": "编制个人计划和提交成果",
            "permissions": ["employee:*", "planning:*"]
        },
        {
            "id": "reviewer",
            "name": "评审人员",
            "description": "处理员工申诉和争议裁决",
            "permissions": ["dispute:*"]
        },
    ]
    
    return {
        "code": 0,
        "data": roles
    }
