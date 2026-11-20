"""P2-3 acceptance: 案例库 + A/B 灰度回流 —— 反馈闭环 / 确定性分桶 / 脱敏 / 样本门槛。"""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    PromptVersion,
    Score,
)
from backend.app.services import feedback_service as svc


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Department(id="D", name="业务", leader_id="M"))
    s.add(Department(id="HR", name="人事"))
    s.add(HrDepartmentConfig(department_id="HR", active=True))
    s.add(Employee(id="E", name="员工E", department_id="D", manager_id="M",
                   employment_status="active", account_status="enabled"))
    s.add(Employee(id="M", name="主管M", department_id="D",
                   employment_status="active", account_status="enabled"))
    s.add(Employee(id="H", name="HR", department_id="HR",
                   employment_status="active", account_status="enabled"))
    s.add(AssessmentTask(id="T", name="任务", period="202611"))
    s.flush()
    s.add(AssessmentRecord(id="R", task_id="T", employee_id="E", confirmed_rating="B"))
    s.add(Score(record_id="R", total=72.0, per_metric_json=[
        {"metric_code": "GMV", "metric_name": "销售额", "value": 90,
         "target": 100, "ratio": 0.9, "level": "B"},
    ]))
    s.commit()
    yield s
    s.close()
    engine.dispose()


# ── 反馈回流 ────────────────────────────────────────────────────────────────

def test_record_feedback(db):
    """反馈落 ai_feedback。"""
    row = svc.record_feedback(
        db, record_id="R", actor_id="M", action="ACCEPT",
        prompt_version="v2", reason="可用",
    )
    db.commit()
    assert row.id
    assert row.action == "ACCEPT"
    assert row.prompt_version == "v2"


def test_feedback_invalid_action_rejected(db):
    """非法动作被拒。"""
    with pytest.raises(svc.FeedbackError, match="反馈动作非法"):
        svc.record_feedback(db, record_id="R", actor_id="M", action="WHATEVER")


def test_feedback_unknown_record_404(db):
    """记录不存在 → 404。"""
    with pytest.raises(svc.FeedbackError, match="不存在"):
        svc.record_feedback(db, record_id="NOPE", actor_id="M", action="ACCEPT")


def test_feedback_does_not_touch_score_or_rating(db):
    """★ 反馈不改 Score / confirmed_rating —— 反馈是对 AI 产出的评价，不是对结论的修改。"""
    svc.record_feedback(db, record_id="R", actor_id="M", action="REJECT")
    db.commit()
    db.expire_all()
    assert db.scalar(select(Score).where(Score.record_id == "R")).total == 72.0
    assert db.get(AssessmentRecord, "R").confirmed_rating == "B"


def test_feedback_stats(db):
    """反馈统计。"""
    svc.record_feedback(db, record_id="R", actor_id="M", action="ACCEPT")
    svc.record_feedback(db, record_id="R", actor_id="M", action="MODIFY")
    svc.record_feedback(db, record_id="R", actor_id="M", action="REJECT")
    db.commit()
    stats = svc.feedback_stats(db, agent="comment_draft")
    assert stats["total"] == 3
    assert stats["accept"] == 1
    assert stats["modify"] == 1
    assert stats["reject"] == 1
    # (1 + 1*0.5) / 3
    assert stats["acceptRate"] == pytest.approx(0.5)


# ── Prompt 灰度 ─────────────────────────────────────────────────────────────

def test_upsert_prompt_version(db):
    """新增版本。"""
    row = svc.upsert_prompt_version(
        db, agent="comment_draft", version="v1", prompt_text="P1", rollout="off"
    )
    db.commit()
    assert row.version == "v1"
    assert row.rollout == "off"


def test_upsert_prompt_is_idempotent(db):
    """同 agent+version 更新而非重复插入。"""
    svc.upsert_prompt_version(db, agent="a", version="v1", prompt_text="P1")
    db.commit()
    svc.upsert_prompt_version(db, agent="a", version="v1", prompt_text="P2")
    db.commit()
    rows = db.scalars(select(PromptVersion).where(PromptVersion.agent == "a")).all()
    assert len(rows) == 1
    assert rows[0].prompt_text == "P2"


def test_invalid_rollout_rejected(db):
    """非法灰度模式被拒。"""
    with pytest.raises(svc.FeedbackError, match="灰度模式非法"):
        svc.upsert_prompt_version(
            db, agent="a", version="v1", prompt_text="P", rollout="random"
        )


def test_canary_ratio_out_of_range_rejected(db):
    """灰度比例越界被拒。"""
    with pytest.raises(svc.FeedbackError, match="比例必须在"):
        svc.upsert_prompt_version(
            db, agent="a", version="v1", prompt_text="P", rollout="canary", canary_ratio=1.5
        )


def test_canary_with_zero_ratio_rejected(db):
    """灰度模式下比例不能为 0。"""
    with pytest.raises(svc.FeedbackError, match="比例必须大于 0"):
        svc.upsert_prompt_version(
            db, agent="a", version="v1", prompt_text="P", rollout="canary", canary_ratio=0.0
        )


def test_resolve_returns_none_when_no_versions(db):
    """没有版本 → none（调用方走默认模板）。"""
    out = svc.resolve_prompt(db, agent="comment_draft", record_id="R")
    assert out["version"] is None
    assert out["mode"] == "none"


def test_resolve_full_rollout(db):
    """full 版本直接生效。"""
    svc.upsert_prompt_version(
        db, agent="a", version="v1", prompt_text="P1", rollout="full"
    )
    db.commit()
    out = svc.resolve_prompt(db, agent="a", record_id="R")
    assert out["version"] == "v1"
    assert out["mode"] == "full"


def test_resolve_canary_is_deterministic(db):
    """★ 同一 record_id 永远落同一个桶 —— 灰度对比的前提。"""
    svc.upsert_prompt_version(
        db, agent="a", version="v2", prompt_text="P2",
        rollout="canary", canary_ratio=0.5, baseline_version="v1",
    )
    svc.upsert_prompt_version(db, agent="a", version="v1", prompt_text="P1", rollout="off")
    db.commit()
    first = svc.resolve_prompt(db, agent="a", record_id="R")
    for _ in range(5):
        assert svc.resolve_prompt(db, agent="a", record_id="R") == first


def test_canary_actually_splits_traffic(db):
    """50% 灰度把流量大致分成两半（统计上，不是精确值）。"""
    svc.upsert_prompt_version(
        db, agent="a", version="v2", prompt_text="P2",
        rollout="canary", canary_ratio=0.5, baseline_version="v1",
    )
    svc.upsert_prompt_version(db, agent="a", version="v1", prompt_text="P1", rollout="off")
    db.commit()
    hits = {"v2": 0, "v1": 0}
    for i in range(400):
        out = svc.resolve_prompt(db, agent="a", record_id=f"REC{i}")
        hits[out["version"]] += 1
    # 允许 ±12% 的统计浮动
    assert 140 < hits["v2"] < 260
    assert 140 < hits["v1"] < 260


def test_canary_ratio_zero_never_hits(db):
    """比例为 0（off 状态）→ 永远不命中灰度版本。"""
    svc.upsert_prompt_version(
        db, agent="a", version="v2", prompt_text="P2",
        rollout="off", baseline_version="v1",
    )
    svc.upsert_prompt_version(db, agent="a", version="v1", prompt_text="P1", rollout="off")
    db.commit()
    for i in range(50):
        out = svc.resolve_prompt(db, agent="a", record_id=f"REC{i}")
        assert out["version"] == "v1"
        assert out["mode"] == "baseline"


def test_canary_does_not_take_over_all_traffic(db):
    """★ 灰度不放大范围：10% 灰度不该接管全部流量。"""
    svc.upsert_prompt_version(
        db, agent="a", version="v2", prompt_text="P2",
        rollout="canary", canary_ratio=0.1, baseline_version="v1",
    )
    svc.upsert_prompt_version(db, agent="a", version="v1", prompt_text="P1", rollout="off")
    db.commit()
    v2_hits = sum(
        1 for i in range(300)
        if svc.resolve_prompt(db, agent="a", record_id=f"REC{i}")["version"] == "v2"
    )
    assert v2_hits < 90  # 10% 期望 30，允许到 90 上限


# ── 灰度对比 ────────────────────────────────────────────────────────────────

def test_compare_rollout_needs_enough_samples(db):
    """★ 样本不足 → 明确不下结论。"""
    svc.record_feedback(db, record_id="R", actor_id="M", action="ACCEPT", prompt_version="v2")
    db.commit()
    out = svc.compare_rollout(db, agent="comment_draft")
    assert out["sufficient"] is False
    assert out["recommendedVersion"] is None
    assert "样本不足" in out["conclusion"]


def test_compare_rollout_with_enough_samples_picks_winner(db):
    """样本足够 → 给出建议版本。"""
    for i in range(40):
        action = "ACCEPT" if i < 35 else "REJECT"
        svc.record_feedback(
            db, record_id="R", actor_id="M", action=action, prompt_version="v2"
        )
    for i in range(40):
        action = "ACCEPT" if i < 10 else "REJECT"
        svc.record_feedback(
            db, record_id="R", actor_id="M", action=action, prompt_version="v1"
        )
    db.commit()
    out = svc.compare_rollout(db, agent="comment_draft")
    assert out["sufficient"] is True
    assert out["recommendedVersion"] == "v2"
    v2 = next(v for v in out["versions"] if v["version"] == "v2")
    assert v2["acceptRate"] == pytest.approx(0.875)


def test_compare_rollout_weights_modify_as_half(db):
    """修改后采纳算 0.5。"""
    svc.record_feedback(db, record_id="R", actor_id="M", action="MODIFY", prompt_version="v2")
    svc.record_feedback(db, record_id="R", actor_id="M", action="REJECT", prompt_version="v2")
    db.commit()
    out = svc.compare_rollout(db, agent="comment_draft")
    v2 = next(v for v in out["versions"] if v["version"] == "v2")
    assert v2["acceptRate"] == pytest.approx(0.25)


def test_compare_rollout_says_based_on_human_feedback(db):
    """对比依据声明为人工反馈。"""
    out = svc.compare_rollout(db, agent="comment_draft")
    assert "人工反馈" in out["note"]


# ── 案例库 ──────────────────────────────────────────────────────────────────

def test_publish_case_requires_confirmed_rating(db):
    """★ 未确认评级不能进案例库。"""
    db.get(AssessmentRecord, "R").confirmed_rating = None
    db.commit()
    with pytest.raises(svc.FeedbackError, match="已确认评级"):
        svc.publish_case(
            db, title="案例", scenario="low_score", resolution="处理方式",
            actor_id="H", source_record_id="R",
        )


def test_publish_case_from_confirmed_record(db):
    """已确认 → 可收录。"""
    row = svc.publish_case(
        db, title="低分案例", scenario="low_score", resolution="面谈 + 改进计划",
        actor_id="H", source_record_id="R",
    )
    db.commit()
    assert row.rating == "B"
    assert row.facts_json["total"] == 72.0
    assert row.facts_json["metrics"][0]["metric"] == "销售额"


def test_published_case_is_forced_redacted(db):
    """★ 入库强制脱敏标记。"""
    row = svc.publish_case(
        db, title="案例", scenario="x", resolution="r", actor_id="H", source_record_id="R"
    )
    db.commit()
    assert row.redacted is True


def test_published_case_facts_contain_no_employee_identity(db):
    """★ 事实摘要里没有员工身份字段。"""
    row = svc.publish_case(
        db, title="案例", scenario="x", resolution="r", actor_id="H", source_record_id="R"
    )
    db.commit()
    facts = row.facts_json
    assert "employee_id" not in facts
    assert "name" not in facts
    for metric in facts["metrics"]:
        assert "employee" not in metric


def test_publish_case_requires_title_and_resolution(db):
    """标题与处理要点不能为空。"""
    with pytest.raises(svc.FeedbackError, match="标题不能为空"):
        svc.publish_case(db, title="  ", scenario="x", resolution="r", actor_id="H")
    with pytest.raises(svc.FeedbackError, match="处理要点不能为空"):
        svc.publish_case(db, title="t", scenario="x", resolution="  ", actor_id="H")


def test_search_cases_by_scenario(db):
    """按场景检索。"""
    svc.publish_case(db, title="A", scenario="veto", resolution="r1", actor_id="H")
    svc.publish_case(db, title="B", scenario="dispute", resolution="r2", actor_id="H")
    db.commit()
    assert len(svc.search_cases(db, scenario="veto")) == 1
    assert len(svc.search_cases(db)) == 2


def test_search_cases_by_keyword(db):
    """按关键词检索。"""
    svc.publish_case(db, title="低分处理", scenario="x", resolution="面谈改进", actor_id="H")
    db.commit()
    assert len(svc.search_cases(db, keyword="面谈")) == 1
    assert len(svc.search_cases(db, keyword="不存在的词")) == 0


def test_mark_case_used_increments_count(db):
    """引用计数。"""
    row = svc.publish_case(db, title="A", scenario="x", resolution="r", actor_id="H")
    db.commit()
    svc.mark_case_used(db, case_id=row.id)
    svc.mark_case_used(db, case_id=row.id)
    db.commit()
    assert row.use_count == 2


def test_case_library_isolation_from_business_tables(db):
    """★ 案例库操作不修改 Score / confirmed_rating。"""
    svc.publish_case(db, title="A", scenario="x", resolution="r", actor_id="H", source_record_id="R")
    db.commit()
    db.expire_all()
    assert db.scalar(select(Score).where(Score.record_id == "R")).total == 72.0
    assert db.get(AssessmentRecord, "R").confirmed_rating == "B"
