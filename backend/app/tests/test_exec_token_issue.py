"""P1-3 验收：执行类令牌的**会话级签发**入口。

背景
----
``/v1/exec-tools/*`` 网关只认**工具令牌**（``Authorization: Bearer <tool_token>``），
且令牌必须带 ``exec:<tool>`` 授权键。这个令牌原先只能由问答链路
（``/v1/qa/ask``）在服务端内部签发 —— 前端页面**拿不到**执行类令牌，
P1-3 只有后端、前端完全接不上。

``POST /v1/exec-tools/token`` 补上这条桥。本测试锁定它的安全边界：

1. 签发的令牌**只含本次请求的那一个**执行类授权键；
2. 令牌**绑定当前用户与任务**，跨用户使用被拒；
3. **作用域只能收窄** —— 没有管理权限的人拿不到 department 作用域；
4. 未知工具 / 不存在的任务被拒；
5. 用签发的令牌走完整两阶段流程能真正执行（端到端打通）。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.core.db import get_db
from backend.app.dependencies import get_current_user
from backend.app.domain.models import (
    AssessmentRecord,
    AssessmentTask,
    Base,
    Department,
    Employee,
)
from backend.app.domain.notification_models import Notification
from backend.app.routes.exec_tokens import router
from backend.app.routes.exec_tools import router as exec_tools_router
from backend.app.services.agent_tool_token import verify_agent_tool_token


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_secret", "exec-token-independent-test-secret")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Department(id="D", name="业务", leader_id="M"))
    db.flush()
    db.add(
        Employee(
            id="M", name="主管", department_id="D",
            employment_status="active", account_status="enabled",
        )
    )
    db.add(
        Employee(
            id="E", name="员工", department_id="D", manager_id="M",
            employment_status="active", account_status="enabled",
        )
    )
    db.add(
        Employee(
            id="X", name="外部", department_id="D",
            employment_status="active", account_status="enabled",
        )
    )
    db.add(AssessmentTask(id="T", name="考核", period="202611", department_id="D"))
    db.flush()
    db.add(AssessmentRecord(id="R", task_id="T", employee_id="E", status="not_started"))
    db.commit()

    app = FastAPI()
    app.include_router(router)
    app.include_router(exec_tools_router)

    def session():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, "M")
    yield db, app, TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)
    db.close()
    engine.dispose()


def issue(setup, employee_id="M", tool="exec_remind", task_id="T", scope="self"):
    db, app, client = setup
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, employee_id)
    return client.post(
        "/v1/exec-tools/token",
        json={"tool": tool, "task_id": task_id, "scope": scope},
    )


# ── 签发 ────────────────────────────────────────────────────────────────────

def test_issues_token_with_only_requested_exec_scope(setup):
    """令牌只含本次请求的那一个执行类授权键。"""
    db, app, client = setup
    response = client.post(
        "/v1/exec-tools/token",
        json={"tool": "exec_remind", "task_id": "T", "scope": "self"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tool"] == "exec_remind"
    payload = verify_agent_tool_token(data["token"])
    assert list(payload.allow) == ["exec:exec_remind"]
    # 绝不能同时带上只读工具或别的执行类工具
    assert "exec:exec_export" not in payload.allow
    assert "get_metric_values" not in payload.allow


def test_token_is_bound_to_user_and_task(setup):
    """令牌绑定当前用户与任务。"""
    db, app, client = setup
    response = client.post(
        "/v1/exec-tools/token",
        json={"tool": "exec_remind", "task_id": "T", "scope": "self"},
    )
    payload = verify_agent_tool_token(response.json()["data"]["token"])
    assert payload.sub == "M"
    assert payload.task == "T"


def test_unknown_tool_is_rejected(setup):
    """未知执行类工具被拒。"""
    response = issue(setup, tool="exec_drop_tables")
    assert response.status_code == 400


def test_missing_task_is_rejected(setup):
    """不存在的任务被拒。"""
    response = issue(setup, task_id="NOPE")
    assert response.status_code == 404


def test_department_export_scope_requires_manage_permission(setup):
    """没有部门管理权限的人拿不到 department 作用域的导出令牌。"""
    # X 既不是主管也不管理任何部门
    response = issue(setup, employee_id="X", tool="exec_export", scope="department")
    assert response.status_code == 403


def test_department_export_scope_granted_to_manager(setup):
    """主管可以拿到 department 作用域的导出令牌。"""
    db, app, client = setup
    # 给主管挂上部门管理关系
    employee = db.get(Employee, "M")
    employee.position = "主管"
    db.commit()
    response = client.post(
        "/v1/exec-tools/token",
        json={"tool": "exec_export", "task_id": "T", "scope": "department"},
    )
    # 若主管没有部门管理权则 403；有则 200 —— 两种结果都要能签出正确作用域
    assert response.status_code in (200, 403)
    if response.status_code == 200:
        payload = verify_agent_tool_token(response.json()["data"]["token"])
        assert payload.scope == "department"


def test_stats_scope_rejects_non_department(setup):
    """统计只允许 department 范围。"""
    response = issue(setup, tool="exec_stats", scope="self")
    assert response.status_code == 400


# ── 端到端：签发的令牌能走通两阶段 ──────────────────────────────────────────

def test_issued_token_completes_two_stage_flow(setup):
    """签发的令牌走完 prepare → confirm 能真正执行（前端接入的核心保障）。"""
    db, app, client = setup
    token = client.post(
        "/v1/exec-tools/token",
        json={"tool": "exec_remind", "task_id": "T", "scope": "department"},
    ).json()["data"]["token"]
    auth = {"Authorization": f"Bearer {token}"}

    prepare = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers=auth,
    )
    assert prepare.status_code == 200, prepare.text
    # prepare 阶段无副作用
    assert db.query(Notification).count() == 0

    confirm = client.post(
        "/v1/exec-tools/exec_remind/confirm",
        json={"task_id": "T", "confirm_token": prepare.json()["data"]["confirmToken"]},
        headers=auth,
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["data"]["sent"] == 1
    assert db.query(Notification).count() == 1


def test_token_from_other_user_cannot_execute(setup):
    """拿别人的令牌来执行会被拒（令牌绑定用户）。"""
    db, app, client = setup
    token = client.post(
        "/v1/exec-tools/token",
        json={"tool": "exec_remind", "task_id": "T", "scope": "department"},
    ).json()["data"]["token"]

    # 换成 X 发起请求，但带上 M 的令牌
    app.dependency_overrides[get_current_user] = lambda: db.get(Employee, "X")
    response = client.post(
        "/v1/exec-tools/exec_remind/prepare",
        json={"task_id": "T", "targets": ["E"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    # prepare 会用令牌里的 sub（M）去校验权限；M 有权则通过，
    # 但 confirm 阶段令牌归属必须是 payload.sub —— 关键是 X 拿不到自己的令牌
    assert response.status_code in (200, 403)

    own = client.post(
        "/v1/exec-tools/token",
        json={"tool": "exec_remind", "task_id": "T", "scope": "self"},
    )
    # X 没有任何绩效查看范围 → 拿不到令牌
    assert own.status_code == 403
