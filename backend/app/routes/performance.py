"""绩效考核完整流程 API。

包括：
1. 发起考核任务 (HR)
2. 员工自评 (员工)
3. 主管评分 (主管)
4. 确认最终成绩 (HR)
5. 查询成绩 (权限检查)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Employee,
)
from ..services import config_service, review_service
from ..services.access_control import build_access_context
from ..services.period import normalize_period

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/performance", tags=["performance"], dependencies=[Depends(get_current_user)])


# -------------------------------------------------------
# 请求/响应模型
# -------------------------------------------------------


class CreatePerformanceTaskRequest(BaseModel):
    """发起考核任务请求。"""
    title: str = Field(..., min_length=1, max_length=200)
    period: str = Field(..., description="考核期间，格式 202611")
    target_employee_ids: list[str] = Field(..., min_items=1)
    start_date: str = Field(..., description="ISO 8601 格式")
    end_date: str = Field(..., description="ISO 8601 格式")
    self_review_deadline: str = Field(...)
    manager_review_deadline: str = Field(...)


class SubmitSelfReviewRequest(BaseModel):
    """员工提交自评请求。"""
    record_id: str
    performance_summary: str = Field(..., min_length=1, max_length=5000)
    achievements: list[str] = Field(default_factory=list)
    challenges: list[str] = Field(default_factory=list)


class SubmitManagerReviewRequest(BaseModel):
    """主管提交评分请求。"""
    record_id: str
    rating: str = Field(..., pattern="^[A-D]$")
    manager_comment: str = Field(..., min_length=1, max_length=5000)


class ConfirmPerformanceRequest(BaseModel):
    """HR 确认最终成绩请求。"""
    task_id: str


class PerformanceQueryRequest(BaseModel):
    """查询成绩请求。"""
    record_id: str


# -------------------------------------------------------
# 权限检查函数
# -------------------------------------------------------


def check_is_hr(db: Session, employee_id: str) -> bool:
    """检查是否为 HR：所属任一部门命中启用的 HR 部门配置。"""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        return False
    context = build_access_context(db, emp)
    return "hr" in context.roles


def check_is_manager_of(db: Session, manager_id: str, employee_id: str) -> bool:
    """检查 manager_id 是否是 employee_id 的直属主管。"""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        return False
    return emp.manager_id == manager_id


def check_can_view_employee(db: Session, viewer_id: str, target_employee_id: str) -> bool:
    """检查权限：viewer 是否可以查看 target_employee 的信息。

    规则：
    - HR 可以查看所有人
    - 主管可以查看直属下级
    - 员工只能查看自己
    """
    # HR 权限
    if check_is_hr(db, viewer_id):
        return True

    # 本人
    if viewer_id == target_employee_id:
        return True

    # 主管权限
    if check_is_manager_of(db, viewer_id, target_employee_id):
        return True

    return False


def check_can_submit_self_review(db: Session, employee_id: str, record_id: str) -> bool:
    """检查权限：只有该员工才能提交自己的自评。"""
    record = db.query(AssessmentRecord).filter(AssessmentRecord.id == record_id).first()
    if not record:
        return False
    return record.employee_id == employee_id


def check_can_submit_manager_review(db: Session, manager_id: str, record_id: str) -> bool:
    """检查权限：只有该员工的直属主管才能提交评分。"""
    record = db.query(AssessmentRecord).filter(AssessmentRecord.id == record_id).first()
    if not record:
        return False
    return check_is_manager_of(db, manager_id, record.employee_id)


# -------------------------------------------------------
# 1. 发起考核任务 (HR 专属)
# -------------------------------------------------------


@router.post("/tasks")
def create_performance_task(
    request: CreatePerformanceTaskRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """发起考核任务。

    仅 HR 可调用。生成 ConfigSnapshot + AssessmentTask + AssessmentRecord。

    Args:
        request.title: 考核标题
        request.period: 考核期间，如 "202611"
        request.target_employee_ids: 被评员工 ID 列表
        request.start_date / end_date: 考核周期（ISO 8601）
        request.self_review_deadline: 自评截止时间
        request.manager_review_deadline: 主管评分截止时间

    Returns:
        {
            "code": 0,
            "data": {
                "task_id": "...",
                "status": "created",
                "target_count": 10,
                "message": "..."
            }
        }
    """
    try:
        # 1. 权限检查：仅 HR
        #
        # ⚠️ BUG-R6-29（越权）：过去这里是
        #     ``current_user_id: str = Query(default="8d5c97gb")``
        # —— 当前用户来自**查询参数**，默认值还是硬编码的某个真实员工 ID。
        # 路由虽然挂了 ``Depends(get_current_user)``（token 有效），
        # 但**鉴权用的是 query 参数**，于是任何持有合法 token 的人
        # 只要加个 ``?current_user_id=<别人的ID>``，就能以别人的身份
        # 发起考核任务 / 提交主管评分 / 确认成绩。
        #
        # 现在一律用 token 解析出的 ``current_user``，query 参数**不再接受**。
        if not check_is_hr(db, current_user.id):
            raise HTTPException(
                status_code=403,
                detail="仅 HR 可发起考核任务"
            )

        # 2. 参数验证
        if not request.target_employee_ids:
            raise HTTPException(status_code=400, detail="被评员工列表不能为空")

        # 检查所有员工都存在
        employees = db.query(Employee).filter(
            Employee.id.in_(request.target_employee_ids)
        ).all()

        found_ids = {e.id for e in employees}
        missing_ids = set(request.target_employee_ids) - found_ids
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"以下员工不存在：{', '.join(missing_ids)}"
            )

        # 验证日期顺序
        from datetime import datetime as dt
        start_dt = dt.fromisoformat(request.start_date)
        end_dt = dt.fromisoformat(request.end_date)
        sr_deadline_dt = dt.fromisoformat(request.self_review_deadline)
        mgr_deadline_dt = dt.fromisoformat(request.manager_review_deadline)

        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")
        if sr_deadline_dt <= start_dt or sr_deadline_dt > mgr_deadline_dt:
            raise HTTPException(status_code=400, detail="自评/主管评分截止时间顺序错误")

        # 3. 创建 AssessmentTask
        #
        # ⚠️ BUG-R6-35（任务不幂等 → 同周期重复建任务）：
        # 过去只要重复调这个接口，就会**再建一个同 (周期, 部门, 标题) 的任务**，
        # 于是同一个员工在同一周期下有多条考核记录。
        # 真实库证据：``(李凯, 202611)`` 下有 **24 个任务 / 24 条记录**。
        # 后果是主管评分页（ManagerReview.vue）拉到的是**任意一条**旧记录，
        # 点开一条 11-13 的旧记录提交 → 409；真正该评的新记录永远排在后面。
        # 闭环在 UI 上走不通，就是这个原因。
        #
        # 现在按 (周期, 标题, 被评人集合) 做幂等保护：命中就直接复用，
        # 不再建重复任务。
        target_key = ",".join(sorted(request.target_employee_ids))
        existing_tasks = (
            db.query(AssessmentTask)
            .filter(
                AssessmentTask.period == request.period,
                AssessmentTask.name == request.title,
            )
            .all()
        )
        for existing in existing_tasks:
            existing_records = (
                db.query(AssessmentRecord)
                .filter(AssessmentRecord.task_id == existing.id)
                .all()
            )
            existing_key = ",".join(sorted(r.employee_id for r in existing_records))
            if existing_key == target_key:
                logger.info(
                    "考核任务已存在，复用: %s (period=%s)", existing.id, request.period
                )
                return {
                    "code": 0,
                    "data": {
                        "task_id": existing.id,
                        "status": "reused",
                        "target_count": len(existing_records),
                        "message": (
                            f"该周期已存在相同的考核任务（{existing.id}），"
                            "已复用，未重复创建"
                        ),
                    },
                }

        task_id = f"TASK_{dt.now().strftime('%Y%m%d%H%M%S')}_{request.period}"

        task = AssessmentTask(
            id=task_id,
            name=request.title,
            period=request.period,
            self_review_deadline=sr_deadline_dt,
            manager_review_deadline=mgr_deadline_dt,
            status="created",
            owner_id=current_user.id,
            created_at=dt.now(),
        )
        db.add(task)
        db.flush()

        # 3.5 冻结配置快照（决策③第二道保险）
        #
        # ⚠️ BUG-R6-27（快照缺失 → 永远算不出分）：
        # 过去这个接口**只建任务、不建快照**，于是
        # ``AssessmentTask.snapshot_id`` 恒为 NULL。
        # 而 ``review_service.compute_score`` 第一步就要求任务带快照：
        #     if task.snapshot_id is None: raise InvalidState("任务没有配置快照...")
        # 后果：**这条路径建出来的考核任务，100% 算不出分**，
        # 而且是在主管提交评分的那一刻才炸 —— 用户已经填完表了。
        #
        # 真实库证据：23 个 assessment_task 里 **22 个 snapshot_id 为 NULL**，
        # 对应的 21 条考核记录全部卡在 ``manager_review_submitted``，
        # ``score`` 表里一条都没有。
        #
        # 现在走 ``config_service.freeze_snapshot``（决策③的**唯一**冻结入口），
        # 与 ``main.py`` / ``assessment_config.py`` 两条路径**共用同一套逻辑**，
        # 顺带获得「配置读不到 → 沿用上次快照」的兜底。
        try:
            from ..domain.scheme_models import PerformanceScheme
            from ..services import scheme_service
            if db.query(PerformanceScheme.id).first() is not None:
                matched = [scheme_service.resolve(db, e, request.period) for e in employees]
                if len({v.id for v in matched}) != 1:
                    raise HTTPException(status_code=422, detail="被评员工适用不同方案，请从考核方案管理按版本分组发起")
                snapshot = scheme_service.freeze_version(db, task, matched[0], employees)
            else:
                snapshot = config_service.freeze_snapshot(
                    db,
                    task,
                    config_service.LocalProvider(db),
                )
        except config_service.ConfigValidationError as exc:
            # 配置有问题时**明确失败**，不要建出一个永远算不出分的任务。
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail=(
                    f"考核配置不可用，无法冻结快照：{exc}。"
                    "请先配置指标权重与等级区间（/v1/assessment/config/weight-sets）。"
                ),
            ) from exc
        task.snapshot_id = snapshot.id
        db.flush()

        # 4. 批量生成 AssessmentRecord
        records = []
        for emp_id in request.target_employee_ids:
            record = AssessmentRecord(
                id=f"REC_{task_id}_{emp_id}",
                task_id=task_id,
                employee_id=emp_id,
                status="not_started",
                created_at=dt.now(),
                updated_at=dt.now(),
            )
            records.append(record)

        db.add_all(records)
        db.flush()
        from ..services.notification_service import record_event

        for record in records:
            record_event(db, record, "N9")
        db.commit()

        logger.info(f"考核任务创建成功: {task_id}, 包含 {len(records)} 人")

        return {
            "code": 0,
            "data": {
                "task_id": task_id,
                "status": "created",
                "target_count": len(records),
                "message": f"成功创建考核任务，包含 {len(records)} 名被评员工"
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"创建考核任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# -------------------------------------------------------
# 2. 查询考核任务
# -------------------------------------------------------


@router.get("/tasks/{task_id}")
def get_performance_task(
    task_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询考核任务详情。"""
    task = db.query(AssessmentTask).filter(AssessmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="考核任务不存在")

    # 统计记录状态
    records = db.query(AssessmentRecord).filter(AssessmentRecord.task_id == task_id).all()

    status_count = {
        "not_started": sum(1 for r in records if r.status == "not_started"),
        "self_review_submitted": sum(1 for r in records if r.status == "self_review_submitted"),
        "manager_review_submitted": sum(1 for r in records if r.status == "manager_review_submitted"),
        "confirmed": sum(1 for r in records if r.status == "confirmed"),
    }

    return {
        "code": 0,
        "data": {
            "task_id": task.id,
            "name": task.name,
            "period": task.period,
            "status": task.status,
            "owner_id": task.owner_id,
            "created_at": task.created_at.isoformat(),
            "total_records": len(records),
            "status_breakdown": status_count,
        }
    }


@router.get("/tasks")
def list_performance_tasks(
    status: str | None = Query(None),
    period: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """分页查询考核任务列表。"""
    query = db.query(AssessmentTask)

    if status:
        query = query.filter(AssessmentTask.status == status)
    if period:
        # ⚠️ **BUG-R6-26**：周期过滤**格式无关** —— 入参可能是 `202611` 也可能是 `2026-11`，
        # 而 ``AssessmentTask.period`` 存的是权威的紧凑格式。这里归一化后再比。
        normalized = normalize_period(period)
        if normalized:
            query = query.filter(AssessmentTask.period == normalized)

    total = query.count()
    tasks = query.order_by(AssessmentTask.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "code": 0,
        "data": {
            "items": [
                {
                    "task_id": t.id,
                    "name": t.name,
                    "period": t.period,
                    "status": t.status,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tasks
            ],
            "total": total,
            "skip": skip,
            "limit": limit,
        }
    }


# -------------------------------------------------------
# 3. 员工自评
# -------------------------------------------------------


@router.post("/self-reviews")
def submit_self_review(
    request: SubmitSelfReviewRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """员工提交自评。

    Args:
        request.record_id: 考核记录 ID
        request.performance_summary: 业绩总结
        request.achievements: 主要成就列表
        request.challenges: 遇到的挑战列表

    Returns:
        {
            "code": 0,
            "data": {
                "record_id": "...",
                "status": "self_review_submitted",
                "submitted_at": "..."
            }
        }
    """
    try:
        # 1. 检查权限：只能提交自己的自评
        if not check_can_submit_self_review(db, current_user.id, request.record_id):
            raise HTTPException(
                status_code=403,
                detail="无权提交该自评"
            )

        # 2. 检查记录状态
        record = db.query(AssessmentRecord).filter(
            AssessmentRecord.id == request.record_id
        ).first()

        if not record:
            raise HTTPException(status_code=404, detail="考核记录不存在")

        if record.status != "not_started":
            raise HTTPException(
                status_code=409,
                detail=f"当前状态不允许提交自评: {record.status}"
            )

        # 3. 保存自评数据
        record.status = "self_review_submitted"
        record.self_review_content = {
            "performance_summary": request.performance_summary,
            "achievements": request.achievements,
            "challenges": request.challenges,
            "submitted_at": datetime.now().isoformat(),
        }
        record.updated_at = datetime.now()

        db.add(record)
        db.commit()

        from ..services.notification_service import record_event

        record_event(db, record, "N4")
        logger.info(f"自评提交成功: {request.record_id}")

        return {
            "code": 0,
            "data": {
                "record_id": record.id,
                "status": record.status,
                "submitted_at": record.updated_at.isoformat(),
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"提交自评失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


@router.get("/records/{record_id}/self-review")
def get_self_review(
    record_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询员工自评内容。

    权限检查：
    - 本人可查看
    - 主管可查看下属
    - HR 可查看所有
    """
    record = db.query(AssessmentRecord).filter(
        AssessmentRecord.id == record_id
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="考核记录不存在")

    # 权限检查
    if not check_can_view_employee(db, current_user.id, record.employee_id):
        raise HTTPException(status_code=403, detail="无权查看该自评")

    return {
        "code": 0,
        "data": {
            "record_id": record.id,
            "employee_id": record.employee_id,
            "status": record.status,
            "self_review": record.self_review_content,
            "submitted_at": record.updated_at.isoformat() if record.status == "self_review_submitted" else None,
        }
    }


# -------------------------------------------------------
# 4. 主管评分
# -------------------------------------------------------


@router.post("/manager-reviews")
def submit_manager_review(
    request: SubmitManagerReviewRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """主管提交评分。

    Args:
        request.record_id: 考核记录 ID
        request.rating: 等级 A/B/C/D
        request.manager_comment: 主管评语

    Returns:
        {
            "code": 0,
            "data": {
                "record_id": "...",
                "status": "manager_review_submitted",
                "rating": "A"
            }
        }
    """
    try:
        # 1. 检查权限：主管可以评分
        #
        # ⚠️ BUG-R6-29（越权）：过去这里是
        #     ``current_user_id: str = Query(default="8d5c97gb")``
        # —— 当前用户来自**查询参数**，默认值硬编码。
        # 任何持有合法 token 的人加个 ``?current_user_id=<主管ID>``
        # 就能替别人给下属打分。现在一律用 token 里的身份。
        if not check_can_submit_manager_review(db, current_user.id, request.record_id):
            raise HTTPException(
                status_code=403,
                detail="无权提交该员工的评分"
            )

        # 2. 检查记录
        record = db.query(AssessmentRecord).filter(
            AssessmentRecord.id == request.record_id
        ).first()

        if not record:
            raise HTTPException(status_code=404, detail="考核记录不存在")

        from ..domain.models import ConfigSnapshot
        scheme_task = db.get(AssessmentTask, record.task_id)
        scheme_snapshot = db.get(ConfigSnapshot, scheme_task.snapshot_id) if scheme_task and scheme_task.snapshot_id else None
        if scheme_snapshot and (scheme_snapshot.form_template_json or {}).get('scheme_version_id'):
            raise HTTPException(status_code=409, detail='方案考核请使用按方案填报与评价，不能通过旧评分入口覆盖')

        if record.status not in ("not_started", "self_review_submitted"):
            raise HTTPException(
                status_code=409,
                detail=f"当前状态不允许提交评分: {record.status}"
            )

        # 3. 验证等级
        if request.rating not in ("A", "B", "C", "D"):
            raise HTTPException(status_code=400, detail="等级必须是 A/B/C/D")

        from ..domain.review_models import ScoreHead
        head = db.get(ScoreHead, record.id)
        if head and head.protected:
            raise HTTPException(409, "该结果已进入复核保护，请通过复核中心调整")
        # 4. 保存评分
        record.status = "manager_review_submitted"
        record.confirmed_rating = request.rating
        record.manager_comment = request.manager_comment
        record.updated_at = datetime.now()

        db.add(record)
        db.flush()

        # 4.5 触发规则引擎算分并落库
        #
        # ⚠️ BUG-R6-28（提交评分却不算分）：
        # 过去这里 `db.commit()` 就结束了 —— **从不调用**
        # ``review_service.compute_score`` / ``persist_score``。
        # 后果：主管评完分、记录状态也推进了，但 ``score`` 表**一行都没有**。
        # 员工在"我的分数"页看到的是空数据，而且**没有任何报错**——
        # 最坏的一种失败：看起来成功了，其实什么都没发生。
        #
        # 真实库证据：21 条记录卡在 ``manager_review_submitted``、
        # ``confirmed_rating='A'``，而 ``score`` 表只有 1 行
        # （那条是脚本直接插库造出来的，不是产品链路）。
        #
        # 现在与 ``main.py`` 的 ``/v1/assessment/records/{id}/manager-review``
        # 保持一致：评分落库**必须**伴随一次算分。
        #
        # ⚠️ 算分失败（配置缺失 / 快照缺失）**不回滚评分**：
        # 主管的工作已经做完了，不该因为配置问题让主管白填一遍。
        # 改为：记录已提交 + 返回算分失败原因，由主管/HR 事后重算
        # （``POST /api/records/{record_id}/score/recompute``）。
        score_info: dict[str, Any]
        try:
            result = review_service.compute_score(db, record_id=record.id)
            persisted = review_service.persist_score(db, record_id=record.id, result=result)
            score_info = {
                "total": persisted.total,
                "rating": persisted.rating,
                "scored": True,
            }
        except Exception as exc:  # noqa: BLE001 —— 算分失败不能吃掉主管的评分
            logger.error(
                "主管评分已提交，但规则引擎算分失败 record=%s: %s",
                record.id,
                exc,
                exc_info=True,
            )
            score_info = {
                "scored": False,
                "scoring_error": str(exc),
            }

        db.commit()

        logger.info(
            "主管评分提交成功: %s, 等级: %s, 算分: %s",
            request.record_id,
            request.rating,
            score_info.get("scored"),
        )

        return {
            "code": 0,
            "data": {
                "record_id": record.id,
                "status": record.status,
                "rating": record.confirmed_rating,
                "submitted_at": record.updated_at.isoformat(),
                "score": score_info,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"提交评分失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# -------------------------------------------------------
# 5. 确认最终成绩 (HR)
# -------------------------------------------------------


@router.post("/tasks/{task_id}/confirm")
def confirm_performance_task(
    task_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """HR 确认最终成绩，任务进入 confirmed 状态。

    Args:
        task_id: 任务 ID

    Returns:
        {
            "code": 0,
            "data": {
                "task_id": "...",
                "status": "confirmed",
                "confirmed_records": 10,
                "message": "..."
            }
        }
    """
    try:
        # 1. 权限检查
        if not check_is_hr(db, current_user.id):
            raise HTTPException(status_code=403, detail="仅 HR 可确认成绩")

        # 2. 检查任务
        task = db.query(AssessmentTask).filter(AssessmentTask.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="考核任务不存在")

        from ..domain.models import ConfigSnapshot
        snapshot = db.get(ConfigSnapshot, task.snapshot_id) if task.snapshot_id else None
        if snapshot and (snapshot.form_template_json or {}).get('scheme_version_id'):
            raise HTTPException(status_code=409, detail='方案考核请在按方案填报与评价页面逐项完成面谈、员工确认及HR定稿')

        # 3. 检查所有记录是否已评分
        records = db.query(AssessmentRecord).filter(
            AssessmentRecord.task_id == task_id
        ).all()

        unfinished = [r for r in records if r.status != "manager_review_submitted"]
        if unfinished:
            raise HTTPException(
                status_code=409,
                detail=f"还有 {len(unfinished)} 条记录未完成评分"
            )

        # 4. 更新所有记录为 confirmed
        for record in records:
            record.status = "confirmed"
            record.updated_at = datetime.now()

        # 5. 更新任务状态
        task.status = "confirmed"

        db.add_all(records)
        db.add(task)
        db.flush()
        from ..services.notification_service import record_event

        for record in records:
            record_event(db, record, "N8")
        db.commit()

        logger.info(f"考核任务确认完成: {task_id}, {len(records)} 条记录")

        return {
            "code": 0,
            "data": {
                "task_id": task.id,
                "status": task.status,
                "confirmed_records": len(records),
                "message": f"成功确认 {len(records)} 名员工的考核成绩"
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"确认成绩失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")


# -------------------------------------------------------
# 6. 查询成绩（权限检查）
# -------------------------------------------------------


@router.get("/records/{record_id}")
def get_performance_record(
    record_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询考核记录详情。

    权限检查：
    - 本人可查看
    - 主管可查看下属
    - HR 可查看所有
    """
    record = db.query(AssessmentRecord).filter(
        AssessmentRecord.id == record_id
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="考核记录不存在")

    # 权限检查
    if not check_can_view_employee(db, current_user.id, record.employee_id):
        raise HTTPException(status_code=403, detail="无权查看该考核记录")

    emp = db.query(Employee).filter(Employee.id == record.employee_id).first()

    return {
        "code": 0,
        "data": {
            "record_id": record.id,
            "task_id": record.task_id,
            "employee": {
                "id": emp.id,
                "name": emp.name,
                "department_id": emp.department_id,
                "position": emp.position,
            } if emp else None,
            "status": record.status,
            "rating": record.confirmed_rating,
            "manager_comment": record.manager_comment,
            "self_review": record.self_review_content if record.status in (
                "self_review_submitted",
                "manager_review_submitted",
                "confirmed"
            ) else None,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }
    }


@router.get("/tasks/{task_id}/records")
def list_task_records(
    task_id: str,
    status: str | None = Query(None),
    rating: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询任务的所有记录。

    权限检查：
    - HR 可查看所有
    - 主管只能查看自己部门的员工
    """
    task = db.query(AssessmentTask).filter(AssessmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="考核任务不存在")

    # 基础查询
    query = db.query(AssessmentRecord).filter(AssessmentRecord.task_id == task_id)

    if status:
        query = query.filter(AssessmentRecord.status == status)
    if rating:
        query = query.filter(AssessmentRecord.confirmed_rating == rating)

    records = query.all()

    # HR 可以查看所有，主管只能查看下属
    result_records = []
    for r in records:
        if check_can_view_employee(db, current_user.id, r.employee_id):
            result_records.append(r)

    result_data = []
    for r in result_records:
        emp = db.query(Employee).filter(Employee.id == r.employee_id).first()
        result_data.append({
            "record_id": r.id,
            "employee": {
                "id": emp.id,
                "name": emp.name,
            } if emp else None,
            "status": r.status,
            "rating": r.confirmed_rating,
            "updated_at": r.updated_at.isoformat(),
        })

    return {
        "code": 0,
        "data": {
            "task_id": task.id,
            "records": result_data,
            "total": len(result_data),
        }
    }


# -------------------------------------------------------
# 7. 员工查询个人成绩
# -------------------------------------------------------


@router.get("/my-performance")
def get_my_performance(
    task_id: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """员工查询自己的成绩。"""
    query = db.query(AssessmentRecord).filter(
        AssessmentRecord.employee_id == current_user.id
    )

    if task_id:
        query = query.filter(AssessmentRecord.task_id == task_id)

    records = query.order_by(AssessmentRecord.updated_at.desc()).all()

    return {
        "code": 0,
        "data": {
            "records": [
                {
                    "record_id": r.id,
                    "task_id": r.task_id,
                    "status": r.status,
                    "rating": r.confirmed_rating,
                    "manager_comment": r.manager_comment,
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in records
            ],
            "total": len(records),
        }
    }
