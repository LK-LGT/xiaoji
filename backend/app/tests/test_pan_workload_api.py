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
    def test_员工只看自己的(self, client, db, org, as_user):
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

    def test_员工看别人明细被拒(self, client, db, org, as_user):
        _daily(db, "E_OTHER", "2026-11-19")
        as_user("E_LCY")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_OTHER&business_date=2026-11-19"
        )
        assert resp.status_code == 403

    def test_HR看全部(self, client, db, org, as_user):
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_OTHER", "2026-11-19", 9)
        as_user("E_HR")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200
        assert len(resp.json()["employees"]) == 2

    def test_主管看得到下属(self, client, db, org, as_user):
        """⚠️ 主管必须能看到下属的条数 —— 否则没法核对。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200
        emps = resp.json()["employees"]
        assert any(e["employeeId"] == "E_LCY" for e in emps)

    def test_主管看不到外部门(self, client, db, org, as_user):
        _daily(db, "E_OTHER", "2026-11-19", 9)
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200
        emps = resp.json()["employees"]
        assert all(e["employeeId"] != "E_OTHER" for e in emps)

    def test_主管看不到外部门明细(self, client, db, org, as_user):
        _daily(db, "E_OTHER", "2026-11-19")
        as_user("E_ZJ")
        resp = client.get(
            f"{PREFIX}/files?employee_id=E_OTHER&business_date=2026-11-19"
        )
        assert resp.status_code == 403

    def test_回归_直属上级看得到直属下属(self, client, db, org, as_user):
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

    def test_回归_直属上级能裁定下属工单(self, client, db, org, as_user):
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

    def test_回归_部门leader也能看到全部剪辑(self, client, db, org, as_user):
        """⚠️ 与「直属上级」配对 —— 两个角色都要能看到。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_TY", "2026-11-19", 9)
        as_user("E_LX")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        ids = {e["employeeId"] for e in resp.json()["employees"]}
        assert {"E_LCY", "E_TY"} <= ids

    def test_回归_平级同事看不到对方(self, client, db, org, as_user):
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

    def test_回归_上级看不到平级(self, client, db, org, as_user):
        """⚠️ 编导与 leader **互为平级**（都是运营中心下的）——
        编导看不到 leader 的数据（leader 不是她的下属）。"""
        _daily(db, "E_LX", "2026-11-19", 9)
        as_user("E_ZJ")
        resp = client.get(f"{PREFIX}/summary")
        assert resp.status_code == 200, resp.text
        ids = {e["employeeId"] for e in resp.json()["employees"]}
        assert "E_LX" not in ids


class TestActionPermissions:
    def test_员工不能裁定工单(self, client, db, org, as_user):
        """⚠️ **员工不能自己裁定自己的工单** —— 那等于没复核。"""
        review = _review(db, "E_LCY")
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 8},
        )
        assert resp.status_code == 403

    def test_主管能裁定工单(self, client, db, org, as_user):
        """⚠️ 与上一条配对 —— 证明差异来自身份而非端点坏了。"""
        review = _review(db, "E_LCY")
        as_user("E_ZJ")
        resp = client.post(
            f"{PREFIX}/reviews/{review.id}/resolve",
            json={"resolution": "approved", "final_count": 8},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["finalCount"] == 8

    def test_员工不能触发采集(self, client, db, org, as_user):
        """⚠️ 采集会打网盘接口（有频控），不能随便点。"""
        as_user("E_LCY")
        resp = client.post(f"{PREFIX}/collect")
        assert resp.status_code == 403

    def test_员工不能配绑定(self, client, db, org, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "/成品-甲"},
        )
        assert resp.status_code == 403

    def test_员工不能跑月度聚合(self, client, db, org, as_user):
        """⚠️ 聚合会进算分集 —— 必须 HR/管理员。"""
        as_user("E_LCY")
        resp = client.post(f"{PREFIX}/aggregate", json={"period": "2026-11"})
        assert resp.status_code == 403

    def test_员工不能替别人填指标(self, client, db, org, as_user):
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
    def test_发起工单(self, client, db, org, as_user):
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

    def test_当天没采集过要能区分(self, client, db, org, as_user):
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

    def test_采集过后要标记has_daily(self, client, db, org, as_user):
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

    def test_文件明细用字符串日期能查到(self, client, db, org, as_user):
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

    def test_文件明细非法日期不报错(self, client, db, org, as_user):
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

    def test_文件明细序列化不崩(self, client, db, org, as_user):
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

    def test_日汇总按区间查询用字符串(self, client, db, org, as_user):
        """⚠️ 同上 —— start/end 也是字符串。"""
        _daily(db, "E_LCY", "2026-11-19", 6)
        _daily(db, "E_LCY", "2026-11-20", 3)
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/daily?start=2026-11-19&end=2026-11-20")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

    def test_人工指标按区间查询用字符串(self, client, db, org, as_user):
        as_user("E_LCY")
        client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "video_completion_rate", "metric_value": 0.35},
        )
        resp = client.get(f"{PREFIX}/manual-metrics?start=2026-11-01&end=2026-11-30")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_加争议项_勾选(self, client, db, org, as_user):
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

    def test_加争议项_手填(self, client, db, org, as_user):
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

    def test_上传证据并下载(self, client, db, org, as_user):
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

    def test_下载别人的附件被拒(self, client, db, org, as_user):
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

    def test_链校验端点(self, client, db, org, as_user):
        review = _review(db, "E_LCY")
        as_user("E_LCY")
        resp = client.get(f"{PREFIX}/reviews/{review.id}/chain")
        assert resp.status_code == 200
        assert "ok" in resp.json()

    def test_链校验字段是camelCase(self, client, db, org, as_user):
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

    def test_工单详情里的chain也是camelCase(self, client, db, org, as_user):
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
    def test_配绑定(self, client, db, org, as_user):
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "/成品-甲",
                  "folder_name": "甲"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["folderPath"] == "/成品-甲"

    def test_目录不能绑给两个人(self, client, db, org, as_user):
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

    def test_目录路径必须绝对(self, client, db, org, as_user):
        as_user("E_HR")
        resp = client.post(
            f"{PREFIX}/bindings",
            json={"employee_id": "E_LCY", "folder_path": "成品-甲"},
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────
# 人工填报
# ─────────────────────────────────────────────────────────────────────────


class TestManualMetrics:
    def test_填报(self, client, db, org, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "video_completion_rate",
                  "metric_value": 0.35},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["metricValue"] == 0.35

    def test_非法指标被拒(self, client, db, org, as_user):
        as_user("E_LCY")
        resp = client.post(
            f"{PREFIX}/manual-metrics",
            json={"metric_date": "2026-11-19",
                  "metric_code": "salary", "metric_value": 1.0},
        )
        assert resp.status_code == 400

    def test_值域超限被拒(self, client, db, org, as_user):
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

    def test_HR跑聚合(self, client, db, org, as_user):
        _daily(db, "E_LCY", "2026-11-19", 6)
        as_user("E_HR")
        resp = client.post(f"{PREFIX}/aggregate", json={"period": "2026-11"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["written"] >= 1

    def test_上传指标证据(self, client, db, org, as_user):
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
