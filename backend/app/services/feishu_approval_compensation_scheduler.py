"""飞书审批回流的**定时补偿**调度器（FS-7）。

⚠️ **为什么要定时轮询**
------------------------------------
审批回流靠 webhook 推送，但 webhook **不可靠** ——
飞书侧丢事件、投递失败不补发、服务重启期间的事件永久丢失。
⇒ 员工在飞书点了「通过」，本系统的计划却一直卡在审批中。

⇒ 本调度器定期主动拉取**陈旧未终态**的审批实例，补上漏掉的状态。

⚠️ **多实例部署**
------------------------------------
与组织架构同步一样，用数据库分布式锁（``job_lock_service``）
保证同一时刻只有一个实例在补偿。

⚠️ **默认关闭**
------------------------------------
补偿会**主动调用**飞书审批接口（消耗频控配额），
且会写入业务状态 ⇒ 必须由业务显式开启，不能默认就跑。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..core.config import settings
from ..core.db import get_sessionmaker
from ..domain.models import _now

logger = logging.getLogger(__name__)

#: 分布式锁名 —— 与其它同步任务互不阻塞。
JOB_NAME = "feishu_approval_compensation"

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()

_STOP_JOIN_TIMEOUT_SECONDS = 5.0

#: 分布式锁 TTL（秒）—— 必须大于一轮补偿的最坏耗时，
#: 否则锁会在任务还没跑完时过期 ⇒ 另一实例并发进来。
_LOCK_TTL_SECONDS = 300

_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": "",
    "consecutive_failures": 0,
    "run_count": 0,
    "skip_count": 0,
}


def _set_state(**kwargs: Any) -> None:
    _state.update(kwargs)


def get_scheduler_status() -> dict[str, Any]:
    """返回调度器运行态（camelCase）。"""
    running = bool(_thread and _thread.is_alive())
    return {
        "enabled": bool(settings.feishu_approval_compensation_enabled),
        "running": running,
        "intervalSeconds": int(settings.feishu_approval_compensation_interval_seconds),
        "staleSeconds": int(settings.feishu_approval_compensation_stale_seconds),
        "started": bool(_state.get("started")),
        "lastRunAt": _state["last_run_at"].isoformat() if _state.get("last_run_at") else None,
        "lastFinishedAt": (
            _state["last_finished_at"].isoformat() if _state.get("last_finished_at") else None
        ),
        "lastResult": _state.get("last_result"),
        "lastError": _state.get("last_error") or "",
        "consecutiveFailures": int(_state.get("consecutive_failures") or 0),
        "runCount": int(_state.get("run_count") or 0),
        "skipCount": int(_state.get("skip_count") or 0),
    }


def run_compensation() -> dict[str, Any]:
    """执行一轮补偿（**不带分布式锁** —— 供手工触发用）。

    Returns:
        本轮统计 ``{"scanned":..., "compensated":..., "updated":..., ...}``。
    """
    db = get_sessionmaker()()
    try:
        return _run_compensation_with(db)
    except Exception as exc:
        db.rollback()
        logger.exception("飞书审批补偿异常")
        _set_state(last_result="error", last_error=str(exc)[:500])
        raise
    finally:
        db.close()


def _run_once() -> None:
    """跑一轮补偿（含分布式锁）。

    ⚠️ **锁与会话绑定** —— 分布式锁是**数据库行**，
    必须在同一个 session 里加锁与释放（否则 session 一关，
    锁的持有者信息就丢了）。
    """
    from .job_lock_service import acquire_job_lock, current_holder_id, release_job_lock

    db = get_sessionmaker()()
    holder_id = current_holder_id()
    locked = False
    try:
        if not acquire_job_lock(db, JOB_NAME, holder_id, ttl_seconds=_LOCK_TTL_SECONDS):
            _set_state(
                last_result="skipped_locked",
                skip_count=int(_state.get("skip_count") or 0) + 1,
            )
            return
        locked = True
        _run_compensation_with(db)
    finally:
        if locked:
            try:
                release_job_lock(db, JOB_NAME, holder_id)
            except Exception:  # pragma: no cover - 释放失败不能炸调度器
                logger.warning("释放飞书审批补偿分布式锁失败", exc_info=True)
        db.close()


def _run_compensation_with(db) -> dict[str, Any]:
    """在既有 session 上执行一轮补偿。"""
    from .feishu_approval_compensation import compensate_once
    from .feishu_client import FeishuClient

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        _set_state(last_result="error", last_error="飞书应用配置未完成")
        raise RuntimeError("飞书应用配置未完成")

    client = FeishuClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        tenant_id=settings.feishu_tenant_id,
    )
    try:
        result = compensate_once(
            db,
            client=client,
            stale_seconds=int(settings.feishu_approval_compensation_stale_seconds),
            limit=int(settings.feishu_approval_compensation_max_per_run),
        )
        return result.as_dict()
    finally:
        client.close()


def _loop() -> None:
    interval = max(30, int(settings.feishu_approval_compensation_interval_seconds))
    while not _stop_event.wait(interval):
        try:
            with _lock:
                started = _now()
                _run_once()
                _set_state(
                    last_run_at=started,
                    last_finished_at=_now(),
                    last_result="ok",
                    last_error="",
                    run_count=int(_state.get("run_count") or 0) + 1,
                    consecutive_failures=0,
                )
        except Exception:  # pragma: no cover - 兜底，调度线程不能死
            logger.exception("飞书审批补偿轮次异常")
            _set_state(
                last_result="error",
                consecutive_failures=int(_state.get("consecutive_failures") or 0) + 1,
            )


def start() -> None:
    """启动调度线程；已启动时不重复启动。"""
    global _thread
    if not settings.feishu_approval_compensation_enabled:
        logger.info("飞书审批补偿未启用")
        _set_state(started=False)
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _set_state(started=True)
    _thread = threading.Thread(
        target=_loop, name="feishu-approval-compensation", daemon=True
    )
    _thread.start()
    logger.info(
        "飞书审批补偿已启动, 间隔秒数=%s",
        settings.feishu_approval_compensation_interval_seconds,
    )


def stop() -> None:
    """停止调度线程（幂等，有界等待）。"""
    _stop_event.set()
    _set_state(started=False)
    thread = _thread
    if thread is None or not thread.is_alive():
        return
    thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "飞书审批补偿线程未在 %.1f 秒内退出，将由 daemon 机制回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )


__all__ = [
    "JOB_NAME",
    "get_scheduler_status",
    "run_compensation",
    "start",
    "stop",
]
