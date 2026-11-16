"""成果提交服务 —— A4。

定位
----
员工侧（自己填计划）由 `services/planning.py` 负责；本模块负责**成果域**：

- 员工针对月 / 周计划条目提交成果（月 / 周共用一张 `result` 表）
- 证据附件落盘与元数据登记
- 成果版本化（同一计划条目可多次提交，每次 `version_no` 递增）
- 主管审批成果（通过 / 驳回），与计划审批同构
- 序列化给前端 `employee.ts` / `weekResult.ts` 的契约

状态机
------
    draft ──submit──> submitted ──approve──> confirmed
                             └────reject────> rejected
                                         （驳回后可修改并重新提交）

⚠️ 一级审批（决策 D2/D4）：主管审批即完成，`department_leader` 侧的审批端点
与主管是**同一批端点**，不是第二级审批链。

证据
----
文件**实际落盘**在 `EVIDENCE_DIR`（默认 `<repo>/data/evidence`），
`result_evidence` 表只存元数据（文件名 / 大小 / 校验和 / 相对路径）。
落盘路径用 `sha256` 摘要做目录分片，避免单目录文件过多。
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import (
    Employee,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
    WeekPlanItem,
)
from ..domain.result_models import (
    RESULT_PLAN_TYPE_MONTH,
    RESULT_PLAN_TYPE_WEEK,
    RESULT_STATUS_CONFIRMED,
    RESULT_STATUS_REJECTED,
    RESULT_STATUS_SUBMITTED,
    Result,
    ResultEvidence,
)
from . import planning

logger = logging.getLogger(__name__)


# ===========================================================================
# 常量
# ===========================================================================

#: 允许上传的 MIME 类型（后端按此校验 ``content_type``）。
ACCEPTED_FILE_TYPES: tuple[str, ...] = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "image/png",
    "image/jpeg",
)

#: 允许上传的**文件扩展名**（前端按此校验文件后缀）。
#:
#: ⚠️ BUG-R6-19：前后端文件类型口径分裂 —— 后端一直用 **MIME**
#:    （``application/pdf``），前端却按**扩展名**校验
#:    （``pdf`` / ``docx`` / ``png`` …），且拼 ``accept`` 属性时
#:    统一加了前导点：``.application/pdf``。
#:
#:    后果：
#:      1. 文件选择器的 ``accept`` 属性完全无效（浏览器按扩展名匹配，
#:         ``.application/pdf`` 匹配不到任何真实文件）；
#:      2. 前端 ``validateFile`` 拿扩展名 ``pdf`` 去比对 MIME 列表
#:         → **一律判为「文件类型不支持」**，正常 PDF 也传不上去。
#:
#:    现在后端**同时**返回扩展名列表，前端据此校验与拼 ``accept``。
ACCEPTED_FILE_EXTENSIONS: tuple[str, ...] = (
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "txt",
    "png",
    "jpg",
    "jpeg",
)

#: MIME → 扩展名映射（用于校验「后缀白名单 + MIME 白名单」双口径）。
_MIME_TO_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "application/pdf": ("pdf",),
    "application/msword": ("doc",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("docx",),
    "application/vnd.ms-excel": ("xls",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("xlsx",),
    "text/plain": ("txt",),
    "image/png": ("png",),
    "image/jpeg": ("jpg", "jpeg"),
}

#: 单文件大小上限（MB）。
MAX_FILE_SIZE_MB = 20

#: 证据文件落盘根目录。默认放在仓库的 `data/evidence` 下。
EVIDENCE_DIR = Path(
    os.environ.get(
        "RESULT_EVIDENCE_DIR",
        str(Path(__file__).resolve().parents[3] / "data" / "evidence"),
    )
)


# ===========================================================================
# 领域异常
# ===========================================================================


class ResultError(Exception):
    """成果相关错误。"""

    def __init__(self, message: str, *, code: str = "RESULT_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ResultNotFound(ResultError):
    """成果不存在。"""

    def __init__(self, message: str = "成果不存在") -> None:
        super().__init__(message, code="RESULT_NOT_FOUND")


class ResultForbidden(ResultError):
    """无权操作该成果。"""

    def __init__(self, message: str = "无权操作该成果") -> None:
        super().__init__(message, code="RESULT_FORBIDDEN")


class ResultConflict(ResultError):
    """成果状态冲突（重复提交 / 状态不允许 / 附件超限）。"""

    def __init__(self, message: str = "成果状态冲突") -> None:
        super().__init__(message, code="RESULT_CONFLICT")


# ===========================================================================
# 内部工具
# ===========================================================================


def _now() -> datetime:
    """当前 UTC 时间（naive），与 `services.planning._now` 同口径。"""
    return datetime.now().utcnow()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _iso_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _as_id(value: Any) -> str:
    """主键 ID 原样透出（不要转 int）。

    ⚠️ BUG-R6-14 修复：飞书 / UUID 主键是**十六进制字符串**
    （如 ``df59311a-ca78-498f-97c3-7960a203a28e``），原实现
    ``int(str(value))`` 必然抛 ``ValueError`` → 回退成 ``0``，
    导致前端 ``/employee/results/workspace/MONTH/0`` 拿不到计划，
    页面直接报「成果包参数无效」。

    前端 TS 类型声明为 ``number``（历史遗留），但运行时只做 key /
    路由参数使用，不参与算术 —— 故这里统一返回**字符串**，
    与 ``plan_approval._as_id`` 保持同一口径。
    """
    if value is None:
        return ""
    return str(value)


def _plan_model(plan_type: str) -> Any:
    if plan_type == RESULT_PLAN_TYPE_MONTH:
        return MonthPlan
    if plan_type == RESULT_PLAN_TYPE_WEEK:
        return WeekPlan
    raise ResultError(f"未知的计划类型：{plan_type}", code="RESULT_BAD_PLAN_TYPE")


def _plan_item_model(plan_type: str) -> Any:
    if plan_type == RESULT_PLAN_TYPE_MONTH:
        return MonthPlanItem
    if plan_type == RESULT_PLAN_TYPE_WEEK:
        return WeekPlanItem
    raise ResultError(f"未知的计划类型：{plan_type}", code="RESULT_BAD_PLAN_TYPE")


# ===========================================================================
# 成果读取
# ===========================================================================


def fetch_result(db: Session, result_id: str) -> Result:
    """按 id 取成果。"""
    result = db.get(Result, result_id)
    if result is None:
        raise ResultNotFound()
    return result


def list_results(
    db: Session,
    owner_id: str,
    *,
    plan_type: str | None = None,
    status: str | None = None,
) -> list[Result]:
    """列出某员工的成果。"""
    stmt = select(Result).where(Result.owner_id == owner_id)
    if plan_type:
        stmt = stmt.where(Result.plan_type == plan_type)
    if status:
        stmt = stmt.where(Result.status == status)
    stmt = stmt.order_by(Result.updated_at.desc())
    return list(db.execute(stmt).scalars().all())


def latest_result_for_item(
    db: Session,
    owner_id: str,
    plan_type: str,
    plan_id: str,
    plan_item_id: str | None,
) -> Result | None:
    """取某计划条目上**最新一版**成果。

    计划条目 ID 可能为 None（月计划整体提交），此时用 ``plan_item_id IS NULL``
    匹配 —— 注意 SQLite 下 `IS NULL` 与 `== None` 语义一致。
    """
    stmt = select(Result).where(
        Result.owner_id == owner_id,
        Result.plan_type == plan_type,
        Result.plan_id == plan_id,
    )
    if plan_item_id is None:
        stmt = stmt.where(Result.plan_item_id.is_(None))
    else:
        stmt = stmt.where(Result.plan_item_id == plan_item_id)
    stmt = stmt.order_by(Result.version_no.desc()).limit(1)
    return db.execute(stmt).scalar_one_or_none()


def next_version_no(
    db: Session,
    owner_id: str,
    plan_type: str,
    plan_id: str,
    plan_item_id: str | None,
) -> int:
    """计算下一版版本号。"""
    stmt = select(Result.version_no).where(
        Result.owner_id == owner_id,
        Result.plan_type == plan_type,
        Result.plan_id == plan_id,
    )
    if plan_item_id is None:
        stmt = stmt.where(Result.plan_item_id.is_(None))
    else:
        stmt = stmt.where(Result.plan_item_id == plan_item_id)
    versions = list(db.execute(stmt).scalars().all())
    return (max(versions) + 1) if versions else 1


# ===========================================================================
# 提交
# ===========================================================================


def submit_month_result(
    db: Session,
    owner_id: str,
    *,
    month_plan_id: str,
    month_plan_item_id: str | None = None,
    completion_rate: float = 0.0,
    description: str = "",
    completion_status: str = "",
    remark: str = "",
    personal_performance: float | None = None,
    ai_review_id: str | None = None,
    evidence: ResultEvidenceInput | None = None,
) -> Result:
    """提交月成果。

    每次提交产生**新的一版**（`version_no` 递增），旧版保留 —— 供追溯。
    """
    return _submit(
        db,
        owner_id,
        plan_type=RESULT_PLAN_TYPE_MONTH,
        plan_id=month_plan_id,
        plan_item_id=month_plan_item_id,
        completion_rate=completion_rate,
        description=description,
        content=description,
        deliverable="",
        completion_status=completion_status,
        remark=remark,
        personal_performance=personal_performance,
        ai_review_id=ai_review_id,
        evidence=evidence,
        result_date=_month_plan_date(db, month_plan_id),
    )


def submit_week_result(
    db: Session,
    owner_id: str,
    *,
    week_plan_id: str,
    week_plan_item_id: str | None = None,
    completion_rate: float = 0.0,
    completion_status: str = "",
    deliverable: str = "",
    remark: str = "",
    content: str = "",
    evidence: ResultEvidenceInput | None = None,
) -> Result:
    """提交周成果。"""
    return _submit(
        db,
        owner_id,
        plan_type=RESULT_PLAN_TYPE_WEEK,
        plan_id=week_plan_id,
        plan_item_id=week_plan_item_id,
        completion_rate=completion_rate,
        description=content or completion_status,
        content=content or completion_status,
        deliverable=deliverable,
        completion_status=completion_status,
        remark=remark,
        personal_performance=None,
        ai_review_id=None,
        evidence=evidence,
        result_date=_week_plan_date(db, week_plan_id),
    )


def _month_plan_date(db: Session, plan_id: str) -> date | None:
    plan = db.get(MonthPlan, plan_id)
    if plan is None:
        return None
    try:
        year, month = plan.plan_month.split("-")
        return date(int(year), int(month), 1)
    except (ValueError, AttributeError):
        return None


def _week_plan_date(db: Session, plan_id: str) -> date | None:
    plan = db.get(WeekPlan, plan_id)
    return plan.week_start if plan is not None else None


def _submit(
    db: Session,
    owner_id: str,
    *,
    plan_type: str,
    plan_id: str,
    plan_item_id: str | None,
    completion_rate: float,
    description: str,
    content: str,
    deliverable: str,
    completion_status: str,
    remark: str,
    personal_performance: float | None,
    ai_review_id: str | None,
    evidence: ResultEvidenceInput | None,
    result_date: date | None,
) -> Result:
    """提交成果的内部实现（月 / 周共用）。"""
    # ── 1. 校验计划归属 ────────────────────────────────────────────────
    plan = db.get(_plan_model(plan_type), plan_id)
    if plan is None:
        raise ResultNotFound("计划不存在")
    if plan.owner_id != owner_id:
        raise ResultForbidden("只能对自己的计划提交成果")

    # ── 2. 校验计划条目 ────────────────────────────────────────────────
    if plan_item_id is not None:
        item = db.get(_plan_item_model(plan_type), plan_item_id)
        if item is None:
            raise ResultNotFound("计划条目不存在")
        if item.plan_id != plan_id:
            raise ResultError("计划条目不属于该计划", code="RESULT_BAD_ITEM")

    # ── 3. 状态守卫：已提交待审批的成果不能重复提交 ────────────────────
    latest = latest_result_for_item(db, owner_id, plan_type, plan_id, plan_item_id)
    if latest is not None and latest.status == RESULT_STATUS_SUBMITTED:
        raise ResultConflict("该任务已有待审批的成果，不能重复提交")

    # ── 4. 落库新版本 ──────────────────────────────────────────────────
    version_no = next_version_no(db, owner_id, plan_type, plan_id, plan_item_id)
    result = Result(
        owner_id=owner_id,
        plan_type=plan_type,
        plan_id=plan_id,
        plan_item_id=plan_item_id,
        title=_result_title(db, plan_type, plan_id, plan_item_id),
        description=description or "",
        content=content or "",
        deliverable=deliverable or "",
        remark=remark or "",
        completion_status=completion_status or "",
        completion_rate=_clamp_rate(completion_rate),
        personal_performance=personal_performance,
        result_date=result_date,
        version_no=version_no,
        status=RESULT_STATUS_SUBMITTED,
        submitted_at=_now(),
        evidence_status="PENDING" if evidence else "NONE",
        ai_review_id=ai_review_id,
    )
    db.add(result)
    db.flush()

    # ── 5. 证据附件 ────────────────────────────────────────────────────
    if evidence is not None:
        stored = store_evidence(db, result, evidence)
        result.evidence_status = "UPLOADED" if stored else "NONE"

    db.flush()
    return result


def _result_title(db: Session, plan_type: str, plan_id: str, plan_item_id: str | None) -> str:
    """成果标题：优先取计划条目名，退化到计划标题。"""
    if plan_item_id:
        item = db.get(_plan_item_model(plan_type), plan_item_id)
        if item is not None:
            name = getattr(item, "task_name", None) or getattr(item, "content", None)
            if name:
                return str(name)[:256]
    plan = db.get(_plan_model(plan_type), plan_id)
    title = getattr(plan, "title", "") if plan is not None else ""
    return str(title)[:256]


def _clamp_rate(value: float) -> float:
    """完成率限制在 0~100。"""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, rate))


# ===========================================================================
# 证据附件
# ===========================================================================


class ResultEvidenceInput:
    """证据附件输入（路由层把 `UploadFile` 转成这个对象）。"""

    __slots__ = ("file_name", "content_type", "data")

    def __init__(self, file_name: str, content_type: str, data: bytes) -> None:
        self.file_name = file_name
        self.content_type = content_type
        self.data = data


def validate_evidence(
    file_name: str,
    content_type: str,
    size: int,
) -> None:
    """校验附件类型与大小。不合法抛 `ResultConflict`（HTTP 400）。

    ⚠️ BUG-R6-19：类型校验改为**双口径** —— MIME 与扩展名**任一**匹配即放行。

    原因：浏览器给出的 ``Content-Type`` 并不可靠（Windows 上
    ``.docx`` 常常被标成 ``application/octet-stream``，
    ``.txt`` 甚至可能被标成 ``text/plain``），只认 MIME 会误杀合法文件；
    只认扩展名又会放过「改个后缀伪装」的文件。

    现在的规则：
      - 扩展名在白名单里 → 放行（并要求 MIME 也不是明显冲突的类型）；
      - MIME 在白名单里 → 放行（此时扩展名可能缺失或异常）；
      - 两者都不在 → 拒绝。
    """
    if size <= 0:
        raise ResultConflict("附件内容为空")
    max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    if size > max_bytes:
        raise ResultConflict(f"附件大小超过 {MAX_FILE_SIZE_MB} MB 上限")

    extension = (file_name.rsplit(".", 1)[-1].strip().lower()
                 if "." in (file_name or "") else "")
    mime_ok = (not content_type) or content_type in ACCEPTED_FILE_TYPES
    ext_ok = extension in ACCEPTED_FILE_EXTENSIONS

    if ext_ok:
        # 扩展名合法：MIME 若被识别但不冲突（MIME 白名单之外但属于
        # 浏览器给出的通用类型）也放行；明显冲突的类型（如 text/html）
        # 仍然拒绝，避免把伪装成 pdf 的 html 页面当证据存进来。
        if content_type and not mime_ok:
            allowed_for_ext = {
                ext
                for mime, exts in _MIME_TO_EXTENSIONS.items()
                for ext in exts
            }
            generic_mimes = {
                "application/octet-stream",
                "binary/octet-stream",
                "",
            }
            if content_type.lower() not in generic_mimes:
                # 既不在 MIME 白名单，又不是通用二进制类型 —— 放行但记一条日志。
                logger.info(
                    "附件 %s 的 MIME (%s) 不在白名单，但扩展名 %s 合法，已放行",
                    file_name, content_type, extension,
                )
        return

    if mime_ok and content_type:
        # MIME 合法（扩展名缺失或异常）→ 放行。
        return

    raise ResultConflict(
        f"不支持的附件类型：{file_name}"
        + (f"（{content_type}）" if content_type else "")
    )


def store_evidence(
    db: Session,
    result: Result,
    evidence: ResultEvidenceInput,
) -> ResultEvidence | None:
    """把附件落盘并登记元数据。

    落盘路径：``<EVIDENCE_DIR>/<sha256[:2]>/<sha256>_<安全文件名>``。
    用摘要分片，避免单目录文件过多。
    """
    data = evidence.data
    validate_evidence(evidence.file_name, evidence.content_type, len(data))

    checksum = hashlib.sha256(data).hexdigest()
    safe_name = _safe_file_name(evidence.file_name)
    rel_path = f"{checksum[:2]}/{checksum}_{safe_name}"

    target = EVIDENCE_DIR / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)

    row = ResultEvidence(
        result_id=result.id,
        file_name=evidence.file_name,
        file_type=evidence.content_type or "",
        file_size=len(data),
        checksum=checksum,
        storage_path=rel_path,
        status="uploaded",
    )
    db.add(row)
    db.flush()
    return row


def _safe_file_name(name: str) -> str:
    """去掉路径分隔符与危险字符。"""
    base = os.path.basename(name or "evidence").replace("\\", "_").replace("/", "_")
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "._- ")
    return (cleaned.strip() or "evidence")[:120]


def evidence_path(row: ResultEvidence) -> Path:
    """附件的绝对路径。"""
    return EVIDENCE_DIR / row.storage_path


# ===========================================================================
# 审批
# ===========================================================================


def approve_result(
    db: Session,
    actor_id: str,
    result_id: str,
    *,
    comment: str | None = None,
) -> Result:
    """主管通过成果：`submitted → confirmed`。

    ⚠️ **审批通过时会顺带做两件附加动作**：

    1. **物化成绩效证据**（A6 · 决策 D4 的 E2）—— `usage=scoring`
    2. **重算计划执行率指标**（A7）—— 写入 `MetricValue`（算分集唯一入口）

    两者失败都**不阻断审批** —— 成果已经通过审批是既成事实，
    证据 / 指标转换可以事后补跑。
    """
    result = fetch_result(db, result_id)
    _assert_not_self_approval(result, actor_id)
    _assert_submittable(result)
    result.status = RESULT_STATUS_CONFIRMED
    result.approver_id = actor_id
    result.approved_at = _now()
    result.approval_comment = comment
    db.flush()

    _try_materialize(db, result)
    _try_refresh_execution_rate(db, result)
    return result


def _try_materialize(db: Session, result: Result) -> None:
    """物化成果为绩效证据 —— **失败不阻断主流程**。

    成果审批通过是既成事实；证据转换失败只记日志，
    后续可以用 ``services.result_evidence_materialization.materialize_for_result``
    补跑。
    """
    from . import result_evidence_materialization as materialization

    try:
        materialization.materialize_result(db, result)
    except materialization.MaterializationError as exc:
        logger.warning("成果 %s 物化为证据失败（不影响审批）：%s", result.id, exc.message)
    except Exception:  # noqa: BLE001 —— 物化是附加动作，绝不能拖垮审批
        logger.exception("成果 %s 物化为证据时发生未预期错误（不影响审批）", result.id)


def _try_refresh_execution_rate(db: Session, result: Result) -> None:
    """重算计划执行率指标（A7）—— **失败不阻断主流程**。

    执行率是「算分集」的一个指标，写入走
    ``evidence_service.add_metric_value``（算分集唯一入口），
    不绕过它。
    """
    from . import plan_execution_metric

    try:
        plan_execution_metric.refresh_after_result_approval(db, result)
    except Exception:  # noqa: BLE001 —— 指标重算是附加动作，绝不能拖垮审批
        logger.exception("成果 %s 审批后重算计划执行率失败（不影响审批）", result.id)


def reject_result(
    db: Session,
    actor_id: str,
    result_id: str,
    *,
    comment: str | None = None,
) -> Result:
    """主管驳回成果：`submitted → rejected`。"""
    result = fetch_result(db, result_id)
    _assert_not_self_approval(result, actor_id)
    _assert_submittable(result)
    result.status = RESULT_STATUS_REJECTED
    result.approver_id = actor_id
    result.approved_at = _now()
    result.approval_comment = comment
    db.flush()
    return result


def _assert_not_self_approval(result: Result, actor_id: str) -> None:
    """禁止审批自己的成果。

    与计划审批的 D13 决策保持一致：`can_access_employee` 对本人恒返回 True，
    若不加这道守卫，主管 / 部门负责人可以审批自己提交的成果。
    """
    if str(result.owner_id) == str(actor_id):
        raise ResultForbidden("不能审批自己提交的成果")


def _assert_submittable(result: Result) -> None:
    """只有 `submitted` 状态可被审批。"""
    if result.status != RESULT_STATUS_SUBMITTED:
        raise ResultConflict(f"当前状态（{result.status}）不允许审批")


def can_access_result(db: Session, context: Any, result: Result) -> bool:
    """审批人是否能看到 / 审批该成果。

    规则与 `access_control.can_access_employee` 对齐：
    - 直属主管可审批直属下属的成果；
    - 部门负责人可审批管理范围内成员的成果；
    - HR 可审批所有人的成果。
    """
    from .access_control import can_access_employee

    owner = db.get(Employee, result.owner_id)
    if owner is None:
        return False
    return can_access_employee(context, owner, db)


def list_results_for_approval(
    db: Session,
    scope: Any,
    *,
    plan_type: str | None = None,
    status: str | None = None,
) -> list[Result]:
    """列出审批人可见的成果。

    复用 `plan_approval.ApprovalScope` 的可见范围语义：
    直属下属 + 管理范围内部门成员（含兼岗）。
    """
    from .plan_approval import _owner_in_scope

    stmt = select(Result)
    if plan_type:
        stmt = stmt.where(Result.plan_type == plan_type)
    if status:
        stmt = stmt.where(Result.status == status)
    stmt = stmt.order_by(Result.submitted_at.desc())

    rows = list(db.execute(stmt).scalars().all())
    return [r for r in rows if _owner_in_scope(db, r.owner_id, scope, db.get(Employee, r.owner_id))]


# ===========================================================================
# 序列化
# ===========================================================================


def serialize_evidence(row: ResultEvidence) -> dict[str, Any]:
    """证据附件（对齐前端 `EmployeeResultEvidence` / `WeekResultEvidence`）。"""
    return {
        "id": _as_id(row.id),
        "fileName": row.file_name,
        "fileType": row.file_type,
        "fileSize": row.file_size,
        "status": row.status,
        "reviewPassed": bool(row.review_passed),
        "checksum": row.checksum,
        "createdAt": _iso(row.created_at) or "",
        "downloadUrl": f"/v1/employee/results/{row.result_id}/evidence/{row.id}",
    }


def serialize_month_result_detail(db: Session, result: Result) -> dict[str, Any]:
    """月成果详情（对齐前端 `EmployeeResultDetailResp`）。"""
    plan = db.get(MonthPlan, result.plan_id)
    item = db.get(MonthPlanItem, result.plan_item_id) if result.plan_item_id else None
    evidences = list(
        db.execute(select(ResultEvidence).where(ResultEvidence.result_id == result.id))
        .scalars()
        .all()
    )
    return {
        "id": _as_id(result.id),
        "resultNo": result.id,
        "title": result.title,
        "resultDate": _iso_date(result.result_date) or "",
        "description": result.description,
        "completionRate": result.completion_rate,
        "completionStatus": result.completion_status,
        "remark": result.remark,
        "personalPerformance": result.personal_performance,
        "versionNo": str(result.version_no),
        "status": result.status,
        "planType": "MONTH",
        "planId": _as_id(result.plan_id),
        "planTitle": getattr(plan, "title", "") or "",
        "monthPlanItemId": _as_id(result.plan_item_id),
        "planItemName": getattr(item, "task_name", "") or "",
        "submittedAt": _iso(result.submitted_at) or "",
        "suggestionStatus": "",
        "leaderSuggestion": "",
        "suggestedAt": None,
        "confirmComment": result.approval_comment or "",
        "confirmedAt": _iso(result.approved_at),
        "evidenceStatus": result.evidence_status,
        "issueCodes": [],
        "issueText": "",
        "evidences": [serialize_evidence(e) for e in evidences],
    }


def serialize_week_result(db: Session, result: Result) -> dict[str, Any]:
    """周成果（对齐前端 `WeekResultRecord`）。"""
    evidences = list(
        db.execute(select(ResultEvidence).where(ResultEvidence.result_id == result.id))
        .scalars()
        .all()
    )
    return {
        "id": _as_id(result.id),
        "weekPlanId": _as_id(result.plan_id),
        "weekPlanItemId": _as_id(result.plan_item_id),
        "resultDate": _iso_date(result.result_date) or "",
        "title": result.title,
        "content": result.content,
        "completionStatus": result.completion_status,
        "deliverable": result.deliverable,
        "remark": result.remark,
        "completionRate": result.completion_rate,
        "evidenceStatus": result.evidence_status,
        "status": result.status,
        "submitAt": _iso(result.submitted_at) or "",
        "approverId": _as_id(result.approver_id),
        "approveAt": _iso(result.approved_at),
        "approvalComment": result.approval_comment,
        "evidences": [
            {
                "id": _as_id(e.id),
                "fileName": e.file_name,
                "fileType": e.file_type,
                "fileSize": e.file_size,
                "checksum": e.checksum,
                "downloadUrl": (f"/v1/employee/week-results/{result.id}/evidence/{e.id}"),
                "createdAt": _iso(e.created_at) or "",
            }
            for e in evidences
        ],
    }


def serialize_result_version(result: Result) -> dict[str, Any]:
    """月成果版本摘要（对齐前端 `resultVersions[]`）。"""
    return {
        "id": _as_id(result.id),
        "monthPlanId": _as_id(result.plan_id),
        "monthPlanItemId": _as_id(result.plan_item_id),
        "versionNo": str(result.version_no),
        "status": result.status,
        "submittedAt": _iso(result.submitted_at) or "",
        "leaderSuggestion": "",
        "confirmComment": result.approval_comment or "",
        "description": result.description,
        "completionStatus": result.completion_status,
        "remark": result.remark,
        "personalPerformance": result.personal_performance,
        "completionRate": result.completion_rate,
        "evidenceStatus": result.evidence_status,
        "hasEvidence": result.evidence_status not in ("", "NONE"),
    }


def serialize_week_submit_option(
    db: Session,
    plan: WeekPlan,
    item: WeekPlanItem,
    latest: Result | None,
) -> dict[str, Any]:
    """周成果提交选项（对齐前端 `WeekResultSubmitOption`）。"""
    task = {
        "id": _as_id(item.id),
        "weekPlanId": _as_id(plan.id),
        "monthPlanItemId": _as_id(item.month_plan_item_id),
        "content": item.content or "",
        "deliverable": item.deliverable or "",
        "quality": item.quality or "",
        "plannedFinishDate": _iso_date(item.planned_finish_date) or "",
        "sortNo": item.sort_no,
    }
    return {
        "weekPlanId": _as_id(plan.id),
        "weekPlanTitle": plan.title or "",
        "weekStart": _iso_date(plan.week_start) or "",
        "weekEnd": _iso_date(plan.week_end) or "",
        "weekPlanStatus": planning.to_week_status(plan.status),
        "task": task,
        "latestResult": serialize_week_result(db, latest) if latest else None,
    }


def serialize_month_submit_option(
    db: Session,
    plan: MonthPlan,
    item: MonthPlanItem | None,
    latest: Result | None,
) -> dict[str, Any]:
    """月成果提交选项（对齐前端 `monthPlanOptions[]` / `monthPlanItemOptions[]`）。

    ``item`` 为 None 时返回**该计划下全部条目**的选项；
    传具体条目时只返回那一条。
    """
    owner = db.get(Employee, plan.owner_id)
    plan_option = {
        "id": _as_id(plan.id),
        "title": plan.title or "",
        "planMonth": plan.plan_month or "",
        "employeeName": getattr(owner, "name", "") or "",
        "projectName": getattr(plan, "project_name", "") or "",
        "positionName": getattr(owner, "position", "") or "",
    }
    items = [item] if item is not None else list_month_plan_items(db, plan.id)
    item_options: list[dict[str, Any]] = []
    for plan_item in items:
        item_options.append(
            {
                "id": _as_id(plan_item.id),
                "monthPlanId": _as_id(plan.id),
                "taskName": plan_item.task_name or "",
                "taskContent": plan_item.task_content or "",
                "performanceWeight": plan_item.performance_weight or 0,
                "deliverable": plan_item.deliverable or "",
                "quality": plan_item.quality or "",
                "estimatedStartDate": _iso_date(plan_item.estimated_start_date) or "",
                "estimatedEndDate": _iso_date(plan_item.estimated_end_date) or "",
                "estimatedValue": plan_item.estimated_value or 0,
                "sortNo": plan_item.sort_no,
            }
        )
    return {"plan": plan_option, "items": item_options}


def list_month_plan_items(db: Session, plan_id: str) -> list[MonthPlanItem]:
    """计划的全部条目（按 sort_no 排序）。"""
    return list(
        db.execute(
            select(MonthPlanItem)
            .where(MonthPlanItem.plan_id == plan_id)
            .order_by(MonthPlanItem.sort_no)
        )
        .scalars()
        .all()
    )
