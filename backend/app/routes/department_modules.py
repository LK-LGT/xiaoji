"""★ **部门功能板块**的管理 API（2026-11-21）。

⚠️ **为什么单独一个路由文件**
板块隔离是**跨模块**的通用机制（今天只有「剪辑工作量」，
将来可能有「直播数据」「仓储盘点」…）⇒ 不应该塞进某个业务模块的路由里。

⚠️ **为什么这个路由不在板块守卫之下**
它是**管理板块本身**的入口 —— 如果它也被守卫挡住，
就没人能给新部门开通板块了（死锁）。
⇒ 所以它**自己**校验「必须是 HR / 管理员」。

⚠️ **前端路由在 ``/system`` 工作区**
⇒ 只有系统管理端的人能看到入口 ⇒ 与「HR / 管理员才能改」的语义一致。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Department, Employee
from ..domain.pan_workload_models import DepartmentModule, PanWorkloadPolicy
from ..services.department_modules import KNOWN_MODULES, has_department_module_manage
from ..services.pan_workload_policy import list_policies as list_policies_svc
from ..services.pan_workload_policy import policy_map

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/department-modules", tags=["department-modules"])


# ─────────────────────────────────────────────────────────────────────────
# 请求模型
# ─────────────────────────────────────────────────────────────────────────


class ModuleGrantRequest(BaseModel):
    """给某个部门开通一个功能板块。"""

    department_id: str = Field(min_length=1, max_length=64)
    module_code: str = Field(min_length=1, max_length=64)
    active: bool = True
    note: str = Field(default="", max_length=255)


# ─────────────────────────────────────────────────────────────────────────
# 权限
# ─────────────────────────────────────────────────────────────────────────


def _require_admin(db: Session, user: Employee) -> None:
    """要求「部门功能板块管理」权限。

    ⚠️ **不能用「板块判定」来守这个端点**
    —— 它就是**管理板块**的入口 ⇒ 守卫它会造成死锁
    （没人能开通 ⇒ 板块永远开不出来）。

    ⚠️ **为什么用权限串而不是只看 ``is_admin_or_hr``**
    前端菜单显示的是**权限串** ``system:department-module:manage``
    ⇒ 后端必须用**同一个口径**判定，
    否则会出现「菜单看不见但接口能调」或「菜单能看见但接口 403」。

    ⇒ 两个判定**取或**：
    ① 权限串命中（前端菜单的同一口径）
    ② HR / 系统管理员（兜底 —— 权限串解析出问题时仍能开通，不至于死锁）
    """
    from ..services.department_modules import is_admin_or_hr

    if has_department_module_manage(db, user):
        return
    if is_admin_or_hr(db, user):
        return
    raise HTTPException(
        status_code=403,
        detail="仅HR与系统管理员可维护部门功能板块",
    )


def _serialize_grant(row: DepartmentModule, dept_name: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "departmentId": row.department_id,
        "departmentName": dept_name,
        "moduleCode": row.module_code,
        "active": row.active,
        "note": row.note,
    }


# ─────────────────────────────────────────────────────────────────────────
# 端点
# ─────────────────────────────────────────────────────────────────────────


@router.get("/known")
def list_known_modules(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """所有已知板块（供配置界面下拉用）。"""
    _require_admin(db, user)
    return {
        "items": [{"code": code, "label": label} for code, label in KNOWN_MODULES],
    }


@router.get("")
def list_grants(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """已开通的板块列表（含部门名，便于界面展示）。"""
    _require_admin(db, user)
    rows = list(
        db.scalars(select(DepartmentModule).order_by(DepartmentModule.module_code))
    )
    dept_names = {
        str(d.id): d.name
        for d in db.scalars(select(Department)).all()
    }
    return {
        "items": [
            _serialize_grant(row, dept_names.get(str(row.department_id), ""))
            for row in rows
        ],
    }


@router.post("")
def grant_module(
    payload: ModuleGrantRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """给部门开通（或停用）一个功能板块。

    ⚠️ **幂等** —— 同一部门同一板块重复开通只是更新，不会报错。
    ⚠️ **不删行** —— 停用置 ``active=False``，保留配置痕迹。
    """
    _require_admin(db, user)

    dept = db.get(Department, payload.department_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="部门不存在")

    known_codes = {code for code, _ in KNOWN_MODULES}
    if payload.module_code not in known_codes:
        raise HTTPException(
            status_code=400,
            detail=f"未知板块 {payload.module_code}（可用：{sorted(known_codes)}）",
        )

    row = db.scalar(
        select(DepartmentModule).where(
            DepartmentModule.department_id == payload.department_id,
            DepartmentModule.module_code == payload.module_code,
        )
    )
    if row is None:
        row = DepartmentModule(
            department_id=payload.department_id,
            module_code=payload.module_code,
        )
        db.add(row)
    row.active = payload.active
    row.note = payload.note
    db.commit()
    db.refresh(row)
    return {"item": _serialize_grant(row, dept.name)}


# ─────────────────────────────────────────────────────────────────────────
# ★ **部门 × 岗位 口径**（2026-11-23）
# ─────────────────────────────────────────────────────────────────────────
#
# ⚠️ **为什么板块开通了还要再配岗位口径**
# ----------------------------------------
# ``department_module`` 只回答「**这个部门有没有剪辑这个业务**」，
# 但**部门内部还有不同岗位**，不同岗位的考核方式**完全不一样**：
#
#     短视频部（实测库）
#         剪辑 → 要填（reporter）
#         编导 → 只审（reviewer）
#         运营 → 只审（reviewer）
#         主播 → 不参与（none，配置行都没有）
#
# ⇒ 板块开通 ≠ 全部门都要收推送。**这两件事必须分开配。**


class PolicyRequest(BaseModel):
    """配置某个部门某个岗位的考核口径。"""

    department_id: str = Field(min_length=1, max_length=64)
    #: 岗位名（与 ``employee.position`` 对齐）
    position: str = Field(min_length=1, max_length=128)
    #: ``reporter``（要填）/ ``reviewer``（只审）/ ``none``（不参与）
    role: str = Field(default="none", max_length=16)
    #: 该岗位要填的指标（逗号分隔）—— 留空 = 用板块默认指标
    metric_codes: str = Field(default="", max_length=255)
    active: bool = True
    note: str = Field(default="", max_length=255)


@router.get("/policies")
def list_policies(
    department_id: str = "",
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """岗位口径列表（含部门名）。"""
    _require_admin(db, user)
    items = list_policies_svc(db, department_id)
    dept_names = {
        str(d.id): d.name for d in db.scalars(select(Department)).all()
    }
    for item in items:
        item["departmentName"] = dept_names.get(item["departmentId"], "")
    return {"items": items}


@router.post("/policies")
def upsert_policy(
    payload: PolicyRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """配置（或更新）某个部门某个岗位的口径。

    ⚠️ **幂等** —— 同一部门同一岗位重复提交只是更新。
    ⚠️ **不删行** —— 停用置 ``active=False``，保留配置痕迹。
    """
    _require_admin(db, user)

    if payload.role not in {"reporter", "reviewer", "none"}:
        raise HTTPException(
            status_code=400,
            detail=f"未知角色 {payload.role}（可用：reporter / reviewer / none）",
        )

    dept = db.get(Department, payload.department_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="部门不存在")

    row = db.scalar(
        select(PanWorkloadPolicy).where(
            PanWorkloadPolicy.department_id == payload.department_id,
            PanWorkloadPolicy.position == payload.position,
        )
    )
    if row is None:
        row = PanWorkloadPolicy(
            department_id=payload.department_id,
            position=payload.position,
        )
        db.add(row)
    row.role = payload.role
    row.metric_codes = payload.metric_codes
    row.active = payload.active
    row.note = payload.note
    row.updated_by = str(user.id)
    db.commit()
    db.refresh(row)
    return {
        "item": {
            "id": row.id,
            "departmentId": row.department_id,
            "departmentName": dept.name,
            "position": row.position,
            "role": row.role,
            "metricCodes": row.metric_codes,
            "active": row.active,
            "note": row.note,
        }
    }


@router.get("/positions")
def list_positions(
    department_id: str,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """列出某部门的**实际在岗岗位**及其当前口径。

    ⚠️ **为什么要这个端点**
    配置界面得知道「这个部门到底有哪些岗位」——
    从 ``employee.position`` 去重得到**真实存在**的岗位，
    而不是让 HR 手打（手打容易拼错，比如「剪辑」vs「剪辑师」）。
    """
    _require_admin(db, user)

    dept = db.get(Department, department_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="部门不存在")

    positions = [
        str(row[0])
        for row in db.execute(
            select(Employee.position)
            .where(Employee.department_id == department_id)
            .distinct()
            .order_by(Employee.position)
        )
        if row[0]
    ]

    policies = policy_map(db, department_id)
    return {
        "departmentId": department_id,
        "departmentName": dept.name,
        "items": [
            {
                "position": position,
                "role": policies[position].role if position in policies else "none",
                "metricCodes": (
                    policies[position].metric_codes if position in policies else ()
                ),
                "configured": position in policies,
                "headcount": int(
                    db.execute(
                        select(func.count())
                        .select_from(Employee)
                        .where(
                            Employee.department_id == department_id,
                            Employee.position == position,
                            Employee.employment_status == "active",
                        )
                    ).scalar_one()
                ),
            }
            for position in positions
        ],
    }
