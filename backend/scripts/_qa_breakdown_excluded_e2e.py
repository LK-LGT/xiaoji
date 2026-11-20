"""评分构成工具的**边界场景**验证：有指标未参与算分。

⚠️ **为什么这个场景最危险**
--------------------------
当某指标缺数据时，它**不参与算分**，其余指标权重**重新归一**。

⇒ 表面上总分没变（甚至可能变高），
   但用户如果问「我为什么少了分」，模型如果不知道这件事，
   就可能：
     - 说成「某项被扣了 X 分」（**事实错误**）
     - 或者答非所问

这个脚本构造一个「3 项指标、1 项缺数据」的考核任务，
然后问模型「为什么感觉分数不对」，看它会不会**主动说清**这件事。

用法::

    python backend/scripts/_qa_breakdown_excluded_e2e.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def build_scenario():
    """构造「3 项指标、1 项缺数据」的任务 + 真实快照 + 指标值。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from backend.app.core.db import get_sessionmaker
    from backend.app.domain.models import (
        AssessmentRecord,
        AssessmentTask,
        ConfigSnapshot,
        Employee,
        MetricValue,
    )

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base = None
    from backend.app.domain.models import Base as _Base

    Base = _Base
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        emp = Employee(
            id="E_BD", name="边界测试员工", employment_status="active", account_status="enabled",
        )
        db.add(emp)
        task = AssessmentTask(id="T_BD", name="评分构成边界验证", period="202611")
        db.add(task)
        db.flush()
        snapshot = ConfigSnapshot(
            id="S_BD", task_id="T_BD",
            metrics_json=[
                {"code": "GMV", "name": "销售额", "unit": "元"},
                {"code": "CONV", "name": "转化率", "unit": "%"},
                {"code": "NEW", "name": "新客数", "unit": "个"},
            ],
            weights_json=[
                {"metric_code": "GMV", "weight": 50},
                {"metric_code": "CONV", "weight": 30},
                {"metric_code": "NEW", "weight": 20},
            ],
            rubric_json=[
                {"metric_code": "GMV", "levels": [
                    {"level": "A", "min_ratio": 1.0, "max_ratio": None, "desc": "达成"},
                    {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "desc": "基本达成"},
                ]},
                {"metric_code": "CONV", "levels": [
                    {"level": "A", "min_ratio": 1.0, "max_ratio": None, "desc": "达成"},
                    {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "desc": "基本达成"},
                ]},
                {"metric_code": "NEW", "levels": [
                    {"level": "A", "min_ratio": 1.0, "max_ratio": None, "desc": "达成"},
                    {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "desc": "基本达成"},
                ]},
            ],
        )
        db.add(snapshot)
        db.flush()
        task.snapshot_id = "S_BD"
        db.add(AssessmentRecord(id="R_BD", task_id="T_BD", employee_id="E_BD"))
        # ⚠️ 只有两项有数据 —— NEW 缺数据 → 未参与算分
        db.add(MetricValue(employee_id="E_BD", metric_code="GMV", period="202611",
                           value=95, target=100, source="manual"))
        db.add(MetricValue(employee_id="E_BD", metric_code="CONV", period="202611",
                           value=85, target=100, source="manual"))
        db.commit()
    engine.dispose()

    # 把数据写进**真实库**（L2 服务用的那个），否则服务看不到

    with get_sessionmaker()() as db:
        emp = db.get(Employee, "E_BD")
        if emp is None:
            db.add(Employee(id="E_BD", name="边界测试员工",
                             employment_status="active", account_status="enabled"))
        task = db.get(AssessmentTask, "T_BD")
        if task is None:
            task = AssessmentTask(id="T_BD", name="评分构成边界验证", period="202611")
            db.add(task)
            db.flush()
        snapshot = db.get(ConfigSnapshot, "S_BD")
        if snapshot is None:
            db.add(ConfigSnapshot(
                id="S_BD", task_id="T_BD",
                metrics_json=[
                    {"code": "GMV", "name": "销售额", "unit": "元"},
                    {"code": "CONV", "name": "转化率", "unit": "%"},
                    {"code": "NEW", "name": "新客数", "unit": "个"},
                ],
                weights_json=[
                    {"metric_code": "GMV", "weight": 50},
                    {"metric_code": "CONV", "weight": 30},
                    {"metric_code": "NEW", "weight": 20},
                ],
                rubric_json=[
                    {"metric_code": "GMV", "levels": [
                        {"level": "A", "min_ratio": 1.0, "max_ratio": None, "desc": "达成"},
                        {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "desc": "基本达成"},
                    ]},
                    {"metric_code": "CONV", "levels": [
                        {"level": "A", "min_ratio": 1.0, "max_ratio": None, "desc": "达成"},
                        {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "desc": "基本达成"},
                    ]},
                    {"metric_code": "NEW", "levels": [
                        {"level": "A", "min_ratio": 1.0, "max_ratio": None, "desc": "达成"},
                        {"level": "B", "min_ratio": 0.8, "max_ratio": 1.0, "desc": "基本达成"},
                    ]},
                ],
            ))
            db.flush()
        task.snapshot_id = "S_BD"
        record = db.get(AssessmentRecord, "R_BD")
        if record is None:
            db.add(AssessmentRecord(id="R_BD", task_id="T_BD", employee_id="E_BD"))
        has_gmv = any(
            mv.employee_id == "E_BD" and mv.metric_code == "GMV"
            for mv in db.query(MetricValue).filter_by(period="202611").all()
        )
        if not has_gmv:
            db.add(MetricValue(employee_id="E_BD", metric_code="GMV", period="202611",
                               value=95, target=100, source="manual"))
            db.add(MetricValue(employee_id="E_BD", metric_code="CONV", period="202611",
                               value=85, target=100, source="manual"))
        db.commit()


def main() -> int:
    print("=" * 70)
    print("评分构成 —— 未参与算分的边界场景")
    print("=" * 70)

    print("\n[0] 构造场景（3 项指标，NEW 缺数据）")
    build_scenario()

    from backend.app.core.db import get_sessionmaker
    from backend.app.domain.models import AssessmentTask
    from backend.app.services import agent_tool_service

    with get_sessionmaker()() as db:
        task = db.get(AssessmentTask, "T_BD")
        result = agent_tool_service.load_scoring_breakdown(
            db, employee_id="E_BD", task=task,
        )

    print("\n[1] 工具层返回")
    check("可用", result["available"] is True)
    check("有参与项", len(result["items"]) == 2, str(len(result["items"])))
    check("有未参与项", len(result["excluded_items"]) == 1, str(result["excluded_items"]))
    if result["excluded_items"]:
        excluded = result["excluded_items"][0]
        check("未参与的是 NEW", excluded["metric_code"] == "NEW")
        check("给出了原因", bool(excluded.get("reason")), str(excluded))
        check("标记为无数据", excluded.get("has_value") is False)
    # ⚠️ 权重重新归一：GMV 50 → 62.5%
    if result["items"]:
        gmv = next((i for i in result["items"] if i["metric_code"] == "GMV"), None)
        check("GMV 权重已重新归一",
              gmv is not None and abs(gmv["normalized_weight"] - 0.625) < 1e-6,
              str(gmv["normalized_weight"]) if gmv else "缺失")
        check("原始权重仍是 50", gmv is not None and gmv["raw_weight"] == 50)

    # ── 真模型：问「为什么分数不对」 ────────────────────────
    print("\n[2] 真模型回答「为什么感觉分数不对」")
    import httpx

    from backend.app.core.db import get_sessionmaker as _gs
    from backend.app.routes.auth import _generate_token

    with _gs()() as db:
        token, _username = _generate_token("E_BD", "bd_test")

    resp = httpx.post(
        "http://127.0.0.1:8000/v1/qa/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "question": "我这个月分数是多少？为什么感觉和预期不太一样？",
            "task_id": "T_BD",
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    body = resp.json()
    answer = body.get("answer", "")

    print("\n  --- 模型回答 ---")
    for line in answer.splitlines()[:30]:
        print(f"  | {line}")
    print("  ----------------")

    print("\n[3] 回答正确性")
    tool_names = [c.get("tool") for c in body.get("tool_calls", [])]
    check("调用了 get_scoring_breakdown", "get_scoring_breakdown" in tool_names, str(tool_names))
    check("未降级", body.get("degraded") is False)
    check("有引用", len(body.get("citations", [])) > 0)
    check("提到了未参与/缺数据",
          any(w in answer for w in ("未参与", "不参与", "缺数据", "没有数据", "缺失")),
          answer[:250])
    check("说明了权重重算/归一",
          any(w in answer for w in ("归一", "重新分配", "折算", "重新计算", "权重调整")),
          answer[:250])
    check("没说成被扣分",
          "被扣" not in answer and "扣了" not in answer and "扣除" not in answer,
          answer[:250])
    check("没把规则分说成最终成绩",
          "最终成绩是" not in answer and "最终得分是" not in answer, answer[:150])

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
