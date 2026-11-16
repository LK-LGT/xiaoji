"""员工侧周计划 API 路由。

对应前端 `src/api/weekPlan.ts` 与 `src/api/weekResult.ts` 的
`/employee/week-plans/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/employee/week-plans` 前缀。

分页约定：前端 WeekPlanSummary[] 直接 .map()，故列表端点返回裸数组。

实现说明：
- 领域逻辑全部在 `services/planning.py`（含状态机、乐观锁、序列化）。
  本模块只做「HTTP ↔ 服务层」的转接。
- 状态机不在路由里散落 `plan.status = ...`，一律走
  `planning.submit_week_plan / withdraw_week_plan` 等服务函数。
- 数据范围：以当前登录用户的 `owner_id` 为边界，跨人访问直接 404。
- `versionNo` 查询参数是乐观锁版本号：不匹配时返回 409。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..domain.result_models import RESULT_PLAN_TYPE_WEEK
from ..services import feishu_approval_sync, planning, result_submission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/employee/week-plans", tags=["employee-week-plan"])


# ---------------------------------------------------------------------------
# 请求模型
#
# 注意：必须用显式 Pydantic 模型，不能用 `payload: dict`。
# 本项目所用 FastAPI 版本不会把裸 dict 注解自动解析为 JSON body，
# 请求体会以字符串形式传入并触发 422（dict_type 校验失败）。
# ---------------------------------------------------------------------------


class WeekPlanSavePayload(BaseModel):
    """周计划保存请求体，对应前端 WeekPlanSavePayload。"""

    model_config = {"extra": "allow"}

    weekStart: str = Field(default="")
    versionNo: int | None = None
    items: list[dict] = Field(default_factory=list)
    recordVersion: bool = False
    versionRequestId: str = ""
    restoredFromVersionId: int | None = None


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


def _translate(exc: Exception) -> Exception:
    """把领域异常映射成 HTTP 状态码。"""
    from fastapi import HTTPException

    if isinstance(exc, planning.PlanNotFound):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, planning.PlanVersionConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, planning.PlanStateError):
        return HTTPException(status_code=409, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="周计划操作失败")


def _run(fn, *args, **kwargs):
    """执行服务层调用，把领域异常转成 HTTPException。"""
    try:
        return fn(*args, **kwargs)
    except (
        planning.PlanNotFound,
        planning.PlanStateError,
        planning.PlanVersionConflict,
        ValueError,
    ) as exc:
        raise _translate(exc) from exc


def _run_result(fn, *args, **kwargs):
    """执行成果服务层调用，把成果领域异常转成 HTTPException。"""
    from fastapi import HTTPException

    try:
        return fn(*args, **kwargs)
    except result_submission.ResultNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except result_submission.ResultForbidden as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except result_submission.ResultConflict as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except result_submission.ResultError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


async def _read_evidence(file: UploadFile | None):
    """把 `UploadFile` 读成成果服务层的附件输入对象。"""
    if file is None:
        return None
    raw = await file.read()
    return result_submission.ResultEvidenceInput(
        file_name=file.filename or "evidence",
        content_type=file.content_type or "",
        data=raw,
    )


# ---------------------------------------------------------------------------
# 周计划本体
#
# 响应结构：WeekPlanSummary[] / WeekPlanDetail / WeekPlanParentOption[]
# ---------------------------------------------------------------------------


@router.get("")
async def list_employee_week_plans(
    status: str | None = Query(None),
    weekStart: str | None = Query(None),
    deptId: str | None = Query(None),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取我的周计划列表。

    响应结构：WeekPlanSummary[]（裸数组）
    """
    plans = _run(planning.list_week_plans, db, current_user.id, status, weekStart, deptId)
    return _ok(
        [
            planning.serialize_week_plan_summary(
                plan, len(_run(planning.list_week_plan_items, db, plan.id))
            )
            for plan in plans
        ]
    )


@router.get("/parent-options")
async def list_week_plan_parent_options(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周计划可选的父级月计划任务。

    响应结构：WeekPlanParentOption[]（裸数组）
    """
    return _ok(_run(planning.list_week_plan_parent_options, db, current_user.id))


@router.get("/{plan_id}")
async def get_employee_week_plan(
    plan_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周计划详情。

    响应结构：WeekPlanDetail
    """
    plan = _run(planning.get_week_plan, db, current_user.id, plan_id)
    items = _run(planning.list_week_plan_items, db, plan.id)
    siblings = _run(planning.sibling_week_plans, db, current_user.id, plan)
    day_count = _run(planning.count_day_plans_for_week, db, current_user.id, plan)
    return _ok(planning.serialize_week_plan_detail(plan, items, siblings, day_count))


@router.post("")
async def create_employee_week_plan(
    payload: WeekPlanSavePayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """创建周计划。

    响应结构：WeekPlanDetail
    """
    plan = _run(planning.upsert_week_plan, db, current_user.id, None, payload.model_dump())
    items = _run(planning.list_week_plan_items, db, plan.id)
    siblings = _run(planning.sibling_week_plans, db, current_user.id, plan)
    day_count = _run(planning.count_day_plans_for_week, db, current_user.id, plan)
    return _ok(planning.serialize_week_plan_detail(plan, items, siblings, day_count))


@router.put("/{plan_id}")
async def update_employee_week_plan(
    plan_id: str,
    payload: WeekPlanSavePayload,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """更新周计划。

    响应结构：WeekPlanDetail
    """
    plan = _run(planning.upsert_week_plan, db, current_user.id, plan_id, payload.model_dump())
    items = _run(planning.list_week_plan_items, db, plan.id)
    siblings = _run(planning.sibling_week_plans, db, current_user.id, plan)
    day_count = _run(planning.count_day_plans_for_week, db, current_user.id, plan)
    return _ok(planning.serialize_week_plan_detail(plan, items, siblings, day_count))


@router.delete("/{plan_id}")
async def delete_employee_week_plan(
    plan_id: str,
    versionNo: int | None = Query(None, description="乐观锁版本号"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """删除周计划。

    响应结构：WeekPlanAction
    """
    plan = _run(planning.get_week_plan, db, current_user.id, plan_id)
    if versionNo is not None:
        _run(planning.assert_version, plan, versionNo)
    _run(planning.delete_week_plan, db, current_user.id, plan_id)
    return _ok(
        {
            "id": plan.id,
            "status": "DRAFT",
            "versionNo": versionNo if versionNo is not None else 1,
            "message": "周计划已删除",
        }
    )


@router.post("/{plan_id}/submit")
async def submit_employee_week_plan(
    plan_id: str,
    versionNo: int | None = Query(None, description="乐观锁版本号"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交周计划审批。

    响应结构：WeekPlanAction
    """
    plan = _run(planning.get_week_plan, db, current_user.id, plan_id)
    if versionNo is not None:
        _run(planning.assert_version, plan, versionNo)
    plan = _run(planning.submit_week_plan, db, current_user.id, plan_id)
    # 飞书 IM 通知审批人（旁路动作，失败不影响提交）
    feishu_approval_sync.notify_plan_submitted_safe(
        db,
        owner_id=current_user.id,
        plan_type="week",
        plan_id=plan.id,
        plan_title=plan.title or f"{plan.week_start} 周计划",
    )
    return _ok(
        {
            "id": plan.id,
            "status": planning.to_week_status(plan.status),
            "versionNo": plan.version_no,
            "message": "周计划已提交",
        }
    )


@router.post("/{plan_id}/withdraw")
async def withdraw_employee_week_plan(
    plan_id: str,
    versionNo: int | None = Query(None, description="乐观锁版本号"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """撤回周计划。

    响应结构：WeekPlanAction
    """
    plan = _run(planning.get_week_plan, db, current_user.id, plan_id)
    if versionNo is not None:
        _run(planning.assert_version, plan, versionNo)
    plan = _run(planning.withdraw_week_plan, db, current_user.id, plan_id)
    return _ok(
        {
            "id": plan.id,
            "status": planning.to_week_status(plan.status),
            "versionNo": plan.version_no,
            "message": "周计划已撤回",
        }
    )


# ---------------------------------------------------------------------------
# 周成果提交
#
# 响应结构：WeekResultSubmitContext / WeekResultSubmitAction
# ---------------------------------------------------------------------------


@router.get("/{plan_id}/items/{item_id}/results/submit-context")
async def get_week_result_submit_context(
    plan_id: str,
    item_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取周成果提交上下文。

    响应结构：WeekResultSubmitContext
    """
    plan = _run(planning.get_week_plan, db, current_user.id, plan_id)
    items = _run(planning.list_week_plan_items, db, plan.id)
    target = next((i for i in items if i.id == item_id), None)
    if target is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="周计划条目不存在")

    latest = result_submission.latest_result_for_item(
        db, current_user.id, RESULT_PLAN_TYPE_WEEK, plan.id, target.id
    )
    return _ok(
        {
            "weekPlanId": result_submission._as_int(plan.id),
            "weekPlanTitle": plan.title,
            "weekStart": planning._date_str(plan.week_start),
            "weekEnd": planning._date_str(plan.week_end),
            "weekPlanStatus": planning.to_week_status(plan.status),
            "task": planning.serialize_week_plan_item(target),
            "latestResult": (
                result_submission.serialize_week_result(db, latest) if latest else None
            ),
            "acceptedFileTypes": list(result_submission.ACCEPTED_FILE_TYPES),
            "maxFileSizeMb": result_submission.MAX_FILE_SIZE_MB,
        }
    )


@router.post("/{plan_id}/items/{item_id}/results/submit")
async def submit_week_result(
    plan_id: str,
    item_id: str,
    completionRate: float | None = Form(None),
    completionStatus: str | None = Form(None),
    deliverable: str | None = Form(None),
    remark: str | None = Form(None),
    content: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交周成果。

    响应结构：WeekResultSubmitAction
    """
    evidence = await _read_evidence(file)
    result = _run_result(
        result_submission.submit_week_result,
        db,
        current_user.id,
        week_plan_id=plan_id,
        week_plan_item_id=item_id,
        completion_rate=completionRate or 0.0,
        completion_status=completionStatus or "",
        deliverable=deliverable or "",
        remark=remark or "",
        content=content or "",
        evidence=evidence,
    )
    return _ok(
        {
            "id": result_submission._as_int(result.id),
            "status": result.status,
            "submittedAt": result_submission._iso(result.submitted_at) or "",
            "message": "周成果已提交",
        }
    )
