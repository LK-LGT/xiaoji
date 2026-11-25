"""★ **部门 × 岗位 → 剪辑工作量口径** 的解析服务（2026-11-23）。

⚠️ **为什么要有这个模块**
------------------------
``department_module`` 回答「**这个部门有没有剪辑这个业务**」，
但**部门内部还有不同岗位**，不同岗位的考核方式**完全不一样**：

    短视频部（实测库）
        巫宜珊 主播 → /口播成品，业务确认**不用管**
        刘旭   运营 → 不产成片
        张洁   编导 → 不产成片，是剪辑的直属上级（只审）
        李婵雨 剪辑 → /成品-小雨（要填）
        佟瑶   剪辑 → /成品-小瑶（要填）

⇒ **「谁要填报、谁只审、填什么指标」必须配在库里**，而不是写死在代码里。

⚠️ **为什么不用「网盘绑定表」推导**
----------------------------------
``pan_account_binding`` 里还有「巫宜珊 / 口播成品」——
而业务已确认**口播成品不用管** ⇒ 按绑定表推会多推 1 个人。

⚠️ **为什么不用「岗位名硬编码」**
--------------------------------
岗位会增减、口径会变（让主播也报数据）⇒ 硬编码要改代码发版。
⇒ 落在 ``pan_workload_policy`` 表里，改完立即生效。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.pan_workload_models import PanWorkloadPolicy
from .department_modules import PAN_WORKLOAD_MODULE

logger = logging.getLogger(__name__)

#: 角色常量
ROLE_REPORTER = "reporter"
ROLE_REVIEWER = "reviewer"
ROLE_NONE = "none"

#: 该板块的**默认指标** —— 岗位没单独配指标时用它。
#:
#: ⚠️ **2026-11-23 角色倒转后，这里是「主管填的比率」**
#: （剪辑填的是三条数量指标，见 ``daily_metric_specs.COUNT_METRICS``）。
DEFAULT_METRIC_CODES: tuple[str, ...] = (
    "video_completion_rate",
    "video_retention_rate",
)


def default_codes_for_role(role: str, module_code: str = PAN_WORKLOAD_MODULE) -> tuple[str, ...]:
    """按角色取**默认指标码**。

    ⚠️ **2026-11-23 角色倒转**
    ------------------------
    - ``reporter``（剪辑）→ 三条**数量**：普通视频 / OOTD / 智能体
    - ``reviewer``（主管）→ 两个**比率**：完播率 / 视频留存率
    - ``none``            → 空（不参与）

    ⚠️ **为什么不一律用 ``DEFAULT_METRIC_CODES``**
    ----------------------------------------------
    那样会让**剪辑去填比率** ⇒ 角色倒转白做 ⇒
    业务要的「剪辑只填条数、主管填比率」根本没落地。

    ⚠️⚠️ **2026-11-25：默认指标是「板块相关」的**
    ----------------------------------------------
    之前**只有** ``pan_workload`` 一个板块 ⇒ 默认值直接写死成视频指标。
    现在加了通用板块 ``dept_workload`` ⇒ 若还沿用视频默认，
    **仓储部的分拣员会收到「今天剪了几条视频」的卡片**。

    ⇒ **只有 ``pan_workload`` 才有视频默认**；
      其它板块的岗位没配指标 ⇒ **返回空**（=「指标待定」）
      ⇒ 卡片不出现任何填报项，等业务定了指标再填。
    """
    from .daily_metric_specs import COUNT_METRICS, RATE_METRICS

    # ⚠️ **非剪辑板块 ⇒ 没有默认指标**（指标后续按部门配）。
    if module_code != PAN_WORKLOAD_MODULE:
        return ()

    if role == ROLE_REPORTER:
        return tuple(spec.code for spec in COUNT_METRICS)
    if role == ROLE_REVIEWER:
        return tuple(spec.code for spec in RATE_METRICS)
    return ()


@dataclass(frozen=True)
class PositionPolicy:
    """一个岗位在这块业务里的口径。"""

    #: 岗位名
    position: str
    #: ``reporter`` / ``reviewer`` / ``none``
    role: str
    #: 要填的指标码（``role == "reporter"`` 时有意义）
    metric_codes: tuple[str, ...]
    #: 配置说明
    note: str = ""

    #: ⚠️ **该岗位是否**真的**在口径表里配过行**（2026-11-24）
    #:
    #: ⚠️ **为什么需要这个字段**
    #: ``resolve_policy`` 查不到行时返回 ``role="none"``，
    #: 与「配了但明确是 none」**返回值完全一样** ⇒ 无法区分。
    #: 而菜单判定必须区分这两种情况：
    #: - 「配了 none」⇒ 业务确认这个岗位**不参与** ⇒ 不显示菜单
    #: - 「没配」  ⇒ **未配置** ⇒ 可由「负责人兜底」规则接管
    configured: bool = False

    @property
    def is_reporter(self) -> bool:
        return self.role == ROLE_REPORTER

    @property
    def is_reviewer(self) -> bool:
        return self.role == ROLE_REVIEWER


def policy_map(
    db: Session, department_id: str, module_code: str = PAN_WORKLOAD_MODULE
) -> dict[str, PositionPolicy]:
    """读一个部门的岗位口径 → ``{岗位名: PositionPolicy}``。

    ⚠️ **按岗位名索引** —— 同一部门里岗位名唯一（唯一约束保证）。

    ⚠️ **``module_code`` 决定「没配指标时的默认值」**
    （2026-11-25）—— 只有剪辑板块才有视频默认指标，
    其它板块没配指标就是「指标待定」⇒ 返回空。
    """
    rows = db.scalars(
        select(PanWorkloadPolicy).where(
            PanWorkloadPolicy.department_id == department_id,
            PanWorkloadPolicy.active.is_(True),
        )
    ).all()

    result: dict[str, PositionPolicy] = {}
    for row in rows:
        codes = tuple(
            item.strip()
            for item in (row.metric_codes or "").split(",")
            if item.strip()
        )
        role = row.role or ROLE_NONE
        result[row.position] = PositionPolicy(
            position=row.position,
            role=role,
            # ⚠️⚠️ **没配指标 = 按角色用默认**（2026-11-23 角色倒转）
            # ----------------------------------------------------
            # ⚠️ **为什么必须按角色区分默认**
            #   剪辑（reporter）填**数量**（普通视频/OOTD/智能体）；
            #   主管（reviewer）填**比率**（完播率/留存率）。
            # ⇒ 一律用「比率默认」会让**剪辑去填比率** ⇒ 角色倒转白做了。
            #
            # ⚠️ **2026-11-25**：默认指标**按板块**给 ——
            #   非剪辑板块没配指标 ⇒ 空（指标待定），**不套用视频指标**。
            metric_codes=codes or default_codes_for_role(role, module_code),
            note=row.note or "",
            # ⚠️ **确实配过行** —— 与「查不到行」区分开（2026-11-24）
            configured=True,
        )
    return result


def resolve_policy(
    db: Session, department_id: str, position: str, module_code: str = PAN_WORKLOAD_MODULE
) -> PositionPolicy:
    """解析某个部门某个岗位的口径。

    ⚠️ **没有配置行 ⇒ 不参与**（``none``）——
    与 ``department_module`` 同一套「默认关闭」哲学。
    ⇒ 漏配 = 收不到推送（可见、可修），而不是乱推（不可见、不可修）。

    ⚠️ **``module_code`` 决定「没配指标时的默认值」**（2026-11-25）。
    """
    position = (position or "").strip()
    if not position or not department_id:
        return PositionPolicy(position=position, role=ROLE_NONE, metric_codes=())

    policies = policy_map(db, department_id, module_code)
    found = policies.get(position)
    if found is not None:
        return found
    return PositionPolicy(position=position, role=ROLE_NONE, metric_codes=())


def reporter_metric_codes(
    db: Session, employee: Any, module_code: str = PAN_WORKLOAD_MODULE
) -> tuple[str, ...]:
    """取某个**填报人**要填的指标码。

    ⚠️ **2026-11-23 角色倒转**
    ------------------------
    剪辑填**数量**（普通视频 / OOTD / 智能体），
    主管填**比率**（完播率 / 视频留存率）⇒
    ⇒ 「谁来填什么」必须按岗位口径取，不能写死。

    ⚠️ **2026-11-25**：非剪辑板块**不套用视频默认指标**
    ⇒ 指标没配就返回空（等业务定了指标再开）。
    """
    policy = resolve_policy(
        db,
        getattr(employee, "department_id", "") or "",
        getattr(employee, "position", "") or "",
        module_code,
    )
    if not policy.is_reporter:
        return ()
    # ⚠️ 口径表配了就用配的，没配就用角色默认（数量指标）。
    return policy.metric_codes or default_codes_for_role(ROLE_REPORTER, module_code)


def reviewer_metric_codes(
    db: Session, employee: Any, module_code: str = PAN_WORKLOAD_MODULE
) -> tuple[str, ...]:
    """取某个**审阅人**要填的指标码。

    ⚠️ **2026-11-23 角色倒转** —— 主管填完播率 / 视频留存率。
    ⇒ 主管卡**有表单**（与「主管卡只读」的旧规则相反）。

    ⚠️ **2026-11-25**：非剪辑板块**不套用视频默认指标**。
    """
    policy = resolve_policy(
        db,
        getattr(employee, "department_id", "") or "",
        getattr(employee, "position", "") or "",
        module_code,
    )
    if not policy.is_reviewer:
        return ()
    return policy.metric_codes or default_codes_for_role(ROLE_REVIEWER, module_code)


def list_policies(db: Session, department_id: str = "") -> list[dict[str, Any]]:
    """列出口径配置（给管理界面用）。"""
    stmt = select(PanWorkloadPolicy)
    if department_id:
        stmt = stmt.where(PanWorkloadPolicy.department_id == department_id)
    rows = db.scalars(stmt.order_by(PanWorkloadPolicy.position)).all()
    return [
        {
            "id": row.id,
            "departmentId": row.department_id,
            "position": row.position,
            "role": row.role,
            "metricCodes": row.metric_codes,
            "active": row.active,
            "note": row.note,
        }
        for row in rows
    ]


__all__ = [
    "DEFAULT_METRIC_CODES",
    "ROLE_NONE",
    "ROLE_REPORTER",
    "ROLE_REVIEWER",
    "PositionPolicy",
    "default_codes_for_role",
    "list_policies",
    "policy_map",
    "reporter_metric_codes",
    "resolve_policy",
    "reviewer_metric_codes",
]
