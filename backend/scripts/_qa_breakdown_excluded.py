"""验证「缺数据指标」场景下 AI 的解释是否正确。

⚠️ **为什么这个场景最重要**
----------------------------
系统规则：某指标**缺数据** → **不参与算分** → 其余指标权重**重新归一**。

这是最容易讲错的地方：
- ❌ 说成「某指标缺数据，扣了 X 分」 —— **完全错误**
- ❌ 说成「某指标缺数据，总分变低了」 —— **完全错误**
- ✅ 说成「某指标缺数据未参与算分，其余指标权重重新归一」 —— **正确**

⚠️ 「不扣分」是系统刻意设计的（决策③）：
   缺数据 / 缺配置 = 业务低绩效？这说不通。
   但如果不解释清楚，用户一定会理解成「被扣分了」。

用法::

    python backend/scripts/_qa_breakdown_excluded.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0

#: 这些词出现就说明 AI 说错了（缺数据 ≠ 扣分）
WRONG_WORDS = ("扣了", "扣分", "减少了", "导致总分降低", "因此扣掉", "被扣")


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def main() -> int:
    print("=" * 70)
    print("缺数据场景的解释正确性验证")
    print("=" * 70)

    # 用**内存库**造一个「两指标、其一缺数据」的场景，
    # 避免动真实数据。
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from backend.app.domain.models import (
        AssessmentRecord,
        AssessmentTask,
        Base,
        ConfigSnapshot,
        Department,
        Employee,
        MetricValue,
    )
    from backend.app.services import agent_tool_service

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Department(id="D", name="部门"))
        db.flush()
        db.add(Employee(id="E", name="员工", department_id="D", employment_status="active", account_status="enabled"))
        db.add(AssessmentTask(id="T", name="任务", period="202611"))
        db.flush()
        db.add(ConfigSnapshot(
            id="S", task_id="T",
            metrics_json=[
                {"code": "GMV", "name": "销售额"},
                {"code": "NEW", "name": "新客数"},
            ],
            weights_json=[
                {"metric_code": "GMV", "weight": 60},
                {"metric_code": "NEW", "weight": 40},
            ],
            rubric_json=[
                {"metric_code": "GMV", "levels": [{"level": "A", "min_ratio": 1, "max_ratio": None}]},
                {"metric_code": "NEW", "levels": [{"level": "A", "min_ratio": 1, "max_ratio": None}]},
            ],
        ))
        db.flush()
        db.get(AssessmentTask, "T").snapshot_id = "S"
        db.add(AssessmentRecord(id="R", task_id="T", employee_id="E"))
        # ⚠️ 只有 GMV 有数据 —— NEW 缺数据
        db.add(MetricValue(employee_id="E", metric_code="GMV", period="202611", value=120, target=100, source="manual"))
        db.commit()

        task = db.get(AssessmentTask, "T")
        breakdown = agent_tool_service.load_scoring_breakdown(db, employee_id="E", task=task)

    print("\n[1] 工具返回的构成")
    print(json.dumps(breakdown, ensure_ascii=False, indent=2))

    print("\n[2] 关键断言")
    check("排除项包含 NEW", any(i["metric_code"] == "NEW" for i in breakdown["excluded_items"]))
    check("GMV 权重归一为 1.0",
          breakdown["items"][0]["normalized_weight"] == 1.0,
          str(breakdown["items"][0]["normalized_weight"]))
    check("总权重仍是 100", breakdown["total_weight"] == 100)
    check("参与权重是 60", breakdown["participating_weight"] == 60)
    check("note 说明了不扣分", "不参与算分" in breakdown["note"], breakdown["note"])
    check("note 说明了重新归一", "重新归一" in breakdown["note"], breakdown["note"])
    check("总分为 100（单指标满级）", breakdown["total_score"] == 100.0, str(breakdown["total_score"]))

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
