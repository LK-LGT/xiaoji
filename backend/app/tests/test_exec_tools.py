"""P1-3 acceptance: 执行类指令 —— 两阶段确认 + 授权隔离 + 审计。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
    Score,
)
from backend.app.domain.notification_models import Notification
from backend.app.routes.exec_tools import router
from backend.app.services import exec_tool_service as svc
from backend.app.services.agent_tool_token import issue_agent_tool_token

EXEC_ALLOW = ["exec:exec_remind", "exec:exec_export", "exec:exec_stats"]


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_secret", "exec-tools-independent-test-secret")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Department(id="D", name="业务", leader_id="M"))
    db.flush()
    db.add(Employee(id="M", name="主管", department_id="D", employment_status="active", account_status="enabled"))
    db.add(Employee(id="E", name="员工", department_id="D", manager_id="M", employment_status="active", account_status="enabled"))
    db.add(AssessmentTask(id="T", name="考核", period="202611", department_id="D"))
    db.flush()
    db.add(AssessmentRecord(id="R", task_id="T", employee_id="E", status="not_started"))
    db.commit()

    app = FastAPI()
    app.include_router(router)

    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = session
    yield db, app, TestClient(app)
    db.close()
    engine.dispose()


def headers(employee_id="M", allow=None, task_id="T"):
    token = issue_agent_tool_token(
        employee_id=employee_id, task_id=task_id, allow=allow or EXEC_ALLOW, scope="department"
    )
    return {"Authorization": f"Bearer {token}"}


def test_prepare_has_no_side_effect(setup):
    """prepare 阶段只产出计划，不产生任何副作用。"""
    db, app, client = setup
    response = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"], "message": "请尽快完成"},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tool"] == "exec_remind"
    assert data["confirmToken"]
    assert data["impact"][0]["employee_id"] == "E"
    # 关键：没有任何通知被发出
    assert db.query(Notification).count() == 0


def test_confirm_executes_and_sends(setup):
    """confirm 阶段才真正执行。"""
    db, app, client = setup
    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=headers(),
    ).json()["data"]
    response = client.post(
        "/v1/exec-tools/exec_remind/confirm",
        json={"task_id": "T", "confirm_token": prepare["confirmToken"]},
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["sent"] == 1
    assert db.query(Notification).count() == 1


def test_confirm_without_prepare_is_rejected(setup):
    """没有有效令牌不能执行。"""
    db, app, client = setup
    response = client.post(
        "/v1/exec-tools/exec_remind/confirm",
        json={"task_id": "T", "confirm_token": "bogus.token"},
        headers=headers(),
    )
    assert response.status_code == 400
    assert db.query(Notification).count() == 0


def test_token_cannot_be_reused_for_another_tool(setup):
    """令牌绑定工具名，跨工具使用被拒。"""
    db, app, client = setup
    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=headers(),
    ).json()["data"]
    response = client.post(
        "/v1/exec-tools/exec_export/confirm",
        json={"task_id": "T", "confirm_token": prepare["confirmToken"]},
        headers=headers(),
    )
    assert response.status_code == 409


def test_token_cannot_be_used_by_another_user(setup):
    """令牌绑定用户，跨用户使用被拒。"""
    db, app, client = setup
    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=headers(employee_id="M"),
    ).json()["data"]
    response = client.post(
        "/v1/exec-tools/exec_remind/confirm",
        json={"task_id": "T", "confirm_token": prepare["confirmToken"]},
        headers=headers(employee_id="E"),
    )
    assert response.status_code == 403


def test_readonly_token_cannot_call_exec_tools(setup):
    """只读令牌不能调用执行类指令 —— 授权隔离。"""
    db, app, client = setup
    readonly = headers(allow=["get_metric_values"])
    response = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=readonly,
    )
    assert response.status_code == 403


def test_expired_token_is_rejected(setup, monkeypatch):
    """过期令牌被拒。"""
    db, app, client = setup
    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=headers(),
    ).json()["data"]
    # 把时钟推到令牌过期之后（用固定值避免自引用递归）
    monkeypatch.setattr(svc.time, "time", lambda: 4_102_444_800)
    response = client.post(
        "/v1/exec-tools/exec_remind/confirm",
        json={"task_id": "T", "confirm_token": prepare["confirmToken"]},
        headers=headers(),
    )
    assert response.status_code == 409


def test_cannot_remind_self(setup):
    """不能催办自己。"""
    db, app, client = setup
    response = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["M"]},
        headers=headers(),
    )
    assert response.status_code == 400


def test_cannot_remind_out_of_scope_employee(setup):
    """不能催办无可见范围的员工。"""
    db, app, client = setup
    db.add(Department(id="OTHER", name="其它"))
    db.add(Employee(id="X", name="他人", department_id="OTHER", employment_status="active", account_status="enabled"))
    db.commit()
    response = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["X"]},
        headers=headers(),
    )
    assert response.status_code in (400, 403)


def test_audit_written_for_prepare_and_confirm(setup):
    """prepare 与 confirm 都写审计。"""
    db, app, client = setup
    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=headers(),
    ).json()["data"]
    client.post(
        "/v1/exec-tools/exec_remind/confirm",
        json={"task_id": "T", "confirm_token": prepare["confirmToken"]},
        headers=headers(),
    )
    from backend.app.domain.models import AiToolCall

    calls = db.scalars(select(AiToolCall)).all()
    tools = {c.tool for c in calls}
    assert "exec_remind.prepare" in tools
    assert "exec_remind.confirm" in tools
    assert all(c.ok for c in calls)


def test_export_is_read_only_and_redacted(setup):
    """导出不改数据，且走脱敏。"""
    db, app, client = setup
    db.add(Score(record_id="R", total=88.0, per_metric_json=[], trace_json={"scheme_rating": "B"}))
    db.commit()
    # 员工 E 导出自己的数据
    prepare = client.post(
        "/v1/exec-tools/exec_export/prepare",
        json={"task_id": "T", "scope": "self"},
        headers=headers(employee_id="E"),
    ).json()["data"]
    response = client.post(
        "/v1/exec-tools/exec_export/confirm",
        json={"task_id": "T", "confirm_token": prepare["confirmToken"]},
        headers=headers(employee_id="E"),
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]["rows"]
    assert rows[0]["total"] == 88.0
    # 分数未被改动
    assert db.scalars(select(Score).where(Score.record_id == "R")).one().total == 88.0


# ---------------------------------------------------------------------------
# 参数化别名（前端用的就是这套路径）
# ---------------------------------------------------------------------------


def test_parameterized_prepare_and_confirm_match_hardcoded_ones(setup):
    """``/exec-tools/{tool}/prepare|confirm`` 与带工具名的路径**行为一致**。

    ⚠️ 前端（``frontend/src/api/p0p1.ts``）用的是参数化路径 ——
    新增执行类工具时前端零改动。两套路径必须等价。
    """
    db, app, client = setup
    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"], "message": "请尽快完成"},
        headers=headers(),
    )
    assert prepare.status_code == 200, prepare.text
    data = prepare.json()["data"]
    assert data["tool"] == "exec_remind"
    assert data["confirmToken"]

    response = client.post(
        "/v1/exec-tools/exec_remind/confirm",
        json={"task_id": "T", "confirm_token": data["confirmToken"]},
        headers=headers(),
    )
    assert response.status_code == 200, response.text


def test_parameterized_path_rejects_unknown_tool(setup):
    """未知工具名 → 404（不是 500）。"""
    db, app, client = setup
    response = client.post(
        "/v1/exec-tools/exec_unknown/prepare",
        json={"task_id": "T"},
        headers=headers(),
    )
    assert response.status_code == 404, response.text


def test_parameterized_confirm_rejects_token_of_another_tool(setup):
    """令牌绑定工具：跨工具确认仍被拒（409）—— 别名路径同样生效。"""
    db, app, client = setup
    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=headers(),
    ).json()["data"]
    response = client.post(
        "/v1/exec-tools/exec_export/confirm",
        json={"task_id": "T", "confirm_token": prepare["confirmToken"]},
        headers=headers(),
    )
    assert response.status_code == 409, response.text


def test_parameterized_prepare_has_no_side_effect(setup):
    """别名路径的 prepare 阶段同样**不产生副作用**。"""
    db, app, client = setup
    client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=headers(),
    )
    assert db.query(Notification).count() == 0
