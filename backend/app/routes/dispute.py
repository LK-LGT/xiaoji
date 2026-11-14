"""争议管理 API 路由。

对应前端 `src/api/dispute.ts` 的 `/dispute/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/dispute` 前缀。

实现说明：
- 领域逻辑全部在 `services/dispute_service.py`（状态机、评审人、意见、裁决、
  序列化）。本模块只做「HTTP ↔ 服务层」的转接。
- **权限校验复用既有 token 鉴权**（`dependencies.get_current_user`）：
  - 员工：只能查看自己的案件；
  - 评审人：可写意见；
  - HR / 部门负责人：可指派评审人、可裁决。
- 领域异常（`BaseAppError` 子类）由 `main.py` 的全局处理器统一转 HTTP 状态码，
  因此这里不再重复 try/except。

分页约定：前端 `DisputeCase[]` 直接 `.map()`，故列表端点返回裸数组。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import build_access_context_for_user, get_current_user
from ..domain.models import Department, Employee
from ..services import dispute_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/dispute", tags=["dispute"])


# ---------------------------------------------------------------------------
# 请求模型
#
# 注意：必须用显式 Pydantic 模型，不能用 `data: dict`。
# 本项目所用 FastAPI 版本不会把裸 dict 注解自动解析为 JSON body，
# 请求体会以字符串形式传入并触发 422（dict_type 校验失败）。
# ---------------------------------------------------------------------------


class AddReviewerRequest(BaseModel):
    """指派评审人请求体。"""

    userId: str = Field(..., description="被指派的用户 ID")


class RecusalRequest(BaseModel):
    """回避申请请求体。"""

    reason: str = Field(default="", description="回避原因")


class OpinionRequest(BaseModel):
    """评审意见请求体。"""

    opinion: str = Field(
        default="", description="意见类型：SUPPORT / REJECT / SUPPLEMENT"
    )
    comment: str = Field(default="", description="意见说明")


class DecisionRequest(BaseModel):
    """裁决请求体。"""

    decision: str = Field(default="", description="裁决类型")
    comment: str = Field(default="", description="裁决说明")


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 权限辅助
# ---------------------------------------------------------------------------


def _can_manage(user: Employee, db: Session) -> bool:
    """能否管理争议（指派评审人 / 裁决 / 归档）。

    HR 与部门负责人可以；普通主管不行（主管只能写意见）。
    """
    context = build_access_context_for_user(user, db)
    return "hr" in context.roles or "department_leader" in context.roles


def _assert_can_view(user: Employee, db: Session, case: Any) -> None:
    """校验当前用户能否查看该案件。

    可查看：案件本人、被指派评审人、HR、部门负责人。
    """
    context = build_access_context_for_user(user, db)
    if case.owner_id == user.id:
        return
    if "hr" in context.roles or "department_leader" in context.roles:
        return
    if user.id in context.direct_report_ids:
        return
    # 被指派为评审人（含已回避的，也可查看历史）
    reviewers = dispute_service.list_reviewers(db, case.id)
    if any(row.user_id == user.id for row in reviewers):
        return
    raise HTTPException(status_code=403, detail="无权查看该争议案件")


def _assert_can_manage(user: Employee, db: Session) -> None:
    """校验当前用户能否管理争议。"""
    if not _can_manage(user, db):
        raise HTTPException(status_code=403, detail="无权执行该争议管理操作")


def _employee_name(db: Session, employee_id: str | None) -> str:
    """取员工姓名。"""
    if not employee_id:
        return ""
    emp = db.get(Employee, employee_id)
    return emp.name if emp else ""


def _department_name(db: Session, department_id: str | None) -> str:
    """取部门名称。"""
    if not department_id:
        return ""
    dept = db.get(Department, department_id)
    return dept.name if dept else ""


# ---------------------------------------------------------------------------
# 争议仪表盘
#
# 响应结构：DisputeDashboard = { metrics, recentCases }
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def get_dispute_dashboard(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取争议管理仪表盘。

    响应结构：DisputeDashboard
    """
    context = build_access_context_for_user(current_user, db)

    # 数据范围：管理侧看全部，普通员工只看自己的
    owner_filter = (
        None
        if ("hr" in context.roles or "department_leader" in context.roles)
        else current_user.id
    )
    cases = dispute_service.list_cases(db, owner_id=owner_filter, limit=200)

    total = len(cases)
    submitted = sum(1 for c in cases if c.status == "SUBMITTED")
    reviewing = sum(1 for c in cases if c.status == "REVIEWING")
    supplement = sum(1 for c in cases if c.status == "NEEDS_SUPPLEMENT")
    decided = sum(1 for c in cases if c.status == "DECIDED")

    metrics = [
        {"code": "TOTAL", "label": "争议总数", "value": total, "tone": "primary"},
        {"code": "SUBMITTED", "label": "待受理", "value": submitted, "tone": "warning"},
        {"code": "REVIEWING", "label": "评审中", "value": reviewing, "tone": "info"},
        {
            "code": "NEEDS_SUPPLEMENT",
            "label": "待补充",
            "value": supplement,
            "tone": "warning",
        },
        {"code": "DECIDED", "label": "已裁决", "value": decided, "tone": "success"},
    ]

    recent = [
        dispute_service.serialize_case_summary(
            db,
            case,
            employee_name=_employee_name(db, case.owner_id),
            org_name=_department_name(db, case.department_id),
        )
        for case in cases[:10]
    ]
    return _ok({"metrics": metrics, "recentCases": recent})


# ---------------------------------------------------------------------------
# 争议案件
#
# 分页约定：前端 DisputeCase[] 直接 .map()，故列表端点返回裸数组。
# ---------------------------------------------------------------------------


@router.get("/cases")
async def list_dispute_cases(
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取争议案件列表。

    响应结构：DisputeCase[]（裸数组）
    """
    context = build_access_context_for_user(current_user, db)
    owner_filter = (
        None
        if ("hr" in context.roles or "department_leader" in context.roles)
        else current_user.id
    )
    cases = dispute_service.list_cases(
        db, owner_id=owner_filter, status=status, keyword=keyword, limit=200
    )
    return _ok(
        [
            dispute_service.serialize_case_summary(
                db,
                case,
                employee_name=_employee_name(db, case.owner_id),
                org_name=_department_name(db, case.department_id),
            )
            for case in cases
        ]
    )


@router.get("/cases/{case_id}")
async def get_dispute_case(
    case_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取争议案件详情。

    响应结构：DisputeDetail
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_view(current_user, db, case)

    reviewers = dispute_service.list_reviewers(db, case.id)
    opinions = dispute_service.list_opinions(db, case.id)

    reviewer_names = {row.user_id: _employee_name(db, row.user_id) for row in reviewers}
    reviewer_names_by_row = {row.id: reviewer_names.get(row.user_id, "") for row in reviewers}

    return _ok(
        {
            "summary": dispute_service.serialize_case_summary(
                db,
                case,
                employee_name=_employee_name(db, case.owner_id),
                org_name=_department_name(db, case.department_id),
            ),
            "appealReason": case.appeal_reason,
            "appealStatus": case.appeal_status,
            "relatedResultId": case.related_result_id or "",
            "resultTitle": case.result_title,
            "resultStatus": case.result_status,
            "packageItems": case.package_items or [],
            "reviewers": [
                dispute_service.serialize_reviewer(
                    row,
                    user_name=reviewer_names.get(row.user_id, ""),
                    current_user_id=current_user.id,
                )
                for row in reviewers
            ],
            "opinions": [
                dispute_service.serialize_opinion(
                    opinion,
                    reviewer_name=reviewer_names_by_row.get(opinion.reviewer_id, ""),
                )
                for opinion in opinions
            ],
            "canDecide": _can_manage(current_user, db),
            "decision": case.decision,
            "decisionComment": case.decision_comment,
        }
    )


@router.get("/cases/{case_id}/reviewers")
async def list_dispute_reviewers(
    case_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取争议案件的评审人列表。

    响应结构：DisputeReviewer[]（裸数组）
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_view(current_user, db, case)

    rows = dispute_service.list_reviewers(db, case.id)
    return _ok(
        [
            dispute_service.serialize_reviewer(
                row,
                user_name=_employee_name(db, row.user_id),
                current_user_id=current_user.id,
            )
            for row in rows
        ]
    )


@router.get("/cases/{case_id}/reviewer-candidates")
async def list_dispute_reviewer_candidates(
    case_id: str,
    keyword: Optional[str] = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取可指派的评审人候选。

    响应结构：DisputeReviewerCandidate[]（裸数组）
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_manage(current_user, db)

    candidates = dispute_service.list_reviewer_candidates(
        db, case_id=case.id, keyword=keyword
    )
    return _ok([dispute_service.serialize_candidate(emp) for emp in candidates])


@router.get("/cases/{case_id}/package")
async def download_dispute_package(
    case_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """下载争议案件证据包。

    响应结构：二进制文件流
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_view(current_user, db, case)

    # 尚无真实证据包时返回 404，避免前端拿到损坏文件。
    if not case.package_items:
        raise HTTPException(status_code=404, detail="证据包不存在或已过期")
    raise HTTPException(status_code=404, detail="证据包生成功能尚未上线")


@router.post("/cases/{case_id}/reviewers")
async def add_dispute_reviewer(
    case_id: str,
    payload: AddReviewerRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """为争议案件指派评审人。

    响应结构：DisputeReviewer
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_manage(current_user, db)

    reviewer = db.get(Employee, str(payload.userId))
    if reviewer is None:
        raise HTTPException(status_code=404, detail="评审人不存在")
    if reviewer.id == case.owner_id:
        raise HTTPException(status_code=400, detail="不能指派案件提交人本人作为评审人")

    record = dispute_service.assign_reviewer(db, case, reviewer)
    return _ok(
        dispute_service.serialize_reviewer(
            record,
            user_name=reviewer.name,
            current_user_id=current_user.id,
        )
    )


@router.delete("/cases/{case_id}/reviewers/{reviewer_id}")
async def remove_dispute_reviewer(
    case_id: str,
    reviewer_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """移除争议案件的评审人。

    响应结构：void（前端声明为 unknown）
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_manage(current_user, db)

    dispute_service.remove_reviewer(db, case, reviewer_id)
    return _ok(None)


@router.post("/cases/{case_id}/recusal")
async def recuse_dispute(
    case_id: str,
    payload: RecusalRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """申请回避。

    响应结构：void（前端声明为 unknown）
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_view(current_user, db, case)

    # 回避人 = 被指派的评审人本人
    rows = dispute_service.list_reviewers(db, case.id)
    mine = next((row for row in rows if row.user_id == current_user.id), None)
    if mine is None:
        raise HTTPException(status_code=403, detail="仅被指派的评审人可以申请回避")

    dispute_service.recuse_reviewer(db, case, current_user.id, payload.reason)
    return _ok(None)


@router.post("/cases/{case_id}/opinions")
async def save_dispute_opinion(
    case_id: str,
    payload: OpinionRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交评审意见。

    响应结构：DisputeOpinionRecord
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_view(current_user, db, case)

    record = dispute_service.submit_opinion(
        db, case, current_user, payload.opinion, payload.comment
    )

    rows = dispute_service.list_reviewers(db, case.id)
    name = next(
        (_employee_name(db, row.user_id) for row in rows if row.id == record.reviewer_id),
        "",
    )
    return _ok(dispute_service.serialize_opinion(record, reviewer_name=name))


@router.post("/cases/{case_id}/decision")
async def submit_dispute_decision(
    case_id: str,
    payload: DecisionRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交争议裁决。

    响应结构：void（前端声明为 unknown）
    """
    case = dispute_service.get_case(db, case_id)
    _assert_can_manage(current_user, db)

    dispute_service.decide_case(db, case, current_user, payload.decision, payload.comment)
    return _ok(None)
