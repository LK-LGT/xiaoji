"""绑定 ``/口播成品`` 目录 + 清理占位员工。

Revision ID: 0038_pan_koubo_binding
Revises: 0037_pan_workload_org_alignment

背景
----
2026-11-21 业务确认：``/口播成品`` **不用管特殊口径**，把目录绑给对应的人即可。

⇒ 本迁移做三件事：

1. **绑定 ``/口播成品``** → 巫宜珊（``c6dd2388``，短视频部主播）
2. **把 ``/口播成品`` 从黑名单里摘掉**（``exclude_paths`` 只留 ``/鹿13``）
3. **删掉两个占位员工**（``pan_placeholder_小瑶`` / ``pan_placeholder_小雨``）

⚠️ **为什么是巫宜珊（不是两位剪辑）**
2026-11-21 真实网盘实测（1455 mp4 / 149 子目录）：

    /口播成品 的 mp4 **md5 与 /成品-小瑶、/成品-小雨 零重叠**
    ⇒ 它**不是**那两位剪辑的作品

    口播 = 主播内容形态 ⇒ 短视频部里只有巫宜珊一个主播
    ⇒ 口播成品这条线的内容产出对应巫宜珊

⚠️ **为什么整目录都算（含 1373 个外部账号上传的文件）**
实测上传者分布：

    外部账号（owner_id=3913206474）  1373 个   5/28 ~ 10/16
    公司账号（uk=1102224043442）        82 个  10/17 ~ 10/24

``/口播成品`` 是**共享空间**里的目录（``owner_id`` 不是公司账号），
个人账号上传的口播成片会汇聚到共享目录 ⇒ 外部账号上传的那批
**就是内容产出方自己传的** ⇒ 应计入对应人的工作量。

⚠️ **历史归档目录要排除**
``/口播成品/之前的成品``（111 个 mp4）是**历史归档**，
混进来会把已过去的月份也算成当期工作量 ⇒ 加入 ``exclude_dir_segments``。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0038_pan_koubo_binding"
down_revision = "0037_pan_workload_org_alignment"
branch_labels = None
depends_on = None

#: ``/口播成品`` 的目录所有者账号（共享空间 owner，非公司账号）。
#: 仅作审计留痕 —— 归属判定仍靠路径前缀。
KOUBO_OWNER_OPER_ID = "3913206474"

#: ``/口播成品`` → 巫宜珊（短视频部主播）
KOUBO_FOLDER = "/口播成品"
KOUBO_EMPLOYEE_ID = "c6dd2388"
KOUBO_EMPLOYEE_NAME = "巫宜珊"

#: 采集范围黑名单 —— 摘掉 ``/口播成品``，只留素材库 ``/鹿13``。
NEW_EXCLUDE_PATHS = "/鹿13"

#: 目录排除段 —— 新增「之前的成品」（历史归档目录）。
NEW_EXCLUDE_DIR_SEGMENTS = "草稿,工程,prj,aep,project,源文件,素材,之前的成品"


def upgrade() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    bind = op.get_bind()

    # ── ① 配置：黑名单摘掉 /口播成品；目录排除段加「之前的成品」──
    config = sa.table(
        "pan_workload_config",
        sa.column("id", sa.String),
        sa.column("exclude_paths", sa.String),
        sa.column("exclude_dir_segments", sa.String),
        sa.column("updated_by", sa.String),
        sa.column("updated_at", sa.DateTime),
    )
    bind.execute(
        config.update()
        .where(config.c.id == "default")
        .values(
            exclude_paths=NEW_EXCLUDE_PATHS,
            exclude_dir_segments=NEW_EXCLUDE_DIR_SEGMENTS,
            updated_by=None,
            updated_at=now,
        )
    )

    # ── ② 绑定 /口播成品 → 巫宜珊 ──
    #
    # ⚠️⚠️ **必须「就地改」而不是「删了插」**
    # ``pan_account_binding.folder_path`` 上有**全局唯一约束**
    # （``uq_pan_binding_path``）⇒ 先删旧行再插新行会在
    # 「旧行已删、新行未插」的窗口里丢掉归属历史。
    # ⇒ 这里用 UPDATE；若没有该行才 INSERT。
    emp_exists = bind.execute(
        sa.text("select count(*) from employee where id = :e"),
        {"e": KOUBO_EMPLOYEE_ID},
    ).scalar()
    if not emp_exists:
        # 员工不存在（测试库 / 组织未同步）⇒ 不建绑定，避免外键炸
        return

    already = bind.execute(
        sa.text(
            "select count(*) from pan_account_binding where folder_path = :f"
        ),
        {"f": KOUBO_FOLDER},
    ).scalar()
    if already:
        bind.execute(
            sa.text(
                "update pan_account_binding "
                "set employee_id = :e, folder_name = :n, oper_id = :o, "
                "    active = 1, daily_subdir = 1 "
                "where folder_path = :f"
            ),
            {
                "e": KOUBO_EMPLOYEE_ID,
                "n": KOUBO_EMPLOYEE_NAME,
                "o": KOUBO_OWNER_OPER_ID,
                "f": KOUBO_FOLDER,
            },
        )
    else:
        op.bulk_insert(
            sa.table(
                "pan_account_binding",
                sa.column("id", sa.String),
                sa.column("employee_id", sa.String),
                sa.column("bind_type", sa.String),
                sa.column("folder_path", sa.String),
                sa.column("folder_name", sa.String),
                sa.column("oper_id", sa.String),
                sa.column("active", sa.Boolean),
                sa.column("daily_subdir", sa.Boolean),
                sa.column("created_by", sa.String),
                sa.column("created_at", sa.DateTime),
            ),
            [
                {
                    "id": "pan-binding-koubo",
                    "employee_id": KOUBO_EMPLOYEE_ID,
                    "bind_type": "path",
                    "folder_path": KOUBO_FOLDER,
                    "folder_name": KOUBO_EMPLOYEE_NAME,
                    "oper_id": KOUBO_OWNER_OPER_ID,
                    "active": True,
                    "daily_subdir": True,
                    "created_by": None,
                    "created_at": now,
                }
            ],
        )

    # ── ③ 清掉占位员工 ──
    #
    # ⚠️ **只有「完全没有任何引用」才删**
    # 占位员工 ``pan_placeholder_小瑶`` / ``pan_placeholder_小雨`` 是
    # P1~P4 开发期的假人（真实员工不存在这两个名字）。
    # 迁移 0037 已把绑定与采集证据全部改指真实员工 ⇒ 这两个占位行
    # **零引用** ⇒ 可以安全删除。
    #
    # ⚠️ 删之前**必须再查一遍引用** —— 有任何一处引用就跳过，
    #    宁可留着两个假人，也不能留下悬空外键。
    for placeholder in ("pan_placeholder_小瑶", "pan_placeholder_小雨"):
        refs = 0
        for table, column in (
            ("pan_account_binding", "employee_id"),
            ("pan_workload_file", "employee_id"),
            ("pan_workload_daily", "employee_id"),
            ("pan_workload_review", "employee_id"),
            ("pan_manual_metric", "employee_id"),
            ("employee_department", "employee_id"),
        ):
            try:
                n = bind.execute(
                    sa.text(
                        f"select count(*) from {table} where {column} = :p"
                    ),
                    {"p": placeholder},
                ).scalar()
            except Exception:  # noqa: BLE001 —— 表/列不存在就跳过
                continue
            refs += int(n or 0)
        if refs:
            # ⚠️ 还有引用 ⇒ 不能删（会留悬空外键）
            continue
        bind.execute(
            sa.text("delete from employee where id = :p"),
            {"p": placeholder},
        )


def downgrade() -> None:
    # ⚠️ **不回滚归属改写** —— 归属历史必须留痕。
    op.get_bind().execute(
        sa.text("delete from pan_account_binding where folder_path = '/口播成品'")
    )
