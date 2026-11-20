"""P2-2 趋势报表的**数据完整性**回归测试。

这个文件锁的是一个真实缺陷（跨端联调时发现）：

``submit_manager_review`` 明确允许「算分失败**不回滚**评分」——
主管的 ``confirmed_rating`` 已经落库，但 ``Score`` 行可能没写进去。

而 ``trend_report`` 的四个端点原本都用 ``JOIN(Score, ...)``（INNER JOIN），
于是这类记录在趋势 / 岗位横向 / 风险清单 / 薪酬导出里**整条消失**。

失效方式很隐蔽：用户看到的不是「这批人还没算分」，而是「这批人不见了」。
更糟的是 ``RISK_CONFIRMED_WITHOUT_BASIS`` 这个风险信号被写成了**死代码** ——
它恰好要检查「有评级但没依据」的记录，而那种记录正是没有 Score 行的。

这些断言把「LEFT JOIN + 缺分记录可见」钉死。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    Score,
)
from backend.app.main import app
from backend.app.services import trend_report


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Department(id="D1", name="研发"))
    session.add(HrDepartmentConfig(department_id="D1", active=True))
    session.add(
        Employee(id="HR", name="HR", department_id="D1",
                 employment_status="active", account_status="enabled")
    )
    session.add(
        Employee(id="E1", name="员工一", department_id="D1", position="开发",
                 employment_status="active", account_status="enabled")
    )
    session.add(
        Employee(id="E2", name="员工二", department_id="D1", position="开发",
                 employment_status="active", account_status="enabled")
    )
    session.add(AssessmentTask(id="T1", name="任务", period="202611"))
    session.flush()

    # E1：正常链路 —— 有评级**也有**分数
    session.add(
        AssessmentRecord(id="R1", task_id="T1", employee_id="E1",
                         status="manager_review_submitted", confirmed_rating="A")
    )
    # E2：算分失败但评分已落库 —— 有评级，**没有** Score 行
    session.add(
        AssessmentRecord(id="R2", task_id="T1", employee_id="E2",
                         status="manager_review_submitted", confirmed_rating="B")
    )
    session.add(
        Score(
            record_id="R1",
            total=88.0,
            per_metric_json=[
                {"metric": "M1", "ratio": 0.9, "level": "A"},
                {"metric": "M2", "ratio": 0.85, "level": "A"},
            ],
            trace_json={},
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, "HR")
    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 服务层
# ---------------------------------------------------------------------------

def test_period_trend_keeps_unscored_period(db):
    """跨周期趋势不能吞掉「有评级但没分数」的周期。"""
    result = trend_report.build_period_trend(db, employee_id="E2")

    assert result["periodCount"] == 1, "缺分数的周期应出现在趋势里"
    point = result["points"][0]
    assert point["total"] is None, "缺分数必须如实为 None，不能编造 0"
    assert point["rating"] == "B", "评级仍然要带出来"


def test_position_comparison_counts_unscored_member(db):
    """岗位横向：人数要算上缺分的人（人数是真实的）。"""
    result = trend_report.build_position_comparison(db, position="开发", period="202611")

    assert result["memberCount"] == 2, "缺分数的员工也在这个岗位里，人数不该少算"
    assert result["smallSample"] is True


def test_position_comparison_excludes_unscored_from_statistics(db):
    """缺分的人**不参与**均值/排名计算（没分就没有分）。"""
    # 先把岗位人数凑够，绕开小样本保护，才能看到分布
    for i in range(3, 8):
        db.add(Employee(id=f"E{i}", name=f"员工{i}", department_id="D1", position="开发",
                        employment_status="active", account_status="enabled"))
        db.add(AssessmentRecord(id=f"R{i}", task_id="T1", employee_id=f"E{i}",
                                status="manager_review_submitted", confirmed_rating="A"))
        db.add(Score(
            record_id=f"R{i}",
            total=80.0,
            per_metric_json=[{"metric": "M1", "ratio": 0.8, "level": "A"}],
            trace_json={},
        ))
    db.commit()

    result = trend_report.build_position_comparison(db, position="开发", period="202611")

    assert result["smallSample"] is False
    assert result["memberCount"] == 7, "人数含缺分的那一位"

    scored_totals = [m["total"] for m in result["members"] if m["total"] is not None]
    assert len(scored_totals) == 6, "只有 6 人有分数"
    assert result["distribution"]["average"] == pytest.approx(
        sum(scored_totals) / 6, abs=0.01
    ), "均值只能由有分数的人算，不能把缺分的当 0 拉低"

    unscored = [m for m in result["members"] if m["total"] is None]
    assert len(unscored) == 1, "缺分的人仍要出现在明细里"
    assert unscored[0]["rank"] is None, "没有分数就没有排名"


def test_risk_roster_flags_confirmed_without_basis(db):
    """★ 风险清单必须能报出「已确认评级但缺指标依据」。"""
    result = trend_report.build_risk_roster(db, period="202611")

    codes = {
        signal["code"]
        for item in result["items"]
        for signal in item["signals"]
    }
    assert "RISK_CONFIRMED_WITHOUT_BASIS" in codes, (
        "已确认评级但没有指标依据的记录，必须被风险清单报出来"
    )

    item = next(i for i in result["items"] if i["employee_id"] == "E2")
    signal = next(
        s for s in item["signals"] if s["code"] == "RISK_CONFIRMED_WITHOUT_BASIS"
    )
    assert signal["severity"] == "HIGH"
    assert signal["basis"], "每条风险信号都要带判断依据"


def test_payroll_export_keeps_unscored_rated_record(db):
    """薪酬导出不能吞掉「有评级但没分数」的记录。

    否则 HR 看到的导出行数比实际确认评级的记录少 —— 少掉的人就没人调薪了。
    """
    result = trend_report.build_payroll_export(db, period="202611")

    assert result["rowCount"] == 2, "两条都确认了评级，都该在导出里"

    unscored = [r for r in result["rows"] if r["total"] is None]
    assert len(unscored) == 1, "缺分数的那条要出现"
    assert unscored[0]["scored"] is False
    assert unscored[0]["rating"] == "B", "评级仍然要带出来"
    assert unscored[0]["coefficient"] is not None, "等级映射的系数仍然要给"


def test_payroll_export_warns_about_unscored_count(db):
    """⚠️ 缺分记录必须**显式警告**，不能混在正常行里。"""
    result = trend_report.build_payroll_export(db, period="202611")

    assert result["unscoredCount"] == 1
    assert "1" in result["disclaimer"]
    assert "缺分数" in result["disclaimer"], "免责说明要讲清楚缺分这件事"
    assert "补齐算分" in result["disclaimer"], "要给出下一步动作"


def test_payroll_export_all_scored_has_no_warning(db):
    """全部算分时不应出现缺分警告（别制造噪音）。"""
    db.add(Score(
        record_id="R2",
        total=75.0,
        per_metric_json=[{"metric": "M1", "ratio": 0.75, "level": "B"}],
        trace_json={},
    ))
    db.commit()

    result = trend_report.build_payroll_export(db, period="202611")

    assert result["unscoredCount"] == 0
    assert "缺分数" not in result["disclaimer"]


def test_payroll_export_still_hides_names_by_default(db):
    """脱敏红线不受本次修复影响：默认仍不导出姓名。"""
    result = trend_report.build_payroll_export(db, period="202611")
    assert all(not r.get("employee_name") for r in result["rows"])


# ---------------------------------------------------------------------------
# API 层
# ---------------------------------------------------------------------------

def test_api_payroll_export_reports_unscored(client):
    """API 层同样要报缺分。"""
    resp = client.get("/v1/trend-report/payroll-export?period=202611")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["rowCount"] == 2
    assert data["unscoredCount"] == 1
    assert any(r["scored"] is False for r in data["rows"])


def test_api_risk_roster_reports_confirmed_without_basis(client):
    """API 层的风险清单要能报出该信号（死代码修复的回归保护）。"""
    resp = client.get("/v1/trend-report/risk-roster?period=202611")
    assert resp.status_code == 200
    data = resp.json()["data"]

    codes = {s["code"] for item in data["items"] for s in item["signals"]}
    assert "RISK_CONFIRMED_WITHOUT_BASIS" in codes


def test_api_trend_report_endpoints_do_not_write(db, client):
    """修复不能引入任何写操作。"""
    before = {
        "records": db.query(AssessmentRecord).count(),
        "scores": db.query(Score).count(),
    }
    client.get("/v1/trend-report/trend/E2")
    client.get("/v1/trend-report/position?position=开发&period=202611")
    client.get("/v1/trend-report/risk-roster?period=202611")
    client.get("/v1/trend-report/payroll-export?period=202611")
    db.expire_all()

    after = {
        "records": db.query(AssessmentRecord).count(),
        "scores": db.query(Score).count(),
    }
    assert before == after, "趋势报表必须全程只读"
