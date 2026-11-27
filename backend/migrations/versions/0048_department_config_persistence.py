"""P0-3：部门配置 / 待办 / 导出 / 计划调整 —— 假成功端点落库。

Revision ID: 0048_department_config_persistence
Revises: 0047_ai_model_config

背景
----
2026-11-26 全面审计发现 ``routes/department.py`` / ``routes/leader.py`` /
``routes/employee.py`` 里有一批端点**只返回结构完整的假对象**：

======================================  ==========================================
端点                                     原行为
======================================  ==========================================
``GET  /v1/department/templates``        恒返回 ``[]``
``POST /v1/department/templates``        返回 ``id: 0``，**库里什么都没写**
``PUT  /v1/department/templates/{id}``   同上
``POST /v1/department/templates/{id}/{action}``  只回字符串
``GET  /v1/department/acceptance-standards``   恒返回 ``[]``
``POST /v1/department/acceptance-standards``   返回 ``id: 0``
``GET  /v1/department/score-rules``      恒返回 ``[]``
``POST /v1/department/score-rules``      返回 ``id: 0``
``POST /v1/department/score-rules/{id}/simulate`` 返回 ``score: 0``
``GET  /v1/department/todos``            恒返回空分页
``POST /v1/department/todos/{id}/*``     只回字符串，无任何写入
``GET  /v1/department/export-tasks``     恒返回空分页
``POST /v1/department/export-tasks``     返回空壳任务
``GET  /v1/department/department-ledgers``  恒返回空分页
``GET  /v1/leader/team-ledgers``         恒返回空分页
``GET  /v1/leader/plan-adjustments``     恒返回 ``[]``
``POST /v1/employee/plan-adjustments``   返回 ``id: 0``
======================================  ==========================================

而前端**有完整的页面和按钮** ⇒ 用户点「保存」弹「已保存」，
**刷新后数据全没了** ⇒ 这是最典型的「假功能」。

⚠️ 建表范围
-----------
本迁移只建**能真正存住、能真正查回来**的最小字段集，
不臆造前端用不到的列。

⚠️ 导出任务的产物
-----------------
文件字节**不入库** —— 落盘到 ``settings.export_output_dir``，
表里只存元信息 + 校验值 + 相对路径。
"""

import sqlalchemy as sa
from alembic import op

revision = "0048_department_config_persistence"
down_revision = "0047_ai_model_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 交付物模板 ────────────────────────────────────────────────
    op.create_table(
        "deliverable_template",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("org_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("template_name", sa.String(length=128), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False, server_default="DOCUMENT"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("applies_to", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("version_no", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ENABLED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deliverable_template_status", "deliverable_template", ["status"])
    op.create_index(
        "ix_deliverable_template_org", "deliverable_template", ["org_id", "status"]
    )

    # ── 验收标准 ──────────────────────────────────────────────────
    op.create_table(
        "acceptance_standard",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "template_id",
            sa.Integer(),
            sa.ForeignKey("deliverable_template.id"),
            nullable=False,
        ),
        sa.Column("standard_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("require_review_passed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("evidence_requirement", sa.Text(), nullable=False, server_default=""),
        sa.Column("version_no", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ENABLED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_acceptance_standard_status", "acceptance_standard", ["status"])
    op.create_index(
        "ix_acceptance_standard_template", "acceptance_standard", ["template_id", "status"]
    )

    # ── 参考分规则 ────────────────────────────────────────────────
    op.create_table(
        "score_rule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("org_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("rule_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ENABLED"),
        sa.Column("effective_start", sa.String(length=16), nullable=True),
        sa.Column("effective_end", sa.String(length=16), nullable=True),
        sa.Column("rule_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_score_rule_status", "score_rule", ["status"])
    op.create_index("ix_score_rule_org", "score_rule", ["org_id", "status"])

    # ── 部门待办 ──────────────────────────────────────────────────
    op.create_table(
        "dept_todo",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scene_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("trigger_text", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("receiver_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("object_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("object_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("requirement", sa.Text(), nullable=False, server_default=""),
        sa.Column("impact", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="UNREAD"),
        sa.Column("remind_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("route_hint", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dept_todo_scene_code", "dept_todo", ["scene_code"])
    op.create_index("ix_dept_todo_status", "dept_todo", ["status"])
    op.create_index("ix_dept_todo_receiver_id", "dept_todo", ["receiver_id"])

    # ── 导出任务 ──────────────────────────────────────────────────
    op.create_table(
        "export_task",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dimension_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("dimension_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("dimension_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("period_type", sa.String(length=16), nullable=False, server_default="ALL"),
        sa.Column("period_start", sa.String(length=16), nullable=True),
        sa.Column("period_end", sa.String(length=16), nullable=True),
        sa.Column("formats", sa.JSON(), nullable=False),
        sa.Column("include_evidence", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("watermark", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("integrity_status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("missing_items", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PROCESSING"),
        sa.Column("size_text", sa.String(length=32), nullable=False, server_default="0 B"),
        sa.Column("requested_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("expire_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("artifact_path", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_export_task_status", "export_task", ["status"])
    op.create_index("ix_export_task_requester", "export_task", ["requested_by"])

    # ── 计划调整 ──────────────────────────────────────────────────
    op.create_table(
        "plan_adjustment",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("adjustment_no", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("original_plan_type", sa.String(length=16), nullable=False, server_default="MONTH"),
        sa.Column("original_plan_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("original_plan_no", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("original_work_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("new_plan_type", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("new_plan_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("new_plan_no", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("adjustment_type", sa.String(length=16), nullable=False, server_default="PAUSE"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("impact_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("operation_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("keep_evidence_chain", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("operator_id", sa.String(length=64), nullable=True),
        sa.Column("operated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plan_adjustment_status", "plan_adjustment", ["status"])
    op.create_index("ix_plan_adjustment_adjustment_no", "plan_adjustment", ["adjustment_no"])
    op.create_index(
        "ix_plan_adjustment_owner_status", "plan_adjustment", ["owner_id", "status"]
    )

    op.create_table(
        "plan_adjustment_seq",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period", name="uq_plan_adjustment_seq_period"),
    )


def downgrade() -> None:
    op.drop_table("plan_adjustment_seq")
    op.drop_index("ix_plan_adjustment_owner_status", table_name="plan_adjustment")
    op.drop_index("ix_plan_adjustment_adjustment_no", table_name="plan_adjustment")
    op.drop_index("ix_plan_adjustment_status", table_name="plan_adjustment")
    op.drop_table("plan_adjustment")
    op.drop_index("ix_export_task_requester", table_name="export_task")
    op.drop_index("ix_export_task_status", table_name="export_task")
    op.drop_table("export_task")
    op.drop_index("ix_dept_todo_receiver_id", table_name="dept_todo")
    op.drop_index("ix_dept_todo_status", table_name="dept_todo")
    op.drop_index("ix_dept_todo_scene_code", table_name="dept_todo")
    op.drop_table("dept_todo")
    op.drop_index("ix_score_rule_org", table_name="score_rule")
    op.drop_index("ix_score_rule_status", table_name="score_rule")
    op.drop_table("score_rule")
    op.drop_index("ix_acceptance_standard_template", table_name="acceptance_standard")
    op.drop_index("ix_acceptance_standard_status", table_name="acceptance_standard")
    op.drop_table("acceptance_standard")
    op.drop_index("ix_deliverable_template_org", table_name="deliverable_template")
    op.drop_index("ix_deliverable_template_status", table_name="deliverable_template")
    op.drop_table("deliverable_template")
