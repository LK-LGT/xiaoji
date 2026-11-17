"""组织同步的**写入阶段不得长时间持有 SQLite 写锁**（BUG-R6-48）。

背景
----
实跑发现：飞书组织同步一启动，系统里提交绩效 / 审批这类**写请求**
就报 ``OperationalError: database is locked``。

⚠️ **归因订正**（重要，避免后人重走弯路）：
最初以为线上报的是「同步线程持锁」导致的超时。查日志后发现那次
traceback 指向的是**注释行**::

    File "backend\\app\\core\\db.py", line 44, in _set_sqlite_pragma
        #    `OperationalError: database is locked`。
                                           ^^^^^^^
    NameError: name 'SQLITE_BUSY_TIMEOUT_MS' is not defined

真正的异常是 **NameError** —— watchfiles 热重载期间的瞬态问题
（正在被编辑的 ``db.py`` 重载时模块状态不一致），**已自愈**，与锁无关。

⇒ 但顺着这条线查下去，发现了**真实**的锁问题（机制见下）。

机制（已在真实库上实测确认）：
- SQLite 的写锁是**库级别**的 —— 一个连接持有写锁，其它连接全都写不了；
- 实测：持锁方 flush 未提交的写后，另一连接写**立即**报
  ``database is locked``（等了 0.00s，不是等 busy_timeout ——
  因为连接池里的连接已开事务，直接撞锁）；
- 同步的**写入阶段**（``_sync_departments`` / ``_sync_employees``）
  全程在一个长事务里（每条一个 SAVEPOINT，最后统一 commit），
  80+ 部门 / 90+ 员工 ⇒ 写锁被持有整个写入阶段。

⇒ 表现为「一跑同步，普通写请求就报锁错误」。

修复
----
``organization_sync.OrganizationSyncService._commit_write_chunk()``：
写入阶段**分块提交**（每 ``_WRITE_CHUNK_SIZE``=20 条提交一次），
把写锁持有时间切成短片段。

本测试锁死这个行为：写入阶段处理记录时，
**另一个连接必须能立刻写入**（不被等锁 / 不报错）。
"""

from __future__ import annotations

import time

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import Base, Department, OrganizationSyncBatch
from backend.app.services import organization_sync
from backend.app.services.organization_sync import OrganizationSyncService


class _StubFeishuClient:
    """不需要真实调飞书的替身。"""

    def fetch_departments(self, **kwargs):
        return []

    def fetch_employees_in_department(self, dept_id, **kwargs):
        return []


def _make_engine(db_path):
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)

    # ⚠️ 给每个连接设**短**的 busy_timeout ——
    # 否则锁冲突时要等满默认的 30 秒，测试会跑成一分钟。
    @event.listens_for(engine, "connect")
    def _set_busy_timeout(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=2000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def _make_batch(session) -> OrganizationSyncBatch:
    batch = OrganizationSyncBatch(
        id="batch-test",
        batch_no="ORG-TEST",
        status="running",
        mode="full",
        trigger_type="manual",
        phase="running",
    )
    session.add(batch)
    session.commit()
    return batch


def test_write_phase_releases_lock_periodically(tmp_path):
    """写入阶段必须周期性提交，释放写锁。

    构造 25 个部门（> ``_WRITE_CHUNK_SIZE``=20），
    在处理过程中从另一个连接写入 —— 必须能立刻成功。
    """
    engine = _make_engine(tmp_path / "write_lock.db")
    Session = sessionmaker(bind=engine, autoflush=False)

    # ⚠️ 基准行必须**已提交** —— 见下方「踩坑记录」。
    baseline = Session()
    baseline.execute(
        text(
            "INSERT INTO organization_sync_cursor "
            "(id, resource_type, cursor_value, page_token, checkpoint_value, "
            " snapshot_complete, last_batch_id, updated_at) "
            "VALUES ('baseline-row', 'probe-baseline', 'origin', NULL, NULL, 1, NULL, datetime('now'))"
        )
    )
    baseline.commit()
    baseline.close()

    sync_session = Session()
    service = OrganizationSyncService(sync_session, _StubFeishuClient())
    batch = _make_batch(sync_session)

    # 25 个部门 → 会触发一次分块提交（第 20 条后）
    departments = [
        {"department_id": f"d{i:03d}", "name": f"部门{i}", "status": {"is_deleted": False}}
        for i in range(25)
    ]

    # ⚠️ 用一个钩子，在**处理到第 21 条之后**（分块提交已发生）
    #    从另一个连接写入 —— 这是最严格的时序。
    written: dict[str, object] = {}
    original_commit_chunk = service._commit_write_chunk

    def hooked_commit_chunk(b):
        original_commit_chunk(b)
        # 分块提交刚做完 —— 此时若仍持锁，说明修复没生效
        other = Session()
        t0 = time.time()
        try:
            other.execute(
                text(
                    "UPDATE organization_sync_cursor SET cursor_value='probe-write' "
                    "WHERE id='baseline-row'"
                )
            )
            other.commit()
            written["ok"] = True
            written["elapsed"] = time.time() - t0
        except Exception as exc:
            other.rollback()
            written["ok"] = False
            written["error"] = str(exc)
        finally:
            other.close()

    service._commit_write_chunk = hooked_commit_chunk  # type: ignore[method-assign]

    stats = {"departments_synced": 0, "departments_skipped": 0, "departments_failed": 0}
    service._sync_departments(batch, departments, stats)

    assert written.get("ok"), (
        f"分块提交后仍持有写锁，另一个连接写入失败: {written.get('error')}"
    )
    assert written["elapsed"] < 1.5, (
        f"写入被锁阻塞了 {written['elapsed']:.1f}s"
    )

    # 25 个部门全部落库
    with Session() as check:
        count = check.query(Department).count()
    assert count == 25, f"部门未全部落库: {count}"

    sync_session.close()


def test_write_chunk_size_is_respected():
    """分块大小常量必须是合理值（不是 0 或极大值）。"""
    size = organization_sync._WRITE_CHUNK_SIZE
    assert 0 < size <= 100, f"分块大小不合理: {size}"
