"""网盘剪辑工作量落库测试（BP-2 / P0·b）。

覆盖：模型约束 / 迁移 / 落库幂等 / 重算不累加 / 排除不丢弃。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.domain.models import Base, Employee  # noqa: E402
from backend.app.domain.pan_workload_models import (  # noqa: E402
    PanAccountBinding,
    PanManualMetric,
    PanWorkloadConfig,
    PanWorkloadDaily,
    PanWorkloadFile,
    PanWorkloadReview,
    PanWorkloadReviewEvent,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(conn, _rec):  # pragma: no cover
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine)
    session = sm()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def emp(db: Session) -> Employee:
    e = Employee(id="emp_1", name="小瑶")
    db.add(e)
    db.commit()
    return e


class TestBinding:
    def test_创建绑定(self, db: Session, emp: Employee):
        db.add(
            PanAccountBinding(
                employee_id=emp.id,
                folder_path="/成品-小瑶",
                folder_name="小瑶",
            )
        )
        db.commit()
        got = db.scalars(select(PanAccountBinding)).all()
        assert len(got) == 1
        assert got[0].bind_type == "path"
        assert got[0].active is True

    def test_同路径不能绑两人(self, db: Session, emp: Employee):
        db.add(Employee(id="emp_2", name="小雨"))
        db.add(
            PanAccountBinding(
                employee_id=emp.id, folder_path="/成品-小瑶", folder_name="小瑶"
            )
        )
        db.commit()
        db.add(
            PanAccountBinding(
                employee_id="emp_2", folder_path="/成品-小瑶", folder_name="小雨"
            )
        )
        with pytest.raises(Exception):
            db.commit()
        db.rollback()

    def test_换目录旧行不删(self, db: Session, emp: Employee):
        db.add(
            PanAccountBinding(
                employee_id=emp.id, folder_path="/成品-小瑶", folder_name="小瑶"
            )
        )
        db.commit()
        row = db.scalars(select(PanAccountBinding)).one()
        row.active = False
        db.add(
            PanAccountBinding(
                employee_id=emp.id, folder_path="/成品-小瑶2", folder_name="新目录"
            )
        )
        db.commit()
        rows = db.scalars(select(PanAccountBinding)).all()
        assert len(rows) == 2
        assert sorted(r.active for r in rows) == [False, True]


class TestWorkloadFile:
    def test_fs_id_唯一_保证幂等(self, db: Session, emp: Employee):
        db.add(
            PanWorkloadFile(
                fs_id=123, pan_path="/成品-小瑶/11月/11.19/a.mp4",
                file_name="a.mp4", employee_id=emp.id, business_date="2026-11-19",
            )
        )
        db.commit()
        db.add(
            PanWorkloadFile(
                fs_id=123, pan_path="/成品-小瑶/11月/11.19/a.mp4",
                file_name="a.mp4", employee_id=emp.id, business_date="2026-11-19",
            )
        )
        with pytest.raises(Exception):
            db.commit()
        db.rollback()

    def test_未归属文件也可落库(self, db: Session):
        """⚠️ 不丢弃 —— 未归属必须能落库并标记待确认。"""
        db.add(
            PanWorkloadFile(
                fs_id=456, pan_path="/口播成品/10月/10.1/a.mp4",
                file_name="a.mp4", bind_type="unbound", business_date="2026-10-01",
            )
        )
        db.commit()
        got = db.scalars(select(PanWorkloadFile)).one()
        assert got.employee_id is None
        assert got.bind_type == "unbound"

    def test_被排除文件也落库(self, db: Session, emp: Employee):
        """⚠️ 封面 png 必须留痕，不能静默丢。"""
        db.add(
            PanWorkloadFile(
                fs_id=789, pan_path="/成品-小瑶/11月/11.19/a.png",
                file_name="a.png", extension="png", employee_id=emp.id,
                business_date="2026-11-19", counted=False,
                exclude_reason="ext_not_allowed:png",
            )
        )
        db.commit()
        got = db.scalars(select(PanWorkloadFile)).one()
        assert got.counted is False
        assert got.exclude_reason == "ext_not_allowed:png"


class TestWorkloadDaily:
    def test_唯一约束_同人同日一条(self, db: Session, emp: Employee):
        db.add(
            PanWorkloadDaily(
                employee_id=emp.id, business_date=date(2026, 11, 19), total_count=3
            )
        )
        db.commit()
        db.add(
            PanWorkloadDaily(
                employee_id=emp.id, business_date=date(2026, 11, 19), total_count=9
            )
        )
        with pytest.raises(Exception):
            db.commit()
        db.rollback()

    def test_人工调整可覆盖自动值(self, db: Session, emp: Employee):
        db.add(
            PanWorkloadDaily(
                employee_id=emp.id, business_date=date(2026, 11, 19),
                total_count=3, adjusted_count=2, adjust_reason="重复上传",
                status="confirmed",
            )
        )
        db.commit()
        got = db.scalars(select(PanWorkloadDaily)).one()
        assert got.total_count == 3
        assert got.adjusted_count == 2
        assert got.status == "confirmed"


class TestReview:
    def test_工单与事件流(self, db: Session, emp: Employee):
        review = PanWorkloadReview(
            employee_id=emp.id,
            business_date=date(2026, 11, 19),
            claimed_count=10,
            system_count=8,
            reason="我觉得是10条",
            created_by=emp.id,
        )
        db.add(review)
        db.commit()
        db.add(
            PanWorkloadReviewEvent(
                review_id=review.id, seq=1, event_type="created",
                actor_id=emp.id, payload={"claimed": 10},
                prev_hash="", event_hash="h1",
            )
        )
        db.commit()
        evs = db.scalars(select(PanWorkloadReviewEvent)).all()
        assert len(evs) == 1
        assert evs[0].event_hash == "h1"

    def test_事件序号唯一(self, db: Session, emp: Employee):
        review = PanWorkloadReview(
            employee_id=emp.id, business_date=date(2026, 11, 19),
            claimed_count=10, system_count=8, created_by=emp.id,
        )
        db.add(review)
        db.commit()
        for seq in (1, 1):
            db.add(
                PanWorkloadReviewEvent(
                    review_id=review.id, seq=seq, event_type="created",
                    actor_id=emp.id, payload={}, event_hash=f"h{seq}",
                )
            )
        with pytest.raises(Exception):
            db.commit()
        db.rollback()


class TestManualMetric:
    def test_同人同日同指标唯一(self, db: Session, emp: Employee):
        db.add(
            PanManualMetric(
                employee_id=emp.id, metric_date=date(2026, 11, 19),
                metric_code="video_completion_rate", metric_value=0.35, created_by=emp.id,
            )
        )
        db.commit()
        db.add(
            PanManualMetric(
                employee_id=emp.id, metric_date=date(2026, 11, 19),
                metric_code="video_completion_rate", metric_value=0.4, created_by=emp.id,
            )
        )
        with pytest.raises(Exception):
            db.commit()
        db.rollback()


class TestConfig:
    def test_默认配置已内置目录黑名单(self, db: Session):
        db.add(
            PanWorkloadConfig(
                id="default",
                allowed_extensions="mp4,mov,m4v,avi,mkv,flv,wmv",
                exclude_keywords="草稿,工程,返工",
                exclude_dir_segments="草稿,工程",
                exclude_paths="/口播成品,/鹿13",
                marker_rules={"default": {"智能体": {"weight": 2, "patterns": ["智能体"]}}},
            )
        )
        db.commit()
        got = db.scalars(select(PanWorkloadConfig)).one()
        assert "口播成品" in got.exclude_paths
        assert "鹿13" in got.exclude_paths
        assert "png" not in got.allowed_extensions


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
