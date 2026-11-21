"""P0-3：问答的授权决策器。

⚠️ **为什么单独一个模块**
----------------------
「能不能查某个人」这个判断散落在各处会失控 ——
每一处各自写一遍，同一个漏洞就可能被绕开 N 次。

⇒ 本模块是**唯一的**授权决策入口：
   输入（提问者、目标人、任务、周期）→ 输出（一个可审计的决策对象）。

⚠️ **决策与执行分离**
----------------------
``AuthorizationDecision`` 里有三样东西：
- ``decision``  —— allow / deny（**是否执行**）
- ``user_message`` —— 给用户看的话（**怎么解释**）
- ``audit_level`` —— 审计级别（**怎么留痕**）

⚠️ **拒绝时不要泄露目标人信息**
------------------------------
跨部门 / 跨级 / 上级 —— 统一文案「不在可见范围内」。

⚠️ 如果文案区分「跨部门」和「上一级」，用户就能通过文案差异
推断出目标人的层级与部门 —— 这是**信息泄露**，比拒绝对他更糟。

⚠️ **写操作在这里被硬拦截**
------------------------------
「AI 问答不提供绩效修改」——
但拦截不能只靠关键词（用户可能说"帮我把分数往上抬一点"）。
本模块做**双层**拦截：
① 关键词命中 → 直接拦截（快速路径，给明确引导）
② 关键词未命中 → 工具层压根没有写工具，模型调不到（兜底）

两层都不依赖模型的判断力。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..domain.models import AssessmentRecord, AssessmentTask, Employee
from . import access_control
from .access_control import PermissionAction, build_access_context
from .period import normalize_period

logger = logging.getLogger(__name__)

Decision = Literal["allow", "deny"]

#: 写操作意图关键词 —— 命中即拦截并给出后台引导。
#:
#: ⚠️ 这只是**快速路径**，不是安全边界。
#:    真正的安全边界是「工具层不提供写工具」。
#:
#: ⚠️ 为什么用「动词 + 名词」**分两组**而不是一个长串：
#:    用户会在中间插词 ——「修改一下我的绩效权重」中间隔着"一下我的绩效"，
#:    连续子串匹配不到。用两个独立词组分别命中，漏网率低得多。
WRITE_ACTION_VERBS: tuple[str, ...] = (
    "改", "修改", "调整", "调", "设为", "设成", "改成", "改为",
    "录入", "录", "填", "录入", "录入", "确认", "录入",
)

WRITE_ACTION_NOUNS: tuple[str, ...] = (
    "分数", "分", "权重", "规则", "方案", "标准", "评级", "等级",
    "实测值", "实测", "指标值", "指标",
)

#: ⚠️ 这些词单独出现**不代表**写操作 —— 「我的绩效多少分」是查询。
#:    只有与动词组合才触发拦截。
#:
#: ⚠️ 「分」不能单独作名词用 —— 「多少分」「扣了多少分」都是查询。
WRITE_ACTION_NOUNS_STRICT: tuple[str, ...] = (
    "分数", "权重", "规则", "方案", "标准", "评级", "等级",
    "实测值", "实测", "指标值",
)

#: ⚠️ 疑问词 —— 出现这些词说明用户可能在**问**，而不是在**做**。
#:
#: ⚠️ 为什么需要这一层：
#:    「实测值填了吗」「权重是多少」「规则是什么」这些句子
#:    既含动词也含名词，但语义是**询问状态**，不是**下达指令**。
#:    误伤会直接毁掉问答体验 —— 用户问「我的权重是多少」被拒答，
#:    是最典型的"问答系统不好用"。
QUESTION_MARKERS: tuple[str, ...] = (
    "吗", "呢", "多少", "怎么", "如何", "为什么", "为何", "哪些",
    "哪个", "是什么", "是啥", "啥样", "在哪", "哪里", "有没有",
    "能不能", "可不可以", "行吗", "好吗", "查", "看看", "看下",
    "显示", "列一下", "算一下", "算下", "查询",
)

#: ⚠️ **显式改写构造** —— 出现这些说明用户明确给出了改写动作，
#:    此时**即使句子是疑问形式也要拦截**。
#:
#: ⚠️ 为什么需要这一层：
#:    「能不能把分数改成95分」是疑问句式，但「把分数改成95分」
#:    是明确的改写指令 —— 带目标值、带「把…改成…」结构。
#:    疑问词检查会把它误判成"询问"，从而放过写操作。
#:
#:    ⇒ 疑问词检查**不能**作为最终判据，只能在与「显式构造」不冲突时生效。
WRITE_ACTION_EXPLICIT_PATTERNS: tuple[str, ...] = (
    "改分", "调分", "改分数", "调分数", "改权重", "改规则", "改评级",
    "把分数改", "把分改", "把权重改", "把规则改", "把评级改",
    "把实测值填", "把分数设", "分数改成", "分数改为", "分数调成",
    "权重改成", "权重改为", "评级改成", "实测值填成",
    "调高", "调低", "往上抬", "往上提",
)

#: 写操作对应的后台页面 —— 用于给出可点击的引导。
WRITE_ACTION_PAGES: dict[str, str] = {
    "score": "考核记录",
    "metric_value": "算分集 / 指标实测值",
    "rule": "绩效方案",
    "rating": "考核记录（评级确认）",
    "nudge": "绩效待办",
}


@dataclass(frozen=True)
class AuthorizationDecision:
    """一次授权决策的完整结果。"""

    decision: Decision
    #: 目标人类型：self / peer / report / delegate / managed / hr
    subject_type: str = "self"
    #: 授权理由（写审计，不给用户看）
    reason: str = ""
    #: 给用户看的话
    user_message: str = ""
    #: 引导用户去哪做（可为空）
    guidance: str = ""
    #: 拦截动作标记：intercept_write / intercept_cross_dept / clarify_subject
    action: str = ""
    #: 审计级别：normal / high
    audit_level: str = "normal"
    #: 已授权的目标人 id（decision=allow 时有效）
    subject_id: str = ""
    #: 本次授权覆盖的周期
    period: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


@dataclass(frozen=True)
class QaScope:
    """问答范围（扩展了 P0 的目标人与授权信息）。"""

    scope: str
    allowed_tools: tuple[str, ...]
    period: str
    #: ★ P0：已授权的目标人（默认 = 提问者本人）
    subject_id: str = ""
    #: ★ P0：目标人类型
    subject_type: str = "self"
    #: ★ P0：授权理由（审计用）
    authorization_reason: str = ""


def detect_write_action(question: str) -> str | None:
    """检测写操作意图 —— 返回命中的描述，没有则 ``None``。

    ⚠️ 这是**快速路径**，不是安全边界。
       真正的边界是「工具层不提供写工具」——
       即使用户说「帮我把分数往上抬一点」绕过了关键词，
       模型也调不到任何写工具。

    ⚠️ 判定逻辑：**动词 + 写操作名词**同时出现才算。
       只看名词会误伤查询（「我多少分」「扣了多少分」）；
       只看动词会误伤（「改一下我的自评」其实是让用户改自己的自评）。
    """
    if not question:
        return None
    text = question.strip()
    if not text:
        return None

    verb = next((v for v in WRITE_ACTION_VERBS if v in text), None)
    if verb is None:
        return None

    # ⚠️ 疑问语境优先 —— 「实测值填了吗」「权重是多少」是**问**，不是**做**。
    #    误伤这些的代价很大：用户问「我的权重是多少」被拒答，
    #    就是"问答系统不好用"的典型表现。
    #
    # ⚠️ **但显式改写构造优先于疑问判断** ——
    #    「能不能把分数改成95分」是疑问句式，但「把分数改成95分」
    #    是明确的改写指令。若疑问判断先行，这条会被放过。
    explicit = next(
        (p for p in WRITE_ACTION_EXPLICIT_PATTERNS if p in text), None
    )
    if explicit is not None:
        return explicit
    if any(marker in text for marker in QUESTION_MARKERS):
        return None

    for noun in WRITE_ACTION_NOUNS_STRICT:
        if noun in text:
            return f"{verb}…{noun}"
    # ⚠️ 「分」只在**明确的改写动词**下才算 ——
    #    「调高」「设为」「改成」本身就表达了改分意图；
    #    「多少分」「扣了多少分」这类查询不会用到这些动词。
    if "分" in text and verb in (
        "调", "调高", "调低", "设为", "设成", "改成", "改为",
    ):
        return f"{verb}…分"
    return None


def _write_action_guidance(hit: str) -> str:
    """根据命中的写操作类型给出后台页面引导。"""
    if any(word in hit for word in ("权重", "规则", "方案", "标准")):
        return WRITE_ACTION_PAGES["rule"]
    if any(word in hit for word in ("实测值", "实测", "指标值")):
        return WRITE_ACTION_PAGES["metric_value"]
    if any(word in hit for word in ("评级", "等级")):
        return WRITE_ACTION_PAGES["rating"]
    return WRITE_ACTION_PAGES["score"]


def authorize_qa_subject(
    db: Session,
    asker: Employee,
    subject: Employee,
    *,
    task: AssessmentTask | None = None,
    period: str | None = None,
    context: access_control.AccessContext | None = None,
) -> AuthorizationDecision:
    """判定提问者是否有权查看 ``subject`` 的绩效数据。

    ⚠️ **判定顺序即安全语义**，不可调换：

    ① 本人 → 放行
    ② HR（全组织）→ 放行
       ⚠️ 必须早于部门判断 —— 否则 HR 查外部门会被误杀
    ③ 汇报线下属（含间接下属）→ 放行
    ④ 代理授权范围内 → 放行
    ⑤ ★ 同部门**同级别**同事 → 放行（**只读**）
       三条件同时满足：同部门 + 汇报线同级 + 不是我的上级
    ⑥ 跨部门 / 跨级 / 上一级 → **拒绝**（高危审计）
    """
    if context is None:
        context = build_access_context(db, asker)

    # ① 本人
    if subject.id == asker.id:
        return AuthorizationDecision(
            decision="allow",
            subject_type="self",
            reason="本人",
            subject_id=subject.id,
            period=period or "",
        )

    # ② HR —— 全组织
    if context.has_permission(PermissionAction.VIEW_ALL_SCORES):
        return AuthorizationDecision(
            decision="allow",
            subject_type="hr",
            reason="HR 全组织查询权",
            subject_id=subject.id,
            period=period or "",
        )

    # ③ 汇报线下属（闭包，含间接下属）
    if (
        subject.id in context.direct_report_ids
        or subject.id in context.reporting_line_ids
    ):
        return AuthorizationDecision(
            decision="allow",
            subject_type="report",
            reason="汇报线下属",
            subject_id=subject.id,
            period=period or "",
        )

    # ④ 代理授权
    if subject.id in context.delegated_employee_ids:
        return AuthorizationDecision(
            decision="allow",
            subject_type="delegate",
            reason="代理授权范围内",
            subject_id=subject.id,
            period=period or "",
        )

    # ⑤ ★ 同部门同级别互查（只读）
    if context.has_permission(PermissionAction.VIEW_DEPARTMENT_PEER):
        if access_control.is_peer_viewable(db, context, subject):
            return AuthorizationDecision(
                decision="allow",
                subject_type="peer",
                reason="同部门同级别同事（只读）",
                subject_id=subject.id,
                period=period or "",
            )

    # ⑥ 拒绝 —— 跨部门 / 跨级 / 上一级
    # ⚠️ 文案**不区分**具体原因 —— 区分会泄露目标人的层级与部门。
    return AuthorizationDecision(
        decision="deny",
        subject_type="none",
        reason="超出可见范围",
        user_message="该同事不在你的可见范围内。",
        guidance=(
            "绩效数据按组织范围隔离：同部门同级别同事可互查，"
            "跨部门、跨级或上级不在此范围内。如确需查看，请联系 HR。"
        ),
        action="intercept_cross_dept",
        audit_level="high",
    )


def resolve_scope(
    db: Session,
    employee: Employee,
    task_id: str,
    period: str | None,
    subject_id: str | None = None,
) -> QaScope:
    """解析问答范围。

    ⚠️ **新增参数 ``subject_id`` 为空时，行为与改造前完全一致**（查自己）。
       这样老调用方不必改，也不会突然获得新权限。
    """
    if employee.employment_status != "active" or employee.account_status != "enabled":
        raise HTTPException(403, "账号当前不可查询")
    context = build_access_context(db, employee)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise HTTPException(404, "考核任务不存在")
    canonical = normalize_period(task.period)
    if canonical is None or (period is not None and normalize_period(period) != canonical):
        raise HTTPException(400, "请求周期与任务不一致")

    # ★ P0：目标人 —— 默认是本人
    target_subject_id = subject_id or employee.id
    subject = db.get(Employee, target_subject_id)
    if subject is None:
        raise HTTPException(404, "查询对象不存在")

    # ★ P0：授权判定（硬闸门）
    decision = authorize_qa_subject(
        db, employee, subject, task=task, period=canonical, context=context
    )
    if not decision.allowed:
        # ⚠️ 越权 → 高危审计留痕
        logger.warning(
            "问答越权尝试：提问者=%s 目标=%s 任务=%s 周期=%s 级别=%s",
            employee.id, subject.id, task.id, canonical, decision.audit_level,
        )
        raise HTTPException(403, decision.user_message or "无权查询该数据")

    tools: list[str] = []
    # ★ 工具白名单取决于**目标人**是谁：
    #   - 查自己 → 三个读工具
    #   - 查同部门平级 → **裁剪版**工具（不含评语/证据原文）
    #   - 查下属/HR → 完整读工具
    if db.query(AssessmentRecord.id).filter_by(
        task_id=task.id, employee_id=subject.id,
    ).first():
        if decision.subject_type == "peer":
            # ⚠️ 平级同事：只给总分/构成，**不给**证据原文与评语
            tools.append("get_peer_score")
        else:
            tools.extend(["get_metric_values", "get_evidence", "get_scoring_breakdown"])

    department_allowed = bool(employee.department_id and (
        employee.department_id in context.managed_department_ids
        or context.has_permission(PermissionAction.VIEW_ALL_SCORES)
    ) and (task.department_id is None or task.department_id == employee.department_id))
    if department_allowed:
        tools.append("get_department_stats")
    if not tools:
        raise HTTPException(403, "无权查询该任务数据")

    return QaScope(
        scope=decision.subject_type,
        allowed_tools=tuple(tools),
        period=canonical,
        subject_id=subject.id,
        subject_type=decision.subject_type,
        authorization_reason=decision.reason,
    )


__all__ = [
    "WRITE_ACTION_NOUNS",
    "WRITE_ACTION_NOUNS_STRICT",
    "WRITE_ACTION_PAGES",
    "WRITE_ACTION_VERBS",
    "AuthorizationDecision",
    "QaScope",
    "authorize_qa_subject",
    "detect_write_action",
    "resolve_scope",
]
