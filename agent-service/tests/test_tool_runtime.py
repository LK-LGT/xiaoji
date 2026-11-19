"""阻塞调用并发有界，满载时拒绝新执行。"""

from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event

import pytest
from app import tool_runtime


def test_capacity_rejection_and_release(monkeypatch):
    gate = Event()
    entered = Event()
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(tool_runtime, "_POOL", pool)
    monkeypatch.setattr(tool_runtime, "_SLOTS", BoundedSemaphore(1))

    def blocked():
        entered.set()
        gate.wait(2)

    try:
        with pytest.raises(TimeoutError):
            tool_runtime.bounded_call(blocked, 0.02)
        assert entered.is_set()
        with pytest.raises(TimeoutError):
            tool_runtime.bounded_call(lambda: "should not execute", 0.1)
    finally:
        gate.set()
        pool.shutdown(wait=True)
    # 只用信号量验证回调释放，不再向已关闭的池提交。
    assert tool_runtime._SLOTS.acquire(blocking=False)
    tool_runtime._SLOTS.release()
