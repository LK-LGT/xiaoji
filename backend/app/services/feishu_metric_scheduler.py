"""飞书表格填报 —— 定时同步调度器。

与 ``pan_workload_scheduler`` 分工：
    ``pan_workload_scheduler``  → 扫**网盘**，数成片条数（自动口径）
    ``feishu_metric_scheduler`` → 读**飞书表格**，取完播率/留存率（人工口径）

调度策略
--------
- **启动即执行一次**：避免「服务刚起来要等一个周期才同步」的空窗。
- **固定间隔**：默认 1 小时（``feishu_metric_sync_interval_seconds``）。
  ⚠️ 飞书表格读取走开放平台，有频控 ⇒ **下限 10 分钟**。
- **分布式锁**：独立锁名 ``feishu_metric_table_sync`` ——
  与其它同步任务**互不阻塞**。

⚠️ **失败必须可见**
同步失败写进绑定行的 ``last_error``，并在返回摘要里体现。
**绝不静默失败** —— 剪辑填了表却没进系统 = 数据停更，必须让人知道。

⚠️ **没绑定任何表格时静默跳过**
那是**预期状态**（还没人用表格填报），不是错误，不该刷告警。
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from ..core.config import settings
from ..core.db import get_sessionmaker
from ..domain.models import _now
from ..domain.pan_workload_models import PanFeishuMetricSource

logger = logging.getLogger(__name__)

#: 分布式锁名 —— 与网盘采集用不同的锁名，两个任务可以并行。
JOB_NAME = "feishu_metric_table_sync"

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()

_STOP_JOIN_TIMEOUT_SECONDS = 5.0

#: 间隔下限（秒）—— 飞书开放平台有频控，不能调太密。
_MIN_INTERVAL_SECONDS = 600

_state: dict[str, Any] = {
    "started": False,
    "last_run_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": "",
    "consecutive_failures": 0,
    "next_run_at": None,
    "run_count": 0,
    "skip_count": 0,
}


def _set_state(**kwargs: Any) -> None:
    _state.update(kwargs)


def get_scheduler_status() -> dict[str, Any]:
    """返回调度器运行态（camelCase）。"""
    running = bool(_thread and _thread.is_alive())
    next_run = _state.get("next_run_at")
    return {
        "enabled": True,
        "running": running,
        "intervalSeconds": max(
            _MIN_INTERVAL_SECONDS, settings.feishu_metric_sync_interval_seconds
        ),
        "started": bool(_state.get("started")),
        "lastRunAt": _state["last_run_at"].isoformat() if _state.get("last_run_at") else None,
        "lastFinishedAt": (
            _state["last_finished_at"].isoformat() if _state.get("last_finished_at") else None
        ),
        "lastResult": _state.get("last_result"),
        "lastError": _state.get("last_error") or "",
        "consecutiveFailures": int(_state.get("consecutive_failures") or 0),
        "nextRunAt": next_run.isoformat() if next_run else None,
        "runCount": int(_state.get("run_count") or 0),
        "skipCount": int(_state.get("skip_count") or 0),
    }


def run_scheduled_sync() -> dict[str, Any] | None:
    """执行一轮定时同步。

    Returns:
        同步摘要；未执行（未配置飞书 / 无绑定 / 已在运行）时返回 ``None``。
    """
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.info("飞书表格同步已跳过：应用凭证未配置")
        _set_state(last_result="skipped_not_configured")
        return None

    from .feishu_client import FeishuClient
    from .feishu_metric_sync import FeishuMetricSourceService
    from .feishu_table import FeishuTableReader
    from .job_lock_service import acquire_job_lock, current_holder_id, release_job_lock

    db = get_sessionmaker()()
    holder = current_holder_id()
    acquired = False
    client: FeishuClient | None = None
    try:
        # ⚠️ **先查有没有绑定** —— 一张表都没绑 ⇒ 没什么可同步的，
        #    那是预期状态（还没人用表格填报），不该建客户端、更不该报错。
        bound = db.query(PanFeishuMetricSource).filter(
            PanFeishuMetricSource.active.is_(True)
        ).count()
        if not bound:
            logger.info("飞书表格同步已跳过：未配置表格绑定")
            _set_state(last_result="skipped_no_bindings")
            return None

        acquired = acquire_job_lock(db, JOB_NAME, holder, ttl_seconds=3600)
        if not acquired:
            logger.info("飞书表格同步跳过：其他实例正在执行")
            _set_state(last_result="skipped_already_running")
            return None

        client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            tenant_id=settings.feishu_tenant_id,
        )
        service = FeishuMetricSourceService(db, FeishuTableReader(client))
        try:
            result = service.sync_all()
            db.commit()
        finally:
            service.close()

        _set_state(last_result="synced", last_error="")
        logger.info("飞书表格同步完成: %s", result)
        return result
    except Exception as exc:
        db.rollback()
        logger.exception("飞书表格同步轮次异常")
        _set_state(last_result="error", last_error=str(exc)[:500])
        return None
    finally:
        if client is not None:
            client.close()
        if acquired:
            release_job_lock(db, JOB_NAME, holder)
        db.close()


def _loop(interval_seconds: int) -> None:
    _run_once(interval_seconds, initial=True)
    while not _stop_event.wait(interval_seconds):
        _run_once(interval_seconds)


def _run_once(interval_seconds: int, *, initial: bool = False) -> None:
    _set_state(last_run_at=_now())
    try:
        with _lock:
            summary = run_scheduled_sync()
    except Exception:  # pragma: no cover - 兜底，调度线程不能死
        logger.exception("飞书表格同步轮次异常")
        summary = None

    finished = _now()
    next_run = finished + timedelta(seconds=interval_seconds)
    last_result = _state.get("last_result")
    if summary is not None:
        _set_state(
            last_finished_at=finished,
            next_run_at=next_run,
            run_count=int(_state.get("run_count") or 0) + 1,
            consecutive_failures=0,
        )
    elif last_result in (
        "skipped_already_running",
        "skipped_not_configured",
        "skipped_no_bindings",
    ):
        # ⚠️ **跳过不算失败** —— 未配置/未绑定是预期状态，不该刷告警。
        _set_state(
            last_finished_at=finished,
            next_run_at=next_run,
            skip_count=int(_state.get("skip_count") or 0) + 1,
        )
    else:
        _set_state(
            last_finished_at=finished,
            next_run_at=next_run,
            consecutive_failures=int(_state.get("consecutive_failures") or 0) + 1,
        )

    if initial:
        logger.info("飞书表格同步首轮执行完成, 结果=%s", last_result)


def start() -> None:
    """启动调度线程；已启动时不重复启动。"""
    global _thread
    if _thread and _thread.is_alive():
        return
    interval = max(_MIN_INTERVAL_SECONDS, settings.feishu_metric_sync_interval_seconds)
    _stop_event.clear()
    _set_state(started=True, next_run_at=_now() + timedelta(seconds=interval))
    _thread = threading.Thread(
        target=_loop, args=(interval,), name="feishu-metric-table-sync", daemon=True
    )
    _thread.start()
    logger.info("飞书表格同步已启动, 间隔秒数=%s", interval)


def stop() -> None:
    """停止调度线程（幂等，有界等待）。"""
    _stop_event.set()
    _set_state(started=False, next_run_at=None)
    thread = _thread
    if thread is None or not thread.is_alive():
        return
    thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "飞书表格同步线程未在 %.1f 秒内退出，将由 daemon 机制回收",
            _STOP_JOIN_TIMEOUT_SECONDS,
        )


__all__ = [
    "JOB_NAME",
    "get_scheduler_status",
    "run_scheduled_sync",
    "start",
    "stop",
]
