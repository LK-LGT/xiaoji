"""R6 勘察发现：飞书同步游标语义缺陷的回归测试。

缺陷本体
--------
游标（``organization_sync_cursor.checkpoint_value``）被
``CursorManager.departments_to_process()`` 当作**切分点**消费：
「checkpoint 之前的部门算已处理，之后的算待处理」。

但旧的 ``_advance_cursors()`` 记录的是 **全量部门列表的最后一个 ID**，
而不是**本轮真正处理过的最后一个部门**。

为什么这个缺陷长期没暴露
------------------------
``departments_to_process()`` 切出的 ``to_process`` 恰好是 ``departments``
的**后缀切片**，所以「全量列表最后一个」与「本轮处理最后一个」
在 ``to_process`` 非空时**恰好相等** —— 旧 bug 被掩盖了。

但一旦 ``to_process`` 为空（增量模式已到树末尾），两者就会错位：
旧实现把 checkpoint 改写成「全量列表最后一个」，
而那可能是一个**本轮根本没处理过**的位置。

真实库已复现的实际故障
----------------------
真实库 30 个部门，checkpoint 恒为 ``e27127g2abc13e82``（列表最后一个，索引 29）。
此后每轮增量同步的 ``to_process`` 恒为空 → **新增/变更的部门永远同步不到**；
而批次状态一直显示 ``ok``（30 skipped / 0 synced），运维完全无感。

另一个真实错位场景：checkpoint 指向的部门**已从远端删除**时，
``departments_to_process()`` 退化为「处理全部」，
但旧实现仍按「全量列表最后一个」记录 checkpoint —— 位置同样是猜的。

修复
----
``_advance_cursors()`` 增加 ``processed_department_ids`` 参数，
checkpoint 改取「本轮实际处理过的最后一个部门」；
本轮一个部门都没处理时**不推进**游标。
"""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import Base, Department, OrganizationSyncCursor
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.organization_sync import OrganizationSyncService
from backend.app.services.sync_cursor import RESOURCE_DEPARTMENT, CursorManager


def _token_response(token: str = "token-1"):
    return {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": token, "expire": 7200},
    }


def _client_with_departments(
    dept_ids: list[str],
    *,
    failing_employee_dept: str | None = None,
):
    """构造一个返回指定部门列表的假飞书客户端。

    ``failing_employee_dept``：该部门的员工列表接口持续返回 500，
    用于制造「部门拉取失败 → 快照不完整 → 该部门未真正处理」。
    """
    employee = {
        "user_id": "u_ok",
        "open_id": "o_ok",
        "union_id": "un_ok",
        "name": "员工",
        "department_ids": [dept_ids[0]],
        "leader_user_id": None,
        "email": "ok@example.com",
        "status": {"is_activated": True, "is_exited": False},
    }
    recordings: dict[str, list] = {
        "POST /auth/v3/tenant_access_token/internal": [_token_response()],
        "GET /contact/v3/departments/0/children": [
            {
                "status_code": 200,
                "json": {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "department_id": did,
                                "name": f"部门{did}",
                                "parent_department_id": "0",
                                "status": 1,
                            }
                            for did in dept_ids
                        ]
                    },
                },
            }
        ],
        "GET /contact/v3/users": [
            {
                "status_code": 200,
                "json": {"code": 0, "data": {"items": [employee]}},
            }
        ],
    }
    if failing_employee_dept:
        recordings["GET /contact/v3/users"] = [
            {"status_code": 500, "json": {"code": 99991400, "msg": "busy"}}
        ]
    return FeishuClient(
        "app", "secret", transport=MockTransport(recordings), sleep=lambda _: None
    )


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _checkpoint(db) -> str | None:
    row = db.execute(
        select(OrganizationSyncCursor).where(
            OrganizationSyncCursor.resource_type == RESOURCE_DEPARTMENT
        )
    ).scalar_one_or_none()
    return row.checkpoint_value if row else None


# ──────────────────────────────────────────────────────────────
# 基础语义
# ──────────────────────────────────────────────────────────────


def test_full_sync_checkpoint_lands_on_last_processed_department():
    """全量同步后 checkpoint 应落在最后一个部门（切分点正确）。"""
    db = _db()
    client = _client_with_departments(["d1", "d2", "d3"])
    service = OrganizationSyncService(db, client, mode="full", triggered_by="admin")
    result = service.sync_all()

    assert result["status"] == "ok"
    assert result["departments_synced"] == 3
    assert _checkpoint(db) == "d3"


def test_incremental_sync_processes_departments_after_checkpoint():
    """增量同步应真的处理 checkpoint 之后的部门。"""
    db = _db()
    cursor = CursorManager(db)
    cursor.advance_checkpoint(RESOURCE_DEPARTMENT, "d1")
    cursor.commit()

    client = _client_with_departments(["d1", "d2", "d3"])
    service = OrganizationSyncService(db, client, mode="incremental", triggered_by="admin")
    result = service.sync_all()

    assert result["departments_skipped"] == 1, "d1 应被跳过"
    assert result["departments_synced"] == 2, "d2、d3 应被处理"
    assert _checkpoint(db) == "d3"


def test_incremental_sync_does_not_advance_cursor_when_nothing_processed():
    """本轮无部门可处理时，游标**不得**推进。

    推进了就等于把「还没处理的部门」标记成已处理 ——
    下次增量同步会跳过它们，缺口永久留下。
    """
    db = _db()
    cursor = CursorManager(db)
    cursor.advance_checkpoint(RESOURCE_DEPARTMENT, "d3")
    cursor.commit()

    client = _client_with_departments(["d1", "d2", "d3"])
    service = OrganizationSyncService(db, client, mode="incremental", triggered_by="admin")
    result = service.sync_all()

    assert result["departments_skipped"] == 3
    assert result["departments_synced"] == 0
    assert _checkpoint(db) == "d3", "无部门处理时 checkpoint 必须保持不变"


# ──────────────────────────────────────────────────────────────
# 核心回归：游标不得推进到未处理的位置
# ──────────────────────────────────────────────────────────────


def test_checkpoint_never_advances_past_unprocessed_department():
    """回归：游标不得越过未成功处理的部门。

    当某部门处理失败、而它后面的部门处理成功时，
    checkpoint 若被推到失败部门之后，该失败部门**永远不会被重试** ——
    数据缺口永久留下。
    """
    db = _db()
    cursor = CursorManager(db)
    cursor.advance_checkpoint(RESOURCE_DEPARTMENT, "d1")
    cursor.commit()

    # d2 的员工列表拉取失败；d3 正常
    client = _client_with_departments(
        ["d1", "d2", "d3"], failing_employee_dept="d2"
    )
    service = OrganizationSyncService(db, client, mode="incremental", triggered_by="admin")
    result = service.sync_all()

    assert result["departments_failed"] >= 1, "d2 的失败必须被记录"

    cp = _checkpoint(db)
    assert cp != "d3", f"checkpoint 不得越过失败的 d2（实际={cp}）"


def test_cursor_stays_put_when_nothing_processed_even_if_tree_grew():
    """回归（核心）：本轮无部门可处理时，游标**保持不变**。

    旧实现取「全量部门列表的最后一个」作为 checkpoint。
    当 checkpoint 已经位于树末尾、而远端又在**前面**插入了新部门时，
    to_process 为空，但旧实现仍会把 checkpoint 改写成那个
    「全量列表最后一个」—— 即一个**本轮根本没处理过**的位置。

    真实库已复现：30 个部门，checkpoint 恒为 ``e27127g2abc13e82``（末尾），
    此后每轮增量 to_process 恒为空 → **新增/变更的部门永远同步不到**，
    而批次状态一直是 ok，运维完全无感。
    """
    db = _db()
    cursor = CursorManager(db)
    cursor.advance_checkpoint(RESOURCE_DEPARTMENT, "d3")
    cursor.commit()

    # 远端在 d3 **之前**插入了新部门 d4 —— 树顺序发生变化
    client = _client_with_departments(["d4", "d1", "d2", "d3"])
    service = OrganizationSyncService(db, client, mode="incremental", triggered_by="admin")
    result = service.sync_all()

    assert result["departments_synced"] == 0, "本轮没有可处理的部门"
    assert _checkpoint(db) == "d3", (
        "游标不得被改写到本轮未处理的位置（d4 之前的位置）"
    )


def test_checkpoint_equals_last_actually_processed_department():
    """回归：checkpoint 必须等于**本轮真正处理过的最后一个部门**。

    旧实现取全量列表最后一个 —— 二者在 to_process 非空时恰好相等，
    所以旧 bug 被长期掩盖；一旦 to_process 为空就会错位。
    修复后语义显式化：checkpoint = 本轮处理集合的末尾。
    """
    db = _db()
    cursor = CursorManager(db)
    cursor.advance_checkpoint(RESOURCE_DEPARTMENT, "d1")
    cursor.commit()

    client = _client_with_departments(["d1", "d2", "d3"])
    service = OrganizationSyncService(db, client, mode="incremental", triggered_by="admin")
    result = service.sync_all()

    processed = {"d2", "d3"}
    assert result["departments_synced"] == len(processed)
    assert _checkpoint(db) == "d3"
    assert _checkpoint(db) in processed, "checkpoint 必须落在已处理集合内"


def test_new_department_appended_is_picked_up_by_incremental_sync():
    """回归：远端**末尾**新增部门后，增量同步必须能把它同步进来。

    旧实现导致的**实际业务故障**：
    checkpoint 恒在末尾 → 新增部门永远进不来，
    但批次状态一直是 ok，运维完全无感。
    """
    db = _db()
    client = _client_with_departments(["d1", "d2"])
    service = OrganizationSyncService(db, client, mode="full", triggered_by="admin")
    service.sync_all()
    assert _checkpoint(db) == "d2"

    # 远端新增 d3
    client2 = _client_with_departments(["d1", "d2", "d3"])
    service2 = OrganizationSyncService(db, client2, mode="incremental", triggered_by="admin")
    result = service2.sync_all()

    assert result["departments_synced"] >= 1, "新增部门必须被同步"
    assert db.get(Department, "d3") is not None, "d3 必须落库"
    assert _checkpoint(db) == "d3"


def test_checkpoint_is_stable_when_department_order_changes():
    """回归：飞书部门树顺序不保证稳定，checkpoint 语义仍应是「切分点」。

    场景：checkpoint=d2，本轮部门树顺序变成 d3,d1,d2,d4。
    按切分点语义：d3/d1 在 d2 之前 → 已处理；d4 在 d2 之后 → 待处理。

    修复后 checkpoint 应推进到 d4（本轮真正处理的最后一个部门），
    而不是「全量列表最后一个」（本例恰好也是 d4，但语义完全不同）。
    """
    db = _db()
    cursor = CursorManager(db)
    cursor.advance_checkpoint(RESOURCE_DEPARTMENT, "d2")
    cursor.commit()

    client = _client_with_departments(["d3", "d1", "d2", "d4"])
    service = OrganizationSyncService(db, client, mode="incremental", triggered_by="admin")
    result = service.sync_all()

    assert result["departments_skipped"] == 3, "d3、d1、d2 在切分点之前，应被跳过"
    assert result["departments_synced"] == 1, "只有 d4 在切分点之后"
    assert _checkpoint(db) == "d4", "checkpoint 应为本轮真正处理的最后一个部门"

    # 下一轮：d4 之后新增 d5，应被切出来处理
    to_process, _skipped = CursorManager(db).departments_to_process(
        [{"department_id": "d3"}, {"department_id": "d1"},
         {"department_id": "d2"}, {"department_id": "d4"},
         {"department_id": "d5"}],
        mode="incremental",
    )
    processed = {d["department_id"] for d in to_process}
    assert processed == {"d5"}, "只有新增的 d5 应进入待处理区间"
