"""人工填报 + 月度聚合 + 飞书端解析 测试（BP-2 / P3）。

覆盖
----
1. 填报 upsert（重复提交是覆盖不是累加）
2. 两种粒度：按天 / 逐条成片（``target_key`` 进唯一约束）
3. 值域校验 / 指标白名单
4. 证据附件：类型白名单 / 大小上限 / 摘要
5. ⚠️ **月度聚合写两条独立 MetricValue**（不合成综合分）
6. ⚠️ **网盘条数优先取人工裁定值**
7. ⚠️ **聚合幂等**（重跑是覆盖不是追加）
8. 飞书端：数值/指标/日期解析 + 提交落库 + 证据留痕
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Employee, MetricValue
from backend.app.domain.pan_workload_models import (
    PanManualMetric,
    PanManualMetricArtifact,
    PanWorkloadDaily,
    PanWorkloadFile,
)
from backend.app.services import feishu_manual_metric as fmm
from backend.app.services.feishu_manual_metric import (
    FeishuManualMetricError,
    detect_metric_code,
    extract_date,
    extract_value,
    parse_rate,
    submit_from_text,
)
from backend.app.services.pan_manual_metric_service import (
    METRIC_COMPLETION_RATE,
    METRIC_RETENTION_RATE,
    METRIC_VIDEO_COUNT,
    PanManualMetricError,
    PanManualMetricService,
)


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
    e = Employee(id="emp_1", name="小瑶")
    db.add(e)
    db.commit()
    return e


@pytest.fixture()
def svc(db):
    return PanManualMetricService(db)


# ─────────────────────────────────────────────────────────────────────────
# 填报
# ─────────────────────────────────────────────────────────────────────────


class TestSubmit:
    def test_提交一条(self, db, emp, svc):
        row = svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.35,
        )
        assert row.metric_value == 0.35
        assert row.scope == "day"
        assert row.source == "frontend"

    def test_重复提交是覆盖不是累加(self, db, emp, svc):
        """⚠️ 员工改了再改是**正常操作** —— 不能变成两个值。"""
        for v in (0.3, 0.4, 0.5):
            svc.submit(
                employee_id=emp.id, metric_date="2026-11-19",
                metric_code=METRIC_COMPLETION_RATE, metric_value=v,
            )
        rows = svc.list_metrics(employee_id=emp.id)
        assert len(rows) == 1
        assert rows[0].metric_value == 0.5

    def test_逐条填报_不同target_key可共存(self, db, emp, svc):
        """⚠️ 这是加 ``target_key`` 进唯一约束的原因。"""
        svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.3,
            scope="file", target_key="file_a",
        )
        svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.5,
            scope="file", target_key="file_b",
        )
        rows = svc.list_metrics(employee_id=emp.id)
        assert len(rows) == 2

    def test_逐条填报必须给target_key(self, db, emp, svc):
        with pytest.raises(PanManualMetricError):
            svc.submit(
                employee_id=emp.id, metric_date="2026-11-19",
                metric_code=METRIC_COMPLETION_RATE, metric_value=0.3, scope="file",
            )

    def test_指标白名单(self, db, emp, svc):
        with pytest.raises(PanManualMetricError):
            svc.submit(
                employee_id=emp.id, metric_date="2026-11-19",
                metric_code="salary", metric_value=1.0,
            )

    def test_值域校验(self, db, emp, svc):
        with pytest.raises(PanManualMetricError):
            svc.submit(
                employee_id=emp.id, metric_date="2026-11-19",
                metric_code=METRIC_COMPLETION_RATE, metric_value=1.5,
            )


# ─────────────────────────────────────────────────────────────────────────
# 证据附件
# ─────────────────────────────────────────────────────────────────────────


class TestArtifact:
    def test_上传截图(self, db, emp, svc):
        m = svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.35,
        )
        art = svc.add_artifact(
            m.id, name="s.png", media_type="image/png", content=b"\x89PNG",
        )
        assert art.digest == hashlib.sha256(b"\x89PNG").hexdigest()

    def test_可执行文件被拒(self, db, emp, svc):
        m = svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.35,
        )
        with pytest.raises(PanManualMetricError):
            svc.add_artifact(
                m.id, name="x.exe", media_type="application/x-msdownload",
                content=b"MZ",
            )

    def test_超大被拒(self, db, emp, svc):
        m = svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.35,
        )
        with pytest.raises(PanManualMetricError):
            svc.add_artifact(
                m.id, name="b.mp4", media_type="video/mp4",
                content=b"0" * (21 * 1024 * 1024),
            )


# ─────────────────────────────────────────────────────────────────────────
# 月度聚合
# ─────────────────────────────────────────────────────────────────────────


class TestAggregate:
    def _seed_daily(self, db, emp, *, day="2026-11-19", total=6, adjusted=None):
        db.add(
            PanWorkloadDaily(
                employee_id=emp.id,
                business_date=date.fromisoformat(day),
                total_count=total,
                file_count=total,
                adjusted_count=adjusted,
            )
        )
        db.commit()

    def test_网盘条数写成独立metric(self, db, emp, svc):
        self._seed_daily(db, emp, total=6)
        result = svc.aggregate_month(period="2026-11")
        codes = {d["metric_code"] for d in result["detail"]}
        assert METRIC_VIDEO_COUNT in codes
        # ⚠️ **period 落库必须是紧凑 ``YYYYMM``**
        #   框架的唯一权威格式是 ``YYYYMM``（见 ``evidence_service.add_metric_value``
        #   的 BUG-R6-26 修复）。本模块原先自己建行并写 ``2026-11``
        #   ⇒ 与其他写入方**格式分叉** ⇒ 唯一约束
        #   ``(employee_id, metric_code, period)`` 失效 ⇒ 同一人同一指标两行。
        row = db.scalar(
            select(MetricValue).where(
                MetricValue.employee_id == emp.id,
                MetricValue.metric_code == METRIC_VIDEO_COUNT,
                MetricValue.period == "202611",
            )
        )
        assert row is not None, "period 没归一化到 YYYYMM"
        assert row.value == 6.0
        assert row.unit == "条"

    def test_回归_周期格式不与框架分叉(self, db, emp, svc):
        """🔴 **回归点** —— 聚合写入的 period 必须走 ``add_metric_value``。

        ⚠️ 真实库里 ``metric_value.period`` 的既有值全是 ``202611``
        （紧凑格式）。若本模块写 ``2026-11`` ⇒ 同一周期两种格式并存
        ⇒ 跨表比对 / 唯一约束 / 报表聚合全部分叉。
        """
        self._seed_daily(db, emp, total=6)
        svc.aggregate_month(period="2026-11")
        periods = {
            r.period
            for r in db.scalars(select(MetricValue)).all()
            if r.metric_code == METRIC_VIDEO_COUNT
        }
        assert periods == {"202611"}, f"周期格式分叉了：{periods}"

    def test_回归_与框架写入方共用唯一约束(self, db, emp, svc):
        """🔴 **回归点** —— 两边写同一指标同一周期 ⇒ 必须合并成一行。

        ⚠️ 这是「绕过 ``add_metric_value``」最直接的危害：
        框架写 ``202611``、本模块写 ``2026-11`` ⇒ 唯一约束拦不住 ⇒ 两行。
        """
        from backend.app.services.evidence_service import add_metric_value

        self._seed_daily(db, emp, total=6)
        # 框架侧先写一行
        add_metric_value(
            db, employee_id=emp.id, metric_code=METRIC_VIDEO_COUNT,
            period="2026-11", value=99.0, unit="条", source="manual",
        )
        db.flush()
        # 本模块再聚合 ⇒ 应**覆盖**同一行，而不是新增一行
        svc.aggregate_month(period="2026-11")
        rows = db.scalars(
            select(MetricValue).where(MetricValue.employee_id == emp.id)
        ).all()
        assert len(rows) == 1, f"同一指标同一周期出现 {len(rows)} 行"
        assert rows[0].value == 6.0

    def test_人工指标与网盘条数分成两条(self, db, emp, svc):
        """⚠️ **量纲不同 ⇒ 不合成综合分**。"""
        self._seed_daily(db, emp, total=6)
        svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.35,
        )
        svc.aggregate_month(period="2026-11")
        rows = db.scalars(
            select(MetricValue).where(MetricValue.employee_id == emp.id)
        ).all()
        codes = {r.metric_code for r in rows}
        assert codes == {METRIC_VIDEO_COUNT, METRIC_COMPLETION_RATE}
        assert len(rows) == 2

    def test_逐条填报取平均(self, db, emp, svc):
        svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.3,
            scope="file", target_key="a",
        )
        svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.5,
            scope="file", target_key="b",
        )
        svc.aggregate_month(period="2026-11")
        row = db.scalar(
            select(MetricValue).where(
                MetricValue.metric_code == METRIC_COMPLETION_RATE
            )
        )
        assert row.value == pytest.approx(0.4)

    def test_优先取人工裁定值(self, db, emp, svc):
        """⚠️ 裁定值优先于机器判定值。"""
        self._seed_daily(db, emp, total=6, adjusted=8)
        svc.aggregate_month(period="2026-11")
        row = db.scalar(
            select(MetricValue).where(
                MetricValue.metric_code == METRIC_VIDEO_COUNT
            )
        )
        assert row.value == 8.0

    def test_聚合幂等(self, db, emp, svc):
        """⚠️ 重跑是覆盖不是追加。"""
        self._seed_daily(db, emp, total=6)
        svc.submit(
            employee_id=emp.id, metric_date="2026-11-19",
            metric_code=METRIC_COMPLETION_RATE, metric_value=0.35,
        )
        svc.aggregate_month(period="2026-11")
        svc.aggregate_month(period="2026-11")
        rows = db.scalars(select(MetricValue)).all()
        assert len(rows) == 2
        row = db.scalar(
            select(MetricValue).where(
                MetricValue.metric_code == METRIC_VIDEO_COUNT
            )
        )
        assert row.value == 6.0

    def test_默认上个月(self, db, emp, svc):
        result = svc.aggregate_month()
        assert re.fullmatch(r"\d{4}-\d{2}", result["period"])


# ─────────────────────────────────────────────────────────────────────────
# 飞书端解析
# ─────────────────────────────────────────────────────────────────────────


class TestFeishuParse:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("35%", 0.35),
            ("0.35", 0.35),
            ("35", 0.35),
            ("35.5%", 0.355),
            ("完播率 35%", 0.35),
        ],
    )
    def test_解析数值(self, text, expected):
        assert extract_value(text) == pytest.approx(expected)

    def test_解析不出数值返回None(self):
        """⚠️ **绝不猜** —— 解析不出就是 None。"""
        assert extract_value("完播率不错") is None
        assert extract_value("") is None

    def test_识别指标(self):
        assert detect_metric_code("完播率 35%") == METRIC_COMPLETION_RATE
        assert detect_metric_code("留存率 20%") == METRIC_RETENTION_RATE

    def test_识别不出指标报错(self):
        with pytest.raises(FeishuManualMetricError):
            detect_metric_code("随便说点什么")

    def test_解析日期(self):
        assert extract_date("2026-11-19 完播率 35%") == "2026-11-19"
        assert extract_date("11月19日完播率35%") == "2026-11-19"

    def test_没有日期用默认(self):
        assert extract_date("完播率 35%", default="2026-01-01") == "2026-01-01"


# ─────────────────────────────────────────────────────────────────────────
# 飞书端提交
# ─────────────────────────────────────────────────────────────────────────


class TestFeishuSubmit:
    def test_从文本提交(self, db, emp):
        result = submit_from_text(
            db, employee_id=emp.id, text="完播率 35%",
            open_id="ou_1", chat_id="oc_1", message_id="om_1",
        )
        assert result["metric_code"] == METRIC_COMPLETION_RATE
        assert result["value"] == pytest.approx(0.35)
        row = db.scalars(select(PanManualMetric)).one()
        assert row.source == "feishu"
        assert row.feishu_open_id == "ou_1"
        assert row.feishu_message_id == "om_1"

    def test_数值解析不出报错(self, db, emp):
        with pytest.raises(FeishuManualMetricError):
            submit_from_text(
                db, employee_id=emp.id, text="完播率还行",
                open_id="ou_1",
            )

    def test_存证据截图(self, db, emp):
        submit_from_text(
            db, employee_id=emp.id, text="完播率 35%",
            open_id="ou_1", message_id="om_1",
        )
        result = fmm.store_evidence(
            db, employee_id=emp.id, message_id="om_2",
            image_key="img_v2_abc", content=b"\x89PNGscreenshot",
            filename="shot.png", open_id="ou_1",
        )
        assert result["artifact_id"]
        art = db.scalars(select(PanManualMetricArtifact)).one()
        assert art.feishu_image_key == "img_v2_abc"
        assert art.source == "feishu"
        assert art.digest == hashlib.sha256(b"\x89PNGscreenshot").hexdigest()

    def test_没指标时存证据_存为待关联(self, db, emp):
        """⚠️ **截图不能因为「还没填数值」被丢弃**。

        员工的自然顺序往往是「先发截图，再补数值」⇒ 截图先存着。
        """
        result = fmm.store_evidence(
            db, employee_id=emp.id, message_id="om_1",
            image_key="img_abc", content=b"x",
        )
        assert result["pending"] is True
        art = db.scalars(select(PanManualMetricArtifact)).one()
        assert art.metric_id is None

    def test_补数值后待关联截图自动挂上(self, db, emp):
        """⚠️ 员工不必再发一次截图。"""
        fmm.store_evidence(
            db, employee_id=emp.id, message_id="om_1",
            image_key="img_abc", content=b"x",
        )
        result = submit_from_text(
            db, employee_id=emp.id, text="完播率 35%",
            open_id="ou_1", message_id="om_2",
        )
        assert result["attached_evidence"] == 1
        art = db.scalars(select(PanManualMetricArtifact)).one()
        assert art.metric_id == result["metric_id"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
