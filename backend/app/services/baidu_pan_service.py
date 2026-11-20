"""百度网盘绩效校验服务（BP-1）。

职责
----
1. **OAuth 闭环**：生成授权链接 → 收授权码 → 换 token → 存 refresh_token
   → 到期自动刷新（**每次刷新都换新的 refresh_token**）。
2. **取数**：递归扫网盘目录 → 按天统计文件数 → 落 ``BaiduPanCount`` 快照。
3. **校验**：把计数写成 ``MetricValue`` + ``Evidence``，
   供主管核对员工申报值。

⚠️ **核心原则：不自动改分**
网盘计数**只作证据**。与申报值不一致时**提示主管核实**，
绝不自动扣分 —— 与 ``semantic_qc`` / ``distribution_qc`` 口径一致：
**数据源只提供依据，判定权在人**。

⚠️ **目录限制**
递归扫描必须落在 ``/apps/{appname}`` 下（百度 2026-06-03 后的硬限制）。
所以 ``watch_dir`` 只能配**授权时勾选过的**子目录。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..domain.baidu_pan_models import (
    BaiduPanCount,
    BaiduPanSyncBatch,
    BaiduPanToken,
)
from .baidu_pan_client import (
    BaiduPanAuthError,
    BaiduPanClient,
    BaiduPanError,
    TokenSet,
    count_by_day,
)

logger = logging.getLogger(__name__)

#: 数据源标识 —— 与 ``DataSourcePolicy.source_code`` 对齐。
SOURCE_CODE = "baidu_pan"

#: token 提前 5 分钟视为过期（避免请求中途失效）。
_EXPIRY_SKEW_SECONDS = 300


class BaiduPanService:
    """百度网盘校验服务。"""

    def __init__(
        self,
        db: Session,
        client: BaiduPanClient | None = None,
    ) -> None:
        self.db = db
        self._client = client

    # ─────────────────────────────────────────────────────────────
    # 客户端
    # ─────────────────────────────────────────────────────────────

    @property
    def client(self) -> BaiduPanClient:
        if self._client is None:
            self._client = BaiduPanClient(
                app_key=settings.baidu_pan_app_key,
                app_secret=settings.baidu_pan_app_secret,
                redirect_uri=settings.baidu_pan_redirect_uri or "oob",
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ─────────────────────────────────────────────────────────────
    # OAuth
    # ─────────────────────────────────────────────────────────────

    def build_authorize_url(self, *, state: str = "") -> str:
        """生成授权链接 —— 让管理员去勾选网盘目录。"""
        return self.client.build_authorize_url(state=state)

    def authorize(self, code: str) -> dict[str, Any]:
        """用授权码换 token 并落库。

        ⚠️ **code 只能用一次** —— 重复调用会失败，
        需要重新走授权页。
        """
        tokens = self.client.exchange_code(code)
        self._save_tokens(tokens)
        # ⚠️ 授权后立刻验一次目录可读性 —— 与其等到定时任务才发现
        #    目录没勾选，不如当场告诉用户。
        probe = self.probe_access()
        return {
            "authorized": True,
            "expires_in": tokens.expires_in,
            "scope": tokens.scope,
            "probe": probe,
        }

    def _save_tokens(self, tokens: TokenSet) -> None:
        row = self.db.get(BaiduPanToken, "default")
        if row is None:
            row = BaiduPanToken(id="default")
            self.db.add(row)
        row.app_key = settings.baidu_pan_app_key
        row.app_secret = settings.baidu_pan_app_secret
        row.redirect_uri = settings.baidu_pan_redirect_uri or "oob"
        row.access_token = tokens.access_token
        # ⚠️⚠️ **百度刷新后可能返回新的 refresh_token，必须覆盖旧的**。
        #    刷新响应也可能**不返回**新的 —— 那就沿用旧的。
        if tokens.refresh_token:
            row.refresh_token = tokens.refresh_token
        row.scope = tokens.scope or row.scope
        row.expires_at = int(tokens.expires_at)
        row.authorized_at = datetime.now()
        row.updated_at = datetime.now()
        self.db.flush()

    def is_authorized(self) -> bool:
        row = self.db.get(BaiduPanToken, "default")
        return bool(row and row.access_token and row.refresh_token)

    def ensure_access_token(self) -> str:
        """拿到一个可用的 access_token —— 必要时自动刷新。

        ⚠️ **刷新时必须把新的 refresh_token 存回去** ——
        百度的 refresh_token 只能用一次，不存就断了。
        """
        row = self.db.get(BaiduPanToken, "default")
        if row is None or not row.refresh_token:
            raise BaiduPanAuthError("尚未授权百度网盘，请先完成授权流程")

        now = time.time()
        if row.access_token and row.expires_at > now + _EXPIRY_SKEW_SECONDS:
            return row.access_token

        if not settings.baidu_pan_auto_refresh_token:
            raise BaiduPanAuthError("access_token 已过期，自动刷新未开启，请重新授权")

        logger.info("百度网盘 access_token 已过期，开始刷新")
        try:
            tokens = self.client.refresh(row.refresh_token)
        except BaiduPanAuthError:
            # ⚠️ 刷新失败通常意味着 refresh_token 已失效 ——
            #    明确提示要重新授权，而不是继续用过期 token 撞接口。
            raise
        # ⚠️ 这里同时更新 access_token **和** refresh_token。
        self._save_tokens(tokens)
        self.db.commit()
        return tokens.access_token

    # ─────────────────────────────────────────────────────────────
    # 目录
    # ─────────────────────────────────────────────────────────────

    def watch_path(self) -> str:
        """要扫描的网盘绝对路径。

        ⚠️⚠️ **实测结论（2026-11-20，授权后验证）**
        ----------------------------------------------------------
        百度官方文档说"应用只能访问 ``/apps/{appname}``"，
        **但实测**：

            - ``file?method=list`` **能读用户网盘的任意授权目录**
              （如 ``/成品-小瑶``）—— 这是业务目录，不在 ``/apps`` 下
            - ``multimedia?method=listall`` 反而**读不了**这些路径
              （报 ``20020``）

        ⇒ 所以 ``watch_dir`` 直接配**网盘里的业务目录**，
        **不要**加 ``/apps/{应用名}`` 前缀。

        例：``BAIDU_PAN_WATCH_DIR=/成品-小瑶``
        """
        sub = settings.baidu_pan_watch_dir.strip()
        if not sub:
            # 没配就退回应用根目录（授权后平台生成的那个）。
            base = settings.baidu_pan_app_name.strip() or "绩效校验"
            return f"/apps/{base}"
        return sub if sub.startswith("/") else f"/{sub}"

    def probe_access(self) -> dict[str, Any]:
        """探一下目录能不能读 —— 授权后立刻验，避免定时任务才发现问题。"""
        path = self.watch_path()
        try:
            token = self.ensure_access_token()
            files = self.client.list_files_recursive(token, path, limit=100)
        except BaiduPanError as exc:
            return {
                "ok": False,
                "path": path,
                "error": exc.message,
                "errno": exc.errno,
            }
        return {
            "ok": True,
            "path": path,
            "scanned": len(files),
            "hint": (
                "目录可读"
                if files
                else "目录可读但为空 —— 确认授权时勾选了正确的目录"
            ),
        }

    # ─────────────────────────────────────────────────────────────
    # 取数
    # ─────────────────────────────────────────────────────────────

    def run_sync(
        self,
        *,
        window_start: str = "",
        window_end: str = "",
        trigger_type: str = "scheduled",
    ) -> BaiduPanSyncBatch:
        """跑一轮取数。

        Args:
            window_start: 统计窗口起（YYYY-MM-DD）。留空 = 上个自然月。
            window_end: 统计窗口止（YYYY-MM-DD，含）。留空 = 上个自然月最后一天。
        """
        batch = BaiduPanSyncBatch(
            trigger_type=trigger_type, status="running", detail={}
        )
        self.db.add(batch)
        self.db.flush()

        try:
            start, end = self._resolve_window(window_start, window_end)
            token = self.ensure_access_token()
            path = self.watch_path()
            files = self.client.list_files_recursive(token, path)

            counts = count_by_day(
                files,
                extensions=settings.baidu_pan_count_extensions,
                exclude_keywords=settings.baidu_pan_exclude_keywords,
            )
            window_counts = {
                day: value
                for day, value in counts.items()
                if start <= day <= end
            }
            total = sum(window_counts.values())

            snapshot = self._upsert_snapshot(
                start=start,
                end=end,
                total=total,
                daily_counts=window_counts,
                scanned=len(files),
                excluded=self._excluded_count(files),
                path=path,
                digest=self._digest(files),
            )

            batch.scanned_files = len(files)
            batch.counted_files = total
            batch.excluded_files = snapshot.excluded_files
            batch.status = "succeeded"
            batch.detail = {
                "window_start": start,
                "window_end": end,
                "path": path,
                "daily_counts": window_counts,
                "total": total,
                "snapshot_id": snapshot.id,
            }
            batch.finished_at = datetime.now()
            self.db.flush()
            return batch
        except Exception as exc:
            logger.exception("百度网盘取数失败")
            batch.status = "failed"
            batch.error_message = str(exc)[:1000]
            batch.finished_at = datetime.now()
            self.db.flush()
            return batch

    @staticmethod
    def _resolve_window(start: str, end: str) -> tuple[str, str]:
        """确定统计窗口 —— 留空时用**上个自然月**。

        ⚠️ 用上个月而不是当月 —— 当月还没过完，
        数据必然不完整，拿去核对会产生假异常。
        """
        if start and end:
            return start, end
        today = date.today()
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        first_last = last_month_end.replace(day=1)
        return (
            start or first_last.isoformat(),
            end or last_month_end.isoformat(),
        )

    def _upsert_snapshot(
        self,
        *,
        start: str,
        end: str,
        total: int,
        daily_counts: dict[str, int],
        scanned: int,
        excluded: int,
        path: str,
        digest: str,
    ) -> BaiduPanCount:
        """写计数快照 —— 同一窗口**覆盖**而非追加（幂等）。"""
        row = self.db.scalar(
            select(BaiduPanCount).where(
                BaiduPanCount.source_code == SOURCE_CODE,
                BaiduPanCount.window_start == start,
                BaiduPanCount.window_end == end,
            )
        )
        if row is None:
            row = BaiduPanCount(
                source_code=SOURCE_CODE, window_start=start, window_end=end
            )
            self.db.add(row)
        row.total = total
        row.daily_counts = daily_counts
        row.scanned_files = scanned
        row.excluded_files = excluded
        row.scanned_path = path
        row.file_digest = digest
        row.as_of = datetime.now()
        self.db.flush()
        return row

    def _excluded_count(self, files: list[Any]) -> int:
        """被计数规则排除掉的文件数。"""
        allowed = {
            x.strip().lower()
            for x in settings.baidu_pan_count_extensions.split(",")
            if x.strip()
        }
        excluded_keywords = [
            x.strip().lower()
            for x in settings.baidu_pan_exclude_keywords.split(",")
            if x.strip()
        ]
        count = 0
        for item in files:
            if item.is_dir:
                continue
            name = item.name.lower()
            if allowed and item.ext not in allowed:
                count += 1
                continue
            if any(keyword in name for keyword in excluded_keywords):
                count += 1
        return count

    @staticmethod
    def _digest(files: list[Any], limit: int = 200) -> str:
        """文件摘要 —— 便于事后追溯「这批数是哪些文件算出来的」。"""
        names = sorted(item.name for item in files if not item.is_dir)
        return " | ".join(names[:limit])

    # ─────────────────────────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────────────────────────

    def latest_snapshot(self) -> BaiduPanCount | None:
        return self.db.scalar(
            select(BaiduPanCount)
            .where(BaiduPanCount.source_code == SOURCE_CODE)
            .order_by(BaiduPanCount.window_end.desc())
            .limit(1)
        )

    def list_snapshots(self, limit: int = 20) -> list[BaiduPanCount]:
        return list(
            self.db.scalars(
                select(BaiduPanCount)
                .where(BaiduPanCount.source_code == SOURCE_CODE)
                .order_by(BaiduPanCount.window_end.desc())
                .limit(limit)
            ).all()
        )


def compare_with_report(
    reported: float | None, actual: float | None
) -> dict[str, Any]:
    """把「申报值」与「网盘实测值」放一起比对。

    ⚠️ **只做提示，不做判定** —— 差异可能是口径问题
    （比如返工片、跨月交付、命名规则），必须让人来判断。

    Args:
        reported: 员工申报值（``None`` = 没填）。
        actual: 网盘实测值（``None`` = 没取到数）。

    Returns:
        ``{"status": ..., "reported": ..., "actual": ..., "diff": ..., "message": ...}``
    """
    if reported is None and actual is None:
        return {
            "status": "no_data",
            "reported": None,
            "actual": None,
            "diff": None,
            "message": "既无申报值也无实测值",
        }
    if actual is None:
        return {
            "status": "no_actual",
            "reported": reported,
            "actual": None,
            "diff": None,
            "message": "网盘未取到数据，无法核对",
        }
    if reported is None:
        return {
            "status": "no_reported",
            "reported": None,
            "actual": actual,
            "diff": None,
            "message": "员工未申报，无法核对",
        }
    diff = float(reported) - float(actual)
    if abs(diff) < 1e-9:
        return {
            "status": "match",
            "reported": reported,
            "actual": actual,
            "diff": 0.0,
            "message": "申报值与网盘实测一致",
        }
    return {
        "status": "mismatch",
        "reported": reported,
        "actual": actual,
        "diff": diff,
        "message": (
            f"申报 {reported} 条，网盘实测 {actual} 条，"
            f"相差 {diff:+g} 条 —— 请核实后人工评定"
        ),
    }
