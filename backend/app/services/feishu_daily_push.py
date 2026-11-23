"""飞书端「每日填报提醒」推送核心（2026-11-23）。

职责
----
1. **谁该收** —— 剪辑本人 + 主管（板块隔离生效）。
2. **发什么** —— 本人：「表格 + 表单」卡；主管：只读汇总卡。
3. **一天只推一次** —— 用推送记录表的日期唯一约束兜底。

⚠️ **为什么推送记录要落库**
------------------------
幂等不能靠内存状态 ——
服务是多实例部署的（滚动更新），两个实例同时判定「该推了」⇒ 推两遍。
⇒ **数据库唯一约束**兜底（``uq_feishu_daily_push_record_day_kind``）。

⚠️ **归属安全**
--------------
表单回调会校验「提交人 == 卡片上的目标人」。
⇒ 卡片被转发也没用 —— B 点了 A 的卡，提交时会被拒。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Employee
from ..domain.pan_workload_models import FeishuDailyPushRecord
from .department_modules import PAN_WORKLOAD_MODULE, has_module

logger = logging.getLogger(__name__)

#: 推送记录的两类对象。
KIND_EDITOR = "editor"
KIND_LEADER = "leader"


def already_pushed_today(db: Session, day: str) -> bool:
    """今天是否已经推过了。

    ⚠️ **只看「剪辑本人」那类** —— 主管汇总可能因剪辑补填而需要再推一次。
    """
    row = db.execute(
        select(FeishuDailyPushRecord.id).where(
            FeishuDailyPushRecord.push_day == day,
            FeishuDailyPushRecord.kind == KIND_EDITOR,
            FeishuDailyPushRecord.status == "sent",
        )
    ).first()
    return row is not None


def _record(
    db: Session,
    *,
    day: str,
    kind: str,
    target_id: str,
    status: str = "sent",
    error: str = "",
) -> None:
    """写一条推送记录（**幂等**）。"""
    row = FeishuDailyPushRecord(
        push_day=day,
        kind=kind,
        target_id=target_id,
        status=status,
        error=error[:512],
    )
    db.add(row)
    try:
        db.flush()
    except Exception:
        # ⚠️ **唯一约束冲突 = 已经推过了** —— 不算错误。
        db.rollback()


def leaders_of(db: Session, employee: Employee) -> list[Employee]:
    """找出这个员工的**直属主管**。

    ⚠️ **口径（2026-11-23 按真实库修正）**
    ------------------------------------
    = **直属上级**（``manager_id``） + **所在部门的 leader**。

    ⚠️ **为什么「上级部门的 leader」不算**
    ------------------------------------
    真实组织::

        运营中心 (leader=杨飞 总经理)
        └── 短视频 (leader=刘旭 运营)
            ├── 张洁  编导   ← ★ 两位剪辑的**直属上级**
            │   ├── 李婵雨 剪辑
            │   └── 佟瑶   剪辑

    张洁的 ``manager_id`` 指向**杨飞**（因为她挂在运营中心那条线上）。
    ⇒ **若沿汇报线再往上走，杨飞就成了「剪辑的主管」**
    ⇒ 总经理每天收到「李婵雨还没填完播率」—— **推给了不该管的人**。

    ⇒ 剪辑的主管就是 **张洁（直属上级）** 与 **刘旭（短视频 leader）**。
    """
    from ..domain.models import Department

    leaders: dict[str, Employee] = {}

    # ① 直属上级（汇报线）
    if employee.manager_id:
        manager = db.get(Employee, employee.manager_id)
        if manager is not None:
            leaders[manager.id] = manager

    # ② 所在部门的 leader
    if employee.department_id:
        dept = db.get(Department, employee.department_id)
        if dept is not None and dept.leader_id:
            leader = db.get(Employee, dept.leader_id)
            if leader is not None:
                leaders[leader.id] = leader

    # ⚠️⚠️ **不要再往上走汇报线**（2026-11-23 修正）
    #
    # 真实组织（实测库）：
    #
    #     运营中心 (leader=杨飞 总经理)
    #     └── 短视频 (leader=刘旭 运营)
    #         ├── 张洁  编导   ← ★ 两位剪辑的**直属上级**
    #         │   ├── 李婵雨 剪辑
    #         │   └── 佟瑶   剪辑
    #
    # 张洁的 ``manager_id`` 指向**杨飞**（总经理，运营中心的 leader）——
    # 因为她被挂在了运营中心那条线上。
    #
    # ⚠️ **若沿汇报线再往上走一层，杨飞就会被当成「剪辑的主管」**
    # ⇒ 总经理每天收到「李婵雨还没填完播率」——
    #    **这是把部门级业务推给了不该管的人**。
    #
    # ⇒ **口径**：剪辑的主管 = **直属上级（张洁）** + **短视频部 leader（刘旭）**。
    #    「上级部门的 leader」**不算** —— 那是运营中心的事，不是剪辑的事。

    result = list(leaders.values())
    result.sort(key=lambda e: (e.name or "", e.id))
    return result


def push_daily_metrics(db: Session, day: str) -> dict[str, Any]:
    """执行一天的推送。

    Returns:
        ``{"day":..., "editors":N, "leaders":N, "skipped":N}``
    """
    from ..core.config import settings
    from . import pan_workload_policy as policy
    from .feishu_bot import get_or_create_session
    from .feishu_card import build_daily_metric_card
    from .feishu_client import FeishuClient
    from .feishu_daily_metric_push import (
        METRIC_LABELS,
        build_daily_status_rows,
        day_label,
        editors_for_push,
        missing_metrics,
    )

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.info("每日填报推送已跳过：飞书应用凭证未配置")
        return {"day": day, "editors": 0, "leaders": 0, "skipped": 0}

    editors = editors_for_push(db, day)
    if not editors:
        logger.info("每日填报推送已跳过：没有该收到提醒的剪辑")
        return {"day": day, "editors": 0, "leaders": 0, "skipped": 0}

    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )
    day_text = day_label(day)
    sent_editors = 0
    sent_leaders = 0
    skipped = 0

    try:
        # ── ① 剪辑本人 ────────────────────────────────
        for employee in editors:
            # ⚠️ **该填哪些指标由岗位口径决定**（2026-11-23 角色倒转）
            #    剪辑填**数量**：普通视频 / OOTD / 智能体。
            metrics = policy.reporter_metric_codes(db, employee)
            if not metrics:
                skipped += 1
                continue
            metric_pairs = tuple(
                (code, METRIC_LABELS.get(code, code)) for code in metrics
            )
            pending = missing_metrics(db, employee.id, day, metric_pairs)
            if not pending:
                # ⚠️ **已填完就不推** —— 骚扰比漏提醒更招人烦。
                skipped += 1
                continue

            receive_id, receive_id_type = _receive_id(employee)
            if not receive_id:
                skipped += 1
                continue

            bot_session = get_or_create_session(
                db,
                open_id=receive_id,
                app_id=settings.feishu_app_id,
                user_id=employee.feishu_user_id or "",
            )
            # ⚠️ **旋转 generation** —— 旧卡片的提交必须失效。
            generation = f"daily_{day}_{employee.id}"
            bot_session.last_message_id = generation
            db.flush()

            card = build_daily_metric_card(
                day=day,
                day_text=day_text,
                status_rows=build_daily_status_rows(
                    db, employee.id, day, metric_pairs
                ),
                pending=pending,
                employee_id=employee.id,
                session_id=bot_session.id,
                generation=generation,
                sheet_url=_sheet_url(db, employee.id),
                # ⚠️ **提醒发截图** —— 卡片里放不了上传控件（真机实测），
                #    ⇒ 只能走「另外发到聊天」这条通路。
                screenshot_hint=True,
            )
            try:
                client.send_card_message(
                    receive_id=receive_id,
                    card=card,
                    receive_id_type=receive_id_type,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("每日填报推送失败（%s）：%s", employee.name, exc)
                _record(
                    db, day=day, kind=KIND_EDITOR, target_id=employee.id,
                    status="failed", error=str(exc),
                )
                continue

            _record(db, day=day, kind=KIND_EDITOR, target_id=employee.id)
            sent_editors += 1

        # ── ② 主管汇总 ─────────────────────────────────
        sent_leaders = _push_leader_summary(
            db, client, editors, day, day_text
        )
    finally:
        client.close()

    return {
        "day": day,
        "editors": sent_editors,
        "leaders": sent_leaders,
        "skipped": skipped,
    }


def _push_leader_summary(
    db: Session,
    client: Any,
    editors: list[Employee],
    day: str,
    day_text: str,
) -> int:
    """给主管推「谁还没填」汇总。"""
    from . import pan_workload_policy as policy
    from .feishu_card import build_leader_daily_status_card
    from .feishu_daily_metric_push import (
        METRIC_LABELS,
        build_leader_status_rows,
        missing_metrics,
    )

    # ⚠️ **按主管聚合** —— 同一个主管管多个剪辑时只推一张卡。
    by_leader: dict[str, list[Employee]] = {}
    for employee in editors:
        for leader in leaders_of(db, employee):
            by_leader.setdefault(leader.id, []).append(employee)

    sent = 0
    for leader_id, members in by_leader.items():
        leader = db.get(Employee, leader_id)
        if leader is None:
            continue
        # ⚠️ **主管自己也要能收到** —— 他可能也是剪辑。
        if not leader.feishu_open_id and not leader.feishu_user_id:
            continue
        # ⚠️ **主管也得开通了板块** —— 否则运营中心的老总会收到短视频的汇总。
        if not has_module(db, leader, PAN_WORKLOAD_MODULE):
            continue
        # ⚠️⚠️ **主管身份必须落在口径表里**（2026-11-23 用户投诉）
        # ----------------------------------------------------------
        # 只靠「是剪辑的直属上级 / 部门 leader」还不够 ——
        # **岗位口径**才说了算：这个岗位在这块业务里是「只审」还是「不参与」。
        #
        # 短视频部实测：
        #   剪辑 reporter（要填）/ 编导 reviewer（只审）/ 运营 reviewer（只审）
        #   主播 —— **没有配置行** ⇒ none ⇒ 不该收到任何东西
        #
        # ⚠️ **兜底**：主管本人若是 ``reporter``（他也是剪辑）⇒ 也要收到
        #    （他自己也要填自己的数）。
        leader_policy = policy.resolve_policy(
            db, leader.department_id or "", leader.position or ""
        )
        if not leader_policy.is_reviewer and not leader_policy.is_reporter:
            continue

        receive_id, receive_id_type = _receive_id(leader)
        if not receive_id:
            continue

        member_ids = [m.id for m in members]
        rows = build_leader_status_rows(db, member_ids, day)
        all_done = all(row[2] == "无" for row in rows) if rows else True

        # ⚠️⚠️ **主管卡现在也有表单**（2026-11-23 角色倒转）
        # ------------------------------------------------------
        # 业务确认：剪辑只填**数量**，**完播率 / 留存率由主管填** ⇒
        # ⇒ 主管卡必须有表单，否则比率这一栏**永远没人填**。
        #
        # ⚠️ **主管填的是「效果指标」，不是替下属填数量**
        # ⇒ 表单第一个控件是「填给哪位剪辑」，
        #    且后端会校验目标人在主管的管理范围内。
        leader_metrics = policy.reviewer_metric_codes(db, leader)
        leader_pairs = tuple(
            (code, METRIC_LABELS.get(code, code)) for code in leader_metrics
        )
        leader_pending = (
            missing_metrics(db, member_ids[0], day, leader_pairs)
            if member_ids and leader_pairs
            else []
        )
        # ⚠️ **主管自己也是剪辑时，也要给他自己的比率填报入口**
        if not leader_pending and leader_metrics:
            own = missing_metrics(db, leader.id, day, leader_pairs)
            if own:
                leader_pending = own

        card = build_leader_daily_status_card(
            day=day,
            day_text=day_text,
            rows=rows,
            all_done=all_done,
            pending=leader_pending,
            targets=[(m.id, m.name or m.id) for m in members],
            employee_id=leader.id,
            generation=f"leader_daily_{day}_{leader.id}",
        )
        try:
            client.send_card_message(
                receive_id=receive_id, card=card, receive_id_type=receive_id_type
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("每日填报汇总推送失败（%s）：%s", leader.name, exc)
            continue

        _record(db, day=day, kind=KIND_LEADER, target_id=leader.id)
        sent += 1
    return sent


def _receive_id(employee: Employee) -> tuple[str, str]:
    """取员工的飞书收件 ID。"""
    from .feishu_approval_sync import employee_feishu_receive_id

    return employee_feishu_receive_id(employee)


def _sheet_url(db: Session, employee_id: str) -> str:
    """取员工绑定的飞书表格链接（没有就空字符串）。"""
    from ..domain.pan_workload_models import PanFeishuMetricSource

    row = db.execute(
        select(PanFeishuMetricSource).where(
            PanFeishuMetricSource.employee_id == employee_id,
            PanFeishuMetricSource.active.is_(True),
        )
    ).scalar_one_or_none()
    return row.source_url if row is not None else ""


__all__ = [
    "KIND_EDITOR",
    "KIND_LEADER",
    "already_pushed_today",
    "leaders_of",
    "push_daily_metrics",
]
