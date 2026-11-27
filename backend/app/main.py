"""业务后端 API —— 唯一事实源。

对外暴露的都是业务语义接口（发起考核、提交自评、生成草稿、确认、查分），
智能体层只通过内部契约被调用。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import dependencies, exceptions, schemas
from .core.config import settings
from .core.db import get_db, init_db
from .domain.enums import SubmitSource
from .domain.models import (
    AssessmentRecord,
    AssessmentTask,
    CommentDraft,
    ConfigSnapshot,
    Employee,
    FormTemplate,
    Metric,
    RatingDraft,
    Rubric,
    Score,
    WeightSet,
)
from .services import access_control, evidence_service, qa_service, review_service
from .services.access_control import PermissionAction, build_access_context
from .services.review_service import InvalidState, NotFound

# ─────────────────────────────────────────────────────────────
# 日志配置
#
# 为什么必须有：模块里到处用 ``logging.getLogger(__name__)`` 记录
# 关键事件（同步批次、锁冲突、事务边界），但默认 logging 配置下
# root logger 没有 handler，这些日志**一条都不会输出** ——
# 出问题时（批次卡在 running）完全无从查起。
#
# 这里显式配置，保证应用日志落到 stdout（容器/进程管理器可收集）。
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

#: 模块级 logger。
#:
#: ⚠️ 之前这里**没有**定义 logger，而下面的算分失败分支用了
#: ``logger.error(...)`` —— 一旦规则引擎算分失败，就会抛
#: ``NameError: name 'logger' is not defined``，
#: 把「算分失败」变成「接口 500」，掩盖真实原因。
#: 由 ruff 的 F821（undefined name）查出。
logger = logging.getLogger(__name__)

from .routes.schemes import router as schemes_router  # noqa: E402

app = FastAPI(
    title="员工绩效管理 - 业务后端",
    version="0.1.0",
    description="唯一事实源。数字只在这里产生；AI 只做判断与写作。",
)


app.include_router(schemes_router)
from .routes.review_center import router as review_center_router  # noqa: E402

app.include_router(review_center_router)


@app.middleware("http")
async def _redact_sensitive_response_headers(request: Request, call_next):
    """兜底：确保任何响应都不会回显凭据相关响应头。"""
    response = await call_next(request)
    for header in ("X-Feishu-Secret", "X-Auth-Token", "X-Authorization", "X-Tenant-Token"):
        if header in response.headers:
            del response.headers[header]
    return response

# ──────────────────────────────────────────────────────────────
# 全局异常处理
# 领域异常（BaseAppError 子类）统一转换为正确的 HTTP 状态码，
# 否则路由内未捕获时会退化成 500，掩盖真实的权限/校验问题。
# ──────────────────────────────────────────────────────────────


@app.exception_handler(exceptions.BaseAppError)
async def _handle_base_app_error(request: Request, exc: exceptions.BaseAppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.message,
            "detail": exc.message,
        },
    )


# ┌─────────────────────────────────────────────────────────────┐
# │ 注册路由（【板块3】自评接口）                              │
# └─────────────────────────────────────────────────────────────┘

try:
    from .routes import self_review as self_review_routes
    app.include_router(self_review_routes.router)
    print("[APP] Self-review router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register self-review router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import feishu as feishu_routes
    app.include_router(feishu_routes.router)
    print("[APP] Feishu router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register feishu router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import feishu_approval as feishu_approval_routes
    app.include_router(feishu_approval_routes.router)
    print("[APP] Feishu approval router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register feishu approval router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import feishu_contact as feishu_contact_routes
    app.include_router(feishu_contact_routes.router)
    print("[APP] Feishu contact router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register feishu contact router: {e}")
    import traceback
    traceback.print_exc()

try:
    # FS-8：飞书绩效方案来源绑定与自动同步
    from .routes import feishu_scheme as feishu_scheme_routes
    app.include_router(feishu_scheme_routes.router)
    print("[APP] Feishu scheme router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register feishu scheme router: {e}")
    import traceback
    traceback.print_exc()

try:
    # BP-1：百度网盘绩效校验（取数 → 计数快照 → 主管核对）
    from .routes import baidu_pan as baidu_pan_routes
    app.include_router(baidu_pan_routes.router)
    print("[APP] Baidu pan router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register baidu pan router: {e}")
    import traceback
    traceback.print_exc()

try:
    # BP-2：网盘剪辑工作量（采集 → 日汇总 → 复核工单 → 人工填报）
    from .routes import pan_workload as pan_workload_routes
    app.include_router(pan_workload_routes.router)
    print("[APP] Pan workload router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register pan workload router: {e}")
    import traceback
    traceback.print_exc()

try:
    # ★ 部门工作量（2026-11-25）—— 各部门通用的填报板块
    # ⚠️ **独立 router + 独立板块守卫**（``dept_workload``）
    # ⇒ 各部门与短视频**互不影响**，
    #    也**不会**让别的部门碰到百度网盘那套接口。
    from .routes import dept_workload as dept_workload_routes
    app.include_router(dept_workload_routes.router)
    print("[APP] Dept workload router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register dept workload router: {e}")
    import traceback
    traceback.print_exc()

try:
    # ★ 飞书表格填报（2026-11-21）——「剪辑以表格形式填写」
    # ⚠️ **挂在 ``/v1/baidu/workload`` 前缀下** ⇒ 自动继承该板块的守卫
    # （部门没开通「剪辑工作量」⇒ 这个 API 也 403）
    from .routes import pan_workload_feishu_table as pan_feishu_table_routes
    app.include_router(pan_feishu_table_routes.router)
    print("[APP] Pan workload feishu table router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register pan feishu table router: {e}")
    import traceback
    traceback.print_exc()

try:
    # 部门功能板块隔离（2026-11-21）—— 管理「哪个部门有哪些板块」
    from .routes import department_modules as department_modules_routes
    app.include_router(department_modules_routes.router)
    print("[APP] Department modules router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register department modules router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import import_data as import_data_routes
    app.include_router(import_data_routes.router)
    print("[APP] Import data router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register import data router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import organizations as organizations_routes
    app.include_router(organizations_routes.router)
    print("[APP] Organizations router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register organizations router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import performance as performance_routes
    app.include_router(performance_routes.router)
    print("[APP] Performance router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register performance router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import department as department_routes
    app.include_router(department_routes.router)
    print("[APP] Department router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register department router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import auth as auth_routes
    app.include_router(auth_routes.router)
    print("[APP] Auth router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register auth router: {e}")
    import traceback
    traceback.print_exc()

try:
    # FS-1/FS-2：飞书免登 OAuth 路由
    from .routes import feishu_auth as feishu_auth_routes
    app.include_router(feishu_auth_routes.router)
    print("[APP] Feishu auth router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register feishu auth router: {e}")
    import traceback
    traceback.print_exc()

try:
    # FS-3：飞书机器人会话填表路由
    from .routes import feishu_bot as feishu_bot_routes
    app.include_router(feishu_bot_routes.router)
    print("[APP] Feishu bot router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register feishu bot router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import messages as messages_routes
    app.include_router(messages_routes.router)
    print("[APP] Messages router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register messages router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import leader as leader_routes
    app.include_router(leader_routes.router)
    print("[APP] Leader router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register leader router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import employee as employee_routes
    app.include_router(employee_routes.router)
    print("[APP] Employee router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register employee router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import employee_plans as employee_plans_routes
    app.include_router(employee_plans_routes.router)
    print("[APP] Employee plans router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register employee plans router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import dispute as dispute_routes
    app.include_router(dispute_routes.router)
    print("[APP] Dispute router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register dispute router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import planning as planning_routes
    app.include_router(planning_routes.router)
    print("[APP] Planning router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register planning router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import employee_ai as employee_ai_routes
    app.include_router(employee_ai_routes.router)
    print("[APP] Employee AI router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register employee AI router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import employee_week_plan as employee_week_plan_routes
    app.include_router(employee_week_plan_routes.router)
    print("[APP] Employee week-plan router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register employee week-plan router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import system_ai as system_ai_routes
    app.include_router(system_ai_routes.router)
    print("[APP] System AI router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register system AI router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import leader_suggestion as leader_suggestion_routes
    app.include_router(leader_suggestion_routes.router)
    print("[APP] Leader suggestion/package router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register leader suggestion router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import employee_result as employee_result_routes
    app.include_router(employee_result_routes.router)
    print("[APP] Employee result router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register employee result router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import ai_review as ai_review_routes
    app.include_router(ai_review_routes.router)
    print("[APP] AI review router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register AI review router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import delegation as delegation_routes
    app.include_router(delegation_routes.router)
    print("[APP] Delegation router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register delegation router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import system_users as system_users_routes
    app.include_router(system_users_routes.router)
    print("[APP] System users router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register system users router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes import department_confirm as department_confirm_routes
    app.include_router(department_confirm_routes.router)
    print("[APP] Department confirm/approval router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register department confirm router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import system as system_routes
    app.include_router(system_routes.router)
    print("[APP] System router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register system router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import system_config as system_config_routes
    app.include_router(system_config_routes.router)
    print("[APP] System config router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register system config router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import attendance as attendance_routes
    app.include_router(attendance_routes.router)
    print("[APP] Attendance router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register attendance router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import assessment_records as assessment_records_routes
    app.include_router(assessment_records_routes.router)
    print("[APP] Assessment records router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register assessment records router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import rules_engine as rules_engine_routes
    app.include_router(rules_engine_routes.router)
    print("[APP] Rules engine router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register rules engine router: {e}")
    import traceback
    traceback.print_exc()

try:
    from .routes.v1 import assessment_config as assessment_config_routes
    app.include_router(assessment_config_routes.router)
    print("[APP] Assessment config router registered successfully")
except Exception as e:
    print(f"[APP] WARNING: Failed to register assessment config router: {e}")
    import traceback
    traceback.print_exc()

# 直接在 main.py 中也注册一份（确保生效）
try:
    from .schemas_self_review import SelfReviewSubmitRequest, SelfReviewSubmitResponse
    from .services import self_review_service

    @app.post(
        "/v1/assessment/records/{record_id}/self-review",
        response_model=SelfReviewSubmitResponse,
        status_code=201,
        tags=["SelfReview"],
        summary="【板块3】员工提交自评",
    )
    async def submit_self_review_direct(
        record_id: str,
        request: SelfReviewSubmitRequest,
        current_user: Employee = Depends(dependencies.get_current_user),
        db: Session = Depends(get_db),
    ):
        try:
            dependencies.check_can_submit_review(current_user.id, record_id, db)
            result_data = self_review_service.submit_self_review(
                db,
                record_id=record_id,
                current_employee=current_user,
                req=request,
            )
            return SelfReviewSubmitResponse(
                code=0, message="自评已提交成功", data=result_data
            )
        except exceptions.PermissionDenied as exc:
            raise HTTPException(status_code=403, detail={"code": exc.code, "message": exc.message}) from exc
        except exceptions.RecordNotFound as exc:
            raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc
        except exceptions.InvalidStateTransition as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
        except exceptions.DataValidationError as exc:
            raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Error: {exc}", exc_info=True)
            db.rollback()
            raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "服务器内部错误"}) from exc

    print("[APP] Self-review endpoint registered in main.py")
except Exception as e:
    print(f"[APP] WARNING: Failed to register self-review endpoint: {e}")
    import traceback
    traceback.print_exc()

@app.on_event("startup")
def _startup() -> None:
    init_db()
    from .services.notification_scheduler import start as start_notifications

    start_notifications()
    try:
        from .services.feishu_scheduler import start_scheduler
        start_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("启动飞书定时同步失败")
    try:
        from .services.org_event_scheduler import start_consumer
        start_consumer()
    except Exception:
        logging.getLogger(__name__).exception("启动飞书事件消费调度器失败")
    try:
        from .services.period_scheduler_runner import start as start_period_scheduler
        start_period_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("启动考核周期调度失败")
    try:
        from .services.feishu_scheme_scheduler import start as start_feishu_scheme_scheduler
        start_feishu_scheme_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("启动飞书方案自动同步失败")
    try:
        from .services.baidu_pan_scheduler import start as start_baidu_pan_scheduler
        start_baidu_pan_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("启动百度网盘绩效校验失败")
    try:
        from .services.pan_workload_scheduler import start as start_pan_workload_scheduler
        start_pan_workload_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("启动网盘剪辑工作量统计失败")
    try:
        # ★ 飞书表格填报的定时同步（2026-11-21）
        # ⚠️ **默认启动** —— 只要绑定了表格就该自动同步；
        #    没绑定时内部会静默跳过（预期状态，不是错误）。
        from .services.feishu_metric_scheduler import start as start_feishu_metric_scheduler
        start_feishu_metric_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("启动飞书表格同步失败")
    try:
        # ★ 每日填报提醒推送（2026-11-23）
        # ⚠️ **默认关闭**（配置项 ``feishu_daily_push_enabled``）——
        #    主动推送会打扰用户，必须业务显式开启。
        from .services.feishu_daily_push_scheduler import (
            start as start_feishu_daily_push_scheduler,
        )
        start_feishu_daily_push_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("启动每日填报推送失败")
    try:
        # ★ 飞书审批回流定时补偿（2026-11-24，FS-7）
        # ⚠️ **默认关闭**（配置项 ``feishu_approval_compensation_enabled``）——
        #    补偿会主动调飞书接口并写入业务状态，必须业务显式开启。
        from .services.feishu_approval_compensation_scheduler import (
            start as start_feishu_approval_compensation,
        )
        start_feishu_approval_compensation()
    except Exception:
        logging.getLogger(__name__).exception("启动飞书审批补偿失败")


@app.on_event("shutdown")
def _shutdown() -> None:
    """停掉定时同步线程。

    ⚠️ 为什么必须有：``feishu_scheduler`` 的线程是 **daemon**，
    没有显式停止的话，它会在进程里一直跑 —— 开发期热重载、
    测试里反复创建 app、容器滚动更新时，都可能留下
    「旧 app 的线程还在跑、同时新 app 又起了一个」的重复调度。

    停线程只置停止事件并短暂等待；**不**强制 join 到超时，
    因为当前这一轮同步可能正在执行（可能要几十秒），
    强杀会打断批次落库。daemon 线程在进程退出时会被自动回收。
    """
    from .services.notification_scheduler import stop as stop_notifications

    stop_notifications()
    try:
        from .services.feishu_scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("停止飞书定时同步失败")
    try:
        from .services.org_event_scheduler import stop_consumer
        stop_consumer()
    except Exception:
        logging.getLogger(__name__).exception("停止飞书事件消费调度器失败")
    try:
        from .services.period_scheduler_runner import stop as stop_period_scheduler
        stop_period_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("停止考核周期调度失败")
    try:
        from .services.feishu_scheme_scheduler import stop as stop_feishu_scheme_scheduler
        stop_feishu_scheme_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("停止飞书方案自动同步失败")
    try:
        from .services.baidu_pan_scheduler import stop as stop_baidu_pan_scheduler
        stop_baidu_pan_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("停止百度网盘绩效校验失败")
    try:
        from .services.pan_workload_scheduler import stop as stop_pan_workload_scheduler
        stop_pan_workload_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("停止网盘剪辑工作量统计失败")
    try:
        # ★ 飞书表格填报的定时同步（2026-11-21）
        from .services.feishu_metric_scheduler import stop as stop_feishu_metric_scheduler
        stop_feishu_metric_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("停止飞书表格同步失败")
    try:
        # ★ 每日填报提醒推送（2026-11-23）
        from .services.feishu_daily_push_scheduler import (
            stop as stop_feishu_daily_push_scheduler,
        )
        stop_feishu_daily_push_scheduler()
    except Exception:
        logging.getLogger(__name__).exception("停止每日填报推送失败")
    try:
        # ★ 飞书审批回流定时补偿（2026-11-24，FS-7）
        from .services.feishu_approval_compensation_scheduler import (
            stop as stop_feishu_approval_compensation,
        )
        stop_feishu_approval_compensation()
    except Exception:
        logging.getLogger(__name__).exception("停止飞书审批补偿失败")


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class CreateTaskReq(BaseModel):
    name: str
    period: str
    department_id: str | None = None
    position: str | None = None
    owner_id: str | None = None


class SubmitSelfReviewReq(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    source: SubmitSource
    dedup_key: str | None = None


class MetricValueReq(BaseModel):
    employee_id: str
    metric_code: str
    period: str
    value: float
    target: float | None = None
    unit: str | None = None
    source: str
    source_ref: str | None = None
    as_of: datetime | None = None


class AnomalyReq(BaseModel):
    employee_id: str
    period: str
    anomaly_type: str
    count: int = 1
    as_of: datetime | None = None
    approved: bool | None = None
    detail: str | None = None


class ConfirmReq(BaseModel):
    confirmed_by: str
    confirmed_rating: str | None = None


# ---------------------------------------------------------------------------
# 基础查询
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "agent_service_url": settings.agent_service_url,
        "rating_policy": settings.rating_policy,
    }


def _period_display(period: str | None) -> str | None:
    """把紧凑周期 `202611` 转成展示用的 `2026-11`。"""
    from .services.period import to_dashed_period

    return to_dashed_period(period)


def _require_employee_management(current_user: Employee, db: Session) -> None:
    """要求「员工管理」权限，否则 403。

    员工增删改属于 HR / 管理员动作，普通员工与主管不应能改别人档案。
    权限来源与前端菜单一致（`access_control` 唯一事实源）。
    """
    access = access_control.build_access_context(db, current_user)
    if not access.has_permission(PermissionAction.MANAGE_EMPLOYEES):
        raise HTTPException(status_code=403, detail="无权管理员工信息")


@app.get("/api/employees")
def list_employees(
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.execute(select(Employee)).scalars().all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "department_id": e.department_id,
            "position": e.position,
            "manager_id": e.manager_id,
        }
        for e in rows
    ]


@app.get("/api/records/{record_id}")
def get_record(
    record_id: str,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查一条考核记录的全貌：分数、草稿、证据。

    证据按**考核周期**过滤：考勤证据的周期写在 source_ref 里，
    必须用考核任务自身的 period 去筛，否则评 10 月绩效会混进 11 月的考勤异常。
    """
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")

    # 数据范围校验：本条记录是**谁的**成绩，只有有权限的人能看。
    # 之前这里完全没校验，任何登录用户都能查任意人的分数与证据。
    access = access_control.build_access_context(db, current_user)
    if not access.can_view_score_of(rec.employee_id):
        raise HTTPException(
            status_code=403,
            detail="无权查看该员工的绩效数据",
        )

    # 取考核任务的周期（紧凑格式 YYYYMM），用于过滤考勤证据
    task = db.get(AssessmentTask, rec.task_id)
    period = task.period if task is not None else None

    score = db.execute(
        select(Score).where(Score.record_id == record_id)
    ).scalar_one_or_none()
    rating = db.execute(
        select(RatingDraft)
        .where(RatingDraft.record_id == record_id)
        .order_by(RatingDraft.created_at.desc())
    ).scalars().first()
    comment = db.execute(
        select(CommentDraft)
        .where(CommentDraft.record_id == record_id)
        .order_by(CommentDraft.created_at.desc())
    ).scalars().first()

    emp = db.get(Employee, rec.employee_id)
    # 按考核周期过滤证据，避免跨月考勤异常混入
    ev_rows = evidence_service.list_scoring_evidence(
        db, rec.employee_id, period=period
    )
    scoring_ev, reference_ev = evidence_service.split_by_usage(ev_rows)

    return {
        "record_id": record_id,
        "status": rec.status,
        "period": period,
        "period_display": _period_display(period),
        "employee": {"id": emp.id, "name": emp.name, "position": emp.position} if emp else None,
        "submit_source": rec.submit_source,
        "confirmed_rating": rec.confirmed_rating,
        "score": (
            {
                "total": score.total,
                "per_metric": score.per_metric,
                "computed_at": score.computed_at.isoformat(),
            }
            if score
            else None
        ),
        "rating_draft": (
            {
                "ratings": rating.ratings_json,
                "citations": rating.citations,
                "gaps": rating.gaps_json,
                "fairness": rating.fairness_json,
                "status": rating.status,
            }
            if rating
            else None
        ),
        "comment_draft": (
            {"markdown": comment.markdown, "citations": comment.citations, "status": comment.status}
            if comment
            else None
        ),
        "evidence": {
            "scoring": [
                {"id": e.id, "summary": e.summary, "impact": e.impact, "source": e.source}
                for e in scoring_ev
            ],
            # 决策②：参考类证据（考勤）单独返回，带免责标注
            "reference_only": evidence_service.build_attendance_view(reference_ev),
        },
    }


# ---------------------------------------------------------------------------
# 考核任务
# ---------------------------------------------------------------------------


@app.post("/v1/assessment/tasks", tags=["Task"])
def create_assessment_task(
    request: schemas.AssessmentTaskRequest,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> schemas.AssessmentTaskResponse:
    """
    【板块2】发起考核任务 - HR 专属接口。

    流程：
    1. 权限检查：仅 HR 可调用
    2. 参数验证：日期、员工列表有效性
    3. 生成 ConfigSnapshot：冻结当前配置（指标、权重、评分标准、表单）
    4. 创建 AssessmentTask：任务主记录
    5. 批量生成 AssessmentRecord：为每个被评员工生成记录

    设计要点：
    - ConfigSnapshot：确保本次考核的配置不会被中途变更影响
    - 幂等性：同一 task_id 重复调用不会重复创建记录
    - 权限隔离：从 token 验证身份，不信任 current_user 参数

    参数：
    - title: 任务标题（如'2026 年 11 月月度考核'）
    - target_employee_ids: 被评员工 ID 列表
    - start_date / end_date: 考核周期
    - self_review_deadline: 自评截止时间
    - manager_review_deadline: 主管评分截止时间

    返回：
    - task_id: 新创建的任务 ID
    - status: 任务状态（created）
    - target_count: 被评员工数
    - created_at: 创建时间
    """

    try:
        # ─────────────────────────────────────────────────────────────
        # 1. 权限检查：仅 HR 可调用
        # ─────────────────────────────────────────────────────────────
        access_context = build_access_context(db, current_user)
        if not access_context.has_permission(PermissionAction.VIEW_ALL_SCORES):
            # 仅 HR 有 view_all_scores 权限
            raise exceptions.PermissionDenied(
                user_id=current_user.id,
                action="create_assessment_task",
                resource="AssessmentTask（仅 HR 可操作）"
            )

        # ─────────────────────────────────────────────────────────────
        # 2. 参数验证
        # ─────────────────────────────────────────────────────────────

        # 验证员工列表不为空
        if not request.target_employee_ids:
            raise exceptions.DataValidationError(
                "被评员工列表不能为空",
                field="target_employee_ids"
            )

        from .domain.scheme_models import PerformanceScheme
        if db.query(PerformanceScheme.id).first() is not None:
            raise HTTPException(status_code=409, detail="已启用HR方案版本，请从考核方案管理发起考核，以冻结员工适用方案")

        # 验证被评员工都存在
        employees = db.query(Employee).filter(
            Employee.id.in_(request.target_employee_ids)
        ).all()

        found_ids = {e.id for e in employees}
        missing_ids = set(request.target_employee_ids) - found_ids
        if missing_ids:
            raise exceptions.DataValidationError(
                f"以下员工不存在：{', '.join(missing_ids)}",
                field="target_employee_ids"
            )

        # 验证日期顺序
        from datetime import datetime as dt_cls
        start_dt = dt_cls.fromisoformat(request.start_date)
        end_dt = dt_cls.fromisoformat(request.end_date)
        self_review_dt = dt_cls.fromisoformat(request.self_review_deadline)
        manager_review_dt = dt_cls.fromisoformat(request.manager_review_deadline)

        if start_dt >= end_dt:
            raise exceptions.DataValidationError(
                "开始日期必须早于结束日期",
                field="start_date"
            )

        if self_review_dt <= start_dt or self_review_dt > manager_review_dt:
            raise exceptions.DataValidationError(
                "自评截止时间必须在考核开始后、主管评分前",
                field="self_review_deadline"
            )

        # ─────────────────────────────────────────────────────────────
        # 3. 生成 ConfigSnapshot（冻结配置）
        # ─────────────────────────────────────────────────────────────

        # 从数据库读取当前活跃的配置
        # （实际项目中这里应该有更复杂的逻辑，这里简化为示例）
        metrics = db.query(Metric).filter_by(active=True).all()
        weight_sets = db.query(WeightSet).filter_by(active=True).all()
        rubrics = db.query(Rubric).all()
        form_templates = db.query(FormTemplate).filter_by(active=True).all()

        # 构建快照 JSON
        metrics_json = [
            {
                "id": m.id,
                "code": m.code,
                "name": m.name,
                "unit": m.unit,
                "calculation": m.calculation,
                "data_source": m.data_source,
                "caliber": m.caliber,
                "owner": m.owner,
                "is_attendance": m.is_attendance,
            }
            for m in metrics
        ]

        # ⚠️ BUG-R6-30（快照格式错 → 算分必崩）
        #
        # 冻结快照的 ``weights_json`` 必须是**扁平**结构
        # ``[{"metric_code": ..., "weight": ...}]``，
        # 因为 ``services/scoring.build_specs`` 是这么读的：
        #
        #     for w in weights_json:
        #         code = w["metric_code"]        # ← 直接下标取键
        #         weight = float(w["weight"])
        #
        # 过去这里冻的是**带壳**结构
        # ``[{"id":..., "department_id":..., "weights":[...], "version":...}]``，
        # 于是 ``build_specs`` 抛 ``KeyError: 'metric_code'`` ——
        # 也就是说：**只要 weight_set 表非空，这个接口建出来的任务
        # 就永远算不出分**（weight_set 为空时反而"碰巧"不报错，
        # 因为 for 循环体一次都不执行，更隐蔽）。
        #
        # 现在统一冻成扁平结构，与 ``config_service.LocalProvider.load()``
        # 的产出格式**完全一致**（同一套快照能被两条路径读懂）。
        weights_json: list[dict[str, Any]] = []
        for w in weight_sets:
            for item in (w.weights_json or []):
                if not isinstance(item, dict):
                    continue
                code = item.get("metric_code")
                if not code:
                    continue
                weights_json.append(
                    {"metric_code": str(code), "weight": item.get("weight")}
                )

        # ⚠️ 同 BUG-R6-30：``rubric_json`` 也统一成
        # ``[{"metric_code": ..., "levels_json": [...]}]`` ——
        # 与 ``config_service.LocalProvider`` 产出格式一致。
        # （``build_specs`` 对 ``levels_json`` / ``levels`` 两种键都认，
        #   但快照是**长期存档**，格式必须唯一，否则以后读快照的代码
        #   要同时兼容两套 —— 那正是本 bug 的成因。）
        rubric_json: list[dict[str, Any]] = [
            {
                "metric_code": r.metric_code,
                "levels_json": r.levels_json,
            }
            for r in rubrics
        ]

        form_template_json = None
        if form_templates:
            form_template_json = {
                "id": form_templates[0].id,
                "name": form_templates[0].name,
                "fields": form_templates[0].fields_json,
                "version": form_templates[0].version,
            }

        # 创建 ConfigSnapshot 记录（task_id 作为唯一键）
        task_id = f"TASK_{dt_cls.now().strftime('%Y%m%d%H%M%S')}_{len(request.target_employee_ids)}"

        config_snapshot = ConfigSnapshot(
            task_id=task_id,
            metrics_json=metrics_json,
            weights_json=weights_json,
            rubric_json=rubric_json,
            form_template_json=form_template_json,
            config_version="1.0.0",
            frozen_at=dt_cls.now()
        )
        db.add(config_snapshot)
        db.flush()

        # ─────────────────────────────────────────────────────────────
        # 4. 创建 AssessmentTask 记录
        # ─────────────────────────────────────────────────────────────

        task = AssessmentTask(
            id=task_id,
            name=request.title,
            period=start_dt.strftime("%Y%m"),  # 期间：202611 格式
            department_id=None,  # 跨部门考核时为 None
            position=None,
            status="created",  # 初始状态
            self_review_deadline=self_review_dt,
            manager_review_deadline=manager_review_dt,
            rating_policy="reference_only",  # Mock 政策：参考类证据仅参考不计分
            snapshot_id=config_snapshot.id,
            owner_id=current_user.id,  # 由当前 HR 用户创建
            created_at=dt_cls.now()
        )
        db.add(task)
        db.flush()

        # ─────────────────────────────────────────────────────────────
        # 5. 批量生成 AssessmentRecord
        # ─────────────────────────────────────────────────────────────

        records = []
        for emp_id in request.target_employee_ids:
            record = AssessmentRecord(
                task_id=task_id,
                employee_id=emp_id,
                status="not_started",  # 初始状态：未开始
                submit_source=None,  # 尚未提交
                submit_locked=False,
                confirmed_rating=None,  # 尚未确认等级
                created_at=dt_cls.now(),
                updated_at=dt_cls.now()
            )
            records.append(record)

        db.add_all(records)
        db.flush()
        from .services.notification_service import record_event

        for record in records:
            record_event(db, record, "N9")

        # ─────────────────────────────────────────────────────────────
        # 6. 提交事务
        # ─────────────────────────────────────────────────────────────

        db.commit()

        # ─────────────────────────────────────────────────────────────
        # 7. 返回响应
        # ─────────────────────────────────────────────────────────────

        return schemas.AssessmentTaskResponse(
            task_id=task_id,
            status="created",
            created_at=task.created_at.isoformat(),
            target_count=len(records),
            message=f"成功创建考核任务，包含 {len(records)} 名被评员工"
        )

    except exceptions.BaseAppError as exc:
        # 自定义业务异常：直接转换为 HTTP 响应
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message
        ) from exc

    except Exception as exc:
        # 未预期的异常：通用错误处理
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"内部错误：{str(exc)}"
        ) from exc


@app.post("/api/tasks")
def create_task(
    req: CreateTaskReq,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        task = review_service.create_task(
            db,
            name=req.name,
            period=req.period,
            department_id=req.department_id,
            position=req.position,
            owner_id=req.owner_id,
        )
    except review_service.config_service.ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=f"配置校验失败：{exc}") from exc
    return {"task_id": task.id, "snapshot_id": task.snapshot_id, "status": task.status}


@app.post("/api/tasks/{task_id}/open-records")
def open_records(
    task_id: str,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        recs = review_service.open_records(db, task_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"count": len(recs), "record_ids": [r.id for r in recs]}


# ---------------------------------------------------------------------------
# 自评
# ---------------------------------------------------------------------------


@app.post("/api/records/{record_id}/self-review")
def submit_self_review(
    record_id: str,
    req: SubmitSelfReviewReq,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # 只能给**自己**的记录写自评。
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    if rec.employee_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能为本人提交自评")
    try:
        sr = review_service.submit_self_review(
            db,
            record_id=record_id,
            answers=req.answers,
            attachments=req.attachments,
            source=req.source,
            dedup_key=req.dedup_key,
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"self_review_id": sr.id, "submitted_at": sr.submitted_at.isoformat() if sr.submitted_at else None}


# ---------------------------------------------------------------------------
# 数据写入
# ---------------------------------------------------------------------------


@app.post("/api/metric-values")
def add_metric_value(
    req: MetricValueReq,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """写入指标实测值（算分集唯一入口）。考勤来源会被拒绝。"""
    # 算分集是绩效的核心，只有 HR / 管理员可以写入他人指标值。
    #
    # ⚠️ P0-4a：这里用 ``can_write_score_of``（**写**权限），
    #    不是 ``can_view_score_of``（读权限）。
    #
    #    指标实测值是**算分的唯一入口** ——
    #    若这里用读权限，同部门平级同事一旦获得 peer 读权限，
    #    就能给对方写实测值 ⇒ **等于可以改别人的绩效分数**。
    #    需求明确禁止"通过问答修改绩效分数"，这条闸门必须独立于读权限。
    access = access_control.build_access_context(db, current_user)
    if not access.can_write_score_of(req.employee_id, db=db):
        raise HTTPException(
            status_code=403,
            detail="无权为该员工写入指标值",
        )
    try:
        mv = evidence_service.add_metric_value(
            db,
            employee_id=req.employee_id,
            metric_code=req.metric_code,
            period=req.period,
            value=req.value,
            target=req.target,
            unit=req.unit,
            source=req.source,
            source_ref=req.source_ref,
            as_of=req.as_of,
        )
    except evidence_service.AttendanceEvidenceRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": mv.id}


@app.post("/api/attendance/anomalies")
def add_anomaly(
    req: AnomalyReq,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """写入考勤异常。全部 reference_only，不进算分集。"""
    from .domain.enums import AttendanceAnomalyType

    # ⚠️ P0-4a：写权限，不是读权限 —— 见 `POST /api/metric-values` 的说明。
    access = access_control.build_access_context(db, current_user)
    if not access.can_write_score_of(req.employee_id, db=db):
        raise HTTPException(
            status_code=403,
            detail="无权为该员工写入考勤异常",
        )
    try:
        atype = AttendanceAnomalyType(req.anomaly_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"未知考勤异常类型：{req.anomaly_type}") from exc

    evs = evidence_service.record_attendance_anomalies(
        db,
        [
            evidence_service.AnomalyInput(
                employee_id=req.employee_id,
                period=req.period,
                anomaly_type=atype,
                count=req.count,
                as_of=req.as_of,
                approved=req.approved,
                detail=req.detail,
            )
        ],
    )
    return {
        "evidence_ids": [e.id for e in evs],
        "usage": evs[0].usage,
        "disclaimer": evidence_service.ATTENDANCE_DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------


@app.post("/api/records/{record_id}/draft")
def create_draft(
    record_id: str,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """生成评级建议 + 评语草稿。

    智能体不可用时自动降级，**不会失败** —— 流程不中断。
    """
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    # ⚠️ P0-4a：生成草稿 = **写**（会落 RatingDraft / CommentDraft）。
    #    用写权限，避免 peer 读权限顺带获得"给同事写评语"的能力。
    access = access_control.build_access_context(db, current_user)
    if not access.can_write_score_of(rec.employee_id, db=db):
        raise HTTPException(status_code=403, detail="无权为该员工生成草稿")
    try:
        bundle = review_service.generate_draft(db, record_id=record_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "record_id": record_id,
        "score": bundle.score.total,
        "ratings": bundle.ratings,
        "comment_draft": bundle.comment_markdown,
        "citations": bundle.citations,
        "gaps": bundle.gaps,
        "fairness": bundle.fairness,
        "degraded": bundle.degraded,
        "agent_status": bundle.agent_status,
    }


@app.post("/api/records/{record_id}/score/recompute")
def recompute_score(
    record_id: str,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """复算分数。**关掉 AI 重跑，结果完全一致**（框架验收第 1 条）。"""
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    # ⚠️ P0-4a：复算会**落库**（persist_score）⇒ 写权限。
    access = access_control.build_access_context(db, current_user)
    if not access.can_write_score_of(rec.employee_id, db=db):
        raise HTTPException(status_code=403, detail="无权复算该员工的分数")
    try:
        result = review_service.compute_score(db, record_id=record_id)
        review_service.persist_score(db, record_id=record_id, result=result)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result.to_dict()


@app.post("/api/records/{record_id}/confirm")
def confirm(
    record_id: str,
    req: ConfirmReq,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """人工确认草稿（最后一道闸）。"""
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    # 确认评级是「上级/HR」的动作，普通员工不能自评自确认。
    access = access_control.build_access_context(db, current_user)
    if not access.has_permission(PermissionAction.CONFIRM_RATING):
        raise HTTPException(status_code=403, detail="无权确认评级")
    # 确认人强制取当前登录人，不接受客户端传入的 confirmed_by。
    try:
        return review_service.confirm_draft(
            db,
            record_id=record_id,
            confirmed_by=current_user.id,
            confirmed_rating=req.confirmed_rating,
        )
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/records/{record_id}/citations/validate")
def validate_citations(
    record_id: str,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """校验草稿引用的证据是否有效（存在、属于该员工、可见性允许）。"""
    rec = db.get(AssessmentRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="考核记录不存在")
    access = access_control.build_access_context(db, current_user)
    if not access.can_view_score_of(rec.employee_id):
        raise HTTPException(status_code=403, detail="无权校验该员工的证据引用")
    rating = db.execute(
        select(RatingDraft)
        .where(RatingDraft.record_id == record_id)
        .order_by(RatingDraft.created_at.desc())
    ).scalars().first()
    if rating is None:
        raise HTTPException(status_code=404, detail="没有草稿")

    ok, valid = review_service.validate_citations(
        db, record_id=record_id, evidence_ids=list(rating.citations)
    )
    return {
        "grounded": ok,
        "valid_citations": valid,
        "invalid_citations": [c for c in rating.citations if c not in valid],
    }


# ┌─────────────────────────────────────────────────────┐
# │ P0-2：Q&A 查询 API（改成 POST）                     │
# │ P0-1：progress / result 拆分                        │
# │ P0-4：数据质量 + 隐私脱敏                           │
# │ P2-1：k-匿名 检查                                  │
# └─────────────────────────────────────────────────────┘


from .routes.agent_tools import router as agent_tools_router  # noqa: E402

app.include_router(agent_tools_router)
from .routes.plan_tools import router as plan_tools_router  # noqa: E402

app.include_router(plan_tools_router)
from .routes.exec_tools import router as exec_tools_router  # noqa: E402

app.include_router(exec_tools_router)
from .routes.exec_tokens import router as exec_tokens_router  # noqa: E402

app.include_router(exec_tokens_router)
from .routes.data_source import router as data_source_router  # noqa: E402

app.include_router(data_source_router)
from .routes.semantic_qc import router as semantic_qc_router  # noqa: E402

app.include_router(semantic_qc_router)
from .routes.trend_report import router as trend_report_router  # noqa: E402

app.include_router(trend_report_router)
from .routes.feedback import router as feedback_router  # noqa: E402

app.include_router(feedback_router)
from .routes.qa import router as qa_router  # noqa: E402

app.include_router(qa_router)


@app.post("/v1/api/qa/query", tags=["QA"])
def query_assessment_data(
    request: schemas.QAQueryRequest,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """
    Q&A 查询接口（改成 POST，参考 P0-2）。

    查询类型（参考 P0-1）：
    - self_progress：我的月中进度（未确认的数据）
    - self_result：我的月末成绩（已确认）
    - colleague_result：某员工的成绩（权限检查）
    - peer_performance_distribution：部门等级分布（聚合，不排名，参考 P2-2）

    关键原则（P0/P2）：
    - 权限来自 token（current_user），不信 URL 参数
    - 响应含 data_quality（替代 confidence）
    - 不暴露 score 字段
    - < 5 人时隐藏平均分（k-匿名，P2-1）
    """

    try:
        # 1. 权限检查 + 数据查询
        if request.query_type == "self_progress":
            if request.target_employee_id != current_user.id:
                raise exceptions.PermissionDenied(
                    current_user.id,
                    f"query {request.query_type}",
                    f"employee:{request.target_employee_id}"
                )
            progress_data = qa_service.query_self_progress(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = progress_data.model_dump()

        elif request.query_type == "self_result":
            if request.target_employee_id != current_user.id:
                raise exceptions.PermissionDenied(
                    current_user.id,
                    "query self_result",
                    f"employee:{request.target_employee_id}"
                )
            result_data = qa_service.query_self_result(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = result_data.model_dump()

        elif request.query_type == "colleague_result":
            # 主管查下级，或 HR 查任何人
            dependencies.check_can_view_score(
                current_user.id,
                request.target_employee_id,
                db
            )
            result_data = qa_service.query_colleague_result(
                request.target_employee_id,
                request.task_id,
                db
            )
            data = result_data.model_dump()

        elif request.query_type == "peer_performance_distribution":
            # 查看某位下属不等于有权查看该下属整个部门的聚合数据。
            target_emp = db.get(Employee, request.target_employee_id)
            if not target_emp:
                raise exceptions.RecordNotFound("Employee", request.target_employee_id)

            context = build_access_context(db, current_user)
            if not (
                context.has_permission(PermissionAction.VIEW_ALL_SCORES)
                or target_emp.department_id in context.employee_department_ids
                or target_emp.department_id in context.managed_department_ids
            ):
                raise exceptions.PermissionDenied(
                    current_user.id,
                    "query peer_performance_distribution",
                    f"department:{target_emp.department_id}",
                )

            summary_data = qa_service.query_department_summary(
                target_emp.department_id,
                request.task_id,
                db
            )
            data = summary_data.model_dump()

        else:
            raise exceptions.DataValidationError(
                f"未知查询类型: {request.query_type}",
                field="query_type"
            )

        # 2. 计算数据质量（参考 P0-5）
        data_quality = schemas.DataQualityInfo(
            completeness=0.95,
            as_of=datetime.now().isoformat(),
            degraded=False,
            citations=["AssessmentRecord", "Score", "MetricValue"]
        )

        if request.query_type in {"self_result", "colleague_result"}:
            result_quality = data.get("score", {}).get("data_quality")
            data_quality = schemas.DataQualityInfo.model_validate(result_quality) if result_quality else schemas.DataQualityInfo(
                completeness=0,
                as_of=datetime.now().isoformat(),
                degraded=True,
                degrade_reason="暂无评分结果",
                citations=["AssessmentRecord"],
            )
        return {
            "query_type": request.query_type,
            "data": data,
            "data_quality": data_quality.model_dump()
        }

    except exceptions.BaseAppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ┌─────────────────────────────────────────────────────┐
# │ 主管评分 API（补全的路由，P0-3）                   │
# └─────────────────────────────────────────────────────┘


class ManagerReviewReq(BaseModel):
    """主管提交评分。"""
    manager_comment: str = Field(..., min_length=1, max_length=2000)
    confirmed_rating: str = Field(..., pattern="^[A-D]$")
    confidence: float | None = Field(None, ge=0.0, le=1.0)


@app.post("/v1/assessment/records/{record_id}/manager-review", tags=["Review"])
def submit_manager_review(
    record_id: str,
    request: ManagerReviewReq,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """主管提交评分（P0-3 权限模型）。"""

    try:
        # 1. 权限检查
        record = db.get(AssessmentRecord, record_id)
        if not record:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id)

        target_emp = db.get(Employee, record.employee_id)
        if not target_emp or target_emp.manager_id != current_user.id:
            raise exceptions.PermissionDenied(
                current_user.id,
                "submit_manager_review",
                f"record:{record_id}"
            )

        # 2. 验证等级
        if request.confirmed_rating not in ("A", "B", "C", "D"):
            raise exceptions.DataValidationError(
                f"等级必须是 A/B/C/D，得到 {request.confirmed_rating}",
                field="confirmed_rating"
            )

        # 3. 更新 Record
        #
        # ⚠️ BUG-R6-34（算分失败 → 主管评分被回滚丢弃）：
        # 过去这里是 ``compute_score`` 一抛异常就整体 ``db.rollback()``，
        # 于是主管**已经填好的评语和等级被一起丢掉**，
        # 前端还弹一个 409，用户白填一遍。
        # 触发条件很常见：任务没冻结配置快照（BUG-R6-27 的历史任务），
        # ``compute_score`` 会抛 ``InvalidState("任务没有配置快照，无法计算")``。
        #
        # 现在：先落库主管评分（这是**用户的主观输入**，绝不能丢），
        # 算分失败只降级为「已评分但未算分」，把原因带回前端。
        if record.status not in ("not_started", "self_review_submitted"):
            raise InvalidState(
                f"该记录当前状态为 {record.status}，不可提交主管评分"
            )
        record.manager_comment = request.manager_comment
        record.confirmed_rating = request.confirmed_rating
        record.stage = "confirmation"
        record.status = "manager_review_submitted"
        db.add(record)
        db.flush()

        # 4. 触发自动计分（失败不丢主管评分）
        score_info: dict[str, Any]
        try:
            result = review_service.compute_score(db, record_id=record_id)
            persisted = review_service.persist_score(db, record_id=record_id, result=result)
            score_info = {
                "scored": True,
                "total": persisted.total,
                "rating": persisted.rating,
            }
        except Exception as exc:
            logger.error(
                "主管评分已提交，但规则引擎算分失败 record=%s: %s",
                record_id, exc, exc_info=True,
            )
            score_info = {"scored": False, "scoring_error": str(exc)}

        db.commit()

        return {
            "status": "success",
            "record_id": record_id,
            "confirmed_rating": request.confirmed_rating,
            "stage": "confirmation",
            **score_info,
        }

    except exceptions.BaseAppError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except InvalidState as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ┌─────────────────────────────────────────────────────┐
# │ 添加指标值路由（补全 P0-5 data_quality）           │
# └─────────────────────────────────────────────────────┘


@app.post("/v1/assessment/records/{record_id}/metrics", tags=["Metrics"])
def add_metric_to_record(
    record_id: str,
    request: schemas.MetricValueRequest,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db)
) -> dict[str, Any]:
    """添加指标值到某条 Record（P0-5 data_quality 相关）。"""

    try:
        record = db.get(AssessmentRecord, record_id)
        if not record:
            raise exceptions.RecordNotFound("AssessmentRecord", record_id)

        # ⚠️ 2026-11-27：指标实测值是**算分集的唯一输入** ——
        #    只有对该员工有「写成绩」权限的人（本人 / 主管 / 部门负责人 / HR）
        #    才能写入。原实现只校验了「已登录」，任何员工都能改别人的指标。
        access = access_control.build_access_context(db, current_user)
        if not access.can_write_score_of(record.employee_id, db=db):
            raise HTTPException(status_code=403, detail="无权为该员工写入指标值")

        task = db.get(AssessmentTask, record.task_id)
        period = str(task.period) if task is not None else ""

        # 添加指标值（决策②检查内置）
        metric_value = evidence_service.add_metric_value(
            db,
            employee_id=record.employee_id,
            metric_code=request.metric_code,
            period=period,
            value=request.value,
            source=request.source,
            as_of=request.collected_at
        )

        db.commit()

        return {
            "status": "success",
            "metric_id": metric_value.id,
            "metric_code": request.metric_code,
            "value": request.value
        }

    except exceptions.AttendanceEvidenceRejected as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except exceptions.BaseAppError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ┌─────────────────────────────────────────────────────┐
# │ 健康检查 + 系统信息                                 │
# └─────────────────────────────────────────────────────┘


@app.get("/v1/health", tags=["Health"])
def health_check() -> dict[str, Any]:
    """健康检查。"""
    return {
        "status": "ok",
        "version": "1.0.0",
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/v1/info", tags=["Health"])
def system_info() -> dict[str, Any]:
    """系统信息。"""
    return {
        "agents": ["QA", "FillCoach", "Suggestion", "Grounding", "PerformanceExplain", "RankExplain"],
        "rules_version": "1.0.0",
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "api_version": "1.0.0",
        "features": {
            "k_anonymity": True,
            "privacy_degradation": True,
            "attendance_rejection": True,
            "model_not_trusted": True
        }
    }


# ============================================================================
# 认证接口（Auth）
# ---------------------------------------------------------------------------
# ⚠️ 本文件**不再提供**任何登录 / 登出 / 当前用户端点。
#
# 历史背景（R3 安全加固）：
#   这里曾存在一整套 Mock 鉴权 —— `MOCK_USERS` 硬编码 4 个账号、
#   token 格式为 `token_{username}_{user_id}_{timestamp}`（**无签名**），
#   并且 `/auth/login` 不带 `/v1` 前缀，直接暴露在公开路径上。
#   后果：任何人可以伪造 token 访问真实业务端点（已实测
#   `POST /auth/login` → 200，伪造 token → `GET /v1/performance/tasks` → 200），
#   属于**越权漏洞**。
#
# 现在唯一鉴权链路：
#   `app/routes/auth.py` → HMAC 签名 Token（`/v1/auth/*`）
#   `app/dependencies.py::get_current_user` → 解签名 + 查库 + 校验离职/禁用
#   `app/services/access_control.py` → 统一权限解析（唯一事实源）
#
# 保留说明：前端 `api/auth.ts` 仍写 `/auth/login`，是因为 `http.ts` 的
# `baseURL='/api'` + Vite 代理 `rewrite: /api → /v1`，实际命中
# `/v1/auth/login`（真实现）。前端路径**无需**改动。
# ============================================================================


# ============================================================================
# 计划与成果接口（Planning）—— Mock 数据，用于演示
# ============================================================================

class PlanningStatsResponse(BaseModel):
    """仪表板统计数据。"""
    pendingDayPlans: int
    overduePendingDayPlans: int
    currentMonthResults: int
    closureRate: str


class MonthPlan(BaseModel):
    """月计划。"""
    id: int
    title: str
    planMonth: str
    status: str
    ownerUserId: int
    deptId: str | None = None


class DayPlan(BaseModel):
    """日计划。"""
    id: int
    title: str
    planDate: str
    content: str
    status: str
    ownerUserId: int
    deptId: str | None = None


class Result(BaseModel):
    """成果。"""
    id: int
    title: str
    resultDate: str
    content: str
    planType: str
    status: str
    ownerUserId: int
    deptId: str | None = None
async def get_planning_stats(db: Session = Depends(get_db)):
    """获取仪表板统计数据。"""
    return {
        "code": 0,
        "data": {
            "pendingDayPlans": 12,
            "overduePendingDayPlans": 3,
            "currentMonthResults": 45,
            "closureRate": "87%",
        }
    }


# ============================================================================
# 员工管理 API
# ============================================================================



@app.get("/system/employees/{employee_id}")
async def get_employee(
    employee_id: str,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
):
    """获取单个员工详情。"""
    _require_employee_management(current_user, db)
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        return {"code": 404, "message": "Employee not found"}

    return {
        "code": 0,
        "data": {
            "id": employee.id,
            "name": employee.name,
            "email": employee.email,
            "department_id": employee.department_id,
            "position": employee.position,
            "manager_id": employee.manager_id,
            "employment_status": employee.employment_status,
        }
    }


@app.post("/system/employees")
async def create_employee(
    req: dict,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
):
    """创建新员工。"""
    _require_employee_management(current_user, db)
    new_employee = Employee(
        name=req.get("name"),
        email=req.get("email"),
        department_id=req.get("department_id"),
        position=req.get("position"),
        manager_id=req.get("manager_id"),
        employment_status="active",
    )
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)

    return {
        "code": 0,
        "data": {"id": new_employee.id, "name": new_employee.name}
    }


@app.put("/system/employees/{employee_id}")
async def update_employee(
    employee_id: str,
    req: dict,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
):
    """更新员工信息。"""
    _require_employee_management(current_user, db)
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        return {"code": 404, "message": "Employee not found"}

    # 允许更新的字段
    for key in ["name", "email", "department_id", "position", "manager_id", "employment_status"]:
        if key in req:
            setattr(employee, key, req[key])

    db.commit()
    db.refresh(employee)

    return {
        "code": 0,
        "data": {"id": employee.id, "name": employee.name}
    }


@app.delete("/system/employees/{employee_id}")
async def delete_employee(
    employee_id: str,
    current_user: Employee = Depends(dependencies.get_current_user),
    db: Session = Depends(get_db),
):
    """删除员工（实际上只是改为禁用状态）。"""
    _require_employee_management(current_user, db)
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        return {"code": 404, "message": "Employee not found"}

    employee.employment_status = "inactive"
    db.commit()

    return {
        "code": 0,
        "message": "Employee disabled"
    }
