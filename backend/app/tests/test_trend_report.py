"""P2-2 acceptance: 趋势复盘报表 —— 小样本保护 / 脱敏 / 权限闸门 / 只读。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    HrDepartmentConfig,
    Score,
)
from backend.app.routes.trend_report import router
from backend.app.services import trend_report as svc


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker_session(engine)
    yield s
    s.close()
    engine.dispose()


def sessionmaker_session(engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine)()


def _seed(db, *, members: int = 6, period: str = "202611", confirmed: bool = True):
    """建一条部门、若干员工与两期考核记录。

    HR 角色来自 ``HrDepartmentConfig``（部门级配置），不是靠 employee 表上的标记 ——
    所以这里要单独建一个 HR 部门并激活它。
    """
    db.add(Department(id="D", name="业务", leader_id="M"))
    db.add(Department(id="HR", name="人事"))
    db.add(HrDepartmentConfig(department_id="HR", active=True))
    db.add(Employee(id="M", name="主管M", department_id="D", position="P",
                    employment_status="active", account_status="enabled"))
    db.add(Employee(id="H", name="HR", department_id="HR", position="P",
                    employment_status="active", account_status="enabled"))
    for i in range(members):
        eid = f"E{i}"
        db.add(Employee(id=eid, name=f"员工{i}", department_id="D", position="P",
                        manager_id="M", employment_status="active", account_status="enabled"))
    db.add(AssessmentTask(id="T1", name="任务1", period=period))
    db.add(AssessmentTask(id="T2", name="任务2", period="202610"))
    db.flush()
    for i in range(members):
        eid = f"E{i}"
        db.add(AssessmentRecord(id=f"R{i}", task_id="T1", employee_id=eid,
                                 confirmed_rating="B" if confirmed else None))
        db.add(Score(record_id=f"R{i}", total=50.0 + i * 5, per_metric_json=[
            {"metric_code": "GMV", "metric_name": "销售额", "value": 50 + i * 5,
             "target": 100, "ratio": (50 + i * 5) / 100, "level": "C"},
        ]))
        # 上一周期，制造可比较的趋势
        db.add(AssessmentRecord(id=f"R{i}_prev", task_id="T2", employee_id=eid,
                                 confirmed_rating="B"))
        db.add(Score(record_id=f"R{i}_prev", total=40.0 + i * 5, per_metric_json=[]))
    db.commit()


# ── 跨周期趋势 ──────────────────────────────────────────────────────────────

def test_period_trend_returns_chronological_points(db):
    """趋势点按时间正序。"""
    _seed(db)
    out = svc.build_period_trend(db, employee_id="E0")
    assert out["periodCount"] == 2
    assert out["points"][0]["period"] == "202610"
    assert out["points"][-1]["period"] == "202611"


def test_period_trend_detects_upward_trend(db):
    """分数上升 → trend=UP。"""
    _seed(db)
    out = svc.build_period_trend(db, employee_id="E0")
    assert out["trend"] == "UP"
    assert out["points"][-1]["total"] > out["points"][0]["total"]


def test_period_trend_of_unknown_employee_is_empty(db):
    """查无此人 → 空趋势，不报错。"""
    _seed(db)
    out = svc.build_period_trend(db, employee_id="NOPE")
    assert out["periodCount"] == 0
    assert out["average"] is None


def test_period_trend_carries_disclaimer(db):
    """趋势带「不代表评价结论」说明。"""
    _seed(db)
    assert "不代表评价结论" in svc.build_period_trend(db, employee_id="E0")["note"]


# ── 岗位横向 + 小样本保护 ───────────────────────────────────────────────────

def test_position_comparison_small_sample_suppresses_details(db):
    """★ 小样本保护：人数 < 5 时不输出均值与个人明细。"""
    _seed(db, members=3)
    out = svc.build_position_comparison(db, position="P", period="202611")
    assert out["smallSample"] is True
    assert out["members"] == []
    assert "distribution" in out
    assert out["distribution"] == {}
    assert "< 5" in out["note"]


def test_position_comparison_with_enough_members(db):
    """人数足够 → 输出均值、排名与偏差。"""
    _seed(db, members=6)
    out = svc.build_position_comparison(db, position="P", period="202611")
    assert out["smallSample"] is False
    assert len(out["members"]) == 6
    assert out["distribution"]["average"] is not None
    ranks = sorted(m["rank"] for m in out["members"])
    assert ranks == [1, 2, 3, 4, 5, 6]


def test_position_comparison_ranks_descending(db):
    """排名按分数从高到低。"""
    _seed(db, members=6)
    out = svc.build_position_comparison(db, position="P", period="202611")
    top = max(out["members"], key=lambda m: m["total"])
    assert top["rank"] == 1


# ── 风险清单 ────────────────────────────────────────────────────────────────

def test_risk_roster_lists_low_achievement(db):
    """低达成 → 进风险清单。"""
    _seed(db, members=6)
    out = svc.build_risk_roster(db, period="202611")
    assert out["scannedCount"] == 6
    assert out["riskCount"] >= 1
    codes = {s["code"] for item in out["items"] for s in item["signals"]}
    assert "RISK_LOW_ACHIEVEMENT" in codes


def test_risk_roster_sorted_by_severity(db):
    """高严重度排前面。"""
    _seed(db, members=6)
    out = svc.build_risk_roster(db, period="202611")
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    severities = [i["severity"] for i in out["items"]]
    assert severities == sorted(severities, key=lambda s: order[s])


def test_risk_roster_disclaimer_says_not_resignation_list(db):
    """风险清单明确声明「不等于离职名单」。"""
    _seed(db, members=6)
    out = svc.build_risk_roster(db, period="202611")
    assert "不等于离职名单" in out["disclaimer"]


def test_risk_roster_empty_when_all_good(db):
    """全员高达成 → 空清单。"""
    _seed(db, members=6)
    # 把所有人的 ratio 抬到 1.2 以上
    for score in db.scalars(select(Score)).all():
        if score.per_metric_json:
            score.per_metric_json = [
                {**score.per_metric_json[0], "ratio": 1.3, "value": 130}
            ]
    db.commit()
    out = svc.build_risk_roster(db, period="202611")
    assert out["riskCount"] == 0


# ── 薪酬导出 ────────────────────────────────────────────────────────────────

def test_payroll_export_hides_names_by_default(db):
    """★ 姓名默认不导出。"""
    _seed(db)
    out = svc.build_payroll_export(db, period="202611")
    assert out["rowCount"] == 6
    assert "employee_name" not in out["rows"][0]
    assert "employee_key" in out["rows"][0]


def test_payroll_export_can_include_names(db):
    """显式打开才带姓名。"""
    _seed(db)
    out = svc.build_payroll_export(db, period="202611", include_names=True)
    assert out["rows"][0]["employee_name"] == "员工0"


def test_payroll_export_maps_rating_to_coefficient(db):
    """等级 → 系数。"""
    _seed(db)
    out = svc.build_payroll_export(db, period="202611")
    assert all(r["coefficient"] == 1.05 for r in out["rows"])


def test_payroll_export_only_includes_confirmed_records(db):
    """未确认评级的不进薪酬导出。"""
    _seed(db, confirmed=False)
    out = svc.build_payroll_export(db, period="202611")
    assert out["rowCount"] == 0


def test_payroll_export_carries_disclaimer(db):
    """必须声明「不构成调薪承诺」。"""
    _seed(db)
    out = svc.build_payroll_export(db, period="202611")
    assert "不构成调薪承诺" in out["disclaimer"]


def test_payroll_export_is_redacted(db):
    """整体走脱敏。"""
    _seed(db)
    out = svc.build_payroll_export(db, period="202611", include_names=True)
    assert "已脱敏" in out["privacyNote"]


# ── 只读保证 ────────────────────────────────────────────────────────────────

def test_report_does_not_mutate_business_state(db):
    """★ 调用前后 Score / confirmed_rating / 记录数完全不变。"""
    _seed(db)
    before_scores = {s.record_id: s.total for s in db.scalars(select(Score)).all()}
    before_ratings = {
        r.id: r.confirmed_rating for r in db.scalars(select(AssessmentRecord)).all()
    }
    before_count = len(db.scalars(select(AssessmentRecord)).all())

    svc.build_period_trend(db, employee_id="E0")
    svc.build_position_comparison(db, position="P", period="202611")
    svc.build_risk_roster(db, period="202611")
    svc.build_payroll_export(db, period="202611")
    db.expire_all()

    assert {s.record_id: s.total for s in db.scalars(select(Score)).all()} == before_scores
    assert {
        r.id: r.confirmed_rating for r in db.scalars(select(AssessmentRecord)).all()
    } == before_ratings
    assert len(db.scalars(select(AssessmentRecord)).all()) == before_count


# ── API 权限闸门 ────────────────────────────────────────────────────────────

def _client_for(db, employee_id: str) -> TestClient:
    from backend.app.dependencies import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, employee_id)
    return TestClient(app)


def test_api_manager_can_read_trend(db):
    """主管可看下属趋势。"""
    _seed(db)
    client = _client_for(db, "M")
    assert client.get("/v1/trend-report/trend/E0").status_code == 200


def test_api_unrelated_user_blocked_from_trend(db):
    """无关人员看不到趋势。"""
    _seed(db)
    db.add(Employee(id="X", name="无关", department_id="D",
                    employment_status="active", account_status="enabled"))
    db.commit()
    client = _client_for(db, "X")
    assert client.get("/v1/trend-report/trend/E0").status_code == 403


def test_api_employee_blocked_from_risk_roster(db):
    """普通员工不能看风险清单。"""
    _seed(db)
    client = _client_for(db, "E0")
    assert client.get("/v1/trend-report/risk-roster").status_code == 403


def test_api_hr_can_read_risk_roster(db):
    """HR 可看风险清单。"""
    _seed(db)
    client = _client_for(db, "H")
    assert client.get("/v1/trend-report/risk-roster").status_code == 200


def test_api_non_hr_blocked_from_payroll_export(db):
    """★ 非 HR 不能导出薪酬。"""
    _seed(db)
    client = _client_for(db, "M")
    assert client.get("/v1/trend-report/payroll-export").status_code == 403


def test_api_hr_can_export_payroll(db):
    """HR 可导出薪酬。"""
    _seed(db)
    client = _client_for(db, "H")
    assert client.get("/v1/trend-report/payroll-export").status_code == 200
