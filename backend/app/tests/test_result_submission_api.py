"""成果提交与审批 API 集成测试 —— A4 阶段。

覆盖：

1. **月成果提交**：选项接口、提交落库、版本递增、字段对齐前端契约。
2. **周成果提交**：选项接口、提交上下文、提交落库。
3. **证据附件**：上传落盘、元数据登记、下载、类型 / 大小校验。
4. **数据范围**：只能提交 / 查看自己的成果，跨人访问返回 404。
5. **状态守卫**：待审批期间不能重复提交；已审批的不能重复审批。
6. **成果审批**：主管 / 部门负责人通过 / 驳回；越权 403；未认证 401。
7. **禁止自审批**：不能审批自己提交的成果（403）。
8. **鉴权**：此前这些端点全部匿名可访问，现已统一要求登录（401）。
"""

from __future__ import annotations

import contextvars
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    Base,
    Department,
    Employee,
    MonthPlan,
    MonthPlanItem,
    WeekPlan,
    WeekPlanItem,
)
from backend.app.domain.result_models import (
    RESULT_PLAN_TYPE_MONTH,
    RESULT_PLAN_TYPE_WEEK,
    RESULT_STATUS_CONFIRMED,
    RESULT_STATUS_REJECTED,
    RESULT_STATUS_SUBMITTED,
    Result,
    ResultEvidence,
)
from backend.app.main import app
from backend.app.services import result_submission

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def org(db):
    """组织结构。

    - `E_LEAD`：主管 / 部门负责人，管理 `E_A`
    - `E_A`：直属下属，提交成果
    - `E_B`：同部门非直属
    - `E_X`：完全在管理范围外
    """
    d_root = Department(id="D_ROOT", name="业务部", active=True)
    d_other = Department(id="D_OTHER", name="其它部", active=True)
    db.add_all([d_root, d_other])
    db.flush()

    lead = Employee(
        id="E_LEAD",
        name="主管",
        department_id="D_ROOT",
        employment_status="active",
        account_status="enabled",
    )
    emp_a = Employee(
        id="E_A",
        name="员工甲",
        department_id="D_ROOT",
        employment_status="active",
        account_status="enabled",
    )
    emp_b = Employee(
        id="E_B",
        name="员工乙",
        department_id="D_ROOT",
        employment_status="active",
        account_status="enabled",
    )
    outsider = Employee(
        id="E_X",
        name="外部员工",
        department_id="D_OTHER",
        employment_status="active",
        account_status="enabled",
    )
    db.add_all([lead, emp_a, emp_b, outsider])
    db.flush()

    emp_a.manager_id = "E_LEAD"
    d_root.leader_id = "E_LEAD"
    db.flush()
    return {"lead": lead, "a": emp_a, "b": emp_b, "x": outsider}


def _client_for(db, user):
    """构造一个 TestClient。

    ⚠️ FastAPI 的 `dependency_overrides` 是**全局字典**，同时存在多个客户端
    fixture 时，后初始化的会覆盖先初始化的 —— 导致"主管客户端"实际以员工
    身份发起请求。

    解法：只覆盖一次 `get_current_user`，让它在**请求时**从 contextvar 里
    取当前用户；每个客户端通过自己的 `X-Test-User` 请求头切换身份。
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _current_user
    return TestClient(app)


def _current_user() -> Employee:
    """按请求头解析当前用户（测试专用）。"""
    user_id = _CURRENT_USER_ID.get()
    if not user_id:
        raise AssertionError("测试请求未指定用户身份")
    return _CURRENT_USER.get()


_CURRENT_USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "test_current_user_id", default=""
)
_CURRENT_USER: contextvars.ContextVar[Employee] = contextvars.ContextVar("test_current_user")


@pytest.fixture()
def login(db, org):
    """切换身份的工厂：``login(org["lead"])`` 返回一个客户端。

    每个客户端通过 `X-Test-User` 请求头携带身份，请求时再解析。
    """

    def _make(user: Employee) -> TestClient:
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = _current_user

        class _Client(TestClient):
            def request(self, method, url, **kwargs):  # type: ignore[override]
                headers = dict(kwargs.pop("headers", None) or {})
                headers["X-Test-User"] = user.id
                kwargs["headers"] = headers
                token = _CURRENT_USER_ID.set(user.id)
                obj = _CURRENT_USER.set(user)
                try:
                    return super().request(method, url, **kwargs)
                finally:
                    _CURRENT_USER_ID.reset(token)
                    _CURRENT_USER.reset(obj)

        return _Client(app)

    yield _make
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def employee_client(db, org, login):
    """以员工 E_A 身份登录。"""
    with login(org["a"]) as c:
        yield c


@pytest.fixture()
def leader_client(db, org, login):
    """以主管 E_LEAD 身份登录。"""
    with login(org["lead"]) as c:
        yield c


@pytest.fixture()
def outsider_client(db, org, login):
    """以管理范围外的 E_X 身份登录。"""
    with login(org["x"]) as c:
        yield c


@pytest.fixture()
def anon_client(db):
    """未认证客户端。

    ⚠️ 必须**显式移除** `get_current_user` 覆盖 —— 否则别的客户端 fixture
    先前留下的覆盖会让"匿名"请求实际带上身份。
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides.pop(get_current_user, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _month_plan(db, owner_id="E_A", month="2026-11"):
    plan = MonthPlan(owner_id=owner_id, plan_month=month, title="十一月计划", status="APPROVED")
    db.add(plan)
    db.flush()
    item = MonthPlanItem(
        plan_id=plan.id,
        task_name="完成订单模块",
        task_content="开发订单模块",
        deliverable="可运行的订单模块",
        performance_weight=10.0,
        estimated_value=100.0,
        status="APPROVED",
        sort_no=1,
    )
    db.add(item)
    db.flush()
    return plan, item


def _week_plan(db, owner_id="E_A"):
    plan = WeekPlan(
        owner_id=owner_id,
        title="本周计划",
        week_start=date(2026, 11, 2),
        week_end=date(2026, 11, 8),
        status="APPROVED",
    )
    db.add(plan)
    db.flush()
    item = WeekPlanItem(
        plan_id=plan.id,
        content="修复线上缺陷",
        deliverable="缺陷清零",
        sort_no=1,
    )
    db.add(item)
    db.flush()
    return plan, item


def _png_bytes() -> bytes:
    """最小合法 PNG。"""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ---------------------------------------------------------------------------
# 1. 鉴权：此前这些端点全部匿名可访问
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/v1/employee/results/submit/options"),
        ("get", "/v1/employee/week-results/submit/options"),
        ("get", "/v1/employee/results/R1"),
        ("get", "/v1/employee/week-results/R1"),
        ("get", "/v1/leader/week-result-approvals"),
        ("get", "/v1/department/week-result-approvals"),
    ],
)
def test_endpoints_require_authentication(anon_client, method, path):
    resp = getattr(anon_client, method)(path)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. 月成果提交
# ---------------------------------------------------------------------------


def test_month_result_submit_options_returns_real_plans(employee_client, db, org):
    _month_plan(db, org["a"].id)
    resp = employee_client.get("/v1/employee/results/submit/options")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["monthPlanOptions"]) == 1
    assert data["monthPlanOptions"][0]["planMonth"] == "2026-11"
    assert len(data["monthPlanItemOptions"]) == 1
    assert data["monthPlanItemOptions"][0]["taskName"] == "完成订单模块"
    assert data["acceptedFileTypes"]
    assert data["maxFileSizeMb"] == result_submission.MAX_FILE_SIZE_MB


# ═══════════════════════════════════════════════════════════════════════════
# BUG-R6-19 回归：前后端「允许的文件类型」口径分裂
#
# 后端一直用 **MIME**（``application/pdf``），前端却按 **扩展名** 校验
# （``pdf`` / ``docx`` / ``png``），且拼 ``accept`` 属性时统一加了前导点
# → 得到 ``.application/pdf`` 这种**无效**的 accept。
#
# 后果：
#   1. 文件选择器不按类型过滤；
#   2. 前端 ``validateFile`` 拿扩展名去比 MIME 列表 →
#      **合法 PDF 也被判「文件类型不支持」**，根本传不上去。
# ═══════════════════════════════════════════════════════════════════════════


def test_submit_options_returns_file_extensions_not_mimes(employee_client):
    """``acceptedFileTypes`` 必须是**扩展名**，否则前端校验必然误杀。"""
    resp = employee_client.get("/v1/employee/results/submit/options")
    assert resp.status_code == 200
    data = resp.json()["data"]

    types = data["acceptedFileTypes"]
    assert types == list(result_submission.ACCEPTED_FILE_EXTENSIONS)
    # 不得出现 MIME（带斜杠）或带前导点的形式。
    for item in types:
        assert "/" not in item, f"acceptedFileTypes 不应包含 MIME：{item}"
        assert not item.startswith("."), f"acceptedFileTypes 不应带前导点：{item}"

    # MIME 口径单独放在 acceptedFileMimes。
    assert data["acceptedFileMimes"] == list(result_submission.ACCEPTED_FILE_TYPES)


def test_validate_evidence_accepts_extension_when_mime_is_wrong():
    """浏览器给的 MIME 不可靠时，只要扩展名合法就该放行。

    Windows 上 ``.docx`` 常被标成 ``application/octet-stream``，
    只认 MIME 会误杀合法文件。
    """
    result_submission.validate_evidence(
        "交付物.pdf", "application/octet-stream", 128
    )
    result_submission.validate_evidence(
        "方案.docx", "application/octet-stream", 128
    )
    result_submission.validate_evidence(
        "截图.png", "application/octet-stream", 128
    )


def test_validate_evidence_rejects_conflicting_mime_and_extension():
    """⚠️ 扩展名与**已知** MIME 冲突时必须拒绝。

    回归点：``.pdf`` 声明成 ``image/png``（或 ``text/plain``）
    属于「伪装」—— 内容类型与后缀对不上。
    原实现里这段判定**根本没写**，只记了条日志就放行
    （死变量 ``allowed_for_ext`` 就是那段缺失逻辑的残留），
    实际拦不住任何伪装文件。

    判定规则：MIME 已知 → 它对应的扩展名集合里必须包含本文件扩展名。
    """
    # .pdf 却声明成 PNG —— 冲突，拒绝。
    with pytest.raises(result_submission.ResultConflict):
        result_submission.validate_evidence(
            "伪装.pdf", "image/png", 128
        )

    # .txt 却声明成 PDF —— 冲突，拒绝。
    with pytest.raises(result_submission.ResultConflict):
        result_submission.validate_evidence(
            "伪装.txt", "application/pdf", 128
        )

    # .jpg / .jpeg 都属于 image/png 的合法伴生扩展名 → 不冲突，放行。
    result_submission.validate_evidence(
        "照片.jpg", "image/jpeg", 128
    )


def test_validate_evidence_accepts_unknown_mime_with_valid_extension():
    """MIME **未知**（不在映射表里）且扩展名合法 → 放行。

    未知 MIME 可能来自各平台/浏览器的怪癖，
    不应误杀合法文件；但「已知且冲突」必须拦（见上一条测试）。
    """
    result_submission.validate_evidence(
        "交付物.pdf", "text/html", 128
    )
    result_submission.validate_evidence(
        "交付物.pdf", "application/x-custom", 128
    )


def test_validate_evidence_accepts_mime_when_extension_is_odd():
    """扩展名异常但 MIME 合法时也应放行。"""
    result_submission.validate_evidence(
        "交付物", "application/pdf", 128
    )


def test_validate_evidence_rejects_unknown_type():
    """既不在扩展名白名单、也不在 MIME 白名单 → 拒绝。"""
    with pytest.raises(result_submission.ResultConflict):
        result_submission.validate_evidence(
            "恶意脚本.exe", "application/x-msdownload", 128
        )
    with pytest.raises(result_submission.ResultConflict):
        result_submission.validate_evidence(
            "网页.html", "text/html", 128
        )


def test_validate_evidence_rejects_oversize_and_empty():
    """大小与空内容仍按原规则拦截。"""
    with pytest.raises(result_submission.ResultConflict):
        result_submission.validate_evidence("a.pdf", "application/pdf", 0)
    with pytest.raises(result_submission.ResultConflict):
        result_submission.validate_evidence(
            "a.pdf",
            "application/pdf",
            (result_submission.MAX_FILE_SIZE_MB + 1) * 1024 * 1024,
        )


def test_month_result_submit_persists_result(employee_client, db, org):
    plan, item = _month_plan(db, org["a"].id)
    resp = employee_client.post(
        "/v1/employee/results/submit",
        data={
            "monthPlanId": plan.id,
            "monthPlanItemId": item.id,
            "completionRate": "80",
            "description": "已完成主要功能",
            "completionStatus": "MOSTLY_DONE",
            "remark": "还差一点",
            "personalPerformance": "8.5",
        },
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == RESULT_STATUS_SUBMITTED
    assert body["versionNo"] == "1"

    row = db.execute(select(Result)).scalar_one()
    assert row.owner_id == org["a"].id
    assert row.plan_type == RESULT_PLAN_TYPE_MONTH
    assert row.plan_id == plan.id
    assert row.plan_item_id == item.id
    assert row.completion_rate == 80.0
    assert row.description == "已完成主要功能"
    assert row.personal_performance == 8.5
    assert row.submitted_at is not None


def test_month_result_submit_requires_matching_plan_owner(employee_client, db, org):
    """不能给自己的计划之外的计划提交成果。"""
    other_plan, other_item = _month_plan(db, org["b"].id)
    resp = employee_client.post(
        "/v1/employee/results/submit",
        data={
            "monthPlanId": other_plan.id,
            "monthPlanItemId": other_item.id,
            "completionRate": "100",
        },
    )
    assert resp.status_code == 403
    assert db.execute(select(Result)).scalar_one_or_none() is None


def test_month_result_submit_missing_plan_returns_404(employee_client, db, org):
    resp = employee_client.post(
        "/v1/employee/results/submit",
        data={"monthPlanId": "NO_SUCH_PLAN", "completionRate": "100"},
    )
    assert resp.status_code == 404


def test_month_result_detail_is_owner_scoped(employee_client, outsider_client, db, org):
    plan, item = _month_plan(db, org["a"].id)
    employee_client.post(
        "/v1/employee/results/submit",
        data={
            "monthPlanId": plan.id,
            "monthPlanItemId": item.id,
            "completionRate": "100",
            "description": "做完了",
        },
    )
    result_id = db.execute(select(Result.id)).scalar_one()

    ok = employee_client.get(f"/v1/employee/results/{result_id}")
    assert ok.status_code == 200
    detail = ok.json()["data"]
    assert detail["status"] == RESULT_STATUS_SUBMITTED
    assert detail["planType"] == "MONTH"
    assert detail["planItemName"] == "完成订单模块"
    assert detail["completionRate"] == 100.0

    # 跨人访问 → 404（不泄露存在性）
    forbidden = outsider_client.get(f"/v1/employee/results/{result_id}")
    assert forbidden.status_code == 404


def test_month_result_duplicate_submit_conflicts(employee_client, db, org):
    """待审批期间不能重复提交。"""
    plan, item = _month_plan(db, org["a"].id)
    data = {
        "monthPlanId": plan.id,
        "monthPlanItemId": item.id,
        "completionRate": "100",
    }
    assert employee_client.post("/v1/employee/results/submit", data=data).status_code == 200
    second = employee_client.post("/v1/employee/results/submit", data=data)
    assert second.status_code == 400
    assert "重复提交" in second.json()["detail"]


def test_month_result_version_increments_after_rejection(employee_client, db, org):
    """驳回后可重新提交，版本号递增。"""
    plan, item = _month_plan(db, org["a"].id)
    data = {
        "monthPlanId": plan.id,
        "monthPlanItemId": item.id,
        "completionRate": "100",
    }
    employee_client.post("/v1/employee/results/submit", data=data)

    result = db.execute(select(Result)).scalar_one()
    result.status = RESULT_STATUS_REJECTED
    db.flush()

    resp = employee_client.post("/v1/employee/results/submit", data=data)
    assert resp.status_code == 200
    assert resp.json()["data"]["versionNo"] == "2"
    assert db.execute(select(Result)).scalars().all().__len__() == 2


# ---------------------------------------------------------------------------
# 3. 周成果提交
# ---------------------------------------------------------------------------


def test_week_result_submit_options_returns_plans_and_tasks(employee_client, db, org):
    _week_plan(db, org["a"].id)
    resp = employee_client.get("/v1/employee/week-results/submit/options")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["plans"]) == 1
    option = data["plans"][0]
    assert option["weekPlanTitle"] == "本周计划"
    assert option["weekStart"] == "2026-11-02"
    assert option["task"]["content"] == "修复线上缺陷"
    assert option["latestResult"] is None


def test_week_result_submit_context_and_submit(employee_client, db, org):
    plan, item = _week_plan(db, org["a"].id)

    ctx = employee_client.get(
        f"/v1/employee/week-plans/{plan.id}/items/{item.id}/results/submit-context"
    )
    assert ctx.status_code == 200
    assert ctx.json()["data"]["task"]["content"] == "修复线上缺陷"

    resp = employee_client.post(
        f"/v1/employee/week-plans/{plan.id}/items/{item.id}/results/submit",
        data={
            "completionRate": "100",
            "completionStatus": "DONE",
            "deliverable": "缺陷已清零",
            "remark": "顺利",
            "content": "修复完成",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == RESULT_STATUS_SUBMITTED

    row = db.execute(select(Result)).scalar_one()
    assert row.plan_type == RESULT_PLAN_TYPE_WEEK
    assert row.plan_id == plan.id
    assert row.deliverable == "缺陷已清零"
    assert row.content == "修复完成"


def test_week_result_detail_round_trip(employee_client, db, org):
    plan, item = _week_plan(db, org["a"].id)
    employee_client.post(
        f"/v1/employee/week-plans/{plan.id}/items/{item.id}/results/submit",
        data={
            "completionRate": "100",
            "completionStatus": "DONE",
            "deliverable": "交付物",
            "content": "内容",
        },
    )
    result_id = db.execute(select(Result.id)).scalar_one()
    resp = employee_client.get(f"/v1/employee/week-results/{result_id}")
    assert resp.status_code == 200
    record = resp.json()["data"]
    assert record["deliverable"] == "交付物"
    assert record["status"] == RESULT_STATUS_SUBMITTED
    assert record["evidences"] == []


# ---------------------------------------------------------------------------
# 4. 证据附件
# ---------------------------------------------------------------------------


def test_month_result_with_evidence_uploads_and_downloads(
    employee_client, db, org, tmp_path, monkeypatch
):
    monkeypatch.setattr(result_submission, "EVIDENCE_DIR", tmp_path / "evidence")
    plan, item = _month_plan(db, org["a"].id)
    resp = employee_client.post(
        "/v1/employee/results/submit",
        data={"monthPlanId": plan.id, "monthPlanItemId": item.id, "completionRate": "100"},
        files={"file": ("shot.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    assert resp.status_code == 200

    ev = db.execute(select(ResultEvidence)).scalar_one()
    assert ev.file_name == "shot.png"
    assert ev.file_type == "image/png"
    assert ev.file_size > 0
    assert ev.checksum
    assert ev.result_id == db.execute(select(Result.id)).scalar_one()

    result = db.execute(select(Result)).scalar_one()
    assert result.evidence_status == "UPLOADED"

    dl = employee_client.get(f"/v1/employee/results/{result.id}/evidence/{ev.id}")
    assert dl.status_code == 200
    assert dl.content == _png_bytes()


def test_evidence_rejects_unsupported_type(employee_client, db, org):
    plan, item = _month_plan(db, org["a"].id)
    resp = employee_client.post(
        "/v1/employee/results/submit",
        data={"monthPlanId": plan.id, "monthPlanItemId": item.id, "completionRate": "100"},
        files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
    )
    assert resp.status_code == 400
    assert db.execute(select(ResultEvidence)).scalar_one_or_none() is None


def test_evidence_rejects_oversize_file(employee_client, db, org, monkeypatch):
    monkeypatch.setattr(result_submission, "MAX_FILE_SIZE_MB", 0)
    plan, item = _month_plan(db, org["a"].id)
    resp = employee_client.post(
        "/v1/employee/results/submit",
        data={"monthPlanId": plan.id, "monthPlanItemId": item.id, "completionRate": "100"},
        files={"file": ("big.png", io.BytesIO(b"0" * 1024), "image/png")},
    )
    assert resp.status_code == 400


def test_evidence_download_is_owner_scoped(
    employee_client, outsider_client, db, org, tmp_path, monkeypatch
):
    monkeypatch.setattr(result_submission, "EVIDENCE_DIR", tmp_path / "evidence")
    plan, item = _month_plan(db, org["a"].id)
    employee_client.post(
        "/v1/employee/results/submit",
        data={"monthPlanId": plan.id, "monthPlanItemId": item.id, "completionRate": "100"},
        files={"file": ("shot.png", io.BytesIO(_png_bytes()), "image/png")},
    )
    ev = db.execute(select(ResultEvidence)).scalar_one()
    result_id = db.execute(select(Result.id)).scalar_one()

    forbidden = outsider_client.get(f"/v1/employee/results/{result_id}/evidence/{ev.id}")
    assert forbidden.status_code == 404


# ---------------------------------------------------------------------------
# 5. 成果审批
# ---------------------------------------------------------------------------


def _submit_week_result(client, db, owner_id="E_A"):
    plan, item = _week_plan(db, owner_id)
    client.post(
        f"/v1/employee/week-plans/{plan.id}/items/{item.id}/results/submit",
        data={
            "completionRate": "100",
            "completionStatus": "DONE",
            "deliverable": "交付物",
            "content": "内容",
        },
    )
    return db.execute(select(Result)).scalar_one()


def test_leader_approves_week_result(leader_client, employee_client, db, org):
    result = _submit_week_result(employee_client, db, org["a"].id)
    resp = leader_client.post(
        f"/v1/leader/week-result-approvals/{result.id}/approve",
        json={"comment": "同意"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "APPROVED"

    db.expire_all()
    row = db.get(Result, result.id)
    assert row.status == RESULT_STATUS_CONFIRMED
    assert row.approver_id == org["lead"].id
    assert row.approval_comment == "同意"
    assert row.approved_at is not None


def test_leader_rejects_week_result(leader_client, employee_client, db, org):
    result = _submit_week_result(employee_client, db, org["a"].id)
    resp = leader_client.post(
        f"/v1/leader/week-result-approvals/{result.id}/reject",
        json={"comment": "证据不足"},
    )
    assert resp.status_code == 200
    db.expire_all()
    row = db.get(Result, result.id)
    assert row.status == RESULT_STATUS_REJECTED
    assert row.approval_comment == "证据不足"


def test_department_approves_week_result(leader_client, employee_client, db, org):
    """部门侧与主管侧是同一批端点（决策 D2/D4）。"""
    result = _submit_week_result(employee_client, db, org["a"].id)
    resp = leader_client.post(
        f"/v1/department/week-result-approvals/{result.id}/approve",
        json={"comment": "部门通过"},
    )
    assert resp.status_code == 200
    db.expire_all()
    assert db.get(Result, result.id).status == RESULT_STATUS_CONFIRMED


def test_outsider_cannot_approve_week_result(outsider_client, employee_client, db, org):
    result = _submit_week_result(employee_client, db, org["a"].id)
    resp = outsider_client.post(
        f"/v1/leader/week-result-approvals/{result.id}/approve",
        json={},
    )
    assert resp.status_code == 403
    db.expire_all()
    assert db.get(Result, result.id).status == RESULT_STATUS_SUBMITTED


def test_self_approval_is_forbidden(employee_client, db, org):
    """员工不能审批自己的成果（403）。

    `can_access_employee` 对本人恒返回 True，必须有专门的守卫。
    """
    result = _submit_week_result(employee_client, db, org["a"].id)
    resp = employee_client.post(
        f"/v1/leader/week-result-approvals/{result.id}/approve",
        json={},
    )
    assert resp.status_code == 403
    assert "自己" in resp.json()["detail"]
    db.expire_all()
    assert db.get(Result, result.id).status == RESULT_STATUS_SUBMITTED


def test_duplicate_approval_conflicts(leader_client, employee_client, db, org):
    result = _submit_week_result(employee_client, db, org["a"].id)
    assert (
        leader_client.post(
            f"/v1/leader/week-result-approvals/{result.id}/approve", json={}
        ).status_code
        == 200
    )
    again = leader_client.post(f"/v1/leader/week-result-approvals/{result.id}/approve", json={})
    assert again.status_code == 409


def test_approve_missing_result_returns_404(leader_client, db, org):
    resp = leader_client.post("/v1/leader/week-result-approvals/NOPE/approve", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. 审批列表
# ---------------------------------------------------------------------------


def test_leader_approval_list_includes_not_submitted(leader_client, employee_client, db, org):
    """审批页要能看到"谁还没交"（NOT_SUBMITTED）。"""
    _week_plan(db, org["a"].id)
    resp = leader_client.get("/v1/leader/week-result-approvals")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] >= 1
    statuses = {i["status"] for i in body["items"]}
    assert "NOT_SUBMITTED" in statuses


def test_leader_approval_list_shows_submitted(leader_client, employee_client, db, org):
    _submit_week_result(employee_client, db, org["a"].id)
    resp = leader_client.get("/v1/leader/week-result-approvals")
    body = resp.json()["data"]
    submitted = [i for i in body["items"] if i["status"] == "SUBMITTED"]
    assert len(submitted) == 1
    item = submitted[0]
    assert item["employeeName"] == "员工甲"
    assert item["weekStart"] == "2026-11-02"
    assert item["deliverable"] == "交付物"


def test_approval_list_filters_by_status(leader_client, employee_client, db, org):
    _submit_week_result(employee_client, db, org["a"].id)
    resp = leader_client.get("/v1/leader/week-result-approvals?status=SUBMITTED")
    body = resp.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["status"] == "SUBMITTED"


def test_approval_list_filters_by_keyword(leader_client, employee_client, db, org):
    _submit_week_result(employee_client, db, org["a"].id)
    hit = leader_client.get("/v1/leader/week-result-approvals?keyword=缺陷")
    assert hit.json()["data"]["total"] == 1
    miss = leader_client.get("/v1/leader/week-result-approvals?keyword=不存在的词")
    assert miss.json()["data"]["total"] == 0


def test_approval_list_paginates(leader_client, db, org):
    for _ in range(3):
        _week_plan(db, org["a"].id)
    resp = leader_client.get("/v1/leader/week-result-approvals?pageNo=1&pageSize=2")
    body = resp.json()["data"]
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["pageNo"] == 1
    assert body["pageSize"] == 2


def test_outsider_approval_list_is_empty(outsider_client, db, org):
    _week_plan(db, org["a"].id)
    resp = outsider_client.get("/v1/leader/week-result-approvals")
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 0
