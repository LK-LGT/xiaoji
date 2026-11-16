"""端到端演示 —— 跑一次完整的月度考核闭环。

用法：
    # 1) 先启动智能体服务
    python -m uvicorn app.main:app --port 8800   (在 agent-service 目录下)

    # 2) 再跑本脚本
    python scripts/demo_e2e.py                    (在 backend 目录下)

它会验证框架的三条验收标准：
    1. 能不能复算 —— 关掉 AI 重跑，分数完全一致
    2. 能不能回溯 —— 评语 → 证据 → 原始数据源 → 冻结快照口径
    3. 模型挂了还在跑 —— 断掉智能体服务，流程不中断
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.db import get_sessionmaker, init_db  # noqa: E402
from app.db import seed as seed_mod  # noqa: E402
from app.domain.enums import AttendanceAnomalyType, EvidenceSource, SubmitSource  # noqa: E402
from app.domain.models import Employee  # noqa: E402
from app.services import evidence_service, review_service  # noqa: E402

SEP = "=" * 72


def hr(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def main() -> None:
    db_path = BACKEND / "data" / "demo.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    import app.core.config as config_mod

    config_mod.settings.database_url = f"sqlite:///{db_path}"
    import app.core.db as db_mod

    db_mod._engine = None
    db_mod._SessionLocal = None
    init_db()

    db = get_sessionmaker()()
    seed_mod.seed(db)

    # ------------------------------------------------------------------
    hr("第 1 步：HR 发起考核（冻结配置快照）")
    task = review_service.create_task(
        db,
        name="运营部 2026-10 月度考核",
        period="2026-10",
        department_id="D-OPS",
        position="运营专员",
        owner_id="E-MGR-001",
    )
    print(f"  任务 ID      : {task.id}")
    print(f"  配置快照 ID  : {task.snapshot_id}   ← 冻结快照，本次考核全程引用")
    print(f"  分布策略     : {task.rating_policy}   ← 决策⑦：第一期只做对比，不强制比例")

    recs = review_service.open_records(db, task.id)
    rec = recs[0]
    emp = db.get(Employee, rec.employee_id)
    print(f"  开启记录     : {len(recs)} 条，员工 {emp.name}（{emp.position}）")

    # ------------------------------------------------------------------
    hr("第 2 步：数据进来 —— 经营指标 + 考勤异常")
    evidence_service.add_metric_value(
        db,
        employee_id=emp.id,
        metric_code="gmv",
        period="2026-10",
        value=1_250_000,
        target=1_200_000,
        unit="元",
        source=EvidenceSource.SYSTEM_API.value,
        source_ref="ecommerce://orders/2026-10",
    )
    evidence_service.add_metric_value(
        db,
        employee_id=emp.id,
        metric_code="conversion_rate",
        period="2026-10",
        value=3.8,
        target=3.5,
        unit="%",
        source=EvidenceSource.SYSTEM_API.value,
    )
    evidence_service.add_metric_value(
        db,
        employee_id=emp.id,
        metric_code="refund_rate",
        period="2026-10",
        value=2.0,
        target=2.5,
        unit="%",
        source=EvidenceSource.SYSTEM_API.value,
    )
    print("  GMV / 转化率 / 退款率  ← 经营数据进算分集")

    anomalies = evidence_service.record_attendance_anomalies(
        db,
        [
            evidence_service.AnomalyInput(
                employee_id=emp.id,
                period="2026-10",
                anomaly_type=AttendanceAnomalyType.LATE,
                count=3,
            ),
            evidence_service.AnomalyInput(
                employee_id=emp.id,
                period="2026-10",
                anomaly_type=AttendanceAnomalyType.LEAVE,
                count=1,
                approved=True,
            ),
            evidence_service.AnomalyInput(
                employee_id=emp.id,
                period="2026-10",
                anomaly_type=AttendanceAnomalyType.OVERTIME,
                count=12,
            ),
        ],
    )
    print(f"  考勤异常     : {len(anomalies)} 条（迟到/请假/加班）")
    print(f"                 usage = {anomalies[0].usage}  ← 不进算分集")
    for a in anomalies:
        print(f"                 · {a.summary}")

    # ------------------------------------------------------------------
    hr("第 3 步：员工提交自评（飞书审批入口）")
    review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={
            "key_work": "双11大促活动策划与执行，负责站内投放与私域转化",
            "achievement": "GMV 达成 125 万，超目标 4.2%；转化率从 3.3% 提升到 3.8%",
            "gmv_note": "GMV 未超 5%，主因竞品同期降价",
        },
        attachments=[{"name": "gmv-dashboard.png", "url": "https://example.com/gmv.png"}],
        source=SubmitSource.FEISHU_APPROVAL,
    )
    print("  自评已提交，来源 = feishu_approval")

    # ------------------------------------------------------------------
    hr("第 4 步：规则引擎算分（唯一真值，与 AI 无关）")
    score = review_service.compute_score(db, record_id=rec.id)
    review_service.persist_score(db, record_id=rec.id, result=score)
    print(f"  总分: {score.total}")
    for pm in score.per_metric:
        print(
            f"    {pm['metric_name']:<8} 实际 {pm['value']} / 目标 {pm['target']} "
            f"→ 达成率 {pm['ratio']} → 等级 {pm['level']} "
            f"→ 权重 {pm['normalized_weight']} → 得分 {pm['contribution']}"
        )

    # ------------------------------------------------------------------
    hr("第 5 步：智能体层 —— 评级建议 + 评语草稿")
    try:
        bundle = review_service.generate_draft(db, record_id=rec.id)
        print(f"  智能体状态: {bundle.agent_status}   降级: {bundle.degraded}")
        print("  评级建议:")
        for r in bundle.ratings:
            print(f"    · {r.get('dimension')}: {r.get('level')} — {r.get('reason')}")
        print("  评语草稿:")
        for line in bundle.comment_markdown.splitlines():
            print(f"    {line}")
        if bundle.gaps:
            print("  证据缺口:")
            for g in bundle.gaps:
                print(f"    · {g.get('dimension')}: {g.get('request')}")
    except Exception as exc:
        print(f"  智能体服务不可用（{exc}）")
        print("  → 业务层已自动降级出模板草稿，流程继续")

    # ------------------------------------------------------------------
    hr("第 6 步：验收 ① —— 能不能复算")
    again = review_service.compute_score(db, record_id=rec.id)
    print(f"  首次: {score.total}   复算: {again.total}   一致: {score.total == again.total}")
    print("  → 分数永远由规则引擎产出，与 AI 无关，可完全复算")

    # ------------------------------------------------------------------
    hr("第 7 步：验收 ② —— 能不能回溯")
    payload = review_service.build_draft_payload(db, record_id=rec.id)
    print("  评语 → 证据 → 原始数据源 → 口径快照：")
    for m in payload["metrics"]:
        print(
            f"    {m['dimension']}: 值 {m['value']} ← 来源 {m['source']} "
            f"← 快照口径已冻结（as_of {m['as_of'] or '-'}）"
        )
    print(f"  考勤异常在独立字段（不进 evidence）：{len(payload['attendance_anomalies'])} 条")

    # ------------------------------------------------------------------
    hr("第 8 步：验收 ③ —— 模型挂了还在跑")
    print("  模拟智能体服务宕机...")

    class DeadClient:
        def draft(self, payload):
            raise RuntimeError("connection refused")

    degraded = review_service.generate_draft(db, record_id=rec.id, agent_client=DeadClient())
    print(f"  降级标记: {degraded.degraded}")
    print(f"  流程未中断，仍产出草稿 {len(degraded.comment_markdown)} 字符")
    print(f"  分数照常算出: {degraded.score.total}")

    # ------------------------------------------------------------------
    hr("第 9 步：人工确认（最后一道闸）")
    out = review_service.confirm_draft(
        db, record_id=rec.id, confirmed_by="E-MGR-001", confirmed_rating="B"
    )
    print(f"  确认等级: {out['rating']}（人工决定）")
    print(f"  确认后重算分数: {out['score']}")

    db.commit()
    print(f"\n{SEP}\n  完成。数据库: {db_path}\n{SEP}")


if __name__ == "__main__":
    main()
