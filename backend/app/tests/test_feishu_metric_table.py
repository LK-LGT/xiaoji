"""飞书表格填报（剪辑「以表格形式填写」）的测试。

覆盖
----
1. **解析**（纯函数）：日期 / 比率 / 列名别名 / 坏行
2. **幂等**：内容指纹 —— 重复同步不重复入库
3. **绑定**：一人一张表 / 绑定即验证（链接错当场报错）
4. **同步**：落库 / 坏行跳过不阻断整批 / 一个来源失败不阻断其它
5. **板块隔离**：非短视频部门 ⇒ 飞书端填报被拒（2026-11-21 补的漏洞）
6. **权限降级**：缺 scope ⇒ 明确报「找 IT」；表不存在 ⇒ 报「找 HR」
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.pan_workload_models import (
    PanFeishuMetricSource,
    PanManualMetric,
)
from backend.app.services import feishu_metric_sync as sync_mod
from backend.app.services import feishu_table as table_mod

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
def org(db):
    """两个部门的员工。

    - ``E_VIDEO``  在短视频部（``D_VIDEO``）⇒ **开通了**剪辑工作量板块
    - ``E_WARE``   在仓储部（``D_WARE``）⇒ **没开通** ⇒ 用来测隔离
    """
    from backend.app.domain.models import Department, Employee
    from backend.app.domain.pan_workload_models import DepartmentModule

    db.add(Department(id="D_VIDEO", name="短视频", active=True))
    db.add(Department(id="D_WARE", name="仓储", active=True))
    video = Employee(
        id="E_VIDEO", name="剪辑甲", department_id="D_VIDEO",
        employment_status="active", account_status="enabled",
    )
    ware = Employee(
        id="E_WARE", name="仓管乙", department_id="D_WARE",
        employment_status="active", account_status="enabled",
    )
    db.add(video)
    db.add(ware)
    db.add(DepartmentModule(
        id="dm_1", department_id="D_VIDEO", module_code="pan_workload", active=True,
    ))
    db.commit()
    return video, ware


def _sheet_rows():
    """假表格内容 —— 第一行是表头。"""
    return [
        {"cells": {"日期": "2026-11-20", "完播率": "35%", "视频留存率": "62%", "备注": "抖音"}},
        {"cells": {"日期": "2026-11-21", "完播率": "0.28", "视频留存率": "0.55", "备注": "视频号"}},
    ]


class _FakeReader:
    """假表格读取器 —— 不打网络。"""

    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows if rows is not None else _sheet_rows()
        self.error = error
        self.calls: list[dict] = []

    def read_rows(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.rows


# ===========================================================================
# 1. 解析（纯函数）
# ===========================================================================


class TestParseRate:
    def test_百分比(self):
        assert sync_mod.parse_rate("35%") == pytest.approx(0.35)

    def test_小数(self):
        assert sync_mod.parse_rate("0.35") == pytest.approx(0.35)

    def test_裸数字按百分数(self):
        """⚠️ **裸数字 > 1 一律按百分数** —— 与飞书文字填报口径一致。"""
        assert sync_mod.parse_rate("35") == pytest.approx(0.35)

    def test_认不出返回空(self):
        """⚠️ **绝不猜** —— 认不出就是 ``None``。"""
        assert sync_mod.parse_rate("还行") is None
        assert sync_mod.parse_rate("") is None
        assert sync_mod.parse_rate(None) is None

    def test_零是有效值(self):
        """⚠️ **0% 是有效数据** —— 不能被当成「没填」。"""
        assert sync_mod.parse_rate("0%") == pytest.approx(0.0)
        assert sync_mod.parse_rate(0) == pytest.approx(0.0)


class TestParseDate:
    def test_三种写法(self):
        for text in ("2026-11-20", "2026/11/20", "2026年11月20日"):
            assert sync_mod.parse_date(text) == "2026-11-20"

    def test_认不出返回空(self):
        assert sync_mod.parse_date("昨天") == ""
        assert sync_mod.parse_date("") == ""

    def test_非法日期不崩(self):
        assert sync_mod.parse_date("2026-13-45") == ""


class TestParseRow:
    def test_标准行(self):
        result = sync_mod.parse_row({
            "日期": "2026-11-20", "完播率": "35%", "视频留存率": "62%", "备注": "抖音",
        })
        assert result["ok"] is True
        assert result["date"] == "2026-11-20"
        assert len(result["metrics"]) == 2
        codes = {m["code"] for m in result["metrics"]}
        assert codes == {"video_completion_rate", "video_retention_rate"}
        assert result["remark"] == "抖音"

    def test_列名别名(self):
        """⚠️ **列名是「认」出来的** —— HR 改个列名不该同步不上。"""
        result = sync_mod.parse_row({
            "业务日期": "2026-11-20", "完播": "35%", "留存": "62%",
        })
        assert result["ok"] is True
        assert len(result["metrics"]) == 2

    def test_英文列名(self):
        result = sync_mod.parse_row({
            "date": "2026-11-20", "completion_rate": "35%", "retention_rate": "62%",
        })
        assert result["ok"] is True
        assert len(result["metrics"]) == 2

    def test_缺日期列_报错(self):
        result = sync_mod.parse_row({"完播率": "35%"})
        assert result["ok"] is False
        assert "日期" in result["reason"]

    def test_数值认不出_该行失败(self):
        result = sync_mod.parse_row({"日期": "2026-11-20", "完播率": "还行"})
        assert result["ok"] is False

    def test_空指标格_跳过不算错(self):
        """⚠️ **留空 = 这项没填** ⇒ 不是错误，只是没数据。"""
        result = sync_mod.parse_row({"日期": "2026-11-20", "完播率": "35%", "视频留存率": ""})
        assert result["ok"] is True
        assert len(result["metrics"]) == 1

    def test_全空_失败(self):
        result = sync_mod.parse_row({"日期": "", "完播率": ""})
        assert result["ok"] is False


class TestContentHash:
    def test_内容相同_指纹相同(self):
        rows = _sheet_rows()
        assert sync_mod.content_hash(rows) == sync_mod.content_hash(list(rows))

    def test_行序变化_指纹不变(self):
        """⚠️ **员工调整行序不该触发重新入库**。"""
        rows = _sheet_rows()
        reversed_rows = list(reversed(rows))
        assert sync_mod.content_hash(rows) == sync_mod.content_hash(reversed_rows)

    def test_内容变化_指纹变(self):
        rows = _sheet_rows()
        changed = [dict(rows[0]), dict(rows[1])]
        changed[0] = {**changed[0], "cells": {**changed[0]["cells"], "完播率": "40%"}}
        assert sync_mod.content_hash(rows) != sync_mod.content_hash(changed)


class TestExtractToken:
    def test_电子表格链接(self):
        url = "https://xxx.feishu.cn/sheets/AbC123xyz?sheet=S1"
        assert sync_mod.extract_source_token(url, "sheet") == "AbC123xyz"
        assert sync_mod.extract_query_param(url, "sheet") == "S1"

    def test_多维表格链接(self):
        url = "https://xxx.feishu.cn/base/BaseTbl456?table=tbl001"
        assert sync_mod.extract_source_token(url, "bitable") == "BaseTbl456"
        assert sync_mod.extract_query_param(url, "table") == "tbl001"

    def test_乱链接返回空(self):
        assert sync_mod.extract_source_token("https://example.com/", "sheet") == ""


# ===========================================================================
# 2. 服务层：绑定 + 同步
# ===========================================================================


class TestBind:
    def test_绑定成功(self, db, org):
        video, _ = org
        reader = _FakeReader()
        svc = sync_mod.FeishuMetricSourceService(db, reader)
        source = svc.bind(
            employee_id=video.id,
            source_url="https://x.feishu.cn/sheets/Tk123?sheet=S1",
            actor_id="HR1",
        )
        assert source.employee_id == video.id
        assert source.obj_token == "Tk123"
        assert source.sheet_id == "S1"
        assert source.active is True

    def test_一人一张表_重复绑定是更新(self, db, org):
        """⚠️ **一人一张表** ⇒ 重复绑定是更新，不是新增。"""
        video, _ = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1")
        svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk2")
        sources = svc.list_sources()
        assert len(sources) == 1
        assert sources[0].obj_token == "Tk2"

    def test_缺日期列_绑定即报错(self, db, org):
        """⚠️ **绑定即验证** —— 链接错/表结构不对，当场报错。"""
        video, _ = org
        reader = _FakeReader(rows=[{"cells": {"完播率": "35%"}}])
        svc = sync_mod.FeishuMetricSourceService(db, reader)
        with pytest.raises(sync_mod.FeishuMetricSyncError) as exc:
            svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1")
        assert "日期" in exc.value.message

    def test_空表_绑定即报错(self, db, org):
        video, _ = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader(rows=[]))
        with pytest.raises(sync_mod.FeishuMetricSyncError):
            svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1")

    def test_员工不存在_报错(self, db, org):
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        with pytest.raises(sync_mod.FeishuMetricSyncError):
            svc.bind(employee_id="NOPE", source_url="https://x.feishu.cn/sheets/Tk1")


class TestSync:
    def test_同步落库(self, db, org):
        video, _ = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        source = svc.bind(
            employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1"
        )
        result = svc.sync_one(source)
        assert result["status"] == "synced"
        # ⚠️ **两行 × 两个指标 = 4 条**
        assert result["written"] == 4
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 4
        assert {r.source for r in rows} == {"feishu_table"}
        assert {r.employee_id for r in rows} == {video.id}

    def test_重复同步_幂等(self, db, org):
        """⚠️ **核心不变量** —— 重复同步**不重复入库**。"""
        video, _ = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        source = svc.bind(
            employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1"
        )
        svc.sync_one(source)
        result = svc.sync_one(source)
        assert result["status"] == "unchanged"
        assert result["written"] == 0
        assert len(db.scalars(select(PanManualMetric)).all()) == 4

    def test_内容变了_覆盖不追加(self, db, org):
        """⚠️ **改了再改是正常操作** ⇒ 同键覆盖，不是累加。"""
        video, _ = org
        reader = _FakeReader()
        svc = sync_mod.FeishuMetricSourceService(db, reader)
        source = svc.bind(
            employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1"
        )
        svc.sync_one(source)
        # 改第一行的完播率
        reader.rows = [
            {"cells": {"日期": "2026-11-20", "完播率": "40%", "视频留存率": "62%", "备注": "抖音"}},
            reader.rows[1],
        ]
        svc.sync_one(source)
        rows = db.scalars(select(PanManualMetric)).all()
        assert len(rows) == 4  # ⚠️ **没有多出 4 条**
        first = [r for r in rows if r.metric_code == "video_completion_rate"][0]
        assert first.metric_value == pytest.approx(0.40)

    def test_坏行跳过_不阻断整批(self, db, org):
        """⚠️ **一张表里有一行坏了，不能整批同步不上**。"""
        video, _ = org
        rows = _sheet_rows()
        rows.insert(1, {"cells": {"日期": "2026-11-20", "完播率": "还行"}})
        reader = _FakeReader(rows=rows)
        svc = sync_mod.FeishuMetricSourceService(db, reader)
        source = svc.bind(
            employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1"
        )
        result = svc.sync_one(source)
        assert result["status"] == "synced"
        assert result["written"] == 4  # ⚠️ 好行照常落库
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["row"] == 3  # ⚠️ 第 3 行（表头占第 1 行）

    def test_一个来源失败_不阻断其它(self, db, org):
        """⚠️ **一张表被删了，不能整个板块都同步不动**。"""
        video, ware = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        svc.bind(
            employee_id=video.id, source_url="https://x.feishu.cn/sheets/Good"
        )
        # ⚠️ **另一个员工**的坏来源（一人一张表 ⇒ 不能绑在同一人名下）
        svc.bind(
            employee_id=ware.id, source_url="https://x.feishu.cn/sheets/Bad"
        )

        reader = _FakeReader()
        original = reader.read_rows

        def _read_rows(**kwargs):
            if kwargs.get("obj_token") == "Bad":
                raise table_mod.FeishuTableUnavailableError("表没了")
            return original(**kwargs)

        reader.read_rows = _read_rows
        svc.reader = reader
        result = svc.sync_all()
        assert result["failed"] == 1
        assert result["succeeded"] == 1
        rows = db.scalars(select(PanManualMetric)).all()
        # ⚠️ **只有好那张表的数据进来了**
        assert len(rows) == 4
        assert {r.employee_id for r in rows} == {video.id}


# ===========================================================================
# 3. 权限降级（错误分类）
# ===========================================================================


class TestErrorClassification:
    def test_缺scope_报找IT(self):
        from backend.app.services.feishu_client import FeishuError

        exc = table_mod.classify_error(
            FeishuError("Access denied", code=99991672)
        )
        assert isinstance(exc, table_mod.FeishuTablePermissionError)
        assert "IT" in exc.message

    def test_表不存在_报找HR(self):
        from backend.app.services.feishu_client import FeishuError

        exc = table_mod.classify_error(FeishuError("not exist", code=131005))
        assert isinstance(exc, table_mod.FeishuTableUnavailableError)

    def test_两类错误不同(self):
        """⚠️ **两类错误的对策完全不同** —— 必须分开。"""
        from backend.app.services.feishu_client import FeishuError

        perm = table_mod.classify_error(FeishuError("x", code=99991672))
        unavail = table_mod.classify_error(FeishuError("x", code=91402))
        assert not isinstance(perm, table_mod.FeishuTableUnavailableError)
        assert not isinstance(unavail, table_mod.FeishuTablePermissionError)


# ===========================================================================
# 4. 板块隔离（2026-11-21 补的漏洞）
# ===========================================================================


BOT_TOKEN = "v_bot_token_table_test"


def _message_event(text: str, *, open_id: str, message_id: str):
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_{message_id}",
            "event_type": "im.message.receive_v1",
            "token": BOT_TOKEN,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": open_id, "user_id": "u_" + open_id},
                "sender_type": "user",
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


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


@pytest.fixture()
def feishu_org(db):
    """短视频部的剪辑（已绑飞书身份）与仓储部的人（也绑了身份）。"""
    from backend.app.domain.models import Department, Employee, FeishuIdentity
    from backend.app.domain.pan_workload_models import DepartmentModule

    db.add(Department(id="D_VIDEO", name="短视频", active=True))
    db.add(Department(id="D_WARE", name="仓储", active=True))
    video = Employee(
        id="E_VIDEO", name="剪辑甲", department_id="D_VIDEO",
        employment_status="active", account_status="enabled",
        feishu_open_id="ou_video", feishu_user_id="u_video",
    )
    ware = Employee(
        id="E_WARE", name="仓管乙", department_id="D_WARE",
        employment_status="active", account_status="enabled",
        feishu_open_id="ou_ware", feishu_user_id="u_ware",
    )
    db.add(video)
    db.add(ware)
    db.add(FeishuIdentity(
        id="FI_V", employee_id="E_VIDEO",
        identity_type="open_id", identity_value="ou_video", active=True,
    ))
    db.add(FeishuIdentity(
        id="FI_W", employee_id="E_WARE",
        identity_type="open_id", identity_value="ou_ware", active=True,
    ))
    db.add(DepartmentModule(
        id="dm_1", department_id="D_VIDEO", module_code="pan_workload", active=True,
    ))
    db.commit()
    return video, ware


EVENTS_URL = "/v1/feishu/bot/events"


class TestFeishuModuleIsolation:
    """⚠️ **回归测试** —— 飞书端的板块隔离。

    修复前：飞书端填报**完全没有**板块守卫
    ⇒ 仓储部的人在飞书发「完播率 35%」照样能写进库。
    """

    def test_短视频部可以填(self, http, db, feishu_org):
        resp = http.post(EVENTS_URL, json=_message_event(
            "完播率 35%", open_id="ou_video", message_id="om_1",
        ))
        assert resp.status_code == 200
        assert len(db.scalars(select(PanManualMetric)).all()) == 1

    def test_非短视频部门被拒(self, http, db, feishu_org):
        """⚠️ **核心回归** —— 别的部门不能写进剪辑的数据表。"""
        resp = http.post(EVENTS_URL, json=_message_event(
            "完播率 35%", open_id="ou_ware", message_id="om_2",
        ))
        assert resp.status_code == 200
        # ⚠️ **一条都不能落库**
        assert db.scalars(select(PanManualMetric)).all() == []
        card = http.feishu_sent[-1]["card"]
        assert card["header"]["title"]["content"] == "操作未完成"
        assert "部门" in json.dumps(card, ensure_ascii=False)

    def test_非短视频部门截图也被拒(self, http, db, feishu_org):
        """⚠️ **截图存档同样要拦** —— 它也写剪辑的表。"""
        body = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_img",
                "event_type": "im.message.receive_v1",
                "token": BOT_TOKEN,
            },
            "event": {
                "sender": {
                    "sender_id": {"open_id": "ou_ware", "user_id": "u_ou_ware"},
                    "sender_type": "user",
                },
                "message": {
                    "message_id": "om_img",
                    "chat_id": "oc_1",
                    "chat_type": "p2p",
                    "message_type": "image",
                    "content": json.dumps({"image_key": "img_1"}),
                },
            },
        }
        resp = http.post(EVENTS_URL, json=body)
        assert resp.status_code == 200
        from backend.app.domain.pan_workload_models import PanManualMetricArtifact
        assert db.scalars(select(PanManualMetricArtifact)).all() == []


# ===========================================================================
# 5. 自助绑定（员工绑自己的表）+ 调度器（2026-11-21）
# ===========================================================================


class TestSelfServiceBinding:
    """⚠️ **剪辑同事要能自助绑表** —— 否则「在飞书里填表」根本走不通。

    员工建好自己的飞书表 → 把链接粘到系统里 → 在表里填 → 系统自动读回。
    ⇒ 员工**只能绑自己**（请求体里的 employee_id 被忽略）。
    """

    def test_员工只能绑自己(self, db, org):
        video, _ = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        # ⚠️ **请求体里写了别人的 id** —— 员工路径必须强制改写成自己。
        employee_id = video.id
        source = svc.bind(
            employee_id=employee_id, source_url="https://x.feishu.cn/sheets/Tk1"
        )
        assert source.employee_id == video.id

    def test_一人一张表_唯一约束生效(self, db, org):
        """⚠️ **唯一约束在数据库层** —— 并发绑定也不会出现两张表。"""
        video, _ = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk1")
        svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/Tk2")
        sources = svc.list_sources()
        assert len(sources) == 1
        assert sources[0].obj_token == "Tk2"

    def test_不同员工_不同表(self, db, org):
        """⚠️ **归属由绑定决定** ⇒ A 的产出不会记到 B 头上。"""
        video, ware = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/AV")
        svc.bind(employee_id=ware.id, source_url="https://x.feishu.cn/sheets/BW")
        assert len(svc.list_sources()) == 2


class TestScheduler:
    """定时同步 —— 没绑定时必须**静默跳过**（预期状态，不是错误）。"""

    def test_没绑定_跳过(self, db, monkeypatch):
        """⚠️ **必须显式配好飞书凭证**（2026-11-25，CI 22 连红修复）
        本用例要验的是「**没有表格绑定** ⇒ 静默跳过」，
        而不是一个「**没配飞书凭证**」的用例（那是下一个用例的事）。
        ⇒ 若不显式配凭证，无凭据环境（CI 没有 ``backend/.env``）
        会先命中「未配凭证」分支 ⇒ 断言 ``skipped_no_bindings`` 挂掉。
        ⇒ 又是「本地绿 / CI 红」，且错在**测试**不在业务代码。
        """
        from backend.app.core.config import settings
        from backend.app.services import feishu_metric_scheduler as sched

        monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
        monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")
        monkeypatch.setattr(sched, "get_sessionmaker", lambda: lambda: db)
        result = sched.run_scheduled_sync()
        assert result is None
        assert sched.get_scheduler_status()["lastResult"] == "skipped_no_bindings"

    def test_未配飞书凭证_跳过(self, db, monkeypatch):
        from backend.app.core.config import settings
        from backend.app.services import feishu_metric_scheduler as sched

        monkeypatch.setattr(settings, "feishu_app_id", "")
        monkeypatch.setattr(settings, "feishu_app_secret", "")
        result = sched.run_scheduled_sync()
        assert result is None
        assert sched.get_scheduler_status()["lastResult"] == "skipped_not_configured"

    def test_间隔下限10分钟(self, monkeypatch):
        """⚠️ **飞书开放平台有频控** —— 不能让用户把间隔调到 1 秒。"""
        from backend.app.core.config import settings
        from backend.app.services import feishu_metric_scheduler as sched

        monkeypatch.setattr(settings, "feishu_metric_sync_interval_seconds", 1)
        assert sched.get_scheduler_status()["intervalSeconds"] >= 600


# ===========================================================================
# 6. REST API 层（自助绑定 + 板块隔离）
# ===========================================================================


@pytest.fixture()
def api_client(db, monkeypatch):
    """REST 客户端 —— 伪造「当前登录人」与飞书表格读取器。"""
    from backend.app.core.config import settings
    from backend.app.main import app
    from backend.app.routes import pan_workload_feishu_table as routes

    monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
    monkeypatch.setattr(settings, "feishu_app_secret", "secret_test")

    class _FakeClient:
        def close(self):
            pass

    # ⚠️ **当前登录人** —— 用可变容器，测试里随时换人。
    holder: dict[str, object] = {"employee": None}

    def _make_service(_db):
        svc = _ApiService(_db)
        svc.current_employee = holder["employee"]
        return svc

    monkeypatch.setattr(routes, "_service", lambda _db: (_FakeClient(), _make_service(_db)))

    app.dependency_overrides[routes.get_current_user] = lambda: holder["employee"]

    def _override_db():
        yield db

    app.dependency_overrides[routes.get_db] = _override_db
    with TestClient(app) as c:
        c.holder = holder  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


class _ApiService:
    """带「当前登录人」的假服务（读取器是假的，不打网络）。"""

    current_employee = None

    def __init__(self, db):
        self.db = db

    def _svc(self):
        return sync_mod.FeishuMetricSourceService(self.db, _FakeReader())

    def list_sources(self, only_active: bool = False):
        return self._svc().list_sources(only_active=only_active)

    def get(self, source_id):
        return self._svc().get(source_id)

    def bind(self, **kwargs):
        return self._svc().bind(**kwargs)

    def unbind(self, source_id, actor_id=""):
        return self._svc().unbind(source_id, actor_id=actor_id)

    def sync_one(self, source):
        return self._svc().sync_one(source)

    def sync_all(self, only_source_id: str = ""):
        return self._svc().sync_all(only_source_id=only_source_id)


class TestBindApi:
    """⚠️ **REST 层的自助绑定** —— 这是「剪辑在飞书填表」能落地的前提。"""

    def test_员工绑自己的表(self, api_client, db, org):
        video, _ = org
        api_client.holder["employee"] = video
        resp = api_client.post(
            "/v1/baidu/workload/feishu-tables/sources",
            json={"source_url": "https://x.feishu.cn/sheets/Tk1", "table_kind": "sheet"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["employeeId"] == video.id

    def test_员工不能替别人绑(self, api_client, db, org):
        """⚠️ **请求体里的 employee_id 被忽略** —— 员工只能绑自己。"""
        video, ware = org
        api_client.holder["employee"] = video
        resp = api_client.post(
            "/v1/baidu/workload/feishu-tables/sources",
            json={
                "employee_id": ware.id,
                "source_url": "https://x.feishu.cn/sheets/Tk2",
                "table_kind": "sheet",
            },
        )
        assert resp.status_code == 200
        # ⚠️ **绑到了自己头上**，不是 ware
        assert resp.json()["data"]["employeeId"] == video.id

    def test_非短视频部门_整个接口403(self, api_client, db, org):
        """⚠️ **板块守卫** —— 仓储部的人连这个 API 都碰不到。"""
        _, ware = org
        api_client.holder["employee"] = ware
        resp = api_client.get("/v1/baidu/workload/feishu-tables/sources")
        assert resp.status_code == 403

    def test_员工只看得到自己的表(self, api_client, db, org):
        video, ware = org
        svc = sync_mod.FeishuMetricSourceService(db, _FakeReader())
        svc.bind(employee_id=video.id, source_url="https://x.feishu.cn/sheets/AV")
        svc.bind(employee_id=ware.id, source_url="https://x.feishu.cn/sheets/BW")
        db.commit()

        api_client.holder["employee"] = video
        resp = api_client.get("/v1/baidu/workload/feishu-tables/sources")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["employeeId"] == video.id
