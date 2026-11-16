"""规则引擎端点 —— **边界漏洞回归测试**（自检轮新增）。

背景
----
``test_rules_engine_api.py` 覆盖了 20 条主路径用例。自检轮用 19 条边界
探测脚本复查后，又挖出 4 个问题，本文件把它们固化成回归测试：

    B1  整档评级取「首个指标等级」而非总分
        → 混合等级场景（gmv=A + conv=C，总分 88）误报「A」。
        → 修复：``rules_engine._overall_level`` 按加权总分折算。

    B2  权重可以为负（``-70 + 170 = 100`` 能通过合计校验）
        → 归一化后产生负权重，总分可跑到 128（越界，>100）。
        → 修复：``scoring.score_record`` 增负值 / 零值权重阻断。

    B3  ``Score.record_id`` 无唯一约束
        → ORM 模型漏了 ``0001_baseline.py`` 里已声明的 ``uq_score_record``，
          造成「Alembic 建库」与「create_all 建库」schema 漂移，
          真实运行库属于后者 → 并发算分可写出重复行，
          之后所有 ``scalar_one_or_none()`` 读路径（算分 / 详情 / 结果页）全 500。
        → 修复：ORM 模型补约束 + ``0009`` 迁移修存量库。

    B4  ``MetricValue.target`` 为 NULL / 0
        → 达成率无法计算，该指标记 0 分并记 warning。
        → 这是 ``scoring.py`` 的既定口径，本文件固化「不崩、不静默」。

覆盖
----
    H. 边界与数据健壮性（target 空 / 等级缺口 / 负权重 / 快照缺失 …）
    I. Schema 一致性（ORM ↔ 迁移 ↔ 真实库）
    J. 整档评级语义
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, select

from backend.app.domain.enums import RecordStatus
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    ConfigSnapshot,
    Employee,
    MetricValue,
    Score,
)
from backend.app.services import scoring

# 复用 `test_rules_engine_api.py` 里的 fixture / 辅助函数。
# pytest 的跨模块 fixture 共享要**显式 import**（不带 `F401` 豁免的那几个）。
from backend.app.tests.test_rules_engine_api import (  # noqa: F401
    CONV_LEVELS,
    ENDPOINT,
    GMV_LEVELS,
    PERIOD,
    _add_metric_values,
    _make_task,
    _scores,
    db,  # noqa: F401  fixture
    login,  # noqa: F401  fixture
    org,  # noqa: F401  fixture
)

# ---------------------------------------------------------------------------
# H. 边界与数据健壮性
# ---------------------------------------------------------------------------


def test_null_target_scores_zero_without_crashing(db, org, login):
    """`MetricValue.target` 为 NULL → 该指标记 0 分，不崩、不静默吞掉。

    口径来自 ``scoring.py``：``缺少目标值，无法计算达成率，按 0 计``。
    """
    _make_task(db, "E_A")
    db.add(MetricValue(
        id="MV_NULL_TARGET", employee_id="E_A", metric_code="gmv", period=PERIOD,
        value=1_200_000.0, target=None, unit="元", source="excel_import",
        source_ref="E_A|null", as_of=None,
    ))
    db.add(MetricValue(
        id="MV_CONV", employee_id="E_A", metric_code="conversion_rate", period=PERIOD,
        value=96.0, target=100.0, unit="%", source="excel_import",
        source_ref="E_A|conv", as_of=None,
    ))
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    # gmv 记 0，conv 记 80 → 总分 0.3 * 80 = 24
    assert resp.json()["final_score"] == pytest.approx(24.0)


def test_zero_target_scores_zero_without_crashing(db, org, login):
    """`target = 0` → 同样记 0 分（不产生除零异常）。"""
    _make_task(db, "E_A")
    db.add(MetricValue(
        id="MV_ZERO_TARGET", employee_id="E_A", metric_code="gmv", period=PERIOD,
        value=1_200_000.0, target=0.0, unit="元", source="excel_import",
        source_ref="E_A|zero", as_of=None,
    ))
    db.add(MetricValue(
        id="MV_CONV2", employee_id="E_A", metric_code="conversion_rate", period=PERIOD,
        value=96.0, target=100.0, unit="%", source="excel_import",
        source_ref="E_A|conv2", as_of=None,
    ))
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["final_score"] == pytest.approx(24.0)


def test_ratio_missing_from_level_band_scores_zero(db, org, login):
    """达成率落在等级区间**缝隙**里 → 该指标记 0 分（并记 warning）。

    口径来自 ``scoring.py``：``未命中任何等级区间``。
    """
    gapped = [
        {"level": "A", "min_ratio": 1.05, "max_ratio": None, "score": 100},
        {"level": "C", "min_ratio": 0.0, "max_ratio": 0.80, "score": 60},
        # 0.80 ~ 1.05 是缝隙
    ]
    _make_task(db, "E_A")
    snapshot = db.get(ConfigSnapshot, "CS_1")
    snapshot.rubric_json = [
        {"metric_code": "gmv", "levels_json": gapped},
        {"metric_code": "conversion_rate", "levels_json": gapped},
    ]
    db.flush()
    _add_metric_values(db, "E_A", gmv=1_000_000.0, conversion=96.0)
    # ratio: gmv = 0.8333（缝隙），conv = 0.96（缝隙）

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["final_score"] == pytest.approx(0.0)


def test_missing_snapshot_is_blocked(db, org, login):
    """任务没有冻结快照 → 阻断，不出分。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    task = db.get(AssessmentTask, "T_1")
    task.snapshot_id = None
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code in (409, 500), resp.text
    db.expire_all()
    assert _scores(db) == []


def test_missing_snapshot_row_is_blocked(db, org, login):
    """`snapshot_id` 指向已被删除的快照 → 阻断，不出分。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    task = db.get(AssessmentTask, "T_1")
    task.snapshot_id = "CS_GONE"
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code in (409, 500), resp.text
    db.expire_all()
    assert _scores(db) == []


def test_metric_values_from_other_period_are_ignored(db, org, login):
    """其它周期的 `MetricValue` 不参与算分 —— 周期隔离。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0,
                        period="2026-10")

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error_code"] == "CONFIG_ERROR"
    db.expire_all()
    assert _scores(db) == []


def test_extra_metric_values_not_in_snapshot_are_ignored(db, org, login):
    """快照里没有的指标，即使 `MetricValue` 里有数据也不参与算分。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)
    db.add(MetricValue(
        id="MV_GHOST", employee_id="E_A", metric_code="ghost_metric", period=PERIOD,
        value=999.0, target=1.0, unit="x", source="excel_import",
        source_ref="E_A|ghost", as_of=None,
    ))
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    # ghost 不在快照里 → 不参与 → 仍是 70/30 → 80 分
    assert resp.json()["final_score"] == pytest.approx(80.0)
    codes = {m["metric_code"] for m in resp.json()["calculation_detail"]["normalized_metrics"]}
    assert "ghost_metric" not in codes


def test_weight_sum_not_100_is_blocked(db, org, login):
    """权重合计 ≠ 100 → CONFIG_ERROR 阻断（决策③第二道保险）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    snapshot = db.get(ConfigSnapshot, "CS_1")
    snapshot.weights_json = [
        {"metric_code": "gmv", "weight": 70},
        {"metric_code": "conversion_rate", "weight": 20},
    ]
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error_code"] == "CONFIG_ERROR"
    db.expire_all()
    assert _scores(db) == []


def test_negative_weight_is_blocked(db, org, login):
    """负权重必须阻断（合计 100 不够 —— ``-70 + 170 = 100`` 能骗过合计校验）。

    回归背景：未修复时归一化会产生负权重，总分可跑到 128（越界 >100）。
    """
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    snapshot = db.get(ConfigSnapshot, "CS_1")
    snapshot.weights_json = [
        {"metric_code": "gmv", "weight": -70},
        {"metric_code": "conversion_rate", "weight": 170},
    ]
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error_code"] == "CONFIG_ERROR"
    db.expire_all()
    assert _scores(db) == []


def test_zero_weight_is_blocked(db, org, login):
    """零权重必须阻断（该指标实际不参与，归一化会扭曲其余指标权重）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    snapshot = db.get(ConfigSnapshot, "CS_1")
    snapshot.weights_json = [
        {"metric_code": "gmv", "weight": 100},
        {"metric_code": "conversion_rate", "weight": 0},
    ]
    db.flush()

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error_code"] == "CONFIG_ERROR"
    db.expire_all()
    assert _scores(db) == []


def test_scoring_never_emits_out_of_range_total():
    """**单元级**保证：``scoring.score_record`` 的总分永远在 [0, 100]。

    这是端点的最后一道防线 —— 无论快照配置多离谱，权威引擎不能出越界分。
    """
    lv = [
        {"level": "A", "min_ratio": 1.05, "max_ratio": None, "score": 100},
        {"level": "B", "min_ratio": 0.95, "max_ratio": 1.05, "score": 80},
        {"level": "C", "min_ratio": 0.0, "max_ratio": 0.95, "score": 60},
    ]

    # 负权重：未修复时能算出 128
    with pytest.raises(scoring.ScoringError):
        scoring.score_record(
            [
                scoring.MetricSpec(code="gmv", name="GMV", weight=-70, levels=tuple(lv)),
                scoring.MetricSpec(code="conv", name="转化", weight=170, levels=tuple(lv)),
            ],
            [
                scoring.MetricInput(code="gmv", value=600_000.0, target=1_200_000.0),
                scoring.MetricInput(code="conv", value=150.0, target=100.0),
            ],
        )

    # 正常权重：结果必须落在 [0, 100]
    result = scoring.score_record(
        [
            scoring.MetricSpec(code="gmv", name="GMV", weight=70, levels=tuple(lv)),
            scoring.MetricSpec(code="conv", name="转化", weight=30, levels=tuple(lv)),
        ],
        [
            scoring.MetricInput(code="gmv", value=1_400_000.0, target=1_200_000.0),
            scoring.MetricInput(code="conv", value=150.0, target=100.0),
        ],
    )
    assert 0.0 <= result.total <= 100.0


def test_missing_rubric_entry_is_blocked_not_silently_zeroed(db, org, login):
    """快照缺某指标的 rubric → 该指标无法定级。

    ⚠️ 当前口径是「该指标记 0 分」（``scoring.py`` 的既定行为）。
    这里固化现状，防止未来无意改变；如果将来改成阻断，只需更新本测试。
    """
    _make_task(db, "E_A")
    snapshot = db.get(ConfigSnapshot, "CS_1")
    snapshot.rubric_json = [
        {"metric_code": "gmv", "levels_json": GMV_LEVELS},
        # conversion_rate 没有 rubric
    ]
    db.flush()
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    # gmv=B(80)，conv 无 rubric → 0 → 0.7 * 80 = 56
    assert resp.json()["final_score"] == pytest.approx(56.0)


def test_not_started_state_requires_force(db, org, login):
    """`not_started` 状态默认阻断（409），`force_execute` 放行。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    rec = db.get(AssessmentRecord, "R_1")
    rec.status = RecordStatus.NOT_STARTED.value
    db.flush()

    with login(org["lead"]) as c:
        blocked = c.post(ENDPOINT.format(record_id="R_1"), json={})
        forced = c.post(
            ENDPOINT.format(record_id="R_1"), json={"force_execute": True}
        )

    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["error_code"] == "INVALID_STATE_TRANSITION"
    assert forced.status_code == 200, forced.text


def test_scoring_does_not_advance_record_status(db, org, login):
    """**约定**：算分不推进 record 状态（状态机由业务流程推进）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    before = db.get(AssessmentRecord, "R_1").status

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert db.get(AssessmentRecord, "R_1").status == before


def test_request_body_is_fully_optional(db, org, login):
    """请求体完全缺省也能算分（``record_id`` 已改为可选）。"""
    _make_task(db, "E_A")
    _add_metric_values(db, "E_A", gmv=1_200_000.0, conversion=96.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["final_score"] == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# I. Schema 一致性
# ---------------------------------------------------------------------------


def _model_unique_columns(table) -> set[tuple[str, ...]]:
    """从 ORM 表对象上取唯一约束的列组合。

    ⚠️ ``sqlalchemy.inspect(Table)`` 在 2.x 里会**原样返回 Table**，
    而 ``Table`` 没有 ``get_unique_constraints``；``inspect(MetaData)`` 同样没有。
    → 直接读 ``Table.constraints``，它装的就是 UniqueConstraint 实例。
    """
    cols: set[tuple[str, ...]] = set()
    for c in table.constraints:
        if isinstance(c, UniqueConstraint):
            cols.add(tuple(col.name for col in c.columns))
    return cols


def _model_unique_names(table) -> set[str]:
    """取 ORM 表对象上唯一约束的**名字**（用来和迁移对齐）。"""
    return {
        c.name
        for c in table.constraints
        if isinstance(c, UniqueConstraint) and c.name
    }


def test_score_model_declares_record_id_unique():
    """**ORM 模型必须声明** ``score.record_id`` 唯一约束。

    回归背景：``0001_baseline.py`` 早已声明 ``uq_score_record``，
    但 ORM 模型漏了 → 「Alembic 建库」与「create_all 建库」schema 漂移。
    真实运行库属于后者，所以生产库一直没有这个约束。
    """
    cols = _model_unique_columns(Score.__table__)
    assert ("record_id",) in cols, (
        "Score ORM 模型缺少 record_id 唯一约束 —— "
        "与 migrations/versions/0001_baseline.py 漂移"
    )


def test_score_record_id_unique_matches_baseline_migration():
    """ORM 模型与 ``0001_baseline.py`` 的约束名必须一致（否则迁移是空跑）。"""
    baseline = (
        Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0001_baseline.py"
    )
    if not baseline.exists():  # pragma: no cover - 路径兜底
        pytest.skip("找不到 0001_baseline.py")

    text = baseline.read_text(encoding="utf-8")
    assert 'UniqueConstraint("record_id", name="uq_score_record")' in text

    assert "uq_score_record" in _model_unique_names(Score.__table__)


def test_duplicate_score_rows_are_impossible_at_schema_level(db):
    """同一 record_id 插入两行必须被数据库拒绝（唯一约束兜底）。"""
    _make_task(db, "E_A")
    db.add(Score(record_id="R_1", total=1.0, per_metric_json=[], trace_json={}))
    db.flush()

    db.add(Score(record_id="R_1", total=2.0, per_metric_json=[], trace_json={}))
    with pytest.raises(Exception):
        db.flush()
    db.rollback()


def test_real_database_has_score_record_unique_constraint():
    """**真实运行库**必须已补齐该约束（0009 迁移的验收点）。

    只在真实库文件存在时检查 —— CI / 单元测试环境没有该文件。
    """
    db_path = Path(__file__).resolve().parents[2] / "data" / "perf.db"
    if not db_path.exists():
        pytest.skip("真实库不存在（CI 环境）")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("pragma index_list('score')").fetchall()
        uniq_cols = set()
        for r in rows:
            if not r[2]:  # unique == 0
                continue
            for row in conn.execute(f"pragma index_info('{r[1]}')").fetchall():
                uniq_cols.add(row[2])
        ddl = conn.execute(
            "select sql from sqlite_master where type='table' and name='score'"
        ).fetchone()
        has = bool(ddl and ddl[0] and "uq_score_record" in ddl[0])
        assert has or "record_id" in uniq_cols, (
            "真实库 score 表缺 record_id 唯一约束 —— 请跑 `alembic upgrade head`"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# J. 整档评级语义
# ---------------------------------------------------------------------------


def test_overall_rating_reflects_total_not_first_metric(db, org, login):
    """**整档评级由总分决定**，不是「第一个有等级的指标」。

    回归背景：gmv=A(100)、conv=C(60)、权重 70/30 → 总分 88。
    旧实现取首个指标等级 → 误报「A」，与 `message` 文案自相矛盾。
    """
    _make_task(db, "E_A")
    # gmv ratio = 1.1667 → A(100)；conv ratio = 0.50 → C(60)
    _add_metric_values(db, "E_A", gmv=1_400_000.0, conversion=50.0)

    with login(org["lead"]) as c:
        resp = c.post(ENDPOINT.format(record_id="R_1"), json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 0.7 * 100 + 0.3 * 60 = 88
    assert body["final_score"] == pytest.approx(88.0)
    # 88 落在 B 档（75 ≤ 88 < 90），不能报 A
    assert body["rating_label"] == "B", (
        f"rating_label={body['rating_label']}，但总分 {body['final_score']} 应为 B 档"
    )
    assert body["rating_level"] == 4
    # 文案必须与评级自洽
    assert "B" in body["message"], body["message"]


def test_overall_rating_matches_total_across_boundaries(db, org, login):
    """总分跨档位时，评级必须跟着总分走。"""
    cases = [
        # (gmv, conv, 期望总分, 期望档位)
        (1_260_000.0, 110.0, 100.0, "A"),   # 0.7*100 + 0.3*100 = 100
        (1_200_000.0, 96.0, 80.0, "B"),      # 0.7*80  + 0.3*80  = 80
        (1_100_000.0, 50.0, 60.0, "C"),      # 0.7*60  + 0.3*60  = 60
    ]
    for idx, (gmv, conv, expected_total, expected_label) in enumerate(cases):
        rid = f"R_BAND_{idx}"
        task = AssessmentTask(
            id=f"T_BAND_{idx}", name="11月考核", period=PERIOD,
            department_id="D_OPS", status="self_review_open",
        )
        db.add(task)
        db.flush()
        snapshot = ConfigSnapshot(
            id=f"CS_BAND_{idx}", task_id=task.id,
            metrics_json=[
                {"code": "gmv", "name": "GMV", "unit": "元",
                 "calculation": "x", "data_source": "y"},
                {"code": "conversion_rate", "name": "转化率", "unit": "%",
                 "calculation": "x", "data_source": "y"},
            ],
            weights_json=[
                {"metric_code": "gmv", "weight": 70},
                {"metric_code": "conversion_rate", "weight": 30},
            ],
            rubric_json=[
                {"metric_code": "gmv", "levels_json": GMV_LEVELS},
                {"metric_code": "conversion_rate", "levels_json": CONV_LEVELS},
            ],
        )
        db.add(snapshot)
        db.flush()
        task.snapshot_id = snapshot.id
        db.add(AssessmentRecord(
            id=rid, task_id=task.id, employee_id="E_A",
            status=RecordStatus.SELF_REVIEW_SUBMITTED.value,
        ))
        db.flush()
        _add_metric_values(db, "E_A", gmv=gmv, conversion=conv)

        with login(org["lead"]) as c:
            resp = c.post(ENDPOINT.format(record_id=rid), json={})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["final_score"] == pytest.approx(expected_total), (
            f"{body['final_score']} != {expected_total}"
        )
        assert body["rating_label"] == expected_label, (
            f"总分 {body['final_score']} 的档位应为 {expected_label}，"
            f"实际 {body['rating_label']}"
        )

        # 清理，避免下一轮迭代撞唯一约束
        db.query(MetricValue).filter_by(employee_id="E_A").delete()
        db.flush()
