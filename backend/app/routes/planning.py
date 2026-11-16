"""计划统计 API 路由。

对应前端 `src/api/planning.ts` 的 `/planning/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/planning` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services import plan_execution_metric
from ..services.access_control import build_access_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/planning", tags=["planning"])


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 计划统计
#
# 响应结构：PlanningStats
# ---------------------------------------------------------------------------


@router.get("/stats")
async def get_planning_stats(
    month: str | None = Query(None, description="统计周期 YYYY-MM，缺省取当前月"),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取计划执行统计概览。

    响应结构：PlanningStats

    ⚠️ **A7 起接真实统计**（此前恒返回四个 0）：

    | 字段 | 口径 |
    |---|---|
    | `pendingDayPlans` | 待审（待抽检）日计划数：`status=submitted` 且未记录抽检结论 |
    | `overduePendingDayPlans` | 逾期未审：`approval_due_at` 已过期且未抽检 |
    | `currentMonthResults` | 周期内成果总数（月成果按 `plan_month`，周成果按周起始月 —— **D6**） |
    | `closureRate` | 计划执行闭环率 = 已审批成果数 / 应提交条目数 |

    **数据范围**按访问上下文（ACL）解析：HR / 管理员看全公司，
    部门负责人看管理范围，主管看直属下属，普通员工只看自己。
    """
    context = build_access_context(db, current_user)
    stats = plan_execution_metric.build_planning_stats(db, context, period=month)
    return _ok(stats.as_dict())
