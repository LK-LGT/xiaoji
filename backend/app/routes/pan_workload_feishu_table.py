"""飞书表格填报绑定与同步 API。

端点清单（挂在 ``/v1/baidu/workload/feishu-tables``）：
------------------------------------------------------------

    GET    /sources                  列出版本表绑定
    POST   /sources                  绑定某个剪辑同事的飞书表格
    DELETE /sources/{id}             解除绑定（停用）
    POST   /sources/{id}/sync        立即同步某一张表
    POST   /sync                     立即同步全部（启用的）表
    GET    /template                 返回「表格该长什么样」的模板说明

⚠️ **为什么挂在 ``/v1/baidu/workload`` 而不是 ``/v1/feishu/...``**
本功能是「**剪辑工作量**」板块的一部分 ⇒ 必须**继承该板块的守卫**
⇒ 放在同一个前缀下，router 级依赖直接生效。
（若放到 ``/v1/feishu`` 下，就得自己再写一遍板块判定 ——
  **两处各写一遍 ⇒ 迟早漂移**。）

⚠️ **权限：写操作要 HR，**员工只能看自己的**
- HR / 系统管理员 → 绑定、解绑、触发同步
- 员工 → 只能看**自己**的绑定与同步状态
  （员工能看见「我的表同步了没有」是刚需 —— 填报反馈闭环）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..dependencies import get_current_user
from ..domain.models import Employee
from ..services.access_control import build_access_context
from ..services.department_modules import PAN_WORKLOAD_MODULE, require_module
from ..services.feishu_client import FeishuClient
from ..services.feishu_metric_sync import (
    FeishuMetricSourceService,
    FeishuMetricSyncError,
)
from ..services.feishu_table import FeishuTableReader

router = APIRouter(
    prefix="/v1/baidu/workload/feishu-tables",
    tags=["baidu-pan-workload"],
    # ⚠️ **板块守卫** —— 与 ``pan_workload`` 同一套判定（共用工厂函数）
    # ⇒ 部门没开通「剪辑工作量」⇒ 这组端点也 403。
    dependencies=[Depends(require_module(PAN_WORKLOAD_MODULE))],
)


# ─────────────────────────────────────────────────────────────────────────────
# 请求模型
# ─────────────────────────────────────────────────────────────────────────────


class BindTableRequest(BaseModel):
    """绑定飞书表格。"""

    #: ⚠️ **普通员工可为空** —— 员工只能绑自己，服务端会强制改写成本人。
    #: **HR / 管理员**才需要指定要给谁绑。
    employee_id: str = Field(default="", max_length=64)
    source_url: str = Field(min_length=1, max_length=512)
    #: ``sheet``（电子表格）/ ``bitable``（多维表格）
    table_kind: str = Field(default="sheet", pattern="^(sheet|bitable)$")


class SyncRequest(BaseModel):
    """立即同步。"""

    source_id: str = Field(default="", max_length=64)


class DailyPushRequest(BaseModel):
    """立即推送每日填报提醒。"""

    #: 推送哪一天（``YYYY-MM-DD``）。留空 = 今天。
    day: str = Field(default="", max_length=16)


# ─────────────────────────────────────────────────────────────────────────────
# 权限
# ─────────────────────────────────────────────────────────────────────────────


def _is_manager(db: Session, user: Employee) -> bool:
    """能否管理绑定（HR / 系统管理员）。"""
    ctx = build_access_context(db, user)
    return bool({"hr", "admin"} & set(ctx.roles))


def _require_manager(db: Session, user: Employee) -> None:
    if not _is_manager(db, user):
        raise HTTPException(status_code=403, detail="仅 HR / 系统管理员可配置飞书表格绑定")


def _serialize(source: Any) -> dict[str, Any]:
    return {
        "id": source.id,
        "employeeId": source.employee_id,
        "tableKind": source.table_kind,
        "objToken": source.obj_token,
        "tableId": source.table_id,
        "sheetId": source.sheet_id,
        "title": source.title,
        "sourceUrl": source.source_url,
        "active": source.active,
        "lastEditTime": source.last_edit_time,
        "lastRowCount": source.last_row_count,
        "lastSyncedAt": (
            source.last_synced_at.isoformat() if source.last_synced_at else None
        ),
        "lastError": source.last_error,
    }


def _service(db: Session) -> tuple[FeishuClient, FeishuMetricSourceService]:
    """构造服务（客户端生命周期由调用方负责关闭）。"""
    from ..core.config import settings

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(status_code=503, detail="飞书应用配置未完成")
    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )
    return client, FeishuMetricSourceService(db, FeishuTableReader(client))


# ─────────────────────────────────────────────────────────────────────────────
# 绑定
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/sources")
def list_sources(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出版本表绑定。

    ⚠️ **员工只能看自己的** —— 其他人的表格链接不该被看到。
    """
    client, service = _service(db)
    try:
        sources = service.list_sources()
    finally:
        client.close()

    if _is_manager(db, current_user):
        items = [_serialize(s) for s in sources]
    else:
        items = [_serialize(s) for s in sources if s.employee_id == current_user.id]
    return {"code": 0, "data": items}


@router.post("/sources")
def bind_source(
    body: BindTableRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """绑定某个剪辑同事的飞书表格。

    ⚠️ **绑定时会立刻读一次表头** ⇒ 链接错了当场报错，
    不用等到同步时才发现。

    ⚠️ **员工可以绑「自己的」表** —— 这是自助填报的关键一环：
    剪辑同事建好自己的飞书表 → 自己把链接粘进来 → 就能填。
    ⇒ **员工只能绑自己**（``employee_id`` 被强制改写为本人），
    ⇒ 杜绝「冒充别人绑表」。
    ⇒ HR / 管理员可以给**任何人**绑（代操作场景）。
    """
    client, service = _service(db)
    try:
        if _is_manager(db, current_user):
            # ⚠️ **HR 模式下必须指定给谁绑** —— 否则会绑到 HR 自己头上。
            employee_id = body.employee_id
            if not employee_id:
                raise HTTPException(status_code=400, detail="请指定要绑定的员工")
        else:
            # ⚠️ **非 HR 一律只能绑自己** —— 请求体里的 employee_id 直接忽略。
            employee_id = current_user.id
        source = service.bind(
            employee_id=employee_id,
            source_url=body.source_url,
            table_kind=body.table_kind,
            actor_id=current_user.id,
        )
        db.commit()
        return {"code": 0, "message": "绑定成功", "data": _serialize(source)}
    except FeishuMetricSyncError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    finally:
        client.close()


@router.delete("/sources/{source_id}")
def delete_source(
    source_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """解除绑定（停用，保留痕迹）。

    ⚠️ **员工可以解绑自己的** —— 换了表要能重新绑。
    """
    client, service = _service(db)
    try:
        source = service.get(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="绑定不存在")
        if not _is_manager(db, current_user) and source.employee_id != current_user.id:
            raise HTTPException(status_code=403, detail="只能解绑自己的表格")
        service.unbind(source_id, actor_id=current_user.id)
        db.commit()
        return {"code": 0, "message": "已解除绑定"}
    except FeishuMetricSyncError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    finally:
        client.close()


# ─────────────────────────────────────────────────────────────────────────────
# 同步
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/sources/{source_id}/sync")
def sync_source(
    source_id: str,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """立即同步某一张表。

    ⚠️ **员工可以触发「同步自己的表」**
    ⇒ 填报反馈闭环：员工填完 → 点一下 → 立刻看到数据进来了。
    ⇒ 但**只能同步自己的** —— 别人的表不该由他来触发。
    """
    client, service = _service(db)
    try:
        source = service.get(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="绑定不存在")
        if not _is_manager(db, current_user) and source.employee_id != current_user.id:
            raise HTTPException(status_code=403, detail="只能同步自己的表格")

        result = service.sync_one(source)
        db.commit()
        return {"code": 0, "message": "同步完成", "data": result}
    except FeishuMetricSyncError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    finally:
        client.close()


@router.post("/sync")
def sync_all(
    body: SyncRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """立即同步全部（或指定的）表。

    ⚠️ **只有 HR / 管理员能批量同步** ——
    批量操作影响所有剪辑同事的数据 ⇒ 不能让普通员工触发。
    """
    _require_manager(db, current_user)
    client, service = _service(db)
    try:
        result = service.sync_all(only_source_id=body.source_id)
        db.commit()
        return {"code": 0, "message": "同步完成", "data": result}
    except FeishuMetricSyncError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    finally:
        client.close()


# ─────────────────────────────────────────────────────────────────────────────
# 每日填报提醒推送
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/daily-push")
def trigger_daily_push(
    body: DailyPushRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """立即推送一次「每日填报提醒」。

    ⚠️ **只有 HR / 管理员能手动触发** ——
    推送是**主动打扰员工**的操作 ⇒ 不能让普通员工随手刷一片卡片。

    ⚠️ **不受「一天一次」限制** ——
    手动触发是运维手段（HR 想立刻测一下）⇒ 不查推送记录。
    ⚠️ **但同一天重复手动触发 = 员工会收到多张卡** ⇒ 提示语要说清。
    """
    _require_manager(db, current_user)
    from ..services.feishu_daily_push_scheduler import run_daily_push

    try:
        result = run_daily_push(body.day)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=f"推送失败：{exc}") from exc
    return {"code": 0, "message": "推送完成", "data": result}


@router.get("/daily-push/status")
def daily_push_status(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """每日推送调度器的运行状态。"""
    _require_manager(db, current_user)
    from ..services.feishu_daily_push_scheduler import get_scheduler_status

    return {"code": 0, "data": get_scheduler_status()}


# ─────────────────────────────────────────────────────────────────────────────
# 模板说明
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/template")
def get_template(
    current_user: Employee = Depends(get_current_user),
):
    """返回「表格该长什么样」的模板说明。

    ⚠️ **给剪辑同事的建表指引**
    ⇒ 直接在飞书里照着建表 ⇒ 不会因为列名不对而同步不上。
    """
    return {
        "code": 0,
        "data": {
            "sheetKind": "电子表格（飞书「表格」）",
            "sheetKindBitable": "多维表格（飞书「多维表格」）",
            "header": ["日期", "完播率", "视频留存率", "备注"],
            "example": [
                ["2026-11-20", "35%", "62%", "抖音"],
                ["2026-11-21", "0.28", "0.55", "视频号"],
            ],
            "rules": [
                "第一行必须是表头，且第一列是「日期」",
                "一行 = 一条填报记录；完播率/留存率留空表示当天没这项数据",
                "比率写 35% / 0.35 / 35 都认；统一按 0~1 存",
                "日期写 2026-11-20 / 2026/11/20 / 2026年11月20日 都认",
                "一人一张表 —— 系统按绑定关系确定归属，不需要「剪辑人」列",
                "表要**共享给应用**（在飞书里把表格权限加给该应用）",
            ],
            "aliasTable": {
                "日期": ["日期", "业务日期", "统计日期", "date", "时间"],
                "完播率": ["完播率", "完播", "播放完成率", "completion_rate"],
                "视频留存率": ["留存率", "视频留存率", "retention_rate"],
                "备注": ["备注", "说明", "作品名", "视频名", "remark"],
            },
        },
    }
