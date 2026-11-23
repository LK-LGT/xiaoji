"""网盘剪辑工作量统计 —— 服务层测试（BP-2 / P1）。

覆盖
----
1. **绑定表驱动** —— 采集范围完全由 ``pan_account_binding`` 决定
2. **幂等 upsert** —— 按 ``fs_id``，重复采集不重复计数（★ 最容易写错的地方）
3. **重算而非累加** —— 重复跑聚合不会翻倍；规则改了重跑即生效
4. **人工调整不被覆盖** —— ``adjusted_count`` 与 ``total_count`` 并存
5. **不丢弃任何文件** —— 未归属 / 被排除的文件也落库并带原因
6. **重复上传识别** —— 同日同 digest 只算一次
7. **异常检测** —— 突增/突减只标记，不改分；基线不足不报警
8. **目录黑名单** —— 黑名单目录不被访问

⚠️ **本文件不联网** —— 网盘交互全部用假 transport 模拟。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Employee
from backend.app.domain.pan_workload_models import (
    PanAccountBinding,
    PanWorkloadConfig,
    PanWorkloadDaily,
    PanWorkloadFile,
)
from backend.app.services.baidu_pan_client import BaiduPanClient, PanFile
from backend.app.services.pan_workload_service import PanWorkloadService

# ─────────────────────────────────────────────────────────────────────────
# 假 transport
# ─────────────────────────────────────────────────────────────────────────


def make_transport(responses: list[dict | httpx.Response]) -> httpx.MockTransport:
    """按顺序返回预设响应。"""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            return httpx.Response(200, json={"errno": -6, "show_msg": "queue empty"})
        item = queue.pop(0)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    return httpx.MockTransport(handler)


def dir_response(files: list[dict]) -> dict:
    """构造 ``file?method=list`` 的响应体。"""
    return {"errno": 0, "list": files, "has_more": 0}


def make_file(
    name: str,
    path: str,
    *,
    fs_id: int = 1,
    size: int = 1000,
    md5: str = "",
    is_dir: bool = False,
) -> dict:
    return {
        "server_filename": name,
        "path": path,
        "fs_id": fs_id,
        "isdir": 1 if is_dir else 0,
        "size": size,
        "server_ctime": 1790000000,
        "server_mtime": 1790000000,
        "md5": md5,
        "oper_id": "1102224043442",
    }


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine)
    session = sm()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def emp(db):
    e = Employee(id="emp_yao", name="小瑶")
    db.add(e)
    e2 = Employee(id="emp_yu", name="小雨")
    db.add(e2)
    db.commit()
    return e


def _service(db, responses: list[dict]) -> PanWorkloadService:
    """造一个带假 transport 的服务。"""
    client = BaiduPanClient(app_key="k", app_secret="s", redirect_uri="oob")
    client._http = httpx.Client(transport=make_transport(responses))
    svc = PanWorkloadService(db)
    svc._pan = _FakePanService(client)
    return svc


class _FakePanService:
    """只提供 ``client`` 与 ``is_authorized`` 的替身。"""

    def __init__(self, client: BaiduPanClient) -> None:
        self.client = client
        self._authorized = True

    def is_authorized(self) -> bool:
        return self._authorized

    def ensure_access_token(self) -> str:
        return "fake-token"

    def close(self) -> None:
        self.client.close()


def _bind(db, emp_id: str, folder: str, name: str) -> PanAccountBinding:
    b = PanAccountBinding(
        employee_id=emp_id, folder_path=folder, folder_name=name
    )
    db.add(b)
    db.commit()
    return b


def _file_rows(path: str) -> list[PanFile]:
    """把 dict 转成 PanFile。"""
    return [
        PanFile(
            path=r["path"],
            name=r["server_filename"],
            is_dir=bool(r["isdir"]),
            size=r["size"],
            ctime=r["server_ctime"],
            md5=r.get("md5", ""),
            fs_id=r["fs_id"],
        )
        for r in path
    ]


# ─────────────────────────────────────────────────────────────────────────
# ① 绑定表驱动
# ─────────────────────────────────────────────────────────────────────────


class TestBindingDriven:
    def test_无绑定则跳过(self, db):
        svc = _service(db, [])
        result = svc.collect()
        assert result["status"] == "skipped"
        assert result["reason"] == "no_bindings"

    def test_只扫绑定目录(self, db, emp):
        """⚠️ 采集范围完全由绑定表决定 —— 未绑定目录不被访问。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        svc = _service(db, [
            dir_response([
                make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4", fs_id=1),
            ]),
        ])
        result = svc.collect()
        assert result["status"] == "succeeded"
        assert result["scanned"] == 1
        rows = db.scalars(select(PanWorkloadFile)).all()
        assert len(rows) == 1
        assert rows[0].employee_id == "emp_yao"

    def test_黑名单目录被跳过(self, db, emp):
        """⚠️ ``/鹿13``（素材库）在黑名单里 —— 绑了也不该扫。"""
        _bind(db, "emp_yao", "/鹿13", "鹿13")
        svc = _service(db, [])
        result = svc.collect()
        assert result["scanned"] == 0
        assert db.scalars(select(PanWorkloadFile)).all() == []

    def test_口播成品已移出黑名单(self, db, emp):
        """⚠️⚠️ **回归点（2026-11-21）**

        业务确认：「口播成品不用管，对应的用户绑定就好了」
        ⇒ ``/口播成品`` **不再是黑名单**，而是**绑定到具体用户**
        （巫宜珊）走常规的绑定-判定-聚合流程。

        ⇒ 这个测试锁死「黑名单里不再有 ``/口播成品``」，
        防止有人「顺手」把它加回去 ⇒ 巫宜珊的工作量会**整段归零**。
        """
        _bind(db, "emp_yao", "/口播成品", "巫宜珊")
        svc = _service(db, [
            dir_response([
                make_file(
                    "阔腿裤10.24.mp4",
                    "/口播成品/10.24/阔腿裤10.24.mp4",
                    fs_id=901,
                ),
            ]),
        ])
        result = svc.collect()
        assert result["scanned"] == 1, "/口播成品 必须能被扫描"
        rows = db.scalars(select(PanWorkloadFile)).all()
        assert len(rows) == 1
        assert rows[0].employee_id == "emp_yao"


# ─────────────────────────────────────────────────────────────────────────
# ② 幂等 upsert
# ─────────────────────────────────────────────────────────────────────────


class TestIdempotentUpsert:
    def test_重复采集不重复计数(self, db, emp):
        """★ 最容易写错的地方 —— 同一文件扫 10 次只能算 1 条。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4", fs_id=1),
        ])
        svc = _service(db, [resp, resp, resp])
        for _ in range(3):
            svc.collect()

        files = db.scalars(select(PanWorkloadFile)).all()
        assert len(files) == 1, "同一 fs_id 只能有一行"
        dailies = db.scalars(select(PanWorkloadDaily)).all()
        assert len(dailies) == 1
        assert dailies[0].total_count == 1, "重复采集不能把条数加三遍"

    def test_规则改了重跑即生效(self, db, emp):
        """★ 重算而非累加的核心价值 —— 改权重不用写数据迁移。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 智能体.mp4", "/成品-小瑶/11月/11.13/11.13 智能体.mp4", fs_id=1),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadDaily)).one()
        assert row.total_count == 2

        # 改配置：智能体权重 2 → 3
        cfg = svc.get_config()
        cfg.marker_rules = {
            "default": {
                "智能体": {"weight": 3, "patterns": ["智能体"], "enabled": True}
            }
        }
        db.commit()

        svc2 = _service(db, [resp])
        svc2.collect()
        db.refresh(row)
        assert row.total_count == 3, "改配置重跑应立即生效"


# ─────────────────────────────────────────────────────────────────────────
# ③ 聚合（重算而非累加）
# ─────────────────────────────────────────────────────────────────────────


class TestAggregate:
    def test_采集后日汇总立即可用(self, db, emp):
        """⚠️⚠️ **回归测试**：曾经因为聚合前没 flush 导致 ``daily_rows=0``。

        ``aggregate_daily`` 用 ``select()`` 查文件行，
        而刚落库的行还在会话 pending 区 ⇒ 不 flush 就查不到。
        """
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4", fs_id=1),
            make_file("11.14 OOTD.mp4", "/成品-小瑶/11月/11.14/11.14 OOTD.mp4", fs_id=2),
        ])
        svc = _service(db, [resp])
        result = svc.collect()
        assert result["daily_rows"] == 2, "采集后必须立刻产出日汇总行"
        dailies = db.scalars(select(PanWorkloadDaily)).all()
        assert len(dailies) == 2
        assert sum(d.total_count for d in dailies) == 3

    def test_按人按天聚合(self, db, emp):
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4", fs_id=1),
            make_file("11.13 智能体.mp4", "/成品-小瑶/11月/11.13/11.13 智能体.mp4", fs_id=2),
            make_file("11.14 OOTD.mp4", "/成品-小瑶/11月/11.14/11.14 OOTD.mp4", fs_id=3),
        ])
        svc = _service(db, [resp])
        svc.collect()
        rows = {r.business_date: r for r in db.scalars(select(PanWorkloadDaily)).all()}
        assert rows[date(2026, 11, 13)].total_count == 3   # 1 + 2
        assert rows[date(2026, 11, 14)].total_count == 2   # OOTD = 2
        assert rows[date(2026, 11, 13)].file_count == 2

    def test_标记明细(self, db, emp):
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 智能体.mp4", "/成品-小瑶/11月/11.13/11.13 智能体.mp4", fs_id=1),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadDaily)).one()
        assert row.marker_breakdown == {"智能体": 1}

    def test_人工调整不被覆盖(self, db, emp):
        """⚠️ 机器判定与人工裁定**并存**，不互相吞噬。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4", fs_id=1),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadDaily)).one()
        row.adjusted_count = 5
        row.adjust_reason = "主管核定"
        row.status = "confirmed"
        db.commit()

        svc2 = _service(db, [resp])
        svc2.collect()
        db.refresh(row)
        assert row.adjusted_count == 5, "人工调整不能被自动重算覆盖"
        assert row.status == "confirmed"
        assert row.total_count == 1


# ─────────────────────────────────────────────────────────────────────────
# 不丢弃任何文件
# ─────────────────────────────────────────────────────────────────────────


class TestNoSilentDrop:
    def test_封面png也落库(self, db, emp):
        """⚠️ 被排除的文件也要留痕 —— 否则无法解释「为什么少了一条」。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.png", "/成品-小瑶/11月/11.13/11.13 波点上衣.png", fs_id=1),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadFile)).one()
        assert row.counted is False
        assert row.exclude_reason == "ext_not_allowed:png"
        d = db.scalars(select(PanWorkloadDaily)).one()
        assert d.total_count == 0
        assert d.excluded_count == 1

    def test_被排除文件不计入条数(self, db, emp):
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("返工-成片.mp4", "/成品-小瑶/11月/11.13/返工-成片.mp4", fs_id=1),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadFile)).one()
        assert row.counted is False
        assert row.exclude_reason.startswith("keyword:")


# ─────────────────────────────────────────────────────────────────────────
# 重复上传识别
# ─────────────────────────────────────────────────────────────────────────


class TestDuplicate:
    def test_同日同内容只算一次(self, db, emp):
        """⚠️ 同一天同一内容传两次 —— 只算一条。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4",
                      fs_id=1, md5="abc", size=100),
            make_file("11.13 波点上衣(1).mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣(1).mp4",
                      fs_id=2, md5="abc", size=100),
        ])
        svc = _service(db, [resp])
        svc.collect()
        d = db.scalars(select(PanWorkloadDaily)).one()
        assert d.total_count == 1, "同内容重复上传只算一次"
        rows = db.scalars(select(PanWorkloadFile)).all()
        dup = [r for r in rows if r.is_duplicate]
        assert len(dup) == 1
        assert dup[0].duplicate_kind == "md5"

    def test_不同内容各算一条(self, db, emp):
        """⚠️ 实测：``智能体`` 与 ``智能体-2`` 是不同内容 ⇒ 各算 1 条。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 智能体.mp4", "/成品-小瑶/11月/11.13/11.13 智能体.mp4",
                      fs_id=1, md5="aaa", size=239),
            make_file("11.13 智能体-2.mp4", "/成品-小瑶/11月/11.13/11.13 智能体-2.mp4",
                      fs_id=2, md5="bbb", size=123),
        ])
        svc = _service(db, [resp])
        svc.collect()
        d = db.scalars(select(PanWorkloadDaily)).one()
        assert d.total_count == 4, "两个文件各 2 条"
        assert not db.scalars(
            select(PanWorkloadFile).where(PanWorkloadFile.is_duplicate.is_(True))
        ).all()


# ─────────────────────────────────────────────────────────────────────────
# 异常检测
# ─────────────────────────────────────────────────────────────────────────


class TestAnomaly:
    def _seed(self, db, emp, counts: list[int], start=date(2026, 10, 1)):
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        svc = _service(db, [])
        cfg = svc.get_config()
        cfg.baseline_days = 5
        db.commit()
        for i, c in enumerate(counts):
            db.add(
                PanWorkloadDaily(
                    employee_id="emp_yao",
                    business_date=start + timedelta(days=i),
                    total_count=c,
                )
            )
        db.commit()
        return svc

    def test_突增被识别(self, db, emp):
        svc = self._seed(db, emp, [6, 6, 6, 6, 6, 6, 20])
        found = svc.detect_anomalies()
        assert any(f["kind"] == "surge" for f in found)

    def test_突减被识别(self, db, emp):
        svc = self._seed(db, emp, [6, 6, 6, 6, 6, 6, 1])
        found = svc.detect_anomalies()
        assert any(f["kind"] == "drop" for f in found)

    def test_基线不足不报警(self, db, emp):
        """⚠️ 否则月初必然误报。"""
        svc = self._seed(db, emp, [6, 20, 30])
        found = svc.detect_anomalies()
        assert found == []

    def test_异常不改分(self, db, emp):
        """★ 判定权在人 —— 检测结果里没有任何改分动作。"""
        svc = self._seed(db, emp, [6, 6, 6, 6, 6, 6, 20])
        before = {r.id: r.total_count for r in db.scalars(select(PanWorkloadDaily)).all()}
        svc.detect_anomalies()
        after = {r.id: r.total_count for r in db.scalars(select(PanWorkloadDaily)).all()}
        assert before == after


# ─────────────────────────────────────────────────────────────────────────
# ⚠️⚠️ **业务日期兜底 —— 静默少计回归**
# ─────────────────────────────────────────────────────────────────────────


class TestDateFallback:
    """⚠️⚠️ **静默少计的回归测试**

    回归点（2026-11-21，绑定 ``/口播成品`` 后暴露）：
    ``/口播成品/灯笼裤.mp4`` 与 ``/口播成品/6月18日.mp4`` 直接挂在**根目录**，
    **目录名里没有日期** ⇒ 原实现返回空 ``business_date``
    ⇒ 文件**计入判定成立**（``counted=True``）
    但 ``aggregate_daily`` 会**跳过没有日期的行**
    ⇒ 「1346 条」在面板上只显示「1345 条」
    ⇒ **差额永远查不出来**（机器判定值与聚合值对不上，且无任何提示）。

    ⇒ 契约：**机器判定条数 == 日汇总条数**，永远相等。
    """

    def test_根目录无日期文件不会丢(self, db, emp):
        _bind(db, "emp_yao", "/口播成品", "巫宜珊")
        resp = dir_response([
            make_file("灯笼裤.mp4", "/口播成品/灯笼裤.mp4", fs_id=1),
            make_file("6月18日.mp4", "/口播成品/6月18日.mp4", fs_id=2),
        ])
        svc = _service(db, [resp])
        result = svc.collect()
        assert result["counted"] == 2

        files = db.scalars(select(PanWorkloadFile)).all()
        assert len(files) == 2
        # ★ 每个计入的文件都必须有业务日期 —— 绝不为空
        for row in files:
            assert row.business_date, "计入的文件必须有业务日期，否则会被聚合静默丢弃"
            assert row.business_date != "", "空字符串同样会被聚合丢弃"

        # ★ 机器判定条数 == 日汇总条数（差 0）
        file_weight = sum(r.count_weight for r in files if r.counted)
        dailies = db.scalars(select(PanWorkloadDaily)).all()
        daily_weight = sum(d.total_count for d in dailies)
        assert file_weight == daily_weight, (
            f"机器判定 {file_weight} 条 vs 日汇总 {daily_weight} 条 —— 静默少计！"
        )

    def test_文件名日期可作兜底(self, db, emp):
        """⚠️ ``6月18日.mp4`` —— 文件名里有日期 ⇒ 能解析出来。"""
        _bind(db, "emp_yao", "/口播成品", "巫宜珊")
        resp = dir_response([
            make_file("6月18日.mp4", "/口播成品/6月18日.mp4", fs_id=2),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadFile)).one()
        assert row.business_date, "文件名日期应当兜底成功"
        assert row.business_date.endswith("-06-18")

    def test_日期未知有显式标记(self, db, emp):
        """⚠️ 兜底到底也要**留痕** —— 主管能看见「这天日期定不出来」。"""
        _bind(db, "emp_yao", "/口播成品", "巫宜珊")
        resp = dir_response([
            make_file("灯笼裤.mp4", "/口播成品/灯笼裤.mp4", fs_id=1),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadFile)).one()
        flags = json.dumps(row.anomaly_flags or [], ensure_ascii=False)
        assert "date_from_name" in flags or "date_from_ctime" in flags or "date_unknown" in flags, (
            f"日期兜底必须留痕，实际 flags={flags}"
        )

    def test_被排除文件也有日期(self, db, emp):
        """⚠️ 被排除的文件若没日期 ⇒ 那天的「全部被排除」在面板上不可见。"""
        _bind(db, "emp_yao", "/口播成品", "巫宜珊")
        resp = dir_response([
            make_file("灯笼裤.png", "/口播成品/灯笼裤.png", fs_id=1),
        ])
        svc = _service(db, [resp])
        svc.collect()
        row = db.scalars(select(PanWorkloadFile)).one()
        assert not row.counted
        assert row.business_date, "被排除的文件也必须落到某一天，否则那天整行消失"


# ─────────────────────────────────────────────────────────────────────────
# 查询
# ─────────────────────────────────────────────────────────────────────────


class TestQueries:
    def test_按人汇总(self, db, emp):
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4", fs_id=1),
            make_file("11.14 OOTD.mp4", "/成品-小瑶/11月/11.14/11.14 OOTD.mp4", fs_id=2),
        ])
        svc = _service(db, [resp])
        svc.collect()
        totals = svc.totals_by_employee()
        assert totals[0]["employee_id"] == "emp_yao"
        assert totals[0]["total_count"] == 3
        assert totals[0]["days"] == 2

    def test_查某天文件明细(self, db, emp):
        """⚠️ 审计根 —— 主管质疑时能回溯到具体文件。"""
        _bind(db, "emp_yao", "/成品-小瑶", "小瑶")
        resp = dir_response([
            make_file("11.13 波点上衣.mp4", "/成品-小瑶/11月/11.13/11.13 波点上衣.mp4", fs_id=1),
            make_file("11.13 智能体.mp4", "/成品-小瑶/11月/11.13/11.13 智能体.mp4", fs_id=2),
        ])
        svc = _service(db, [resp])
        svc.collect()
        files = svc.files_of_day("emp_yao", "2026-11-13")
        assert len(files) == 2
        assert {f.file_name for f in files} == {
            "11.13 波点上衣.mp4", "11.13 智能体.mp4"
        }


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
