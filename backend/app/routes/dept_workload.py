"""★ **部门工作量** 路由（2026-11-25 新增）。

⚠️ **为什么要有这个独立路由（而不是直接复用 ``/v1/baidu/workload``）**
----------------------------------------------------------------------
``/v1/baidu/workload`` 里有**两类**端点：

1. **网盘相关**（``/files`` ``/bindings`` ``/collect``）
   —— 只有短视频用得上（百度网盘自动采集成片条数）。
2. **人工填报相关**（``/manual-metrics``）
   —— **任何部门**都能用：填指标、查指标。

⇒ 通用板块只需要第 2 类 ⇒ **单独开一个 router**，
   好处是**板块守卫天然分开**：
   短视频走 ``pan_workload``，其它部门走 ``dept_workload``，
   两边**互不影响**，也不会出现「仓储部能调网盘接口」。

⚠️ **路由前缀用业务名而不是实现名**
   ``/v1/dept/workload`` —— 不叫 ``/v1/baidu/...``，
   因为这里**压根没有网盘**。

⚠️ **板块守卫与短视频那一套完全一致**
   ⇒ 「别的部门看到这个板块」的问题**结构上**不会出现。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..domain.pan_workload_models import PanManualMetric
from ..services.department_modules import (
    DEPT_WORKLOAD_MODULE,
    employee_department_ids,
    has_module,
    module_guard_message,
)
from ..services.pan_manual_metric_service import (
    ALLOWED_METRIC_CODES,
    PanManualMetricError,
    PanManualMetricService,
)
from ..services.pan_workload_policy import (
    reporter_metric_codes,
    reviewer_metric_codes,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# 板块守卫
# ─────────────────────────────────────────────────────────────────────────


def _require_dept_module(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> Employee:
    """★ **部门板块守卫** —— 整个 ``/v1/dept/workload`` 的总闸门。

    ⚠️ **必须挂在 router 上**（不是逐个端点）——
    逐个加守卫 ⇒ 以后有人加端点忘了加 ⇒ 泄露。
    ⇒ 总闸门在 router ⇒ 新端点自动受保护。

    ⚠️ **判定口径与剪辑板块完全一致**
    ⇒ 「别的部门看到不属于自己的板块」这个问题**结构上**不会重现。
    """
    if not has_module(db, user, DEPT_WORKLOAD_MODULE):
        raise HTTPException(
            status_code=403,
            detail=module_guard_message(DEPT_WORKLOAD_MODULE),
        )
    return user


router = APIRouter(
    prefix="/v1/dept/workload",
    tags=["dept-workload"],
    dependencies=[Depends(_require_dept_module)],
)


# ─────────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────────


def _scope_employee_ids(db: Session, user: Employee) -> set[str] | None:
    """这个用户**能看谁的数据**。

    ⚠️ **返回 ``None`` = 不限**（HR / 管理员）。

    ⚠️ **口径与剪辑板块一致**
    --------------------------------------------------
    - 员工 ⇒ 只看自己
    - 主管 ⇒ 看**直属下属**（汇报线）
    - 部门负责人 ⇒ 看**管辖树**
    - HR / 管理员 ⇒ 不限
    """
    from ..services.hr_admin_roles import is_hr, is_system_admin
    from ..services.pan_workload_scope import scope_employee_ids

    if is_hr(db, user) or is_system_admin(db, user):
        return None
    return scope_employee_ids(db, user)


def _serialize_manual_metric(row: PanManualMetric) -> dict[str, Any]:
    """序列化一条人工填报指标。"""
    return {
        "id": row.id,
        "employeeId": row.employee_id,
        "metricDate": row.metric_date.isoformat(),
        "metricCode": row.metric_code,
        "metricValue": row.metric_value,
        "scope": row.scope,
        "source": row.source,
        "remark": row.remark or "",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }


# ─────────────────────────────────────────────────────────────────────────
# 端点 —— 查询
# ─────────────────────────────────────────────────────────────────────────


@router.get("/my-metrics")
def list_my_metrics(
    start: str = "",
    end: str = "",
    metric_code: str = "",
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """**我填过的指标**（按范围收敛）。"""
    allowed = _scope_employee_ids(db, user)
    stmt = select(PanManualMetric)
    if allowed is not None:
        stmt = stmt.where(PanManualMetric.employee_id.in_(allowed))
    if metric_code:
        stmt = stmt.where(PanManualMetric.metric_code == metric_code)
    if start:
        stmt = stmt.where(PanManualMetric.metric_date >= date.fromisoformat(start))
    if end:
        stmt = stmt.where(PanManualMetric.metric_date <= date.fromisoformat(end))
    rows = list(db.scalars(stmt.order_by(PanManualMetric.metric_date.desc())))
    return {"items": [_serialize_manual_metric(r) for r in rows]}


@router.get("/my-scope")
def get_my_scope(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """我的填报口径 —— 前端据此渲染**该填哪些指标**。

    ⚠️ **前端不定义指标** —— 指标口径在库里
    ⇒ 前端只渲染后端下发的「你要填哪几个」。
    ⚠️ **指标的标签与量纲也由后端下发**
    ⇒ 业务定了新指标后**前端不用改**。

    ⚠️⚠️ **角色必须取「岗位口径」而不是「指标码」**（2026-11-25）
    ------------------------------------------------------
    之前这里用 ``"reporter" if reporter_codes else ...`` 推断角色
    ⇒ 而 ``reporter_metric_codes`` 在**指标还没配**时返回**空**
    ⇒ 分拣员明明在口径表里是 ``reporter``，接口却返回 ``role: "none"``
    ⇒ **前端会显示「暂未纳入该板块」** ⇒ 用户以为自己没权限。

    ⇒ 角色以 ``resolve_policy(...).role`` 为准（那是**权威**），
      指标码只是「要填哪几个」。
    """
    from ..services.daily_metric_specs import specs_for_codes
    from ..services.pan_workload_policy import resolve_policy

    # ⚠️ **角色：取岗位口径**（权威口径，不是「有没有指标」）
    role = "none"
    for dept_id in employee_department_ids(db, user):
        policy = resolve_policy(
            db, dept_id, str(user.position or ""), DEPT_WORKLOAD_MODULE
        )
        if policy.role in ("reporter", "reviewer"):
            role = policy.role
            break

    reporter = reporter_metric_codes(db, user, DEPT_WORKLOAD_MODULE)
    reviewer = reviewer_metric_codes(db, user, DEPT_WORKLOAD_MODULE)
    all_codes = tuple(reporter) + tuple(reviewer)
    specs = specs_for_codes(all_codes)
    return {
        "role": role,
        "reporterMetricCodes": list(reporter),
        "reviewerMetricCodes": list(reviewer),
        "metrics": [
            {
                "code": spec.code,
                "label": spec.label,
                "kind": spec.kind,
                "placeholder": spec.placeholder,
                "required": spec.required,
            }
            for spec in specs
        ],
    }


# ─────────────────────────────────────────────────────────────────────────
# 端点 —— 写操作
# ─────────────────────────────────────────────────────────────────────────


@router.post("/manual-metrics")
def submit_manual_metric(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """填报指标（**只能填自己的**）。

    ⚠️ **三重校验（顺序不能反）**
    --------------------------------------------------
    ① 指标**存不存在**（400）—— 参数错误，不是越权
    ② **能不能填**（403）—— 岗位没配填报项
    ③ 填的是**不是我的指标**（403）—— 别人的指标不许填
    """
    metric_code = str(payload.get("metric_code") or "").strip()
    metric_date = str(payload.get("metric_date") or "").strip()
    raw_value = payload.get("metric_value")

    if not metric_code or not metric_date or raw_value is None:
        raise HTTPException(
            status_code=400,
            detail="metric_code / metric_date / metric_value 必填",
        )
    if metric_code not in ALLOWED_METRIC_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的指标：{metric_code}",
        )

    reporter = reporter_metric_codes(db, user, DEPT_WORKLOAD_MODULE)
    reviewer = reviewer_metric_codes(db, user, DEPT_WORKLOAD_MODULE)
    allowed_codes = reporter or reviewer
    if not allowed_codes:
        # ⚠️ **区分两种「不能填」**（2026-11-25）
        #   ① 岗位**不参与**这个板块 ⇒ 403「不参与」
        #   ② 岗位**参与**但**指标还没定** ⇒ 403「指标待定」
        # ⇒ 混成一句话 ⇒ 用户以为是权限问题，去找管理员，其实业务还没定指标。
        from ..services.pan_workload_policy import resolve_policy

        role = "none"
        for dept_id in employee_department_ids(db, user):
            policy = resolve_policy(
                db, dept_id, str(user.position or ""), DEPT_WORKLOAD_MODULE
            )
            if policy.role in ("reporter", "reviewer"):
                role = policy.role
                break

        if role == "none":
            raise HTTPException(
                status_code=403,
                detail="你的岗位在这块业务里没有填报项（不参与该板块的填报）",
            )
        raise HTTPException(
            status_code=403,
            detail="该板块的填报指标尚未配置（等业务确定「填什么指标」后即可填报）",
        )
    if metric_code not in allowed_codes:
        raise HTTPException(
            status_code=403,
            detail=(
                f"指标 {metric_code} 不在你的填报口径内"
                f"（你只能填：{', '.join(allowed_codes)}）"
            ),
        )

    try:
        metric_value = float(raw_value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="metric_value 必须是数字") from None

    svc = PanManualMetricService(db)
    try:
        row = svc.submit(
            employee_id=user.id,
            metric_date=date.fromisoformat(metric_date),
            metric_code=metric_code,
            metric_value=metric_value,
            remark=str(payload.get("remark") or ""),
            source="manual",
            created_by=user.id,
        )
    except PanManualMetricError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    return {"id": row.id, "metricCode": row.metric_code, "metricValue": row.metric_value}
