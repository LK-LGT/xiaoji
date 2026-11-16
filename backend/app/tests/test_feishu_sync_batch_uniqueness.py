"""飞书组织同步：批次唯一性回归测试。

背景
----
实跑验证时发现：每次触发同步，数据库里会多出**两条**批次记录 ——
路由 / 服务层建了一条（``status=running``），而 ``sync_all()`` 内部
又新建了一条真正执行的批次。

后果：
- 路由那条永远停在 ``running`` → 前端一直转圈；
- 「已有任务在运行」把后续所有触发全部挡住（409）；
- 运维看批次列表会看到成对的、状态矛盾的记录。

修复：``sync_all(batch=...)`` 复用调用方已建好的批次行。

本测试锁定不变量：**一次触发 = 一条批次记录**。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import Base, OrganizationSyncBatch
from backend.app.services.organization_sync import OrganizationSyncService


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _make_service(db) -> OrganizationSyncService:
    client = MagicMock()
    client.fetch_departments.return_value = []
    client.fetch_employee.return_value = {}
    client.fetch_department_employees.return_value = []
    return OrganizationSyncService(db, client, mode="full", triggered_by="admin")


def test_sync_all_reuses_provided_batch(db):
    """传入批次时，sync_all 必须复用它，不能另建一条。"""
    service = _make_service(db)

    batch = OrganizationSyncBatch(
        batch_no="ORG-REUSE",
        status="running",
        mode="full",
        trigger_type="manual",
        triggered_by="admin",
        phase="running",
    )
    db.add(batch)
    db.commit()
    batch_id = batch.id
    before = db.query(func.count(OrganizationSyncBatch.id)).scalar()

    service.sync_all(batch=batch)

    after = db.query(func.count(OrganizationSyncBatch.id)).scalar()
    assert after == before, "sync_all 不得新建批次记录"

    db.expire_all()
    row = db.get(OrganizationSyncBatch, batch_id)
    assert row is not None
    assert row.status in ("ok", "partial_failed"), (
        f"复用的批次应落到终态，实际 {row.status}"
    )
    assert row.phase == "finished"
    assert row.finished_at is not None


def test_sync_all_without_batch_creates_one(db):
    """不传批次时仍应新建一条（保留独立调用能力）。"""
    service = _make_service(db)

    before = db.query(func.count(OrganizationSyncBatch.id)).scalar()
    result = service.sync_all()
    after = db.query(func.count(OrganizationSyncBatch.id)).scalar()

    assert after == before + 1, "未传批次时应新建一条"
    assert result["batch_id"] is not None
