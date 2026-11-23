"""网盘剪辑工作量统计服务（BP-2 / P1）。

职责
----
把「网盘上有哪些文件」变成「数据库里能查到某人某天做了几条」：

    绑定表驱动扫描 ──> pan_workload_file（单文件证据）
                              │
                              └──> pan_workload_daily（按人按天聚合）

⚠️ **核心原则（继承框架第一性原则）**
1. **判定权在人** —— 异常只**标记**，绝不自动扣分或改分。
2. **不丢弃任何文件** —— 未归属 / 解析失败 / 被排除的文件
   全部落库并带原因标记，面板上高亮「待人工确认」，避免**静默少计**。
3. **幂等** —— 按 ``fs_id`` upsert；日聚合**重算**而非累加。

⚠️ **为什么归属靠目录路径而不是上传者**
实测（2026-11-21）：公司共享网盘下 ``oper_id`` **232/232 条全部等于公司账号 uk**，
共享目录**不暴露真实上传者** ⇒ ``oper_id`` 无区分度。
实测网盘根下已有按人隔离的顶层目录（``/成品-小瑶``、``/成品-小雨``），
⇒ 归属 = **路径前缀匹配**，映射表在 ``pan_account_binding``。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.pan_workload_models import (
    PanAccountBinding,
    PanWorkloadConfig,
    PanWorkloadDaily,
    PanWorkloadFile,
)
from .baidu_pan_client import PanFile
from .baidu_pan_service import BaiduPanService
from .pan_workload_rules import (
    DEFAULT_ALLOWED_EXTENSIONS,
    DEFAULT_EXCLUDE_DIR_SEGMENTS,
    DEFAULT_EXCLUDE_KEYWORDS,
    FileVerdict,
    judge_file,
    resolve_file_business_date,
    resolve_owner,
)

logger = logging.getLogger(__name__)


def _split_csv(value: str | None) -> tuple[str, ...]:
    """把逗号分隔的配置串拆成元组（去空白、去空项）。"""
    if not value:
        return ()
    return tuple(x.strip() for x in value.split(",") if x.strip())


class PanWorkloadService:
    """网盘剪辑工作量统计服务。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._pan: BaiduPanService | None = None

    # ─────────────────────────────────────────────────────────────
    # 依赖
    # ─────────────────────────────────────────────────────────────

    @property
    def pan(self) -> BaiduPanService:
        """BP-1 的网盘服务 —— 复用它的 token 管理与递归下钻。"""
        if self._pan is None:
            self._pan = BaiduPanService(self.db)
        return self._pan

    def close(self) -> None:
        if self._pan is not None:
            self._pan.close()
            self._pan = None

    # ─────────────────────────────────────────────────────────────
    # 配置
    # ─────────────────────────────────────────────────────────────

    def get_config(self) -> PanWorkloadConfig:
        """取规则配置 —— 没有就建默认行。"""
        row = self.db.get(PanWorkloadConfig, "default")
        if row is None:
            row = PanWorkloadConfig(id="default")
            self.db.add(row)
            self.db.flush()
        return row

    def _rule_kwargs(self) -> dict[str, Any]:
        """从配置行解析出规则引擎需要的参数。"""
        cfg = self.get_config()
        return {
            "rules": cfg.marker_rules or None,
            "allowed_extensions": _split_csv(cfg.allowed_extensions)
            or DEFAULT_ALLOWED_EXTENSIONS,
            "exclude_keywords": _split_csv(cfg.exclude_keywords)
            or DEFAULT_EXCLUDE_KEYWORDS,
            "exclude_dir_segments": _split_csv(cfg.exclude_dir_segments)
            or DEFAULT_EXCLUDE_DIR_SEGMENTS,
        }

    # ─────────────────────────────────────────────────────────────
    # 绑定表
    # ─────────────────────────────────────────────────────────────

    def list_bindings(self, *, active_only: bool = True) -> list[PanAccountBinding]:
        """读绑定表 —— **采集范围完全由它决定**。"""
        stmt = select(PanAccountBinding)
        if active_only:
            stmt = stmt.where(PanAccountBinding.active.is_(True))
        return list(self.db.scalars(stmt.order_by(PanAccountBinding.folder_path)))

    def binding_dicts(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        """绑定表 → 规则引擎能吃的 dict 列表。"""
        return [
            {
                "employee_id": b.employee_id,
                "folder_path": b.folder_path,
                "folder_name": b.folder_name,
                "active": b.active,
            }
            for b in self.list_bindings(active_only=active_only)
        ]

    # ─────────────────────────────────────────────────────────────
    # ① 采集
    # ─────────────────────────────────────────────────────────────

    def collect(self, *, trigger_type: str = "scheduled") -> dict[str, Any]:
        """跑一轮完整采集。

        ⚠️ **只扫绑定表里的目录** ⇒ 未绑定目录（如 ``/口播成品``、``/鹿13``）
        **根本不会被访问** —— 既符合业务口径，也大幅降低频控压力。
        """
        cfg = self.get_config()
        excluded_paths = set(_split_csv(cfg.exclude_paths))
        bindings = self.binding_dicts()
        if not bindings:
            return {
                "status": "skipped",
                "reason": "no_bindings",
                "scanned": 0,
                "counted": 0,
                "excluded": 0,
            }

        token = self.pan.ensure_access_token()
        rule_kwargs = self._rule_kwargs()

        scanned = 0
        excluded = 0
        counted = 0
        per_binding: list[dict[str, Any]] = []

        for binding in bindings:
            folder = binding["folder_path"]
            if folder in excluded_paths:
                logger.info("跳过黑名单目录：%s", folder)
                continue
            files = self.pan.client.list_files_recursive(token, folder)
            scanned += len(files)
            b_counted, b_excluded = self._persist_files(files, binding, rule_kwargs)
            counted += b_counted
            excluded += b_excluded
            per_binding.append(
                {
                    "employee_id": binding["employee_id"],
                    "folder_path": folder,
                    "scanned": len(files),
                    "counted": b_counted,
                    "excluded": b_excluded,
                }
            )

        # ⚠️⚠️ **必须先 flush**
        # ``aggregate_daily`` 用 ``select()`` 从库里查文件行，
        # 而刚落库的 ``PanWorkloadFile`` 还在会话的 pending 区 —— 不 flush
        # 查询就**看不到它们** ⇒ 聚合结果是空的（``daily_rows=0``）。
        # ⇒ 这里 flush 一次，让待写行进入当前事务的查询视野。
        self.db.flush()

        # ── ③ 聚合（重算）──
        daily_rows = self.aggregate_daily()
        # ── ④ 异常检测 ──
        anomalies = self.detect_anomalies()

        self.db.flush()
        return {
            "status": "succeeded",
            "trigger_type": trigger_type,
            "scanned": scanned,
            "counted": counted,
            "excluded": excluded,
            "bindings": per_binding,
            "daily_rows": len(daily_rows),
            "anomalies": len(anomalies),
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _persist_files(
        self,
        files: list[PanFile],
        binding: dict[str, Any],
        rule_kwargs: dict[str, Any],
    ) -> tuple[int, int]:
        """把一批文件落库 —— **幂等 upsert**。

        ⚠️ **按 ``fs_id`` upsert**
        同一个文件会被反复扫到（6 小时一次）⇒ 必须 upsert 而非 insert。
        ``fs_id`` 是网盘文件唯一 ID，**永不变化**（改名也不变）⇒ 天然幂等锚点。

        ⚠️ **重复上传识别**
        同一 ``(employee_id, business_date, content_digest)`` 下
        已有记录 ⇒ 判为重复（``is_duplicate=True``），只算一次。

        Returns:
            ``(计入数, 排除数)``
        """
        counted = 0
        excluded = 0
        # 本批内已出现过的 digest —— 同一批里的重复也要能识别
        seen_digests: dict[tuple[str, str], str] = {}

        for f in files:
            if f.is_dir:
                continue
            verdict = judge_file(f, **rule_kwargs)
            emp_id, bind_type, bind_note = resolve_owner(f.path, [binding])
            # ⚠️ 目录绑定就是归属来源 ⇒ 命中即 path
            if bind_type == "unbound":
                bind_type = "path"

            # ⚠️⚠️ **每个文件都必须有业务日期** —— 否则**静默少计**
            #
            # ``judge_file`` 对扩展名/关键词/草稿目录是**提前 return** 的，
            # 此时 ``business_date`` 是空的。若直接落空，
            # 聚合时会因为「没有日期」被跳过 ⇒ **那天在日汇总里整行消失**
            # ⇒ 面板上看不到「这天有文件但全被排除了」⇒ 静默少计。
            #
            # ⚠️⚠️ **计入的文件更严重**（2026-11-21 回归点）
            # ``/口播成品/灯笼裤.mp4`` 与 ``/口播成品/6月18日.mp4`` 直接挂在根目录，
            # **目录名里没有日期** ⇒ 日期为空
            # ⇒ 文件**计入判定成立**（``counted=True``）
            # 但 ``aggregate_daily`` 会跳过没有日期的行
            # ⇒ 「1346 条」在面板上只显示「1345 条」
            # ⇒ **差额永远查不出来**（机器判定值与聚合值对不上，无提示）。
            #
            # ⇒ ``resolve_file_business_date`` 保证**永不返回空**
            #    （目录日期 → 文件名日期 → 上传时间 → 显式占位）。
            if not verdict.business_date:
                day, extra = resolve_file_business_date(
                    f, default_year=rule_kwargs.get("default_year", 0)
                )
                verdict.business_date = day
                verdict.anomaly_flags = [
                    *verdict.anomaly_flags,
                    *[x for x in extra if x not in verdict.anomaly_flags],
                ]

            is_dup = False
            dup_kind = "none"
            dup_of = None
            if verdict.counted and verdict.business_date:
                key = (emp_id or "", verdict.business_date, verdict.content_digest)
                prior = seen_digests.get(key)
                if prior is None:
                    prior = self.db.scalar(
                        select(PanWorkloadFile.id).where(
                            PanWorkloadFile.employee_id == (emp_id or None),
                            PanWorkloadFile.business_date == verdict.business_date,
                            PanWorkloadFile.content_digest == verdict.content_digest,
                            PanWorkloadFile.fs_id != f.fs_id,
                        )
                    )
                if prior is not None:
                    is_dup = True
                    dup_kind = "md5" if verdict.content_digest.startswith("md5:") else "name_size"
                    dup_of = prior
                else:
                    seen_digests[key] = str(f.fs_id)

            if verdict.counted and not is_dup:
                counted += 1
            else:
                excluded += 1

            self._upsert_file(f, verdict, emp_id, bind_type, bind_note,
                              is_dup, dup_kind, dup_of)

        return counted, excluded

    def _upsert_file(
        self,
        f: PanFile,
        verdict: FileVerdict,
        emp_id: str | None,
        bind_type: str,
        bind_note: str | None,
        is_dup: bool,
        dup_kind: str,
        dup_of: str | None,
    ) -> None:
        """按 ``fs_id`` upsert 单个文件。"""
        row = self.db.scalar(
            select(PanWorkloadFile).where(PanWorkloadFile.fs_id == f.fs_id)
        )
        if row is None:
            row = PanWorkloadFile(fs_id=f.fs_id)
            self.db.add(row)
            row.created_at = datetime.now()

        row.pan_path = f.path
        row.file_name = f.name
        row.extension = f.ext
        row.size_bytes = f.size
        row.content_digest = verdict.content_digest
        row.md5 = getattr(f, "md5", "") or ""
        row.pan_ctime = f.ctime
        row.employee_id = emp_id
        row.bind_type = bind_type
        row.bind_note = bind_note or ""
        row.business_date = verdict.business_date or None
        # ⚠️ 重复文件**不计入**，但仍然落库留痕
        row.counted = bool(verdict.counted) and not is_dup
        row.count_weight = verdict.count_weight if row.counted else 0
        row.markers = list(verdict.markers.names)
        row.exclude_reason = (
            "" if row.counted
            else (f"duplicate:{dup_kind}" if is_dup else verdict.exclude_reason)
        )
        row.anomaly_flags = list(verdict.anomaly_flags)
        row.is_duplicate = is_dup
        row.duplicate_kind = dup_kind
        row.duplicate_of_id = dup_of
        row.last_seen_at = datetime.now()

    # ─────────────────────────────────────────────────────────────
    # ③ 聚合（重算而非累加）
    # ─────────────────────────────────────────────────────────────

    def aggregate_daily(self) -> list[PanWorkloadDaily]:
        """把 ``pan_workload_file`` 重算成 ``pan_workload_daily``。

        ⚠️⚠️ **重算而非累加** —— 这是本方法存在的全部理由
        - 重复跑同步不会把条数加两遍
        - 规则改了重跑一次就全部生效（不需要数据迁移脚本）

        ⚠️ **人工调整不被覆盖**
        ``adjusted_count`` 存在时保留它，只更新 ``total_count``。
        ⇒ 机器判定与人工裁定**并存**，不互相吞噬。
        """
        rows = list(
            self.db.scalars(
                select(PanWorkloadFile).where(PanWorkloadFile.employee_id.is_not(None))
            )
        )
        groups: dict[tuple[str, str], list[PanWorkloadFile]] = defaultdict(list)
        for r in rows:
            if not r.business_date:
                continue
            groups[(r.employee_id, r.business_date)].append(r)

        out: list[PanWorkloadDaily] = []
        for (emp_id, day_str), items in groups.items():
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue
            counted = [x for x in items if x.counted]
            excluded = [x for x in items if not x.counted]
            total = sum(x.count_weight for x in counted)

            markers: dict[str, int] = defaultdict(int)
            for x in counted:
                for name in (x.markers or []):
                    markers[name] += 1
            anomaly_count: dict[str, int] = defaultdict(int)
            for x in items:
                for flag in (x.anomaly_flags or []):
                    anomaly_count[flag] += 1

            row = self.db.scalar(
                select(PanWorkloadDaily).where(
                    PanWorkloadDaily.employee_id == emp_id,
                    PanWorkloadDaily.business_date == day,
                )
            )
            if row is None:
                row = PanWorkloadDaily(employee_id=emp_id, business_date=day)
                self.db.add(row)

            row.total_count = total
            row.file_count = len(counted)
            row.scanned_count = len(items)
            row.excluded_count = len(excluded)
            row.marker_breakdown = dict(markers)
            row.anomaly_count = dict(anomaly_count)
            row.last_synced_at = datetime.now()
            # ⚠️ 人工调整过的行 —— 状态保持 confirmed，不被自动判定覆盖
            if row.adjusted_count is None:
                row.status = "auto"
            out.append(row)

        self.db.flush()
        return out

    # ─────────────────────────────────────────────────────────────
    # ④ 异常检测（只标记，不改分）
    # ─────────────────────────────────────────────────────────────

    def detect_anomalies(self) -> list[dict[str, Any]]:
        """检测突增 / 突减 —— **只标记，绝不自动扣分**。

        ⚠️ **判定权在人**
        异常只作为「提示主管核实」的依据，与 ``semantic_qc`` /
        ``distribution_qc`` 口径一致。

        ⚠️ **基线不足不报警**
        历史天数 < ``baseline_days`` 时不判定 —— 否则月初必然误报。
        """
        cfg = self.get_config()
        baseline_days = max(3, int(cfg.baseline_days or 20))
        surge = float(cfg.surge_multiplier or 2.0)
        drop = float(cfg.drop_multiplier or 0.4)

        rows = list(
            self.db.scalars(
                select(PanWorkloadDaily).order_by(
                    PanWorkloadDaily.employee_id, PanWorkloadDaily.business_date
                )
            )
        )
        by_emp: dict[str, list[PanWorkloadDaily]] = defaultdict(list)
        for r in rows:
            by_emp[r.employee_id].append(r)

        found: list[dict[str, Any]] = []
        for emp_id, items in by_emp.items():
            for idx, row in enumerate(items):
                history = items[max(0, idx - baseline_days):idx]
                if len(history) < baseline_days:
                    continue
                avg = sum(h.total_count for h in history) / len(history)
                if avg <= 0:
                    continue
                current = row.total_count
                if current >= avg * surge:
                    found.append(
                        {
                            "employee_id": emp_id,
                            "business_date": row.business_date.isoformat(),
                            "kind": "surge",
                            "current": current,
                            "baseline_avg": round(avg, 2),
                            "message": f"突增：{current} 条（近{baseline_days}天均 {avg:.1f}）",
                        }
                    )
                elif current <= avg * drop:
                    found.append(
                        {
                            "employee_id": emp_id,
                            "business_date": row.business_date.isoformat(),
                            "kind": "drop",
                            "current": current,
                            "baseline_avg": round(avg, 2),
                            "message": f"突减：{current} 条（近{baseline_days}天均 {avg:.1f}）",
                        }
                    )
        return found

    # ─────────────────────────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────────────────────────

    def daily_rows(
        self, *, employee_id: str = "", start: str = "", end: str = ""
    ) -> list[PanWorkloadDaily]:
        """查日汇总（可按人、按区间过滤）。"""
        stmt = select(PanWorkloadDaily)
        if employee_id:
            stmt = stmt.where(PanWorkloadDaily.employee_id == employee_id)
        if start:
            stmt = stmt.where(PanWorkloadDaily.business_date >= date.fromisoformat(start))
        if end:
            stmt = stmt.where(PanWorkloadDaily.business_date <= date.fromisoformat(end))
        return list(self.db.scalars(stmt.order_by(PanWorkloadDaily.business_date)))

    def files_of_day(
        self, employee_id: str, day: str, *, counted_only: bool = True
    ) -> list[PanWorkloadFile]:
        """查某天某人的文件明细 —— **审计根**，供主管复核。"""
        stmt = select(PanWorkloadFile).where(
            PanWorkloadFile.employee_id == employee_id,
            PanWorkloadFile.business_date == day,
        )
        if counted_only:
            stmt = stmt.where(PanWorkloadFile.counted.is_(True))
        return list(self.db.scalars(stmt.order_by(PanWorkloadFile.pan_path)))

    def totals_by_employee(self) -> list[dict[str, Any]]:
        """按人汇总总条数 —— 面板用。"""
        stmt = (
            select(
                PanWorkloadDaily.employee_id,
                func.sum(PanWorkloadDaily.total_count),
                func.count(PanWorkloadDaily.id),
            )
            .group_by(PanWorkloadDaily.employee_id)
        )
        return [
            {
                "employee_id": emp_id,
                "total_count": int(total or 0),
                "days": int(days or 0),
            }
            for emp_id, total, days in self.db.execute(stmt)
        ]
