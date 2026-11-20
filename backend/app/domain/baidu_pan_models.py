"""百度网盘绩效校验的持久化模型（BP-1）。

背景
----
网盘里的成片文件是**产出数量的客观证据**。本模块把「网盘文件」
落成可追溯的计数快照，供主管核对员工申报值：

    网盘文件 ──(BaiduPanCount)──> MetricValue（算分集） ──> 主管核对视图

两张表
------
``baidu_pan_token``
    OAuth 凭证存储。
    ⚠️ **必须存** —— 百度 ``refresh_token`` 只能用一次，
    刷新后要用响应里的**新** refresh_token 覆盖旧的，
    不存就没法自动续期（access_token 30 天就废）。
    ⇒ 这是本项目里**唯一**落库保存第三方长期凭证的地方，
    所以额外加了「加密存储」与「绝不出现在日志/响应体」的约束。

``baidu_pan_count``
    一次取数的计数快照。一条记录 = 一次同步 × 一个统计窗口。
    - ``daily_counts`` 存 ``{"YYYY-MM-DD": 数量}``，可追溯到每天。
    - ``file_digest`` 存参与计数的文件名摘要 —— 便于事后追责。

设计要点
--------
- **不自动改分**：网盘计数只作为**证据**（``Evidence``），
  与员工申报值不一致时**提示主管核实**，绝不自动扣分。
  这与 ``semantic_qc`` / ``distribution_qc`` 的口径一致：
  **AI 与数据源只提供建议，判定权在人**。
- **幂等**：``(source, window_start, window_end)`` 唯一 ——
  同一窗口重复取数会覆盖而不是追加，避免重复计数。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _uuid


class BaiduPanToken(Base):
    """百度网盘 OAuth 凭证（**唯一一条**）。

    ⚠️ **为什么必须落库**
    百度 ``access_token`` 有效期 30 天，``refresh_token`` 有效期 10 年，
    但**刷新时必须用响应里的新 refresh_token 覆盖旧的**。
    不存 → 30 天后整条链路断掉，必须人工重新授权。

    ⚠️ **安全约束**
    - 一条记录（``id='default'``），不按用户分 —— 因为网盘目录授权是
      **管理员一个人**的事，员工不参与授权。
    - ``access_token`` / ``refresh_token`` **绝不出现在日志或 API 响应里**。
    """

    __tablename__ = "baidu_pan_token"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    #: 应用密钥（也可从配置读，这里存一份便于多应用切换）。
    app_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    app_secret: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    #: 授权时用的回调地址 —— 换 token 时必须与授权时**完全一致**。
    redirect_uri: Mapped[str] = mapped_column(String(512), default="oob", nullable=False)
    #: 当前 access_token。
    access_token: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 用于续期的 refresh_token（**每次刷新后必须更新**）。
    refresh_token: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 授权时拿到的 scope（``basic netdisk``）。
    scope: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: access_token 到期时间戳。
    expires_at: Mapped[float] = mapped_column(Integer, default=0, nullable=False)
    #: 授权时间（用于判断是否需要人工重授权）。
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class BaiduPanCount(Base):
    """一次取数的计数快照。

    一条记录 = 一次同步 × 一个统计窗口 × 一个数据源标识。
    """

    __tablename__ = "baidu_pan_count"
    __table_args__ = (
        UniqueConstraint(
            "source_code", "window_start", "window_end", name="uq_baidu_pan_count_window"
        ),
        Index("ix_baidu_pan_count_window", "window_start", "window_end"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: 数据源标识（一般就是 ``baidu_pan``）。
    source_code: Mapped[str] = mapped_column(String(64), default="baidu_pan", nullable=False)
    #: 统计窗口（YYYY-MM-DD，含端点）。
    window_start: Mapped[str] = mapped_column(String(16), nullable=False)
    window_end: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 窗口内总数。
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 逐日计数 ``{"YYYY-MM-DD": 数量}``。
    daily_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 参与计数的文件数（**总**数，含被规则排除的）—— 用于对账。
    scanned_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 被规则排除的文件数 —— 便于解释「为什么数出来是这个数」。
    excluded_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 扫描的网盘路径（便于排查扫错目录）。
    scanned_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    #: 文件名摘要（前 N 个），用于事后追溯。
    file_digest: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 数据截至时间（网盘上传时间有延迟）。
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class BaiduPanSyncBatch(Base):
    """一次取数同步的执行记录。"""

    __tablename__ = "baidu_pan_sync_batch"
    __table_args__ = (Index("ix_baidu_pan_sync_batch_status", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: ``scheduled`` / ``manual``
    trigger_type: Mapped[str] = mapped_column(String(16), default="scheduled", nullable=False)
    #: ``running`` / ``succeeded`` / ``failed``
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    #: 统计到的文件总数。
    scanned_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 计入计数的文件数。
    counted_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 被排除的文件数。
    excluded_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 逐条明细。
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
