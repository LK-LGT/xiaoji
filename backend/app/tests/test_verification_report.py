"""P0-3 acceptance: 智能核验 —— 自动生成中立报告 + 跨周期历史对比。"""
import pytest
from sqlalchemy import select

from backend.app.domain.models import AssessmentRecord, AssessmentTask, Score
from backend.app.domain.review_models import ReviewCase, VerificationReport
from backend.app.services import review_center as svc


def as_user(app, people, key):
    """切换当前登录用户（复用 app.dependency_overrides）。"""
    app.dependency_overrides[
        __import__(
            "backend.app.dependencies", fromlist=["get_current_user"]
        ).get_current_user
    ] = lambda: people[key]


def _new_case(db, people):
    row = svc.create_case(db, people["E"], target="score:REC", title="评分异议", reason="证据遗漏")
    db.commit()
    return row


def _accept(db, user, case):
    """受理案件（生成核验报告的前置状态）。"""
    ext = db.get(ReviewCase, case.id)
    svc.act_case(db, user, case, action="ACCEPT", revision=ext.revision, comment="受理并核查")
    db.commit()


def test_accept_auto_generates_neutral_report(env):
    """受理后自动产出核验报告，评审人无需再手工点击。"""
    db, people, app, client = env
    case = _new_case(db, people)
    as_user(app, people, "H")
    response = client.post(
        f"/v1/review-center/cases/{case.id}/actions",
        json={"revision": 1, "action": "ACCEPT", "comment": "受理并核查"},
    )
    assert response.status_code == 200, response.text
    reports = db.scalars(
        select(VerificationReport).where(VerificationReport.case_id == case.id)
    ).all()
    assert len(reports) == 1
    content = reports[0].content
    # 中立报告：包含复算事实、限制声明，且不改分
    assert content["engine"] == "deterministic-v1"
    assert any(f["type"] == "RULE_RECALCULATION" for f in content["facts"])
    assert "报告不作裁决，不自动改分" in content["limitations"]
    # 自动生成后分数不变
    assert db.scalar(select(Score).where(Score.record_id == "REC")).total == 70


def test_cross_period_history_comparison(env):
    """跨周期对比：列出同员工历史周期分数与趋势，仅作线索。"""
    db, people, app, client = env
    # 造一个上一周期记录
    db.add(AssessmentTask(id="T_PREV", name="上期考核", period="202610", snapshot_id="S"))
    db.add(AssessmentRecord(id="REC_PREV", task_id="T_PREV", employee_id="E", status="confirmed"))
    db.add(Score(record_id="REC_PREV", total=60.0, per_metric_json=[], trace_json={"scheme_rating": "C"}))
    db.commit()

    case = _new_case(db, people)
    _accept(db, people["H"], case)
    report = svc.generate_report(db, people["H"], case, db.get(ReviewCase, case.id).revision)
    db.commit()
    content = report.content
    cross = content["cross_period"]
    assert len(cross["items"]) == 1
    assert cross["items"][0]["period"] == "202610"
    assert cross["items"][0]["total"] == 60.0
    assert cross["items"][0]["delta"] == -10.0
    assert any(f["type"] == "CROSS_PERIOD_SCORE" for f in content["facts"])


def test_auto_generate_report_is_idempotent(env):
    """已存在报告时不重复生成。"""
    db, people, app, client = env
    case = _new_case(db, people)
    _accept(db, people["H"], case)
    svc.generate_report(db, people["H"], case, db.get(ReviewCase, case.id).revision)
    db.commit()
    again, reason = svc.auto_generate_report(db, people["H"], case)
    assert again is None
    assert "已存在" in reason


def test_auto_generate_report_without_snapshot_returns_reason(env):
    """历史案件缺快照时返回原因而非抛错（不阻断受理）。"""
    db, people, app, client = env
    case = _new_case(db, people)
    ext = db.get(ReviewCase, case.id)
    ext.snapshot = None
    db.commit()
    report, reason = svc.auto_generate_report(db, people["H"], case)
    assert report is None
    assert reason and "快照" in reason
