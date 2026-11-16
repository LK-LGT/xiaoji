"""离职员工状态判定策略。

背景
----
飞书通讯录的 ``status`` 有 5 个布尔位，组合起来有多种语义。
本模块把「飞书状态 → 本地在职状态」的判定规则**显式化、可配置化**，
目的是：

1. 业务口径待确认时，能**只改配置不改代码**；
2. 判定依据可追溯 —— 每条判定都带 ``reason``，便于对账时解释；
3. 明确区分「离职（exited）」与「冻结/未激活（inactive）」——
   前者需要人工复职，后者解冻后应自动恢复。

判定口径
--------
================= ========================== ==================
飞书标志           业务含义                   本地状态
================= ========================== ==================
is_exited         离职                       exited
is_resigned       主动离职                   exited
is_frozen         冻结（临时停用）           inactive
is_activated=F    未激活                     inactive
is_unjoin=T       未加入企业                 inactive
================= ========================== ==================

待业务确认的口径
----------------
``is_unjoin``（未加入企业）到底算「离职」还是「未入职」？
- 视为 ``inactive``：账号禁用，但解冻/入职后可自动恢复（当前默认）；
- 视为 ``exited``：视为已离开企业，需走人工复职流程。

该口径可通过 ``FEISHU_SYNC_UNJOIN_AS_EXITED`` 环境变量切换，
**默认保持 ``inactive``**（更保守，不会误伤在职员工）。

安全约束
--------
「远端没拉到这个人」**不等于**「已离职」——
可能只是拉取失败或应用可见范围变化。只有「全量模式 + 快照完整」
才允许做离职判定，避免整部门员工被误停用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 本地在职状态取值。
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_EXITED = "exited"

# 判定依据（reason），用于对账时解释「为什么这个人是这个状态」。
REASON_ACTIVE = "active"
REASON_EXITED = "is_exited"
REASON_RESIGNED = "is_resigned"
REASON_FROZEN = "is_frozen"
REASON_NOT_ACTIVATED = "not_activated"
REASON_UNJOINED = "unjoined"
REASON_NO_STATUS = "no_status_info"


@dataclass(frozen=True)
class OffboardingDecision:
    """一次状态判定的结果。"""

    status: str
    reason: str
    #: 是否应当禁用账号（离职/冻结都要禁用）。
    disable_account: bool = False
    #: 是否属于「永久离职」—— 需要人工复职，不能自动恢复。
    permanent: bool = False
    #: 判定依据的原始快照，便于排查。
    source: dict[str, Any] = field(default_factory=dict)

    @property
    def is_exited(self) -> bool:
        return self.status == STATUS_EXITED

    @property
    def is_inactive(self) -> bool:
        return self.status == STATUS_INACTIVE

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "disableAccount": self.disable_account,
            "permanent": self.permanent,
            "source": self.source,
        }


class OffboardingPolicy:
    """离职判定策略。

    Parameters:
        unjoin_as_exited: ``is_unjoin=True`` 是否按离职处理。
            默认 False（按 inactive 处理，更保守）。
        exited_disables_account: 离职是否禁用账号。默认 True。
        inactive_disables_account: 冻结/未激活是否禁用账号。默认 True。
    """

    def __init__(
        self,
        *,
        unjoin_as_exited: bool = False,
        exited_disables_account: bool = True,
        inactive_disables_account: bool = True,
    ) -> None:
        self.unjoin_as_exited = unjoin_as_exited
        self.exited_disables_account = exited_disables_account
        self.inactive_disables_account = inactive_disables_account

    @classmethod
    def from_settings(cls) -> OffboardingPolicy:
        """按配置构造策略（业务口径确认后只需改配置）。"""
        from ..core.config import settings

        return cls(
            unjoin_as_exited=bool(getattr(settings, "feishu_sync_unjoin_as_exited", False)),
            exited_disables_account=bool(
                getattr(settings, "feishu_sync_exited_disables_account", True)
            ),
            inactive_disables_account=bool(
                getattr(settings, "feishu_sync_inactive_disables_account", True)
            ),
        )

    def decide(self, status: dict[str, Any] | None) -> OffboardingDecision:
        """根据飞书 ``status`` 判定本地在职状态。

        判定优先级：**离职 > 冻结 > 未激活 > 未加入 > 在职**。
        离职优先级最高 —— 已离职的员工无论其它标志如何都是 exited。
        """
        source = dict(status or {})

        if not source:
            # 拿不到状态时**不能**判离职 —— 保持现状最安全。
            return OffboardingDecision(
                status=STATUS_ACTIVE,
                reason=REASON_NO_STATUS,
                disable_account=False,
                source=source,
            )

        # 1) 离职（含主动离职）。
        if source.get("is_exited") or source.get("is_resigned"):
            reason = REASON_EXITED if source.get("is_exited") else REASON_RESIGNED
            return OffboardingDecision(
                status=STATUS_EXITED,
                reason=reason,
                disable_account=self.exited_disables_account,
                permanent=True,
                source=source,
            )

        # 2) 冻结 —— 临时停用，可自动恢复。
        if source.get("is_frozen"):
            return OffboardingDecision(
                status=STATUS_INACTIVE,
                reason=REASON_FROZEN,
                disable_account=self.inactive_disables_account,
                permanent=False,
                source=source,
            )

        # 3) 未激活。
        if not source.get("is_activated", True):
            return OffboardingDecision(
                status=STATUS_INACTIVE,
                reason=REASON_NOT_ACTIVATED,
                disable_account=self.inactive_disables_account,
                permanent=False,
                source=source,
            )

        # 4) 未加入企业 —— 口径可配置。
        if source.get("is_unjoin"):
            if self.unjoin_as_exited:
                return OffboardingDecision(
                    status=STATUS_EXITED,
                    reason=REASON_UNJOINED,
                    disable_account=self.exited_disables_account,
                    permanent=True,
                    source=source,
                )
            return OffboardingDecision(
                status=STATUS_INACTIVE,
                reason=REASON_UNJOINED,
                disable_account=self.inactive_disables_account,
                permanent=False,
                source=source,
            )

        # 5) 正常在职。
        return OffboardingDecision(
            status=STATUS_ACTIVE,
            reason=REASON_ACTIVE,
            disable_account=False,
            permanent=False,
            source=source,
        )


def should_disable_identity(employment_status: str | None) -> bool:
    """离职/冻结的员工是否应停用其身份绑定。

    「停用身份绑定」≠「解除绑定」—— 绑定关系保留（便于复职时恢复），
    只是不再可用于登录解析。

    业务口径待确认：
    - 当前实现：``exited`` 与 ``inactive`` 都停用；
    - 若业务认为「未激活员工仍需保留登录能力」，
      可只对 ``exited`` 停用 —— 改动点集中在这里。
    """
    return employment_status in (STATUS_EXITED, STATUS_INACTIVE)


__all__ = [
    "REASON_ACTIVE",
    "REASON_EXITED",
    "REASON_FROZEN",
    "REASON_NOT_ACTIVATED",
    "REASON_NO_STATUS",
    "REASON_RESIGNED",
    "REASON_UNJOINED",
    "STATUS_ACTIVE",
    "STATUS_EXITED",
    "STATUS_INACTIVE",
    "OffboardingDecision",
    "OffboardingPolicy",
    "should_disable_identity",
]
