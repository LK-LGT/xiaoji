"""领导工作台 API 路由。

对应前端 `src/api/leader.ts` 的 `/leader/*` 调用。
前端经 Vite 代理把 `/api/*` 重写到 `/v1/*`，故此处统一使用 `/v1/leader` 前缀。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import build_access_context_for_user, get_current_user
from ..domain.models import Employee
from ..services import (
    department_config_service,
    knowledge_parser,
    knowledge_service,
    ledger_service,
    plan_adjustment_service,
    plan_approval,
    workbench_service,
)
from ..services.access_control import PermissionAction
from .shared import JsonBody

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/leader", tags=["leader"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# 响应构造辅助
# ---------------------------------------------------------------------------


def _ok(data: Any) -> dict:
    """统一响应信封：前端 http.ts 会自动拆包出 data。"""
    return {"code": 0, "data": data}


# ---------------------------------------------------------------------------
# 计划审批 —— 公共辅助
# ---------------------------------------------------------------------------


def _approval_scope(current_user: Employee, db: Session) -> plan_approval.ApprovalScope:
    """构造审批人的可见范围。"""
    context = build_access_context_for_user(current_user, db)
    return plan_approval.ApprovalScope.from_access_context(context)


def _assert_can_approve_plan(
    current_user: Employee,
    plan_id: str,
    plan_type: str,
    db: Session,
) -> None:
    """校验审批人是否有权审批该计划。

    规则（与 `services/access_control.can_access_employee` 对齐）：
    - 直属主管可审批直属下属的计划；
    - 部门负责人可审批管理范围内成员的计划。
    """
    from ..services.access_control import can_access_employee

    context = build_access_context_for_user(current_user, db)
    plan = db.get(plan_approval.plan_model(plan_type), plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    owner = db.get(Employee, plan.owner_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="计划归属人不存在")
    if not can_access_employee(context, owner, db):
        raise HTTPException(status_code=403, detail="无权审批该计划")


def _run_approval(action, *args, **kwargs) -> Any:
    """执行审批动作，把领域异常翻译成 HTTP 状态码。"""
    try:
        return action(*args, **kwargs)
    except plan_approval.PlanApprovalConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except plan_approval.PlanApprovalForbidden as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except plan_approval.PlanApprovalError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


def _request_version(data: dict) -> int | None:
    """从请求体取 `versionNo`（乐观锁版本号）。

    前端 `weekPlan.ts` / `leader.ts` 的审批接口会带 `versionNo`。
    缺省视为不校验（兼容旧调用方）。
    """
    raw = data.get("versionNo")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _batch_approve(
    current_user: Employee,
    db: Session,
    data: dict,
    plan_type: str,
    *,
    approved: bool,
) -> list[dict]:
    """批量审批，逐条校验权限，单条失败不影响其它条目。

    返回 `ActionResult[]`，与前端 `_batch_action_results` 的形状一致。
    """
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    comment = data.get("comment")
    version_no = _request_version(data)

    results: list[dict] = []
    for raw_id in ids:
        plan_id = str(raw_id)
        try:
            _assert_can_approve_plan(current_user, plan_id, plan_type, db)
            if plan_type == plan_approval.PLAN_TYPE_MONTH:
                action = (
                    plan_approval.approve_month_plan
                    if approved
                    else plan_approval.reject_month_plan
                )
                _run_approval(
                    action, db, current_user.id, plan_id,
                    comment=comment, version_no=version_no,
                )
            elif plan_type == plan_approval.PLAN_TYPE_WEEK:
                action = (
                    plan_approval.approve_week_plan
                    if approved
                    else plan_approval.reject_week_plan
                )
                _run_approval(
                    action, db, current_user.id, plan_id,
                    comment=comment, version_no=version_no,
                )
            else:
                _run_approval(
                    plan_approval.review_day_plan,
                    db,
                    current_user.id,
                    plan_id,
                    approved=approved,
                    comment=comment,
                    version_no=version_no,
                )
            results.append(
                _action_result(plan_id, "APPROVED" if approved else "REJECTED")
            )
        except HTTPException as exc:
            results.append(
                {
                    "objectId": plan_id,
                    "status": "FAILED",
                    "message": str(exc.detail),
                    "auditActionCode": "",
                    "auditDeferred": False,
                }
            )
    return results


# ---------------------------------------------------------------------------
# 企业知识库 —— 领导侧上传
#
# 响应结构：KnowledgeUploadResult / UploadedKnowledgeDocument[]（裸数组）
#
# ⚠️ **2026-11-27：这两个端点原先是空壳**
#   上传恒返回 ``documentId: 0`` / ``chunkCount: 0``，
#   列表恒返回 ``[]`` ⇒ 文件**根本没存**。
#   现已接入 :mod:`services.knowledge_service` 真正落库。
#
# ⚠️ **表单字段与前端一致**
#   前端 ``uploadLeaderKnowledgeApi`` 提交的是
#   ``audienceCodes / scopeType / scopeOrgId / knowledgeType / appliesTo / bindingLevel``
#   （**没有 title**）⇒ 后端原来必填 ``title`` ⇒ 必定 422。
#   现在 title 由**文件名推导**，前端不必再传。
# ---------------------------------------------------------------------------

#: 允许上传知识文档的角色（领导端“文档入库”）。
_LEADER_KNOWLEDGE_ROLES = frozenset({"manager", "department_leader", "hr", "admin"})


def _require_leader_knowledge_writer(db: Session, current_user: Employee) -> None:
    """领导端知识上传仅限主管/部门负责人/HR/管理员。

    ⚠️ 不使用全局 ``MANAGE_KNOWLEDGE`` 权限位 —— 那个位还包含
    「停用 / 删除任意文档」，不能顺带授予主管。
    """
    context = build_access_context_for_user(current_user, db)
    if not (context.roles & _LEADER_KNOWLEDGE_ROLES):
        raise HTTPException(status_code=403, detail="无权上传知识文档")


@router.post("/knowledge/upload")
async def upload_leader_knowledge(
    file: UploadFile = File(...),
    audienceCodes: str = Form(""),
    scopeType: str = Form("GLOBAL"),
    scopeOrgId: str = Form(""),
    knowledgeType: str = Form(""),
    appliesTo: str = Form(""),
    bindingLevel: str = Form("REFERENCE"),
    title: str = Form(""),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """领导上传知识库文档。

    响应结构：KnowledgeUploadResult

    ⚠️ **真的入库**
    解析 → 切块 → 落盘 → 建索引 ⇒ 上传后立刻能被 AI 检索到。
    """
    _require_leader_knowledge_writer(db, current_user)
    data = await file.read()
    audience = [item.strip() for item in (audienceCodes or "").split(",") if item.strip()]
    scopes = [item.strip() for item in (appliesTo or "").split(",") if item.strip()]

    if not audience:
        raise HTTPException(status_code=422, detail="请选择至少一个适用角色")
    if not scopes:
        raise HTTPException(status_code=422, detail="请选择至少一个适用业务")
    if (scopeType or "GLOBAL") != "GLOBAL" and not (scopeOrgId or "").strip():
        raise HTTPException(status_code=422, detail="指定部门或项目时必须选择范围")

    scope_org_value = (scopeOrgId or "").strip() or None
    scope_org_name = None
    if scope_org_value:
        from ..domain.models import Department

        department = db.get(Department, scope_org_value)
        scope_org_name = department.name if department else None

    try:
        result = knowledge_service.ingest_document(
            db,
            data=data,
            filename=file.filename or "",
            title=title,
            knowledge_type=knowledgeType,
            applies_to=scopes,
            audience_codes=audience,
            binding=bindingLevel,
            scope_type=scopeType or "GLOBAL",
            scope_org_id=scope_org_value,
            scope_org_name=scope_org_name,
            source_type="UPLOAD",
            owner_id=current_user.id,
            owner_name=current_user.name or current_user.id,
        )
    except knowledge_service.KnowledgeServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except knowledge_parser.KnowledgeParseError as exc:
        raise HTTPException(status_code=422, detail=f"文档解析失败：{exc}") from exc
    return _ok(result)


@router.get("/knowledge/uploads")
async def list_leader_knowledge_uploads(
    limit: int = Query(12, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取领导上传的知识库文档列表。

    响应结构：UploadedKnowledgeDocument[]（裸数组）

    ⚠️ **只返回该领导自己上传的**
    领导端是「我的入库台」⇒ 不该看到别人的上传记录。
    ⚠️ 列表按 ``owner_id == 当前用户`` 过滤，普通员工看也只会看到空列表
    ⇒ 不额外加角色闸，避免把「只看自己」误伤成 403。
    """
    from sqlalchemy import select

    from ..domain.knowledge_models import KnowledgeDocumentRow

    rows = db.execute(
        select(KnowledgeDocumentRow)
        .where(
            KnowledgeDocumentRow.owner_id == current_user.id,
            KnowledgeDocumentRow.status != "DELETED",
        )
        .order_by(
            KnowledgeDocumentRow.created_at.desc(), KnowledgeDocumentRow.id.desc()
        )
        .limit(limit)
    ).scalars().all()
    return _ok([knowledge_service.serialize_uploaded_document(row) for row in rows])


# ---------------------------------------------------------------------------
# 导出任务
#
# 响应结构：ExportTask / ExportDownload
# ---------------------------------------------------------------------------


@router.post("/export-tasks")
async def create_leader_export_task(
    data: JsonBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """创建领导侧导出任务。

    响应结构：ExportTask

    ⚠️ **2026-11-26：原实现返回空壳任务** —— 库里什么都没有，
    前端拿到 ``id: ""`` 去下载必然 404。
    ⇒ 现在**真的建任务 + 真的生成产物 + 真的算校验值**。
    """
    try:
        row = department_config_service.create_export_task(
            db, dict(data), requested_by=current_user.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    content, file_name = _build_leader_export_artifact(db, current_user)
    if content is None:
        department_config_service.mark_export_failed(db, row, "暂无可导出的数据")
    else:
        department_config_service.mark_export_success(
            db,
            row,
            content=content,
            file_name=file_name,
            checksum=department_config_service.checksum_of(content),
        )
    return _ok(department_config_service.serialize_export_task(row))


def _build_leader_export_artifact(db: Session, current_user: Employee) -> tuple[bytes | None, str]:
    """生成领导侧导出产物（CSV —— 团队台账）。"""
    import csv
    import io

    context = build_access_context_for_user(current_user, db)
    employee_ids = sorted(context.reporting_line_ids) or sorted(context.direct_report_ids)
    rows = ledger_service.build_ledger_rows(db, employee_ids=employee_ids or None)
    detail = [r for r in rows if r["id"] != "__summary__"]
    if not detail:
        return None, ""

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["员工", "账号", "部门", "计划数", "成果数", "完成率(%)", "参考分", "逾期数", "缺证据数"])
    for row in detail:
        writer.writerow([
            row["employeeName"],
            row["employeeNo"],
            row["orgName"],
            row["planCount"],
            row["resultCount"],
            row["avgCompletionRatio"],
            row["referenceScore"],
            row["overdueCount"],
            row["missingEvidenceCount"],
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8"), "leader-export.csv"


@router.get("/export-tasks/{task_id}/download-info")
async def get_leader_export_download_info(
    task_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """获取导出任务下载信息。

    响应结构：ExportDownload
    """
    from ..domain.config_models import ExportTaskRow

    row = db.get(ExportTaskRow, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    return _ok(department_config_service.export_download_info(row))


@router.get("/export-tasks/{task_id}/download")
async def download_leader_export_task(
    task_id: str,
    db: Session = Depends(get_db),
) -> Any:
    """下载导出任务产物。

    响应结构：二进制文件流
    """
    result = department_config_service.get_export_artifact(db, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="导出产物不存在或已过期")
    row, content = result
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{row.artifact_path}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# 组织树 / 工作台
#
# 响应结构：OrgNode[] / LeaderWorkbench
# ---------------------------------------------------------------------------


def _leader_employee_ids(db: Session, current_user: Employee) -> list[str]:
    """领导端「直属员工」范围（飞书 ``manager_id``）。"""
    context = build_access_context_for_user(current_user, db)
    return sorted(context.direct_report_ids)


def _leader_department_ids(
    db: Session, current_user: Employee, employee_ids: list[str]
) -> list[str]:
    """领导可见部门 = 直属员工所在部门 + 本人管理部门。"""
    context = build_access_context_for_user(current_user, db)
    ids = set(context.managed_department_ids)
    if employee_ids:
        rows = (
            db.query(Employee.department_id)
            .filter(Employee.id.in_(employee_ids))
            .all()
        )
        ids.update(str(row[0]) for row in rows if row[0])
    return sorted(ids)


def _assert_leader_can_view(db: Session, current_user: Employee, owner_id: str) -> None:
    """校验目标员工是否在汇报线 / 管理部门 / 代理授权范围内。"""
    context = build_access_context_for_user(current_user, db)
    if str(owner_id) == current_user.id:
        return
    if context.has_permission(PermissionAction.VIEW_ALL_SCORES):
        return
    if str(owner_id) in (
        set(context.direct_report_ids)
        | set(context.reporting_line_ids)
        | set(context.delegated_employee_ids)
    ):
        return
    if context.managed_department_ids:
        employee = db.get(Employee, str(owner_id))
        if employee is not None and str(employee.department_id or "") in context.managed_department_ids:
            return
    raise HTTPException(status_code=403, detail="无权查看该员工的工作记录")


@router.get("/org-tree")
async def get_leader_org_tree(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取领导侧组织树。

    响应结构：OrgNode[]（裸数组）
    """
    employee_ids = _leader_employee_ids(db, current_user)
    department_ids = _leader_department_ids(db, current_user, employee_ids)
    return _ok(workbench_service.org_tree(db, department_ids))


@router.get("/workbench")
async def get_leader_workbench(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取领导工作台聚合数据。

    响应结构：LeaderWorkbench
    = { orgTree, metrics, dateStatuses, subordinateSummaries }

    ⚠️ ``dateStatuses`` / ``subordinateSummaries`` 目前未使用（前端工作台
    走员工列表与计划包接口）⇒ 如实返回空数组，不编造数据。
    """
    employee_ids = _leader_employee_ids(db, current_user)
    department_ids = _leader_department_ids(db, current_user, employee_ids)
    return _ok(
        {
            "orgTree": workbench_service.org_tree(db, department_ids),
            "metrics": workbench_service.workbench_metrics(db, employee_ids),
            "dateStatuses": [],
            "subordinateSummaries": [],
        }
    )


# ---------------------------------------------------------------------------
# 下属员工工作项
#
# 响应结构：LeaderEmployeeSummary[] / LeaderEmployeeWorkPage / LeaderEmployeeMonthPlanDetail
# ---------------------------------------------------------------------------


@router.get("/employees")
async def list_leader_employees(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取领导下属员工摘要列表。

    响应结构：LeaderEmployeeSummary[]（裸数组）
    """
    return _ok(
        workbench_service.employee_summaries(
            db, _leader_employee_ids(db, current_user)
        )
    )


@router.get("/employees/{owner_id}/work-items")
async def list_leader_employee_work_items(
    owner_id: str,
    page_no: int = Query(1, ge=1, alias="pageNo"),
    page_size: int = Query(20, ge=1, le=200, alias="pageSize"),
    work_type: str | None = Query(None, alias="workType"),
    start_date: str | None = Query(None, alias="startDate"),
    end_date: str | None = Query(None, alias="endDate"),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取指定员工的工作项分页。

    响应结构：LeaderEmployeeWorkPage
    = { employee, items, total, pageNo, pageSize }
    """
    _assert_leader_can_view(db, current_user, owner_id)
    employee = db.get(Employee, owner_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="员工不存在")
    return _ok(
        workbench_service.work_items_page(
            db,
            employee,
            work_type=work_type,
            start_date=start_date,
            end_date=end_date,
            status=status,
            keyword=keyword,
            page_no=page_no,
            page_size=page_size,
        )
    )


@router.get("/employees/{owner_id}/month-plans/{plan_id}")
async def get_leader_employee_month_plan(
    owner_id: str,
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取指定员工的月计划详情。

    响应结构：LeaderEmployeeMonthPlanDetail
    """
    _assert_leader_can_view(db, current_user, owner_id)
    detail = workbench_service.month_plan_detail(db, owner_id, plan_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="月计划不存在")
    return _ok(detail)


def _empty_leader_employee_summary(owner_id: str) -> dict:
    """构造空 LeaderEmployeeSummary，字段与 frontend/src/api/leader.ts 保持一致。

    ⚠️ `ownerId` / `orgId` 是飞书稳定 ID（长十六进制字符串），故为 string。
    """
    return {
        "ownerId": owner_id,
        "employeeNo": "",
        "employeeName": "",
        # 根节点哨兵值是字符串 "0"，不能写成数字 0。
        "orgId": "0",
        "orgName": "",
        "monthPlanCount": 0,
        "weekPlanCount": 0,
        "dayPlanCount": 0,
        "resultCount": 0,
        "totalWorkCount": 0,
    }


# ---------------------------------------------------------------------------
# 月计划 / 周计划审批
#
# 响应结构：MonthPlanApprovalItem[] / LeaderMonthPlanApprovalPage /
#           LeaderMonthPlanApprovalDetail
# ---------------------------------------------------------------------------


@router.get("/month-plan-approvals")
async def list_leader_month_plan_approvals(
    request: Request,
    planYear: int | None = Query(None),
    planMonth: int | None = Query(None),
    status: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧月计划审批列表。

    查询参数用 camelCase，与前端 `MonthPlanApproval.vue` 实际发送的参数对齐。

    响应结构：MonthPlanApprovalItem[]（裸数组）
    """
    scope = _approval_scope(current_user, db)

    plan_month = None
    if planYear and planMonth:
        plan_month = f"{planYear:04d}-{planMonth:02d}"

    plans = plan_approval.list_pending_month_plans(
        db,
        scope,
        plan_month=plan_month,
        status=status,
    )
    return _ok(
        [
            plan_approval.serialize_month_plan_approval_item(db, plan)
            for plan in plans
        ]
    )


@router.get("/month-plan-approvals/page")
async def page_leader_month_plan_approvals(
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    planYear: int | None = Query(None),
    planMonth: int | None = Query(None),
    status: str | None = Query(None),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧月计划审批分页。

    查询参数用 camelCase，与前端 `frontend/src/views/department/MonthPlanApproval.vue`
    实际发送的 `pageNo` / `pageSize` / `planYear` / `planMonth` / `status` 对齐。

    响应结构：LeaderMonthPlanApprovalPage
    """
    scope = _approval_scope(current_user, db)

    # 前端传 planYear + planMonth（两个 number），内部统一成 "2026-11"
    plan_month = None
    if planYear and planMonth:
        plan_month = f"{planYear:04d}-{planMonth:02d}"
    elif planMonth:
        plan_month = f"-{planMonth:02d}"

    plans = plan_approval.list_pending_month_plans(
        db,
        scope,
        plan_month=plan_month,
        status=status,
        limit=10000,
    )

    total = len(plans)
    start = (pageNo - 1) * pageSize
    page_items = plans[start : start + pageSize]

    return _ok(
        {
            "items": [
                plan_approval.serialize_month_plan_approval_item(db, plan)
                for plan in page_items
            ],
            "total": total,
            "pageNo": pageNo,
            "pageSize": pageSize,
            "submittedCount": total,
            "approvedCount": 0,
            "totalEmployeeCount": len({p.owner_id for p in plans}),
        }
    )


@router.get("/month-plan-approvals/{approval_id}")
async def get_leader_month_plan_approval(
    approval_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧月计划审批详情。

    响应结构：LeaderMonthPlanApprovalDetail
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_MONTH), approval_id)
    return _ok(
        {
            "plan": plan_approval.serialize_month_plan_approval_item(db, plan),
            "comparisonStatus": "NONE",
        }
    )


@router.get("/month-plan-approvals/{approval_id}/template-download")
async def download_leader_month_plan_template(
    approval_id: str,
    db: Session = Depends(get_db),
) -> Any:
    """下载月计划模板。

    响应结构：二进制文件流
    """
    # 尚无真实模板产物时返回 404，避免前端拿到损坏文件。
    raise HTTPException(status_code=404, detail="模板不存在或已过期")


@router.post("/month-plan-approvals/batch-approve")
async def batch_approve_month_plans(
    data: dict,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量通过月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(
        _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_MONTH,
            approved=True,
        )
    )


@router.post("/month-plan-approvals/batch-reject")
async def batch_reject_month_plans(
    data: dict,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量驳回月计划。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(
        _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_MONTH,
            approved=False,
        )
    )


@router.post("/month-plan-approvals/{approval_id}/approve")
async def approve_month_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """通过月计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    _run_approval(
        plan_approval.approve_month_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return _ok(_action_result(approval_id, "APPROVED"))


@router.post("/month-plan-approvals/{approval_id}/reject")
async def reject_month_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """驳回月计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_MONTH, db
    )
    _run_approval(
        plan_approval.reject_month_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return _ok(_action_result(approval_id, "REJECTED"))


# ---------------------------------------------------------------------------
# 周计划审批
# ---------------------------------------------------------------------------


@router.get("/week-plan-approvals")
async def list_leader_week_plan_approvals(
    request: Request,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧周计划审批列表。

    响应结构：WeekPlanSummary[]（裸数组）
    """
    scope = _approval_scope(current_user, db)
    plans = plan_approval.list_pending_week_plans(db, scope)
    return _ok(
        [plan_approval.serialize_week_plan_summary(db, plan) for plan in plans]
    )


@router.get("/week-plan-approvals/{approval_id}")
async def get_leader_week_plan_approval(
    approval_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧周计划审批详情。

    响应结构：WeekPlanSummary
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_WEEK, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_WEEK), approval_id)
    return _ok(plan_approval.serialize_week_plan_summary(db, plan))


@router.post("/week-plan-approvals/{approval_id}/approve")
async def approve_week_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """通过周计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_WEEK, db
    )
    _run_approval(
        plan_approval.approve_week_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return _ok(_action_result(approval_id, "APPROVED"))


@router.post("/week-plan-approvals/{approval_id}/reject")
async def reject_week_plan(
    approval_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """驳回周计划。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, approval_id, plan_approval.PLAN_TYPE_WEEK, db
    )
    _run_approval(
        plan_approval.reject_week_plan,
        db,
        current_user.id,
        approval_id,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return _ok(_action_result(approval_id, "REJECTED"))


# ---------------------------------------------------------------------------
# 日计划点评
#
# 响应结构：DailyReviewItem[]
# ---------------------------------------------------------------------------


@router.get("/daily-reviews")
async def list_leader_daily_reviews(
    request: Request,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取领导侧日计划点评列表。

    响应结构：DailyReviewItem[]（裸数组）
    """
    scope = _approval_scope(current_user, db)
    plans = plan_approval.list_pending_day_plans(db, scope)
    return _ok(
        [plan_approval.serialize_daily_review_item(db, plan) for plan in plans]
    )


@router.get("/daily-reviews/{review_id}")
async def get_leader_daily_review(
    review_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """领导侧日计划点评详情。

    响应结构：DailyReviewItem
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_DAY), review_id)
    return _ok(plan_approval.serialize_daily_review_item(db, plan))


@router.post("/daily-reviews/{review_id}/comment")
async def comment_daily_review(
    review_id: str,
    data: JsonBody,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """填写日计划点评。

    响应结构：ActionResult
    """
    _assert_can_approve_plan(
        current_user, review_id, plan_approval.PLAN_TYPE_DAY, db
    )
    _run_approval(
        plan_approval.review_day_plan,
        db,
        current_user.id,
        review_id,
        approved=True,
        comment=data.get("comment"),
        version_no=_request_version(data),
    )
    return _ok(_action_result(review_id, "COMMENTED"))


def _mark_day_plan_risk(
    db: Session, current_user: Employee, review_id: str, data: JsonBody
) -> dict:
    _assert_can_approve_plan(current_user, review_id, plan_approval.PLAN_TYPE_DAY, db)
    plan = db.get(plan_approval.plan_model(plan_approval.PLAN_TYPE_DAY), review_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="日计划不存在")
    level = str(data.get("riskLevel") or data.get("risk_level") or "HIGH").upper()
    if level not in {"LOW", "MEDIUM", "HIGH"}:
        raise HTTPException(status_code=422, detail="风险等级只能是 LOW / MEDIUM / HIGH")
    plan.risk_level = level
    plan.review_status = "RISK_MARKED"
    comment = data.get("comment")
    if comment:
        plan.leader_comment = str(comment)
    db.commit()
    return _action_result(review_id, "RISK_MARKED", message=f"已标记为{level}风险")


@router.post("/daily-reviews/{review_id}/risk")
async def mark_daily_review_risk(
    review_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """标记日计划风险。

    响应结构：ActionResult
    """
    return _ok(_mark_day_plan_risk(db, current_user, review_id, data))


@router.post("/daily-reviews/batch-comment")
async def batch_comment_daily_reviews(
    data: dict,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量填写日计划点评。

    请求体：{ ids: string[] }
    响应结构：ActionResult[]（裸数组）
    """
    return _ok(
        _batch_approve(
            current_user,
            db,
            data,
            plan_approval.PLAN_TYPE_DAY,
            approved=True,
        )
    )


@router.post("/daily-reviews/batch-risk")
async def batch_mark_daily_reviews_risk(
    data: dict,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """批量标记日计划风险。

    请求体：{ ids: string[], riskLevel?: LOW/MEDIUM/HIGH, comment?: string }
    响应结构：ActionResult[]（裸数组）
    """
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    results = []
    for review_id in ids:
        try:
            results.append(
                _mark_day_plan_risk(db, current_user, str(review_id), JsonBody(**dict(data)))
            )
        except HTTPException as exc:
            results.append(
                _action_result(str(review_id), "FAILED", message=str(exc.detail))
            )
    return _ok(results)


# ---------------------------------------------------------------------------
# 计划调整
#
# 响应结构：PlanAdjustmentItem[] / PlanAdjustmentItem
# ---------------------------------------------------------------------------


def _adjustment_owner_scope(db: Session, current_user: Employee) -> list[str] | None:
    """计划调整可见的申请人范围；``None`` 表示 HR 可见全部。"""
    context = build_access_context_for_user(current_user, db)
    if context.has_permission(PermissionAction.VIEW_ALL_SCORES):
        return None
    ids = (
        set(context.direct_report_ids)
        | set(context.reporting_line_ids)
        | set(context.delegated_employee_ids)
    )
    if context.managed_department_ids:
        rows = (
            db.query(Employee.id)
            .filter(Employee.department_id.in_(context.managed_department_ids))
            .all()
        )
        ids.update(str(row[0]) for row in rows)
    return sorted(ids)


def _assert_can_manage_adjustment(
    db: Session, current_user: Employee, owner_id: str
) -> None:
    """只有申请人本人、其主管/部门负责人或 HR 能读/处理该申请。"""
    context = build_access_context_for_user(current_user, db)
    if str(owner_id) == current_user.id:
        return
    if not context.can_write_score_of(str(owner_id), db=db):
        raise HTTPException(status_code=403, detail="无权处理该计划调整申请")


@router.get("/plan-adjustments")
async def list_leader_plan_adjustments(
    scopeOrgId: str | None = Query(None),
    status: str | None = Query(None),
    periodMonth: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取计划调整申请列表。

    响应结构：PlanAdjustmentItem[]（裸数组）

    ⚠️ **2026-11-26：原实现恒返回 ``[]``** —— 领导端永远看不到员工的调整申请
    ⇒ 整个「计划调整」功能是断的。
    ⇒ 现在从 ``plan_adjustment`` 表**真实读取**。

    ⚠️ **2026-11-27：列表必须按汇报线过滤** —— 否则任何领导都能看到全公司申请。
    """
    context = build_access_context_for_user(current_user, db)
    is_hr = context.has_permission(PermissionAction.VIEW_ALL_SCORES)
    if scopeOrgId and not is_hr and scopeOrgId not in context.managed_department_ids:
        raise HTTPException(status_code=403, detail="无权查看该部门的计划调整")
    return _ok(
        plan_adjustment_service.list_adjustments(
            db,
            owner_ids=_adjustment_owner_scope(db, current_user),
            scope_org_id=scopeOrgId,
            status=status,
            period_month=periodMonth,
        )
    )


@router.get("/plan-adjustments/{adjustment_id}")
async def get_leader_plan_adjustment(
    adjustment_id: str,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """获取计划调整申请详情。

    响应结构：PlanAdjustmentItem
    """
    item = plan_adjustment_service.get_adjustment(db, adjustment_id)
    if item is None:
        raise HTTPException(status_code=404, detail="计划调整申请不存在")
    _assert_can_manage_adjustment(db, current_user, str(item.get("ownerId") or ""))
    return _ok(item)


@router.post("/plan-adjustments/{adjustment_id}/process")
async def process_leader_plan_adjustment(
    adjustment_id: str,
    data: JsonBody,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """处理计划调整申请。

    响应结构：ActionResult

    ⚠️ **2026-11-26：原实现只回一个字符串** —— 状态**根本没变**，
    员工端永远看不到「已处理」。
    ⇒ 现在真的改状态 + 写处理说明 + **把原计划置为暂停/撤销**。

    ⚠️ **2026-11-27：必须校验「这一条」是否在我管辖范围内** ——
    「是主管」不等于「是这条申请的主管」。
    """
    item = plan_adjustment_service.get_adjustment(db, adjustment_id)
    if item is None:
        raise HTTPException(status_code=404, detail="计划调整申请不存在")
    _assert_can_manage_adjustment(db, current_user, str(item.get("ownerId") or ""))
    try:
        row = plan_adjustment_service.process_adjustment(
            db, adjustment_id, dict(data), operator_id=current_user.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="计划调整申请不存在")

    message = "原计划已暂停" if row.status == "PAUSED" else "原计划已撤销"
    return _ok(_action_result(adjustment_id, row.status, message=message))


# ---------------------------------------------------------------------------
# 团队台账
#
# 响应结构：PageResponse<LedgerItem>
# ---------------------------------------------------------------------------


@router.get("/team-ledgers")
async def list_leader_team_ledgers(
    periodMonth: str | None = Query(None, alias="periodMonth"),
    pageNo: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """团队台账分页。

    响应结构：PageResponse<LedgerItem> = { items, total, pageNo, pageSize }

    ⚠️ **2026-11-26：原实现恒返回空分页** ⇒ 团队台账页永远空白。
    ⇒ 现在从**真实业务表**聚合，且**只包含自己的下属**。
    """
    context = build_access_context_for_user(current_user, db)
    employee_ids = sorted(context.reporting_line_ids) or sorted(context.direct_report_ids)
    page = ledger_service.page_ledger(
        db,
        employee_ids=employee_ids or None,
        period_month=periodMonth,
        page_no=pageNo,
        page_size=pageSize,
    )
    return _ok(page)


@router.post("/team-ledgers/export")
async def export_leader_team_ledgers(
    data: dict,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """导出团队台账。

    响应结构：ExportTask

    ⚠️ **2026-11-26：原实现返回空壳任务** ⇒ 现在**真的建任务 + 生成产物**。
    """
    try:
        row = department_config_service.create_export_task(
            db, dict(data), requested_by=current_user.id, dimension_name="团队台账"
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    content, file_name = _build_team_ledger_artifact(db, current_user)
    if content is None:
        department_config_service.mark_export_failed(db, row, "当前团队暂无可导出的台账数据")
    else:
        department_config_service.mark_export_success(
            db,
            row,
            content=content,
            file_name=file_name,
            checksum=department_config_service.checksum_of(content),
        )
    return _ok(department_config_service.serialize_export_task(row))


def _build_team_ledger_artifact(db: Session, current_user: Employee) -> tuple[bytes | None, str]:
    """生成团队台账导出产物（CSV）。"""
    import csv
    import io

    context = build_access_context_for_user(current_user, db)
    employee_ids = sorted(context.reporting_line_ids) or sorted(context.direct_report_ids)
    rows = ledger_service.build_ledger_rows(db, employee_ids=employee_ids or None)
    detail = [r for r in rows if r["id"] != "__summary__"]
    if not detail:
        return None, ""

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["员工", "账号", "部门", "计划数", "成果数", "完成率(%)", "参考分", "逾期数", "缺证据数"])
    for row in detail:
        writer.writerow([
            row["employeeName"],
            row["employeeNo"],
            row["orgName"],
            row["planCount"],
            row["resultCount"],
            row["avgCompletionRatio"],
            row["referenceScore"],
            row["overdueCount"],
            row["missingEvidenceCount"],
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8"), "team-ledger.csv"


# ---------------------------------------------------------------------------
# 响应构造辅助
# ---------------------------------------------------------------------------


def _action_result(object_id: str, status: str, message: str = "") -> dict:
    """构造 ActionResult，字段与 frontend/src/api/performance.ts 保持一致。

    ⚠️ **message 现在有内容**
    前端多处直接 ``ElMessage.success(result.message)``
    ⇒ 原来恒为 ``""`` 时用户看到的是**空提示框**。
    """
    return {
        "objectId": object_id,
        "status": status,
        "message": message,
        "auditActionCode": "",
        "auditDeferred": False,
    }


def _batch_action_results(data: dict, status: str) -> list[dict]:
    """按 ids 批量构造 ActionResult。"""
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    return [_action_result(str(i), status) for i in ids]


def _empty_month_plan_approval_item(approval_id: str) -> dict:
    """构造 MonthPlanApprovalItem，字段与 frontend/src/api/department.ts 保持一致。"""
    return {
        "id": approval_id,
        "planNo": "",
        "ownerId": 0,
        "employeeNo": "",
        "employeeName": "",
        "orgId": 0,
        "orgName": "",
        "planYear": 0,
        "planMonth": 0,
        "deliverable": "",
        "estimatedStartDate": "",
        "estimatedEndDate": "",
        "status": "PENDING",
        "leaderComment": "",
        "aiCheckResult": "",
        "missingFields": [],
        "submittedAt": "",
        "version": 1,
        "items": [],
    }


def _empty_week_plan_summary(plan_id: str) -> dict:
    """构造 WeekPlanSummary，字段与 frontend/src/api/weekPlan.ts 保持一致。"""
    return {
        "id": plan_id,
        "title": "",
        "weekStart": "",
        "weekEnd": "",
        "status": "PENDING",
        "versionNo": 1,
        "ownerUserId": 0,
        "employeeName": "",
        "departmentName": "",
        "itemCount": 0,
    }


def _empty_daily_review_item(review_id: str) -> dict:
    """构造 DailyReviewItem，字段与 frontend/src/api/leader.ts 保持一致。"""
    return {
        "id": review_id,
        "ownerId": 0,
        "employeeNo": "",
        "employeeName": "",
        "orgId": 0,
        "orgName": "",
        "planDate": "",
        "workContent": "",
        "deliverable": "",
        "overdueApproval": False,
        "missingFields": [],
        "aiCheckResult": "",
        "reviewStatus": "PENDING",
        "riskLevel": "NONE",
    }


def _empty_plan_adjustment_item(adjustment_id: str) -> dict:
    """构造 PlanAdjustmentItem，字段与 frontend/src/api/leader.ts 保持一致。"""
    return {
        "id": adjustment_id,
        "originalPlanType": "",
        "originalPlanId": 0,
        "originalPlanNo": "",
        "originalWorkContent": "",
        "newPlanType": "",
        "newPlanId": 0,
        "newPlanNo": "",
        "ownerId": 0,
        "employeeName": "",
        "adjustmentType": "",
        "reason": "",
        "impactText": "",
        "status": "PENDING",
        "keepEvidenceChain": True,
    }


