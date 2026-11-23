"""网盘剪辑工作量 —— 人工填报与月度聚合服务（BP-2 / P3）。

职责
----
1. **人工填报**：完播率 / 留存率等网盘里没有的数据
   ⇒ 支持**前端上传**与**飞书端上传**两条通路。
2. **月度聚合**：把「网盘条数」与「人工指标」分别写成
   **独立的 ``MetricValue``**（2026-11-21 业务确认）。

⚠️ **为什么分成两个 metric_code**
``video_count``（网盘条数）与 ``completion_rate``（完播率）
是**量纲不同**的两类指标。合成一个综合分会让
「谁贡献了多少」无法拆开，也没法分别设目标值。
⇒ 月度聚合写**两条** ``MetricValue``，由绩效方案的权重去组合。

⚠️ **网盘条数取哪个值**
``total_count``（机器判定）还是 ``adjusted_count``（人工裁定后）？
⇒ **优先用 ``adjusted_count``**（有人工裁定时以裁定为准），
但**原始值仍留在 ``total_count``** ⇒ 可对照、可审计。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.pan_workload_models import (
    PanManualMetric,
    PanManualMetricArtifact,
    PanWorkloadDaily,
)

logger = logging.getLogger(__name__)

#: 网盘条数的 metric_code
METRIC_VIDEO_COUNT = "video_count"
#: 完播率
#:
#: ⚠️ **必须带 ``video_`` 前缀** —— 不能直接叫 ``completion_rate``。
#: 真实库里 ``retention_rate`` **已被 HR 占用**（``metric`` 表：
#: ``retention_rate`` / 名称「留任率」/ ``data_source=hr_system`` / ``owner=HR``），
#: 语义是**员工留存率**，与短视频的**视频留存率**完全不是一回事。
#: ⚠️ 同名 ⇒ 同一条 ``MetricValue`` ⇒ 两个业务互相覆写 ⇒ 绩效错乱。
#: ⇒ 短视频侧一律用 ``video_`` 前缀，三条指标自成一套。
METRIC_COMPLETION_RATE = "video_completion_rate"
#: 视频留存率（**不是** HR 的「留任率」—— 见上方说明）
METRIC_RETENTION_RATE = "video_retention_rate"

#: ★ **普通视频条数**（2026-11-23 业务确认）
#:
#: ⚠️ **为什么数量与比率要分开存**
#: 数量（条）与比率（%）**量纲不同**，塞进同一列会让
#: 「值域校验」和「月度聚合的平均值」都算错。
#: ⇒ 数量走整数指标码，比率走 ``video_*_rate`` 指标码。
METRIC_NORMAL_COUNT = "video_normal_count"
#: ★ **OOTD 条数**（业务已确认 OOTD 每条算 2 条工作量，此处填**条数**）
METRIC_OOTD_COUNT = "video_ootd_count"
#: ★ **智能体条数**（业务已确认智能体每条算 2 条工作量，此处填**条数**）
METRIC_AGENT_COUNT = "video_agent_count"

#: ⚠️ **允许填报的指标** —— 白名单，防止写任意指标码污染算分集
ALLOWED_METRIC_CODES = (
    METRIC_COMPLETION_RATE,
    METRIC_RETENTION_RATE,
    METRIC_NORMAL_COUNT,
    METRIC_OOTD_COUNT,
    METRIC_AGENT_COUNT,
)

#: ★ **数量类指标**（2026-11-23）—— 值域是「非负整数」，不是 0~1。
#:
#: ⚠️ **为什么不能共用比率的值域**
#: 比率是 0~1（35% ⇒ 0.35），数量是 0~N（剪了 8 条 ⇒ 8）。
#: ⇒ 共用 0~1 的话，「8 条」会被判成越界直接拒掉。
COUNT_METRIC_CODES = frozenset(
    {
        METRIC_NORMAL_COUNT,
        METRIC_OOTD_COUNT,
        METRIC_AGENT_COUNT,
    }
)

#: 数量类指标的值域上限（防止手滑填个 999999 进来）
COUNT_MAX = 10000.0

#: 指标值的取值范围（比率类 0~1）
RATE_MIN = 0.0
RATE_MAX = 1.0

#: 来源
SOURCE_FRONTEND = "frontend"
SOURCE_FEISHU = "feishu"
SOURCE_IMPORT = "import"

#: ⚠️ **允许的证据 MIME 类型** —— 与复核工单同一套白名单
ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
        "image/bmp", "image/heic", "image/tiff",
        "video/mp4", "video/quicktime", "video/x-msvideo", "video/webm",
        "video/x-matroska", "video/avi", "video/mpeg",
        "application/pdf", "text/plain", "text/csv",
    }
)

#: 单个附件大小上限（字节）
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024


class PanManualMetricError(Exception):
    """人工填报业务异常。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PanManualMetricService:
    """人工填报与月度聚合服务。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ─────────────────────────────────────────────────────────────
    # 填报
    # ─────────────────────────────────────────────────────────────

    def submit(
        self,
        *,
        employee_id: str,
        metric_date: str,
        metric_code: str,
        metric_value: float,
        scope: str = "day",
        target_key: str = "",
        source: str = SOURCE_FRONTEND,
        created_by: str = "",
        remark: str = "",
        feishu_open_id: str = "",
        feishu_chat_id: str = "",
        feishu_message_id: str = "",
        import_batch_id: str = "",
    ) -> PanManualMetric:
        """提交一条人工指标。

        ⚠️ **upsert 而非 insert**
        同一个人同一天同一个指标反复提交是**正常操作**（改了再改），
        唯一约束保证不会重复累加 —— 后提交覆盖先提交。
        """
        if metric_code not in ALLOWED_METRIC_CODES:
            raise PanManualMetricError(
                f"不支持的指标：{metric_code}（仅允许 {', '.join(ALLOWED_METRIC_CODES)}）"
            )
        if scope not in ("day", "file"):
            raise PanManualMetricError(f"填报粒度非法：{scope}")
        if scope == "file" and not target_key:
            raise PanManualMetricError("逐条填报必须指定 target_key（网盘文件 id）")
        # ⚠️ **量纲不同，值域也不同**（2026-11-23）
        #   比率 0~1（35% ⇒ 0.35）；数量 0~10000 且**必须是整数**。
        #   ⇒ 共用 0~1 的话「剪了 8 条」会被判越界直接拒掉。
        if metric_code in COUNT_METRIC_CODES:
            if metric_value < 0 or metric_value > COUNT_MAX:
                raise PanManualMetricError(
                    f"数量超出范围：{metric_value}（应在 0~{COUNT_MAX:.0f}）"
                )
            if float(metric_value) != int(metric_value):
                raise PanManualMetricError(f"条数必须是整数：{metric_value}")
        elif not (RATE_MIN <= metric_value <= RATE_MAX):
            raise PanManualMetricError(
                f"指标值超出范围：{metric_value}（应在 {RATE_MIN}~{RATE_MAX}）"
            )
        try:
            day = date.fromisoformat(metric_date)
        except ValueError as exc:
            raise PanManualMetricError(f"日期格式非法：{metric_date}") from exc

        row = self.db.scalar(
            select(PanManualMetric).where(
                PanManualMetric.employee_id == employee_id,
                PanManualMetric.metric_date == day,
                PanManualMetric.metric_code == metric_code,
                PanManualMetric.target_key == target_key,
            )
        )
        if row is None:
            row = PanManualMetric(
                employee_id=employee_id,
                metric_date=day,
                metric_code=metric_code,
                target_key=target_key,
            )
            self.db.add(row)

        row.metric_value = float(metric_value)
        row.scope = scope
        row.source = source
        row.remark = remark[:255]
        row.created_by = created_by or employee_id
        row.updated_at = datetime.now()
        row.import_batch_id = import_batch_id or None
        if feishu_open_id:
            row.feishu_open_id = feishu_open_id
        if feishu_chat_id:
            row.feishu_chat_id = feishu_chat_id
        if feishu_message_id:
            row.feishu_message_id = feishu_message_id
        self.db.flush()
        return row

    def add_artifact(
        self,
        metric_id: str,
        *,
        name: str,
        media_type: str,
        content: bytes,
        source: str = SOURCE_FRONTEND,
        feishu_image_key: str = "",
        feishu_message_id: str = "",
        uploaded_by: str = "",
    ) -> PanManualMetricArtifact:
        """给指标挂证据附件。

        ⚠️ **类型白名单 + 大小上限**
        —— 白名单而不是前缀匹配（``application/`` 会放行 exe）。

        ⚠️ **``metric_id`` 可空** —— 空 = 待关联
        员工先发截图、还没填数值时，截图**先存着**，
        等他补了数值再挂上去。⇒ **截图不会因为缺数值而丢失**。
        """
        if metric_id and self.db.get(PanManualMetric, metric_id) is None:
            raise PanManualMetricError("指标不存在", 404)
        if not content:
            raise PanManualMetricError("附件内容为空")
        if len(content) > MAX_ARTIFACT_BYTES:
            raise PanManualMetricError(
                f"附件过大（{len(content) // 1024 // 1024}MB），上限 20MB"
            )
        if media_type.lower() not in ALLOWED_MEDIA_TYPES:
            raise PanManualMetricError(
                f"不支持的证据类型：{media_type}（仅支持图片/视频/文档）"
            )
        artifact = PanManualMetricArtifact(
            metric_id=metric_id or None,
            name=name[:255],
            media_type=media_type[:100],
            content=content,
            digest=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            source=source,
            feishu_image_key=feishu_image_key or None,
            feishu_message_id=feishu_message_id or None,
            uploaded_by=uploaded_by,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def attach_pending_evidence(
        self, employee_id: str, metric_id: str
    ) -> int:
        """把该员工的**待关联证据**挂到指定指标上。

        ⚠️ **为什么需要这一步**
        飞书端员工的自然顺序往往是「先发截图，再补数值」——
        截图先到时还没有指标可挂 ⇒ 存成待关联。
        ⇒ 他补了数值后，这里把待关联的截图自动挂上。

        Returns:
            挂上的附件数。
        """
        pending = list(
            self.db.scalars(
                select(PanManualMetricArtifact).where(
                    PanManualMetricArtifact.metric_id.is_(None),
                    PanManualMetricArtifact.uploaded_by == employee_id,
                )
            )
        )
        for art in pending:
            art.metric_id = metric_id
        self.db.flush()
        return len(pending)

    def list_pending_evidence(self, employee_id: str) -> list[PanManualMetricArtifact]:
        """查该员工的待关联证据。"""
        return list(
            self.db.scalars(
                select(PanManualMetricArtifact).where(
                    PanManualMetricArtifact.metric_id.is_(None),
                    PanManualMetricArtifact.uploaded_by == employee_id,
                )
            )
        )

    def list_metrics(
        self,
        *,
        employee_id: str = "",
        start: str = "",
        end: str = "",
        metric_code: str = "",
    ) -> list[PanManualMetric]:
        stmt = select(PanManualMetric)
        if employee_id:
            stmt = stmt.where(PanManualMetric.employee_id == employee_id)
        if metric_code:
            stmt = stmt.where(PanManualMetric.metric_code == metric_code)
        if start:
            stmt = stmt.where(PanManualMetric.metric_date >= date.fromisoformat(start))
        if end:
            stmt = stmt.where(PanManualMetric.metric_date <= date.fromisoformat(end))
        return list(
            self.db.scalars(stmt.order_by(PanManualMetric.metric_date.desc()))
        )

    def list_artifacts(self, metric_id: str) -> list[PanManualMetricArtifact]:
        return list(
            self.db.scalars(
                select(PanManualMetricArtifact).where(
                    PanManualMetricArtifact.metric_id == metric_id
                )
            )
        )

    # ─────────────────────────────────────────────────────────────
    # 月度聚合
    # ─────────────────────────────────────────────────────────────

    def aggregate_month(
        self, *, period: str = "", employee_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """月度聚合 → 写 ``MetricValue``。

        ⚠️ **写两条独立的 MetricValue**（2026-11-21 业务确认）
        ``video_count``      网盘条数（量纲：条）
        ``completion_rate``  完播率（量纲：比率）
        ``retention_rate``   留存率（量纲：比率）

        ⇒ 各自独立设目标值、独立进算分集，不合成综合分。

        ⚠️ **网盘条数优先取人工裁定值**
        有人工裁定（``adjusted_count``）⇒ 用裁定值；
        没有 ⇒ 用机器判定值（``total_count``）。
        ⚠️ 但 ``total_count`` **永远保留** ⇒ 可对照、可审计。

        ⚠️ **幂等**
        ``MetricValue`` 上有 ``(employee_id, metric_code, period)`` 唯一约束
        ⇒ 重跑是**覆盖**不是追加。

        Args:
            period: 考核周期（YYYY-MM）。留空 = 上个自然月。
            employee_ids: 只聚合这些人。留空 = 全部有数据的人。
        """
        period = period or self._last_month()
        start, end = self._month_range(period)

        # ── 网盘条数 ──
        daily_stmt = select(PanWorkloadDaily).where(
            PanWorkloadDaily.business_date >= start,
            PanWorkloadDaily.business_date <= end,
        )
        if employee_ids:
            daily_stmt = daily_stmt.where(PanWorkloadDaily.employee_id.in_(employee_ids))
        dailies = list(self.db.scalars(daily_stmt))

        video_rows: dict[str, dict[str, Any]] = {}
        for d in dailies:
            entry = video_rows.setdefault(
                d.employee_id,
                {"count": 0, "days": 0, "adjusted_days": 0},
            )
            # ⚠️ 优先用人工裁定值
            value = d.adjusted_count if d.adjusted_count is not None else d.total_count
            entry["count"] += value
            entry["days"] += 1
            if d.adjusted_count is not None:
                entry["adjusted_days"] += 1

        # ── 人工指标 ──
        manual_stmt = select(PanManualMetric).where(
            PanManualMetric.metric_date >= start,
            PanManualMetric.metric_date <= end,
        )
        if employee_ids:
            manual_stmt = manual_stmt.where(
                PanManualMetric.employee_id.in_(employee_ids)
            )
        manual_rows = list(self.db.scalars(manual_stmt))

        rate_rows: dict[tuple[str, str], list[float]] = {}
        for m in manual_rows:
            key = (m.employee_id, m.metric_code)
            rate_rows.setdefault(key, []).append(float(m.metric_value))

        # ── 写 MetricValue ──
        written: list[dict[str, Any]] = []
        targets = set(video_rows) | {emp for emp, _ in rate_rows}
        for emp_id in sorted(targets):
            v = video_rows.get(emp_id)
            if v and v["count"] > 0:
                written.append(
                    self._upsert_metric_value(
                        employee_id=emp_id,
                        metric_code=METRIC_VIDEO_COUNT,
                        period=period,
                        value=float(v["count"]),
                        unit="条",
                        source_ref=f"pan_workload:{period}",
                        extra={
                            "days": v["days"],
                            "adjusted_days": v["adjusted_days"],
                        },
                    )
                )
            for metric_code in (METRIC_COMPLETION_RATE, METRIC_RETENTION_RATE):
                values = rate_rows.get((emp_id, metric_code))
                if not values:
                    continue
                # ⚠️ 逐条填报时取**平均** —— 每个成片一个完播率
                avg = sum(values) / len(values)
                written.append(
                    self._upsert_metric_value(
                        employee_id=emp_id,
                        metric_code=metric_code,
                        period=period,
                        value=avg,
                        unit="%",
                        source_ref=f"pan_manual:{period}",
                        extra={"sample_count": len(values)},
                    )
                )

        self.db.flush()
        return {
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "written": len(written),
            "employees": len(targets),
            "detail": written,
        }

    def _upsert_metric_value(
        self,
        *,
        employee_id: str,
        metric_code: str,
        period: str,
        value: float,
        unit: str,
        source_ref: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """写一条 ``MetricValue`` —— **走唯一入口** ``add_metric_value``。

        ⚠️ **为什么不能自己 upsert**
        框架里 ``MetricValue.period`` 的**唯一权威格式是紧凑 ``YYYYMM``**
        （见 ``evidence_service.add_metric_value`` 的 BUG-R6-26 修复：
        历史上 ``YYYY-MM`` 与 ``YYYYMM`` 混存，导致唯一约束
        ``(employee_id, metric_code, period)`` 失效 ⇒ 同一人同一指标出现两行）。

        ⇒ 本模块原先自己建行并写 ``period="2026-11"`` ⇒ **分叉了**。
        ⇒ 现在统一交给 ``add_metric_value`` 归一化，
          本方法的 ``period`` 入参用**业务可读格式** ``YYYY-MM``，
          落库时会被转成 ``YYYYMM``。

        ⚠️ **来源必须可追溯**
        ``source_ref`` 指向来源（``pan_workload:2026-11`` / ``pan_manual:2026-11``），
        主管在核对时能顺着它找到原始数据。
        """
        from .evidence_service import add_metric_value

        row = add_metric_value(
            self.db,
            employee_id=employee_id,
            metric_code=metric_code,
            period=period,
            value=float(value),
            unit=unit,
            source="baidu_pan_workload",
            source_ref=source_ref,
            as_of=datetime.now(),
        )
        return {
            "employee_id": employee_id,
            "metric_code": metric_code,
            #: ⚠️ **落库格式**（紧凑 ``YYYYMM``）—— 权威值
            "period": row.period,
            #: 业务周期（``YYYY-MM``）—— 只给人看
            "business_period": period,
            "value": float(value),
            "unit": unit,
            **(extra or {}),
        }

    @staticmethod
    def _last_month() -> str:
        """上个自然月（YYYY-MM）。

        ⚠️ 用上个月而不是当月 —— 当月还没过完，
        数据必然不完整，拿去核对会产生假异常。
        """
        today = date.today()
        first = today.replace(day=1)
        last = first - timedelta(days=1)
        return f"{last.year:04d}-{last.month:02d}"

    @staticmethod
    def _month_range(period: str) -> tuple[date, date]:
        year, month = (int(x) for x in period.split("-")[:2])
        start = date(year, month, 1)
        if month == 12:
            end = date(year, 12, 31)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)
        return start, end
