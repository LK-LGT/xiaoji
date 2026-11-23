"""飞书表格填报绑定（``pan_feishu_metric_source`` 表）。

Revision ID: 0041_pan_feishu_metric_source
Revises: 0040_department_module_isolation

背景
----
2026-11-21 用户诉求：

    「那剪辑人员如何在飞书端填表呢，
      我想要的就是该部门是以**表格**的形式填写。」

实测结论（2026-11-21 当天跑飞书开放平台）
-------------------------------------------
| 能力 | 实测结果 | 错误码 |
|---|---|---|
| 云表格**读** ``sheets:spreadsheet:readonly`` | ✅ 已开通 | 参数类错误 |
| 云表格**写** ``sheets:spreadsheet`` | ❌ 未开通 | ``99991672`` |
| 多维表格 ``bitable:app:readonly`` | ❌ 未开通 | ``99991672`` |
| 云空间建文件 / 上传素材 | ❌ 未开通 | ``99991672`` |
| 云空间读 / wiki 读 | ✅ 已开通 | — |

⚠️ **飞书卡片 JSON 2.0 的 ``table`` 组件不能内嵌输入框**
（官方：「表格组件不支持内嵌其它组件」）
⇒ **卡片里做不出「能填写的表格」**。

⇒ 结论：**表格必须落在飞书原生的表格上**，系统只做「读 + 同步」：
1. **电子表格（Spreadsheets）** —— 读权限已开通，**今天就能用**
2. **多维表格（Bitable）** —— 最贴合「表格」语义（人员字段自动归属、
   附件字段挂截图），但**需要 IT 申请 scope**

本表 = 「员工 ↔ 飞书表格」的绑定。
⚠️ **一人一张表** ⇒ 表格里每一行归属**在绑定时就已确定**，
   同步时**不需要按人名猜** ⇒ 不会把数据算到别人头上。

本迁移只建表，**不预置任何绑定行** ——
绑定要由 HR 在系统里粘贴每个剪辑同事的表格链接才能建立，
而链接内容系统无从推断 ⇒ 预置空行或假行都只会误导。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0041_pan_feishu_metric_source"
down_revision = "0040_department_module_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pan_feishu_metric_source",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(length=64), nullable=False),
        sa.Column("table_kind", sa.String(length=16), nullable=False, server_default="sheet"),
        sa.Column("obj_token", sa.String(length=64), nullable=False),
        sa.Column("table_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("sheet_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_edit_time", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("last_content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employee.id"], name="fk_pan_feishu_metric_source_employee"
        ),
        sa.PrimaryKeyConstraint("id"),
        # ⚠️ **一个员工只能绑一张表** —— 否则同步会重复计入
        sa.UniqueConstraint("employee_id", name="uq_pan_feishu_metric_source_employee"),
    )
    op.create_index(
        "ix_pan_feishu_metric_source_active",
        "pan_feishu_metric_source",
        ["active"],
        unique=False,
    )
    op.create_index(
        "ix_pan_feishu_metric_source_employee",
        "pan_feishu_metric_source",
        ["employee_id"],
        unique=False,
    )


def downgrade() -> None:
    # ⚠️ **先删数据再删表** —— SQLite 的 ``drop_table`` 不带外键级联
    op.get_bind().execute(sa.text("delete from pan_feishu_metric_source"))
    op.drop_index("ix_pan_feishu_metric_source_employee", table_name="pan_feishu_metric_source")
    op.drop_index("ix_pan_feishu_metric_source_active", table_name="pan_feishu_metric_source")
    op.drop_table("pan_feishu_metric_source")
