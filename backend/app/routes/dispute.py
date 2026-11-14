"""争议管理 API 路由。

对应前端 `src/api/dispute.ts` 的 `/dispute/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/dispute` 前缀。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db

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

    userId: int = Field(..., description="被指派的用户 ID")


class RecusalRequest(BaseModel):
    """回避申请请求体。"""

    reason: str = Field(default="", description="回避原因")


class OpinionRequest(BaseModel):
    """评审意见请求体。"""

    opinion: str = Field(default="", description="意见类型：SUPPORT / REJECT / SUPPLEMENT")
    comment: str = Field(default="", description="意见说明")


class DecisionRequest(BaseModel):
    """裁决请求体。"""

    decision: str = Field(default="", description="裁决类型")
    comment: str = Field(default="", description="裁决说明")


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 争议仪表盘
#
# 响应结构：DisputeDashboard = { metrics, recentCases }
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def get_dispute_dashboard(db: Session = Depends(get_db)) -> dict:
    """获取争议管理仪表盘。

    响应结构：DisputeDashboard
    """
    return _ok({"metrics": [], "recentCases": []})


# ---------------------------------------------------------------------------
# 争议案件
#
# 分页约定：前端 DisputeCase[] 直接 .map()，故列表端点返回裸数组。
# ---------------------------------------------------------------------------


@router.get("/cases")
async def list_dispute_cases(
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取争议案件列表。

    响应结构：DisputeCase[]（裸数组）
    """
    return _ok([])


@router.get("/cases/{case_id}")
async def get_dispute_case(case_id: int, db: Session = Depends(get_db)) -> dict:
    """获取争议案件详情。

    响应结构：DisputeDetail
    """
    return _ok(
        {
            "summary": _empty_dispute_case(case_id),
            "appealReason": "",
            "appealStatus": "",
            "relatedResultId": None,
            "resultTitle": "",
            "resultStatus": "",
            "packageItems": [],
            "reviewers": [],
            "opinions": [],
            "canDecide": False,
            "decision": None,
            "decisionComment": None,
        }
    )


@router.get("/cases/{case_id}/reviewers")
async def list_dispute_reviewers(
    case_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """获取争议案件的评审人列表。

    响应结构：DisputeReviewer[]（裸数组）
    """
    return _ok([])


@router.get("/cases/{case_id}/reviewer-candidates")
async def list_dispute_reviewer_candidates(
    case_id: int,
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """获取可指派的评审人候选。

    响应结构：DisputeReviewerCandidate[]（裸数组）
    """
    return _ok([])


@router.get("/cases/{case_id}/package")
async def download_dispute_package(
    case_id: int,
    db: Session = Depends(get_db),
) -> Any:
    """下载争议案件证据包。

    响应结构：二进制文件流
    """
    # 尚无真实证据包时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="证据包不存在或已过期")


@router.post("/cases/{case_id}/reviewers")
async def add_dispute_reviewer(
    case_id: int,
    payload: AddReviewerRequest,
    db: Session = Depends(get_db),
) -> dict:
    """为争议案件指派评审人。

    响应结构：DisputeReviewer
    """
    return _ok(
        {
            "id": 0,
            "userId": payload.userId,
            "userName": "",
            "sourceType": "MANUAL",
            "recusalStatus": "NONE",
            "recusalReason": None,
            "currentUser": False,
        }
    )


@router.delete("/cases/{case_id}/reviewers/{reviewer_id}")
async def remove_dispute_reviewer(
    case_id: int,
    reviewer_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """移除争议案件的评审人。

    响应结构：void（前端声明为 unknown）
    """
    return _ok(None)


@router.post("/cases/{case_id}/recusal")
async def recuse_dispute(
    case_id: int,
    payload: RecusalRequest,
    db: Session = Depends(get_db),
) -> dict:
    """申请回避。

    响应结构：void（前端声明为 unknown）
    """
    return _ok(None)


@router.post("/cases/{case_id}/opinions")
async def save_dispute_opinion(
    case_id: int,
    payload: OpinionRequest,
    db: Session = Depends(get_db),
) -> dict:
    """提交评审意见。

    响应结构：DisputeOpinionRecord
    """
    return _ok(
        {
            "id": 0,
            "reviewerId": 0,
            "reviewerName": "",
            "opinion": payload.opinion,
            "comment": payload.comment,
            "versionNo": 0,
            "submittedAt": datetime.utcnow().isoformat(),
        }
    )


@router.post("/cases/{case_id}/decision")
async def submit_dispute_decision(
    case_id: int,
    payload: DecisionRequest,
    db: Session = Depends(get_db),
) -> dict:
    """提交争议裁决。

    响应结构：void（前端声明为 unknown）
    """
    return _ok(None)


def _empty_dispute_case(case_id: int) -> dict:
    """构造空 DisputeCase，字段与 frontend/src/api/dispute.ts 保持一致。"""
    return {
        "id": case_id,
        "caseNo": "",
        "appealId": 0,
        "employeeName": "",
        "orgName": "",
        "periodStart": "",
        "periodEnd": "",
        "disputeSubject": "",
        "appealTitle": "",
        "status": "SUBMITTED",
        "packageStatus": "PENDING",
        "deadlineAt": None,
        "reviewerCount": 0,
        "opinionCount": 0,
    }
