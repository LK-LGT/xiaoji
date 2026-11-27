"""【板块3】员工自评 API 路由（自研前端渠道入口）。

设计：
    - 身份鉴权：依赖注入 get_current_user(header: Authorization)
    - 权限检查：调用 check_can_submit_review(user_id, record_id, db)
    - 业务编排：调用 self_review_service.submit_self_review()
    - 不含渠道转换代码（飞书相关的所有逻辑放在板块6）

不变式（多渠道架构的第一条防线）：
    - 业务接口参数形状一致（同一个 SelfReviewSubmitRequest）
    - 业务接口返回结构一致（dict 格式，统一响应字段）
    - 错误异常全部来自 exceptions 模块
    - 状态流转、权限校验、参数校验口径在 dependencies/services 中**定义一次、共用两遍**
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from .. import dependencies, exceptions
from ..core.db import get_db
from ..domain.models import AssessmentRecord, Employee
from ..schemas_self_review import (
    SelfReviewDraftRequest,
    SelfReviewErrorResponse,
    SelfReviewSubmitRequest,
    SelfReviewSubmitResponse,
)
from ..services import self_review_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/assessment", tags=["self_review"])


@router.put(
    "/records/{record_id}/self-review/draft",
    summary="【自研前端】保存自评草稿",
)
async def save_self_review_draft(
    record_id: str = Path(..., description="考核记录 ID"),
    request: SelfReviewDraftRequest = None,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """保存自评草稿（允许未填完；不改记录状态）。"""
    if request is None:
        raise HTTPException(status_code=422, detail="请求体不能为空")
    record = db.get(AssessmentRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    if record.employee_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能保存自己的自评草稿")
    if record.submit_locked or record.status in {"self_review_submitted", "confirmed", "archived"}:
        raise HTTPException(status_code=409, detail="自评已提交，不能再保存草稿")

    draft = dict(record.self_review_content or {})
    if request.self_score is not None:
        draft["self_score"] = request.self_score
    draft["self_comment"] = request.self_comment
    draft["highlights"] = request.highlights
    draft["improvement_areas"] = request.improvement_areas
    for item in request.answers or []:
        draft[item.field_key] = item.text
    record.self_review_content = draft
    db.commit()
    db.refresh(record)
    return {
        "code": 0,
        "data": {
            "record_id": record.id,
            "status": record.status,
            "saved_at": record.updated_at.isoformat() if record.updated_at else None,
            "message": "草稿已保存",
        },
    }


@router.post(
    "/records/{record_id}/self-review",
    response_model=SelfReviewSubmitResponse,
    status_code=201,
    responses={
        400: {
            "model": SelfReviewErrorResponse,
            "description": "参数验证失败或业务逻辑冲突",
        },
        403: {
            "model": SelfReviewErrorResponse,
            "description": "权限不足（不是记录所有者）",
        },
        404: {
            "model": SelfReviewErrorResponse,
            "description": "记录或任务不存在",
        },
        409: {
            "model": SelfReviewErrorResponse,
            "description": "记录状态冲突（已提交或已锁定）",
        },
        422: {
            "model": SelfReviewErrorResponse,
            "description": "截止时间已过或参数超出范围",
        },
    },
    summary="【自研前端】员工提交自评",
    description="员工填写自评表单并提交，包括自评分数、评论、指标值和附件。"
                "权限检查：只能提交自己的自评。"
                "状态检查：记录必须处于'待自评'状态且未被锁定。",
)
async def submit_self_review(
    record_id: str = Path(..., description="考核记录 ID"),
    request: SelfReviewSubmitRequest = None,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> SelfReviewSubmitResponse:
    """【板块3.1】员工提交自评（自研前端工作台入口）。

    业务流程（严格遵循多渠道架构 —— 两渠道的业务逻辑完全相同）：
        1. [鉴权] 验证 token 有效性 → get_current_user ✓ 已完成
        2. [权限] 检查用户是否是该记录的所有者 → check_can_submit_review
        3. [参数验证] 检查自评分数/评论/指标值的有效性
        4. [状态] 检查记录是否处于待自评状态且未被锁定
        5. [截止时间] 检查是否超过自评截止时间
        6. [保存] 调用 review_service.submit_self_review() 落库并流转状态
        7. [返回] 统一响应格式

    异常处理（全部转 HTTP 200-level）：
        - 404: 记录/任务不存在
        - 403: 权限不足
        - 409: 状态冲突
        - 422: 参数或时间超出范围
        - 400: 参数验证失败

    示例请求::

        POST /v1/assessment/records/rec_001/self-review
        Authorization: Bearer emp_token_001
        Content-Type: application/json

        {
          "self_score": 4,
          "self_comment": "本月完成销售目标 105%，客户满意度 95% 以上",
          "highlights": "新客户拓展、团队协作",
          "improvement_areas": "产品知识、演讲技能",
          "metric_values": [
            {"metric_code": "GMV", "value": 1050, "unit": "万元", "comment": "新签客户贡献 50 万"}
          ],
          "attachments": [
            {"file_id": "f_abc123", "file_name": "月度报表.pdf", "file_size": 2048}
          ]
        }

    示例成功响应 (201)::

        {
          "code": 0,
          "message": "自评已提交成功",
          "data": {
            "record_id": "rec_001",
            "employee_id": "E001",
            "self_review_id": "sr_abc123",
            "status": "self_review_submitted",
            "submit_source": "h5",
            "submit_locked": true,
            "self_score": 4,
            "self_comment": "本月完成销售目标 105%...",
            "metric_values_count": 1,
            "attachments_count": 1,
            "submitted_at": "2026-11-12T15:30:00",
            "message": "自评已提交成功，等待主管评分",
            "next_step": "主管评分"
          }
        }

    示例失败响应 (403)::

        {
          "code": "PERMISSION_DENIED",
          "message": "用户 E002 无权执行 submit_self_review record:rec_001",
          "details": {
            "user_id": "E002",
            "action": "submit_self_review",
            "resource": "record:rec_001"
          }
        }
    """

    try:
        # ──────────────────────────────────────────────────────
        # 1. 权限检查（依赖注入完成鉴权，这里检查是否有权限操作该记录）
        # ──────────────────────────────────────────────────────
        # （这个调用会查记录、查用户关系、查权限矩阵）
        _ = dependencies.check_can_submit_review(current_user.id, record_id, db)

        # ──────────────────────────────────────────────────────
        # 2. 业务层编排（参数验证、状态检查、截止时间检查、保存）
        # ──────────────────────────────────────────────────────
        result_data = self_review_service.submit_self_review(
            db,
            record_id=record_id,
            current_employee=current_user,
            req=request,
        )

        # ──────────────────────────────────────────────────────
        # 3. 统一响应
        # ──────────────────────────────────────────────────────
        logger.info(
            "[POST /v1/assessment/records/{%s}/self-review] "
            "success: employee=%s status=%s",
            record_id,
            current_user.id,
            result_data.get("status"),
        )

        return SelfReviewSubmitResponse(
            code=0, message="自评已提交成功", data=result_data
        )

    # ─────────────────────────────────────────────────────────────
    # 异常处理（全部转 HTTPException，由 FastAPI 转为 JSON）
    # ─────────────────────────────────────────────────────────────
    except exceptions.PermissionDenied as exc:
        logger.warning(f"[POST /...] PermissionDenied: {exc}")
        raise HTTPException(
            status_code=403,
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc

    except exceptions.RecordNotFound as exc:
        logger.warning(f"[POST /...] RecordNotFound: {exc}")
        raise HTTPException(
            status_code=404,
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc

    except exceptions.InvalidStateTransition as exc:
        logger.warning(f"[POST /...] InvalidStateTransition: {exc}")
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc

    except exceptions.DeadlineExceeded as exc:
        logger.warning(f"[POST /...] DeadlineExceeded: {exc}")
        raise HTTPException(
            status_code=422,
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc

    except exceptions.DataValidationError as exc:
        logger.warning(f"[POST /...] DataValidationError: {exc}")
        raise HTTPException(
            status_code=400,
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc

    except exceptions.NotAuthenticated as exc:
        logger.warning(f"[POST /...] NotAuthenticated: {exc}")
        raise HTTPException(
            status_code=401,
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc

    except Exception as exc:
        logger.error(f"[POST /...] Unexpected error: {exc}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
                "details": {},
            },
        ) from exc
