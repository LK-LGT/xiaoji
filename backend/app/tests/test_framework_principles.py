"""框架核心保证的测试。

三条不可破的原则，各有对应测试：
- 原则三（模型不算分）：AI 建议改了，分数不变；分数永远可复算
- 决策②（考勤仅参考）：考勤进不了算分集；考勤不进 AI 的 evidence 字段
- 决策③（冻结快照）：中途改配置，不影响已评分数

迁移说明（R19）
----------------
本文件原在 ``backend/tests/test_framework.py``，但那个目录**不在**
``pyproject.toml`` 的 ``testpaths`` 里 —— 裸 ``pytest`` 根本不收集它，
等于「躺在仓库里假装是测试，实际一次都没跑过」。

而且它的导入根算错了：``parents[1] / "backend"`` 解析到
``backend/backend``（**从不存在的路径**），所以显式跑它会直接
``ModuleNotFoundError: No module named 'app'``。

现迁入 ``backend/app/tests/``（活跃套件），导入统一改成
``backend.app.*``，与仓库其余 54 个测试文件一致。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_sessionmaker, init_db
from backend.app.db import seed as seed_mod
from backend.app.domain.enums import (
    AttendanceAnomalyType,
    DraftStatus,
    EvidenceSource,
    EvidenceUsage,
    SubmitSource,
)
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    CommentDraft,
    ConfigSnapshot,
    Employee,
    Evidence,
    Metric,
    MetricValue,
    RatingDraft,
    Score,
    SelfReview,
    WeightSet,
)
from backend.app.services import evidence_service, review_service, scoring
from backend.app.services.config_service import ConfigValidationError, validate_config


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """每个测试一个独立库。

    ⚠️ 这里**不用** ``init_db()`` —— 它在 ``AUTO_CREATE_TABLES`` 关闭时
    会直接 return（schema 交给 Alembic 管），建不出表。
    仓库其余测试统一用 ``Base.metadata.create_all(engine)``，这里跟��致。
    """
    from backend.app.core import config as config_mod
    from backend.app.domain.models import Base

    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setattr(config_mod.settings, "database_url", url)
    # 重置 engine / sessionmaker
    import backend.app.core.db as db_mod

    db_mod._engine = None
    db_mod._SessionLocal = None

    engine = create_engine(url)
    Base.metadata.create_all(engine)

    sm = get_sessionmaker()
    session = sm()
    seed_mod.seed(session)
    yield session
    session.close()
    db_mod._engine = None
    db_mod._SessionLocal = None
    engine.dispose()


@pytest.fixture()
def prepared(db):
    """建任务 + 开记录 + 写入指标值。返回 (task, record, employee)。"""
    task = review_service.create_task(
        db,
        name="运营部 2026-10 月度考核",
        period="2026-10",
        department_id="D-OPS",
        position="运营专员",
        owner_id="E-MGR-001",
    )
    recs = review_service.open_records(db, task.id)
    rec = recs[0]
    emp = db.get(Employee, rec.employee_id)

    evidence_service.add_metric_value(
        db,
        employee_id=emp.id,
        metric_code="gmv",
        period="2026-10",
        value=1_250_000,
        target=1_200_000,
        unit="元",
        source=EvidenceSource.SYSTEM_API.value,
        as_of=datetime(2026, 10, 31, 23, 0),
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
    return task, rec, emp


# ---------------------------------------------------------------------------
# 规则引擎
# ---------------------------------------------------------------------------


def test_weight_must_sum_to_100():
    """权重合计 ≠ 100 → 拒绝算分。这是配置错误，必须阻断不能降级。"""
    specs = [scoring.MetricSpec(code="a", name="A", weight=60)]
    with pytest.raises(scoring.ScoringError):
        scoring.score_record(specs, [])


def test_level_matching_and_score():
    """达成率 104.2% → B 区间（0.95~1.05）。"""
    specs = [
        scoring.MetricSpec(
            code="gmv",
            name="GMV",
            weight=100,
            levels=(
                {"level": "A", "min_ratio": 1.05, "max_ratio": None, "desc": "超额", "score": 100},
                {"level": "B", "min_ratio": 0.95, "max_ratio": 1.05, "desc": "达成", "score": 80},
                {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95, "desc": "未达成", "score": 60},
            ),
        )
    ]
    inputs = [scoring.MetricInput(code="gmv", value=1_250_000, target=1_200_000)]
    r = scoring.score_record(specs, inputs)
    assert r.per_metric[0]["level"] == "B"
    assert r.per_metric[0]["ratio"] == pytest.approx(1.0416667, rel=1e-4)
    assert r.total == pytest.approx(80.0)


def test_missing_metric_does_not_penalize_others():
    """缺数据的指标不参与算分，其余指标权重重新归一 —— 缺数据 ≠ 扣分。"""
    specs = [
        scoring.MetricSpec(code="a", name="A", weight=50, levels=({"level": "B", "min_ratio": 0, "max_ratio": None, "score": 80},)),
        scoring.MetricSpec(code="b", name="B", weight=50, levels=({"level": "B", "min_ratio": 0, "max_ratio": None, "score": 80},)),
    ]
    inputs = [scoring.MetricInput(code="a", value=100, target=100)]
    r = scoring.score_record(specs, inputs)
    assert r.total == pytest.approx(80.0)
    assert r.per_metric[0]["normalized_weight"] == pytest.approx(1.0)
    assert any("缺少数据" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# 原则三：模型永远不产生数字
# ---------------------------------------------------------------------------


def test_score_is_reproducible_without_ai(prepared, db):
    """框架验收第 1 条：关掉 AI 重跑规则引擎，分数完全一致。"""
    task, rec, emp = prepared

    r1 = review_service.compute_score(db, record_id=rec.id)
    review_service.persist_score(db, record_id=rec.id, result=r1)

    # 反复复算
    for _ in range(3):
        r2 = review_service.compute_score(db, record_id=rec.id)
        assert r2.total == r1.total

    score = db.execute(select(Score).where(Score.record_id == rec.id)).scalar_one()
    assert score.total == r1.total


def test_ai_draft_never_writes_score(prepared, db, monkeypatch):
    """AI 结果只能进 RatingDraft/CommentDraft，**绝不进 Score**。"""

    class EvilAgentClient:
        """返回一个试图篡改分数的响应。"""

        def draft(self, payload):
            return {
                "ratings": [{"dimension": "GMV", "level": "A", "reason": "x", "evidence_ids": []}],
                "comment_draft": "很好的一个月",
                "citations": [],
                "gaps": [],
                "fairness": {"grounded": True, "warnings": [], "unsupported_claims": 0},
                # 试图塞进分数
                "score": 999.0,
                "total": 999.0,
                "degraded": False,
                "agent_status": {},
            }

    task, rec, emp = prepared
    bundle = review_service.generate_draft(db, record_id=rec.id, agent_client=EvilAgentClient())

    # AI 声称的分数被忽略
    assert bundle.score.total != 999.0

    # Score 表里只有规则引擎算出的值
    score = db.execute(select(Score).where(Score.record_id == rec.id)).scalar_one_or_none()
    assert score is None or score.total != 999.0

    # AI 结果落在草稿表
    rd = db.execute(
        select(RatingDraft).where(RatingDraft.record_id == rec.id)
    ).scalars().first()
    assert rd is not None
    assert rd.status == DraftStatus.DRAFT.value
    assert rd.ratings_json[0]["level"] == "A"


def test_confirm_recomputes_score(prepared, db):
    """人工确认时分数重算 —— 因为确认的等级可能与 AI 建议不同。"""
    task, rec, emp = prepared
    before = review_service.compute_score(db, record_id=rec.id)
    review_service.persist_score(db, record_id=rec.id, result=before)

    out = review_service.confirm_draft(
        db, record_id=rec.id, confirmed_by="E-MGR-001", confirmed_rating="B"
    )
    assert out["rating"] == "B"
    # 分数仍然等于规则引擎算出的值
    after = review_service.compute_score(db, record_id=rec.id)
    assert out["score"] == after.total


# ---------------------------------------------------------------------------
# 决策②：考勤六类异常只作参考
# ---------------------------------------------------------------------------


def test_attendance_cannot_enter_scoring_set(prepared, db):
    """考勤来源写进算分集 → 直接拒绝。"""
    emp = db.get(Employee, "E-001")
    with pytest.raises(evidence_service.AttendanceEvidenceRejected):
        evidence_service.add_metric_value(
            db,
            employee_id=emp.id,
            metric_code="attendance_score",
            period="2026-10",
            value=80,
            source=EvidenceSource.ATTENDANCE.value,
        )


def test_attendance_evidence_is_reference_only(prepared, db):
    """六类异常全部写进 Evidence，但 usage = reference_only。"""
    emp = db.get(Employee, "E-001")
    for t in AttendanceAnomalyType:
        evs = evidence_service.record_attendance_anomalies(
            db,
            [
                evidence_service.AnomalyInput(
                    employee_id=emp.id,
                    period="2026-10",
                    anomaly_type=t,
                    count=2,
                    as_of=datetime(2026, 10, 31, 23, 0),
                    approved=True if t == AttendanceAnomalyType.LEAVE else None,
                )
            ],
        )
        assert evs[0].usage == EvidenceUsage.REFERENCE_ONLY.value
        # 绝不在算分集里
        assert db.execute(
            select(MetricValue).where(MetricValue.employee_id == emp.id)
        ).scalars().first() is not None  # 只有 gmv 等真实指标
        assert not db.execute(
            select(MetricValue).where(
                MetricValue.employee_id == emp.id,
                MetricValue.source == EvidenceSource.ATTENDANCE.value,
            )
        ).scalars().first()


def test_attendance_labels_and_disclaimer(prepared, db):
    """请假标"已审批"，加班标"仅参考、不参与评分"，视图带免责声明。"""
    emp = db.get(Employee, "E-001")
    evidence_service.record_attendance_anomalies(
        db,
        [
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
                count=8,
            ),
        ],
    )
    rows = evidence_service.list_scoring_evidence(db, emp.id)
    scoring_rows, reference_rows = evidence_service.split_by_usage(rows)
    assert len(reference_rows) == 2

    view = evidence_service.build_attendance_view(reference_rows)
    assert all(v["disclaimer"] == "考勤异常不自动扣分，仅作参考" for v in view)

    summaries = " ".join(v["summary"] for v in view)
    assert "已审批" in summaries
    assert "仅参考、不参与评分" in summaries


def test_attendance_not_in_agent_evidence_field(prepared, db):
    """**关键**：考勤不进 AI 的 evidence 字段，而是独立的 attendance_anomalies 字段。

    这样模型物理上无法把考勤当结论支撑。
    """
    task, rec, emp = prepared
    evidence_service.record_attendance_anomalies(
        db,
        [
            evidence_service.AnomalyInput(
                employee_id=emp.id,
                period="2026-10",
                anomaly_type=AttendanceAnomalyType.LATE,
                count=3,
            )
        ],
    )
    payload = review_service.build_draft_payload(db, record_id=rec.id)

    # evidence 里没有考勤
    for e in payload["evidence"]:
        assert e["usage"] == EvidenceUsage.SCORING.value
        assert e["kind"] != EvidenceSource.ATTENDANCE.value

    # 考勤在独立字段里
    assert len(payload["attendance_anomalies"]) == 1
    assert payload["attendance_anomalies"][0]["type"] == "late"
    assert payload["attendance_anomalies"][0]["disclaimer"]


def test_attendance_does_not_change_score(prepared, db):
    """加了考勤异常，分数纹丝不动。"""
    task, rec, emp = prepared
    before = review_service.compute_score(db, record_id=rec.id).total

    evidence_service.record_attendance_anomalies(
        db,
        [
            evidence_service.AnomalyInput(
                employee_id=emp.id,
                period="2026-10",
                anomaly_type=AttendanceAnomalyType.MISSING_PUNCH,
                count=5,
            )
        ],
    )
    after = review_service.compute_score(db, record_id=rec.id).total
    assert before == after


# ---------------------------------------------------------------------------
# 决策③：冻结快照
# ---------------------------------------------------------------------------


def test_config_change_does_not_affect_running_task(prepared, db):
    """**冻结快照**：任务启动后 HR 改配置，不影响本次考核的计算。"""
    task, rec, emp = prepared

    before = review_service.compute_score(db, record_id=rec.id)

    # HR 在多维表格改了权重（本地模拟：改 WeightSet）
    ws = db.execute(
        select(WeightSet).where(WeightSet.id == "W-OPS-SPEC")
    ).scalar_one()
    ws.weights_json = [
        {"metric_code": "gmv", "weight": 10},
        {"metric_code": "conversion_rate", "weight": 80},
        {"metric_code": "refund_rate", "weight": 10},
    ]
    db.flush()

    after = review_service.compute_score(db, record_id=rec.id)
    assert before.total == after.total
    # 快照里的权重没变
    snap = db.get(ConfigSnapshot, task.snapshot_id)
    assert snap.weights_json[0]["weight"] == 50


def test_config_validation_catches_bad_weights():
    """读入校验：权重合计 ≠ 100 → 报错不生效。"""
    with pytest.raises(ConfigValidationError, match="权重合计"):
        validate_config(
            metrics=[{"code": "a", "calculation": "x", "data_source": "y"}],
            weights=[{"metric_code": "a", "weight": 50}],
            rubric=[],
        )


def test_config_validation_catches_missing_caliber():
    """读入校验：口径字段非空。"""
    with pytest.raises(ConfigValidationError, match="calculation"):
        validate_config(
            metrics=[{"code": "a", "calculation": "", "data_source": "y"}],
            weights=[{"metric_code": "a", "weight": 100}],
            rubric=[],
        )


def test_config_validation_catches_duplicate_codes():
    """读入校验：指标 ID 唯一。"""
    with pytest.raises(ConfigValidationError, match="重复"):
        validate_config(
            metrics=[
                {"code": "a", "calculation": "x", "data_source": "y"},
                {"code": "a", "calculation": "x", "data_source": "y"},
            ],
            weights=[{"metric_code": "a", "weight": 100}],
            rubric=[],
        )


# ---------------------------------------------------------------------------
# 双入口
# ---------------------------------------------------------------------------


def test_one_entry_lock_after_submit(prepared, db):
    """一个考核任务只能从一个入口提交，另一端只读。

    ⚠️ 契约澄清（迁移时发现，见 ``models.py`` 的 ``SelfReview``）：
    ``self_review.record_id`` 有 **UNIQUE 约束**（迁移 ``0001_baseline``
    声明、ORM 后补齐）。这是**有意为之** —— 重复自评行会让按 record_id 的
    ``scalar_one_or_none()`` 直接 500。

    所以「同入口重复提交」的幂等**必须靠 ``dedup_key``**（飞书事件回调
    可能重复投递），而不是「同 source 就自动幂等」。

    本用例因此覆盖三件事：

    1. 同 ``dedup_key`` 重复提交 → 幂等，返回同一条；
    2. 同 source 但**无** dedup_key → 撞 UNIQUE 约束（拒绝写第二条）；
    3. 换入口 → ``InvalidState``「只读」。
    """
    task, rec, emp = prepared

    review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={"key_work": "做活动"},
        attachments=[],
        source=SubmitSource.FEISHU_APPROVAL,
        dedup_key="evt-lock-1",
    )

    # 1. 同 dedup_key 重复投递 → 幂等（不报错，返回同一条）
    again = review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={"key_work": "做活动"},
        attachments=[],
        source=SubmitSource.FEISHU_APPROVAL,
        dedup_key="evt-lock-1",
    )
    existing = db.execute(
        select(SelfReview).where(SelfReview.record_id == rec.id)
    ).scalar_one()
    assert again.id == existing.id, "同 dedup_key 应返回同一条自评"

    # 2. 换入口 → 拒绝（另一入口只读）
    with pytest.raises(review_service.InvalidState, match="只读"):
        review_service.submit_self_review(
            db,
            record_id=rec.id,
            answers={"key_work": "做活动"},
            attachments=[],
            source=SubmitSource.H5,
        )


def test_second_self_review_row_is_rejected_by_unique_constraint(prepared, db):
    """一条考核记录只能有一行自评（UNIQUE 约束兜底）。

    ⚠️ 这是**数据库层**的兜底：即使服务层漏了判断，
    第二行也写不进去 —— 避免重复自评把按 record_id 的
    ``scalar_one_or_none()`` 查询打成 500。
    """
    task, rec, emp = prepared

    review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={"key_work": "做活动"},
        attachments=[],
        source=SubmitSource.FEISHU_APPROVAL,
        dedup_key="evt-unique-1",
    )
    db.commit()

    # 不带 dedup_key 再写一条 → 撞 UNIQUE(record_id)
    with pytest.raises(IntegrityError):
        review_service.submit_self_review(
            db,
            record_id=rec.id,
            answers={"key_work": "再做一次"},
            attachments=[],
            source=SubmitSource.FEISHU_APPROVAL,
        )
    db.rollback()

    rows = db.execute(
        select(SelfReview).where(SelfReview.record_id == rec.id)
    ).scalars().all()
    assert len(rows) == 1, "只应存在一行自评"


def test_dedup_key_prevents_duplicate_submission(prepared, db):
    """幂等：同一审批事件的重复回调不会产生两条自评。"""
    task, rec, emp = prepared
    review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={"key_work": "x"},
        attachments=[],
        source=SubmitSource.FEISHU_APPROVAL,
        dedup_key="apv-001:evt-1",
    )
    review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={"key_work": "y"},
        attachments=[],
        source=SubmitSource.FEISHU_APPROVAL,
        dedup_key="apv-001:evt-1",
    )
    count = len(
        db.execute(
            select(SelfReview).where(
                SelfReview.record_id == rec.id
            )
        ).scalars().all()
    )
    assert count == 1


# ---------------------------------------------------------------------------
# 原则四：可降级
# ---------------------------------------------------------------------------


def test_agent_downtime_degrades_but_flow_continues(prepared, db):
    """智能体服务挂了 → 降级出模板草稿，流程不中断。"""

    class DeadAgentClient:
        def draft(self, payload):
            raise RuntimeError("connection refused")

    task, rec, emp = prepared
    bundle = review_service.generate_draft(db, record_id=rec.id, agent_client=DeadAgentClient())

    assert bundle.degraded is True
    assert bundle.comment_markdown  # 有降级产物
    assert bundle.score.total is not None  # 分数照常算出来

    # 草稿仍然落库
    rd = db.execute(
        select(RatingDraft).where(RatingDraft.record_id == rec.id)
    ).scalars().first()
    assert rd is not None


def test_self_review_becomes_evidence_cards(prepared, db):
    """自评提交后自动生成证据卡片（visibility = allow_for_review）。"""
    task, rec, emp = prepared
    review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={
            "key_work": "双11大促活动策划与执行",
            "achievement": "GMV 达成 125 万，超目标 4.2%",
            "empty": "",
        },
        attachments=[],
        source=SubmitSource.FEISHU_APPROVAL,
    )
    rows = db.execute(
        select(Evidence).where(Evidence.employee_id == emp.id)
    ).scalars().all()
    # 空值不生成证据
    assert len(rows) == 2
    assert all(r.visibility == "allow_for_review" for r in rows)
    assert all(r.usage == EvidenceUsage.SCORING.value for r in rows)


def test_self_review_evidence_links_to_metric(prepared, db):
    """自评里提到 GMV → 该证据被关联到 gmv 指标。"""
    task, rec, emp = prepared
    review_service.submit_self_review(
        db,
        record_id=rec.id,
        answers={"achievement": "GMV 达成 125 万，超目标 4.2%"},
        attachments=[],
        source=SubmitSource.FEISHU_APPROVAL,
    )
    payload = review_service.build_draft_payload(db, record_id=rec.id)
    gmv = next(m for m in payload["metrics"] if m["code"] == "gmv")
    assert len(gmv["evidence_ids"]) == 1


# ---------------------------------------------------------------------------
# 引用校验
# ---------------------------------------------------------------------------


def test_citation_validation_rejects_other_employee_evidence(prepared, db):
    """引用校验：不能引用别的员工的证据。"""
    task, rec, emp = prepared
    other = db.get(Employee, "E-002")
    ev = Evidence(
        employee_id=other.id,
        kind=EvidenceSource.MANUAL_UPLOAD.value,
        summary="别人的证据",
        usage=EvidenceUsage.SCORING.value,
    )
    db.add(ev)
    db.flush()

    ok, valid = review_service.validate_citations(
        db, record_id=rec.id, evidence_ids=[ev.id]
    )
    assert ok is False
    assert valid == []


def test_citation_validation_accepts_own_evidence(prepared, db):
    """引用校验：本人的有效证据通过。"""
    task, rec, emp = prepared
    ev = Evidence(
        employee_id=emp.id,
        kind=EvidenceSource.MANUAL_UPLOAD.value,
        summary="GMV 超额完成 4.2%",
        usage=EvidenceUsage.SCORING.value,
    )
    db.add(ev)
    db.flush()

    ok, valid = review_service.validate_citations(db, record_id=rec.id, evidence_ids=[ev.id])
    assert ok is True
    assert valid == [ev.id]
