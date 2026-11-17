"""BUG-R6-45 回归测试：分部门粒度游标的 ``snapshot_complete``。

## 背景

台账登记的 BUG-R6-45：

> ``snapshot_complete`` 恒为 ``False`` → 「远端缺失 = 离职」判定前提从未成立。

## 根因

``snapshot_complete`` 是「远端缺失 = 离职/删除」这一判定的**唯一前提**。
它有三个写入点：

| 游标粒度 | 写入点 | 修复前 |
|---|---|---|
| ``department`` | ``_advance_cursors`` | ✅ True |
| ``employee`` | ``_advance_cursors`` | ✅ True |
| ``employee:<部门ID>`` | **无人写入** | ❌ 恒 False |

分部门粒度的游标由 ``save_page_token`` 维护（分页续传用），
而它**只写 ``page_token``、从不碰 ``snapshot_complete``** —— 于是永远保持初始值 False。

真实库实测：30 条 ``employee:<dept>`` 游标**全部**为 False。
"""

from __future__ import annotations

from sqlalchemy import select

from backend.app.domain.models import OrganizationSyncCursor
from backend.app.services.feishu_client import FeishuClient, MockTransport
from backend.app.services.organization_sync import OrganizationSyncService
from backend.app.services.sync_cursor import RESOURCE_EMPLOYEE, CursorManager


def _token_response():
    return {
        "status_code": 200,
        "json": {"code": 0, "tenant_access_token": "t", "expire": 7200},
    }


def _make_client(*, page_count: int = 1):
    """构造一个 Mock 飞书客户端。"""
    user_payload = {
        "user_id": "u1",
        "open_id": "ou1",
        "union_id": "on1",
        "name": "张三",
        "department_ids": ["d1"],
        "leader_user_id": None,
        "status": {
            "is_activated": True,
            "is_exited": False,
            "is_frozen": False,
            "is_resigned": False,
        },
    }

    recordings = {
        "POST /auth/v3/tenant_access_token/internal": [_token_response()],
        "GET /contact/v3/departments/0/children": [
            {
                "status_code": 200,
                "json": {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "department_id": "d1",
                                "name": "研发部",
                                "parent_department_id": "0",
                            }
                        ],
                        "has_more": False,
                    },
                },
            }
        ],
        "GET /contact/v3/users": [
            {
                "status_code": 200,
                "json": {
                    "code": 0,
                    "data": {"items": [user_payload], "has_more": False},
                },
            }
        ],
    }

    return FeishuClient(
        app_id="a",
        app_secret="s",
        transport=MockTransport(recordings),
        sleep=lambda _: None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 一、游标管理器层面
# ═══════════════════════════════════════════════════════════════════════════


def test_save_page_token_marks_complete_when_cleared(db):
    """⚠️ 清掉 page_token（= 拉完最后一页）必须置 ``snapshot_complete=True``。"""
    manager = CursorManager(db)
    resource = f"{RESOURCE_EMPLOYEE}:d1"

    manager.save_page_token(resource, "token-1")
    db.commit()
    row = db.execute(
        select(OrganizationSyncCursor).where(
            OrganizationSyncCursor.resource_type == resource
        )
    ).scalar_one()
    assert row.snapshot_complete is False, "还在分页中，不该标记为完整"
    assert row.page_token == "token-1"

    # 拉完 → 清 token → 必须标记完整
    manager.save_page_token(resource, None)
    db.commit()
    db.refresh(row)
    assert row.page_token is None
    assert row.snapshot_complete is True, (
        "拉完最后一页后 snapshot_complete 必须为 True —— "
        "否则「远端缺失 = 离职」的判定前提不成立（BUG-R6-45）"
    )


def test_save_page_token_explicit_false_while_paging(db):
    """⚠️ 显式传 ``snapshot_complete=False`` 必须被尊重。"""
    manager = CursorManager(db)
    resource = f"{RESOURCE_EMPLOYEE}:d2"

    manager.save_page_token(resource, "token-1", snapshot_complete=False)
    db.commit()
    row = db.execute(
        select(OrganizationSyncCursor).where(
            OrganizationSyncCursor.resource_type == resource
        )
    ).scalar_one()
    assert row.snapshot_complete is False


def test_save_page_token_keeps_true_when_re_paging_completed_department(db):
    """⚠️ 已完成的部门重新拉取（分页中）应回到 False，拉完再回 True。

    这样 ``snapshot_complete`` 始终反映**当前**真实状态。
    """
    manager = CursorManager(db)
    resource = f"{RESOURCE_EMPLOYEE}:d3"

    manager.save_page_token(resource, None)
    db.commit()
    row = db.execute(
        select(OrganizationSyncCursor).where(
            OrganizationSyncCursor.resource_type == resource
        )
    ).scalar_one()
    assert row.snapshot_complete is True

    # 重新拉取，开始分页
    manager.save_page_token(resource, "token-1", snapshot_complete=False)
    db.commit()
    db.refresh(row)
    assert row.snapshot_complete is False

    # 拉完
    manager.save_page_token(resource, None, snapshot_complete=True)
    db.commit()
    db.refresh(row)
    assert row.snapshot_complete is True


# ═══════════════════════════════════════════════════════════════════════════
# 二、同步链路层面（端到端）
# ═══════════════════════════════════════════════════════════════════════════


def test_full_sync_marks_department_cursor_complete(db):
    """⚠️ 全量同步后，**所有**游标的 ``snapshot_complete`` 都应为 True。

    修复前：``employee:<dept>`` 这类分部门游标恒为 False。
    """
    service = OrganizationSyncService(db, _make_client(), mode="full")
    result = service.sync_all()

    assert result.get("status") == "ok"

    rows = db.execute(select(OrganizationSyncCursor)).scalars().all()
    assert rows, "没有游标被写入"

    incomplete = [r.resource_type for r in rows if not r.snapshot_complete]
    assert not incomplete, (
        "以下游标的 snapshot_complete 仍为 False（BUG-R6-45 未修复）："
        f"{incomplete}"
    )


def test_department_granularity_cursor_is_marked_complete(db):
    """⚠️ 回归目标：``employee:<部门ID>`` 这条游标必须被标记为完整。"""
    service = OrganizationSyncService(db, _make_client(), mode="full")
    service.sync_all()

    row = db.execute(
        select(OrganizationSyncCursor).where(
            OrganizationSyncCursor.resource_type == "employee:d1"
        )
    ).scalar_one_or_none()
    assert row is not None, "分部门粒度的游标没有被创建"
    assert row.snapshot_complete is True, (
        "分部门粒度游标的 snapshot_complete 未被置 True（BUG-R6-45）"
    )


def test_snapshot_complete_true_enables_offboarding_judgement(db):
    """⚠️ **业务语义**：快照完整时，远端缺失的员工必须被判定为离职。

    这是 ``snapshot_complete`` 的**存在意义** ——
    修复前这个前提从未成立，所以离职链路从未被真实验证过。

    ⚠️ 前置条件：员工必须是**从飞书同步过来的**（``feishu_user_id`` 非空）。
    本地手工建的员工（没有 ``feishu_user_id``）不在判定范围内 ——
    否则会把手工账号误判为离职。
    """
    from backend.app.domain.models import Employee

    # 造一个「曾经从飞书同步过、但远端已不存在」的员工
    db.add(
        Employee(
            id="legacy_gone",
            name="离职员工",
            feishu_user_id="legacy_gone",
            employment_status="active",
            account_status="active",
            department_id="d1",
        )
    )
    db.commit()

    service = OrganizationSyncService(db, _make_client(), mode="full")
    service.sync_all()

    employee = db.get(Employee, "legacy_gone")
    db.refresh(employee)
    assert employee.employment_status == "exited", (
        "完整快照下，远端缺失的员工必须被判定为离职 —— "
        "employee.employment_status 仍为 "
        f"{employee.employment_status!r}"
    )
    assert employee.account_status == "disabled"


def test_local_only_employee_is_not_marked_exited(db):
    """⚠️ **安全边界**：没有 ``feishu_user_id`` 的本地员工绝不能被判离职。

    手工创建的账号（测试数据、管理员）不属于飞书通讯录，
    把它们误判为离职会造成大面积误停用。
    """
    from backend.app.domain.models import Employee

    db.add(
        Employee(
            id="manual_account",
            name="本地手工账号",
            employment_status="active",
            account_status="active",
            department_id="d1",
        )
    )
    db.commit()

    service = OrganizationSyncService(db, _make_client(), mode="full")
    service.sync_all()

    employee = db.get(Employee, "manual_account")
    db.refresh(employee)
    assert employee.employment_status == "active", (
        "没有 feishu_user_id 的本地员工不应被判离职 —— "
        f"实际 employment_status={employee.employment_status!r}"
    )


def test_incomplete_snapshot_does_not_mark_employees_exited(db):
    """⚠️ 反向保护：快照**不完整**时，绝不能误判离职。

    这是 ``snapshot_complete`` 存在的安全边界 ——
    拉取失败时若误判离职，会把在职员工全部停用。
    """
    from backend.app.domain.models import Employee

    db.add(
        Employee(
            id="legacy_gone2",
            name="在职员工",
            feishu_user_id="legacy_gone2",
            employment_status="active",
            account_status="active",
            department_id="d1",
        )
    )
    db.commit()

    # 构造一个会拉取失败的客户端
    class FailingClient:
        """部门列表能拉到，但员工列表拉取必失败 → 快照不完整。"""

        app_id = "a"
        app_secret = "s"

        def fetch_departments(self, *args, **kwargs):
            return [
                {
                    "department_id": "d1",
                    "name": "研发部",
                    "parent_department_id": "0",
                }
            ]

        def fetch_employees_in_department(self, dept_id, **kwargs):
            from backend.app.services.feishu_client import FeishuError

            raise FeishuError("模拟部门员工拉取失败")

        def fetch_employees(self, *args, **kwargs):
            return []

    service = OrganizationSyncService(db, FailingClient(), mode="full")
    service.sync_all()

    employee = db.get(Employee, "legacy_gone2")
    db.refresh(employee)
    assert employee.employment_status == "active", (
        "快照不完整时绝不能判定离职 —— "
        f"实际 employment_status={employee.employment_status!r}"
    )
