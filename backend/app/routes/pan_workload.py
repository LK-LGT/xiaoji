"""网盘剪辑工作量 —— 管理 API（BP-2 / P4）。

端点清单（挂在 ``/v1/baidu/workload``）：

    GET    /summary                    面板总览（按人汇总 + 异常 + 待办）
    GET    /daily                      日汇总（按人 / 按月）
    GET    /files                      文件明细（**审计根**）
    GET    /reviews                    复核工单列表
    GET    /reviews/{id}               工单详情（含事件流与链校验）
    POST   /reviews                    发起工单
    POST   /reviews/{id}/items         加争议项
    POST   /reviews/{id}/artifacts     上传证据
    POST   /reviews/{id}/resolve       裁定
    GET    /reviews/{id}/chain         事件链校验
    GET    /manual-metrics             人工指标列表
    POST   /manual-metrics             人工填报（前端上传）
    POST   /aggregate                  月度聚合写 MetricValue
    GET    /bindings                   目录绑定列表
    POST   /bindings                   配目录绑定
    POST   /collect                    手动触发采集

权限
----
- **员工**可发起工单、填报自己的指标、查看自己的数据。
- **主管**可裁定工单、查看下属数据。
- **HR / 管理员**可维护绑定、触发采集、跑月度聚合。

⚠️ **数据范围由后端收敛**
前端权限位只管「谁能打开页面」，
**能看到谁的数据一律由后端按权限判定**（与 ``/v1/qa/tasks`` 同一口径）。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..domain.pan_workload_models import (
    PanAccountBinding,
    PanManualMetric,
    PanManualMetricArtifact,
    PanWorkloadDaily,
    PanWorkloadFile,
    PanWorkloadReview,
    PanWorkloadReviewArtifact,
)
from ..services.access_control import build_access_context
from ..services.department_modules import (
    PAN_WORKLOAD_MODULE,
    has_module,
    module_guard_message,
)
from ..services.pan_manual_metric_service import (
    PanManualMetricError,
    PanManualMetricService,
)
from ..services.pan_workload_review_service import (
    PanWorkloadReviewError,
    PanWorkloadReviewService,
)
from ..services.pan_workload_rules import validate_bindings
from ..services.pan_workload_service import PanWorkloadService

logger = logging.getLogger(__name__)


def _require_pan_module(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> Employee:
    """★ **部门板块守卫** —— 整个 ``/v1/baidu/workload`` 的总闸门。

    ⚠️⚠️ **为什么必须有这层（2026-11-21 用户投诉）**

    用户反馈：「别的部门为什么会有剪辑板块？」

    修复前的状况：``pan:workload:view`` 授予了基础 ``employee`` 角色
    ⇒ **仓储部、客服部**的侧边栏也出现「剪辑工作量」
    ⇒ 点进去发现自己一条数据都没有。

    ⇒ 板块隔离**必须由后端兜底** ——
    前端隐藏菜单只是表象，用户仍可直接敲 URL 访问
    ⇒ 「看不见入口但照样能访问」等于没隔离。

    ⚠️ **为什么做成 router 级依赖**
    逐个端点加守卫 ⇒ 一定会有人漏掉新加的端点
    ⇒ **总闸门在 router 上**，新端点自动受保护。

    ⚠️ **HR / 管理员豁免**（见 ``department_modules.is_admin_or_hr``）
    他们要配置板块、维护绑定、核对全局数据 ⇒ 不能被挡住。

    ⚠️ **判定口径与飞书端共用**（2026-11-21）
    飞书端填报也受同一套板块约束 ⇒ 两处判定若各写一份，
    迟早漂移 ⇒ 提示文案与判定都取自 ``department_modules``。

    Returns:
        通过则返回当前用户；不通过抛 403。
    """
    if not has_module(db, user, PAN_WORKLOAD_MODULE):
        raise HTTPException(
            status_code=403,
            detail=module_guard_message(PAN_WORKLOAD_MODULE),
        )
    return user


router = APIRouter(
    prefix="/v1/baidu/workload",
    tags=["baidu-pan-workload"],
    # ⚠️ **整组端点的板块守卫** —— 见 ``_require_pan_module``。
    dependencies=[Depends(_require_pan_module)],
)

#: 证据上传上限（与服务层一致）
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


# ─────────────────────────────────────────────────────────────────────────
# 请求模型
# ─────────────────────────────────────────────────────────────────────────


class BindingRequest(BaseModel):
    """目录绑定配置。"""

    employee_id: str = Field(min_length=1, max_length=64)
    folder_path: str = Field(min_length=1, max_length=255)
    folder_name: str = Field(default="", max_length=100)
    active: bool = True


class ReviewCreateRequest(BaseModel):
    """发起复核工单。"""

    business_date: str = Field(min_length=10, max_length=10)
    claimed_count: int = Field(ge=0, le=10000)
    reason: str = Field(min_length=1, max_length=2000)
    #: 裁定粒度：``day``（按天）/ ``file``（逐文件）
    scope: str = Field(default="day", pattern="^(day|file)$")


class ReviewItemRequest(BaseModel):
    """加争议项。"""

    #: 勾选系统已列出的文件（**可空** —— 空则用手填）
    pan_workload_file_id: str = Field(default="", max_length=64)
    #: 手填文件名（系统没扫到时用）
    manual_name: str = Field(default="", max_length=255)
    manual_description: str = Field(default="", max_length=2000)
    #: 争议类型：``missing`` / ``overcounted`` / ``weight`` / ``other``
    dispute_type: str = Field(default="other", pattern="^(missing|overcounted|weight|other)$")
    claimed_weight: int = Field(default=1, ge=0, le=100)


class ReviewResolveRequest(BaseModel):
    """裁定工单。"""

    resolution: str = Field(pattern="^(approved|rejected)$")
    #: 按天裁定时必填；逐文件裁定可留空（自动算）
    final_count: int | None = Field(default=None, ge=0, le=10000)
    resolution_note: str = Field(default="", max_length=2000)


class ItemResolveRequest(BaseModel):
    """裁定单个争议项。"""

    resolution: str = Field(pattern="^(upheld|rejected|need_more)$")
    final_weight: int | None = Field(default=None, ge=0, le=100)
    resolution_note: str = Field(default="", max_length=2000)


class ManualMetricRequest(BaseModel):
    """人工填报。"""

    metric_date: str = Field(min_length=10, max_length=10)
    metric_code: str = Field(min_length=1, max_length=64)
    metric_value: float = Field(ge=0.0, le=1.0)
    #: 填报粒度：``day`` / ``file``
    scope: str = Field(default="day", pattern="^(day|file)$")
    target_key: str = Field(default="", max_length=64)
    remark: str = Field(default="", max_length=255)


class AggregateRequest(BaseModel):
    """月度聚合。"""

    period: str = Field(default="", max_length=16)
    employee_ids: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# 权限
# ─────────────────────────────────────────────────────────────────────────


def _ctx(db: Session, user: Employee):
    return build_access_context(db, user)


def _is_admin(ctx) -> bool:
    return bool({"hr", "admin"} & set(ctx.roles))


def _is_leader(ctx) -> bool:
    """能否**裁定**复核工单。

    ⚠️ **必须含 ``manager``** —— 直属主管也是裁定人。
    真实组织里剪辑的直属上级是**张洁（编导）**，她不是部门 leader
    （短视频部 leader 是刘旭）⇒ 旧逻辑只认 ``department_leader``
    ⇒ 张洁**裁不了**自己下属的工单 ⇒ 主链路断了。

    ⇒ 判定口径 = 「有下属」或「管着一个部门」或「HR / 管理员」。
    """
    if {"hr", "admin", "department_leader"} & set(ctx.roles):
        return True
    # ⚠️ 直属下属 / 汇报线全链下属 / 代理授权 —— 任一非空即是主管
    return bool(
        getattr(ctx, "direct_report_ids", None)
        or getattr(ctx, "reporting_line_ids", None)
        or getattr(ctx, "delegated_employee_ids", None)
    )


def _require_admin(db: Session, user: Employee) -> None:
    """要求 HR / 系统管理员。"""
    if not _is_admin(_ctx(db, user)):
        raise HTTPException(
            status_code=403, detail="仅HR与系统管理员可维护网盘工作量配置"
        )


def _require_leader(db: Session, user: Employee) -> None:
    """要求主管 / HR / 管理员。"""
    if not _is_leader(_ctx(db, user)):
        raise HTTPException(status_code=403, detail="无权查看该范围的工作量数据")


def _scope_employee_ids(db: Session, user: Employee) -> list[str] | None:
    """算出**当前用户能看谁的数据**。

    Returns:
        ``None`` = 不限（HR / 管理员）；
        列表 = 只能看这些人。

    ⚠️ **员工只看自己** —— 绝不能让人看到同事的条数。

    ⚠️⚠️ **必须含「直属下属 + 汇报线全链下属」**
    真实组织（2026-11-21 查库）：

        运营中心 (leader=杨飞)
        └── 短视频 (leader=刘旭)
            ├── 张洁  编导   ← ★ 两位剪辑的**直属上级**
            │   ├── 李婵雨 剪辑
            │   └── 佟瑶   剪辑

    张洁**不是**部门 leader（leader 是刘旭）⇒ 旧逻辑只查
    ``managed_department_ids`` ⇒ 她**看不到自己的下属** ⇒
    「主管核对下属工作量」这条主链路**根本走不通**。

    ⇒ 这里必须并上 ``direct_report_ids`` 与 ``reporting_line_ids``
      （汇报线全链闭包，主管要能查到「下属的下属」）。
    """
    ctx = _ctx(db, user)
    if {"hr", "admin"} & set(ctx.roles):
        return None

    allowed: set[str] = {user.id}

    # ⚠️ ① 直属下属 + 汇报线全链下属
    allowed.update(getattr(ctx, "direct_report_ids", None) or set())
    allowed.update(getattr(ctx, "reporting_line_ids", None) or set())

    # ⚠️ ② 部门负责人：所管部门（含下级部门）全员
    managed = set(getattr(ctx, "managed_department_ids", None) or [])
    if "department_leader" in ctx.roles and managed:
        rows = db.scalars(
            select(Employee.id).where(Employee.department_id.in_(managed))
        ).all()
        allowed.update(rows)

    # ⚠️ ③ 代理授权范围
    allowed.update(getattr(ctx, "delegated_employee_ids", None) or set())

    # ⚠️ **普通员工 ⇒ 只看自己**
    #   上面加的都是「有下属/有管理权」才有的集合 ⇒ 无管理权者为空。
    if allowed == {user.id}:
        return [user.id]
    return sorted(allowed)


def _check_visible(db: Session, user: Employee, employee_id: str) -> None:
    """确认当前用户能看这个人的数据。"""
    allowed = _scope_employee_ids(db, user)
    if allowed is None:
        return
    if employee_id not in allowed:
        raise HTTPException(status_code=403, detail="无权查看该员工的工作量数据")


# ─────────────────────────────────────────────────────────────────────────
# 序列化
# ─────────────────────────────────────────────────────────────────────────


def _as_date_str(value: Any) -> str | None:
    """把「日期」字段统一成 ``YYYY-MM-DD`` 字符串。

    ⚠️ **为什么需要**
    同一模块里日期列有两种类型：
    - ``PanWorkloadDaily.business_date`` / ``PanWorkloadReview.business_date``
      是 ``Date`` ⇒ 读出来是 ``datetime.date``；
    - ``PanWorkloadFile.business_date`` 是 ``String(16)``
      （文件级业务日期来自目录名解析，未必是合法日期）⇒ 读出来是 ``str``。

    ⇒ 序列化时不能一律 ``.isoformat()``（字符串会 AttributeError ⇒ 500）。
    """
    if value is None:
        return None
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    # ⚠️ 兜底：datetime 之类
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _serialize_daily(row: PanWorkloadDaily) -> dict[str, Any]:
    return {
        "id": row.id,
        "employeeId": row.employee_id,
        "businessDate": row.business_date.isoformat(),
        "totalCount": row.total_count,
        "fileCount": row.file_count,
        "scannedCount": row.scanned_count,
        "excludedCount": row.excluded_count,
        "markerBreakdown": row.marker_breakdown or {},
        "anomalyCount": row.anomaly_count or {},
        "adjustedCount": row.adjusted_count,
        "adjustReason": row.adjust_reason or "",
        "status": row.status,
        "lastSyncedAt": row.last_synced_at.isoformat() if row.last_synced_at else None,
    }


def _serialize_file(row: PanWorkloadFile) -> dict[str, Any]:
    return {
        "id": row.id,
        "employeeId": row.employee_id,
        "panPath": row.pan_path,
        "fileName": row.file_name,
        "extension": row.extension,
        "sizeBytes": row.size_bytes,
        "contentDigest": row.content_digest,
        # ⚠️ **这列是 String(16)，不是 Date**
        # ⇒ 不能无脑 ``.isoformat()``（会 AttributeError ⇒ 500）。
        # 用统一的小工具，两种类型都能处理。
        "businessDate": _as_date_str(row.business_date),
        "counted": row.counted,
        "countWeight": row.count_weight,
        "markers": row.markers or [],
        "excludeReason": row.exclude_reason or "",
        "isDuplicate": row.is_duplicate,
        "duplicateKind": row.duplicate_kind or "",
        "bindType": row.bind_type or "",
    }


def _serialize_review(row: PanWorkloadReview) -> dict[str, Any]:
    return {
        "id": row.id,
        "employeeId": row.employee_id,
        "businessDate": row.business_date.isoformat(),
        "status": row.status,
        "scope": row.scope,
        "claimedCount": row.claimed_count,
        "systemCount": row.system_count,
        "finalCount": row.final_count,
        "reason": row.reason,
        "resolution": row.resolution or "",
        "dailySnapshot": row.daily_snapshot or {},
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def _serialize_item(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "panWorkloadFileId": row.pan_workload_file_id,
        "manualName": row.manual_name,
        "manualDescription": row.manual_description,
        "disputeType": row.dispute_type,
        "claimedWeight": row.claimed_weight,
        "resolution": row.resolution,
        "resolutionNote": row.resolution_note or "",
        "finalWeight": row.final_weight,
    }


def _serialize_artifact(row: PanManualMetricArtifact) -> dict[str, Any]:
    return {
        "id": row.id,
        "metricId": row.metric_id,
        "name": row.name,
        "mediaType": row.media_type,
        "digest": row.digest,
        "sizeBytes": row.size_bytes,
        "source": row.source,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_manual_metric(row: PanManualMetric) -> dict[str, Any]:
    return {
        "id": row.id,
        "employeeId": row.employee_id,
        "metricDate": row.metric_date.isoformat(),
        "metricCode": row.metric_code,
        "metricValue": row.metric_value,
        "scope": row.scope,
        "targetKey": row.target_key,
        "source": row.source,
        "remark": row.remark or "",
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_binding(row: PanAccountBinding) -> dict[str, Any]:
    return {
        "id": row.id,
        "employeeId": row.employee_id,
        "folderPath": row.folder_path,
        "folderName": row.folder_name,
        "active": row.active,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_chain(chain: dict[str, Any]) -> dict[str, Any]:
    """链校验结果 —— **转成 camelCase**。

    ⚠️ **服务层返回 snake_case，路由层统一转**
    服务层 ``verify_chain`` 是通用服务（可能给脚本用），
    保持 snake_case 更贴近 Python 惯例；
    但**对外 API 一律 camelCase** —— 与本模块其他端点一致，
    否则前端按 ``eventCount`` 读会拿到 ``undefined``。
    """
    return {
        "ok": bool(chain.get("ok")),
        "issues": list(chain.get("issues") or []),
        "eventCount": int(chain.get("event_count") or 0),
        "headHash": str(chain.get("head_hash") or ""),
        "guarantee": str(chain.get("guarantee") or ""),
    }


# ─────────────────────────────────────────────────────────────────────────
# 端点 —— 查询
# ─────────────────────────────────────────────────────────────────────────


@router.get("/summary")
def get_summary(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """面板总览 —— **一屏看完**。

    ⚠️ **数据范围由后端收敛** —— 员工只看自己，
    主管看自己管的人，HR 看全部。
    """
    allowed = _scope_employee_ids(db, user)
    svc = PanWorkloadService(db)

    daily_stmt = select(PanWorkloadDaily)
    manual_stmt = select(PanManualMetric)
    review_stmt = select(PanWorkloadReview)
    if allowed is not None:
        daily_stmt = daily_stmt.where(PanWorkloadDaily.employee_id.in_(allowed))
        manual_stmt = manual_stmt.where(PanManualMetric.employee_id.in_(allowed))
        review_stmt = review_stmt.where(PanWorkloadReview.employee_id.in_(allowed))

    dailies = list(db.scalars(daily_stmt))
    manuals = list(db.scalars(manual_stmt))
    reviews = list(db.scalars(review_stmt))

    # ── 按人汇总 ──
    by_emp: dict[str, dict[str, Any]] = {}
    for d in dailies:
        entry = by_emp.setdefault(
            d.employee_id,
            {"count": 0, "days": 0, "adjusted": 0, "disputed": 0, "anomalies": 0},
        )
        value = d.adjusted_count if d.adjusted_count is not None else d.total_count
        entry["count"] += value
        entry["days"] += 1
        if d.adjusted_count is not None:
            entry["adjusted"] += 1
        if d.status == "disputed":
            entry["disputed"] += 1
        entry["anomalies"] += sum((d.anomaly_count or {}).values())

    names = {
        e.id: e.name
        for e in db.scalars(
            select(Employee).where(
                Employee.id.in_(list(by_emp) or ["__none__"])
            )
        ).all()
    }

    return {
        "employees": [
            {
                "employeeId": emp_id,
                "employeeName": names.get(emp_id, emp_id),
                "totalCount": v["count"],
                "days": v["days"],
                "adjustedDays": v["adjusted"],
                "disputedDays": v["disputed"],
                "anomalyCount": v["anomalies"],
            }
            for emp_id, v in sorted(
                by_emp.items(), key=lambda kv: -kv[1]["count"]
            )
        ],
        "manualMetrics": [
            _serialize_manual_metric(m) for m in manuals
        ],
        "pendingReviews": [
            _serialize_review(r) for r in reviews if r.status == "pending"
        ],
        "reviewCount": len(reviews),
        "anomalies": svc.detect_anomalies(),
    }


@router.get("/daily")
def list_daily(
    employee_id: str = "",
    start: str = "",
    end: str = "",
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """日汇总列表。"""
    allowed = _scope_employee_ids(db, user)
    stmt = select(PanWorkloadDaily)
    # ⚠️ **两个条件都要生效** —— `allowed` 是「谁能看」，
    #    `employee_id` 是「看谁」。以前写成了 elif，
    #    导致主管传了 employee_id 就**绕过**了范围限制。
    if allowed is not None:
        stmt = stmt.where(PanWorkloadDaily.employee_id.in_(allowed))
    if employee_id:
        _check_visible(db, user, employee_id)
        stmt = stmt.where(PanWorkloadDaily.employee_id == employee_id)
    if start:
        stmt = stmt.where(PanWorkloadDaily.business_date >= date.fromisoformat(start))
    if end:
        stmt = stmt.where(PanWorkloadDaily.business_date <= date.fromisoformat(end))
    rows = list(db.scalars(stmt.order_by(PanWorkloadDaily.business_date.desc())))
    return {"items": [_serialize_daily(r) for r in rows]}


@router.get("/files")
def list_files(
    employee_id: str,
    business_date: str,
    counted_only: bool = False,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """文件明细 —— **审计根**。

    ⚠️ 主管质疑「为什么是 10 条」时，靠这个端点回溯到具体文件。

    ⚠️ **``business_date`` 是字符串**
    ``PanWorkloadFile.business_date`` 的列类型是 ``String(16)``
    （文件级业务日期来自目录名解析，未必是合法日期）
    ⇒ 查询与序列化都按**字符串**处理，不要 ``date.fromisoformat``。
    """
    _check_visible(db, user, employee_id)
    stmt = select(PanWorkloadFile).where(
        PanWorkloadFile.employee_id == employee_id,
        PanWorkloadFile.business_date == business_date,
    )
    if counted_only:
        stmt = stmt.where(PanWorkloadFile.counted.is_(True))
    rows = list(db.scalars(stmt.order_by(PanWorkloadFile.counted.desc(), PanWorkloadFile.pan_path)))
    return {"items": [_serialize_file(r) for r in rows]}


@router.get("/bindings")
def list_bindings(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """目录绑定列表。"""
    _require_leader(db, user)
    rows = list(db.scalars(select(PanAccountBinding).order_by(PanAccountBinding.folder_path)))
    return {"items": [_serialize_binding(r) for r in rows]}


@router.get("/manual-metrics")
def list_manual_metrics(
    employee_id: str = "",
    start: str = "",
    end: str = "",
    metric_code: str = "",
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """人工指标列表。"""
    allowed = _scope_employee_ids(db, user)
    stmt = select(PanManualMetric)
    if allowed is not None:
        stmt = stmt.where(PanManualMetric.employee_id.in_(allowed))
    if employee_id:
        _check_visible(db, user, employee_id)
        stmt = stmt.where(PanManualMetric.employee_id == employee_id)
    if metric_code:
        stmt = stmt.where(PanManualMetric.metric_code == metric_code)
    if start:
        stmt = stmt.where(PanManualMetric.metric_date >= date.fromisoformat(start))
    if end:
        stmt = stmt.where(PanManualMetric.metric_date <= date.fromisoformat(end))
    rows = list(db.scalars(stmt.order_by(PanManualMetric.metric_date.desc())))
    return {"items": [_serialize_manual_metric(r) for r in rows]}


# ─────────────────────────────────────────────────────────────────────────
# 端点 —— 写操作
# ─────────────────────────────────────────────────────────────────────────


@router.post("/bindings")
def create_binding(
    payload: BindingRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """配目录绑定。

    ⚠️ **同一人多目录、同目录多人**都要挡一下 ——
    配错就是工作量算给错误的人。
    """
    _require_admin(db, user)
    folder = payload.folder_path.strip()
    if not folder.startswith("/"):
        raise HTTPException(status_code=400, detail="目录路径必须以 / 开头")

    existing = db.scalar(
        select(PanAccountBinding).where(PanAccountBinding.folder_path == folder)
    )
    if existing is not None and existing.employee_id != payload.employee_id:
        raise HTTPException(
            status_code=409, detail=f"目录 {folder} 已绑定给其他员工"
        )
    if existing is None:
        existing = PanAccountBinding(folder_path=folder)
        db.add(existing)
    existing.employee_id = payload.employee_id
    existing.folder_name = payload.folder_name or folder.rsplit("/", 1)[-1]
    existing.active = payload.active

    # ⚠️⚠️ **必须校验「前缀互不冲突」** —— 这是归属正确性的地基
    #
    # 归属判定 = ``file_path.startswith(folder_path)``（见
    # ``pan_workload_rules.resolve_owner``）。
    # ⇒ ``/成品`` 与 ``/成品-小瑶`` **互为前缀** ⇒ 同一个文件会命中两条绑定
    #    ⇒ 采集时判为 ``ambiguous``（归属为空，不落任何人），
    #    或者在别的实现里「取第一条」⇒ **同一份工作量算给两个人**。
    #
    # ⚠️ 这段校验在**服务层**（``validate_bindings``）已经写好了，
    #    但**路由层没调用** ⇒ 配错绑定不会报错，只会在采集时静默算错。
    # ⇒ 这里补上：把「将要生效的全部绑定」一起送进校验器。
    active_bindings = [
        {
            "employee_id": b.employee_id,
            "folder_path": b.folder_path,
            "active": b.active,
        }
        for b in db.scalars(select(PanAccountBinding)).all()
        if b.id != existing.id
    ]
    active_bindings.append(
        {
            "employee_id": existing.employee_id,
            "folder_path": existing.folder_path,
            "active": existing.active,
        }
    )
    conflicts = validate_bindings(active_bindings)
    if conflicts:
        raise HTTPException(status_code=409, detail="；".join(conflicts))

    db.commit()
    return {"item": _serialize_binding(existing)}


@router.post("/collect")
def trigger_collect(
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """手动触发采集。

    ⚠️ **必须 HR / 管理员** —— 采集会打网盘接口（有频控），
    不能让随便谁点一下就跑一轮。
    """
    _require_admin(db, user)
    svc = PanWorkloadService(db)
    try:
        result = svc.collect(trigger_type="manual")
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("手动采集失败")
        raise HTTPException(status_code=502, detail=f"采集失败：{exc}") from exc
    return result


@router.post("/manual-metrics")
def submit_manual_metric(
    payload: ManualMetricRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """人工填报（**前端上传通路**）。

    ⚠️ **只能填自己的** —— 不允许替别人填。
    """
    svc = PanManualMetricService(db)
    try:
        row = svc.submit(
            employee_id=user.id,
            metric_date=payload.metric_date,
            metric_code=payload.metric_code,
            metric_value=payload.metric_value,
            scope=payload.scope,
            target_key=payload.target_key,
            source="frontend",
            created_by=user.id,
            remark=payload.remark,
        )
        db.commit()
    except PanManualMetricError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"item": _serialize_manual_metric(row)}


@router.post("/aggregate")
def aggregate(
    payload: AggregateRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """月度聚合 → 写 MetricValue。

    ⚠️ **必须 HR / 管理员** —— 这会进算分集。
    """
    _require_admin(db, user)
    svc = PanManualMetricService(db)
    result = svc.aggregate_month(
        period=payload.period, employee_ids=payload.employee_ids or None
    )
    db.commit()
    return result


# ─────────────────────────────────────────────────────────────────────────
# 端点 —— 复核工单
# ─────────────────────────────────────────────────────────────────────────


@router.get("/reviews")
def list_reviews(
    employee_id: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """工单列表。"""
    allowed = _scope_employee_ids(db, user)
    svc = PanWorkloadReviewService(db)
    rows = svc.list_reviews(employee_id=employee_id, status=status)
    # ⚠️ **范围过滤必须在服务端做** —— 不能把别人的工单发给前端再过滤
    if allowed is not None:
        rows = [r for r in rows if r.employee_id in allowed]
    return {"items": [_serialize_review(r) for r in rows]}


@router.get("/reviews/{review_id}")
def get_review(
    review_id: str,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """工单详情（含争议项、附件、事件流、链校验）。"""
    svc = PanWorkloadReviewService(db)
    review = svc.get_review(review_id)
    _check_visible(db, user, review.employee_id)
    detail = svc.review_detail(review_id)
    return {
        "review": _serialize_review(review),
        "items": [_serialize_item(i) for i in detail["items"]],
        "artifacts": [
            {
                "id": a.id,
                "reviewItemId": a.review_item_id,
                "name": a.name,
                "mediaType": a.media_type,
                "digest": a.digest,
                "sizeBytes": a.size_bytes,
            }
            for a in detail["artifacts"]
        ],
        "events": [
            {
                "seq": e.seq,
                "eventType": e.event_type,
                "actorId": e.actor_id,
                "payload": e.payload,
                "prevHash": e.prev_hash,
                "eventHash": e.event_hash,
                "createdAt": e.created_at.isoformat() if e.created_at else None,
            }
            for e in detail["events"]
        ],
        "chain": _serialize_chain(detail["chain"]),
    }

@router.get("/reviews/{review_id}/chain")
def get_chain(
    review_id: str,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """事件链校验结果。"""
    svc = PanWorkloadReviewService(db)
    review = svc.get_review(review_id)
    _check_visible(db, user, review.employee_id)
    return _serialize_chain(svc.verify_chain(review_id))


@router.post("/reviews")
def create_review(
    payload: ReviewCreateRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """发起复核工单（**只能给自己发起**）。"""
    svc = PanWorkloadReviewService(db)
    try:
        review = svc.create_review(
            employee_id=user.id,
            business_date=payload.business_date,
            claimed_count=payload.claimed_count,
            reason=payload.reason,
            scope=payload.scope,
            created_by=user.id,
        )
        db.commit()
    except PanWorkloadReviewError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"item": _serialize_review(review)}


@router.post("/reviews/{review_id}/items")
def add_review_item(
    review_id: str,
    payload: ReviewItemRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """加争议项。"""
    svc = PanWorkloadReviewService(db)
    review = svc.get_review(review_id)
    _check_visible(db, user, review.employee_id)
    try:
        item = svc.add_item(
            review_id,
            pan_workload_file_id=payload.pan_workload_file_id,
            manual_name=payload.manual_name,
            manual_description=payload.manual_description,
            dispute_type=payload.dispute_type,
            claimed_weight=payload.claimed_weight,
            actor_id=user.id,
        )
        db.commit()
    except PanWorkloadReviewError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"item": _serialize_item(item)}


@router.post("/reviews/{review_id}/artifacts")
async def add_review_artifact(
    review_id: str,
    file: UploadFile = File(...),
    review_item_id: str = Form(default=""),
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """上传证据附件（**前端上传通路**）。"""
    svc = PanWorkloadReviewService(db)
    review = svc.get_review(review_id)
    _check_visible(db, user, review.employee_id)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="附件过大（上限 20MB）")
    try:
        artifact = svc.add_artifact(
            review_id,
            name=file.filename or "evidence.bin",
            media_type=file.content_type or "application/octet-stream",
            content=content,
            review_item_id=review_item_id,
            actor_id=user.id,
        )
        db.commit()
    except PanWorkloadReviewError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {
        "item": {
            "id": artifact.id,
            "name": artifact.name,
            "mediaType": artifact.media_type,
            "digest": artifact.digest,
            "sizeBytes": artifact.size_bytes,
        }
    }


@router.get("/reviews/{review_id}/artifacts/{artifact_id}")
def download_review_artifact(
    review_id: str,
    artifact_id: str,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> Any:
    """下载证据附件。

    ⚠️ **必须校验归属** —— 否则知道 id 就能下别人的证据。
    """
    svc = PanWorkloadReviewService(db)
    review = svc.get_review(review_id)
    _check_visible(db, user, review.employee_id)
    artifact = db.get(PanWorkloadReviewArtifact, artifact_id)
    if artifact is None or artifact.review_id != review_id:
        raise HTTPException(status_code=404, detail="附件不存在")
    from fastapi.responses import Response

    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f'inline; filename="{artifact.name}"',
            "X-Content-Digest": artifact.digest,
        },
    )


@router.post("/reviews/{review_id}/items/{item_id}/resolve")
def resolve_review_item(
    review_id: str,
    item_id: str,
    payload: ItemResolveRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """裁定单个争议项 —— **主管专属**。"""
    _require_leader(db, user)
    svc = PanWorkloadReviewService(db)
    # ⚠️ **先确认工单存在** —— 不存在直接 404（而不是等 item 找不到时报 404）。
    svc.get_review(review_id)
    # ⚠️⚠️ **必须校验「争议项属于这个工单」**（2026-11-23 修的真实漏洞）
    # ------------------------------------------------------------------
    # 路由签名是 ``/reviews/{review_id}/items/{item_id}/resolve``，
    # 但 ``svc.resolve_item()`` **只按 item_id 找项** ⇒
    # ⚠️ **review_id 形同虚设**：
    #    主管 A 拿自己工单的 review_id + 别人工单的 item_id 就能串门裁定。
    #
    # ⇒ 显式比对 ``item.review_id == review_id``，对不上直接 404。
    try:
        item = svc.resolve_item(
            item_id,
            resolution=payload.resolution,
            final_weight=payload.final_weight,
            resolution_note=payload.resolution_note,
            actor_id=user.id,
            expect_review_id=review_id,
        )
        db.commit()
    except PanWorkloadReviewError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"item": _serialize_item(item)}


@router.post("/reviews/{review_id}/resolve")
def resolve_review(
    review_id: str,
    payload: ReviewResolveRequest,
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """裁定工单 —— **主管专属**。

    ⚠️ 员工**不能**自己裁定自己的工单。
    """
    _require_leader(db, user)
    svc = PanWorkloadReviewService(db)
    try:
        review = svc.resolve_review(
            review_id,
            resolution=payload.resolution,
            final_count=payload.final_count,
            resolution_note=payload.resolution_note,
            actor_id=user.id,
        )
        db.commit()
    except PanWorkloadReviewError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"item": _serialize_review(review)}


@router.post("/manual-metrics/{metric_id}/artifacts")
async def add_manual_metric_artifact(
    metric_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> dict[str, Any]:
    """给人工指标上传证据（**前端上传通路**）。"""
    svc = PanManualMetricService(db)
    row = db.get(PanManualMetric, metric_id)
    if row is None:
        raise HTTPException(status_code=404, detail="指标不存在")
    if row.employee_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作该指标")
    content = await file.read()
    try:
        artifact = svc.add_artifact(
            metric_id,
            name=file.filename or "evidence.bin",
            media_type=file.content_type or "application/octet-stream",
            content=content,
            source="frontend",
            uploaded_by=user.id,
        )
        db.commit()
    except PanManualMetricError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"item": _serialize_artifact(artifact)}
