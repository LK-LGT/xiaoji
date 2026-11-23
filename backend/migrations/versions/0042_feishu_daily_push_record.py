"""每日填报提醒的推送记录（``feishu_daily_push_record`` 表）。

Revision ID: 0042_feishu_daily_push_record
Revises: 0041_pan_feishu_metric_source

背景
----
2026-11-23 用户诉求：

    「就是每天短视频填报的这种飞书端的话，
      是以表格的形式推送吗，还是怎么样的呢」

真机实测结论（2026-11-23）
--------------------------
| 结论 | 说明 |
|---|---|
| ``table`` 元素**内部**放不了 ``input`` | 飞书官方限制（纯展示） |
| ``table`` 与 ``form`` **作为兄弟元素并列** | ✅ **可以** —— 真发一张卡验证通过 |

⇒ 「以表格形式推送」**做得到**：
一张卡上半部分是**表格**（今天要填什么、当前状态），
下半部分是**表单**（直接填完提交）⇒ 员工不用跳走。

⚠️ **为什么要有这张表**
----------------------
推送**幂等**必须落库 ——
服务是多实例部署的，内存状态挡不住「两个实例都判定该推了」
⇒ 剪辑会收到**两张一样的提醒卡**。

⇒ 唯一约束 ``(push_day, kind, target_id)`` 兜底。

⚠️ **两种 kind**
- ``editor`` —— 推给剪辑本人的「表格 + 表单」卡
- ``leader`` —— 推给主管的**只读**汇总卡（不给表单，防止代填）

本迁移只建表，**不预置任何行** ——
推送记录是运行时产生的，预置假行只会污染统计。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0042_feishu_daily_push_record"
down_revision = "0041_pan_feishu_metric_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feishu_daily_push_record",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("push_day", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="sent"),
        sa.Column("error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id"], ["employee.id"], name="fk_feishu_daily_push_record_employee"
        ),
        sa.PrimaryKeyConstraint("id"),
        # ⚠️ **一天一人一类只推一次** —— 多实例兜底
        sa.UniqueConstraint(
            "push_day", "kind", "target_id", name="uq_feishu_daily_push_record_day_kind"
        ),
    )
    op.create_index(
        "ix_feishu_daily_push_record_day",
        "feishu_daily_push_record",
        ["push_day"],
        unique=False,
    )
    op.create_index(
        "ix_feishu_daily_push_record_target",
        "feishu_daily_push_record",
        ["target_id"],
        unique=False,
    )


def downgrade() -> None:
    # ⚠️ **先删数据再删表** —— SQLite 的 ``drop_table`` 不带外键级联
    op.get_bind().execute(sa.text("delete from feishu_daily_push_record"))
    op.drop_index(
        "ix_feishu_daily_push_record_target", table_name="feishu_daily_push_record"
    )
    op.drop_index(
        "ix_feishu_daily_push_record_day", table_name="feishu_daily_push_record"
    )
    op.drop_table("feishu_daily_push_record")
