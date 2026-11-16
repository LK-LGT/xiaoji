"""阶段三 B-1/B-2：周期过滤口径修复 + 待评记录列表接口测试。

覆盖：
1. 周期格式归一化工具。
2. 考勤证据按考核周期过滤（**核心正确性**）。
3. 考核记录详情的周期口径。
4. 待评记录列表接口（权限矩阵 + 周期筛选）。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.domain.enums import EvidenceUsage
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    EmployeeDepartment,
    Evidence,
    HrDepartmentConfig,
    SystemAdmin,
)
from backend.app.main import app
from backend.app.services import evidence_service
from backend.app.services.period import (
    normalize_period,
    period_date_range,
    periods_overlap,
    to_dashed_period,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def org(db):
    """组织 + 考核任务 + 记录 + 跨月考勤证据。"""
    hr_dept = Department(id="D_HR", name="人力资源部", active=True)
    biz_dept = Department(id="D_BIZ", name="业务部", active=True)
    db.add(hr_dept)
    db.add(biz_dept)
    db.flush()

    hr = Employee(
        id="E_HR",
        name="HR负责人",
        department_id="D_HR",
        employment_status="active",
        account_status="enabled",
    )
    admin = Employee(
        id="E_ADMIN",
        name="系统管理员",
        employment_status="active",
        account_status="enabled",
    )
    leader = Employee(
        id="E_LEADER",
        name="部门负责人",
        department_id="D_BIZ",
        employment_status="active",
        account_status="enabled",
    )
    emp = Employee(
        id="E_EMP",
        name="普通员工",
        department_id="D_BIZ",
        manager_id="E_LEADER",
        employment_status="active",
        account_status="enabled",
    )
    other = Employee(
        id="E_OTHER",
        name="另一部门员工",
        department_id="D_OTHER",
        employment_status="active",
        account_status="enabled",
    )
    for row in (hr, admin, leader, emp, other):
        db.add(row)
    db.flush()

    db.add(EmployeeDepartment(employee_id="E_HR", department_id="D_HR", is_primary=True))
    db.add(EmployeeDepartment(employee_id="E_EMP", department_id="D_BIZ", is_primary=True))
    db.add(
        EmployeeDepartment(employee_id="E_LEADER", department_id="D_BIZ", is_primary=True)
    )
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.add(SystemAdmin(employee_id="E_ADMIN", active=True))

    # 考核任务：11 月周期
    task = AssessmentTask(
        id="TASK_202611",
        name="2026 年 11 月月度考核",
        period="202611",
        status="active",
    )
    db.add(task)
    db.flush()

    # 待评记录（自评已交）
    rec_pending = AssessmentRecord(
        id="REC_PENDING",
        task_id="TASK_202611",
        employee_id="E_EMP",
        status="self_review_submitted",
    )
    # 已评记录
    rec_done = AssessmentRecord(
        id="REC_DONE",
        task_id="TASK_202611",
        employee_id="E_LEADER",
        status="manager_review_submitted",
    )
    db.add(rec_pending)
    db.add(rec_done)
    db.flush()

    # 跨月考勤证据：10 月 + 11 月
    for period, count in (("2026-10", 3), ("2026-11", 1)):
        db.add(
            Evidence(
                employee_id="E_EMP",
                kind="attendance",
                summary=f"缺卡 {count} 次",
                source="attendance",
                source_ref=f"E_EMP|{period}|attendance|missing_punch",
                usage=EvidenceUsage.REFERENCE_ONLY.value,
                anomaly_type="missing_punch",
                anomaly_count=count,
            )
        )
    db.flush()

    return {
        "hr": hr,
        "admin": admin,
        "leader": leader,
        "emp": emp,
        "other": other,
        "task": task,
        "rec_pending": rec_pending,
        "rec_done": rec_done,
    }


@pytest.fixture()
def client(db, org):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    """把「角色名」翻译成真实的 HMAC 鉴权头。

    R3 安全加固前，这里靠 `settings.secure_auth=False` + `MOCK_TOKENS`
    走 Mock 鉴权分支。现在系统只认 `/v1/auth/*` 的 HMAC Token，
    所以这里按 employee_id 直接签发真实 Token。
    """
    from backend.app.routes.auth import _generate_token

    employee_id = {
        "hr_token_001": "E_HR",
        "admin_token_001": "E_ADMIN",
        "leader_token_001": "E_LEADER",
        "emp_token_001": "E_EMP",
    }[token]
    real_token, _refresh = _generate_token(employee_id, employee_id, 1)
    return {"Authorization": f"Bearer {real_token}"}


# ---------------------------------------------------------------------------
# 1. 周期格式归一化
# ---------------------------------------------------------------------------


def test_normalize_period_accepts_both_formats():
    assert normalize_period("202611") == "202611"
    assert normalize_period("2026-11") == "202611"


def test_normalize_period_rejects_invalid():
    assert normalize_period("abc") is None
    assert normalize_period("202613") is None
    assert normalize_period("20261") is None
    assert normalize_period("") is None
    assert normalize_period(None) is None


def test_to_dashed_period():
    assert to_dashed_period("202611") == "2026-11"
    assert to_dashed_period("2026-11") == "2026-11"
    assert to_dashed_period("bad") is None


def test_period_date_range():
    assert period_date_range("202611") == (date(2026, 11, 1), date(2026, 11, 30))
    assert period_date_range("202612") == (date(2026, 12, 1), date(2026, 12, 31))
    assert period_date_range("202602") == (date(2026, 2, 1), date(2026, 2, 28))


def test_periods_overlap():
    assert periods_overlap("202611", "2026-11") is True
    assert periods_overlap("202611", "202610") is False
    assert periods_overlap("202611", None) is False


# ---------------------------------------------------------------------------
# 2. 证据周期过滤（核心正确性）
# ---------------------------------------------------------------------------


def test_list_scoring_evidence_filters_by_compact_period(db, org):
    """紧凑周期 202611 必须只返回 2026-11 的证据。"""
    rows = evidence_service.list_scoring_evidence(db, "E_EMP", period="202611")
    assert len(rows) == 1
    assert rows[0].source_ref == "E_EMP|2026-11|attendance|missing_punch"
    assert rows[0].anomaly_count == 1


def test_list_scoring_evidence_filters_by_dashed_period(db, org):
    """带横杠周期 2026-11 结果必须一致。"""
    rows = evidence_service.list_scoring_evidence(db, "E_EMP", period="2026-11")
    assert len(rows) == 1
    assert rows[0].anomaly_count == 1


def test_list_scoring_evidence_without_period_returns_all(db, org):
    """不传周期时保持原行为（返回全部），避免误伤既有调用方。"""
    rows = evidence_service.list_scoring_evidence(db, "E_EMP")
    assert len(rows) == 2


def test_list_scoring_evidence_other_period_returns_its_own_rows(db, org):
    """筛 202610 应只返回 10 月那条，不含 11 月的。"""
    rows = evidence_service.list_scoring_evidence(db, "E_EMP", period="202610")
    assert len(rows) == 1
    assert rows[0].source_ref == "E_EMP|2026-10|attendance|missing_punch"
    assert rows[0].anomaly_count == 3


def test_list_scoring_evidence_invalid_period_does_not_wipe_results(db, org):
    """周期无法解析时不过滤，而不是把证据全清空。"""
    rows = evidence_service.list_scoring_evidence(db, "E_EMP", period="garbage")
    assert len(rows) == 2


def test_period_filter_keeps_non_attendance_evidence(db, org):
    """非考勤证据没有周期段，不应被周期过滤误伤。"""
    db.add(
        Evidence(
            employee_id="E_EMP",
            kind="manual_upload",
            summary="项目交付材料",
            source="manual_upload",
            source_ref=None,
            usage=EvidenceUsage.SCORING.value,
        )
    )
    db.flush()

    rows = evidence_service.list_scoring_evidence(db, "E_EMP", period="202611")
    kinds = {r.kind for r in rows}
    # 非考勤证据（无周期段）保留；考勤证据按周期过滤
    assert "manual_upload" in kinds
    assert "attendance" in kinds
    assert len([r for r in rows if r.kind == "attendance"]) == 1


# ---------------------------------------------------------------------------
# 3. 记录详情的周期口径
# ---------------------------------------------------------------------------


def test_record_detail_filters_attendance_by_task_period(client, db, org):
    """评 202611 时，记录详情不能返回 2026-10 的考勤证据。"""
    resp = client.get(
        f"/api/records/{org['rec_pending'].id}", headers=auth("emp_token_001")
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["period"] == "202611"
    assert body["period_display"] == "2026-11"

    ref = body["evidence"]["reference_only"]
    assert len(ref) == 1
    assert ref[0]["count"] == 1
    assert ref[0]["type"] == "missing_punch"
    assert ref[0]["disclaimer"] == evidence_service.ATTENDANCE_DISCLAIMER


def test_record_detail_404_for_unknown(client):
    resp = client.get("/api/records/NOPE", headers=auth("emp_token_001"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3b. v1 记录详情（前端实际调用的路径）
# ---------------------------------------------------------------------------


def test_v1_record_detail_filters_attendance_by_task_period(client, db, org):
    """GET /v1/assessment/records/{id} 同样必须按考核周期过滤。"""
    resp = client.get(
        f"/v1/assessment/records/{org['rec_pending'].id}",
        headers=auth("leader_token_001"),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["period"] == "202611"
    assert data["period_display"] == "2026-11"
    assert data["employee"]["id"] == "E_EMP"

    ref = data["evidence"]["reference_only"]
    assert len(ref) == 1
    assert ref[0]["count"] == 1
    assert ref[0]["type"] == "missing_punch"
    assert ref[0]["disclaimer"] == evidence_service.ATTENDANCE_DISCLAIMER


def test_v1_record_detail_requires_auth(client):
    resp = client.get("/v1/assessment/records/REC_PENDING")
    assert resp.status_code in (401, 403)


def test_v1_record_detail_forbidden_for_other_employee(client):
    """普通员工不能查看别人的记录详情。"""
    resp = client.get(
        "/v1/assessment/records/REC_DONE", headers=auth("emp_token_001")
    )
    assert resp.status_code == 403


def test_v1_record_detail_404_unknown(client):
    resp = client.get("/v1/assessment/records/NOPE", headers=auth("leader_token_001"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. 待评记录列表
# ---------------------------------------------------------------------------


def test_pending_records_requires_auth(client):
    resp = client.get("/v1/assessment/records/pending")
    assert resp.status_code in (401, 403)


def test_manager_sees_direct_report_pending_record(client):
    """主管能看到直属下属的待评记录。"""
    resp = client.get(
        "/v1/assessment/records/pending", headers=auth("leader_token_001")
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["record_id"] == "REC_PENDING"
    assert items[0]["employee"]["id"] == "E_EMP"
    assert items[0]["period"] == "202611"
    assert items[0]["period_display"] == "2026-11"


def test_pending_records_excludes_non_pending_status(client):
    """默认只返回待评分状态（self_review_submitted）。"""
    resp = client.get(
        "/v1/assessment/records/pending", headers=auth("leader_token_001")
    )
    statuses = {i["status"] for i in resp.json()["data"]["items"]}
    assert statuses == {"self_review_submitted"}


def test_pending_records_can_include_all_statuses(client):
    resp = client.get(
        "/v1/assessment/records/pending?include_all_statuses=true",
        headers=auth("leader_token_001"),
    )
    assert resp.status_code == 200
    ids = {i["record_id"] for i in resp.json()["data"]["items"]}
    assert "REC_DONE" in ids


def test_employee_sees_own_pending_record(client):
    """普通员工能看到自己的记录。"""
    resp = client.get(
        "/v1/assessment/records/pending?include_all_statuses=true",
        headers=auth("emp_token_001"),
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["record_id"] == "REC_PENDING"


def test_employee_cannot_see_others_records(client):
    """普通员工看不到别人的记录。"""
    resp = client.get(
        "/v1/assessment/records/pending?include_all_statuses=true",
        headers=auth("emp_token_001"),
    )
    ids = {i["record_id"] for i in resp.json()["data"]["items"]}
    assert "REC_DONE" not in ids


def test_hr_sees_all_records(client):
    """HR 能看到全部记录。"""
    resp = client.get(
        "/v1/assessment/records/pending?include_all_statuses=true",
        headers=auth("hr_token_001"),
    )
    assert resp.status_code == 200
    ids = {i["record_id"] for i in resp.json()["data"]["items"]}
    assert ids == {"REC_PENDING", "REC_DONE"}


def test_admin_cannot_see_business_records(client):
    """系统管理员**不参与**业务评分（阶段一决策），因此看不到考核记录。

    这是设计意图，不是缺陷：管理员只管系统配置，不自动获得员工绩效数据访问权。
    """
    resp = client.get(
        "/v1/assessment/records/pending?include_all_statuses=true",
        headers=auth("admin_token_001"),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 0
    assert resp.json()["data"]["items"] == []


def test_pending_records_filter_by_period_compact(client):
    resp = client.get(
        "/v1/assessment/records/pending?period=202611",
        headers=auth("leader_token_001"),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["period"] == "202611"
    assert len(resp.json()["data"]["items"]) == 1


def test_pending_records_filter_by_period_dashed(client):
    """带横杠格式的周期筛选结果必须与紧凑格式一致。"""
    resp = client.get(
        "/v1/assessment/records/pending?period=2026-11",
        headers=auth("leader_token_001"),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["period"] == "202611"
    assert len(resp.json()["data"]["items"]) == 1


def test_pending_records_filter_by_other_period(client):
    resp = client.get(
        "/v1/assessment/records/pending?period=202610",
        headers=auth("leader_token_001"),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


def test_pending_records_invalid_period_rejected(client):
    resp = client.get(
        "/v1/assessment/records/pending?period=garbage",
        headers=auth("leader_token_001"),
    )
    assert resp.status_code in (400, 422)


def test_pending_records_filter_by_task_id(client):
    resp = client.get(
        "/v1/assessment/records/pending?task_id=TASK_202611",
        headers=auth("leader_token_001"),
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) == 1


def test_visible_periods_endpoint(client):
    resp = client.get("/v1/assessment/records/periods", headers=auth("leader_token_001"))
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["period"] == "202611"
    assert items[0]["period_display"] == "2026-11"
    assert items[0]["record_count"] == 2
    assert items[0]["pending_count"] == 1


def test_visible_periods_requires_auth(client):
    resp = client.get("/v1/assessment/records/periods")
    assert resp.status_code in (401, 403)
