"""🔴 **「假成功」端点回归**（2026-11-26）。

⚠️ **本文件锁住的缺陷**
------------------------------------------------------------------
2026-11-26 全面审计发现一批端点**只返回结构完整的假对象**，
**数据库里什么都没写**：

============================  ======================================
端点                            原行为
============================  ======================================
``GET  /v1/department/templates``            恒返回 ``[]``
``POST /v1/department/templates``            返回 ``id: 0``
``GET  /v1/department/acceptance-standards`` 恒返回 ``[]``
``POST /v1/department/acceptance-standards`` 返回 ``id: 0``
``GET  /v1/department/score-rules``          恒返回 ``[]``
``POST /v1/department/score-rules``          返回 ``id: 0``
``POST /v1/department/score-rules/{id}/simulate`` 恒返回 ``score: 0``
``GET  /v1/department/todos``                恒返回空分页
``POST /v1/department/todos/{id}/remind``    只回字符串，次数恒为 0
``GET/POST /v1/department/export-tasks``     空壳任务
``GET  /v1/department/department-ledgers``   恒返回空分页
``GET  /v1/leader/team-ledgers``             恒返回空分页
``GET  /v1/leader/plan-adjustments``         恒返回 ``[]``
``POST /v1/employee/plan-adjustments``       返回 ``id: 0``
============================  ======================================

而前端**有完整的页面和按钮** ⇒ 用户点「保存」弹「已保存」，
**刷新后数据全没了**。

⚠️ **断言策略**
每条都**同时**断言「响应结构对」+「**库里真的有**」，
只断言响应结构是抓不住的 —— 空壳的响应结构也是对的。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.domain.config_models import (
    AcceptanceStandardRow,
    DeliverableTemplateRow,
    ExportTaskRow,
    PlanAdjustmentRow,
    ScoreRuleRow,
    TodoRow,
)
from backend.app.domain.models import Base, Department, Employee, MonthPlan, MonthPlanItem
from backend.app.main import app
from backend.app.routes.auth import _generate_token


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def org(db):
    db.add_all([
        Department(id="D_ROOT", name="总部", parent_id=None, active=True),
        Department(id="D_HR", name="人事行政部", parent_id="D_ROOT", active=True),
        Department(id="D_OTHER", name="其它部门", parent_id="D_ROOT", active=True),
    ])
    db.add_all([
        Employee(id="E_LEADER", name="主管", department_id="D_OTHER",
                 employment_status="active", account_status="enabled", token_version=1),
        Employee(id="E_EMP", name="员工甲", department_id="D_OTHER", manager_id="E_LEADER",
                 employment_status="active", account_status="enabled", token_version=1),
        Employee(id="E_EMP2", name="员工乙", department_id="D_OTHER", manager_id="E_LEADER",
                 employment_status="active", account_status="enabled", token_version=1),
    ])
    db.commit()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _headers(employee_id: str, token_version: int = 1) -> dict:
    token, _ = _generate_token(employee_id, "u", token_version)
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════
# 1. 交付物模板
# ═══════════════════════════════════════════════════════════════════════════


class TestDeliverableTemplate:
    def test_新建模板真的落库(self, client, db, org):
        resp = client.post(
            "/v1/department/templates",
            json={
                "orgId": "D_OTHER",
                "templateName": "剪辑成片",
                "evidenceType": "DOCUMENT",
                "required": True,
                "appliesTo": "MONTH_PLAN,RESULT",
                "description": "每条月计划都要有成片",
            },
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # ⚠️ 空壳特征：id 恒为 0
        assert data["id"] > 0, "返回的 id 不能是 0"
        assert data["templateName"] == "剪辑成片"

        row = db.get(DeliverableTemplateRow, data["id"])
        assert row is not None, "库里没有这个模板 ⇒ 假成功"
        assert row.template_name == "剪辑成片"
        assert row.required is True
        assert row.applies_to == "MONTH_PLAN,RESULT"

    def test_列表真的能查回来(self, client, db, org):
        client.post(
            "/v1/department/templates",
            json={"orgId": "D_OTHER", "templateName": "模板A", "evidenceType": "FILE"},
            headers=_headers("E_LEADER"),
        )
        listing = client.get("/v1/department/templates", headers=_headers("E_LEADER")).json()["data"]
        assert len(listing) == 1, "列表恒为空 ⇒ 假成功"
        assert listing[0]["templateName"] == "模板A"

    def test_空名称返回422(self, client, db, org):
        resp = client.post(
            "/v1/department/templates",
            json={"orgId": "D_OTHER", "templateName": ""},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 422, resp.text

    def test_更新真的落库(self, client, db, org):
        created = client.post(
            "/v1/department/templates",
            json={"orgId": "D_OTHER", "templateName": "旧名"},
            headers=_headers("E_LEADER"),
        ).json()["data"]

        resp = client.put(
            f"/v1/department/templates/{created['id']}",
            json={"templateName": "新名", "required": True},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["templateName"] == "新名"

        db.expire_all()
        assert db.get(DeliverableTemplateRow, created["id"]).template_name == "新名"

    def test_启用停用真的落库(self, client, db, org):
        created = client.post(
            "/v1/department/templates",
            json={"orgId": "D_OTHER", "templateName": "T"},
            headers=_headers("E_LEADER"),
        ).json()["data"]

        client.post(f"/v1/department/templates/{created['id']}/disable", headers=_headers("E_LEADER"))
        db.expire_all()
        assert db.get(DeliverableTemplateRow, created["id"]).status == "DISABLED"

        client.post(f"/v1/department/templates/{created['id']}/enable", headers=_headers("E_LEADER"))
        db.expire_all()
        assert db.get(DeliverableTemplateRow, created["id"]).status == "ENABLED"

    def test_更新不存在的模板返回404(self, client, db, org):
        resp = client.put(
            "/v1/department/templates/99999",
            json={"templateName": "x"},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 404, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 2. 验收标准
# ═══════════════════════════════════════════════════════════════════════════


class TestAcceptanceStandard:
    def test_新建标准真的落库(self, client, db, org):
        template = client.post(
            "/v1/department/templates",
            json={"orgId": "D_OTHER", "templateName": "T"},
            headers=_headers("E_LEADER"),
        ).json()["data"]

        resp = client.post(
            "/v1/department/acceptance-standards",
            json={
                "templateId": template["id"],
                "standardText": "画面无穿帮",
                "requireReviewPassed": True,
                "evidenceRequirement": "成片链接",
            },
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["id"] > 0
        # ⚠️ 模板名必须**实时查出来**，不能是空串
        assert data["templateName"] == "T"

        row = db.get(AcceptanceStandardRow, data["id"])
        assert row is not None, "库里没有这条标准 ⇒ 假成功"
        assert row.standard_text == "画面无穿帮"
        assert row.require_review_passed is True

    def test_引用数是真实统计(self, client, db, org):
        template = client.post(
            "/v1/department/templates",
            json={"orgId": "D_OTHER", "templateName": "T"},
            headers=_headers("E_LEADER"),
        ).json()["data"]
        for text in ("标准A", "标准B"):
            client.post(
                "/v1/department/acceptance-standards",
                json={"templateId": template["id"], "standardText": text},
                headers=_headers("E_LEADER"),
            )

        listing = client.get("/v1/department/templates", headers=_headers("E_LEADER")).json()["data"]
        assert listing[0]["referenceCount"] == 2, "引用数恒为 0 ⇒ 假数据"

    def test_模板不存在返回422(self, client, db, org):
        resp = client.post(
            "/v1/department/acceptance-standards",
            json={"templateId": 99999, "standardText": "x"},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 422, resp.text

    def test_标准内容为空返回422(self, client, db, org):
        template = client.post(
            "/v1/department/templates",
            json={"orgId": "D_OTHER", "templateName": "T"},
            headers=_headers("E_LEADER"),
        ).json()["data"]
        resp = client.post(
            "/v1/department/acceptance-standards",
            json={"templateId": template["id"], "standardText": "  "},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 422, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# 3. 参考分规则
# ═══════════════════════════════════════════════════════════════════════════


class TestScoreRule:
    def test_新建规则真的落库(self, client, db, org):
        resp = client.post(
            "/v1/department/score-rules",
            json={
                "orgId": "D_OTHER",
                "ruleName": "剪辑质量分",
                "ruleJson": {
                    "baseScore": 60,
                    "maxScore": 100,
                    "factors": [
                        {"code": "ON_TIME", "name": "按期交付", "metric": "COMPLETION_RATIO",
                         "threshold": 0.9, "weight": 0.6, "bonus": 10},
                    ],
                },
            },
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["id"] > 0

        row = db.get(ScoreRuleRow, data["id"])
        assert row is not None, "库里没有这条规则 ⇒ 假成功"
        assert row.rule_name == "剪辑质量分"
        assert row.rule_json["factors"][0]["code"] == "ON_TIME"

    def test_规则Json字符串也能接住(self, client, db, org):
        """⚠️ 前端传对象，但也可能传 JSON 字符串 ⇒ 两种都要接。"""
        resp = client.post(
            "/v1/department/score-rules",
            json={"orgId": "D_OTHER", "ruleName": "R", "ruleJson": '{"baseScore": 70}'},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["ruleJson"]["baseScore"] == 70

    def test_非法Json返回422(self, client, db, org):
        resp = client.post(
            "/v1/department/score-rules",
            json={"orgId": "D_OTHER", "ruleName": "R", "ruleJson": "{不是 JSON"},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 422, resp.text

    def test_模拟真的算出分数(self, client, db, org):
        """🔴 原来恒返回 ``score: 0`` ⇒ 模拟功能等于没有。"""
        rule = client.post(
            "/v1/department/score-rules",
            json={
                "orgId": "D_OTHER",
                "ruleName": "R",
                "ruleJson": {
                    "baseScore": 60,
                    "maxScore": 100,
                    "factors": [
                        {"code": "ON_TIME", "name": "按期交付", "metric": "COMPLETION_RATIO",
                         "threshold": 0.9, "weight": 1.0, "bonus": 20},
                        {"code": "NO_OVERDUE", "name": "无逾期", "metric": "OVERDUE_COUNT",
                         "threshold": 0, "weight": 1.0, "bonus": 10},
                    ],
                },
            },
            headers=_headers("E_LEADER"),
        ).json()["data"]

        resp = client.post(
            f"/v1/department/score-rules/{rule['id']}/simulate",
            json={"employeeName": "员工甲", "completionRatio": 0.95, "overdueCount": 0},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["score"] > 60, f"命中两个因子，分数应高于基准 60，实际 {data}"
        assert "按期交付" in data["hitFactors"]
        assert "无逾期" in data["hitFactors"]

    def test_模拟未命中时分数等于基准(self, client, db, org):
        rule = client.post(
            "/v1/department/score-rules",
            json={
                "orgId": "D_OTHER",
                "ruleName": "R",
                "ruleJson": {
                    "baseScore": 60,
                    "factors": [
                        {"code": "ON_TIME", "name": "按期交付", "metric": "COMPLETION_RATIO",
                         "threshold": 0.9, "weight": 1.0, "bonus": 20},
                    ],
                },
            },
            headers=_headers("E_LEADER"),
        ).json()["data"]

        resp = client.post(
            f"/v1/department/score-rules/{rule['id']}/simulate",
            json={"employeeName": "员工甲", "completionRatio": 0.5},
            headers=_headers("E_LEADER"),
        )
        data = resp.json()["data"]
        assert data["hitFactors"] == []
        assert data["score"] == 60

    def test_模拟不存在的规则返回404(self, client, db, org):
        resp = client.post(
            "/v1/department/score-rules/99999/simulate",
            json={"employeeName": "x"},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 404, resp.text

    def test_启用唯一(self, client, db, org):
        a = client.post("/v1/department/score-rules",
                         json={"orgId": "D_OTHER", "ruleName": "A"},
                         headers=_headers("E_LEADER")).json()["data"]
        b = client.post("/v1/department/score-rules",
                         json={"orgId": "D_OTHER", "ruleName": "B"},
                         headers=_headers("E_LEADER")).json()["data"]

        client.post(f"/v1/department/score-rules/{a['id']}/enable", headers=_headers("E_LEADER"))
        client.post(f"/v1/department/score-rules/{b['id']}/enable", headers=_headers("E_LEADER"))

        db.expire_all()
        assert db.get(ScoreRuleRow, a["id"]).status == "DISABLED"
        assert db.get(ScoreRuleRow, b["id"]).status == "ENABLED"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 待办
# ═══════════════════════════════════════════════════════════════════════════


def _make_todo(db, **kwargs) -> TodoRow:
    row = TodoRow(
        scene_code=kwargs.get("scene_code", "DAY_PLAN_REVIEW"),
        title=kwargs.get("title", "待办"),
        trigger_text=kwargs.get("trigger_text", "触发"),
        receiver_id=kwargs.get("receiver_id", "E_LEADER"),
        object_type=kwargs.get("object_type", "DAY_PLAN"),
        object_id=kwargs.get("object_id", "P1"),
        requirement=kwargs.get("requirement", "请处理"),
        route_hint=kwargs.get("route_hint", "/department/todo"),
        status=kwargs.get("status", "UNREAD"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class TestTodos:
    def test_列表真的能查到(self, client, db, org):
        _make_todo(db, title="日计划补审")
        data = client.get("/v1/department/todos", headers=_headers("E_LEADER")).json()["data"]
        assert data["total"] == 1, "列表恒为空 ⇒ 假成功"
        assert data["items"][0]["title"] == "日计划补审"

    def test_按状态筛选(self, client, db, org):
        _make_todo(db, title="A", status="UNREAD")
        _make_todo(db, title="B", status="DONE")
        data = client.get(
            "/v1/department/todos", params={"status": "DONE"}, headers=_headers("E_LEADER")
        ).json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["title"] == "B"

    def test_催办真的加次数(self, client, db, org):
        todo = _make_todo(db)
        client.post(f"/v1/department/todos/{todo.id}/remind", headers=_headers("E_LEADER"))
        db.expire_all()
        assert db.get(TodoRow, todo.id).remind_count == 1, "催办次数恒为 0 ⇒ 假成功"

        client.post(f"/v1/department/todos/{todo.id}/remind", headers=_headers("E_LEADER"))
        db.expire_all()
        assert db.get(TodoRow, todo.id).remind_count == 2

    def test_标记已读真的改状态(self, client, db, org):
        todo = _make_todo(db, status="UNREAD")
        client.post(f"/v1/department/todos/{todo.id}/read", headers=_headers("E_LEADER"))
        db.expire_all()
        assert db.get(TodoRow, todo.id).status == "READ"

    def test_升级真的改状态(self, client, db, org):
        todo = _make_todo(db)
        client.post(f"/v1/department/todos/{todo.id}/escalate", headers=_headers("E_LEADER"))
        db.expire_all()
        row = db.get(TodoRow, todo.id)
        assert row.status == "ESCALATED"
        assert "已标记升级处理" in row.impact

    def test_完成真的改状态(self, client, db, org):
        todo = _make_todo(db)
        client.post(f"/v1/department/todos/{todo.id}/done", headers=_headers("E_LEADER"))
        db.expire_all()
        assert db.get(TodoRow, todo.id).status == "DONE"

    def test_不存在的待办返回404(self, client, db, org):
        resp = client.post("/v1/department/todos/NOPE/remind", headers=_headers("E_LEADER"))
        assert resp.status_code == 404, resp.text

    def test_批量催办真的改数据(self, client, db, org):
        a = _make_todo(db)
        b = _make_todo(db)
        resp = client.post(
            "/v1/department/todos/batch-remind",
            json={"ids": [a.id, b.id]},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 2
        db.expire_all()
        assert db.get(TodoRow, a.id).remind_count == 1
        assert db.get(TodoRow, b.id).remind_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. 导出任务
# ═══════════════════════════════════════════════════════════════════════════


class TestExportTask:
    def test_新建导出任务真的落库并生成产物(self, client, db, org):
        resp = client.post(
            "/v1/department/export-tasks",
            json={
                "dimensionType": "EMPLOYEE",
                "periodType": "ALL",
                "formats": ["CSV"],
                "includeEvidence": False,
            },
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # ⚠️ 空壳特征：id 恒为 ""
        assert data["id"], "返回的任务 id 不能是空串"
        assert data["status"] == "SUCCESS"
        assert data["checksum"], "必须真的算校验值"

        row = db.get(ExportTaskRow, data["id"])
        assert row is not None, "库里没有这个任务 ⇒ 假成功"
        assert row.artifact_path

    def test_下载真的能拿到文件(self, client, db, org):
        created = client.post(
            "/v1/department/export-tasks",
            json={"dimensionType": "EMPLOYEE", "formats": ["CSV"]},
            headers=_headers("E_LEADER"),
        ).json()["data"]

        resp = client.get(
            f"/v1/department/export-tasks/{created['id']}/download",
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.content) > 0
        assert "员工甲" in resp.content.decode("utf-8-sig")

    def test_校验真的读回文件算SHA(self, client, db, org):
        created = client.post(
            "/v1/department/export-tasks",
            json={"dimensionType": "EMPLOYEE", "formats": ["CSV"]},
            headers=_headers("E_LEADER"),
        ).json()["data"]

        resp = client.post(
            f"/v1/department/export-tasks/{created['id']}/check",
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        db.expire_all()
        row = db.get(ExportTaskRow, created["id"])
        assert row.integrity_status == "COMPLETE", "校验没有真的读文件"

    def test_文件被篡改后校验必须失败(self, client, db, org):
        """🔴 校验必须**真的**能发现文件被动过。"""
        from pathlib import Path

        from backend.app.core.config import settings

        created = client.post(
            "/v1/department/export-tasks",
            json={"dimensionType": "EMPLOYEE", "formats": ["CSV"]},
            headers=_headers("E_LEADER"),
        ).json()["data"]

        db.expire_all()
        row = db.get(ExportTaskRow, created["id"])
        path = Path(settings.export_output_dir) / row.artifact_path
        path.write_bytes(b"tampered")

        client.post(
            f"/v1/department/export-tasks/{created['id']}/check",
            headers=_headers("E_LEADER"),
        )
        db.expire_all()
        row = db.get(ExportTaskRow, created["id"])
        assert row.integrity_status == "INCOMPLETE"
        assert row.status == "NEEDS_REVIEW"

    def test_下载信息真的指向真实产物(self, client, db, org):
        created = client.post(
            "/v1/department/export-tasks",
            json={"dimensionType": "EMPLOYEE", "formats": ["CSV"]},
            headers=_headers("E_LEADER"),
        ).json()["data"]

        info = client.get(
            f"/v1/department/export-tasks/{created['id']}/download-info",
            headers=_headers("E_LEADER"),
        ).json()["data"]
        assert info["taskId"] == created["id"]
        assert info["fileName"], "下载文件名不能为空"
        assert info["checksum"]

    def test_不存在的任务返回404(self, client, db, org):
        resp = client.get("/v1/department/export-tasks/NOPE", headers=_headers("E_LEADER"))
        assert resp.status_code == 404, resp.text

    def test_列表只返回自己的任务(self, client, db, org):
        client.post(
            "/v1/department/export-tasks",
            json={"dimensionType": "EMPLOYEE", "formats": ["CSV"]},
            headers=_headers("E_LEADER"),
        )
        mine = client.get("/v1/department/export-tasks", headers=_headers("E_LEADER")).json()["data"]
        theirs = client.get("/v1/department/export-tasks", headers=_headers("E_EMP")).json()["data"]
        assert mine["total"] == 1
        assert theirs["total"] == 0, "不该看到别人的导出任务"


# ═══════════════════════════════════════════════════════════════════════════
# 6. 计划调整
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanAdjustment:
    def _make_plan(self, db):
        plan = MonthPlan(
            id="MP1", owner_id="E_EMP", plan_month="2026-11", title="11月计划", status="submitted",
        )
        db.add(plan)
        db.flush()
        db.add(MonthPlanItem(plan_id="MP1", task_name="剪辑 10 条", sort_no=1))
        db.commit()
        return plan

    def test_员工发起真的落库(self, client, db, org):
        self._make_plan(db)
        resp = client.post(
            "/v1/employee/plan-adjustments",
            json={
                "planType": "MONTH",
                "planId": "MP1",
                "adjustmentType": "PAUSE",
                "reason": "项目延期",
                "impactText": "影响两条任务",
            },
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # ⚠️ 空壳特征：id 恒为 0
        assert data["id"], "返回的 id 不能是空"
        assert data["adjustmentNo"].startswith("ADJ-"), "必须有真实单号"

        row = db.get(PlanAdjustmentRow, data["id"])
        assert row is not None, "库里没有这条调整申请 ⇒ 假成功"
        assert row.owner_id == "E_EMP"
        assert row.status == "PENDING"
        assert row.reason == "项目延期"

    def test_单号单调递增(self, client, db, org):
        self._make_plan(db)
        first = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "MP1", "adjustmentType": "PAUSE", "reason": "a"},
            headers=_headers("E_EMP"),
        ).json()["data"]
        second = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "MP1", "adjustmentType": "CANCEL", "reason": "b"},
            headers=_headers("E_EMP"),
        ).json()["data"]
        assert first["adjustmentNo"] != second["adjustmentNo"]

    def test_计划不存在返回404(self, client, db, org):
        resp = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "NOPE", "adjustmentType": "PAUSE", "reason": "a"},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 404, resp.text

    def test_缺原因返回422(self, client, db, org):
        self._make_plan(db)
        resp = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "MP1", "adjustmentType": "PAUSE"},
            headers=_headers("E_EMP"),
        )
        assert resp.status_code == 422, resp.text

    def test_领导端列表真的能看到(self, client, db, org):
        self._make_plan(db)
        created = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "MP1", "adjustmentType": "PAUSE", "reason": "延期"},
            headers=_headers("E_EMP"),
        ).json()["data"]

        listing = client.get("/v1/leader/plan-adjustments", headers=_headers("E_LEADER")).json()["data"]
        assert len(listing) == 1, "领导端列表恒为空 ⇒ 假成功"
        assert listing[0]["id"] == created["id"]
        assert listing[0]["employeeName"] == "员工甲"

    def test_处理真的改状态并暂停原计划(self, client, db, org):
        """🔴 原来只回一个字符串 ⇒ 状态没变、原计划照跑。"""
        self._make_plan(db)
        created = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "MP1", "adjustmentType": "PAUSE", "reason": "延期"},
            headers=_headers("E_EMP"),
        ).json()["data"]

        resp = client.post(
            f"/v1/leader/plan-adjustments/{created['id']}/process",
            json={"action": "PAUSE", "comment": "同意暂停", "keepEvidenceChain": True},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "PAUSED"

        db.expire_all()
        row = db.get(PlanAdjustmentRow, created["id"])
        assert row.status == "PAUSED"
        assert row.operation_comment == "同意暂停"
        assert row.operator_id == "E_LEADER"
        # ⚠️ **原计划必须真的被暂停**
        assert db.get(MonthPlan, "MP1").status == "paused", "原计划状态没变 ⇒ 只是改了个标签"

    def test_重复处理返回422(self, client, db, org):
        self._make_plan(db)
        created = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "MP1", "adjustmentType": "PAUSE", "reason": "延期"},
            headers=_headers("E_EMP"),
        ).json()["data"]

        client.post(
            f"/v1/leader/plan-adjustments/{created['id']}/process",
            json={"action": "PAUSE", "comment": "同意"},
            headers=_headers("E_LEADER"),
        )
        resp = client.post(
            f"/v1/leader/plan-adjustments/{created['id']}/process",
            json={"action": "PAUSE", "comment": "再同意"},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 422, resp.text

    def test_处理不存在的申请返回404(self, client, db, org):
        resp = client.post(
            "/v1/leader/plan-adjustments/NOPE/process",
            json={"action": "PAUSE", "comment": "x"},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 404, resp.text

    def test_详情真的查得到(self, client, db, org):
        self._make_plan(db)
        created = client.post(
            "/v1/employee/plan-adjustments",
            json={"planType": "MONTH", "planId": "MP1", "adjustmentType": "PAUSE", "reason": "延期"},
            headers=_headers("E_EMP"),
        ).json()["data"]

        detail = client.get(
            f"/v1/leader/plan-adjustments/{created['id']}", headers=_headers("E_LEADER")
        ).json()["data"]
        assert detail["id"] == created["id"]
        assert detail["employeeName"] == "员工甲"
        assert detail["originalWorkContent"] == "剪辑 10 条"


# ═══════════════════════════════════════════════════════════════════════════
# 7. 台账
# ═══════════════════════════════════════════════════════════════════════════


class TestLedgers:
    def test_部门台账真的有数据(self, client, db, org):
        """🔴 原来恒返回空分页 ⇒ 台账页永远空白。"""
        db.add(MonthPlan(id="MP1", owner_id="E_EMP", plan_month="2026-11",
                         title="11月", status="submitted", completion_rate=80.0))
        db.commit()

        data = client.get("/v1/department/department-ledgers", headers=_headers("E_LEADER")).json()["data"]
        assert data["total"] >= 1, "台账恒为空 ⇒ 假成功"
        names = {item["employeeName"] for item in data["items"]}
        assert "员工甲" in names

    def test_团队台账只含下属(self, client, db, org):
        db.add(MonthPlan(id="MP1", owner_id="E_EMP", plan_month="2026-11",
                         title="11月", status="submitted"))
        db.add(MonthPlan(id="MP2", owner_id="E_EMP2", plan_month="2026-11",
                         title="11月", status="submitted"))
        db.commit()

        data = client.get("/v1/leader/team-ledgers", headers=_headers("E_LEADER")).json()["data"]
        assert data["total"] == 2, "团队台账恒为空 ⇒ 假成功"
        ids = {item["ownerId"] for item in data["items"]}
        assert ids == {"E_EMP", "E_EMP2"}

    def test_没有下属时团队台账为空(self, client, db, org):
        data = client.get("/v1/leader/team-ledgers", headers=_headers("E_EMP")).json()["data"]
        assert data["total"] == 0
        assert data["items"] == []

    def test_部门台账导出真的生成产物(self, client, db, org):
        db.add(MonthPlan(id="MP1", owner_id="E_EMP", plan_month="2026-11",
                         title="11月", status="submitted"))
        db.commit()

        resp = client.post(
            "/v1/department/department-ledgers/export",
            json={"dimensionType": "DEPARTMENT_LEDGER", "formats": ["CSV"]},
            headers=_headers("E_LEADER"),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["id"]
        assert data["status"] == "SUCCESS"

        download = client.get(
            f"/v1/department/export-tasks/{data['id']}/download",
            headers=_headers("E_LEADER"),
        )
        assert download.status_code == 200, download.text
        assert "员工甲" in download.content.decode("utf-8-sig")
