"""网盘剪辑工作量统计（BP-2）—— 目录映射 / 文件证据 / 日聚合 / 复核工单 / 人工填报 / 配置。

Revision ID: 0034_pan_workload
Revises: 0033_baidu_pan_verify

背景
----
BP-1 只做「部门级总量」快照。本迁移把同一批网盘文件落成
**按人、按天、带证据**的可核对工作量：

    网盘文件 ──> pan_workload_file ──> pan_workload_daily ──> MetricValue
                  （审计根，永久留存）    （物化聚合，可重算）

七张表
------
``pan_account_binding``
    员工 ↔ 网盘目录映射。**归属判定的唯一依据**
    （实测：共享目录下 ``oper_id`` 无区分度）。

``pan_workload_file``
    ★ 审计根 —— 每个网盘文件一行，含 md5 / 路径 / 权重 / 排除原因。
    按 ``fs_id`` 唯一 ⇒ 幂等。

``pan_workload_daily``
    按人按天的物化聚合。**重算而非累加**。

``pan_workload_review`` / ``pan_workload_review_event``
    异议复核工单 + 事件流（hash 链）。

``pan_manual_metric``
    运营人工填报（完播率 / 留存率）。

``pan_workload_config``
    规则配置（单行）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_pan_workload"
down_revision = "0033_baidu_pan_verify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 目录映射 ──
    op.create_table(
        "pan_account_binding",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("employee_id", sa.String(64), nullable=False),
        sa.Column("bind_type", sa.String(16), nullable=False),
        sa.Column("folder_path", sa.String(512), nullable=False),
        sa.Column("folder_name", sa.String(128), nullable=False),
        sa.Column("oper_id", sa.String(32), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("daily_subdir", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.UniqueConstraint(
            "employee_id", "folder_path", name="uq_pan_binding_emp_path"
        ),
        sa.UniqueConstraint("folder_path", name="uq_pan_binding_path"),
    )
    op.create_index(
        "ix_pan_binding_active_path", "pan_account_binding", ["active", "folder_path"]
    )
    op.create_index(
        "ix_pan_account_binding_employee_id", "pan_account_binding", ["employee_id"]
    )

    # ── 文件证据（审计根）──
    op.create_table(
        "pan_workload_file",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("fs_id", sa.Integer(), nullable=False),
        sa.Column("pan_path", sa.String(512), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(128), nullable=False),
        sa.Column("md5", sa.String(64), nullable=False),
        sa.Column("pan_ctime", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.String(64), nullable=True),
        sa.Column("bind_type", sa.String(16), nullable=False),
        sa.Column("bind_note", sa.String(255), nullable=False),
        sa.Column("business_date", sa.String(16), nullable=True),
        sa.Column("counted", sa.Boolean(), nullable=False),
        sa.Column("count_weight", sa.Integer(), nullable=False),
        sa.Column("markers", sa.JSON(), nullable=False),
        sa.Column("exclude_reason", sa.String(64), nullable=False),
        sa.Column("anomaly_flags", sa.JSON(), nullable=False),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False),
        sa.Column("duplicate_kind", sa.String(16), nullable=False),
        sa.Column("duplicate_of_id", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.UniqueConstraint("fs_id", name="uq_pan_workload_file_fs_id"),
    )
    op.create_index(
        "ix_pan_workload_file_owner_date",
        "pan_workload_file",
        ["employee_id", "business_date"],
    )
    op.create_index(
        "ix_pan_workload_file_digest", "pan_workload_file", ["content_digest"]
    )
    op.create_index("ix_pan_workload_file_bind", "pan_workload_file", ["bind_type"])
    op.create_index(
        "ix_pan_workload_file_business_date", "pan_workload_file", ["business_date"]
    )
    op.create_index(
        "ix_pan_workload_file_employee_id", "pan_workload_file", ["employee_id"]
    )

    # ── 日聚合 ──
    op.create_table(
        "pan_workload_daily",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("employee_id", sa.String(64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("marker_breakdown", sa.JSON(), nullable=False),
        sa.Column("anomaly_count", sa.JSON(), nullable=False),
        sa.Column("adjusted_count", sa.Integer(), nullable=True),
        sa.Column("adjust_reason", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.UniqueConstraint(
            "employee_id", "business_date", name="uq_pan_workload_daily_emp_date"
        ),
    )
    op.create_index(
        "ix_pan_workload_daily_date", "pan_workload_daily", ["business_date"]
    )
    op.create_index(
        "ix_pan_workload_daily_emp", "pan_workload_daily", ["employee_id"]
    )

    # ── 复核工单 ──
    op.create_table(
        "pan_workload_review",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("employee_id", sa.String(64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("claimed_count", sa.Integer(), nullable=False),
        sa.Column("system_count", sa.Integer(), nullable=False),
        sa.Column("final_count", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("related_file_ids", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
    )
    op.create_index(
        "ix_pan_workload_review_status", "pan_workload_review", ["status"]
    )
    op.create_index(
        "ix_pan_workload_review_emp", "pan_workload_review", ["employee_id"]
    )
    op.create_index(
        "ix_pan_workload_review_business_date",
        "pan_workload_review",
        ["business_date"],
    )

    op.create_table(
        "pan_workload_review_event",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["pan_workload_review.id"]),
        sa.UniqueConstraint("review_id", "seq", name="uq_pan_review_event_seq"),
    )
    op.create_index(
        "ix_pan_workload_review_event_review",
        "pan_workload_review_event",
        ["review_id"],
    )

    # ── 人工填报 ──
    op.create_table(
        "pan_manual_metric",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("employee_id", sa.String(64), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("metric_code", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("related_file_ids", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("import_batch_id", sa.String(64), nullable=True),
        sa.Column("remark", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.UniqueConstraint(
            "employee_id", "metric_date", "metric_code", name="uq_pan_manual_metric_key"
        ),
    )
    op.create_index(
        "ix_pan_manual_metric_emp", "pan_manual_metric", ["employee_id"]
    )
    op.create_index(
        "ix_pan_manual_metric_metric_date", "pan_manual_metric", ["metric_date"]
    )

    # ── 配置 ──
    op.create_table(
        "pan_workload_config",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("allowed_extensions", sa.String(255), nullable=False),
        sa.Column("exclude_keywords", sa.String(512), nullable=False),
        sa.Column("exclude_dir_segments", sa.String(255), nullable=False),
        sa.Column("exclude_paths", sa.String(512), nullable=False),
        sa.Column("marker_rules", sa.JSON(), nullable=False),
        sa.Column("surge_multiplier", sa.Float(), nullable=False),
        sa.Column("drop_multiplier", sa.Float(), nullable=False),
        sa.Column("baseline_days", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # ── 初始化配置单行 + 目录黑名单（2026-11-21 业务确认）──
    op.execute(
        """
        INSERT INTO pan_workload_config (
            id, allowed_extensions, exclude_keywords, exclude_dir_segments,
            exclude_paths, marker_rules, surge_multiplier, drop_multiplier,
            baseline_days, updated_by, updated_at
        ) VALUES (
            'default',
            'mp4,mov,m4v,avi,mkv,flv,wmv',
            '草稿,工程,返工,废片,废弃,作废,rework,_v2,_v3,副本,prj,aep',
            '草稿,工程,prj,aep,project,源文件,素材',
            '/口播成品,/鹿13',
            '{"default": {"智能体": {"weight": 2, "patterns": ["智能体"], "enabled": true}, "OOTD": {"weight": 2, "patterns": ["ootd"], "enabled": true}}, "by_folder": {}}',
            2.0, 0.4, 20, 'system', CURRENT_TIMESTAMP
        )
        """
    )


def downgrade() -> None:
    op.drop_table("pan_workload_config")
    op.drop_table("pan_manual_metric")
    op.drop_table("pan_workload_review_event")
    op.drop_table("pan_workload_review")
    op.drop_table("pan_workload_daily")
    op.drop_table("pan_workload_file")
    op.drop_table("pan_account_binding")
