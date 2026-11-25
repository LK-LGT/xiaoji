"""飞书端「每日填报提醒推送」的测试（2026-11-23）。

覆盖
----
1. **卡片结构** —— 表格 + 表单并列；已填完不给表单；表单只放待填指标
2. **推送对象** —— 板块隔离（非短视频部门不推）；已填完不推
3. **归属安全** —— 卡片被转发 ⇒ B 替 A 填被拒；非板块部门提交被拒
4. **幂等** —— 一天只推一次（唯一约束兜底）
5. **主管汇总** —— 表格（只读）+ 表单（填**效果指标**）

⚠️ **角色倒转（2026-11-23 业务确认）**
--------------------------------------
    剪辑（reporter）  填 **数量**：普通视频 / OOTD / 智能体 + 备注
    主管（reviewer）  填 **比率**：完播率 / 视频留存率

⇒ 「谁填什么」由 ``pan_workload_policy`` 的**岗位口径**决定，不是写死的。

⚠️ **真机实测前提**
--------------------
「``table`` 与 ``form`` 可以作为兄弟元素并列」是 2026-11-23
真发一张卡验证过的结论（探针：``_probe_feishu_card_table_form.py``）。
⇒ 本测试固化的结构与真机通过的**完全一致**。

⚠️ **截图为什么不在表单里**（2026-11-23 真机实测）
---------------------------------------------------
``input_type="file"`` → ``invalid input type``；
``file_input`` / ``file_upload`` tag → ``not support tag``。
⇒ 飞书卡片**放不了上传控件** ⇒ 截图只能走「另外发到聊天」这条通路。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.pan_workload_models import (
    FeishuDailyPushRecord,
    PanManualMetric,
    PanWorkloadPolicy,
)

# ===========================================================================
# 准备
# ===========================================================================


@pytest.fixture()
def db():
    """⚠️ **必须用 StaticPool** —— TestClient 在工作线程里发请求。"""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from backend.app.domain.models import Base

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
def db_file_engine(tmp_path):
    """🔴⚠️ **文件型 SQLite** —— 用来开**真正独立**的第二条连接。

    ⚠️ **为什么不能用内存库 + StaticPool 验证「落库」**
    --------------------------------------------------
    ``StaticPool`` 让**所有 session 共用同一条连接** ⇒
    第二个 session 看到的**就是第一个 session 的那个事务**
    ⇒ **没 commit 也能查到** ⇒ 这种测法**证明不了**数据落库了
    （⚠️ 实测：去掉 ``db.commit()`` 测试照样通过 ⇒ 测法无效）。

    ⇒ 必须用**文件型**数据库 —— 不同连接各自独立提交/可见。
    """
    from sqlalchemy import create_engine

    from backend.app.domain.models import Base

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'persist.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def org(db):
    """短视频部（开通板块）+ 仓储部（未开通板块）。

    真实组织（2026-11-21 查库）::

        运营中心 (leader=杨飞)
        └── 短视频 (leader=刘旭)
            ├── 张洁  编导   ← ★ 直属上级
            │   ├── 李婵雨 剪辑
            │   └── 佟瑶   剪辑
    """
    from backend.app.domain.models import Department, Employee, FeishuIdentity
    from backend.app.domain.pan_workload_models import DepartmentModule

    db.add(Department(id="D_CENTER", name="运营中心", active=True))
    db.add(Department(id="D_VIDEO", name="短视频", active=True))
    db.add(Department(id="D_WARE", name="仓储", active=True))

    # ⚠️ **短视频部不只有剪辑** —— 还有编导、运营、主播。
    #    只有剪辑该收填报提醒（岗位过滤）。
    leader = Employee(
        id="E_LEADER", name="张洁", department_id="D_VIDEO", position="编导",
        employment_status="active", account_status="enabled",
        feishu_open_id="ou_leader", feishu_user_id="u_leader",
    )
    editor = Employee(
        id="E_EDITOR", name="李婵雨", department_id="D_VIDEO", position="剪辑",
        manager_id="E_LEADER",
        employment_status="active", account_status="enabled",
        feishu_open_id="ou_editor", feishu_user_id="u_editor",
    )
    anchor = Employee(
        id="E_ANCHOR", name="巫宜珊", department_id="D_VIDEO", position="主播",
        employment_status="active", account_status="enabled",
        feishu_open_id="ou_anchor", feishu_user_id="u_anchor",
    )
    ware = Employee(
        id="E_WARE", name="仓管乙", department_id="D_WARE",
        employment_status="active", account_status="enabled",
        feishu_open_id="ou_ware", feishu_user_id="u_ware",
    )
    db.add_all([leader, editor, anchor, ware])
    db.add(FeishuIdentity(
        id="FI_E", employee_id="E_EDITOR",
        identity_type="open_id", identity_value="ou_editor", active=True,
    ))
    db.add(FeishuIdentity(
        id="FI_W", employee_id="E_WARE",
        identity_type="open_id", identity_value="ou_ware", active=True,
    ))
    db.add(FeishuIdentity(
        id="FI_A", employee_id="E_ANCHOR",
        identity_type="open_id", identity_value="ou_anchor", active=True,
    ))
    db.add(FeishuIdentity(
        id="FI_L", employee_id="E_LEADER",
        identity_type="open_id", identity_value="ou_leader", active=True,
    ))
    db.add(DepartmentModule(
        id="dm_1", department_id="D_VIDEO", module_code="pan_workload", active=True,
    ))
    # ⚠️⚠️ **岗位口径**（2026-11-23）—— 板块开通 ≠ 全部门都要收推送。
    #    短视频部 5 个人 3 种角色，**口径必须配在库里**。
    db.add(PanWorkloadPolicy(
        id="pwp_1", department_id="D_VIDEO", position="剪辑",
        role="reporter", active=True,
        note="唯一产出成片、需填报的岗位",
    ))
    db.add(PanWorkloadPolicy(
        id="pwp_2", department_id="D_VIDEO", position="编导",
        role="reviewer", active=True,
        note="剪辑的直属上级，只审",
    ))
    # ⚠️ **主播刻意不配置** ⇒ 默认 none（业务已确认口播成品不用管）
    db.commit()
    return editor, ware, leader


# ===========================================================================
# 1. 卡片结构
# ===========================================================================


class TestDailyCard:
    """⚠️ **卡片结构必须与真机通过的完全一致**。"""

    def test_表格与表单并列(self):
        """⚠️ **核心结构** —— 真机实测：两者作为兄弟元素并列是允许的。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23",
            day_text="11月23日（周一）",
            # ⚠️ **填过一条** ⇒ 表格有信息量 ⇒ 显示
            status_rows=[["普通视频", "8 条", "已填"], ["OOTD", "—", "待填"]],
            pending=[("video_ootd_count", "OOTD")],
            employee_id="E_EDITOR",
        )
        tags = [e.get("tag") for e in card["body"]["elements"]]
        assert "table" in tags
        assert "form" in tags
        # ⚠️ **表格必须在表单之前** —— 先看状态再填。
        assert tags.index("table") < tags.index("form")

    def test_全空的表格不显示(self):
        """⚠️ **一天都没填时不显示表格**（2026-11-23 业务确认）。

        全「—」的表格跟下面的输入框**说的是同一句话**
        ⇒ 重复且占高 ⇒ 改成一行文字。
        """
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[["普通视频", "—", "待填"], ["OOTD", "—", "待填"]],
            pending=[("video_normal_count", "普通视频"),
                     ("video_ootd_count", "OOTD")],
            employee_id="E_EDITOR",
        )
        tags = [e.get("tag") for e in card["body"]["elements"]]
        assert "table" not in tags
        assert "form" in tags
        # ⚠️ **改用一行文字说清楚要填什么**
        text = json.dumps(card, ensure_ascii=False)
        assert "今天还没填：普通视频、OOTD" in text

    def test_表格结构合法(self):
        """⚠️ **飞书对非法表格不报错**，只显示空白 ⇒ 必须固化结构。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[["普通视频", "8 条", "已填"], ["OOTD", "—", "待填"]],
            pending=[("video_ootd_count", "OOTD")],
            employee_id="E_EDITOR",
        )
        table = next(
            e for e in card["body"]["elements"] if e.get("tag") == "table"
        )
        # ⚠️ ``page_size`` 官方范围 [1,10]
        assert 1 <= table["page_size"] <= 10
        assert len(table["columns"]) == 3
        # ⚠️ **rows 是对象数组**，键是 columns[].name
        for row in table["rows"]:
            assert isinstance(row, dict)
            for column in table["columns"]:
                assert column["name"] in row
                assert column.get("display_name")
                assert column.get("data_type")

    def test_已填完不给表单(self):
        """⚠️ **都填完了还发表单 = 骚扰**。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[["普通视频", "8 条", "已填"]],
            pending=[],
            employee_id="E_EDITOR",
        )
        tags = [e.get("tag") for e in card["body"]["elements"]]
        assert "form" not in tags

    def test_表单只放待填指标(self):
        """⚠️ **已填的指标不该再问一遍**。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[["普通视频", "8 条", "已填"], ["OOTD", "—", "待填"]],
            pending=[("video_ootd_count", "OOTD")],
            employee_id="E_EDITOR",
        )
        form = next(
            e for e in card["body"]["elements"] if e.get("tag") == "form"
        )
        names = [e.get("name") for e in form["elements"] if e.get("tag") == "input"]
        # ⚠️ **备注框也在表单里**（2026-11-23 业务确认）
        assert names == ["video_ootd_count", "remark"]

    def test_回执卡的正文元素形态合法(self):
        """🔴⚠️ **回执卡发不出去 = 用户看到「显示出错了重试」**。

        ⚠️ **2026-11-23 真机实测踩到的坑**
        --------------------------------
        ``plain_text`` **不能**直接当 body 元素 —— 飞书会**拒收整张卡**::

            type of element is not supported tag: plain_text,
            path: ROOT -> body -> elements -> [0]

        ⇒ 数据其实**已经存进库了**，但回执卡发不出去 ⇒ 用户以为失败 ⇒ 重试。
        ⇒ ``plain_text`` 只能出现在**字段值**里
        （label / placeholder / button.text / header.title / div.text）。
        """
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_saved_card(
            day_text="11月23日（周一）",
            saved=[("普通视频", 8.0)],
            remark="今天剪了 8 条",
            kind="count",
        )
        elements = card["body"]["elements"]
        assert elements
        for element in elements:
            assert element.get("tag") != "plain_text", (
                "❌ plain_text 不能直接当 body 元素 —— 飞书会拒收整张卡"
            )
            assert element.get("tag") in ("div", "markdown"), (
                f"未知的 body 元素形态：{element.get('tag')}"
            )

    def test_所有卡片的正文元素形态合法(self):
        """🔴⚠️ **一次性守住所有卡片** —— 别再出现「某张卡发不出去」。

        ⚠️ **为什么要全量扫** ——
        这个 bug 是**逐张卡暴露**的：填报卡能发、回执卡发不出去
        ⇒ 只有一次「把所有构造器都跑一遍」才能提前发现。
        """
        from backend.app.services import feishu_card

        cards = [
            feishu_card.build_daily_metric_card(
                day="2026-11-23", day_text="x",
                status_rows=[["普通视频", "8 条", "已填"]],
                pending=[("video_ootd_count", "OOTD")],
                employee_id="E1", screenshot_hint=True,
            ),
            feishu_card.build_daily_metric_saved_card(
                day_text="x", saved=[("普通视频", 8.0)], kind="count",
            ),
            feishu_card.build_daily_metric_saved_card(
                day_text="x", saved=[("完播率", 0.35)], kind="rate",
            ),
            feishu_card.build_daily_metric_pending_card(
                day_text="x", pending=[("video_ootd_count", "OOTD")],
            ),
            feishu_card.build_leader_daily_status_card(
                day="2026-11-23", day_text="x",
                rows=[["佟瑶", "普通视频", "OOTD"]],
                all_done=False,
                pending=[("video_completion_rate", "完播率")],
                targets=[("E1", "佟瑶")], employee_id="E2",
            ),
            feishu_card.build_error_card("出错了"),
            feishu_card.build_manual_metric_saved_card(
                metric_date="2026-11-23", metric_name="完播率", value=0.35,
            ),
            feishu_card.build_evidence_saved_card(
                has_metric=True, metric_date="2026-11-23",
            ),
            # ⚠️ **P0-8 的选择同事卡也要守** ——
            #    它是人名卡片，最容易写成 plain_text（markdown 会被解析成 @某人）。
            feishu_card.build_qa_subject_picker_card(
                candidates=[
                    {"employee_id": "E1", "name": "佟瑶", "label": "佟瑶"},
                    {"employee_id": "E2", "name": "佟瑶", "label": "佟瑶（剪辑）"},
                ],
                question="查一下佟瑶的绩效",
                period="2026-11",
            ),
            feishu_card.build_qa_task_picker_card(
                candidates=[
                    {"task_id": "T1", "label": "2026 年 11 月月度考核"},
                    {"task_id": "T2", "label": "2026 年 11 月季度考核"},
                ],
                period="2026-11",
                question="我的绩效多少分",
            ),
        ]
        allowed = {
            "markdown", "div", "hr", "img", "column_set", "column",
            "form", "table", "action", "note", "collapsible_panel",
            "chart", "person", "interaction", "overflow",
        }
        for card in cards:
            elements = (card.get("body") or {}).get("elements") or []
            for element in elements:
                tag = element.get("tag")
                assert tag != "plain_text", (
                    f"❌ plain_text 不能当 body 元素：{element}"
                )
                assert tag in allowed, f"未知的 body 元素形态：{tag}"

    def test_表单含备注框(self):
        """⚠️ **剪辑要填备注** —— 业务确认（2026-11-23）。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[],
            pending=[("video_normal_count", "普通视频")],
            employee_id="E_EDITOR",
        )
        form = next(
            e for e in card["body"]["elements"] if e.get("tag") == "form"
        )
        remark = next(
            e for e in form["elements"] if e.get("name") == "remark"
        )
        assert remark["tag"] == "input"
        # ⚠️ **多行文本** —— 飞书只有 text / multiline_text / password
        assert remark["input_type"] == "multiline_text"

    def test_备注是选填的(self):
        """⚠️ **备注不是必填**（2026-11-23 业务确认）—— 不填也能提交。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[],
            pending=[("video_normal_count", "普通视频")],
            employee_id="E_EDITOR",
        )
        form = next(
            e for e in card["body"]["elements"] if e.get("tag") == "form"
        )
        remark = next(
            e for e in form["elements"] if e.get("name") == "remark"
        )
        assert remark["required"] is False
        # ⚠️ **标签上不能写「必填」** —— 飞书会同时显示红星和「必填」字样
        assert "必填" not in remark["label"]["content"]

    def test_表单没有文件上传控件(self):
        """⚠️⚠️ **飞书卡片放不了上传控件**（2026-11-23 真机实测）。

        ``input_type="file"`` → ``invalid input type``；
        ``file_input`` / ``file_upload`` tag → ``not support tag``。

        ⇒ 表单里**绝不能**出现文件控件 ⇒ 截图走「另外发到聊天」。
        """
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[],
            pending=[("video_normal_count", "普通视频")],
            employee_id="E_EDITOR",
            screenshot_hint=True,
        )
        form = next(
            e for e in card["body"]["elements"] if e.get("tag") == "form"
        )
        for element in form["elements"]:
            assert element.get("input_type") != "file", "飞书不接受文件控件"
            assert element.get("tag") not in ("file_input", "file_upload")
        # ⚠️ **但要有指引** —— 让剪辑知道截图发到聊天里
        text = json.dumps(card, ensure_ascii=False)
        assert "另外发到本对话" in text

    def test_提交按钮在表单内(self):
        """⚠️ **提交按钮必须内嵌 form** —— 放在外会报「no submit button」。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_daily_metric_card(
            day="2026-11-23", day_text="x",
            status_rows=[["普通视频", "—", "待填"]],
            pending=[("video_normal_count", "普通视频")],
            employee_id="E_EDITOR",
        )
        form = next(
            e for e in card["body"]["elements"] if e.get("tag") == "form"
        )
        buttons = [e for e in form["elements"] if e.get("tag") == "button"]
        assert len(buttons) == 1
        assert buttons[0].get("form_action_type") == "submit"

    def test_主管卡有表单但只填比率(self):
        """⚠️ **2026-11-23 角色倒转** —— 主管卡**有**表单。

        ⚠️ **但只能填比率**（完播率 / 留存率）——
        主管**不能**替剪辑填条数（那正是「A 的产出记到 B 头上」）。
        """
        from backend.app.services import feishu_card

        card = feishu_card.build_leader_daily_status_card(
            day="2026-11-23", day_text="x",
            rows=[["李婵雨", "普通视频", "OOTD"]],
            all_done=False,
            pending=[("video_completion_rate", "完播率")],
            targets=[("E_EDITOR", "李婵雨")],
            employee_id="E_LEADER",
        )
        tags = [e.get("tag") for e in card["body"]["elements"]]
        assert "table" in tags
        assert "form" in tags
        form = next(
            e for e in card["body"]["elements"] if e.get("tag") == "form"
        )
        names = [e.get("name") for e in form["elements"] if e.get("tag") == "input"]
        # ⚠️ **第一个控件是「填给谁」** —— 飞书没有下拉控件
        assert names[0] == "target_employee"
        assert "video_completion_rate" in names

    def test_主管卡没有目标人时不给表单(self):
        """⚠️ **不知道填给谁就不该给表单** —— 认不出目标人。"""
        from backend.app.services import feishu_card

        card = feishu_card.build_leader_daily_status_card(
            day="2026-11-23", day_text="x",
            rows=[["李婵雨", "普通视频", "OOTD"]],
            all_done=False,
            pending=[("video_completion_rate", "完播率")],
            targets=[],
            employee_id="E_LEADER",
        )
        tags = [e.get("tag") for e in card["body"]["elements"]]
        assert "form" not in tags


# ===========================================================================
# 2. 推送对象（板块隔离）
# ===========================================================================


class TestPushTargets:
    def test_只推短视频部门(self, db, org):
        """⚠️ **板块隔离** —— 仓储部的人绝不能收到剪辑填报提醒。"""
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        editor, ware, _ = org
        targets = editors_for_push(db, "2026-11-23")
        ids = {t.id for t in targets}
        assert "E_EDITOR" in ids
        assert "E_WARE" not in ids

    def test_只推剪辑_不推主播编导(self, db, org):
        """⚠️ **板块里只有剪辑要报成片数据**。

        短视频部有主播、编导、运营 ——
        推给他们 = 天天骚扰跟他们无关的人。
        """
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        targets = editors_for_push(db, "2026-11-23")
        ids = {t.id for t in targets}
        assert "E_EDITOR" in ids
        # ⚠️ **主播（巫宜珊）不收** —— 她产出的是口播成品，业务已确认不用管
        assert "E_ANCHOR" not in ids
        # ⚠️ **编导（张洁）不收** —— 她是主管，走汇总卡
        assert "E_LEADER" not in ids

    def test_没绑飞书身份的不推(self, db, org):
        """⚠️ **没有 open_id 根本发不出去** ⇒ 跳过。"""
        from backend.app.domain.models import Employee
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        db.add(Employee(
            id="E_NOFEISHU", name="没绑飞书", department_id="D_VIDEO",
            employment_status="active", account_status="enabled",
        ))
        db.commit()
        targets = editors_for_push(db, "2026-11-23")
        assert "E_NOFEISHU" not in {t.id for t in targets}

    def test_离职的不推(self, db, org):
        from backend.app.domain.models import Employee
        from backend.app.services.feishu_daily_metric_push import editors_for_push

        db.add(Employee(
            id="E_LEFT", name="离职了", department_id="D_VIDEO",
            employment_status="resigned", account_status="enabled",
            feishu_open_id="ou_left",
        ))
        db.commit()
        targets = editors_for_push(db, "2026-11-23")
        assert "E_LEFT" not in {t.id for t in targets}

    def test_主管识别(self, db, org):
        """⚠️ **主管是汇报线上的上级** —— 不是运营中心的老总。"""
        from backend.app.services.feishu_daily_push import leaders_of

        editor, _, leader = org
        found = leaders_of(db, editor)
        assert "E_LEADER" in {e.id for e in found}

    def test_主管不含上级部门leader(self, db, org):
        """⚠️⚠️ **核心回归** —— 上级部门的 leader **不是**本部门的主管。

        真实组织（2026-11-23 实测库）::

            运营中心 (leader=杨飞 总经理)
            └── 短视频 (leader=刘旭 运营)
                ├── 张洁  编导   ← ★ 两位剪辑的**直属上级**
                │   ├── 李婵雨 剪辑
                │   └── 佟瑶   剪辑

        张洁的 ``manager_id`` 指向**杨飞**（她挂在运营中心那条线上）⇒
        若沿汇报线再往上走，**杨飞（总经理）就会被当成「剪辑的主管」**
        ⇒ 总经理每天收到「李婵雨还没填完播率」。

        ⇒ 口径 = 直属上级 + **本部门** leader。
        """
        from backend.app.domain.models import Employee
        from backend.app.services.feishu_daily_push import leaders_of

        # ⚠️ **杨飞是运营中心的 leader** —— 但运营中心是短视频的**上级部门**
        boss = Employee(
            id="E_BOSS", name="杨飞", department_id="D_CENTER", position="总经理",
            employment_status="active", account_status="enabled",
            feishu_open_id="ou_boss",
        )
        db.add(boss)
        # ⚠️ **编导挂在运营中心那条线上** ⇒ 她的上级就是总经理
        db.get(Employee, "E_LEADER").manager_id = "E_BOSS"
        db.commit()

        editor, _, _ = org
        found = leaders_of(db, editor)
        ids = {e.id for e in found}
        assert "E_LEADER" in ids
        # ⚠️ **总经理不该在名单里**
        assert "E_BOSS" not in ids


# ===========================================================================
# 3. 填报状态计算
# ===========================================================================


class TestStatus:
    def test_待填指标(self, db, org):
        """⚠️ **剪辑待填的是三条数量指标**（角色倒转）。"""
        from backend.app.services.feishu_daily_metric_push import missing_metrics

        editor, _, _ = org
        pending = missing_metrics(db, editor.id, "2026-11-23")
        assert len(pending) == 3
        assert {code for code, _ in pending} == {
            "video_normal_count",
            "video_ootd_count",
            "video_agent_count",
        }

    def test_填了一个就少一个(self, db, org):
        from backend.app.services.feishu_daily_metric_push import missing_metrics
        from backend.app.services.pan_manual_metric_service import (
            METRIC_NORMAL_COUNT,
            PanManualMetricService,
        )

        editor, _, _ = org
        PanManualMetricService(db).submit(
            employee_id=editor.id,
            metric_date="2026-11-23",
            metric_code=METRIC_NORMAL_COUNT,
            metric_value=8,
            scope="day",
        )
        db.commit()
        pending = missing_metrics(db, editor.id, "2026-11-23")
        assert len(pending) == 2
        assert "video_normal_count" not in {code for code, _ in pending}

    def test_逐文件填报不算日填报(self, db, org):
        """⚠️ **口径必须分清** —— 逐条成片的填报不是「这天填过了」。"""
        from backend.app.services.feishu_daily_metric_push import missing_metrics
        from backend.app.services.pan_manual_metric_service import (
            METRIC_NORMAL_COUNT,
            PanManualMetricService,
        )

        editor, _, _ = org
        PanManualMetricService(db).submit(
            employee_id=editor.id,
            metric_date="2026-11-23",
            metric_code=METRIC_NORMAL_COUNT,
            metric_value=1,
            scope="file",
            target_key="FILE_1",
        )
        db.commit()
        pending = missing_metrics(db, editor.id, "2026-11-23")
        assert len(pending) == 3

    def test_主管汇总行(self, db, org):
        """⚠️ **主管汇总看的是「条数」指标**（剪辑填的那些）。"""
        from backend.app.services.feishu_daily_metric_push import build_leader_status_rows

        editor, _, _ = org
        rows = build_leader_status_rows(db, [editor.id], "2026-11-23")
        assert len(rows) == 1
        assert rows[0][0] == "李婵雨"
        assert rows[0][2] == "普通视频、OOTD、智能体"

    def test_数量显示带条字(self, db, org):
        """⚠️ **数量与比率的显示不同** —— 「8 条」不是「8.0%」。"""
        from backend.app.services.feishu_daily_metric_push import build_daily_status_rows
        from backend.app.services.pan_manual_metric_service import (
            METRIC_NORMAL_COUNT,
            PanManualMetricService,
        )

        editor, _, _ = org
        PanManualMetricService(db).submit(
            employee_id=editor.id,
            metric_date="2026-11-23",
            metric_code=METRIC_NORMAL_COUNT,
            metric_value=8,
            scope="day",
        )
        db.commit()
        rows = build_daily_status_rows(db, editor.id, "2026-11-23")
        assert rows[0][1] == "8 条"


# ===========================================================================
# 4. 幂等
# ===========================================================================


class TestIdempotency:
    def test_唯一约束存在(self, db):
        """⚠️ **多实例兜底** —— 靠数据库唯一约束，不靠内存状态。"""
        from backend.app.domain.pan_workload_models import FeishuDailyPushRecord as R

        names = {c.name for c in R.__table__.columns}
        assert {"push_day", "kind", "target_id"} <= names
        constraints = {
            tuple(sorted(c.name for c in constraint.columns))
            for constraint in R.__table__.constraints
            if constraint.name == "uq_feishu_daily_push_record_day_kind"
        }
        assert ("kind", "push_day", "target_id") in constraints

    def test_重复插入被挡(self, db, org):
        from sqlalchemy.exc import IntegrityError

        from backend.app.domain.pan_workload_models import FeishuDailyPushRecord as R

        editor, _, _ = org
        db.add(R(push_day="2026-11-23", kind="editor", target_id=editor.id))
        db.commit()
        db.add(R(push_day="2026-11-23", kind="editor", target_id=editor.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_已推送判定(self, db, org):
        from backend.app.domain.pan_workload_models import FeishuDailyPushRecord as R
        from backend.app.services.feishu_daily_push import already_pushed_today

        editor, _, _ = org
        assert already_pushed_today(db, "2026-11-23") is False
        db.add(R(push_day="2026-11-23", kind="editor", target_id=editor.id, status="sent"))
        db.commit()
        assert already_pushed_today(db, "2026-11-23") is True

    def test_失败的不算已推送(self, db, org):
        """⚠️ **推失败了下次还要推** —— 否则提醒就永久丢了。"""
        from backend.app.domain.pan_workload_models import FeishuDailyPushRecord as R
        from backend.app.services.feishu_daily_push import already_pushed_today

        editor, _, _ = org
        db.add(R(push_day="2026-11-23", kind="editor", target_id=editor.id, status="failed"))
        db.commit()
        assert already_pushed_today(db, "2026-11-23") is False


# ===========================================================================
# 5. 卡片回调（归属安全）
# ===========================================================================


BOT_TOKEN = "v_bot_token_daily_push"


@pytest.fixture()
def http(db, monkeypatch):
    from backend.app.core.config import settings
    from backend.app.main import app
    from backend.app.routes import feishu_bot as feishu_bot_routes

    monkeypatch.setattr(settings, "feishu_bot_event_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "feishu_approval_webhook_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "feishu_encrypt_key", "")
    monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
    monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")

    sent: list[dict] = []

    class _FakeClient:
        def send_card_message(self, receive_id, card, **kwargs):
            sent.append({"receive_id": receive_id, "card": card, **kwargs})
            return {"code": 0, "data": {"message_id": "om_fake"}}

    monkeypatch.setattr(feishu_bot_routes, "_client", lambda: _FakeClient())

    def _override():
        yield db

    app.dependency_overrides[feishu_bot_routes.get_db] = _override
    with TestClient(app) as c:
        c.feishu_sent = sent  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _daily_action_event(
    *, open_id: str, target_employee_id: str, form_value: dict, event_id: str = "evt_daily_1"
):
    """构造「每日填报表单提交」的回调报文。

    ⚠️ **每次提交必须换 ``event_id``** ——
    飞书每次点击都会给一个新的 ``event_id``，
    而系统按 ``event_id`` 做幂等去重 ⇒ 复用同一个 id 会被当成「重推」而跳过。
    """
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "card.action.trigger",
            "token": BOT_TOKEN,
        },
        "event": {
            "operator": {
                "open_id": open_id,
                "user_id": "u_" + open_id,
                "union_id": "on_" + open_id,
            },
            "action": {
                "value": {
                    "action": "submit_daily_metric",
                    "employee_id": target_employee_id,
                    "metric_date": "2026-11-23",
                    "session_id": "sess_x",
                    "generation": "daily_2026-11-23_E_EDITOR",
                },
                "form_value": form_value,
                "tag": "button",
                "name": "daily_metric_submit_btn",
            },
        },
    }


ACTIONS_URL = "/v1/feishu/bot/card/actions"


class TestDailySubmit:
    def test_本人提交成功(self, http, db, org):
        """⚠️ **剪辑填数量** —— 「8」是 8 条，**不是** 0.08。"""
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={"video_normal_count": "8", "remark": "今天剪了 8 条"},
        ))
        assert resp.status_code == 200
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 1
        assert rows[0].metric_code == "video_normal_count"
        assert rows[0].metric_value == pytest.approx(8.0)

    def test_条数不被当成百分比(self, http, db, org):
        """🔴⚠️ **最危险的坑** —— ``parse_rate("8")`` 会返回 **0.08**。

        ⇒ 「我剪了 8 条」变成「0.08 条」⇒ **数据静默错掉**。
        ⇒ 条数必须走自己的解析口径。
        """
        editor, _, _ = org
        http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={"video_normal_count": "8"},
        ))
        row = db.scalars(select(PanManualMetric)).one()
        assert row.metric_value == pytest.approx(8.0), "8 条被当成了 0.08"

    def test_备注被存下来(self, http, db, org):
        """⚠️ **备注填了就必须真的落库**。"""
        editor, _, _ = org
        http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={"video_normal_count": "8", "remark": "有 2 条返工"},
        ))
        row = db.scalars(select(PanManualMetric)).one()
        assert row.remark == "有 2 条返工"

    def test_备注留空也能提交(self, http, db, org):
        """⚠️ **备注是选填的** —— 不填备注**不能**被拒。"""
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={"video_normal_count": "8"},
        ))
        assert resp.status_code == 200
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 1
        assert rows[0].metric_code == "video_normal_count"
        # ⚠️ **备注留空时不该留着占位文案**
        assert rows[0].remark != "飞书每日填报表单"

    def test_备注只有空白也当没填(self, http, db, org):
        """⚠️ **只打空格 = 没填** —— 留着空白备注没意义。"""
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={"video_normal_count": "8", "remark": "   \n  "},
        ))
        assert resp.status_code == 200
        row = db.scalars(select(PanManualMetric)).one()
        assert row.remark != "   \n  "

    def test_不能替别人填(self, http, db, org):
        """⚠️ **核心安全回归** —— 卡片被转发，B 替 A 填必须被拒。"""
        editor, ware, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_ware",
            target_employee_id=editor.id,
            form_value={"video_normal_count": "8"},
        ))
        assert resp.status_code == 200
        # ⚠️ **一条都不能落库**
        assert db.scalars(select(PanManualMetric)).all() == []
        card = http.feishu_sent[-1]["card"]
        assert "不能替别人提交" in json.dumps(card, ensure_ascii=False)

    def test_非板块部门提交被拒(self, http, db, org):
        """⚠️ **仓储部的人不能写剪辑的数据**。"""
        _, ware, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_ware",
            target_employee_id=ware.id,
            form_value={"video_normal_count": "8"},
        ))
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []
        card = http.feishu_sent[-1]["card"]
        assert "部门" in json.dumps(card, ensure_ascii=False)

    def test_数值认不出不猜(self, http, db, org):
        """⚠️ **绝不猜** —— 认不出就报错，不落库。"""
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={"video_normal_count": "还行"},
        ))
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []
        card = http.feishu_sent[-1]["card"]
        assert "没认出" in json.dumps(card, ensure_ascii=False)

    def test_三个指标都填_存三条(self, http, db, org):
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={
                "video_normal_count": "8",
                "video_ootd_count": "2",
                "video_agent_count": "1",
            },
        ))
        assert resp.status_code == 200
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 3
        assert {r.metric_code for r in rows} == {
            "video_normal_count", "video_ootd_count", "video_agent_count",
        }

    def test_部分填报_已填的照样存(self, http, db, org):
        """⚠️ **不能因为「不完整」就丢弃已填的数据**。"""
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id=editor.id,
            form_value={
                "video_normal_count": "8",
                "video_ootd_count": "还行",
            },
        ))
        assert resp.status_code == 200
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 1
        assert rows[0].metric_code == "video_normal_count"

    def test_重复提交_覆盖不追加(self, http, db, org):
        """⚠️ **改了再填是正常操作** ⇒ 覆盖，不是追加。"""
        editor, _, _ = org
        for index, value in enumerate(("8", "10")):
            http.post(ACTIONS_URL, json=_daily_action_event(
                open_id="ou_editor",
                target_employee_id=editor.id,
                form_value={"video_normal_count": value},
                event_id=f"evt_daily_{index + 1}",
            ))
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 1
        assert rows[0].metric_value == pytest.approx(10.0)


# ===========================================================================
# 5·一、★★ 数据真的落库了吗（跨连接验证）
# ===========================================================================


class TestPersistence:
    """🔴⚠️ **「同一个 session 里查得到」证明不了数据落库了**。

    ⚠️ **为什么**
    ----------
    SQLAlchemy 的 **autoflush** 会让**未提交**的行也能被同一个 session 查到
    ⇒ 「回调返回后查一下有数据」这种测法，**即使忘了 commit 也会过**。

    ⚠️ **内存库 + StaticPool 也不行**
    --------------------------------
    ``StaticPool`` 让所有 session **共用一条连接**
    ⇒ 第二个 session 看到的就是第一个 session 的那个事务
    ⇒ **没 commit 也能查到**
    （⚠️ 实测：去掉 ``db.commit()``，用 StaticPool 写的测试**照样通过**）

    ⇒ 必须用**文件型 SQLite** —— 不同连接各自独立提交、互相可见。

    ⚠️ **这正是「填完到底有没有存进去」的正解** ——
    业务上用户最关心的就是「填完立刻能查得到、刷新页面还在」。
    """

    @pytest.fixture()
    def file_db(self, db_file_engine):
        """把整个 fixture 链切到**文件型**引擎上。"""
        from backend.app.domain.models import Department, Employee, FeishuIdentity
        from backend.app.domain.pan_workload_models import DepartmentModule

        session = sessionmaker(bind=db_file_engine)()
        session.add(Department(id="D_VIDEO", name="短视频", active=True))
        session.add(Employee(
            id="E_EDITOR", name="李婵雨", department_id="D_VIDEO", position="剪辑",
            manager_id="E_LEADER", employment_status="active", account_status="enabled",
            feishu_open_id="ou_editor", feishu_user_id="u_editor",
        ))
        session.add(Employee(
            id="E_LEADER", name="张洁", department_id="D_VIDEO", position="编导",
            employment_status="active", account_status="enabled",
            feishu_open_id="ou_leader", feishu_user_id="u_leader",
        ))
        # ⚠️ **飞书身份必须真的绑上** —— 否则回调解析不出员工
        session.add(FeishuIdentity(
            id="FI_E", employee_id="E_EDITOR",
            identity_type="open_id", identity_value="ou_editor", active=True,
        ))
        session.add(FeishuIdentity(
            id="FI_L", employee_id="E_LEADER",
            identity_type="open_id", identity_value="ou_leader", active=True,
        ))
        session.add(DepartmentModule(
            id="dm_1", department_id="D_VIDEO", module_code="pan_workload", active=True,
        ))
        session.add(PanWorkloadPolicy(
            id="pwp_1", department_id="D_VIDEO", position="剪辑",
            role="reporter", active=True, note="",
        ))
        session.add(PanWorkloadPolicy(
            id="pwp_2", department_id="D_VIDEO", position="编导",
            role="reviewer", active=True, note="",
        ))
        session.commit()
        try:
            yield session
        finally:
            session.close()

    @pytest.fixture()
    def file_http(self, file_db, db_file_engine, monkeypatch):
        """走**文件型**库的 HTTP 客户端。"""
        from backend.app.core.config import settings
        from backend.app.main import app
        from backend.app.routes import feishu_bot as feishu_bot_routes

        monkeypatch.setattr(settings, "feishu_bot_event_token", BOT_TOKEN)
        monkeypatch.setattr(settings, "feishu_approval_webhook_token", BOT_TOKEN)
        monkeypatch.setattr(settings, "feishu_encrypt_key", "")
        monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
        monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")

        sent: list[dict] = []

        class _FakeClient:
            def send_card_message(self, receive_id, card, **kwargs):
                sent.append({"receive_id": receive_id, "card": card, **kwargs})
                return {"code": 0, "data": {"message_id": "om_fake"}}

        monkeypatch.setattr(feishu_bot_routes, "_client", lambda: _FakeClient())

        def _override():
            yield file_db

        app.dependency_overrides[feishu_bot_routes.get_db] = _override
        with TestClient(app) as c:
            c.feishu_sent = sent  # type: ignore[attr-defined]
            yield c
        app.dependency_overrides.clear()

    def test_剪辑填报_换独立连接也能查到(self, file_http, file_db, db_file_engine):
        """⚠️ **核心** —— 填完立刻落库，不依赖任何后续动作。"""
        resp = file_http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id="E_EDITOR",
            form_value={"video_normal_count": "8", "remark": "今天剪了 8 条"},
        ))
        assert resp.status_code == 200

        # ⚠️ **另一条独立连接** —— 绕开原事务的 autoflush 假象
        with sessionmaker(bind=db_file_engine)() as other:
            rows = other.scalars(select(PanManualMetric)).all()
            assert len(rows) == 1
            assert rows[0].employee_id == "E_EDITOR"
            assert rows[0].metric_code == "video_normal_count"
            assert rows[0].metric_value == pytest.approx(8.0)
            assert rows[0].remark == "今天剪了 8 条"

    def test_主管填报_换独立连接也能查到(self, file_http, file_db, db_file_engine):
        """⚠️ **主管的效果指标也是立即落库**。"""
        resp = file_http.post(ACTIONS_URL, json=_leader_action_event(
            open_id="ou_leader",
            form_value={
                "target_employee": "李婵雨",
                "video_completion_rate": "35%",
            },
        ))
        assert resp.status_code == 200

        with sessionmaker(bind=db_file_engine)() as other:
            rows = other.scalars(select(PanManualMetric)).all()
            assert len(rows) == 1
            assert rows[0].employee_id == "E_EDITOR"
            assert rows[0].metric_code == "video_completion_rate"
            assert rows[0].metric_value == pytest.approx(0.35)

    def test_被拒的提交不会落库(self, file_http, file_db, db_file_engine):
        """⚠️ **拒绝的提交必须真的没写进去** —— 换独立连接验证。"""
        resp = file_http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id="E_LEADER",  # ⚠️ 不是本人 ⇒ 被拒
            form_value={"video_normal_count": "8"},
        ))
        assert resp.status_code == 200

        with sessionmaker(bind=db_file_engine)() as other:
            assert other.scalars(select(PanManualMetric)).all() == []

    def test_回执卡发不出去_数据仍然要落库(self, file_http, file_db, db_file_engine, monkeypatch):
        """🔴⚠️🔴 **核心回归** —— 回执失败**绝不能**回滚已保存的数据。

        ⚠️ **2026-11-23 真机踩出来的血泪 bug**
        --------------------------------------
        原来的顺序是 ``send_card()`` → ``db.commit()``：

        1. 数据写进库（未提交）；
        2. ``send_card()`` 发回执卡 —— **卡结构错了，飞书拒收** ⇒ 抛异常；
        3. 外层 ``db.rollback()`` ⇒ **刚存进去的数据全没了**；
        4. 用户看到「显示出错了重试」⇒ 重试 ⇒ 又失败 ⇒ **永远填不进去**。

        ⇒ 修法：**先 commit，再发回执**；回执失败只记日志。

        ⚠️ **「用户填了但库是空的」就是这个 bug 的症状。**
        """
        from backend.app.routes import feishu_bot as feishu_bot_routes

        # ⚠️ **让回执卡发送失败** —— 模拟飞书拒收
        class _BoomClient:
            def send_card_message(self, receive_id, card, **kwargs):
                raise RuntimeError("飞书拒收：type of element is not supported tag")

        monkeypatch.setattr(feishu_bot_routes, "_client", lambda: _BoomClient())

        resp = file_http.post(ACTIONS_URL, json=_daily_action_event(
            open_id="ou_editor",
            target_employee_id="E_EDITOR",
            form_value={"video_normal_count": "8", "remark": "今天剪了 8 条"},
        ))
        # ⚠️ **回执失败不该把整个请求变成 500** —— 数据已保存
        assert resp.status_code == 200

        # ⚠️ **关键断言** —— 数据必须还在
        with sessionmaker(bind=db_file_engine)() as other:
            rows = other.scalars(select(PanManualMetric)).all()
            assert len(rows) == 1
            assert rows[0].metric_code == "video_normal_count"
            assert rows[0].metric_value == pytest.approx(8.0)
            assert rows[0].remark == "今天剪了 8 条"

    def test_主管回执卡发不出去_数据仍然要落库(self, file_http, file_db, db_file_engine, monkeypatch):
        """🔴⚠️ **主管那边同样不能丢数据**。"""
        from backend.app.routes import feishu_bot as feishu_bot_routes

        class _BoomClient:
            def send_card_message(self, receive_id, card, **kwargs):
                raise RuntimeError("飞书拒收")

        monkeypatch.setattr(feishu_bot_routes, "_client", lambda: _BoomClient())

        resp = file_http.post(ACTIONS_URL, json=_leader_action_event(
            open_id="ou_leader",
            form_value={
                "target_employee": "李婵雨",
                "video_completion_rate": "35%",
            },
        ))
        assert resp.status_code == 200

        with sessionmaker(bind=db_file_engine)() as other:
            rows = other.scalars(select(PanManualMetric)).all()
            assert len(rows) == 1
            assert rows[0].metric_code == "video_completion_rate"
            assert rows[0].metric_value == pytest.approx(0.35)


# ===========================================================================
# 6. 端到端推送
# ===========================================================================


class TestPushEndToEnd:
    """⚠️ **推送主链路** —— 这里必须真跑一遍 ``push_daily_metrics``。

    ⇒ 才能发现「函数引用了不存在的名字」这类错误
    （纯结构测试不执行这些分支 ⇒ 发现不了）。
    """

    @pytest.fixture()
    def push_env(self, db, monkeypatch):
        from backend.app.core.config import settings
        from backend.app.services import feishu_daily_push

        monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
        monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")
        monkeypatch.setattr(settings, "feishu_tenant_id", "")

        sent: list[dict] = []

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def send_card_message(self, receive_id, card, **kwargs):
                sent.append({"receive_id": receive_id, "card": card, **kwargs})
                return {"code": 0}

            def close(self):
                pass

        # ⚠️ **模块内的 import 是延迟的** ⇒ 得 patch 真正的来源模块。
        monkeypatch.setattr(
            "backend.app.services.feishu_client.FeishuClient", _FakeClient
        )
        return sent

    def test_剪辑收到卡_主管收到汇总(self, db, org, push_env):
        from backend.app.services.feishu_daily_push import push_daily_metrics

        editor, _, leader = org
        result = push_daily_metrics(db, "2026-11-23")
        db.commit()

        assert result["editors"] == 1
        assert result["leaders"] == 1

        # ⚠️ **剪辑收到的是「表单」卡**（一天没填 ⇒ 空表格不显示）
        editor_card = next(
            c["card"] for c in push_env if c["receive_id"] == "ou_editor"
        )
        tags = [e.get("tag") for e in editor_card["body"]["elements"]]
        assert "form" in tags
        # ⚠️ **全「—」的表格不显示** —— 跟输入框重复
        assert "table" not in tags

        # ⚠️ **主管收到的是汇总卡**（有表格 + 效果指标表单 —— 角色倒转）
        leader_card = next(
            c["card"] for c in push_env if c["receive_id"] == "ou_leader"
        )
        leader_tags = [e.get("tag") for e in leader_card["body"]["elements"]]
        assert "table" in leader_tags
        assert "form" in leader_tags

    def test_剪辑卡只有数量指标(self, http, db, org, push_env):
        """🔴⚠️ **核心回归** —— 剪辑卡里**绝不能**出现比率指标。

        角色倒转的意义就在这儿：
        剪辑填**数量**（普通视频/OOTD/智能体），
        完播率/留存率**由主管填** ⇒
        ⇒ 剪辑卡里出现「完播率」就是**角色倒转没落地**。
        """
        from backend.app.services.feishu_daily_push import push_daily_metrics

        push_daily_metrics(db, "2026-11-23")
        db.commit()
        editor_card = next(
            c["card"] for c in push_env if c["receive_id"] == "ou_editor"
        )
        form = next(
            e for e in editor_card["body"]["elements"] if e.get("tag") == "form"
        )
        names = [e.get("name") for e in form["elements"] if e.get("tag") == "input"]
        assert "video_completion_rate" not in names
        assert "video_retention_rate" not in names
        assert "video_normal_count" in names
        assert "video_ootd_count" in names
        assert "video_agent_count" in names

    def test_已填完不推(self, db, org, push_env):
        """⚠️ **都填完了还推 = 骚扰**。"""
        from backend.app.services.feishu_daily_push import push_daily_metrics
        from backend.app.services.pan_manual_metric_service import (
            METRIC_AGENT_COUNT,
            METRIC_NORMAL_COUNT,
            METRIC_OOTD_COUNT,
            PanManualMetricService,
        )

        editor, _, _ = org
        svc = PanManualMetricService(db)
        for code, value in (
            (METRIC_NORMAL_COUNT, 8),
            (METRIC_OOTD_COUNT, 2),
            (METRIC_AGENT_COUNT, 1),
        ):
            svc.submit(
                employee_id=editor.id,
                metric_date="2026-11-23",
                metric_code=code,
                metric_value=value,
                scope="day",
            )
        db.commit()

        result = push_daily_metrics(db, "2026-11-23")
        db.commit()
        assert result["editors"] == 0
        assert result["skipped"] == 1
        # ⚠️ **剪辑不该收到任何卡**
        assert not [c for c in push_env if c["receive_id"] == "ou_editor"]

    def test_非板块部门不收到(self, db, org, push_env):
        """⚠️ **板块隔离** —— 仓储部的人绝不能收到剪辑填报提醒。"""
        from backend.app.services.feishu_daily_push import push_daily_metrics

        _, ware, _ = org
        push_daily_metrics(db, "2026-11-23")
        db.commit()
        assert not [c for c in push_env if c["receive_id"] == "ou_ware"]

    def test_推送记录落库(self, db, org, push_env):
        """⚠️ **幂等靠这张表** —— 没落库就挡不住多实例重复推。"""
        from backend.app.services.feishu_daily_push import push_daily_metrics

        push_daily_metrics(db, "2026-11-23")
        db.commit()

        rows = db.scalars(select(FeishuDailyPushRecord)).all()
        kinds = {(r.kind, r.target_id) for r in rows}
        assert ("editor", "E_EDITOR") in kinds
        assert ("leader", "E_LEADER") in kinds

    # ───────────────────────────────────────────────────────────────────
    # ⚠️⚠️ **重复推送（2026-11-24 修）**
    # ------------------------------------------------------------------
    # 之前只靠**唯一约束**兜底：先发消息，插入记录时才撞约束。
    # ⇒ 后果：消息**已经发出去了**，幂等锚点却没写进去
    #   （`_record` 里的异常被吞掉）⇒ 重复触发**拦不住**
    #   ⇒ 员工会**收到两张一样的卡**。
    #
    # ⇒ 修法：① 发送**前**先查「当天已推过」→ 跳过；
    #         ② `_record` 用 SAVEPOINT ⇒ 撞约束不牵连会话里的其它改动。

    def test_重复推送不重发(self, db, org, push_env):
        """⚠️ **一天只推一次** —— 第二次调用必须一条都不发。"""
        from backend.app.services.feishu_daily_push import push_daily_metrics

        push_daily_metrics(db, "2026-11-23")
        db.commit()
        first_count = len(push_env)
        assert first_count > 0, "第一次应该发出去了"

        result = push_daily_metrics(db, "2026-11-23")
        db.commit()

        assert len(push_env) == first_count, "重复推送**不应该**再发消息"
        assert result["editors"] == 0
        assert result["leaders"] == 0

    def test_重复推送后幂等锚点仍在(self, db, org, push_env):
        """
        ⚠️⚠️ **这条是本轮修复的核心**

        旧实现里 ``_record`` 撞唯一约束时调的是 ``db.rollback()``
        ⇒ 把**整个会话**的未提交改动一起丢了：
        - 前面几个人的幂等锚点（⇒ 下次又推一遍）；
        - ``bot_session.last_message_id = generation``
          （⇒ **旧卡片提交不失效** ⇒ 剪辑用上一张卡还能提交）。

        ⇒ 修法：用 SAVEPOINT（``begin_nested()``）把冲突隔离在这一条 INSERT 上。

        ⚠️ **怎么构造「撞约束」而不被前置的 skip 挡掉**
        ------------------------------------------------------
        新实现会在**发送前**就跳过「当天已推过」的人 ⇒ 根本走不到 ``_record``。
        ⇒ 要测 SAVEPOINT 这条路径，得**直接调 ``_record``** 制造冲突。
        """
        from backend.app.domain.pan_workload_models import FeishuDailyPushRecord as R
        from backend.app.services.feishu_daily_push import _record

        editor, _, _ = org
        # 先插一条（模拟「另一个实例已经推过」）
        db.add(R(push_day="2026-11-23", kind="editor", target_id=editor.id))
        db.commit()

        # ⚠️ **会话里先挂一个别的改动** ——
        #    旧实现（db.rollback()）会把它一起丢掉；
        #    新实现（SAVEPOINT）必须保住它。
        marker = R(push_day="2026-11-23", kind="leader", target_id=editor.id)
        db.add(marker)

        # 再写一条冲突的 ⇒ 撞唯一约束
        _record(db, day="2026-11-23", kind="editor", target_id=editor.id)

        # ⚠️ **关键断言**：先前那个改动**必须还在**
        db.flush()
        db.commit()

        rows = db.scalars(select(R)).all()
        kinds = {(r.kind, r.target_id) for r in rows}
        assert ("leader", editor.id) in kinds, (
            "⚠️ 幂等锚点被 rollback 掉了 ⇒ 下次推送会重复打扰员工"
        )

    def test_主管汇总也不重发(self, db, org, push_env):
        """⚠️ 主管汇总同样不能重发 —— 否则主管一天收好几张一样的卡。"""
        from backend.app.services.feishu_daily_push import push_daily_metrics

        push_daily_metrics(db, "2026-11-23")
        db.commit()
        leader_cards = [c for c in push_env if c["receive_id"] == "ou_leader"]
        assert leader_cards, "第一次应该给主管发了汇总"

        push_daily_metrics(db, "2026-11-23")
        db.commit()

        assert len([c for c in push_env if c["receive_id"] == "ou_leader"]) == len(leader_cards)

    def test_失败的不算已推送(self, db, org, monkeypatch):
        """⚠️ **推失败了下次还要推** —— 否则提醒永久丢失。"""
        from backend.app.core.config import settings
        from backend.app.services import feishu_daily_push

        monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
        monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")

        class _BoomClient:
            def __init__(self, *args, **kwargs):
                pass

            def send_card_message(self, *args, **kwargs):
                raise RuntimeError("飞书接口炸了")

            def close(self):
                pass

        monkeypatch.setattr(
            "backend.app.services.feishu_client.FeishuClient", _BoomClient
        )
        feishu_daily_push.push_daily_metrics(db, "2026-11-23")
        db.commit()

        from backend.app.services.feishu_daily_push import already_pushed_today

        # ⚠️ **失败的不算已推送** ⇒ 下一轮还会再试。
        assert already_pushed_today(db, "2026-11-23") is False
        rows = db.scalars(select(FeishuDailyPushRecord)).all()
        assert all(r.status == "failed" for r in rows)


# ===========================================================================
# 7. ★ 主管提交效果指标（2026-11-23 角色倒转）
# ===========================================================================


def _leader_action_event(
    *,
    open_id: str,
    form_value: dict,
    event_id: str = "evt_leader_1",
):
    """构造「主管提交效果指标」的回调报文。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "card.action.trigger",
            "token": BOT_TOKEN,
        },
        "event": {
            "operator": {
                "open_id": open_id,
                "user_id": "u_" + open_id,
                "union_id": "on_" + open_id,
            },
            "action": {
                "value": {
                    "action": "submit_leader_metric",
                    "employee_id": "E_LEADER",
                    "metric_date": "2026-11-23",
                    "session_id": "sess_x",
                    "generation": "leader_daily_2026-11-23_E_LEADER",
                },
                "form_value": form_value,
                "tag": "button",
                "name": "leader_metric_submit_btn",
            },
        },
    }


class TestLeaderMetricSubmit:
    """⚠️ **主管填效果指标** —— 角色倒转的另一半。

    ⚠️ **核心安全边界**
    1. 主管**只能**填**管理范围内**的剪辑 ⇒ 越权必须被拒；
    2. 主管**不能**替剪辑填**条数**（那是剪辑自己的产出）；
    3. **认不出目标人就不填** —— 绝不瞎猜一个目标人。
    """

    def test_主管给下属填完播率(self, http, db, org):
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_leader_action_event(
            open_id="ou_leader",
            form_value={
                "target_employee": "李婵雨",
                "video_completion_rate": "35%",
                "video_retention_rate": "62%",
            },
        ))
        assert resp.status_code == 200
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 2
        # ⚠️ **落在被填的人名下** —— 不是落在主管名下
        assert {r.employee_id for r in rows} == {editor.id}
        assert {r.metric_code for r in rows} == {
            "video_completion_rate", "video_retention_rate",
        }

    def test_主管不能填条数(self, http, db, org):
        """⚠️⚠️ **关键边界** —— 主管**不能**替剪辑填条数。

        业务确认：**剪辑填条数，主管填比率** ⇒
        ⇒ 主管卡只有比率输入框，后端也**只接受比率**。
        """
        editor, _, _ = org
        http.post(ACTIONS_URL, json=_leader_action_event(
            open_id="ou_leader",
            form_value={
                "target_employee": "李婵雨",
                "video_normal_count": "99",
            },
        ))
        rows = db.scalars(select(PanManualMetric)).all()
        assert rows == [], "主管不该能填条数"

    def test_越权填别人被拒(self, http, db, org):
        """🔴 **越权必须被拒** —— 仓储部主管不能给短视频剪辑填指标。"""
        from backend.app.domain.models import Employee

        _, _, _ = org
        # ⚠️ **另一个部门的剪辑** —— 不在短视频主管的管理范围内
        db.add(Employee(
            id="E_OUT", name="外部剪辑", department_id="D_WARE", position="剪辑",
            employment_status="active", account_status="enabled",
        ))
        db.commit()
        resp = http.post(ACTIONS_URL, json=_leader_action_event(
            open_id="ou_leader",
            form_value={
                "target_employee": "外部剪辑",
                "video_completion_rate": "35%",
            },
        ))
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []
        card = http.feishu_sent[-1]["card"]
        assert "管理范围" in json.dumps(card, ensure_ascii=False)

    def test_认不出目标人不填(self, http, db, org):
        """⚠️ **认不出就拒绝** —— 绝不瞎猜一个目标人。"""
        resp = http.post(ACTIONS_URL, json=_leader_action_event(
            open_id="ou_leader",
            form_value={
                "target_employee": "查无此人",
                "video_completion_rate": "35%",
            },
        ))
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []
        card = http.feishu_sent[-1]["card"]
        assert "没找到" in json.dumps(card, ensure_ascii=False)

    def test_剪辑不能走主管通道(self, http, db, org):
        """⚠️ **剪辑不是审阅人** ⇒ 不能提交效果指标。"""
        editor, _, _ = org
        resp = http.post(ACTIONS_URL, json=_leader_action_event(
            open_id="ou_editor",
            form_value={
                "target_employee": "李婵雨",
                "video_completion_rate": "35%",
            },
        ))
        assert resp.status_code == 200
        assert db.scalars(select(PanManualMetric)).all() == []


# ===========================================================================
# 8. 条数解析（独立于比率解析）
# ===========================================================================


class TestParseCount:
    """🔴⚠️ **条数绝不能走比率解析**。

    ``parse_rate("8")`` → **0.08**（裸数字按百分数理解）
    ⇒ 「我剪了 8 条」会变成「0.08 条」⇒ **数据静默错掉**。
    """

    def test_整数(self):
        from backend.app.services.feishu_manual_metric import parse_count

        assert parse_count("8") == 8.0
        assert parse_count(" 12 ") == 12.0
        assert parse_count("0") == 0.0

    def test_带条字(self):
        from backend.app.services.feishu_manual_metric import parse_count

        assert parse_count("8条") == 8.0

    def test_不认小数(self):
        """⚠️ **条数必须是整数** —— 8.5 条是什么？"""
        from backend.app.services.feishu_manual_metric import parse_count

        assert parse_count("8.5") is None

    def test_不认百分号(self):
        from backend.app.services.feishu_manual_metric import parse_count

        assert parse_count("35%") is None

    def test_认不出返回None(self):
        from backend.app.services.feishu_manual_metric import parse_count

        assert parse_count("还行") is None
        assert parse_count("") is None

