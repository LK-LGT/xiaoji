"""★ **部门 × 岗位 考核口径** 的测试（2026-11-23）。

⚠️ **为什么这层要单独测**
------------------------
用户投诉：「**哪个部门都有不一样的考核方式，肯定是不一样的功能呀**」。

⇒ 板块隔离（``department_module``）只解决了「这个部门有没有剪辑这个业务」，
**但部门内部还有不同岗位**，不同岗位的考核方式**完全不一样**。

短视频部（2026-11-23 实测库）—— 5 个人、**3 种角色**：

==================  ==========  ==========================  ========
人物                岗位         网盘产出                     角色
==================  ==========  ==========================  ========
李婵雨              剪辑         ``/成品-小雨``               reporter
佟瑶                剪辑         ``/成品-小瑶``               reporter
巫宜珊              主播         ``/口播成品``（**不用管**）   none
张洁                编导         ——                           reviewer
刘旭                运营         ——（部门 leader）            reviewer
==================  ==========  ==========================  ========

⚠️ **本测试固化的核心结论**
----------------------------
1. **板块开通 ≠ 全部门都收推送** —— 岗位口径必须单独配。
2. **没配置 = 不参与**（默认关闭，漏配只导致「没出现」，不导致「到处乱出现」）。
3. **指标也能按岗位配** —— 不同岗位的考核指标本来就不一样。
4. **改了口径立即生效，不发版** —— 口径在库里，不在代码里。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base
from backend.app.domain.pan_workload_models import DepartmentModule, PanWorkloadPolicy


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
    """短视频部（开通板块）+ 仓储部（未开通板块）。"""
    from backend.app.domain.models import Department, Employee

    db.add(Department(id="D_VIDEO", name="短视频", active=True, leader_id="E_5"))
    db.add(Department(id="D_WARE", name="仓储", active=True))

    db.add_all([
        Employee(id="E_1", name="李婵雨", department_id="D_VIDEO", position="剪辑",
                 manager_id="E_4",
                 employment_status="active", account_status="enabled",
                 feishu_open_id="ou_1"),
        Employee(id="E_2", name="佟瑶", department_id="D_VIDEO", position="剪辑",
                 manager_id="E_4",
                 employment_status="active", account_status="enabled",
                 feishu_open_id="ou_2"),
        Employee(id="E_3", name="巫宜珊", department_id="D_VIDEO", position="主播",
                 manager_id="E_5",
                 employment_status="active", account_status="enabled",
                 feishu_open_id="ou_3"),
        # ⚠️ **张洁是两位剪辑的直属上级**（真实组织，2026-11-23 实测）
        Employee(id="E_4", name="张洁", department_id="D_VIDEO", position="编导",
                 employment_status="active", account_status="enabled",
                 feishu_open_id="ou_4"),
        # ⚠️ **刘旭是短视频 leader**（部门 leader）
        Employee(id="E_5", name="刘旭", department_id="D_VIDEO", position="运营",
                 employment_status="active", account_status="enabled",
                 feishu_open_id="ou_5"),
        Employee(id="E_W", name="仓管", department_id="D_WARE", position="剪辑",
                 employment_status="active", account_status="enabled",
                 feishu_open_id="ou_w"),
    ])
    db.add(DepartmentModule(
        id="dm_v", department_id="D_VIDEO", module_code="pan_workload", active=True,
    ))
    db.add_all([
        PanWorkloadPolicy(id="p1", department_id="D_VIDEO", position="剪辑",
                          role="reporter", active=True),
        PanWorkloadPolicy(id="p2", department_id="D_VIDEO", position="编导",
                          role="reviewer", active=True),
        PanWorkloadPolicy(id="p3", department_id="D_VIDEO", position="运营",
                          role="reviewer", active=True),
        # ⚠️ **主播刻意不配置** ⇒ 默认 none
    ])
    db.commit()
    return db


class TestPolicyResolution:
    """口径解析。"""

    def test_剪辑是填报人(self, db, org):
        from backend.app.services.pan_workload_policy import resolve_policy

        p = resolve_policy(db, "D_VIDEO", "剪辑")
        assert p.is_reporter
        assert not p.is_reviewer

    def test_编导运营只审(self, db, org):
        from backend.app.services.pan_workload_policy import resolve_policy

        assert resolve_policy(db, "D_VIDEO", "编导").is_reviewer
        assert resolve_policy(db, "D_VIDEO", "运营").is_reviewer

    def test_主播不参与(self, db, org):
        """⚠️ **没配置行 ⇒ 不参与** —— 不是「默认参与」。"""
        from backend.app.services.pan_workload_policy import resolve_policy

        p = resolve_policy(db, "D_VIDEO", "主播")
        assert p.role == "none"
        assert not p.is_reporter
        assert not p.is_reviewer

    def test_不存在的岗位不参与(self, db, org):
        from backend.app.services.pan_workload_policy import resolve_policy

        assert resolve_policy(db, "D_VIDEO", "实习生").role == "none"

    def test_停用的配置不生效(self, db, org):
        """⚠️ **停用 = 不参与**（保留配置痕迹，不删行）。"""
        from backend.app.services.pan_workload_policy import resolve_policy

        row = db.get(PanWorkloadPolicy, "p1")
        row.active = False
        db.commit()
        assert resolve_policy(db, "D_VIDEO", "剪辑").role == "none"

    def test_空岗位不参与(self, db, org):
        from backend.app.services.pan_workload_policy import resolve_policy

        assert resolve_policy(db, "D_VIDEO", "").role == "none"
        assert resolve_policy(db, "", "剪辑").role == "none"

    def test_默认指标按角色分(self, db, org):
        """⚠️⚠️ **2026-11-23 角色倒转** —— 默认指标**按角色**分。

        剪辑（reporter）→ 三条**数量**（普通视频/OOTD/智能体）
        主管（reviewer）→ 两个**比率**（完播率/视频留存率）

        ⇒ 一律用「比率默认」会让**剪辑去填比率** ⇒ 角色倒转白做。
        """
        from backend.app.services.pan_workload_policy import resolve_policy

        editor = resolve_policy(db, "D_VIDEO", "剪辑")
        assert editor.metric_codes == (
            "video_normal_count",
            "video_ootd_count",
            "video_agent_count",
        )
        leader = resolve_policy(db, "D_VIDEO", "编导")
        assert leader.metric_codes == (
            "video_completion_rate",
            "video_retention_rate",
        )

    def test_指标可按岗位配(self, db, org):
        """⚠️ **不同岗位的考核指标本来就不一样** ⇒ 必须能分开配。"""
        from backend.app.services.pan_workload_policy import resolve_policy

        row = db.get(PanWorkloadPolicy, "p1")
        row.metric_codes = "video_completion_rate"
        db.commit()
        assert resolve_policy(db, "D_VIDEO", "剪辑").metric_codes == (
            "video_completion_rate",
        )


class TestPushTargeting:
    """⚠️ **推送对象必须由口径表驱动**。"""

    def test_只推剪辑(self, db, org):
        """⚠️ **核心** —— 板块开了，但只有 reporter 收。"""
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        ids = {e.id for e in editors_for_push(db, "2026-11-23")}
        assert ids == {"E_1", "E_2"}

    def test_主播编导运营都不推(self, db, org):
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        ids = {e.id for e in editors_for_push(db, "2026-11-23")}
        assert "E_3" not in ids  # 主播
        assert "E_4" not in ids  # 编导
        assert "E_5" not in ids  # 运营

    def test_其他部门不推(self, db, org):
        """⚠️ **板块隔离依然生效** —— 仓储部的剪辑不该收到。"""
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        ids = {e.id for e in editors_for_push(db, "2026-11-23")}
        assert "E_W" not in ids

    def test_改口径立即生效不发版(self, db, org):
        """⚠️ **让主播也报数据 ⇒ 改一行配置就行，不用改代码发版**。"""
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        db.add(PanWorkloadPolicy(
            id="p4", department_id="D_VIDEO", position="主播",
            role="reporter", active=True,
        ))
        db.commit()
        ids = {e.id for e in editors_for_push(db, "2026-11-23")}
        assert "E_3" in ids

    def test_停用口径就不再推(self, db, org):
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        row = db.get(PanWorkloadPolicy, "p1")
        row.active = False
        db.commit()
        ids = {e.id for e in editors_for_push(db, "2026-11-23")}
        assert ids == set()


class TestLeaderSummary:
    """⚠️ **主管汇总：只审的人收，不参与的人不收**。"""

    def test_编导收汇总(self, db, org):
        from backend.app.domain.models import Employee
        from backend.app.services.feishu_daily_push import leaders_of

        target = db.get(Employee, "E_1")
        names = {e.name for e in leaders_of(db, target)}
        assert "张洁" in names

    def test_运营收汇总(self, db, org):
        from backend.app.domain.models import Employee
        from backend.app.services.feishu_daily_push import leaders_of

        target = db.get(Employee, "E_1")
        names = {e.name for e in leaders_of(db, target)}
        assert "刘旭" in names

    def test_主管卡无表单(self, db, org):
        """⚠️ **绝不给主管表单** —— 否则主管能替下属填，数据串了。"""
        from backend.app.services.feishu_card import build_leader_daily_status_card

        card = build_leader_daily_status_card(
            day="2026-11-23", day_text="x",
            rows=[["李婵雨", "完播率", "待填"]], all_done=False,
        )
        tags = [e.get("tag") for e in card["body"]["elements"]]
        assert "form" not in tags
        assert "table" in tags
