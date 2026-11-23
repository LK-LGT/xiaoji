"""剪辑工作量的**部门 × 岗位** 口径表（``pan_workload_policy`` 表）。

Revision ID: 0043_pan_workload_policy
Revises: 0042_feishu_daily_push_record

背景
----
用户投诉（2026-11-23）：

    「哪个部门都有不一样的考核方式，肯定是不一样的功能呀」

⚠️ **之前只做了一半的隔离**
---------------------------
``department_module``（0040）解决的是「**这个部门有没有剪辑这个业务**」，
但**部门内部还有不同岗位**，不同岗位的考核方式**完全不一样**。

实测真实库（2026-11-23）—— 短视频部 5 个人，**3 种角色**：

| 人物 | 岗位 | 网盘产出 | 该不该收填报提醒 |
|---|---|---|---|
| 李婵雨 | 剪辑 | `/成品-小雨` | ✅ 要填 |
| 佟瑶 | 剪辑 | `/成品-小瑶` | ✅ 要填 |
| 巫宜珊 | 主播 | `/口播成品` | ❌ 业务已确认**口播成品不用管** |
| 张洁 | 编导 | — | ❌ 只审不填（是剪辑的直属上级） |
| 刘旭 | 运营 | — | ❌ 只审不填（短视频 leader） |

⇒ 若只按「部门开通了板块就全推」⇒ 主播/运营/编导**天天收到**
  跟他们无关的填报提醒。

⚠️ **为什么不用「网盘绑定表」推导**
----------------------------------
``pan_account_binding`` 里还有「巫宜珊 / 口播成品」——
业务已确认**口播成品不用管** ⇒ 按绑定表推会多推 1 人。

⚠️ **为什么不用「岗位名硬编码在 config.py」**
--------------------------------------------
岗位会增减、口径会变（哪天让主播也报数据）⇒ 硬编码要改代码发版。
⇒ 落在表里，改完立即生效。

⚠️ **预置行的依据**
------------------
下面这 3 行**不是猜的**，是 2026-11-23 实测真实库 + 业务确认得出的：

- 短视频 / 剪辑     → ``reporter``（两位剪辑是唯一产出成片并报数据的岗位）
- 短视频 / 编导     → ``reviewer``（张洁是两位剪辑的直属上级）
- 短视频 / 运营     → ``reviewer``（刘旭是短视频 leader）

⚠️ **主播 / 口播** 刻意**不预置** ⇒ 默认 ``none``（不参与）。
这是**业务明确决策**（「口播成品不用管」），不是遗漏 ——
若将来要让主播也报数据，在界面上加一行即可，不用发版。

⚠️ **其他部门** 同样**不预置** ⇒ 默认关闭。
⇒ 漏配 = 该部门收不到推送（**可见、可修**），
  而不是「功能到处乱出现」（**不可见、不可修**）。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0043_pan_workload_policy"
down_revision = "0042_feishu_daily_push_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pan_workload_policy",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("metric_codes", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"], ["department.id"], name="fk_pan_workload_policy_department"
        ),
        sa.PrimaryKeyConstraint("id"),
        # ⚠️ **一个部门一个岗位一条** —— 口径不能有歧义
        sa.UniqueConstraint(
            "department_id", "position", name="uq_pan_workload_policy_dept_position"
        ),
    )
    op.create_index(
        "ix_pan_workload_policy_active",
        "pan_workload_policy",
        ["active"],
        unique=False,
    )
    op.create_index(
        "ix_pan_workload_policy_department_id",
        "pan_workload_policy",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_pan_workload_policy_position",
        "pan_workload_policy",
        ["position"],
        unique=False,
    )

    # ─────────────────────────────────────────────────────────────
    # 预置短视频部的真实口径（依据 2026-11-23 实测库 + 业务确认）
    # ─────────────────────────────────────────────────────────────
    #
    # ⚠️ **为什么用「查了再插」而不是写死 id** ——
    #    部门 id 是飞书同步进来的 GUID，**不是常量** ⇒ 写死会插到不存在的部门。
    bind = op.get_bind()
    video = bind.execute(
        sa.text("SELECT id FROM department WHERE name = '短视频' LIMIT 1")
    ).first()
    if video is None:
        # ⚠️ **部门不存在就不预置** —— 空表是合法状态（默认关闭）。
        return

    department_id = video[0]
    now = datetime.now(UTC).replace(tzinfo=None)
    seed = [
        # 剪辑 = 要填报的人（两位剪辑是唯一产出成片并报数据的岗位）
        ("剪辑", "reporter", "", "短视频部唯一产出成片、需填报完播率/留存率的岗位"),
        # 编导 = 直属上级，只审不填
        ("编导", "reviewer", "", "两位剪辑的直属上级，只收只读汇总卡"),
        # 运营 = 部门 leader，只审不填
        ("运营", "reviewer", "", "短视频部 leader，只收只读汇总卡"),
    ]
    for position, role, metric_codes, note in seed:
        bind.execute(
            sa.text(
                "INSERT INTO pan_workload_policy "
                "(id, department_id, position, role, metric_codes, active, note, "
                " created_at, updated_at) "
                "VALUES (:id, :department_id, :position, :role, :metric_codes, "
                "        1, :note, :created_at, :updated_at)"
            ),
            {
                "id": f"pwp_{position}",
                "department_id": department_id,
                "position": position,
                "role": role,
                "metric_codes": metric_codes,
                "note": note,
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    # ⚠️ **先删数据再删表** —— SQLite 的 ``drop_table`` 不带外键级联
    op.get_bind().execute(sa.text("delete from pan_workload_policy"))
    op.drop_index("ix_pan_workload_policy_position", table_name="pan_workload_policy")
    op.drop_index(
        "ix_pan_workload_policy_department_id", table_name="pan_workload_policy"
    )
    op.drop_index("ix_pan_workload_policy_active", table_name="pan_workload_policy")
    op.drop_table("pan_workload_policy")
