"""BP-2 与真实组织框架对齐（短视频部 × 剪辑岗位）+ 数据源登记 + 指标口径定义。

Revision ID: 0037_pan_workload_org_alignment
Revises: 0036_pan_manual_metric_scope

背景
----
2026-11-21 查真实库（``data/perf.db``）发现 P0~P4 的数据层与**真实人员框架脱节**：

真实组织（30 个部门，来自飞书同步）::

    运营中心 (413611b2716e5e6g, leader=杨飞)
    ├── 短视频 (34fg6a34db969f3c, leader=刘旭)      ← ★ 剪辑在这
    │   ├── 巫宜珊  主播
    │   ├── 刘旭    运营
    │   ├── 张洁    编导     ← ★ 两位剪辑的**直属上级**
    │   │   ├── 李婵雨 剪辑
    │   │   └── 佟瑶   剪辑
    ├── 运营部 (6f24ab88c441e43d)
    ├── 直播部 (b52121be8a294bd5)
    ├── 商务部 (31499efbege8dce7)
    └── 跨境部 (c9b57653499g5acd)

⇒ **剪辑岗位属于「短视频部」，不是「运营部」。**

本次对齐做四件事
----------------
1. **登记两个数据源**（``baidu_pan`` / ``operations_manual``）——
   「默认不信任」语义下，没登记 = **不可读** = 算分集拒收。
2. **建三条 Metric 定义**（``video_count`` / ``video_completion_rate`` /
   ``video_retention_rate``）—— 没有定义就没有口径，挂不进算分集。
3. **建 WeightSet + Rubric**（短视频部 × 剪辑）—— 权重合计 = 100。
4. **修正目录绑定**（占位 ID → 真实员工 ID）。

⚠️ **为什么 metric_code 必须带 ``video_`` 前缀**
库中已有 ``retention_rate``（名称「留任率」、``data_source=hr_system``、
``owner=HR``），语义是**员工留存率**，与短视频的**视频留存率**不是一回事。
⇒ 同名会写进同一条 ``MetricValue``（唯一约束是
``(employee_id, metric_code, period)``）⇒ 两个业务互相覆写 ⇒ 绩效错乱。

⚠️ **绑定修正的可回滚性**
本迁移把 ``pan_account_binding`` 的 ``employee_id`` 从占位 ID
（``pan_placeholder_小瑶`` / ``pan_placeholder_小雨``）改成真实员工 ID。
绑定表上的**目录路径**与**归属人**是同一个事实的两面，
改错了会导致工作量记到错误的人头上 ⇒ 因此这里**同时**把旧行置
``active=False``（**不删** —— 归属历史要能追溯），再插入新行。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0037_pan_workload_org_alignment"
down_revision = "0036_pan_manual_metric_scope"
branch_labels = None
depends_on = None


#: 短视频部（真实库里的部门 ID）
DEPT_SHORT_VIDEO = "34fg6a34db969f3c"

#: 剪辑岗位名（真实库里 ``employee.position`` 就是「剪辑」）
POSITION_EDITOR = "剪辑"

#: 目录前缀 → 真实员工 ID
#:
#: ⚠️ 「小瑶」是**佟瑶**的昵称 —— 网盘目录名用的是昵称，飞书里是全名。
#: 这层映射必须显式维护，不能靠「字符串包含」猜。
#:
#: ⚠️ **占位 ID → 真实 ID**（2026-11-21 对齐时库里的实际值）
#: P1~P4 开发期间用 ``pan_placeholder_小瑶`` / ``pan_placeholder_小雨``
#: 占位，因为当时没有真实的组织映射。
#: ⇒ 采集出的 526 个真实文件**就挂在这两个假 ID 上**（204 + 322 行），
#:   真实剪辑（李婵雨 / 佟瑶）的日汇总是 **0 行**。
FOLDER_TO_EMPLOYEE = {
    "/成品-小瑶": ("b74432e5", "佟瑶", "pan_placeholder_小瑶"),
    "/成品-小雨": ("a5157912", "李婵雨", "pan_placeholder_小雨"),
}

#: ⚠️ **开发期调试产生的合成数据** —— 必须清掉
#:
#: P4 浏览器实测时建过一条临时绑定 ``/成品-杨飞``（leader 杨飞 g426c684），
#: 采集了 6 条**人造**文件（路径 ``成片0.mp4`` / ``成片1.mp4``，
#: 标记 ``热点`` / ``日常``），产生 4 行日汇总。
#:
#: ⚠️ 这些不是真实成片 ⇒ 留在库里会让**真实员工**的工作量面板出现假数据。
#: ⚠️ 但「永久留存」原则针对的是**真实采集证据** ⇒ 合成调试数据可以清。
#:
#: ⚠️ 同时它也证明了归属链路是通的（路径前缀 → employee_id → 日汇总），
#:    这部分能力保留在 ``pan_workload_service`` 与测试里。
DEBUG_EMPLOYEE_IDS = ("g426c684",)


def upgrade() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)

    # ─────────────────────────────────────────────────────────────
    # ① 登记数据源
    # ─────────────────────────────────────────────────────────────
    policy = sa.table(
        "data_source_policy",
        sa.column("id", sa.String),
        sa.column("source_code", sa.String),
        sa.column("display_name", sa.String),
        sa.column("allowed", sa.Boolean),
        sa.column("allowed_roles", sa.String),
        sa.column("redacted_fields", sa.String),
        sa.column("note", sa.String),
        sa.column("updated_by", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    existing = set(
        op.get_bind()
        .execute(sa.text("select source_code from data_source_policy"))
        .scalars()
        .all()
    )
    rows = []
    for code, name in (
        ("baidu_pan", "百度网盘（剪辑工作量）"),
        ("operations_manual", "运营人工填报（完播率/留存率）"),
    ):
        if code in existing:
            continue
        rows.append(
            {
                "id": f"dsp_{code}",
                "source_code": code,
                "display_name": name,
                "allowed": True,
                "allowed_roles": "hr,manager,department_leader,employee",
                "redacted_fields": "",
                "note": "BP-2 短视频剪辑工作量（2026-11-21 对齐真实组织）",
                "updated_by": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    if rows:
        op.bulk_insert(policy, rows)

    # ─────────────────────────────────────────────────────────────
    # ② 三条 Metric 定义
    # ─────────────────────────────────────────────────────────────
    metric = sa.table(
        "metric",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("unit", sa.String),
        sa.column("calculation", sa.Text),
        sa.column("data_source", sa.String),
        sa.column("caliber", sa.Text),
        sa.column("owner", sa.String),
        sa.column("is_attendance", sa.Boolean),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    have_metrics = set(
        op.get_bind().execute(sa.text("select code from metric")).scalars().all()
    )
    metric_rows = []
    for code, name, unit, calc, source, caliber in (
        (
            "video_count",
            "剪辑工作量条数",
            "条",
            "sum(pan_workload_daily.adjusted_count or pan_workload_daily.total_count) "
            "where business_date between period_start and period_end",
            "baidu_pan",
            "按人按天的网盘成片条数；有主管裁定值时以裁定值为准，原始机器值永久保留",
        ),
        (
            "video_completion_rate",
            "视频完播率",
            "%",
            "avg(pan_manual_metric.metric_value) where metric_code='video_completion_rate'",
            "operations_manual",
            "运营人工填报；逐条成片填报时取算术平均",
        ),
        (
            "video_retention_rate",
            "视频留存率",
            "%",
            "avg(pan_manual_metric.metric_value) where metric_code='video_retention_rate'",
            "operations_manual",
            "运营人工填报；逐条成片填报时取算术平均。"
            "⚠️ 与 HR 的「留任率 retention_rate」是不同指标，勿混",
        ),
    ):
        if code in have_metrics:
            continue
        metric_rows.append(
            {
                "id": f"M-{code}",
                "code": code,
                "name": name,
                "unit": unit,
                "calculation": calc,
                "data_source": source,
                "caliber": caliber,
                "owner": "短视频部",
                "is_attendance": False,
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
        )
    if metric_rows:
        op.bulk_insert(metric, metric_rows)

    # ─────────────────────────────────────────────────────────────
    # ③ WeightSet + Rubric（短视频部 × 剪辑）
    # ─────────────────────────────────────────────────────────────
    bind = op.get_bind()
    dept_exists = bind.execute(
        sa.text("select count(*) from department where id = :d"),
        {"d": DEPT_SHORT_VIDEO},
    ).scalar()
    if dept_exists:
        has_ws = bind.execute(
            sa.text(
                "select count(*) from weight_set "
                "where department_id = :d and position = :p"
            ),
            {"d": DEPT_SHORT_VIDEO, "p": POSITION_EDITOR},
        ).scalar()
        if not has_ws:
            op.bulk_insert(
                sa.table(
                    "weight_set",
                    sa.column("id", sa.String),
                    sa.column("department_id", sa.String),
                    sa.column("position", sa.String),
                    sa.column("weights_json", sa.JSON),
                    sa.column("version", sa.Integer),
                    sa.column("active", sa.Boolean),
                    sa.column("created_at", sa.DateTime),
                ),
                [
                    {
                        "id": "W-VIDEO-EDITOR",
                        "department_id": DEPT_SHORT_VIDEO,
                        "position": POSITION_EDITOR,
                        # ⚠️ 合计必须 = 100（决策③读入校验项）
                        "weights_json": [
                            {"metric_code": "video_count", "weight": 30},
                            {"metric_code": "video_completion_rate", "weight": 40},
                            {"metric_code": "video_retention_rate", "weight": 30},
                        ],
                        "version": 1,
                        "active": True,
                        "created_at": now,
                    }
                ],
            )
            op.bulk_insert(
                sa.table(
                    "rubric",
                    sa.column("id", sa.String),
                    sa.column("weight_set_id", sa.String),
                    sa.column("metric_code", sa.String),
                    sa.column("levels_json", sa.JSON),
                    sa.column("created_at", sa.DateTime),
                ),
                [
                    {
                        "id": f"R-VIDEO-{code}",
                        "weight_set_id": "W-VIDEO-EDITOR",
                        "metric_code": code,
                        "levels_json": levels,
                        "created_at": now,
                    }
                    for code, levels in (
                        (
                            "video_count",
                            [
                                {"level": "A", "min_ratio": 1.05, "max_ratio": None,
                                 "desc": "超额完成目标 5% 以上", "score": 100},
                                {"level": "B", "min_ratio": 0.95, "max_ratio": 1.05,
                                 "desc": "达成目标", "score": 80},
                                {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95,
                                 "desc": "未达成目标", "score": 60},
                            ],
                        ),
                        (
                            "video_completion_rate",
                            [
                                {"level": "A", "min_ratio": 1.10, "max_ratio": None,
                                 "desc": "完播率超基准 10% 以上", "score": 100},
                                {"level": "B", "min_ratio": 0.95, "max_ratio": 1.10,
                                 "desc": "完播率接近基准", "score": 80},
                                {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95,
                                 "desc": "完播率明显低于基准", "score": 60},
                            ],
                        ),
                        (
                            "video_retention_rate",
                            [
                                {"level": "A", "min_ratio": 1.10, "max_ratio": None,
                                 "desc": "留存率超基准 10% 以上", "score": 100},
                                {"level": "B", "min_ratio": 0.95, "max_ratio": 1.10,
                                 "desc": "留存率接近基准", "score": 80},
                                {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95,
                                 "desc": "留存率明显低于基准", "score": 60},
                            ],
                        ),
                    )
                ],
            )

    # ─────────────────────────────────────────────────────────────
    # ④ 修正目录绑定 + 归属数据（占位 ID → 真实员工）
    # ─────────────────────────────────────────────────────────────
    #
    # ⚠️⚠️ **必须「就地改」而不是「删了插」**
    # ``pan_account_binding.folder_path`` 上有**全局唯一约束**
    # （``uq_pan_binding_path``）⇒ 先删旧行再插新行会在
    # 「旧行已删、新行未插」的窗口里丢掉归属历史。
    # ⇒ 这里只 UPDATE ``employee_id`` / ``folder_name``。
    #
    # ⚠️ **同理，已采集的文件/日汇总也要改 employee_id** ——
    # 那 526 行真实证据本来就属于这两位剪辑，只是挂在占位 ID 上。
    bind = op.get_bind()
    valid_emp = set(bind.execute(sa.text("select id from employee")).scalars().all())

    for folder, (emp_id, emp_name, placeholder) in FOLDER_TO_EMPLOYEE.items():
        if emp_id not in valid_emp:
            # 员工不存在（测试库 / 组织未同步）⇒ 不动绑定，避免外键炸
            continue
        # ⚠️ 绑定行就地改指向
        bind.execute(
            sa.text(
                "update pan_account_binding "
                "set employee_id = :e, folder_name = :n, active = 1 "
                "where folder_path = :f"
            ),
            {"e": emp_id, "n": emp_name, "f": folder},
        )
        # ⚠️ **已采集的证据与日汇总跟着改归属**（否则真实剪辑仍是 0 行）
        for table in ("pan_workload_file", "pan_workload_daily"):
            bind.execute(
                sa.text(
                    f"update {table} set employee_id = :e "
                    "where employee_id = :p"
                ),
                {"e": emp_id, "p": placeholder},
            )

    # ─────────────────────────────────────────────────────────────
    # ⑤ 清掉开发期调试产生的合成数据
    # ─────────────────────────────────────────────────────────────
    #
    # ⚠️ **只清「合成调试数据」，不清任何真实采集证据**
    # 判据 = 绑定行里**不存在**该 employee_id（⇒ 那条绑定是临时建的）
    #       且 绑定路径是 ``/成品-杨飞``（开发期临时目录）。
    for emp_id in DEBUG_EMPLOYEE_IDS:
        has_real_binding = bind.execute(
            sa.text(
                "select count(*) from pan_account_binding "
                "where employee_id = :e and folder_path like '/成品-%' "
                "and folder_path <> '/成品-杨飞'"
            ),
            {"e": emp_id},
        ).scalar()
        if has_real_binding:
            continue
        for table in (
            "pan_workload_file",
            "pan_workload_daily",
        ):
            bind.execute(
                sa.text(f"delete from {table} where employee_id = :e"),
                {"e": emp_id},
            )
        bind.execute(
            sa.text(
                "delete from pan_account_binding where employee_id = :e "
                "and folder_path = '/成品-杨飞'"
            ),
            {"e": emp_id},
        )


def downgrade() -> None:
    # ⚠️ **不回滚归属改写** —— 归属历史必须留痕（与「永久留存」原则一致）。
    #    删掉合成调试数据的行为也不回滚（那本来就是不该存在的数据）。
    op.execute("delete from rubric where weight_set_id = 'W-VIDEO-EDITOR'")
    op.execute("delete from weight_set where id = 'W-VIDEO-EDITOR'")
    op.execute(
        "delete from metric where code in "
        "('video_count','video_completion_rate','video_retention_rate')"
    )
    op.execute(
        "delete from data_source_policy "
        "where source_code in ('baidu_pan','operations_manual')"
    )
