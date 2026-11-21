"""网盘剪辑工作量 —— 复核工单测试（BP-2 / P2）。

覆盖
----
1. 发起工单（**快照当时判定值**）
2. 争议项：✅ 勾选系统文件 / ✅ 手填系统没有的 / ❌ 两者都不给
3. 证据附件：✅ 上传 / ❌ 类型不支持 / ❌ 超过 20MB
4. 逐项裁定三档：``upheld`` / ``rejected`` / ``need_more``
5. 逐文件裁定的**增量算法**（漏算加、多算减、权重调整）
6. ⚠️ **裁定只写 ``adjusted_count``，永不改 ``total_count``**
7. **hash 链**：追加、连续性、篡改检测、附件摘要校验
8. 附件与事件流**不可变**
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import Base, Employee
from backend.app.domain.pan_workload_models import (
    PanWorkloadDaily,
    PanWorkloadFile,
    PanWorkloadReview,
    PanWorkloadReviewArtifact,
    PanWorkloadReviewEvent,
    PanWorkloadReviewItem,
)
from backend.app.services.pan_workload_review_service import (
    PanWorkloadReviewError,
    PanWorkloadReviewService,
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
    return PanWorkloadReviewService(db)


def _seed_day(db, emp, *, total: int = 6, files: int = 5, day="2026-11-19"):
    """造一条日汇总 + 若干文件。"""
    from datetime import date as _date

    db.add(
        PanWorkloadDaily(
            employee_id=emp.id,
            business_date=_date.fromisoformat(day),
            total_count=total,
            file_count=files,
            scanned_count=files + 2,
            excluded_count=2,
        )
    )
    ids = []
    for i in range(files):
        f = PanWorkloadFile(
            fs_id=100 + i,
            pan_path=f"/成品-小瑶/11月/11.19/成片{i}.mp4",
            file_name=f"成片{i}.mp4",
            extension="mp4",
            employee_id=emp.id,
            business_date=day,
            counted=True,
            count_weight=1,
        )
        db.add(f)
        ids.append(f)
    db.commit()
    return ids


# ─────────────────────────────────────────────────────────────────────────
# 发起
# ─────────────────────────────────────────────────────────────────────────


class TestCreate:
    def test_发起工单_快照当时判定值(self, db, emp, svc):
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id,
            business_date="2026-11-19",
            claimed_count=8,
            reason="我剪了 8 条",
            created_by=emp.id,
        )
        assert review.status == "pending"
        assert review.system_count == 6
        assert review.claimed_count == 8
        assert review.daily_snapshot["total_count"] == 6
        assert review.daily_snapshot["excluded_count"] == 2

    def test_发起后日汇总标记为有异议(self, db, emp, svc):
        _seed_day(db, emp)
        svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=8, reason="数不对",
        )
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.status == "disputed"

    def test_必须填理由(self, db, emp, svc):
        _seed_day(db, emp)
        with pytest.raises(PanWorkloadReviewError):
            svc.create_review(
                employee_id=emp.id, business_date="2026-11-19",
                claimed_count=8, reason="   ",
            )

    def test_非法粒度被拒(self, db, emp, svc):
        _seed_day(db, emp)
        with pytest.raises(PanWorkloadReviewError):
            svc.create_review(
                employee_id=emp.id, business_date="2026-11-19",
                claimed_count=8, reason="x", scope="week",
            )


# ─────────────────────────────────────────────────────────────────────────
# 争议项
# ─────────────────────────────────────────────────────────────────────────


class TestItems:
    def test_勾选系统文件(self, db, emp, svc):
        files = _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="有一条漏了",
        )
        item = svc.add_item(
            review.id, pan_workload_file_id=files[0].id,
            dispute_type="missing", claimed_weight=1,
        )
        assert item.pan_workload_file_id == files[0].id
        assert item.resolution == "pending"

    def test_手填系统没有的文件(self, db, emp, svc):
        """⚠️ 漏传的情况 —— 系统根本没扫到。"""
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="有个文件没传上去",
        )
        item = svc.add_item(
            review.id, manual_name="漏传的成片.mp4",
            manual_description="当天剪的，忘记传到网盘",
            dispute_type="missing", claimed_weight=1,
        )
        assert item.pan_workload_file_id is None
        assert item.manual_name == "漏传的成片.mp4"

    def test_既不勾选也不手填_被拒(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        with pytest.raises(PanWorkloadReviewError):
            svc.add_item(review.id)

    def test_已裁定后不能加(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        svc.resolve_review(review.id, resolution="approved", final_count=7)
        with pytest.raises(PanWorkloadReviewError):
            svc.add_item(review.id, manual_name="a.mp4")


# ─────────────────────────────────────────────────────────────────────────
# 证据附件
# ─────────────────────────────────────────────────────────────────────────


class TestArtifacts:
    def test_上传图片(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        art = svc.add_artifact(
            review.id, name="shot.png", media_type="image/png",
            content=b"\x89PNG-fake",
        )
        assert art.digest == hashlib.sha256(b"\x89PNG-fake").hexdigest()
        assert art.size_bytes == 9

    def test_上传视频(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        art = svc.add_artifact(
            review.id, name="v.mp4", media_type="video/mp4", content=b"fake-mp4",
        )
        assert art.media_type == "video/mp4"

    def test_不支持的类型被拒(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        with pytest.raises(PanWorkloadReviewError):
            svc.add_artifact(
                review.id, name="x.exe", media_type="application/x-msdownload",
                content=b"MZ",
            )

    def test_超大文件被拒(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        with pytest.raises(PanWorkloadReviewError):
            svc.add_artifact(
                review.id, name="big.mp4", media_type="video/mp4",
                content=b"0" * (21 * 1024 * 1024),
            )

    def test_附件挂到争议项(self, db, emp, svc):
        files = _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        item = svc.add_item(
            review.id, pan_workload_file_id=files[0].id, dispute_type="missing",
        )
        art = svc.add_artifact(
            review.id, name="s.png", media_type="image/png", content=b"x",
            review_item_id=item.id,
        )
        assert art.review_item_id == item.id


# ─────────────────────────────────────────────────────────────────────────
# 裁定
# ─────────────────────────────────────────────────────────────────────────


class TestResolve:
    def test_按天裁定(self, db, emp, svc):
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=8, reason="x", scope="day",
        )
        svc.resolve_review(
            review.id, resolution="approved", final_count=8,
            resolution_note="核实属实", actor_id="mgr",
        )
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.adjusted_count == 8
        assert daily.status == "confirmed"

    def test_按天裁定必须给最终条数(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=8, reason="x", scope="day",
        )
        with pytest.raises(PanWorkloadReviewError):
            svc.resolve_review(review.id, resolution="approved")

    def test_逐文件裁定_漏算项被加上(self, db, emp, svc):
        """⚠️ 核心算法：final = system + Σ增量。"""
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x", scope="file",
        )
        item = svc.add_item(
            review.id, manual_name="漏传.mp4", dispute_type="missing",
            claimed_weight=1,
        )
        svc.resolve_item(item.id, resolution="upheld", actor_id="mgr")
        svc.resolve_review(review.id, resolution="approved", actor_id="mgr")
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.adjusted_count == 7, "6 + 1 = 7"

    def test_逐文件裁定_多算项被减去(self, db, emp, svc):
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=5, reason="x", scope="file",
        )
        item = svc.add_item(
            review.id, manual_name="封面.mp4", dispute_type="overcounted",
            claimed_weight=1,
        )
        # 裁定：这条不该算（final_weight=0）
        svc.resolve_item(item.id, resolution="upheld", final_weight=0, actor_id="mgr")
        svc.resolve_review(review.id, resolution="approved", actor_id="mgr")
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.adjusted_count == 5, "6 - 1 = 5"

    def test_逐文件裁定_权重项被调整(self, db, emp, svc):
        """⚠️ 该算 2 条算成了 1 条 ⇒ 应 +1。"""
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x", scope="file",
        )
        item = svc.add_item(
            review.id, manual_name="智能体.mp4", dispute_type="weight",
            claimed_weight=1,
        )
        svc.resolve_item(item.id, resolution="upheld", final_weight=2, actor_id="mgr")
        svc.resolve_review(review.id, resolution="approved", actor_id="mgr")
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.adjusted_count == 7, "6 + (2-1) = 7"

    def test_不成立项不改变数(self, db, emp, svc):
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x", scope="file",
        )
        item = svc.add_item(
            review.id, manual_name="瞎说的.mp4", dispute_type="missing",
            claimed_weight=1,
        )
        svc.resolve_item(item.id, resolution="rejected", actor_id="mgr")
        svc.resolve_review(review.id, resolution="approved", actor_id="mgr")
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.adjusted_count == 6, "被驳回的项不改变条数"

    def test_需补证不改变数(self, db, emp, svc):
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x", scope="file",
        )
        item = svc.add_item(
            review.id, manual_name="待补证.mp4", dispute_type="missing",
            claimed_weight=1,
        )
        svc.resolve_item(item.id, resolution="need_more", actor_id="mgr")
        svc.resolve_review(review.id, resolution="approved", actor_id="mgr")
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.adjusted_count == 6

    def test_驳回工单_不调整条数(self, db, emp, svc):
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=8, reason="x",
        )
        svc.resolve_review(review.id, resolution="rejected", actor_id="mgr")
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.adjusted_count is None, "驳回不产生调整值"
        assert daily.total_count == 6

    def test_裁定不改total_count(self, db, emp, svc):
        """⚠️⚠️ **机器判定值永不被覆盖** —— 这是审计的前提。"""
        _seed_day(db, emp, total=6)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=8, reason="x",
        )
        svc.resolve_review(
            review.id, resolution="approved", final_count=8, actor_id="mgr",
        )
        daily = db.scalars(select(PanWorkloadDaily)).one()
        assert daily.total_count == 6, "total_count 是机器判定原值，不能被改"
        assert daily.adjusted_count == 8, "调整写在 adjusted_count"

    def test_已裁定不能重复裁定(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=8, reason="x",
        )
        svc.resolve_review(review.id, resolution="approved", final_count=8)
        with pytest.raises(PanWorkloadReviewError):
            svc.resolve_review(review.id, resolution="approved", final_count=9)

    def test_撤回(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=8, reason="x",
        )
        svc.withdraw(review.id, actor_id=emp.id, note="搞错了")
        db.refresh(review)
        assert review.status == "withdrawn"


# ─────────────────────────────────────────────────────────────────────────
# hash 链
# ─────────────────────────────────────────────────────────────────────────


class TestHashChain:
    def test_事件按序追加(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        svc.add_item(review.id, manual_name="a.mp4", dispute_type="missing")
        svc.add_artifact(review.id, name="s.png", media_type="image/png", content=b"x")
        events = svc.list_events(review.id)
        assert [e.seq for e in events] == [1, 2, 3]
        assert [e.event_type for e in events] == [
            "created", "item_added", "artifact_added"
        ]

    def test_链首尾相接(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        svc.add_item(review.id, manual_name="a.mp4")
        events = svc.list_events(review.id)
        assert events[0].prev_hash == ""
        assert events[1].prev_hash == events[0].event_hash

    def test_链校验通过(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        svc.add_artifact(review.id, name="s.png", media_type="image/png", content=b"x")
        result = svc.verify_chain(review.id)
        assert result["ok"], result["issues"]
        assert result["event_count"] == 2

    def test_篡改事件能被检出(self, db, emp, svc):
        """⚠️ 这是 hash 链存在的全部意义。"""
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        svc.add_item(review.id, manual_name="a.mp4")
        # ⚠️ 直接改库（绕过 ORM 的不可变守卫，模拟 DBA 级篡改）
        db.execute(
            PanWorkloadReviewEvent.__table__.update()
            .where(PanWorkloadReviewEvent.review_id == review.id)
            .values(payload={"hacked": True})
        )
        db.commit()
        result = svc.verify_chain(review.id)
        assert not result["ok"]
        assert any("摘要异常" in i for i in result["issues"])

    def test_篡改附件能被检出(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        svc.add_artifact(review.id, name="s.png", media_type="image/png", content=b"x")
        db.execute(
            PanWorkloadReviewArtifact.__table__.update()
            .where(PanWorkloadReviewArtifact.review_id == review.id)
            .values(content=b"tampered")
        )
        db.commit()
        result = svc.verify_chain(review.id)
        assert not result["ok"]
        assert any("附件摘要异常" in i for i in result["issues"])


# ─────────────────────────────────────────────────────────────────────────
# 不可变
# ─────────────────────────────────────────────────────────────────────────


class TestImmutable:
    def test_事件不可改(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        ev = svc.list_events(review.id)[0]
        ev.payload = {"changed": True}
        with pytest.raises(ValueError):
            db.commit()
        db.rollback()

    def test_附件不可删(self, db, emp, svc):
        _seed_day(db, emp)
        review = svc.create_review(
            employee_id=emp.id, business_date="2026-11-19",
            claimed_count=7, reason="x",
        )
        art = svc.add_artifact(
            review.id, name="s.png", media_type="image/png", content=b"x",
        )
        db.delete(art)
        with pytest.raises(ValueError):
            db.commit()
        db.rollback()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
