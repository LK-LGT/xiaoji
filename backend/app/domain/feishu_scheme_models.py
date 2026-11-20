"""飞书绩效方案来源绑定与自动同步批次。

背景
----
飞书上的部门绩效方案文档是**事实来源**。本模块把「飞书文档」与
「本系统方案版本」绑定起来，使同步可以全自动完成：

    飞书文档 ──(FeishuSchemeSource)──> 方案编码 ──> SchemeVersion

两张表
------
``feishu_scheme_source``
    方案来源绑定。一条记录 = 一个部门（或岗位范围）对应一份飞书文档。
    - ``binding_mode`` 区分「自动发现」与「人工指定」：
      人工指定的绑定**优先**于自动发现，且自动发现**不会覆盖**它。
    - ``last_edit_time`` 存飞书文档的 ``obj_edit_time``（秒级时间戳），
      是**变更检测**的唯一依据。

``feishu_scheme_sync_batch``
    一次同步的执行记录。保留完整结果，便于排查「为什么没同步」。

设计要点
--------
- **不存 token**：飞书 user_access_token / refresh_token 一律不落库，
  本模块只存「文档标识」与「变更指纹」。
- **幂等**：``last_content_hash`` 存内容 sha256 —— 同一份内容不重复建版本。
- **历史不被改**：自动同步只**新建**版本，绝不覆盖已发布版本；
  旧版本保留不动，新版本接在它后面生效。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _uuid


class FeishuSchemeSource(Base):
    """飞书绩效方案来源绑定。

    一条记录 = 一份飞书方案文档 + 它对应的适用范围（部门/岗位/周期）。
    """

    __tablename__ = "feishu_scheme_source"
    __table_args__ = (
        # 同一个飞书文档只能绑一次 —— 否则会重复建版本。
        UniqueConstraint("obj_token", name="uq_feishu_scheme_source_obj_token"),
        # 同一部门 + 同一周期类型下的绑定查询。
        Index("ix_feishu_scheme_source_dept", "department_id", "period_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: 飞书 wiki 节点 token（知识库节点标识）
    node_token: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    #: 飞书对象 token（docx / sheet 的真实标识）
    obj_token: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 对象类型：docx / sheet / file
    obj_type: Mapped[str] = mapped_column(String(32), default="docx", nullable=False)
    #: 文档标题（飞书返回，便于展示与人工核对）
    title: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    #: 知识空间 ID
    space_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 原始链接（人工填表用）
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    #: 适用范围
    department_id: Mapped[str] = mapped_column(
        ForeignKey("department.id"), nullable=False, index=True
    )
    position: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), default="monthly", nullable=False)

    #: 方案编码 —— 与 PerformanceScheme.code 对应
    scheme_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    #: ``auto`` = 自动发现的绑定；``manual`` = 人工指定的绑定（优先，自动发现不覆盖）
    binding_mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)

    #: 是否参与自动同步。人工可临时停用某个绑定。
    active: Mapped[bool] = mapped_column(default=True, nullable=False)

    #: 上次同步到的飞书文档编辑时间（秒级时间戳字符串）
    last_edit_time: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    #: 上次同步后解析出的内容指纹（sha256），用于幂等去重
    last_content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 上次同步产生的版本 ID
    last_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 上次同步的错误信息
    last_error: Mapped[str] = mapped_column(String(1024), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )


class FeishuSchemeSyncBatch(Base):
    """一次飞书方案同步的执行记录。"""

    __tablename__ = "feishu_scheme_sync_batch"
    __table_args__ = (
        Index("ix_feishu_scheme_sync_batch_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: ``scheduled`` / ``manual``
    trigger_type: Mapped[str] = mapped_column(String(16), default="scheduled", nullable=False)
    #: ``running`` / ``succeeded`` / ``partial`` / ``failed``
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    #: 处理的来源数
    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 检测到变更的来源数
    changed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 成功建版本的来源数
    published_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 失败的来源数
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 幂等键：同一窗口内的重复触发复用同一批次
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: 逐条明细
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


__all__ = ["FeishuSchemeSource", "FeishuSchemeSyncBatch"]
