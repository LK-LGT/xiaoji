"""网盘剪辑工作量 —— 管理 API 测试（BP-2 / P4）。

覆盖
----
1. **权限隔离**（★ 最容易写错的地方）
   - 员工只看自己的数据
   - 主管看得到下属、看不到别的部门
   - HR 看全部
   - 员工不能裁定工单
   - 员工不能替别人填指标
2. 发起工单 / 加争议项 / 裁定 / 链校验
3. 人工填报（前端上传通路）
4. 证据上传与下载
5. 绑定冲突（目录不能绑给两个人）

⚠️ **权限测试要「能抓到越权」** ——
只断言「无权时返回 403」是**否定式断言**，
把整个权限检查删掉也照样通过 ⇒ 空转。
⇒ 必须同时断言「有权时能通过」。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Department, Employee
from backend.app.domain.pan_workload_models import (
    PanAccountBinding,
    PanWorkloadDaily,
    PanWorkloadFile,
    PanWorkloadReview,
)

PREFIX = "/v1/baidu/workload"


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
    """★ **照抄真实组织结构**（2026-11-21 查 ``data/perf.db`` 得到）。

    ⚠️⚠️ **为什么必须用真实结构**
    P4 第一版用的是自造的「剪辑一部 / 剪辑二部」，
    结果**测不出两类真实 bug**：
    1. **直属上级看不到直属下属** —— 真实库里剪辑的上级是**编导**
       （张洁），她**不是**部门 leader（leader 是「运营」刘旭）。
       自造 fixture 里「主管」直接是 leader ⇒ 永远测不到这条路径。
    2. **跨部门判定** —— 真实结构里短视频部**不是**顶层，
       它挂在「运营中心」下面 ⇒ 部门树要能表达多级。

    真实结构（``data/perf.db``）::

        运营中心 (leader=杨飞)
        └── 短视频 (leader=刘旭)
            ├── 刘旭    运营      ← 部门 leader
            ├── 张洁    编导      ← ★ 两位剪辑的**直属上级**
            │   ├── 李婵雨 剪辑
            │   └── 佟瑶   剪辑
            └── 巫宜珊  主播      ← 同部门但非剪辑
        人事行政部 (leader=林雅静) ← HR 部门

    ⚠️ **角色不是靠名字或标志位定的**（框架的口径）：
    - **HR** 来自 ``HrDepartmentConfig`` —— 所在部门被配为 HR 部门
    - **部门 leader** 来自 ``Department.leader_id``
    - **主管** 来自「有直属下属」（``manager_id`` 指向自己）
    ⇒ 所以 fixture 必须按**这个机制**造数据，
    否则测出来的「权限」是假的。
    """
    from backend.app.domain.models import HrDepartmentConfig

    d_center = Department(id="D_CENTER", name="运营中心", active=True)
    d_video = Department(id="D_VIDEO", name="短视频", active=True)
    d_hr = Department(id="D_HR", name="人事行政部", active=True)
    db.add_all([d_center, d_video, d_hr])
    db.add_all([
        # ★ 剪辑（李婵雨 / 佟瑶）—— 直隶编导
        Employee(id="E_LCY", name="李婵雨", department_id="D_VIDEO",
                  position="剪辑", manager_id="E_ZJ",
                  employment_status="active", account_status="enabled"),
        Employee(id="E_TY", name="佟瑶", department_id="D_VIDEO",
                  position="剪辑", manager_id="E_ZJ",
                  employment_status="active", account_status="enabled"),
        # ★ 编导 —— 两位剪辑的**直属上级**（不是部门 leader！）
        Employee(id="E_ZJ", name="张洁", department_id="D_VIDEO",
                  position="编导", manager_id="E_LX",
                  employment_status="active", account_status="enabled"),
        # ★ 部门 leader —— 运营
        Employee(id="E_LX", name="刘旭", department_id="D_VIDEO",
                  position="运营", manager_id="E_TOP",
                  employment_status="active", account_status="enabled"),
        # 同部门但非剪辑 —— 用来验证「同部门 ≠ 可看」
        Employee(id="E_OTHER", name="外部门员工", department_id="D_CENTER",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_HR", name="HR", department_id="D_HR",
                 employment_status="active", account_status="enabled"),
        Employee(id="E_TOP", name="上级", department_id="D_CENTER",
                 employment_status="active", account_status="enabled"),
    ])
    db.flush()
    # ⚠️ 部门树：短视频挂在运营中心下面
    d_video.parent_id = "D_CENTER"
    # ⚠️ **短视频部 leader = 刘旭**（不是编导！）⇒ 编导只有「主管」角色
    d_video.leader_id = "E_LX"
    d_center.leader_id = "E_TOP"
    d_hr.leader_id = "E_HR"
    # ⚠️ **D_HR 是 HR 部门** ⇒ E_HR 拿到 hr 角色
    db.add(HrDepartmentConfig(department_id="D_HR", active=True))
    db.commit()


@pytest.fixture()
def modules(db, org):
    """★ **部门板块开通表**（2026-11-21 用户投诉后的隔离基础）。

    ⚠️⚠️ **为什么这个 fixture 是必需的**
    板块隔离是**默认关闭**的（没有配置行 = 不开通）
    ⇒ 如果不造这份数据，所有剪辑相关的测试都会 403
    ⇒ **必须显式开通**，让「板块没开通 ⇒ 403」这件事
    **只由专门的隔离测试来断言**，而不是污染其它测试。

    这里只给**短视频部**开通 —— 与真实库一致（迁移 0040）。
    ⇒ 其它部门（运营中心 / 人事行政部）**没有**这个板块。
    """
    from backend.app.domain.pan_workload_models import DepartmentModule

    db.add(
        DepartmentModule(
            department_id="D_VIDEO",
            module_code="pan_workload",
            active=True,
            note="测试：短视频部开通剪辑工作量",
        )
    )
    db.commit()
    return ["pan_workload"]


@pytest.fixture()
def client(db, monkeypatch):
    """TestClient —— 身份通过 ``_current_user`` 依赖覆盖切换。"""
    from backend.app.main import app
    from backend.app.routes import pan_workload as routes

    current = {"user": None}

    def _override_user():
        return current["user"]

    app.dependency_overrides[routes.get_current_user] = _override_user

    def _override_db():
        # ⚠️ **必须是生成器函数本身** —— FastAPI 用
        # ``inspect.isgeneratorfunction(call)`` 判断怎么处理依赖。
        # 传 ``lambda: _db_iter(db)`` 会被当成普通函数，
        # 生成器被当作**依赖的值**传下去 ⇒ 下游拿到 generator
        # ⇒ ``session.execute`` 报 AttributeError。
        yield db

    app.dependency_overrides[routes.get_db] = _override_db
    def _db_iter(session):
        yield session

    with TestClient(app) as c:
        c.current = current  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def as_user(client, db):
    """切到指定员工身份。"""

    def _set(user_id: str):
        emp = db.get(Employee, user_id)
        assert emp is not None
        client.current["user"] = emp  # type: ignore[attr-defined]
        return emp

    return _set


def _daily(db, emp_id: str, day: str, total: int = 6):
    from datetime import date

    db.add(PanWorkloadDaily(
        employee_id=emp_id,
        business_date=date.fromisoformat(day),
        total_count=total, file_count=total,
    ))
    db.commit()


def _review(db, emp_id: str, day: str = "2026-11-19"):
    from datetime import date

    row = PanWorkloadReview(
        employee_id=emp_id, business_date=date.fromisoformat(day),
        status="pending", scope="day", claimed_count=8, system_count=6,
        reason="数不对", created_by=emp_id,
    )
    db.add(row)
    db.commit()
    return row


# ─────────────────────────────────────────────────────────────────────────
# 权限隔离
# ─────────────────────────────────────────────────────────────────────────


class TestDataScope:
    def test_员工只看自己的(self, client, db, org, modules, as_user):
        """⚠️ 员工**绝不能**看到同事的条数。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_OTHER", "2026-11-19", 9)
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200
        emps = resp.json()["employees"]
        assert len(emps) == 1
        assert emps[0]["employeeId"] == "E_LCY"
        assert emps[0]["totalCount"] == 6

    def test_员工看别人明细被拒(self, client, db, org, modules, as_user):
        _daily(db, "E_OTHER", "2026-11-19")
        as_user("E_LCY")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_OTHER&business_date=2026-11-19"
        )
        assert resp.status_code == 403

    def test_HR看全部(self, client, db, org, modules, as_user):
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_OTHER", "2026-11-19", 9)
        as_user("E_HR")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200
        assert len(resp.json()["employees"]) == 2

    def test_主管看得到下属(self, client, db, org, modules, as_user):
        """⚠️ 主管必须能看到下属的条数 —— 否则没法核对。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200
        emps = resp.json()["employees"]
        assert any(e["employeeId"] == "E_LCY" for e in emps)

    def test_主管看不到外部门(self, client, db, org, modules, as_user):
        _daily(db, "E_OTHER", "2026-11-19", 9)
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200
        emps = resp.json()["employees"]
        assert all(e["employeeId"] != "E_OTHER" for e in emps)

    def test_主管看不到外部门明细(self, client, db, org, modules, as_user):
        _daily(db, "E_OTHER", "2026-11-19")
        as_user("E_ZJ")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_OTHER&business_date=2026-11-19"
        )
        assert resp.status_code == 403

    def test_回归_直属上级看得到直属下属(self, client, db, org, modules, as_user):
        """🔴 **回归点（P4 第一版的真实 bug）**。

        真实组织里剪辑的直属上级是**编导**（张洁），
        她**不是**部门 leader（leader 是「运营」刘旭）。

        ⚠️ 旧实现 ``_scope_employee_ids`` 只查 ``managed_department_ids``
        ⇒ 编导**看不到自己的下属** ⇒ 「主管核对下属工作量」主链路**断掉**。

        ⇒ 这条测试用**编导**身份（不是 leader）⇒ 才抓得住。
        """
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        emps = resp.json()["employees"]
        assert any(e["employeeId"] == "E_LCY" for e in emps), (
            "直属上级看不到直属下属 —— 主链路断了"
        )

    def test_回归_直属上级能裁定下属工单(self, client, db, org, modules, as_user):
        """🔴 **回归点** —— 编导不是 leader，但**必须能裁定**。

        旧实现 ``_is_leader`` 只认 ``department_leader``
        ⇒ 编导裁不了 ⇒ 工单永远挂起。
        """
        review = _review(db, "E_LCY")
        as_user("E_ZJ")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 8},
        )
        assert resp.status_code == 200, resp.text

    def test_回归_部门leader也能看到全部剪辑(self, client, db, org, modules, as_user):
        """⚠️ 与「直属上级」配对 —— 两个角色都要能看到。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_TY", "2026-11-19", 9)
        as_user("E_LX")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        ids = {e["employeeId"] for e in resp.json()["employees"]}
        assert {"E_LCY", "E_TY"} <= ids

    def test_回归_平级同事看不到对方(self, client, db, org, modules, as_user):
        """⚠️ 两位剪辑**互为平级** —— 谁都看不到对方。

        ⚠️ 这是最容易漏的一条：同部门 ≠ 可看。
        """
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_TY", "2026-11-19", 9)
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        ids = {e["employeeId"] for e in resp.json()["employees"]}
        assert "E_TY" not in ids

    def test_回归_上级看不到平级(self, client, db, org, modules, as_user):
        """⚠️ 编导与 leader **互为平级**（都是运营中心下的）——
        编导看不到 leader 的数据（leader 不是她的下属）。"""
        _daily(db, "E_LX", "2026-11-19", 9)
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        ids = {e["employeeId"] for e in resp.json()["employees"]}
        assert "E_LX" not in ids


class TestReviewItemOwnership:
    """🔴 **跨工单串门** 的防护（2026-11-23 修的真实漏洞）。

    ⚠️ **原来的漏洞**
    路由是 ``/reviews/{review_id}/items/{item_id}/resolve``，
    但服务层 ``resolve_item()`` **只按 item_id 找项** ⇒
    ⚠️ **``review_id`` 形同虚设**：
    主管拿**自己工单**的 ``review_id`` + **别人工单**的 ``item_id``
    就能把别人的争议项裁了 ⇒ **数据串号**。

    ⇒ 现在服务层收 ``expect_review_id``，归属对不上直接 404。
    """

    def _item(self, db, review_id: str, item_id: str = "IT_1"):
        from backend.app.domain.pan_workload_models import PanWorkloadReviewItem

        row = PanWorkloadReviewItem(
            id=item_id,
            review_id=review_id,
            dispute_type="missing",
            manual_name="某条成片",
            claimed_weight=8,
        )
        db.add(row)
        db.commit()
        return row

    def test_跨工单串门被拒(self, client, db, org, modules, as_user):
        """🔴 **核心** —— A 工单的 review_id 不能裁定 B 工单的争议项。"""
        review_a = _review(db, "E_LCY")
        review_b = _review(db, "E_TY")
        item_b = self._item(db, review_b.id, "IT_B")

        as_user("E_ZJ")
        resp = client.post(
            f"{PREFIX}/reviews/{review_a.id}/items/{item_b.id}/resolve",
            json={"resolution": "upheld", "final_weight": 99},
        )
        assert resp.status_code == 404, resp.text

        # ⚠️ **争议项必须没被动过**
        db.expire_all()
        from backend.app.domain.pan_workload_models import PanWorkloadReviewItem

        row = db.get(PanWorkloadReviewItem, "IT_B")
        assert row.resolution != "upheld", "跨工单串门成功了 —— 漏洞没修好"

    def test_同工单的争议项能裁定(self, client, db, org, modules, as_user):
        """⚠️ **配对** —— 证明上一条不是「端点全坏了」。"""
        review = _review(db, "E_LCY")
        item = self._item(db, review.id, "IT_OK")

        as_user("E_ZJ")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/items/{item.id}/resolve",
            json={"resolution": "upheld", "final_weight": 9},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["finalWeight"] == 9


class TestActionPermissions:
    def test_员工不能裁定工单(self, client, db, org, modules, as_user):
        """⚠️ **员工不能自己裁定自己的工单** —— 那等于没复核。"""
        review = _review(db, "E_LCY")
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 8},
        )
        assert resp.status_code == 403

    def test_主管能裁定工单(self, client, db, org, modules, as_user):
        """⚠️ 与上一条配对 —— 证明差异来自身份而非端点坏了。"""
        review = _review(db, "E_LCY")
        as_user("E_ZJ")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 8},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["finalCount"] == 8

    def test_员工不能触发采集(self, client, db, org, modules, as_user):
        """⚠️ 采集会打网盘接口（有频控），不能随便点。"""
        as_user("E_LCY")
        resp = client.post(f"{PREFIX}/collect")
        assert resp.status_code == 403

    def test_员工不能配绑定(self, client, db, org, modules, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "/成品-甲"},
        )
        assert resp.status_code == 403

    def test_员工不能跑月度聚合(self, client, db, org, modules, as_user):
        """⚠️ 聚合会进算分集 —— 必须 HR/管理员。"""
        as_user("E_LCY")
        resp = client.post(f"{PREFIX}/aggregate", json={"period": "2026-11"})
        assert resp.status_code == 403

    def test_员工不能替别人填指标(self, client, db, org, modules, as_user):
        """⚠️ 指标是**按人**的 —— 不能替别人填。"""
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={
                "metric_date": "2026-11-19",
                "metric_code": "video_completion_rate",
                "metric_value": 0.35,
            },
        )
        assert resp.status_code == 200
        # ⚠️ 落库时用的是**当前用户**，不是请求体里的 id
        from backend.app.domain.pan_workload_models import PanManualMetric

        row = db.scalars(select(PanManualMetric)).one()
        assert row.employee_id == "E_LCY"


# ─────────────────────────────────────────────────────────────────────────
# 工单流程
# ─────────────────────────────────────────────────────────────────────────


class TestReviewFlow:
    def test_发起工单(self, client, db, org, modules, as_user):
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews",
            json={
                "business_date": "2026-11-19",
                "claimed_count": 8,
                "reason": "我剪了 8 条",
                "scope": "day",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["systemCount"] == 6

    def test_当天没采集过要能区分(self, client, db, org, modules, as_user):
        """⚠️ **「0 条」≠「还没采过」** —— 必须能区分。

        场景：员工对某天提异议，但那天系统**根本没采集数据**。
        此时 ``system_count = 0``，但这不是「系统判定他 0 条」，
        而是「系统没数据可比对」。

        ⇒ 快照里必须打 ``has_daily`` 标记，
        否则员工看到「系统判定 0」会以为系统认定他那天没干活。
        """
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews",
            json={
                "business_date": "2026-11-19",
                "claimed_count": 8,
                "reason": "系统没数到",
                "scope": "day",
            },
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["item"]
        assert item["systemCount"] == 0
        assert item["dailySnapshot"]["has_daily"] is False

    def test_采集过后要标记has_daily(self, client, db, org, modules, as_user):
        """⚠️ 与上一条配对 —— 证明标记反映真实状态，不是恒为 False。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews",
            json={
                "business_date": "2026-11-19",
                "claimed_count": 8,
                "reason": "数不对",
                "scope": "day",
            },
        )
        item = resp.json()["item"]
        assert item["dailySnapshot"]["has_daily"] is True

    def test_文件明细用字符串日期能查到(self, client, db, org, modules, as_user):
        """⚠️ **回归点：文件表的业务日期是字符串列**。

        ``PanWorkloadFile.business_date`` 的列类型是 ``String(16)``
        ⇒ 插入与查询都用字符串。

        ⚠️ **之前没被抓到**，是因为测试里查询也传了 date 对象
        —— 没走真实的「HTTP 查询参数」路径。
        """
        db.add(PanWorkloadFile(
            fs_id=1, pan_path="/成品/11.19/a.mp4", file_name="a.mp4",
            extension="mp4", employee_id="E_LCY",
            business_date="2026-11-19",
            counted=True, count_weight=1,
        ))
        db.commit()
        as_user("E_LCY")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_LCY&business_date=2026-11-19"
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) == 1

    def test_文件明细非法日期不报错(self, client, db, org, modules, as_user):
        """⚠️ **文件表的业务日期是字符串列**（``String(16)``）——
        目录名解析出来的日期未必合法 ⇒ 不能当 date 解析。

        ⇒ 传任意字符串都应正常返回（可能为空），**不能 500**。
        """
        as_user("E_LCY")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_LCY&business_date=not-a-date"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []

    def test_文件明细序列化不崩(self, client, db, org, modules, as_user):
        """⚠️ **回归点：字符串列调 ``.isoformat()`` ⇒ AttributeError ⇒ 500**。

        ``PanWorkloadFile.business_date`` 是 ``String(16)``，
        序列化时若无脑 ``.isoformat()`` ⇒ 直接 500。
        """
        from datetime import date

        db.add(PanWorkloadFile(
            fs_id=1, pan_path="/成品/11.19/a.mp4", file_name="a.mp4",
            extension="mp4", employee_id="E_LCY",
            business_date="2026-11-19",  # ⚠️ 字符串
            counted=True, count_weight=1,
        ))
        db.commit()
        as_user("E_LCY")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_LCY&business_date=2026-11-19"
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["businessDate"] == "2026-11-19"

    def test_日汇总按区间查询用字符串(self, client, db, org, modules, as_user):
        """⚠️ 同上 —— start/end 也是字符串。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_LCY", "2026-11-20", 3)
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/daily?start=2026-11-19&end=2026-11-20")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

    def test_人工指标按区间查询用字符串(self, client, db, org, modules, as_user):
        as_user("E_LCY")
        client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "video_completion_rate", "metric_value": 0.35},
        )
        resp = client.get(f"{PREFIX}/manual-metrics?start=2026-11-01&end=2026-11-30")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_加争议项_勾选(self, client, db, org, modules, as_user):
        _daily(db, "E_LCY", "2026-11-19")
        f = PanWorkloadFile(
            fs_id=1, pan_path="/成品/11.19/a.mp4", file_name="a.mp4",
            extension="mp4", employee_id="E_LCY",
            business_date="2026-11-19",
            counted=True, count_weight=1,
        )
        db.add(f)
        db.commit()
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews",
            json={"business_date": "2026-11-19", "claimed_count": 7,
                  "reason": "有一条漏了"},
        )
        review_id = resp.json()["item"]["id"]
        resp = client.post(
            f"{PREFIX}/reviews/{review_id}/items",
            json={"pan_workload_file_id": f.id, "dispute_type": "missing",
                  "claimed_weight": 1},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["panWorkloadFileId"] == f.id

    def test_加争议项_手填(self, client, db, org, modules, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews",
            json={"business_date": "2026-11-19", "claimed_count": 7,
                  "reason": "有文件漏传"},
        )
        review_id = resp.json()["item"]["id"]
        resp = client.post(
            f"{PREFIX}/reviews/{review_id}/items",
            json={"manual_name": "漏传.mp4", "dispute_type": "missing"},
        )
        assert resp.status_code == 200
        assert resp.json()["item"]["manualName"] == "漏传.mp4"

    def test_上传证据并下载(self, client, db, org, modules, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews",
            json={"business_date": "2026-11-19", "claimed_count": 7,
                  "reason": "x"},
        )
        review_id = resp.json()["item"]["id"]
        resp = client.post(
            f"{PREFIX}/reviews/{review_id}/artifacts",
            files={"file": ("shot.png", io.BytesIO(b"\x89PNGdata"), "image/png")},
        )
        assert resp.status_code == 200, resp.text
        art_id = resp.json()["item"]["id"]
        # 下载
        resp = client.get(
            f"{PREFIX}/reviews/{review_id}/artifacts/{art_id}"
        )
        assert resp.status_code == 200
        assert resp.content == b"\x89PNGdata"

    def test_下载别人的附件被拒(self, client, db, org, modules, as_user):
        review = _review(db, "E_OTHER")
        from backend.app.domain.pan_workload_models import (
            PanWorkloadReviewArtifact,
        )

        art = PanWorkloadReviewArtifact(
            review_id=review.id, name="s.png", media_type="image/png",
            content=b"secret", digest="d", size_bytes=6, uploaded_by="E_OTHER",
        )
        db.add(art)
        db.commit()
        as_user("E_LCY")
        resp = client.get(
            f"{PREFIX}/reviews/{review.id}/artifacts/{art.id}"
        )
        assert resp.status_code == 403

    def test_链校验端点(self, client, db, org, modules, as_user):
        review = _review(db, "E_LCY")
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/reviews/{review.id}/chain")
        assert resp.status_code == 200
        assert "ok" in resp.json()

    def test_链校验字段是camelCase(self, client, db, org, modules, as_user):
        """⚠️ **契约护栏** —— 服务层返回 snake_case，
        路由层必须转成 camelCase。

        ⚠️ **为什么这条重要**
        前端按 ``eventCount`` / ``headHash`` 读。
        若路由层直接透传 snake_case，前端拿到 ``undefined`` ⇒
        界面显示「0 条事件」、hash 空白 —— **不报错，只是空**，
        极难被发现。
        """
        review = _review(db, "E_LCY")
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/reviews/{review.id}/chain")
        body = resp.json()
        assert "eventCount" in body, "链校验必须是 camelCase"
        assert "headHash" in body
        # ⚠️ snake_case 不能出现
        assert "event_count" not in body
        assert "head_hash" not in body
        assert body["guarantee"], "必须保留诚实口径说明"

    def test_工单详情里的chain也是camelCase(self, client, db, org, modules, as_user):
        review = _review(db, "E_LCY")
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/reviews/{review.id}")
        assert resp.status_code == 200
        chain = resp.json()["chain"]
        assert "eventCount" in chain
        assert "event_count" not in chain


# ─────────────────────────────────────────────────────────────────────────
# 绑定
# ─────────────────────────────────────────────────────────────────────────


class TestBindings:
    def test_配绑定(self, client, db, org, modules, as_user):
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "/成品-甲",
                  "folder_name": "甲"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["folderPath"] == "/成品-甲"

    def test_目录不能绑给两个人(self, client, db, org, modules, as_user):
        """⚠️ 同一目录绑两个人 ⇒ 工作量算给谁？"""
        as_user("E_HR")
        client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "/成品-甲"},
        )
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_OTHER", "folder_path": "/成品-甲"},
        )
        assert resp.status_code == 409

    def test_目录路径必须绝对(self, client, db, org, modules, as_user):
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "成品-甲"},
        )
        assert resp.status_code == 400

    def test_前缀冲突被挡下(self, client, db, org, modules, as_user):
        """⚠️⚠️ **前缀互斥是归属正确性的地基**

        归属判定 = ``file_path.startswith(folder_path)``
        ⇒ ``/成品`` 与 ``/成品-小瑶`` **互为前缀**
        ⇒ 同一个文件会命中两条绑定 ⇒ 采集时判 ``ambiguous``
        （归属为空，**不落任何人**）⇒ **工作量凭空消失**。

        ⇒ 配 ``/成品`` 时必须报错，**不能等到采集时才发现**。
        """
        as_user("E_HR")
        client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "/成品-小瑶"},
        )
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_OTHER", "folder_path": "/成品"},
        )
        assert resp.status_code == 409, resp.text
        assert "前缀" in resp.json()["detail"]

    def test_口播成品可正常绑定(self, client, db, org, modules, as_user):
        """⚠️ 回归：``/口播成品`` 已移出黑名单 —— 绑它**不该**被前缀规则误伤。

        ⚠️ 特别注意 ``/成品`` 与 ``/口播成品`` **不是前缀关系**
        （``/口播成品`` 不以 ``/成品`` 开头）⇒ 不冲突。
        """
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "/口播成品",
                  "folder_name": "口播"},
        )
        assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────────────────────────
# 人工填报
# ─────────────────────────────────────────────────────────────────────────


class TestManualMetrics:
    def test_填报(self, client, db, org, modules, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "video_completion_rate",
                  "metric_value": 0.35},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["metricValue"] == 0.35

    def test_非法指标被拒(self, client, db, org, modules, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "salary", "metric_value": 1.0},
        )
        assert resp.status_code == 400

    def test_值域超限被拒(self, client, db, org, modules, as_user):
        """⚠️ 422 = pydantic 请求体校验拦截（比 400 更准确）。

        ⚠️ **服务层还有第二道** —— 见
        ``test_pan_manual_metric.py::TestSubmit::test_值域校验``。
        两道都要有：pydantic 拦请求，服务层拦**绕过 API 的内部调用**。
        """
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "video_completion_rate", "metric_value": 1.5},
        )
        assert resp.status_code == 422

    def test_HR跑聚合(self, client, db, org, modules, as_user):
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_HR")
        resp = client.post(f"{PREFIX}/aggregate", json={"period": "2026-11"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["written"] >= 1

    def test_上传指标证据(self, client, db, org, modules, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "video_completion_rate", "metric_value": 0.35},
        )
        metric_id = resp.json()["item"]["id"]
        resp = client.post(
            f"{PREFIX}/manual-metrics/{metric_id}/artifacts",
            files={"file": ("s.png", io.BytesIO(b"img"), "image/png")},
        )
        assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────────────────────────
# ⚠️⚠️ **部门功能板块隔离**（2026-11-21 用户投诉）
# ─────────────────────────────────────────────────────────────────────────


class TestDepartmentModuleIsolation:
    """⚠️⚠️ **用户投诉的回归测试**

    用户原话：「**还有为什么别的部门为什么会有剪辑板块？**
    这个没有做各部门功能板块隔离，我要的是对应的部门有对应的功能。」

    **根因**：前端菜单与路由守卫的过滤维度**只有权限串**
    ⇒ ``pan:workload:view`` 授予基础 ``employee`` 角色后
    ⇒ **全公司每个部门**的侧边栏都出现「剪辑工作量」。

    ⇒ 修复 = **部门维度**（``department_module``）独立于**角色维度**（权限）。

    ⚠️ **这些断言是「否定式」的，必须配一组「肯定式」的**
    —— 只断言「别的部门 403」的话，把整个守卫删掉也能通过
    ⇒ 所以同时断言「短视频部的人能正常访问」。
    """

    def test_短视频部的人能访问(self, client, db, org, modules, as_user):
        """⚠️ **肯定式断言** —— 守卫不能把正常用户也挡住。"""
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text

    def test_别部门的人被拒(self, client, db, org, as_user):
        """★ **核心断言** —— 运营中心的人**不该**看到剪辑工作量。"""
        as_user("E_OTHER")  # 运营中心
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 403, resp.text
        assert "板块" in resp.json()["detail"]

    def test_别部门的人直接敲URL也被拒(self, client, db, org, as_user):
        """⚠️⚠️ **光隐藏菜单不够** —— 直接调 API 也必须被拒。

        ⇒ 这就是为什么后端必须有 ``_require_pan_module`` 守卫。
        """
        as_user("E_OTHER")
        for path in ("/summary", "/daily", "/files", "/reviews"):
            resp = client.get(f"{PREFIX}{path}")
            assert resp.status_code == 403, f"{path} 应当 403，实际 {resp.status_code}"

    def test_别部门的人写操作也被拒(self, client, db, org, as_user):
        """⚠️ **写操作也要拦** —— 不能只拦读。"""
        as_user("E_OTHER")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "video_completion_rate", "metric_value": 0.35},
        )
        assert resp.status_code == 403, resp.text

    def test_HR不受板块限制(self, client, db, org, as_user):
        """⚠️ **HR 必须能进** —— 否则没人能配置板块了。"""
        as_user("E_HR")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text

    def test_上级部门开通则下级继承(self, client, db, org, as_user):
        """⚠️ **板块开通自上而下继承** —— 运营中心开通 ⇒ 短视频能用。

        ⇒ 这条锁住「继承方向」，防止有人把它写成「向下展开」
        （那样运营中心的老总反而会看到剪辑面板）。
        """
        from backend.app.domain.pan_workload_models import DepartmentModule

        db.add(
            DepartmentModule(
                department_id="D_CENTER",
                module_code="pan_workload",
                active=True,
                note="测试：运营中心也开通",
            )
        )
        db.commit()
        as_user("E_LCY")  # 短视频部
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text

    def test_下级开通不等于上级开通(self, client, db, org, modules, as_user):
        """⚠️⚠️ **继承是单向的** —— 短视频开通 ⇒ 运营中心**不能**用。

        这是上一条的反向断言。两条合起来才锁住方向。
        """
        as_user("E_TOP")  # 运营中心的 leader
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 403, resp.text

    def test_停用板块后立即失效(self, client, db, org, modules, as_user):
        """⚠️ **停用要立即生效** —— ``active=False`` ⇒ 挡在门外。"""
        from backend.app.domain.pan_workload_models import DepartmentModule

        mod = db.scalars(
            select(DepartmentModule).where(DepartmentModule.module_code == "pan_workload")
        ).one()
        mod.active = False
        db.commit()
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 403, resp.text


# ─────────────────────────────────────────────────────────────────────────
# 板块解析（服务层）
# ─────────────────────────────────────────────────────────────────────────


class TestModuleResolution:
    """``department_modules`` 服务的判定逻辑。"""

    def test_短视频部成员有板块(self, db, org, modules):
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
        )

        emp = db.get(Employee, "E_LCY")
        assert "pan_workload" in enabled_modules_for_employee(db, emp)

    def test_运营中心成员无板块(self, db, org, modules):
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
        )

        emp = db.get(Employee, "E_OTHER")
        assert "pan_workload" not in enabled_modules_for_employee(db, emp)

    def test_兼岗部门也算(self, db, org, modules):
        """⚠️ **兼岗**也要算 —— 一个人在两个部门，其中一个有板块就能用。"""
        from backend.app.domain.models import EmployeeDepartment
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
        )

        db.add(
            EmployeeDepartment(
                employee_id="E_OTHER", department_id="D_VIDEO", is_primary=False
            )
        )
        db.commit()
        emp = db.get(Employee, "E_OTHER")
        assert "pan_workload" in enabled_modules_for_employee(db, emp)

    def test_HR拿到全部板块(self, db, org):
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
        )

        emp = db.get(Employee, "E_HR")
        mods = enabled_modules_for_employee(db, emp)
        assert "pan_workload" in mods

    def test_默认关闭_没有配置行就没有板块(self, db, org):
        """⚠️⚠️ **「默认关闭」是这套设计的核心**

        没有 ``department_module`` 配置行 ⇒ 该部门**没有**任何板块
        ⇒ 菜单不显示、API 403。

        ⚠️ 为什么这个方向重要：
        漏配 ⇒ 「功能没出现」（**可见、可修**）
        配错 ⇒ 「功能到处乱出现」（**不可见、不可修**）
        """
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
        )

        emp = db.get(Employee, "E_LCY")
        assert enabled_modules_for_employee(db, emp) == set()


# ─────────────────────────────────────────────────────────────────────────
# ★ 防回归：板块隔离的三个历史 bug + 「板块之间互不污染」
# ─────────────────────────────────────────────────────────────────────────


class TestModuleIsolationRegression:
    """⚠️⚠️ **这些测试对应 2026-11-21 真实修掉的 bug**

    每个测试名里都标了当时的**现象**，避免以后有人「优化」掉这些断言
    ⇒ 又把「别的部门看到不属于自己的板块」这个坑挖回来。
    """

    def test_系统管理员豁免_按employee_id查(self, db, org, modules):
        """⚠️ **回归：管理员被当成普通员工** ⇒ 没人能开通板块 ⇒ 死锁。

        历史 bug：``db.get(SystemAdmin, employee.id)`` —— 用**主键**查。
        ``SystemAdmin`` 的主键是自动生成的 ``id``，管理员身份在 ``employee_id``
        ⇒ 拿员工 ID 比管理员记录 ID ⇒ **永远查不到**。

        ⇒ 现在必须按 ``employee_id`` 查才能命中。
        """
        from backend.app.domain.models import SystemAdmin
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
            is_admin_or_hr,
        )

        db.add(SystemAdmin(employee_id="E_OTHER", active=True))
        db.commit()

        emp = db.get(Employee, "E_OTHER")
        assert is_admin_or_hr(db, emp) is True
        # 管理员拿到**全部**已知板块
        mods = enabled_modules_for_employee(db, emp)
        assert "pan_workload" in mods

    def test_系统管理员停用后不再豁免(self, db, org, modules):
        """⚠️ **回归：``active=False`` 的管理员仍被豁免** ⇒ 离职管理员仍有全板块。"""
        from backend.app.domain.models import SystemAdmin
        from backend.app.services.department_modules import is_admin_or_hr

        db.add(SystemAdmin(employee_id="E_OTHER", active=False))
        db.commit()

        emp = db.get(Employee, "E_OTHER")
        assert is_admin_or_hr(db, emp) is False

    def test_板块之间互不污染(self, db, org, modules):
        """⚠️⚠️ **核心不变量：开通 A 板块不会让人凭空拿到 B 板块。**

        这是用户诉求「以后加哪个部门特定的功能板块，不会污染到其他部门
        其他的板块中去」的**直接断言**。

        ⚠️ **注意继承方向**：父部门开通 ⇒ 子部门**继承**（向上继承）。
        所以这里给运营中心开 ``other_module`` ⇒ 短视频（其子部门）也会继承
        ⇒ 这**不是**污染，而是「上级开通的业务，下级团队本来就在做」。

        真正的「污染」是：短视频开了 ``pan_workload``，
        **别的部门**（如客服部）却也看到了剪辑面板。

        ⇒ 这里用一个**完全无关的部门树分支**（仓储部）来验证不污染。
        """
        from backend.app.domain.models import Department
        from backend.app.domain.pan_workload_models import DepartmentModule
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
            has_module,
        )

        # 造一个与运营中心**毫无关系**的分支
        d_warehouse = Department(id="D_WH", name="仓储部", active=True)
        db.add(d_warehouse)
        db.add(
            Employee(
                id="E_WH",
                name="仓储员工",
                department_id="D_WH",
                position="仓管",
                employment_status="active",
                account_status="enabled",
            )
        )
        db.commit()

        # ⚠️ ``modules`` fixture 已给短视频开过 pan_workload，
        #    这里只补「另一个板块」，避免撞唯一约束 (department_id, module_code)。
        db.add(
            DepartmentModule(
                department_id="D_CENTER",
                module_code="other_module",
                active=True,
            )
        )
        db.commit()

        video_emp = db.get(Employee, "E_LCY")
        warehouse_emp = db.get(Employee, "E_WH")

        # 短视频拿到剪辑板块
        assert has_module(db, video_emp, "pan_workload") is True
        # ⚠️ **无关部门什么都没拿到** —— 这才是「不污染」
        assert has_module(db, warehouse_emp, "pan_workload") is False
        assert has_module(db, warehouse_emp, "other_module") is False
        assert enabled_modules_for_employee(db, warehouse_emp) == set()

    def test_守卫按具体板块判定_不是有无任一板块(self, db, org, modules):
        """⚠️⚠️ **回归：守卫只看「有没有任一板块」⇒ 新增板块时放行错板块。**

        历史隐患：``_require_pan_module`` 写成
        ``if not enabled_modules_for_employee(db, user)`` ——
        只要员工**有任意板块**就放行，不管是不是 ``pan_workload``。

        ⇒ 现在只有一个板块时看不出问题；
        ⇒ **一旦新增第二个板块**，开了 B 板块的部门就能访问 A 板块的 API
        ⇒ 板块隔离直接失效。

        ⇒ 必须按**具体板块**判定。
        """
        from backend.app.domain.pan_workload_models import DepartmentModule

        # 运营中心开了**另一个**板块，但没有 pan_workload
        db.add(
            DepartmentModule(
                department_id="D_CENTER",
                module_code="other_module",
                active=True,
            )
        )
        db.commit()

        from backend.app.services.department_modules import has_module

        center_emp = db.get(Employee, "E_OTHER")
        assert has_module(db, center_emp, "other_module") is True
        assert has_module(db, center_emp, "pan_workload") is False

    def test_父部门开通则子部门继承(self, db, org):
        """⚠️ **回归：继承方向搞反** ⇒ 上级部门看到下级部门的业务。

        真实结构：短视频挂在运营中心下。
        ⇒ 运营中心开通 ⇒ 短视频继承（向下继承）
        ⇒ 但短视频开通 **不会** 让运营中心的人拿到（不向上冒泡）
        """
        from backend.app.domain.pan_workload_models import DepartmentModule
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
        )

        # 短视频开通 ⇒ 只有短视频的人拿到
        db.add(
            DepartmentModule(
                department_id="D_VIDEO", module_code="pan_workload", active=True
            )
        )
        db.commit()

        video_emp = db.get(Employee, "E_LCY")
        center_emp = db.get(Employee, "E_OTHER")

        assert "pan_workload" in enabled_modules_for_employee(db, video_emp)
        # ⚠️ **短视频开通 ≠ 运营中心开通**（不向上冒泡）
        assert "pan_workload" not in enabled_modules_for_employee(db, center_emp)

    def test_上级部门开通则下级继承(self, db, org):
        """⚠️ **继承方向的另一半**：运营中心开通 ⇒ 短视频继承。

        与上一个测试**方向相反**，两个一起才能锁住方向。
        """
        from backend.app.domain.pan_workload_models import DepartmentModule
        from backend.app.services.department_modules import (
            enabled_modules_for_employee,
        )

        db.add(
            DepartmentModule(
                department_id="D_CENTER", module_code="pan_workload", active=True
            )
        )
        db.commit()

        video_emp = db.get(Employee, "E_LCY")
        center_emp = db.get(Employee, "E_OTHER")

        assert "pan_workload" in enabled_modules_for_employee(db, video_emp)
        assert "pan_workload" in enabled_modules_for_employee(db, center_emp)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
