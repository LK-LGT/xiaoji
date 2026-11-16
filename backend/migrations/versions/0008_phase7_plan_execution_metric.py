"""A7: 计划执行率指标定义（数据迁移）

Revision ID: 0008_phase7_plan_execution_metric
Revises: 0007_phase8_feishu_binding
Create Date: 2026-11-16

播种「计划执行率」指标定义。

背景
----
A7 定义了 ``plan_execution_rate`` 指标 —— 员工月度计划执行率，
按计划条目的绩效权重加权计算，审批通过时写入 ``MetricValue``（算分集）。

但**指标定义本身**（``metric`` 表的一行）也需要存在于数据库里，
否则：

- 规则引擎的权重集 / rubric 引用 ``metric_code = "plan_execution_rate"``
  时找不到对应定义；
- 「指标口径说明」（``caliber``）无处可查 —— 主管看到「计划执行率 82%」
  时需要知道这是怎么算的。

⚠️ 为什么用**数据迁移**而不是只在代码里懒创建
------------------------------------------------
``services.plan_execution_metric.ensure_metric_definition()`` 提供了幂等的
懒创建（应用启动 / 首次调用时补齐），但那只覆盖**代码跑过的地方**。
生产库的 ``metric`` 表应该由迁移来保证 —— 这条链路要和 Alembic 的
「schema 归迁移管」原则保持一致。

设计要点
--------
- **幂等**：用 ``code`` 判重，已存在则跳过（迁移可能被重放）。
- **不覆盖**：若已存在同 ``code`` 的定义，**保留原有行** ——
  运维可能已经手工改过口径 / 责任人，迁移不该覆盖人工配置。
- **口径字段非空**：``calculation`` / ``data_source`` 是决策③的读入校验项，
  必须非空。
- **不是考勤类**：``is_attendance = False`` —— 计划执行率是真业绩，
  要进算分集（与考勤的 ``reference_only`` 相反）。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_phase7_plan_execution_metric"
down_revision: str | Sequence[str] | None = "0007_phase8_feishu_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 指标编码（与 `services.plan_execution_metric.PLAN_EXECUTION_RATE_CODE` 一致）
METRIC_CODE = "plan_execution_rate"

#: 指标定义 ID
METRIC_ID = "METRIC_plan_execution_rate"


def upgrade() -> None:
    """播种「计划执行率」指标定义。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "metric" not in inspector.get_table_names():
        # 表不存在（老库未迁移到位）—— 不在这里建表，避免与基线迁移冲突
        return

    existing = bind.execute(
        sa.text("SELECT id FROM metric WHERE code = :code"), {"code": METRIC_CODE}
    ).first()
    if existing is not None:
        # 已存在（可能是懒创建补上的，也可能是人工配的）—— 不覆盖
        return

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
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        ),
        {
            "id": METRIC_ID,
            "code": METRIC_CODE,
            "name": "计划执行率",
            "unit": "%",
            "calculation": (
                "sum(result.completion_rate * item.performance_weight) "
                "/ sum(item.performance_weight)"
            ),
            "data_source": "plan_result",
            "caliber": (
                "按计划条目的绩效权重加权计算月度计划执行率；"
                "只统计已审批通过的成果，权重全为 0 时退化为简单平均。"
            ),
            "owner": "计划管理",
        },
    )


def downgrade() -> None:
    """删除「计划执行率」指标定义。

    ⚠️ 只删**本迁移播种的那一行**（按固定 ID + code 双重匹配）——
    若是运维手工改过的，不应该被回滚抹掉。
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "metric" not in inspector.get_table_names():
        return

    bind.execute(
        sa.text(
            "DELETE FROM metric WHERE id = :id AND code = :code"
        ),
        {"id": METRIC_ID, "code": METRIC_CODE},
    )
