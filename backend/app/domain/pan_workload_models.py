"""网盘剪辑工作量统计的持久化模型（BP-2）。

背景
----
BP-1 把「网盘里有多少个成片」变成**部门级总量**快照。本模块把同一批网盘文件
落成**按人、按天、带证据**的可核对工作量：

    网盘文件 ──(PanWorkloadFile)──> PanWorkloadDaily ──> MetricValue
                    │                      │
                    └─ 审计根（永久留存）  └─ 物化聚合（可重算）

七张表
------
``pan_account_binding``
    员工 ↔ 网盘目录的映射。**归属判定的唯一依据**
    （实测：共享目录下 ``oper_id`` 无区分度，见 §〇 C1）。

``pan_workload_file``
    ★ **审计根** —— 每个网盘文件一行，永久留存，含 md5 / 路径 / 权重 / 排除原因。
    ⇒ 任何条数都能回溯到具体文件。

``pan_workload_daily``
    按人按天的物化聚合。**重算而非累加**，保证重复跑同步不重复计数。

``pan_workload_review``
    异议复核工单（员工对条数有异议 → 发起 → 主管裁定）。

``pan_workload_review_event``
    工单事件流（hash 链，全程留痕可审计）。

``pan_manual_metric``
    运营人工填报（完播率/留存率）—— 网盘没有的数据。

``pan_workload_config``
    规则配置（标记字典 / 扩展名白名单 / 告警阈值）。

设计要点
--------
- **不自动改分**：网盘条数只作为**证据**，与申报值不一致时提示主管核实。
- **判定权在人**：归属冲突、无归属、日期冲突都**留痕待人工确认**，不静默丢弃。
- **幂等**：按 ``fs_id`` upsert；日聚合**重算**而非累加。
- **永久留存**：``pan_workload_file`` 只增不改（更正走追加 + 复核工单）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _uuid


class PanAccountBinding(Base):
    """剪辑人员 ↔ 网盘目录映射（**归属判定的唯一依据**）。

    ⚠️ **为什么不能用上传者 ID 拆分（2026-11-21 实测）**
    公司共享网盘下 ``oper_id`` **232/232 条全部等于公司账号的 uk**，
    共享目录**不暴露真实上传者** ⇒ ``oper_id`` 无区分度。
    实测网盘根下已有按人隔离的顶层目录（``/成品-小瑶``、``/成品-小雨``），
    ⇒ 归属 = **路径前缀匹配**。

    ⚠️ **前缀唯一性约束**
    ``/成品`` 与 ``/成品-小瑶`` 互为前缀 ⇒ 必须禁止同时配置，
    否则一个文件命中两条绑定（``ambiguous``），
    而自动取第一条会导致**同一文件算给两个人**。
    ⇒ 校验逻辑见 ``pan_workload_rules.validate_bindings``。

    业务约束：
    - 一个员工可有多条绑定（多个目录 / 换目录时新增一条）
    - 同一时间同一条路径只能绑一个员工
    - 换目录时旧绑定置 ``active=False``，**不删**（历史证据要能追溯）
    """

    __tablename__ = "pan_account_binding"
    __table_args__ = (
        UniqueConstraint("employee_id", "folder_path", name="uq_pan_binding_emp_path"),
        UniqueConstraint("folder_path", name="uq_pan_binding_path"),
        Index("ix_pan_binding_active_path", "active", "folder_path"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    #: 归属判定方式：``path``（按目录，唯一有效值）
    bind_type: Mapped[str] = mapped_column(String(16), default="path", nullable=False)
    #: 网盘绝对路径前缀，如 ``/成品-小瑶``
    folder_path: Mapped[str] = mapped_column(String(512), nullable=False)
    #: 目录显示名（如「小瑶」），用于前端展示与校验
    folder_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    #: 百度 oper_id（**实测在共享目录下无区分度**，仅作审计留痕）
    oper_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 是否生效（换目录时旧行置 False，不物理删除）
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 该绑定下的子目录是否也按日拆分（``11月/11.19`` 结构 → True）
    daily_subdir: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanWorkloadFile(Base):
    """★ **审计根** —— 单文件证据（**永久留存**）。

    ⚠️ **为什么每个文件都要落库**
    只存「某人某天 10 条」，出了争议无法回溯是哪 10 个文件。
    ⇒ 这一张表是**争议时的唯一依据**，必须能回溯到
    「具体哪个 fs_id、什么路径、什么 md5、为什么算这个权重」。

    ⚠️ **只增不改**
    文件改名/移动会体现为**同一 fs_id 的新行**或 ``last_seen_at`` 更新，
    但**历史权重与结论不被覆盖** —— 更正走复核工单（追加留痕）。
    """

    __tablename__ = "pan_workload_file"
    __table_args__ = (
        # ★ 同一 fs_id 只一行 ⇒ 幂等锚点（重复采集不会重复计数）
        UniqueConstraint("fs_id", name="uq_pan_workload_file_fs_id"),
        Index("ix_pan_workload_file_owner_date", "employee_id", "business_date"),
        Index("ix_pan_workload_file_digest", "content_digest"),
        Index("ix_pan_workload_file_bind", "bind_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: 网盘文件 ID（**永不变化** ⇒ 幂等主键）
    fs_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 网盘绝对路径
    pan_path: Mapped[str] = mapped_column(String(512), nullable=False)
    #: 文件名
    file_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 扩展名（小写，不含点）
    extension: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    #: 文件大小（字节）
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 内容指纹（``md5:<hash>`` 或 ``ns:<name>:<size>``）
    content_digest: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    #: 网盘 md5（原始值）
    md5: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 网盘上传时间戳（**≠ 业务日期**，仅作参考）
    pan_ctime: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 归属员工（**空 = 未归属**，不丢弃、待人工确认）
    employee_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=True, index=True
    )
    #: 归属判定方式：``path`` / ``unbound`` / ``ambiguous``
    bind_type: Mapped[str] = mapped_column(String(16), default="path", nullable=False)
    #: 归属异常说明（如「命中 2 条绑定」）
    bind_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 业务日期 YYYY-MM-DD（**从目录名解析**，不从上传时间）
    business_date: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    #: 是否计入工作量
    counted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 折算条数（普通 1，智能体/OOTD 2）
    count_weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: 命中的标记名列表
    markers: Mapped[dict[str, Any]] = mapped_column(JSON, default=list, nullable=False)
    #: 排除原因（未计入时必填，如 ``ext_not_allowed:png``）
    exclude_reason: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 异常标记列表（如 ``["name_date_conflict"]``）
    anomaly_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=list, nullable=False)
    #: 是否判定为重复上传
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 重复类型：``md5`` / ``name_size`` / ``none``
    duplicate_kind: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    #: 重复指向的文件 id
    duplicate_of_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 最近一次采集到它的时间
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    #: 首次采集时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanWorkloadDaily(Base):
    """按人按天的物化聚合。

    ⚠️ **重算而非累加**
    采集时把某人某天的**全部文件重新判定一遍**再写这里。
    ⇒ 重复跑同步不会把条数加两遍；
    ⇒ 规则改了重跑一次就全部生效（不需要数据迁移）。
    """

    __tablename__ = "pan_workload_daily"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "business_date", name="uq_pan_workload_daily_emp_date"
        ),
        Index("ix_pan_workload_daily_date", "business_date"),
        Index("ix_pan_workload_daily_emp", "employee_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    #: 业务日期（Date 类型，便于按月聚合）
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    #: 总条数（已含标记权重）
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 计入的文件数
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 扫描到的文件数（含被排除的）
    scanned_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 被排除的文件数
    excluded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 标记明细 ``{"智能体": 2, "OOTD": 3}``
    marker_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 异常标记计数 ``{"name_date_conflict": 1}``
    anomaly_count: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 人工调整后的条数（**空 = 未调整**；复核工单通过后写入）
    adjusted_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 调整原因
    adjust_reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 状态：``auto``（机器判定）/ ``confirmed``（人工已确认）/ ``disputed``（有异议）
    status: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanWorkloadReview(Base):
    """异议复核工单。

    ⚠️ **判定权在人的载体**
    员工对「我的条数不对」发起工单 → 主管裁定 → 结果写回日汇总。
    ⇒ 网盘条数**从不自动改分**。

    ⚠️ **裁定粒度有两种**（2026-11-21 业务确认）
    ``day``
        **按天整体裁定** —— 主管直接定这天最终多少条。
        适用于「这天整体数错了」。
    ``file``
        **逐文件裁定** —— 每个争议文件单独判，最终条数 =
        系统条数 ± 裁定调整。适用于「就是这几个文件有问题」。
    ⇒ 两种都要能表达，否则员工只能笼统地说「我那天数不对」。
    """

    __tablename__ = "pan_workload_review"
    __table_args__ = (
        Index("ix_pan_workload_review_status", "status"),
        Index("ix_pan_workload_review_emp", "employee_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    #: 状态：``pending`` / ``approved`` / ``rejected`` / ``withdrawn``
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    #: ⚠️ **裁定粒度**：``day``（按天整体）/ ``file``（逐文件）
    scope: Mapped[str] = mapped_column(String(16), default="day", nullable=False)
    #: 员工申报的条数
    claimed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 系统判定的条数（发起时的快照）
    system_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 裁定后的条数
    final_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 员工申诉理由
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 主管裁定意见
    resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 涉及的文件 id 列表（快照，便于复核时定位证据）
    related_file_ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=list, nullable=False)
    #: ⚠️ **发起时的日汇总快照** —— 争议发生后系统可能重算，
    #: 但「当时判定为多少条」必须固定下来，否则无从对账。
    daily_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PanWorkloadReviewItem(Base):
    """★ **争议项** —— 工单里「具体哪些文件有争议」。

    ⚠️ **为什么必须逐项记录**
    员工说「我那天是 8 条，系统算 6 条」是不够的 ——
    得知道**差在哪两个**。逐项记录才能：
    - 主管逐个查看每个争议文件的证据（路径、md5、权重）
    - 逐个裁定（认可 / 不认可 / 需补证）
    - 事后回溯「当初争议的是什么」

    ⚠️ **系统没有的文件也能提**
    实测存在「系统没扫到但确实剪了」的情况（如漏传）。
    ⇒ ``pan_workload_file_id`` **可为空**，
    此时用 ``manual_name`` / ``manual_description`` 记录。
    """

    __tablename__ = "pan_workload_review_item"
    __table_args__ = (
        Index("ix_pan_workload_review_item_review", "review_id"),
        Index("ix_pan_workload_review_item_file", "pan_workload_file_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    review_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("pan_workload_review.id"), nullable=False
    )
    #: 关联的网盘文件（**空 = 系统没扫到，员工手动描述**）
    pan_workload_file_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("pan_workload_file.id"), nullable=True
    )
    #: 员工手填的文件名（系统没扫到时用）
    manual_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 员工手填的说明
    manual_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 争议类型
    #: ``missing``（系统漏了）/ ``overcounted``（系统多算了）
    #: ``weight``（条数权重不对）/ ``other``
    dispute_type: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    #: 员工主张的条数（该文件应算几条）
    claimed_weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: 裁定结果：``pending`` / ``upheld``（成立）/ ``rejected``（不成立）
    #: / ``need_more``（需补证）
    resolution: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    #: 裁定意见
    resolution_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 裁定后该文件应算的条数（空 = 未裁定）
    final_weight: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanWorkloadReviewArtifact(Base):
    """★ **证据附件** —— 员工上传的截图 / 视频 / 文档。

    ⚠️ **与框架 ``ReviewArtifact`` 保持一致的设计**
    - 内容用 ``LargeBinary`` 存库（**不落文件系统** ⇒ 不会丢文件、不会路径穿越）
    - 存 ``digest``（sha256）⇒ 可校验文件未被篡改
    - **不可变**（``before_update`` / ``before_delete`` 抛错）⇒ 证据不能被改

    ⚠️ **为什么证据要进 hash 链**
    员工上传的截图是裁定的依据。若事后能替换截图，链就失去意义。
    ⇒ 附件的 ``digest`` 会写进事件流的 payload，
    链校验时能发现「证据被换过」。
    """

    __tablename__ = "pan_workload_review_artifact"
    __table_args__ = (
        Index("ix_pan_workload_review_artifact_review", "review_id"),
        Index("ix_pan_workload_review_artifact_item", "review_item_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    review_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("pan_workload_review.id"), nullable=False
    )
    #: 归属的争议项（**空 = 整个工单的通用证据**）
    review_item_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("pan_workload_review_item.id"), nullable=True
    )
    #: 原始文件名
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: MIME 类型（image/png、video/mp4 …）
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    #: 文件内容
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: 内容 sha256 —— 可校验未被篡改
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 文件大小（字节）
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanWorkloadReviewEvent(Base):
    """复核工单事件流（**hash 链** ⇒ 全程留痕可审计）。

    ⚠️ **hash 链的作用**
    每条事件存 ``prev_hash`` 与本事件内容的 ``hash``，
    任何一条被篡改或删除，后续链校验就会断 ⇒ 可事后验证完整性。
    """

    __tablename__ = "pan_workload_review_event"
    __table_args__ = (
        Index("ix_pan_workload_review_event_review", "review_id"),
        UniqueConstraint("review_id", "seq", name="uq_pan_review_event_seq"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    review_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("pan_workload_review.id"), nullable=False
    )
    #: 序号（从 1 开始递增）
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 事件类型：``created`` / ``approved`` / ``rejected`` / ``withdrawn`` / ``comment``
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 操作人
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 事件内容摘要
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 上一条事件的 hash
    prev_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 本事件的 hash
    event_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanManualMetric(Base):
    """运营人工填报（完播率 / 留存率）—— 网盘里**没有**的数据。

    ⚠️ **与网盘条数合并进同一个绩效面板**
    网盘只提供「做了多少条」，人工填报提供「效果如何」，
    两者在月度聚合时合并 —— 但**来源必须可区分**（``source`` 字段）。

    ⚠️ **两种填报粒度**（2026-11-21 业务确认）
    ``scope="day"``
        按天填一个值（``target_key`` 空）。
    ``scope="file"``
        逐条成片填（``target_key`` = 网盘文件 id）。
    ⇒ 唯一约束必须带上 ``target_key``，
    否则同一天同一指标第二条成片的填报会撞唯一键。
    """

    __tablename__ = "pan_manual_metric"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "metric_date", "metric_code", "target_key",
            name="uq_pan_manual_metric_key",
        ),
        Index("ix_pan_manual_metric_emp", "employee_id"),
        Index("ix_pan_manual_metric_date", "metric_date"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("employee.id"), nullable=False, index=True
    )
    #: 业务日期
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    #: 指标编码（如 ``completion_rate`` / ``retention_rate``）
    metric_code: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 指标值
    metric_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    #: ⚠️ **填报粒度**：``day``（按天）/ ``file``（逐条成片）
    scope: Mapped[str] = mapped_column(String(16), default="day", nullable=False)
    #: ⚠️ **填报对象键** —— ``scope=day`` 时为空串；
    #: ``scope=file`` 时为网盘文件 id。⇒ 参与唯一约束。
    target_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 关联的网盘文件 id（用于「这条指标对应哪条成片」）
    related_file_ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=list, nullable=False)
    #: 来源：``manual``（前端）/ ``feishu``（飞书）/ ``import``（Excel）
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    #: ⚠️ **飞书来源留痕** —— 哪个机器人会话、哪条消息、谁发的
    feishu_open_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 导入批次 id（Excel 导入时关联）
    import_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanManualMetricArtifact(Base):
    """★ **人工填报的证据附件** —— 飞书端或前端上传的截图。

    ⚠️ **为什么单独一张表，不复用复核工单的附件**
    人工填报**没有工单**，它是「直接提交一条指标 + 证据」。
    ⇒ 挂在指标上，删指标时一并删（由服务层控制）。

    ⚠️ **飞书来源要留痕**
    飞书端的图片是**从飞书消息里拉下来的**（``image_key``），
    ⇒ 必须记下「哪个 key / 哪条消息 / 谁发的」，
    否则事后无法回溯「这张截图是从哪来的」。
    """

    __tablename__ = "pan_manual_metric_artifact"
    __table_args__ = (
        Index("ix_pan_manual_metric_artifact_metric", "metric_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    #: ⚠️ **归属的人工指标** —— **空 = 待关联**（员工先发了截图，
    #: 还没补数值；等他补了再挂上来）。
    #: ⇒ 附件**永远不会因为「还没填数值」被丢弃**。
    metric_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("pan_manual_metric.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 来源：``frontend`` / ``feishu``
    source: Mapped[str] = mapped_column(String(16), default="frontend", nullable=False)
    #: ⚠️ 飞书来源留痕
    feishu_image_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feishu_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PanWorkloadConfig(Base):
    """规则配置（单行表）。

    ⚠️ **为什么规则要能配**
    实测两个人的命名风格差异很大（``11.13 智能体`` vs ``11.14-智能体``），
    且业务口径会变（标记权重、扩展名白名单、排除关键词）。
    ⇒ 规则**存在库里而不是代码里** —— 改完重跑采集即可，不发版。
    """

    __tablename__ = "pan_workload_config"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    #: 允许的扩展名 —— ⚠️ **刻意不含 png/jpg**（实测 mp4+png 是同一内容的两种格式）
    allowed_extensions: Mapped[str] = mapped_column(
        String(255), default="mp4,mov,m4v,avi,mkv,flv,wmv", nullable=False
    )
    #: 排除关键词（草稿/工程/返工）
    exclude_keywords: Mapped[str] = mapped_column(
        String(512),
        default="草稿,工程,返工,废片,废弃,作废,rework,_v2,_v3,副本,prj,aep",
        nullable=False,
    )
    #: 排除的目录路径段
    exclude_dir_segments: Mapped[str] = mapped_column(
        String(255), default="草稿,工程,prj,aep,project,源文件,素材", nullable=False
    )
    #: ⚠️ **扫描范围的目录黑名单**（2026-11-21 业务确认）
    #: ``/口播成品``（1455 mp4，另一类业务）与 ``/鹿13``（素材库）**不纳入统计**
    exclude_paths: Mapped[str] = mapped_column(
        String(512), default="/口播成品,/鹿13", nullable=False
    )
    #: 标记字典（JSON）—— 全局默认 + 按目录覆盖
    marker_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 突增阈值（相对近 N 日均值的倍数）
    surge_multiplier: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    #: 突减阈值
    drop_multiplier: Mapped[float] = mapped_column(Float, default=0.4, nullable=False)
    #: 基线窗口天数
    baseline_days: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# ---------------------------------------------------------------------------
# 不可变守卫
# ---------------------------------------------------------------------------
# ⚠️ **证据与事件流不可覆盖**
# 事件流是 hash 链的一环，附件是裁定的依据 ——
# 任何一条被 UPDATE 或 DELETE，链就失去审计价值。
# ⇒ 与框架 ``review_models._immutable`` 保持同一套做法。
# ---------------------------------------------------------------------------


def _immutable(_mapper, _connection, _target):
    raise ValueError("正式历史记录禁止覆盖或删除，请追加更正记录")


for _model in (
    PanWorkloadReviewEvent,
    PanWorkloadReviewArtifact,
):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
