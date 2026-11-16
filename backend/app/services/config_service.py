"""配置服务 —— 决策③「多维表格 HR 直改 + 三道保险」的落点。

三道保险：
1. 读入校验：权重合计 = 100、指标 ID 唯一、口径字段非空；校验失败告警且不生效
2. 冻结快照：考核任务启动时复制配置到本地库，考核中途改配置不影响已评分数
3. 失败兜底：读取失败时使用上次快照继续运行，并通知 HR
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ..domain.enums import ConfigSource, SyncStatus
from ..domain.models import (
    AssessmentTask,
    ConfigSnapshot,
    ConfigSyncLog,
    FormTemplate,
    Metric,
    Rubric,
    WeightSet,
)

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """配置校验失败 —— 触发告警且不生效。"""


class ConfigProvider(Protocol):
    """配置来源协议。

    实现两个：
    - BitableProvider : 从飞书多维表格读（决策③的主路径）
    - LocalProvider   : 从本地库读（多维表格未就绪时的兜底）
    """

    source: str

    def load(self, department_id: str | None, position: str | None) -> dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# 第一道保险：读入校验
# ---------------------------------------------------------------------------


def validate_config(
    metrics: list[dict[str, Any]],
    weights: list[dict[str, Any]],
    rubric: list[dict[str, Any]],
) -> None:
    """校验配置。失败抛 ConfigValidationError，调用方负责"告警且不生效"。

    校验项（对齐决策③）：
    1. 权重合计 = 100
    2. 指标 ID 唯一
    3. 口径字段非空（calculation / data_source）
    4. 权重引用的指标必须已定义
    5. 权重必须为正数（负值 / 零值都不合法）
    6. 有权重的指标必须配置等级区间（rubric）

    ⚠️ 第 5 项：只校验「合计 = 100」是不够的。
       ``-70 + 170 = 100`` 能通过合计校验，但归一化后会产生**负权重**，
       把好指标扣成负贡献、把差指标放大成 >100 的贡献，
       最终总分跑到 0~100 之外（实测能算出 128）。
       `scoring.score_record` 已在运行时兜底阻断，这里是**第一道保险** ——
       两道门保持一致，配置阶段就拦下，不让坏配置进入快照。

    ⚠️ 第 6 项：有权重却没配等级区间的指标，算分时会因"定不了级"而
       **不参与**（P0 口径）—— 权重会被重新归一到其它指标上。
       也就是说配置漏了 rubric = 分数被悄悄改变。必须在冻结前拦下。
    """
    errors: list[str] = []

    # ── 权重解析（先解析一次，后面合计与合法性检查共用）──
    # ⚠️ 不能直接 `float(w.get("weight"))`：非数字权重会抛 ValueError，
    #    而这里要的是「收集所有违规后一次性报出」，不是抛异常中断。
    parsed_weights: list[tuple[str | None, float | None, object]] = []
    for w in weights:
        code = w.get("metric_code")
        raw = w.get("weight", 0)
        try:
            parsed_weights.append((code, float(raw), raw))
        except (TypeError, ValueError):
            parsed_weights.append((code, None, raw))

    # 1. 权重合计（只累加能解析出来的）
    total_weight = sum(v for _, v, _ in parsed_weights if v is not None)
    if abs(total_weight - 100.0) > 1e-6:
        errors.append(f"权重合计必须等于 100，当前为 {total_weight}")

    # 2. 指标 ID 唯一
    codes = [m.get("code") for m in metrics]
    dupes = {c for c in codes if codes.count(c) > 1}
    if dupes:
        errors.append(f"指标 ID 重复：{sorted(dupes)}")

    # 3. 口径字段非空
    for m in metrics:
        if not (m.get("calculation") or "").strip():
            errors.append(f"指标 {m.get('code')} 的 calculation（口径）为空")
        if not (m.get("data_source") or "").strip():
            errors.append(f"指标 {m.get('code')} 的 data_source 为空")

    # 4. 权重引用的指标必须已定义
    defined = set(codes)
    for w in weights:
        code = w.get("metric_code")
        if code not in defined:
            errors.append(f"权重引用了未定义的指标 {code}")

    # 5. 权重必须为正数
    for code, weight, raw in parsed_weights:
        if weight is None:
            errors.append(f"指标 {code} 的权重不是数字：{raw!r}")
            continue
        if weight < 0:
            errors.append(f"指标 {code} 的权重不得为负：{weight}")
        elif weight == 0:
            errors.append(f"指标 {code} 的权重不得为 0")

    # 6. 有权重的指标必须配置等级区间
    rubric_by_code = {r.get("metric_code"): r for r in rubric}
    for w in weights:
        code = w.get("metric_code")
        if code not in defined:
            continue  # 已在第 4 项报过
        entry = rubric_by_code.get(code)
        levels = None
        if isinstance(entry, dict):
            levels = entry.get("levels_json") or entry.get("levels")
        if not levels:
            errors.append(f"指标 {code} 有权重但未配置等级区间（rubric）")

    if errors:
        raise ConfigValidationError("；".join(errors))


# ---------------------------------------------------------------------------
# 第二道保险：冻结快照
# ---------------------------------------------------------------------------


def freeze_snapshot(
    db: Session,
    task: AssessmentTask,
    provider: ConfigProvider,
) -> ConfigSnapshot:
    """把当前配置整体复制成快照，绑定到任务。

    考核中途 HR 改配置，本任务的计算仍引用快照 —— 已评的分数不受影响。
    """
    try:
        raw = provider.load(task.department_id, task.position)
    except Exception as exc:  # 第三道保险：读取失败 → 用上次快照继续运行
        logger.warning("配置读取失败，尝试使用上次快照: %s", exc)
        existing = _latest_snapshot_for_scope(db, task.department_id, task.position)
        if existing is None:
            _log_sync(db, provider.source, SyncStatus.READ_FAILED, str(exc), None)
            raise ConfigValidationError(f"配置读取失败且无可用快照：{exc}") from exc
        snapshot = ConfigSnapshot(
            task_id=task.id,
            metrics_json=existing.metrics_json,
            weights_json=existing.weights_json,
            rubric_json=existing.rubric_json,
            form_template_json=existing.form_template_json,
            config_version=existing.config_version,
            frozen_at=existing.frozen_at,
        )
        db.add(snapshot)
        db.flush()
        task.snapshot_id = snapshot.id
        _log_sync(db, provider.source, SyncStatus.FROZEN, f"沿用快照 {existing.id}", existing.id)
        return snapshot

    metrics = raw.get("metrics", [])
    weights = raw.get("weights", [])
    rubric = raw.get("rubric", [])

    # 第一道保险：校验失败 → 告警且不生效
    try:
        validate_config(metrics, weights, rubric)
    except ConfigValidationError as exc:
        _log_sync(db, provider.source, SyncStatus.VALIDATION_FAILED, str(exc), None)
        logger.error("配置校验失败，本次配置不生效: %s", exc)
        raise

    snapshot = ConfigSnapshot(
        task_id=task.id,
        metrics_json=metrics,
        weights_json=weights,
        rubric_json=rubric,
        form_template_json=raw.get("form_template"),
        config_version=raw.get("version"),
    )
    db.add(snapshot)
    db.flush()
    task.snapshot_id = snapshot.id
    _log_sync(db, provider.source, SyncStatus.FROZEN, "快照已冻结", snapshot.id)
    return snapshot


def _latest_snapshot_for_scope(
    db: Session, department_id: str | None, position: str | None
) -> ConfigSnapshot | None:
    """取同部门同岗位最近一次成功快照（失败兜底用）。"""
    q = (
        db.query(ConfigSnapshot)
        .join(AssessmentTask, AssessmentTask.snapshot_id == ConfigSnapshot.id)
        .filter(AssessmentTask.department_id == department_id)
    )
    if position:
        q = q.filter(AssessmentTask.position == position)
    return q.order_by(ConfigSnapshot.frozen_at.desc()).first()


def _log_sync(
    db: Session,
    source: str,
    status: SyncStatus,
    message: str | None,
    snapshot_used: str | None,
) -> None:
    db.add(
        ConfigSyncLog(
            source=source,
            status=status.value,
            message=message,
            snapshot_used=snapshot_used,
        )
    )


# ---------------------------------------------------------------------------
# 配置来源实现
# ---------------------------------------------------------------------------


class LocalProvider:
    """从本地库读配置。

    现阶段多维表格接入前的主路径；也是失败兜底的数据来源。
    """

    source = ConfigSource.LOCAL.value

    def __init__(self, db: Session) -> None:
        self.db = db

    def load(self, department_id: str | None, position: str | None) -> dict[str, Any]:
        ws_q = self.db.query(WeightSet).filter(WeightSet.active.is_(True))
        if department_id:
            ws_q = ws_q.filter(WeightSet.department_id == department_id)
        if position:
            ws_q = ws_q.filter(WeightSet.position == position)
        weight_set = ws_q.order_by(WeightSet.version.desc()).first()
        if weight_set is None:
            raise ConfigValidationError(
                f"未找到部门 {department_id} 岗位 {position} 的权重配置"
            )

        codes = [w["metric_code"] for w in weight_set.weights_json]
        metrics = [
            {
                "code": m.code,
                "name": m.name,
                "unit": m.unit,
                "calculation": m.calculation,
                "data_source": m.data_source,
                "caliber": m.caliber,
                "owner": m.owner,
                "is_attendance": m.is_attendance,
            }
            for m in self.db.query(Metric).filter(Metric.code.in_(codes)).all()
        ]
        rubric = [
            {"metric_code": r.metric_code, "levels_json": r.levels_json}
            for r in self.db.query(Rubric)
            .filter(Rubric.weight_set_id == weight_set.id)
            .all()
        ]

        ft_q = self.db.query(FormTemplate).filter(FormTemplate.active.is_(True))
        if department_id:
            ft_q = ft_q.filter(FormTemplate.department_id == department_id)
        if position:
            ft_q = ft_q.filter(FormTemplate.position == position)
        form = ft_q.first()

        return {
            "metrics": metrics,
            "weights": weight_set.weights_json,
            "rubric": rubric,
            "form_template": (
                {"id": form.id, "name": form.name, "fields": form.fields_json} if form else None
            ),
            "version": str(weight_set.version),
        }


class BitableProvider:
    """从飞书多维表格读配置（决策③的主路径）。

    具体读取逻辑待接飞书 SDK；此处定义接口与校验入口，
    保证"校验失败告警且不生效"的语义在切到多维表格后完全一致。
    """

    source = ConfigSource.BITABLE.value

    def __init__(self, reader: Any) -> None:
        # reader: 一个可调用对象，返回 {"metrics": [...], "weights": [...], "rubric": [...], ...}
        self._reader = reader

    def load(self, department_id: str | None, position: str | None) -> dict[str, Any]:
        raw = self._reader(department_id, position)
        if not isinstance(raw, dict):
            raise ConfigValidationError("多维表格读取结果格式非法")
        return raw
