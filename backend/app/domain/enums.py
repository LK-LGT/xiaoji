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
