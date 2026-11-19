"""有界同步调用隔离：超时停止等待，不承诺终止底层线程。"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import BoundedSemaphore
from typing import Any

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent-tool")
_SLOTS = BoundedSemaphore(8)


def bounded_call(fn: Callable[[], Any], timeout_s: float) -> Any:
    if timeout_s <= 0 or not _SLOTS.acquire(blocking=False):
        raise TimeoutError("执行预算耗尽或并发已满")
    try:
        future = _POOL.submit(fn)
    except Exception:
        _SLOTS.release()
        raise
    future.add_done_callback(lambda _: _SLOTS.release())
    try:
        return future.result(timeout=timeout_s)
    except TimeoutError:
        future.cancel()
        raise
