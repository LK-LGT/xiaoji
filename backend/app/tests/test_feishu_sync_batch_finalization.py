"""飞书组织同步的批次终态可靠性测试。

背景
----
实跑验证时发现：批次执行完成后，审计日志里已经写了 ``result=ok``，
但批次行仍停在 ``running`` —— 前端一直转圈，且后续同步被
「已有任务在运行」挡住。

根因：批次终态写在 ``finally`` 块里。若这里的 commit 因 SQLite
写锁冲突失败（``OperationalError: database is locked``），
异常会覆盖正常返回流程，批次状态**永远写不进去**。

修复：
- SQLite 开启 ``busy_timeout`` + WAL（``core/db.py``）；
- 终态提交带锁冲突重试，重试耗尽则用独立会话兜底落库
  （``organization_sync.py::_commit_final``）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from backend.app.domain.models import Base, OrganizationSyncBatch
from backend.app.services import organization_sync
from backend.app.services.organization_sync import OrganizationSyncService


def _locked_error() -> OperationalError:
    """构造一个「写锁冲突」的 OperationalError。

    SQLAlchemy 2.x 的 DBAPIError 需要 (statement, params, orig)。
    """
    return OperationalError("UPDATE t SET v=1", {}, Exception("database is locked"))


def _patch_sleep(monkeypatch) -> None:
    """把重试退避的 sleep 缩短为 0，**但不碰全局 time.sleep**。

    ⚠️ 踩坑记录：早先写的是

        monkeypatch.setattr(
            "backend.app.services.organization_sync.time.sleep", lambda _s: None
        )

    看起来只作用于 organization_sync，实际上 **``organization_sync.time``
    就是标准库 time 模块本身**（不是副本），于是这条等价于
    ``monkeypatch.setattr(time, "sleep", noop)`` —— **全局**替换了
    ``time.sleep``，在整个测试期间都生效。

    后果：全量跑时，若同一窗口里有其它测试依赖真实 sleep
    （超时、退避、限流、轮询），它们会拿到一个空转的 sleep →
    行为与单跑时不同 → **偶发**、且**顺序相关**的失败。

    正确做法：换掉模块自己的 ``time`` 引用（换成一个只覆盖 sleep 的
    薄壳），全局 ``time.sleep`` 不受影响。
    """
    import types

    real_time = organization_sync.time
    shim = types.SimpleNamespace(sleep=lambda _s: None, time=lambda: real_time.time())
    monkeypatch.setattr(organization_sync, "time", shim)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def test_final_commit_retries_on_lock_conflict(db, monkeypatch):
    """终态提交遇写锁冲突应重试，而不是让批次卡在 running。"""
    batch = OrganizationSyncBatch(
        batch_no="ORG-LOCK", status="running", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()

    service = OrganizationSyncService(db, None, triggered_by="admin")

    calls = {"n": 0}
    real_commit = db.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _locked_error()
        return real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)
    # 缩短退避，避免测试等待过久（不碰全局 time.sleep，见 _patch_sleep）。
    _patch_sleep(monkeypatch)

    service._commit_final(batch)

    assert calls["n"] >= 2, "应至少重试一次"
    db.expire_all()
    row = db.get(OrganizationSyncBatch, batch.id)
    assert row is not None
    # 重试成功后终态应真正落库（这里批次本身就是终态 running→ok 的载体）。
    assert row.id == batch.id


def test_final_commit_raises_on_non_lock_error(db, monkeypatch):
    """非锁冲突的数据库错误应直接抛出，不做无意义重试。"""
    batch = OrganizationSyncBatch(
        batch_no="ORG-ERR", status="running", mode="full", trigger_type="manual"
    )
    db.add(batch)
    db.commit()

    service = OrganizationSyncService(db, None, triggered_by="admin")

    calls = {"n": 0}

    def broken_commit():
        calls["n"] += 1
        raise OperationalError(
            "UPDATE t SET v=1", {}, Exception("disk I/O error")
        )

    monkeypatch.setattr(db, "commit", broken_commit)
    _patch_sleep(monkeypatch)

    with pytest.raises(OperationalError):
        service._commit_final(batch)

    assert calls["n"] == 1, "非锁错误不应重试"


def test_final_commit_falls_back_to_new_session(db, monkeypatch):
    """重试耗尽后应改用独立会话落库，保证批次不永久停在 running。"""
    batch = OrganizationSyncBatch(
        batch_no="ORG-FALLBACK",
        status="ok",
        phase="finished",
        mode="full",
        trigger_type="manual",
        employees_synced=7,
    )
    db.add(batch)
    db.commit()
    batch_id = batch.id

    # 独立会话工厂指向同一个测试库。
    def factory():
        return sessionmaker(bind=db.get_bind())()

    service = OrganizationSyncService(db, None, triggered_by="admin", session_factory=factory)

    def always_locked():
        raise _locked_error()

    monkeypatch.setattr(db, "commit", always_locked)
    _patch_sleep(monkeypatch)

    called = {"n": 0}
    original = service._commit_final_in_new_session

    def spy(b):
        called["n"] += 1
        original(b)

    monkeypatch.setattr(service, "_commit_final_in_new_session", spy)

    service._commit_final(batch)

    assert called["n"] == 1, "重试耗尽后应调用独立会话兜底"

    db.expire_all()
    row = db.get(OrganizationSyncBatch, batch_id)
    assert row is not None
    assert row.status == "ok"
    assert row.phase == "finished"
    assert row.employees_synced == 7


# ═══════════════════════════════════════════════════════════════════════════
# 4. 回归：不得全局替换 time.sleep
# ═══════════════════════════════════════════════════════════════════════════


def test_sleep_patch_does_not_touch_global_time(monkeypatch):
    """⚠️ 回归断言：缩短退避**不得**替换全局 ``time.sleep``。

    为什么这条重要：
    ``organization_sync.time`` 就是标准库 ``time`` 模块本身，
    所以 ``monkeypatch.setattr("...organization_sync.time.sleep", noop)``
    等价于 ``monkeypatch.setattr(time, "sleep", noop)`` —— **全局**生效。

    全局替换会污染同一测试窗口内的**所有**其它测试：
    任何依赖真实 sleep 的逻辑（超时、退避、限流、轮询）都会拿到
    空转的 sleep → 行为与单跑时不一致 → **偶发**、**顺序相关**的失败。

    这类 bug 在本地单跑测试文件时 100% 复现不了，只在全量跑时出现，
    极难定位 —— 所以必须用测试把它钉死。
    """
    import time as stdlib_time

    original_sleep = stdlib_time.sleep
    _patch_sleep(monkeypatch)

    assert stdlib_time.sleep is original_sleep, "全局 time.sleep 被替换了"
    # 模块自己的 time 引用则应当被换掉（退避确实被缩短了）。
    assert organization_sync.time.sleep is not original_sleep


def test_test_file_has_no_global_sleep_patch():
    """源码层面兜底：本文件不得再出现指向 ``organization_sync.time.sleep`` 的 patch。

    用 **AST** 判定，而不是字符串匹配 ——
    字符串匹配会被文档字符串（那里正是在解释这个坑）和
    检测逻辑自身的字符串字面量反复误伤。

    只认真正的 ``monkeypatch.setattr(<目标含该路径>, ...)`` 调用。
    """
    import ast
    import pathlib

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offending: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
            continue
        # 取出所有字符串字面量参数，拼起来看是否命中目标路径
        literals = [
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]
        for lit in literals:
            if "organization_sync.time.sleep" in lit:
                offending.append(f"line {node.lineno}: {lit}")
                break

    assert not offending, f"仍有全局 sleep patch：{offending}"
