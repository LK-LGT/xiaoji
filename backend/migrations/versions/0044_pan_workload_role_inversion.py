"""★ 剪辑工作量「角色倒转」的指标定义 + 岗位口径调整（2026-11-23）。

Revision ID: 0044_pan_workload_role_inversion
Revises: 0043_pan_workload_policy

背景
----
业务确认（2026-11-23）：

    **剪辑只填数量，主管填比率。**

    之前：剪辑填「完播率 / 视频留存率」（两个比率）
    现在：剪辑填「普通视频 xx 条 / OOTD xx 条 / 智能体 xx 条」+ 备注 + 截图
          **完播率 / 视频留存率 由主管填**

⚠️ **为什么这是「角色倒转」**
---------------------------
同一块业务里，**不同角色填不同的指标** ——
这正是「部门 × 岗位 口径表」（0043）存在的意义。

⚠️ **本次做两件事**
------------------
1. **建三条数量指标的 Metric 定义** ——
   没有定义就没有口径，挂不进算分集（「默认不信任」语义下不可读）。
2. **把 0043 预置的岗位口径改成角色对应的默认指标** ——
   剪辑（reporter）→ 三条数量；编导/运营（reviewer）→ 两个比率。

⚠️ **为什么数量与比率要分成不同的 metric_code**
----------------------------------------------
量纲不同（条 vs %）：
- 值域校验不同（比率 0~1，数量 0~10000 整数）；
- 月度聚合口径不同（比率取平均，数量求和）。

⇒ 合成一个 code 会让**两个环节都算错**。

⚠️ **为什么不能沿用 ``video_count``**
-------------------------------------
``video_count`` 是**网盘条数**（机器采集，权重已含「智能体/OOTD 算 2 条」）。
而这里三条数量是**剪辑手工报的分项条数** ——
两者口径不同（一个是成品数，一个是分项数）⇒ 分开记。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0044_pan_workload_role_inversion"
down_revision = "0043_pan_workload_policy"
branch_labels = None
depends_on = None


#: 三条数量指标：(code, 名称, 单位)
COUNT_METRICS = (
    ("video_normal_count", "普通视频条数", "条"),
    ("video_ootd_count", "OOTD 条数", "条"),
    ("video_agent_count", "智能体条数", "条"),
)

#: 两条比率指标：(code, 名称)
RATE_METRICS = (
    ("video_completion_rate", "完播率"),
    ("video_retention_rate", "视频留存率"),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "metric" not in inspector.get_table_names():
        # ⚠️ **表不存在（老库未迁移到位）** —— 不在这里建表。
        return

    now = datetime.now(UTC).replace(tzinfo=None)

    # ── ① 三条数量指标的 Metric 定义 ──────────────────────
    for code, name, unit in COUNT_METRICS:
        existing = bind.execute(
            sa.text("SELECT id FROM metric WHERE code = :code"), {"code": code}
        ).first()
        if existing is not None:
            # ⚠️ **已存在不覆盖** —— 人工配过的口径不能被迁移改掉
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO metric (
                    id, code, name, unit, calculation, data_source,
                    caliber, owner, is_attendance, active,
                    created_at, updated_at
                ) VALUES (
                    :id, :code, :name, :unit, :calculation, :data_source,
                    :caliber, :owner, 0, 1,
                    :now, :now
                )
                """
            ),
            {
                "id": f"M-{code}",
                "code": code,
                "name": name,
                "unit": unit,
                # ⚠️ **条数是「报多少就是多少」** —— 不做加权
                "calculation": "sum(value)",
                "data_source": "operations_manual",
                "caliber": "剪辑本人每日手工填报的分项条数",
                "owner": "短视频部",
                "now": now,
            },
        )

    # ── ② 岗位口径改成「角色对应的默认指标」 ──────────────
    # ⚠️ **为什么不写死 metric_codes**
    #    0043 里 ``metric_codes`` 留空 ⇒ 代码按**角色**取默认
    #    （见 ``pan_workload_policy.default_codes_for_role``）⇒
    #    ⇒ 这里**不需要**改数据，只需要保证口径行的 role 是对的。
    #
    #    ⚠️ **但要清掉可能残留的旧配置** ——
    #    如果有人在界面上把 剪辑 的 metric_codes 配成了两个比率，
    #    那就还是「剪辑填比率」⇒ 与业务确认冲突 ⇒ 这里纠正回留空。
    video = bind.execute(
        sa.text("SELECT id FROM department WHERE name = '短视频' LIMIT 1")
    ).first()
    if video is None:
        return
    department_id = video[0]

    bind.execute(
        sa.text(
            "UPDATE pan_workload_policy SET metric_codes = '' "
            "WHERE department_id = :dept AND position = '剪辑' "
            "AND metric_codes LIKE '%video_completion_rate%'"
        ),
        {"dept": department_id},
    )
    # ⚠️ **审阅人（主管）同理** —— 主管填的是两个比率
    bind.execute(
        sa.text(
            "UPDATE pan_workload_policy SET metric_codes = '' "
            "WHERE department_id = :dept AND role = 'reviewer' "
            "AND metric_codes LIKE '%video_normal_count%'"
        ),
        {"dept": department_id},
    )


def downgrade() -> None:
    op.execute(
        "delete from metric where code in "
        "('video_normal_count','video_ootd_count','video_agent_count')"
    )
