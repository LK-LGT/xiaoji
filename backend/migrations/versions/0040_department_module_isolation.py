"""部门功能板块隔离（``department_module`` 表）。

Revision ID: 0040_department_module_isolation
Revises: 0039_bot_session_qa_state

背景
----
2026-11-21 用户投诉：

    「**还有为什么别的部门为什么会有剪辑板块？**
      这个没有做各部门功能板块隔离，我要的是对应的部门有对应的功能。」

根因
----
前端菜单（``frontend/src/navigation/workspaces.ts``）与路由守卫的过滤维度
**只有权限串** ⇒ 而 ``pan:workload:view`` 授予了基础 ``employee`` 角色
⇒ **全公司每个部门**的侧边栏都出现「剪辑工作量」入口
⇒ 仓储部 / 客服部的人点进去发现自己一条数据都没有。

⇒ **权限 ≠ 板块**
- **权限**回答「这个人能不能干这件事」（角色维度）
- **板块**回答「这个部门有没有这个业务」（组织维度）

本迁移做三件事
--------------
1. 建 ``department_module`` 表（部门 ↔ 功能板块开通关系）
2. 给**真实组织**里该有的部门开通 ``pan_workload``
3. 留下**显式未开通**的痕迹 —— 其它部门默认没有这个板块

⚠️ **为什么「默认关闭」**
没有配置行 = 不开通。与框架 ``DataSourcePolicy``「默认不信任」同一套哲学：
漏配导致「功能没出现」（可见、可修），而不是「功能到处乱出现」（不可见、不可修）。

⚠️ **为什么开通「短视频」而不是「运营中心」**
``/口播成品``、``/成品-小瑶``、``/成品-小雨`` 三个网盘目录
绑定的三个人（巫宜珊 / 佟瑶 / 李婵雨）**全部在短视频部**
⇒ 这个业务**就是短视频部的业务** ⇒ 只给短视频部开通。

⚠️ **但短视频部**挂**在运营中心**下**
⇒ 若只给短视频部开通，运营中心的老总（杨飞）看不到 ——
这是**有意的**：他不管剪辑这块，业务板块归口到短视频部。
⇒ 若将来运营中心也要，**再插一行**即可（改配置不发版）。

⚠️ **为什么不给「运营中心」开通**
「运营中心」是个**大口袋部门**（下面有商务部 / 直播部 / 短视频 / 运营部）
⇒ 给它开通等于把剪辑板块推给所有下级部门 ⇒ 回到老问题。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0040_department_module_isolation"
down_revision = "0039_bot_session_qa_state"
branch_labels = None
depends_on = None


#: **剪辑工作量**板块代码
PAN_WORKLOAD_MODULE = "pan_workload"

#: 短视频部（真实库里的 ID —— 2026-11-21 查 ``data/perf.db`` 得到）
#:
#: ⚠️ **按部门名查，不写死 ID**
#: 不同环境（测试库 / 新库）的部门 ID **不一样**（飞书同步生成的）
#: ⇒ 写死 ID 会导致「测试库有、真库没有」或反之。
#: ⇒ 这里按**部门名**查找，ID 由库里的实际行决定。
SHORT_VIDEO_DEPARTMENT_NAME = "短视频"

#: 开通说明（审计留痕）
NOTE = "2026-11-21 业务确认：剪辑工作量是短视频部的业务板块"


def upgrade() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    bind = op.get_bind()

    # ── ① 建表 ──
    op.create_table(
        "department_module",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("module_code", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"], ["department.id"], name="fk_department_module_department"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "module_code",
            name="uq_department_module_dept_code",
        ),
    )
    op.create_index(
        "ix_department_module_code",
        "department_module",
        ["module_code"],
        unique=False,
    )

    # ── ② 给短视频部开通「剪辑工作量」──
    #
    # ⚠️ **按部门名查 ID** —— 不同环境的部门 ID 不同。
    dept_ids = [
        str(row)
        for row in bind.execute(
            sa.text(
                "select id from department "
                "where name = :n and (feishu_deleted_at is null)"
            ),
            {"n": SHORT_VIDEO_DEPARTMENT_NAME},
        ).scalars().all()
    ]
    if not dept_ids:
        # ⚠️ 没有短视频部 ⇒ **不开通任何部门**。
        #    「默认关闭」比「开给错误的部门」安全得多。
        return

    module_table = sa.table(
        "department_module",
        sa.column("id", sa.String),
        sa.column("department_id", sa.String),
        sa.column("module_code", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("note", sa.String),
        sa.column("updated_by", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    for dept_id in dept_ids:
        exists = bind.execute(
            sa.text(
                "select count(*) from department_module "
                "where department_id = :d and module_code = :m"
            ),
            {"d": dept_id, "m": PAN_WORKLOAD_MODULE},
        ).scalar()
        if exists:
            continue
        bind.execute(
            module_table.insert().values(
                id=f"deptmod-{PAN_WORKLOAD_MODULE}-{dept_id}",
                department_id=dept_id,
                module_code=PAN_WORKLOAD_MODULE,
                active=True,
                note=NOTE,
                updated_by=None,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    # ⚠️ **先删数据再删表** —— SQLite 的 ``drop_table`` 不带外键级联
    op.get_bind().execute(sa.text("delete from department_module"))
    op.drop_index("ix_department_module_code", table_name="department_module")
    op.drop_table("department_module")
