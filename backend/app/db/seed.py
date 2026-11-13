"""种子数据 —— 让框架能独立跑起来。

对应决策⑤的推荐试点部门（运营部）：指标清楚、数据完整。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import (
    Department,
    Employee,
    FormTemplate,
    Metric,
    Rubric,
    WeightSet,
)

GMV_LEVELS = [
    {"level": "A", "min_ratio": 1.05, "max_ratio": None, "desc": "超额完成目标 5% 以上", "score": 100},
    {"level": "B", "min_ratio": 0.95, "max_ratio": 1.05, "desc": "达成目标", "score": 80},
    {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95, "desc": "未达成目标", "score": 60},
]

CONVERSION_LEVELS = [
    {"level": "A", "min_ratio": 1.10, "max_ratio": None, "desc": "转化率超基准 10% 以上", "score": 100},
    {"level": "B", "min_ratio": 0.95, "max_ratio": 1.10, "desc": "转化率接近基准", "score": 80},
    {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95, "desc": "转化率明显低于基准", "score": 60},
]

REFUND_LEVELS = [
    {"level": "A", "min_ratio": 0.0, "max_ratio": 0.80, "desc": "退款率控制在基准 80% 以内", "score": 100},
    {"level": "B", "min_ratio": 0.80, "max_ratio": 1.0, "desc": "退款率与基准持平", "score": 80},
    {"level": "C", "min_ratio": 1.0, "max_ratio": None, "desc": "退款率高于基准", "score": 60},
]


def seed(db: Session) -> None:
    """幂等种子。已存在则跳过。"""
    if db.execute(select(Department)).scalars().first() is not None:
        return

    # --- 组织 ---
    ops = Department(id="D-OPS", name="运营部")
    cs = Department(id="D-CS", name="客服部")
    db.add_all([ops, cs])
    db.flush()

    mgr = Employee(
        id="E-MGR-001",
        name="张运营",
        department_id="D-OPS",
        position="运营负责人",
        employment_status="active",
    )
    db.add(mgr)
    db.flush()

    emp1 = Employee(
        id="E-001",
        name="李晓",
        email="li@example.com",
        department_id="D-OPS",
        position="运营专员",
        manager_id="E-MGR-001",
        employment_status="active",
    )
    emp2 = Employee(
        id="E-002",
        name="王强",
        email="wang@example.com",
        department_id="D-OPS",
        position="运营专员",
        manager_id="E-MGR-001",
        employment_status="active",
    )
    db.add_all([emp1, emp2])
    db.flush()

    # --- 指标定义 ---
    db.add_all(
        [
            Metric(
                id="M-GMV",
                code="gmv",
                name="GMV",
                unit="元",
                calculation="sum(order.amount) where order.paid_at in period",
                data_source="api:ecommerce",
                caliber="统计周期内已支付订单金额合计，退款订单不计入",
                owner="运营部",
            ),
            Metric(
                id="M-CONV",
                code="conversion_rate",
                name="转化率",
                unit="%",
                calculation="paid_orders / visitors * 100",
                data_source="api:ecommerce",
                caliber="访客到支付订单的转化率，按日加权平均",
                owner="运营部",
            ),
            Metric(
                id="M-REFUND",
                code="refund_rate",
                name="退款率",
                unit="%",
                calculation="refund_orders / paid_orders * 100",
                data_source="api:ecommerce",
                caliber="退款金额 / 支付金额，按周期累计",
                owner="仓储部",
            ),
        ]
    )
    db.flush()

    # --- 权重集（运营部 × 运营专员）---
    ws = WeightSet(
        id="W-OPS-SPEC",
        department_id="D-OPS",
        position="运营专员",
        weights_json=[
            {"metric_code": "gmv", "weight": 50},
            {"metric_code": "conversion_rate", "weight": 30},
            {"metric_code": "refund_rate", "weight": 20},
        ],
        version=1,
    )
    db.add(ws)
    db.flush()

    db.add_all(
        [
            Rubric(id="R-GMV", weight_set_id="W-OPS-SPEC", metric_code="gmv", levels_json=GMV_LEVELS),
            Rubric(
                id="R-CONV",
                weight_set_id="W-OPS-SPEC",
                metric_code="conversion_rate",
                levels_json=CONVERSION_LEVELS,
            ),
            Rubric(
                id="R-REFUND",
                weight_set_id="W-OPS-SPEC",
                metric_code="refund_rate",
                levels_json=REFUND_LEVELS,
            ),
        ]
    )

    # --- 表单定义（一份定义双端渲染）---
    db.add(
        FormTemplate(
            id="F-OPS-SPEC",
            department_id="D-OPS",
            position="运营专员",
            name="运营专员月度自评表",
            fields_json=[
                {
                    "key": "key_work",
                    "label": "本月重点工作",
                    "type": "long_text",
                    "required": True,
                    "explanation": "写 2-3 件本月最重要的事，每件带上具体结果",
                },
                {
                    "key": "achievement",
                    "label": "成果说明",
                    "type": "long_text",
                    "required": True,
                    "explanation": "用「做了什么 → 带来什么结果」描述，尽量带数字",
                },
                {
                    "key": "gmv_note",
                    "label": "GMV 相关说明",
                    "type": "long_text",
                    "required": False,
                    "metric_code": "gmv",
                    "explanation": "GMV 未达标请说明原因与改进动作",
                },
                {
                    "key": "screenshots",
                    "label": "后台截图",
                    "type": "attachment",
                    "required": False,
                    "explanation": "上传后台数据截图作为证据",
                },
                {
                    "key": "support_needed",
                    "label": "困难与支持需求",
                    "type": "long_text",
                    "required": False,
                },
                {
                    "key": "next_month",
                    "label": "下月计划",
                    "type": "long_text",
                    "required": False,
                },
            ],
            version=1,
        )
    )
    db.flush()
