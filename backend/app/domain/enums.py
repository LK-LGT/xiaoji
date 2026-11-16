"""领域枚举 —— 所有状态机的取值集合。

约定：枚举值一律以字符串落库，服务层/智能体边界用 Pydantic 重新校验。
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# 审核 / 生命周期
# ---------------------------------------------------------------------------

class AssessmentStatus(StrEnum):
    """考核任务状态（决策①：自评 / 评分 / 申诉三套审批的状态机载体）。"""

    DRAFT = "draft"
    SELF_REVIEW_OPEN = "self_review_open"
    SELF_REVIEW_SUBMITTED = "self_review_submitted"
    MANAGER_SCORING = "manager_scoring"
    DEPT_REVIEW = "dept_review"
    CALIBRATED = "calibrated"
    HR_REVIEW = "hr_review"
    ARCHIVED = "archived"
    RETURNED = "returned"
    APPEALING = "appealing"
    APPEAL_REVIEWING = "appeal_reviewing"


class RecordStatus(StrEnum):
    """单条员工考核记录的状态（任务内）。"""

    NOT_STARTED = "not_started"
    SELF_REVIEW_OPEN = "self_review_open"
    SELF_REVIEW_SUBMITTED = "self_review_submitted"
    MANAGER_SCORING = "manager_scoring"
    DEPT_REVIEW = "dept_review"
    CALIBRATED = "calibrated"
    HR_REVIEW = "hr_review"
    ARCHIVED = "archived"
    RETURNED = "returned"
    APPEALING = "appealing"


class SubmitSource(StrEnum):
    """填报入口来源（决策：两个入口不是两套系统，答案落同一张标准表）。"""

    FEISHU_APPROVAL = "feishu_approval"
    H5 = "h5"


class RatingPolicy(StrEnum):
    """强制分布策略开关（决策⑦：第一期只做分布对比，不强制比例）。"""

    REFERENCE_ONLY = "reference_only"
    FIXED_DISTRIBUTION = "fixed_distribution"


class ApprovalKind(StrEnum):
    """飞书审批定义的三套（决策①）。"""

    SELF_REVIEW = "self_review"
    MANAGER_SCORING = "manager_scoring"
    APPEAL = "appeal"


# ---------------------------------------------------------------------------
# 证据域
# ---------------------------------------------------------------------------

class EvidenceSource(StrEnum):
    """证据来源。覆盖我们真实存在的全部通道（reviewops 原版只有 4 个且含 mock）。"""

    FEISHU_APPROVAL = "feishu_approval"
    ATTENDANCE = "attendance"
    EXCEL_IMPORT = "excel_import"
    SYSTEM_API = "system_api"
    PEER_REVIEW = "peer_review"
    MANUAL_UPLOAD = "manual_upload"
    # 计划成果物化（A6 · 决策 D4 的 E2「转换物化」）。
    # 成果审批通过时由 `services.result_evidence_materialization` 生成，
    # `usage=scoring` —— 与考勤的 `reference_only` 相反，成果是真业绩，进算分集。
    PLAN_RESULT = "plan_result"


class EvidenceUsage(StrEnum):
    """证据用途 —— 决策②的物理落点。

    SCORING         : 可参与算分，进入 MetricValue 算分集
    REFERENCE_ONLY  : 仅作参考（如考勤异常），永不进算分集
    """

    SCORING = "scoring"
    REFERENCE_ONLY = "reference_only"


class EvidenceStatus(StrEnum):
    """证据审核状态（沿用 reviewops 的置信度路由）。"""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


class Visibility(StrEnum):
    """可见性 / 员工授权（沿用 reviewops 的 consent gate）。"""

    PRIVATE_DRAFT = "private_draft"
    SHARE_WITH_MANAGER = "share_with_manager"
    ALLOW_FOR_REVIEW = "allow_for_review"
    HR_ONLY = "hr_only"


class AttendanceAnomalyType(StrEnum):
    """考勤异常六类（决策②：全部展示给主管，均不自动扣分）。"""

    LATE = "late"
    EARLY_LEAVE = "early_leave"
    MISSING_PUNCH = "missing_punch"
    OUTSIDE = "outside"
    LEAVE = "leave"
    OVERTIME = "overtime"


# ---------------------------------------------------------------------------
# 输出域
# ---------------------------------------------------------------------------

class DraftStatus(StrEnum):
    """AI 草稿状态 —— 必须人工确认后才可生效。"""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ConfigSource(StrEnum):
    """配置来源（决策③：多维表格由 HR 直改，系统定时读）。"""

    BITABLE = "bitable"
    LOCAL = "local"


class SyncStatus(StrEnum):
    """配置同步状态（决策③：三道保险的记录载体）。"""

    OK = "ok"
    VALIDATION_FAILED = "validation_failed"
    READ_FAILED = "read_failed"
    FROZEN = "frozen"


# ---------------------------------------------------------------------------
# 计划域
# ---------------------------------------------------------------------------

class PlanStatus(StrEnum):
    """计划状态机（月 / 周 / 日计划共用一套状态机）。

    状态流转（合法迁移见 ``services/planning.plan_state_machine``）::

        draft ──submit──> submitted
        submitted ──withdraw──> draft
        submitted ──approve──> approved
        submitted ──reject──> rejected
        approved ──withdraw──> draft

    - **draft**：草稿，员工可自由编辑。
    - **submitted**：已提交，进入审批队列，不可编辑。
    - **withdrawn**：已撤回（前端枚举值为 ``paused`` / ``canceled`` 的子集语义），
      员工可再次编辑并重新提交。
    - **pending / approved / rejected**：审批态，由主管 / 部门负责人流转。
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    WITHDRAWN = "withdrawn"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PlanPeriodType(StrEnum):
    """计划周期类型。"""

    MONTH = "month"
    WEEK = "week"
    DAY = "day"


class PlanTaskType(StrEnum):
    """计划任务类型（当前仅常规任务，预留扩展）。"""

    REGULAR = "REGULAR"


class PlanVersionState(StrEnum):
    """计划版本记录状态。

    对应前端 ``recordVersion`` / ``restoredFromVersionId`` 语义：
    开启 ``recordVersion`` 时每次保存都留一份快照，``restoredFromVersionId``
    可指向任意历史快照做恢复。
    """

    ACTIVE = "active"
    ARCHIVED = "archived"


class PlanVersionSource(StrEnum):
    """计划草稿版本的产生来源。

    对应前端 ``PlanDraftVersionSource``：
    ``AI_GENERATE`` / ``AI_OPTIMIZE`` / ``MANUAL_SAVE`` / ``RESTORED_SAVE``。
    """

    AI_GENERATE = "AI_GENERATE"
    AI_OPTIMIZE = "AI_OPTIMIZE"
    MANUAL_SAVE = "MANUAL_SAVE"
    RESTORED_SAVE = "RESTORED_SAVE"


class PlanApprovalAction(StrEnum):
    """计划审批动作（写入审批流水，区分谁做了什么）。"""

    SUBMIT = "submit"
    WITHDRAW = "withdraw"
    APPROVE = "approve"
    REJECT = "reject"
    RESUBMIT = "resubmit"
    ARCHIVE = "archive"


# ---------------------------------------------------------------------------
# 争议域
# ---------------------------------------------------------------------------

class DisputeStatus(StrEnum):
    """绩效争议案件状态机。

    状态流转::

        submitted ──assign reviewer──> reviewing
        reviewing ──need supplement──> needs_supplement
        needs_supplement ──supplement──> reviewing
        reviewing ──decide──> decided
        decided ──archive──> archived

    - **submitted**：员工已提交争议，等待指派评审人。
    - **reviewing**：评审进行中，可收集意见。
    - **needs_supplement**：材料不足，退回员工补充。
    - **decided**：已裁决（采纳 / 不采纳）。
    - **archived**：已归档，不可再变更。
    """

    SUBMITTED = "SUBMITTED"
    REVIEWING = "REVIEWING"
    NEEDS_SUPPLEMENT = "NEEDS_SUPPLEMENT"
    DECIDED = "DECIDED"
    ARCHIVED = "ARCHIVED"


class DisputeOpinion(StrEnum):
    """评审意见类型。"""

    SUPPORT = "SUPPORT"
    REJECT = "REJECT"
    SUPPLEMENT = "SUPPLEMENT"


class DisputeDecision(StrEnum):
    """争议裁决结果。"""

    SUPPORT = "SUPPORT"
    REJECT = "REJECT"
    PARTIAL = "PARTIAL"


class DisputeRecusalStatus(StrEnum):
    """评审人回避状态。"""

    NONE = "NONE"
    RECUSED = "RECUSED"


class DisputeReviewerSource(StrEnum):
    """评审人指派来源。"""

    MANUAL = "MANUAL"
    AUTO = "AUTO"
    DEPT_LEADER = "DEPT_LEADER"


class DisputePackageStatus(StrEnum):
    """证据包状态。"""

    PENDING = "PENDING"
    READY = "READY"
    MISSING = "MISSING"


# ---------------------------------------------------------------------------
# AI 域
# ---------------------------------------------------------------------------

class AiReviewStatus(StrEnum):
    """AI 复核执行状态。

    - **SUCCESS**：模型 + 规则均成功。
    - **RULE_ONLY**：仅规则引擎产出（模型未配置或调用失败）。
    - **MODEL_FAILED**：模型调用失败。
    """

    SUCCESS = "SUCCESS"
    RULE_ONLY = "RULE_ONLY"
    MODEL_FAILED = "MODEL_FAILED"


class AiRiskLevel(StrEnum):
    """AI 复核风险等级。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AiBizType(StrEnum):
    """AI 复核的业务对象类型。"""

    MONTH_PLAN = "MONTH_PLAN"
    WEEK_PLAN = "WEEK_PLAN"
    DAY_PLAN = "DAY_PLAN"
    RESULT = "RESULT"


class AiConversationStatus(StrEnum):
    """AI 会话状态。

    - **active**：活跃会话，可继续追问。
    - **archived**：已归档，只读。
    """

    ACTIVE = "active"
    ARCHIVED = "archived"


class AiMessageRole(StrEnum):
    """AI 会话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class AiSuggestionAction(StrEnum):
    """AI 建议采纳动作。

    对应前端 ``AiSuggestionAction.actionCode``。
    """

    PREVIEW = "PREVIEW"
    APPLY_ALL = "APPLY_ALL"
    APPLY_ITEM = "APPLY_ITEM"
    APPLY_FIELDS = "APPLY_FIELDS"
    ADOPT_WITH_EDIT = "ADOPT_WITH_EDIT"
    IGNORE = "IGNORE"
